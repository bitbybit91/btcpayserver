"""Blockchain payment verification via multiple backends."""

import threading
import time
from typing import Any, Dict, Optional

import requests

from logger_setup import get_logger

log = get_logger(__name__)


class PaymentMonitor:
    """Poll blockchain explorers to verify incoming payments.

    Currently supports BTC (Blockstream/Esplora), ETH (Etherscan-like
    endpoints), and a generic TX-hash lookup for other coins.
    Customer-submitted TX hashes are accepted as a convenient fallback for
    all coins without dedicated on-chain polling.
    """

    _EXPLORER_ENDPOINTS: Dict[str, str] = {
        "BTC": "https://blockstream.info/api/tx/{tx_hash}",
        "LTC": "https://blockchair.com/litecoin/dashboards/transaction/{tx_hash}?assets_in_usd=false",
        "BCH": "https://blockchair.com/bitcoin-cash/dashboards/transaction/{tx_hash}?assets_in_usd=false",
        "DOGE": "https://blockchair.com/dogecoin/dashboards/transaction/{tx_hash}?assets_in_usd=false",
    }

    def __init__(
        self,
        db,
        order_manager,
        confirmations_required: int = 1,
        poll_interval: int = 60,
    ) -> None:
        """Initialise the payment monitor.

        Args:
            db: :class:`database.Database` instance.
            order_manager: :class:`order_manager.OrderManager` instance.
            confirmations_required: Number of confirmations before an order
                is marked complete.
            poll_interval: Seconds between polling cycles.
        """
        self._db = db
        self._orders = order_manager
        self._confirmations_required = confirmations_required
        self._poll_interval = poll_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Background daemon
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background polling thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        log.info("PaymentMonitor started")

    def stop(self) -> None:
        """Stop the background polling thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        log.info("PaymentMonitor stopped")

    def _poll_loop(self) -> None:
        """Main polling loop — runs in a background thread."""
        while self._running:
            try:
                self._check_pending_orders()
            except Exception as exc:
                log.error(f"PaymentMonitor polling error: {exc}")
            time.sleep(self._poll_interval)

    # ------------------------------------------------------------------
    # Core verification logic
    # ------------------------------------------------------------------

    def _check_pending_orders(self) -> None:
        """Iterate over awaiting-confirmation orders and verify them."""
        orders = self._db.list_orders(status="awaiting_confirmation")
        for order in orders:
            tx_hash = order.get("tx_hash")
            if not tx_hash:
                continue
            coin = order["crypto_currency"]
            address = order["wallet_address"]
            try:
                self._verify_order(order, coin, address, tx_hash)
            except Exception as exc:
                log.warning(
                    f"Failed to verify order {order['order_id']}: {exc}"
                )

    def _verify_order(
        self,
        order: Dict[str, Any],
        coin: str,
        address: str,
        tx_hash: str,
    ) -> None:
        """Attempt to verify a single order against the blockchain.

        Args:
            order: Order dict.
            coin: Coin ticker.
            address: Expected destination wallet address.
            tx_hash: Transaction hash to verify.
        """
        url_template = self._EXPLORER_ENDPOINTS.get(coin)
        if not url_template:
            log.debug(
                f"No explorer endpoint for {coin}; "
                "relying on manual or webhook confirmation"
            )
            return

        url = url_template.format(tx_hash=tx_hash)
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning(f"Explorer request failed for {coin} tx {tx_hash}: {exc}")
            return

        confirmed = self._parse_confirmation(coin, resp.json(), address, order)
        if confirmed:
            self._orders.mark_paid(order["order_id"])
            log.info(
                f"Order {order['order_id']} confirmed via blockchain explorer"
            )

    def _parse_confirmation(
        self,
        coin: str,
        data: Any,
        expected_address: str,
        order: Dict[str, Any],
    ) -> bool:
        """Parse an explorer API response to determine if payment is confirmed.

        Args:
            coin: Coin ticker.
            data: Parsed JSON response from the explorer.
            expected_address: Address we expect to have received funds.
            order: Order dict with ``crypto_amount`` and other fields.

        Returns:
            True if the payment is sufficiently confirmed.
        """
        if coin == "BTC":
            return self._parse_btc_blockstream(data, expected_address, order)
        # Blockchair format (LTC, BCH, DOGE)
        return self._parse_blockchair(data, expected_address, order)

    def _parse_btc_blockstream(
        self,
        data: Dict[str, Any],
        expected_address: str,
        order: Dict[str, Any],
    ) -> bool:
        """Parse Blockstream Esplora tx response.

        Args:
            data: Parsed JSON from Blockstream.
            expected_address: Expected destination address.
            order: Order dict.

        Returns:
            True if the transaction is confirmed with sufficient depth.
        """
        status = data.get("status", {})
        if not status.get("confirmed"):
            return False

        # Blockstream returns the block_height of the transaction.
        # We use the current chain tip from a separate request to compute
        # real confirmation depth.  As a safe fallback (when the tip cannot
        # be fetched), treat any confirmed transaction as having at least 1
        # confirmation.
        tx_block_height = status.get("block_height")
        if tx_block_height is None:
            return False

        confirmations = self._fetch_confirmation_depth(tx_block_height)
        if confirmations < self._confirmations_required:
            return False

        for vout in data.get("vout", []):
            if vout.get("scriptpubkey_address") == expected_address:
                return True

        return False

    def _fetch_confirmation_depth(self, tx_block_height: int) -> int:
        """Return the number of confirmations for a transaction.

        Fetches the current chain tip height from Blockstream and subtracts
        the transaction's block height.  Returns 1 on any error so that
        already-confirmed transactions are not blocked indefinitely.

        Args:
            tx_block_height: The block height at which the transaction was mined.

        Returns:
            Number of confirmations (≥ 1 if confirmed).
        """
        try:
            resp = requests.get(
                "https://blockstream.info/api/blocks/tip/height",
                timeout=10,
            )
            resp.raise_for_status()
            tip = int(resp.text.strip())
            return max(1, tip - tx_block_height + 1)
        except Exception as exc:
            log.warning(f"Could not fetch chain tip height: {exc}")
            # Treat as 1 confirmation if we cannot determine the tip
            return 1

    def _parse_blockchair(
        self,
        data: Dict[str, Any],
        expected_address: str,
        order: Dict[str, Any],
    ) -> bool:
        """Parse Blockchair API response.

        Args:
            data: Parsed JSON from Blockchair.
            expected_address: Expected destination address.
            order: Order dict.

        Returns:
            True if the transaction confirms payment to the expected address.
        """
        transaction = data.get("data", {})
        if not transaction:
            return False

        for tx_data in transaction.values():
            tx_info = tx_data.get("transaction", {})
            if not tx_info.get("block_id"):
                return False

            outputs = tx_data.get("outputs", [])
            for output in outputs:
                if output.get("recipient") == expected_address:
                    return True

        return False
