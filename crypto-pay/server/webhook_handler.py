"""Merchant webhook dispatcher for payment event notifications."""

import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional

import requests

from logger_setup import get_logger

log = get_logger(__name__)


class WebhookHandler:
    """Send signed, retryable webhook notifications to merchant endpoints."""

    def __init__(
        self,
        secret: str = "",
        timeout: int = 10,
        max_retries: int = 3,
    ) -> None:
        """Initialise the webhook handler.

        Args:
            secret: Shared HMAC-SHA256 secret used to sign payloads.
            timeout: HTTP request timeout in seconds.
            max_retries: Maximum delivery attempts per notification.
        """
        self._secret = secret
        self._timeout = timeout
        self._max_retries = max_retries

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send(
        self,
        url: str,
        event: str,
        order: Dict[str, Any],
    ) -> bool:
        """Send a webhook notification for the given event and order.

        The payload is HMAC-SHA256 signed using the configured secret.
        Delivery is retried with exponential back-off on transient errors.

        Args:
            url: Merchant webhook URL.
            event: Event name (e.g. ``"payment.completed"``).
            order: Order dict to include in the payload.

        Returns:
            True if the webhook was delivered successfully.
        """
        payload = self._build_payload(event, order)
        signature = self._sign(payload)
        headers = {
            "Content-Type": "application/json",
            "X-CryptoPay-Signature": signature,
            "X-CryptoPay-Event": event,
        }

        delay = 1.0
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = requests.post(
                    url,
                    data=payload,
                    headers=headers,
                    timeout=self._timeout,
                )
                if resp.status_code < 400:
                    log.info(
                        f"Webhook delivered ({event}) → {url} "
                        f"[{resp.status_code}] (attempt {attempt})"
                    )
                    return True
                log.warning(
                    f"Webhook returned {resp.status_code} for {event} → {url} "
                    f"(attempt {attempt})"
                )
            except requests.RequestException as exc:
                log.warning(
                    f"Webhook delivery failed for {event} → {url} "
                    f"(attempt {attempt}): {exc}"
                )

            if attempt < self._max_retries:
                time.sleep(delay)
                delay *= 2

        log.error(
            f"Webhook delivery permanently failed for {event} → {url} "
            f"after {self._max_retries} attempts"
        )
        return False

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """Verify an inbound webhook signature.

        Args:
            payload: Raw request body bytes.
            signature: ``X-CryptoPay-Signature`` header value.

        Returns:
            True if the signature matches.
        """
        expected = self._sign(payload)
        return hmac.compare_digest(expected, signature)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_payload(self, event: str, order: Dict[str, Any]) -> bytes:
        """Serialise the webhook payload to JSON bytes.

        Args:
            event: Event name.
            order: Order dict.

        Returns:
            JSON-encoded payload bytes.
        """
        data = {
            "event": event,
            "timestamp": int(time.time()),
            "order": {
                "order_id": order.get("order_id"),
                "status": order.get("status"),
                "fiat_amount": order.get("fiat_amount"),
                "fiat_currency": order.get("fiat_currency"),
                "crypto_currency": order.get("crypto_currency"),
                "crypto_amount": order.get("crypto_amount"),
                "wallet_address": order.get("wallet_address"),
                "tx_hash": order.get("tx_hash"),
                "product_name": order.get("product_name"),
                "customer_email": order.get("customer_email"),
            },
        }
        return json.dumps(data, separators=(",", ":")).encode("utf-8")

    def _sign(self, payload: bytes) -> str:
        """Compute the HMAC-SHA256 signature for a payload.

        Args:
            payload: Raw bytes to sign.

        Returns:
            Hex-encoded HMAC-SHA256 digest.
        """
        if not self._secret:
            return ""
        return hmac.new(
            self._secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()
