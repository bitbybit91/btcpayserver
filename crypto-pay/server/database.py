"""SQLite database layer with parameterised queries for crypto-pay."""

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from logger_setup import get_logger

log = get_logger(__name__)

_SCHEMA_FILE = Path(__file__).parent.parent / "database" / "schema.sql"

_local = threading.local()


class Database:
    """Thread-safe SQLite wrapper that enforces parameterised queries.

    Each thread receives its own :class:`sqlite3.Connection` so the
    single ``Database`` instance is safe to use from a multi-threaded
    Flask app.
    """

    def __init__(self, db_path: str) -> None:
        """Initialise the database layer.

        Args:
            db_path: Absolute path to the SQLite database file.
        """
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Return (and cache) a per-thread connection.

        Returns:
            An open :class:`sqlite3.Connection`.
        """
        conn = getattr(_local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            _local.conn = conn
            log.debug(f"Opened DB connection (thread {threading.get_ident()}) → {self._db_path}")
        return conn

    @contextmanager
    def _cursor(self) -> Generator[sqlite3.Cursor, None, None]:
        """Context manager that yields a cursor and commits / rolls back.

        Yields:
            An open :class:`sqlite3.Cursor`.
        """
        conn = self._connect()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()

    # ------------------------------------------------------------------
    # Schema initialisation
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        """Create tables from schema.sql if they do not exist."""
        if not _SCHEMA_FILE.exists():
            raise FileNotFoundError(f"Schema file not found: {_SCHEMA_FILE}")

        schema_sql = _SCHEMA_FILE.read_text(encoding="utf-8")
        conn = self._connect()
        conn.executescript(schema_sql)
        conn.commit()
        log.info("Database schema initialised")

    # ------------------------------------------------------------------
    # Order helpers
    # ------------------------------------------------------------------

    def create_order(self, data: Dict[str, Any]) -> int:
        """Insert a new order record.

        Args:
            data: Mapping of column names to values.

        Returns:
            The ``rowid`` of the newly inserted row.
        """
        columns = ", ".join(data.keys())
        placeholders = ", ".join("?" * len(data))
        sql = f"INSERT INTO orders ({columns}) VALUES ({placeholders})"  # noqa: S608
        with self._cursor() as cur:
            cur.execute(sql, list(data.values()))
            return cur.lastrowid  # type: ignore[return-value]

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single order by ``order_id``.

        Args:
            order_id: The UUID-style order identifier.

        Returns:
            Row as a dict, or ``None`` if not found.
        """
        sql = "SELECT * FROM orders WHERE order_id = ?"
        with self._cursor() as cur:
            cur.execute(sql, (order_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def update_order(self, order_id: str, data: Dict[str, Any]) -> int:
        """Update arbitrary columns of an existing order.

        Args:
            order_id: The UUID-style order identifier.
            data: Mapping of column names to new values.

        Returns:
            Number of rows affected.
        """
        set_clause = ", ".join(f"{col} = ?" for col in data.keys())
        sql = f"UPDATE orders SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE order_id = ?"  # noqa: S608
        values = list(data.values()) + [order_id]
        with self._cursor() as cur:
            cur.execute(sql, values)
            return cur.rowcount

    def list_orders(
        self,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Return a paginated list of orders, optionally filtered by status.

        Args:
            status: If provided, only orders with this status are returned.
            limit: Maximum number of rows to return.
            offset: Number of rows to skip for pagination.

        Returns:
            List of order dicts.
        """
        if status:
            sql = "SELECT * FROM orders WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params: Tuple = (status, limit, offset)
        else:
            sql = "SELECT * FROM orders ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params = (limit, offset)

        with self._cursor() as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Price cache helpers
    # ------------------------------------------------------------------

    def upsert_price(
        self,
        coin_id: str,
        fiat_currency: str,
        price: float,
        change_24h: Optional[float] = None,
    ) -> None:
        """Insert or replace a price cache entry.

        Args:
            coin_id: CoinGecko coin identifier (e.g. ``"bitcoin"``).
            fiat_currency: Fiat currency code in lowercase (e.g. ``"usd"``).
            price: Current price in the specified fiat currency.
            change_24h: 24-hour price change percentage (optional).
        """
        sql = """
            INSERT INTO price_cache (coin_id, fiat_currency, price, change_24h, fetched_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(coin_id, fiat_currency) DO UPDATE SET
                price       = excluded.price,
                change_24h  = excluded.change_24h,
                fetched_at  = excluded.fetched_at
        """
        with self._cursor() as cur:
            cur.execute(sql, (coin_id, fiat_currency, price, change_24h))

    def get_price(
        self,
        coin_id: str,
        fiat_currency: str,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve the cached price for a coin/fiat pair.

        Args:
            coin_id: CoinGecko coin identifier.
            fiat_currency: Fiat currency code in lowercase.

        Returns:
            Row dict with ``price``, ``change_24h``, and ``fetched_at``
            fields, or ``None`` if no cached entry exists.
        """
        sql = "SELECT * FROM price_cache WHERE coin_id = ? AND fiat_currency = ?"
        with self._cursor() as cur:
            cur.execute(sql, (coin_id, fiat_currency))
            row = cur.fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # Payment address helpers
    # ------------------------------------------------------------------

    def add_address(self, coin: str, address: str, label: str = "") -> None:
        """Add a payment address to the pool.

        Args:
            coin: Coin ticker (e.g. ``"BTC"``).
            address: Public wallet address.
            label: Optional human-readable label.
        """
        sql = """
            INSERT OR IGNORE INTO payment_addresses (coin, address, label)
            VALUES (?, ?, ?)
        """
        with self._cursor() as cur:
            cur.execute(sql, (coin, address, label))

    def get_address(self, coin: str) -> Optional[str]:
        """Return the first active address for the given coin.

        Args:
            coin: Coin ticker (e.g. ``"BTC"``).

        Returns:
            Wallet address string, or ``None`` if none is configured.
        """
        sql = (
            "SELECT address FROM payment_addresses "
            "WHERE coin = ? AND is_active = 1 "
            "ORDER BY id ASC LIMIT 1"
        )
        with self._cursor() as cur:
            cur.execute(sql, (coin,))
            row = cur.fetchone()
            return row["address"] if row else None

    def list_addresses(self) -> List[Dict[str, Any]]:
        """Return all payment addresses.

        Returns:
            List of address dicts.
        """
        with self._cursor() as cur:
            cur.execute("SELECT * FROM payment_addresses ORDER BY coin, id")
            return [dict(row) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Return aggregated order statistics.

        Returns:
            Dictionary with revenue and order-count breakdowns.
        """
        stats: Dict[str, Any] = {}

        with self._cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) as total, status FROM orders GROUP BY status"
            )
            by_status = {row["status"]: row["total"] for row in cur.fetchall()}
            stats["orders_by_status"] = by_status
            stats["total_orders"] = sum(by_status.values())

            cur.execute(
                """
                SELECT fiat_currency,
                       SUM(fiat_amount) AS revenue,
                       COUNT(*) AS count
                FROM orders
                WHERE status = 'completed'
                GROUP BY fiat_currency
                """
            )
            stats["revenue_by_currency"] = {
                row["fiat_currency"]: {
                    "revenue": row["revenue"],
                    "count": row["count"],
                }
                for row in cur.fetchall()
            }

        return stats
