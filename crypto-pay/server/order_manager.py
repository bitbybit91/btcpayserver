"""Order creation, tracking, and status management."""

import uuid
import datetime
from typing import Any, Dict, List, Optional

from logger_setup import get_logger

log = get_logger(__name__)

# Valid order state transitions
_ALLOWED_TRANSITIONS: Dict[str, List[str]] = {
    "pending": ["expired", "cancelled", "awaiting_confirmation"],
    "awaiting_confirmation": ["completed", "cancelled", "expired"],
    "completed": [],
    "expired": [],
    "cancelled": [],
}


class OrderManager:
    """Create and manage payment orders."""

    def __init__(self, db, wallet_manager, price_engine, expiry_minutes: int = 30) -> None:
        """Initialise the order manager.

        Args:
            db: :class:`database.Database` instance.
            wallet_manager: :class:`wallet_manager.WalletManager` instance.
            price_engine: :class:`price_engine.PriceEngine` instance.
            expiry_minutes: Minutes until a newly created order expires.
        """
        self._db = db
        self._wallet = wallet_manager
        self._prices = price_engine
        self._expiry_minutes = expiry_minutes

    # ------------------------------------------------------------------
    # Order lifecycle
    # ------------------------------------------------------------------

    def create_order(
        self,
        fiat_amount: float,
        fiat_currency: str,
        crypto_currency: str,
        product_name: str = "",
        product_id: str = "",
        customer_email: str = "",
        customer_ip: str = "",
        customer_note: str = "",
        site_name: str = "",
        page_url: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Create a new payment order.

        Args:
            fiat_amount: Amount to charge in the specified fiat currency.
            fiat_currency: Fiat currency code (e.g. ``"usd"``).
            crypto_currency: Coin ticker (e.g. ``"BTC"``).
            product_name: Human-readable product name.
            product_id: Internal product identifier.
            customer_email: Customer e-mail address for the receipt.
            customer_ip: Customer IP address for fraud signals.
            customer_note: Optional note from the customer.
            site_name: Name of the site that created the order.
            page_url: URL of the page that initiated payment.

        Returns:
            Newly created order dict, or ``None`` on error.
        """
        coin = crypto_currency.upper()
        fiat = fiat_currency.lower()

        address = self._wallet.get_address(coin)
        if not address:
            log.error(f"No active wallet address configured for {coin}")
            return None

        crypto_amount_str = self._prices.convert(fiat_amount, coin, fiat)
        if not crypto_amount_str:
            log.error(f"Cannot get price for {coin}/{fiat}")
            return None

        exchange_rate = self._prices.get_price(coin, fiat)
        if not exchange_rate:
            return None

        order_id = str(uuid.uuid4())
        now = datetime.datetime.utcnow()
        expires_at = now + datetime.timedelta(minutes=self._expiry_minutes)
        # Store as SQLite-compatible format (space-separated, no microseconds)
        _fmt = "%Y-%m-%d %H:%M:%S"

        data = {
            "order_id": order_id,
            "expires_at": expires_at.strftime(_fmt),
            "status": "pending",
            "product_name": product_name,
            "product_id": product_id,
            "fiat_amount": fiat_amount,
            "fiat_currency": fiat,
            "crypto_currency": coin,
            "crypto_amount": crypto_amount_str,
            "wallet_address": address,
            "exchange_rate": exchange_rate,
            "customer_email": customer_email,
            "customer_ip": customer_ip,
            "customer_note": customer_note,
            "site_name": site_name,
            "page_url": page_url,
        }

        self._db.create_order(data)
        log.info(
            f"Order created: {order_id} — {fiat_amount} {fiat.upper()} → "
            f"{crypto_amount_str} {coin}"
        )
        return self._db.get_order(order_id)

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve an order by its identifier, expiring stale orders on-the-fly.

        Args:
            order_id: UUID-style order identifier.

        Returns:
            Order dict, or ``None`` if not found.
        """
        order = self._db.get_order(order_id)
        if not order:
            return None

        if order["status"] == "pending":
            order = self._maybe_expire(order)

        return order

    def submit_tx_hash(self, order_id: str, tx_hash: str) -> Optional[Dict[str, Any]]:
        """Record a transaction hash submitted by the customer.

        Args:
            order_id: UUID-style order identifier.
            tx_hash: Blockchain transaction hash.

        Returns:
            Updated order dict, or ``None`` if the order was not found.
        """
        order = self.get_order(order_id)
        if not order:
            log.warning(f"submit_tx_hash: order not found: {order_id}")
            return None

        if order["status"] not in ("pending",):
            log.warning(
                f"submit_tx_hash: order {order_id} in non-pending status "
                f"({order['status']})"
            )
            return order

        self._db.update_order(order_id, {
            "tx_hash": tx_hash,
            "status": "awaiting_confirmation",
        })
        log.info(f"Order {order_id}: TX hash submitted — {tx_hash}")
        return self._db.get_order(order_id)

    def mark_paid(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Manually mark an order as completed (admin action).

        Args:
            order_id: UUID-style order identifier.

        Returns:
            Updated order dict, or ``None`` if not found.
        """
        order = self._db.get_order(order_id)
        if not order:
            return None

        self._transition(order_id, order["status"], "completed")
        log.info(f"Order {order_id}: manually marked as paid")
        return self._db.get_order(order_id)

    def cancel_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Cancel an order.

        Args:
            order_id: UUID-style order identifier.

        Returns:
            Updated order dict, or ``None`` if not found.
        """
        order = self._db.get_order(order_id)
        if not order:
            return None

        self._transition(order_id, order["status"], "cancelled")
        log.info(f"Order {order_id}: cancelled")
        return self._db.get_order(order_id)

    def list_orders(
        self,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Return a paginated list of orders.

        Args:
            status: Optional status filter.
            limit: Maximum number of results.
            offset: Pagination offset.

        Returns:
            List of order dicts.
        """
        return self._db.list_orders(status=status, limit=limit, offset=offset)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _maybe_expire(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """Expire the order if its expiry timestamp has passed.

        Args:
            order: Order dict (must have ``expires_at`` and ``status``).

        Returns:
            Possibly updated order dict.
        """
        import datetime

        expires_at = order.get("expires_at")
        if not expires_at:
            return order

        if isinstance(expires_at, str):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
                try:
                    expires_at = datetime.datetime.strptime(expires_at, fmt)
                    break
                except ValueError:
                    continue
            else:
                return order

        if datetime.datetime.utcnow() > expires_at:
            self._db.update_order(order["order_id"], {"status": "expired"})
            order["status"] = "expired"
            log.info(f"Order {order['order_id']} expired")

        return order

    def _transition(self, order_id: str, current_status: str, new_status: str) -> bool:
        """Apply a state transition, enforcing the state machine.

        Args:
            order_id: UUID-style order identifier.
            current_status: The order's current status string.
            new_status: Desired new status string.

        Returns:
            True if the transition was applied.
        """
        allowed = _ALLOWED_TRANSITIONS.get(current_status, [])
        if new_status not in allowed:
            log.error(
                f"Order {order_id}: invalid transition "
                f"{current_status!r} → {new_status!r}"
            )
            return False

        update: Dict[str, Any] = {"status": new_status}
        if new_status == "completed":
            update["verified_at"] = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        self._db.update_order(order_id, update)
        return True
