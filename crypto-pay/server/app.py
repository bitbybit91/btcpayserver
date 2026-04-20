"""Flask payment verification server for crypto-pay."""

import os
import sys
import functools
import html
import re
from typing import Any, Callable, Dict, Optional, Tuple

from flask import Flask, jsonify, request, abort, Response, send_file
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Ensure local server modules are importable when run directly
sys.path.insert(0, os.path.dirname(__file__))

from config_loader import load_config, validate_config
from logger_setup import setup_logger, get_logger
from database import Database
from price_engine import PriceEngine, COIN_REGISTRY
from wallet_manager import WalletManager
from order_manager import OrderManager
from qr_generator import QRGenerator
from webhook_handler import WebhookHandler
from payment_monitor import PaymentMonitor

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
log = get_logger(__name__)


def create_app(config: Optional[Dict[str, Any]] = None) -> Flask:
    """Application factory.

    Args:
        config: Optional pre-built config dict (used in tests).  When
            ``None``, ``load_config()`` is called automatically.

    Returns:
        Configured Flask application instance.
    """
    cfg = config or load_config()
    setup_logger(
        log_level=cfg["logging"]["level"],
        log_dir=cfg["logging"]["dir"],
    )

    if not validate_config(cfg):
        log.warning("Config validation warnings present — proceeding anyway")

    # Flask core
    app = Flask(__name__, static_folder=None)
    app.secret_key = cfg["flask"]["secret_key"]

    # CORS — restricted to configured origins
    CORS(
        app,
        origins=cfg["cors"]["origins"],
        methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Admin-API-Key"],
    )

    # Per-IP rate limiting
    rate_limit_str = (
        f"{cfg['rate_limit']['per_ip_per_minute']} per minute"
    )
    limiter = Limiter(
        key_func=get_remote_address,
        app=app,
        default_limits=[rate_limit_str],
        storage_uri="memory://",
    )

    # Infrastructure
    db = Database(cfg["database"]["path"])
    wallet = WalletManager(db)
    prices = PriceEngine(
        db=db,
        cache_ttl=cfg["price_engine"]["cache_ttl"],
        stale_warn_after=cfg["price_engine"]["stale_warn_after"],
        stale_hard_limit=cfg["price_engine"]["stale_hard_limit"],
        api_key=cfg["price_engine"]["coingecko_api_key"],
    )
    orders = OrderManager(
        db=db,
        wallet_manager=wallet,
        price_engine=prices,
        expiry_minutes=cfg["payment"]["expiry_minutes"],
    )
    qr_gen = QRGenerator()
    webhook = WebhookHandler(
        secret=cfg["webhook"]["secret"],
        timeout=cfg["webhook"]["timeout"],
        max_retries=cfg["webhook"]["retries"],
    )
    monitor = PaymentMonitor(
        db=db,
        order_manager=orders,
        confirmations_required=cfg["payment"]["confirmations_required"],
    )
    monitor.start()

    # Seed wallet addresses from config.yaml
    for coin, address in cfg.get("wallets", {}).items():
        if address:
            wallet.add_address(coin, address)

    admin_api_key = cfg["admin"]["api_key"]

    # ------------------------------------------------------------------
    # Auth decorator for admin endpoints
    # ------------------------------------------------------------------

    def require_admin_key(func: Callable) -> Callable:
        """Decorator: enforce X-Admin-API-Key header on admin endpoints."""
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = request.headers.get("X-Admin-API-Key", "")
            if not admin_api_key or key != admin_api_key:
                abort(401)
            return func(*args, **kwargs)
        return wrapper

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _sanitise(value: str) -> str:
        """HTML-escape a user-supplied string to prevent XSS in HTML contexts.

        Use this only for values that will be rendered in HTML responses.
        For API query parameters (tickers, codes), use ``_validate_token``
        instead.

        Args:
            value: Raw user input.

        Returns:
            HTML-escaped string.
        """
        return html.escape(str(value), quote=True)

    def _validate_token(value: str, max_len: int = 50) -> str:
        """Sanitise a short identifier token (coin ticker, currency code, etc.).

        Strips whitespace, truncates to ``max_len``, and allows only
        alphanumeric characters plus ``_/-``.

        Args:
            value: Raw user input.
            max_len: Maximum allowed length.

        Returns:
            Cleaned token string.
        """
        cleaned = re.sub(r"[^A-Za-z0-9_\-/]", "", str(value).strip())
        return cleaned[:max_len]

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    @app.route("/api/health")
    def health():
        """System health check."""
        return jsonify({"status": "ok", "service": "crypto-pay"})

    # ------------------------------------------------------------------
    # Price endpoints
    # ------------------------------------------------------------------

    @app.route("/api/price")
    def get_price():
        """GET /api/price?coin=bitcoin&fiat=usd&amount=49.99"""
        coin = _validate_token(request.args.get("coin", ""))
        fiat = _validate_token(request.args.get("fiat", "usd"))
        amount_str = request.args.get("amount", "")

        if not coin:
            return jsonify({"error": "coin parameter required"}), 400

        price = prices.get_price(coin, fiat)
        if price is None:
            return jsonify({"error": "Price not available"}), 503

        result: Dict[str, Any] = {
            "coin": coin,
            "fiat": fiat,
            "price": price,
        }

        if amount_str:
            try:
                fiat_amount = float(amount_str)
                result["fiat_amount"] = fiat_amount
                result["crypto_amount"] = prices.convert(fiat_amount, coin, fiat)
            except ValueError:
                return jsonify({"error": "Invalid amount"}), 400

        return jsonify(result)

    @app.route("/api/prices")
    def get_prices():
        """GET /api/prices?fiat=usd"""
        fiat = _validate_token(request.args.get("fiat", "usd"))
        all_prices = prices.get_all_prices(fiat)
        return jsonify({"fiat": fiat, "prices": all_prices})

    @app.route("/api/supported-coins")
    def supported_coins():
        """GET /api/supported-coins"""
        return jsonify({"coins": prices.supported_coins()})

    # ------------------------------------------------------------------
    # Order endpoints
    # ------------------------------------------------------------------

    @app.route("/api/order/create", methods=["POST"])
    def create_order():
        """POST /api/order/create — create a new payment order."""
        data = request.get_json(force=True, silent=True) or {}

        try:
            fiat_amount = float(data.get("fiat_amount", 0))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid fiat_amount"}), 400

        if fiat_amount <= 0:
            return jsonify({"error": "fiat_amount must be positive"}), 400

        fiat_currency = _validate_token(data.get("fiat_currency", "usd"))
        crypto_currency = _validate_token(data.get("crypto_currency", ""))

        if not crypto_currency:
            return jsonify({"error": "crypto_currency required"}), 400

        order = orders.create_order(
            fiat_amount=fiat_amount,
            fiat_currency=fiat_currency,
            crypto_currency=crypto_currency,
            product_name=_sanitise(data.get("product_name", "")),
            product_id=_sanitise(data.get("product_id", "")),
            customer_email=_sanitise(data.get("customer_email", "")),
            customer_ip=request.remote_addr or "",
            customer_note=_sanitise(data.get("customer_note", "")),
            site_name=_sanitise(data.get("site_name", "")),
            page_url=_sanitise(data.get("page_url", "")),
        )

        if not order:
            return jsonify({"error": "Failed to create order"}), 503

        return jsonify(order), 201

    @app.route("/api/order/<order_id>/status")
    def order_status(order_id: str):
        """GET /api/order/{order_id}/status"""
        order_id = _sanitise(order_id)
        order = orders.get_order(order_id)
        if not order:
            return jsonify({"error": "Order not found"}), 404

        return jsonify({
            "order_id": order["order_id"],
            "status": order["status"],
            "crypto_currency": order["crypto_currency"],
            "crypto_amount": order["crypto_amount"],
            "wallet_address": order["wallet_address"],
            "expires_at": order.get("expires_at"),
        })

    @app.route("/api/order/<order_id>/verify", methods=["POST"])
    def verify_order(order_id: str):
        """POST /api/order/{order_id}/verify — submit TX hash."""
        order_id = _sanitise(order_id)
        data = request.get_json(force=True, silent=True) or {}
        tx_hash = _sanitise(data.get("tx_hash", ""))

        if not tx_hash:
            return jsonify({"error": "tx_hash required"}), 400

        order = orders.submit_tx_hash(order_id, tx_hash)
        if not order:
            return jsonify({"error": "Order not found or invalid state"}), 404

        return jsonify({"order_id": order["order_id"], "status": order["status"]})

    # ------------------------------------------------------------------
    # QR code
    # ------------------------------------------------------------------

    @app.route("/api/qr/<order_id>")
    def get_qr(order_id: str):
        """GET /api/qr/{order_id} — return PNG QR code."""
        order_id = _sanitise(order_id)
        order = orders.get_order(order_id)
        if not order:
            return jsonify({"error": "Order not found"}), 404

        png_bytes = qr_gen.generate_png(
            coin=order["crypto_currency"],
            address=order["wallet_address"],
            amount=order.get("crypto_amount"),
        )

        return Response(png_bytes, mimetype="image/png")

    # ------------------------------------------------------------------
    # Admin endpoints
    # ------------------------------------------------------------------

    @app.route("/api/admin/orders")
    @require_admin_key
    def admin_list_orders():
        """GET /api/admin/orders"""
        status = request.args.get("status")
        limit = min(int(request.args.get("limit", 100)), 500)
        offset = int(request.args.get("offset", 0))
        return jsonify(orders.list_orders(status=status, limit=limit, offset=offset))

    @app.route("/api/admin/orders/<order_id>")
    @require_admin_key
    def admin_get_order(order_id: str):
        """GET /api/admin/orders/{order_id}"""
        order_id = _sanitise(order_id)
        order = orders.get_order(order_id)
        if not order:
            return jsonify({"error": "Order not found"}), 404
        return jsonify(order)

    @app.route("/api/admin/orders/<order_id>/mark-paid", methods=["POST"])
    @require_admin_key
    def admin_mark_paid(order_id: str):
        """POST /api/admin/orders/{order_id}/mark-paid"""
        order_id = _sanitise(order_id)
        order = orders.mark_paid(order_id)
        if not order:
            return jsonify({"error": "Order not found"}), 404
        return jsonify({"order_id": order["order_id"], "status": order["status"]})

    @app.route("/api/admin/orders/<order_id>/cancel", methods=["POST"])
    @require_admin_key
    def admin_cancel_order(order_id: str):
        """POST /api/admin/orders/{order_id}/cancel"""
        order_id = _sanitise(order_id)
        order = orders.cancel_order(order_id)
        if not order:
            return jsonify({"error": "Order not found"}), 404
        return jsonify({"order_id": order["order_id"], "status": order["status"]})

    @app.route("/api/admin/stats")
    @require_admin_key
    def admin_stats():
        """GET /api/admin/stats — revenue statistics."""
        return jsonify(db.get_stats())

    # ------------------------------------------------------------------
    # Shutdown hook
    # ------------------------------------------------------------------

    @app.teardown_appcontext
    def shutdown_monitor(exception=None):
        pass  # Monitor runs as daemon thread; Flask handles cleanup

    return app


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cfg = load_config()
    application = create_app(cfg)
    application.run(
        host=cfg["server"]["host"],
        port=int(cfg["server"]["port"]),
        debug=cfg["flask"].get("debug", False),
    )
