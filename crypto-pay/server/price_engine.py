"""CoinGecko live rate fetcher with caching and exponential-backoff retry."""

import time
import threading
from typing import Any, Dict, List, Optional, Tuple

import requests

from logger_setup import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Coin registry — maps internal ticker → CoinGecko id
# ---------------------------------------------------------------------------
COIN_REGISTRY: Dict[str, Dict[str, Any]] = {
    "BTC": {
        "coingecko_id": "bitcoin",
        "name": "Bitcoin",
        "decimals": 8,
        "qr_prefix": "bitcoin:",
    },
    "XMR": {
        "coingecko_id": "monero",
        "name": "Monero",
        "decimals": 12,
        "qr_prefix": "monero:",
    },
    "ETH": {
        "coingecko_id": "ethereum",
        "name": "Ethereum",
        "decimals": 8,
        "qr_prefix": "ethereum:",
    },
    "LTC": {
        "coingecko_id": "litecoin",
        "name": "Litecoin",
        "decimals": 8,
        "qr_prefix": "litecoin:",
    },
    "USDT_ERC20": {
        "coingecko_id": "tether",
        "name": "Tether (ERC-20)",
        "decimals": 2,
        "qr_prefix": "ethereum:",
    },
    "USDT_TRC20": {
        "coingecko_id": "tether",
        "name": "Tether (TRC-20)",
        "decimals": 2,
        "qr_prefix": "tron:",
    },
    "BCH": {
        "coingecko_id": "bitcoin-cash",
        "name": "Bitcoin Cash",
        "decimals": 8,
        "qr_prefix": "bitcoincash:",
    },
    "SOL": {
        "coingecko_id": "solana",
        "name": "Solana",
        "decimals": 9,
        "qr_prefix": "solana:",
    },
    "DOGE": {
        "coingecko_id": "dogecoin",
        "name": "Dogecoin",
        "decimals": 8,
        "qr_prefix": "dogecoin:",
    },
    "USDC": {
        "coingecko_id": "usd-coin",
        "name": "USD Coin (ERC-20)",
        "decimals": 2,
        "qr_prefix": "ethereum:",
    },
}

_COINGECKO_FREE_URL = "https://api.coingecko.com/api/v3/simple/price"
_COINGECKO_PRO_URL = "https://pro-api.coingecko.com/api/v3/simple/price"

_SUPPORTED_FIAT = [
    "usd", "eur", "gbp", "cad", "aud",
    "aed", "sar", "jpy", "chf", "cny",
]


