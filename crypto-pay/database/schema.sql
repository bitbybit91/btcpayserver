-- SQLite schema for crypto-pay payment gateway
-- All queries against this schema must use parameterised statements.

CREATE TABLE IF NOT EXISTS orders (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id               TEXT    UNIQUE NOT NULL,
    created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at             TIMESTAMP NOT NULL,
    status                 TEXT    DEFAULT 'pending',
    product_name           TEXT,
    product_id             TEXT,
    fiat_amount            REAL    NOT NULL,
    fiat_currency          TEXT    DEFAULT 'USD',
    crypto_currency        TEXT    NOT NULL,
    crypto_amount          TEXT    NOT NULL,
    crypto_amount_received TEXT    DEFAULT '0',
    wallet_address         TEXT    NOT NULL,
    exchange_rate          REAL    NOT NULL,
    tx_hash                TEXT,
    confirmations          INTEGER DEFAULT 0,
    verified_at            TIMESTAMP,
    customer_email         TEXT,
    customer_ip            TEXT,
    customer_note          TEXT,
    site_name              TEXT,
    page_url               TEXT,
    updated_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS price_cache (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    coin_id       TEXT    NOT NULL,
    fiat_currency TEXT    NOT NULL,
    price         REAL    NOT NULL,
    change_24h    REAL,
    fetched_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(coin_id, fiat_currency)
);

CREATE TABLE IF NOT EXISTS payment_addresses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    coin       TEXT    NOT NULL,
    address    TEXT    NOT NULL,
    label      TEXT,
    is_active  BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(coin, address)
);

-- Indices for common query patterns
CREATE INDEX IF NOT EXISTS idx_orders_status     ON orders (status);
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_price_cache_coin  ON price_cache (coin_id, fiat_currency);