class PriceEngine:
    """Fetch, cache, and serve cryptocurrency prices from CoinGecko.

    Thread-safe: a single instance can be shared between all request
    handlers.  Prices are fetched lazily on demand and cached in the
    database and in-memory.
    """

    def __init__(
        self,
        db,  # database.Database instance
        cache_ttl: int = 60,
        stale_warn_after: int = 300,
        stale_hard_limit: int = 900,
        api_key: str = "",
    ) -> None:
        """Initialise the price engine.

        Args:
            db: Initialised :class:`database.Database` instance.
            cache_ttl: Seconds before a cached price is considered stale.
            stale_warn_after: Seconds after which a staleness warning is
                emitted.
            stale_hard_limit: Seconds after which a hard-limit warning is
                emitted (prices may be unreliable).
            api_key: Optional CoinGecko Pro API key.
        """
        self._db = db
        self._cache_ttl = cache_ttl
        self._stale_warn_after = stale_warn_after
        self._stale_hard_limit = stale_hard_limit
        self._api_key = api_key
        self._lock = threading.Lock()
        # In-memory cache: (coin_id, fiat) → (price, fetched_epoch)
        self._mem_cache: Dict[Tuple[str, str], Tuple[float, float]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_price(self, coin: str, fiat: str = "usd") -> Optional[float]:
        """Return the current price of *coin* in *fiat*.

        Uses in-memory cache first, then DB cache, then live API fetch.

        Args:
            coin: Coin ticker or CoinGecko id (e.g. ``"BTC"`` or
                ``"bitcoin"``).
            fiat: Fiat currency code, lowercase (e.g. ``"usd"``).

        Returns:
            Price as a float, or ``None`` on failure.
        """
        fiat = fiat.lower()
        coin_id = self._resolve_coin_id(coin)
        if not coin_id:
            log.error(f"Unknown coin: {coin}")
            return None

        cached = self._get_from_cache(coin_id, fiat)
        if cached is not None:
            return cached

        return self._fetch_and_cache(coin_id, fiat)

    def get_all_prices(self, fiat: str = "usd") -> Dict[str, float]:
        """Return prices for all supported coins in *fiat*.

        Args:
            fiat: Fiat currency code, lowercase.

        Returns:
            Mapping of CoinGecko id → price.
        """
        fiat = fiat.lower()
        coin_ids = list({v["coingecko_id"] for v in COIN_REGISTRY.values()})
        self._fetch_bulk(coin_ids, fiat)

        result: Dict[str, float] = {}
        for coin_id in coin_ids:
            price = self._get_from_cache(coin_id, fiat)
            if price is not None:
                result[coin_id] = price
        return result

    def convert(self, fiat_amount: float, coin: str, fiat: str = "usd") -> Optional[str]:
        """Convert a fiat amount to the equivalent crypto amount.

        Args:
            fiat_amount: Amount in fiat currency.
            coin: Coin ticker (e.g. ``"BTC"``).
            fiat: Fiat currency code, lowercase.

        Returns:
            Crypto amount as a string with correct decimal places, or
            ``None`` if the price cannot be determined.
        """
        price = self.get_price(coin, fiat)
        if not price or price <= 0:
            return None

        coin_upper = coin.upper()
        decimals = COIN_REGISTRY.get(coin_upper, {}).get("decimals", 8)
        crypto_amount = fiat_amount / price
        return f"{crypto_amount:.{decimals}f}"

    def supported_coins(self) -> List[Dict[str, Any]]:
        """Return a list of supported coin descriptors.

        Returns:
            List of dicts with coin metadata.
        """
        return [
            {"ticker": ticker, **meta}
            for ticker, meta in COIN_REGISTRY.items()
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_coin_id(self, coin: str) -> Optional[str]:
        """Map a ticker or CoinGecko id to a canonical CoinGecko id.

        Args:
            coin: Ticker (e.g. ``"BTC"``) or CoinGecko id (e.g.
                ``"bitcoin"``).

        Returns:
            CoinGecko id string, or ``None`` if unknown.
        """
        upper = coin.upper()
        if upper in COIN_REGISTRY:
            return COIN_REGISTRY[upper]["coingecko_id"]
        # Accept raw CoinGecko ids directly
        known_ids = {v["coingecko_id"] for v in COIN_REGISTRY.values()}
        if coin.lower() in known_ids:
            return coin.lower()
        return None

    def _get_from_cache(self, coin_id: str, fiat: str) -> Optional[float]:
        """Check in-memory and DB cache for a fresh price.

        Args:
            coin_id: CoinGecko coin id.
            fiat: Fiat currency code.

        Returns:
            Cached price, or ``None`` if absent / stale.
        """
        now = time.time()

        # In-memory cache check
        key = (coin_id, fiat)
        with self._lock:
            entry = self._mem_cache.get(key)
            if entry:
                price, fetched = entry
                age = now - fetched
                if age < self._cache_ttl:
                    return price
                if age < self._stale_hard_limit:
                    log.warning(
                        f"Price for {coin_id}/{fiat} is {age:.0f}s old "
                        f"(warn threshold: {self._stale_warn_after}s)"
                    )
                    return price

        # DB cache check
        row = self._db.get_price(coin_id, fiat)
        if row:
            import datetime

            fetched_at = row["fetched_at"]
            if isinstance(fetched_at, str):
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
                    try:
                        fetched_at = datetime.datetime.strptime(fetched_at, fmt)
                        break
                    except ValueError:
                        continue
                else:
                    fetched_at = datetime.datetime.utcnow()
            age = (datetime.datetime.utcnow() - fetched_at).total_seconds()

            if age < self._stale_hard_limit:
                with self._lock:
                    self._mem_cache[key] = (row["price"], now - age)
                if age >= self._stale_warn_after:
                    log.warning(
                        f"Using stale DB price for {coin_id}/{fiat} — "
                        f"{age:.0f}s old"
                    )
                return row["price"]

        return None

    def _fetch_and_cache(self, coin_id: str, fiat: str) -> Optional[float]:
        """Fetch a single coin price from CoinGecko and cache it.

        Args:
            coin_id: CoinGecko coin id.
            fiat: Fiat currency code.

        Returns:
            Price float, or ``None`` on error.
        """
        result = self._fetch_bulk([coin_id], fiat)
        return result.get(coin_id)

    def _fetch_bulk(
        self,
        coin_ids: List[str],
        fiat: str,
        max_retries: int = 4,
    ) -> Dict[str, float]:
        """Fetch multiple coin prices in a single CoinGecko request.

        Uses exponential back-off on transient failures.

        Args:
            coin_ids: List of CoinGecko coin ids.
            fiat: Fiat currency code.
            max_retries: Maximum number of retry attempts.

        Returns:
            Mapping of coin_id → price for successfully fetched coins.
        """
        base_url = _COINGECKO_PRO_URL if self._api_key else _COINGECKO_FREE_URL
        params = {
            "ids": ",".join(coin_ids),
            "vs_currencies": fiat,
            "include_24hr_change": "true",
        }
        headers = {}
        if self._api_key:
            headers["x-cg-pro-api-key"] = self._api_key

        delay = 1.0
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.get(
                    base_url,
                    params=params,
                    headers=headers,
                    timeout=10,
                )
                if response.status_code == 429:
                    log.warning(
                        f"CoinGecko rate limited (attempt {attempt}); "
                        f"sleeping {delay:.1f}s"
                    )
                    time.sleep(delay)
                    delay *= 2
                    continue

                response.raise_for_status()
                data: Dict[str, Any] = response.json()
                result: Dict[str, float] = {}
                now = time.time()

                for coin_id, values in data.items():
                    price = values.get(fiat)
                    change = values.get(f"{fiat}_24h_change")
                    if price is not None:
                        result[coin_id] = price
                        self._db.upsert_price(coin_id, fiat, price, change)
                        with self._lock:
                            self._mem_cache[(coin_id, fiat)] = (price, now)

                log.info(
                    f"Fetched prices for {list(result.keys())} in {fiat}"
                )
                return result

            except requests.RequestException as exc:
                log.warning(
                    f"CoinGecko request failed (attempt {attempt}/{max_retries}): {exc}"
                )
                if attempt < max_retries:
                    time.sleep(delay)
                    delay *= 2

        log.error("All CoinGecko retries exhausted; prices unavailable")
        return {}
