# crypto-pay

A lightweight, self-hosted cryptocurrency payment gateway with a Flask backend,
SQLite database, embeddable frontend widgets, and a site-injection CLI tool.

**Zero private-key exposure. No third-party payment processors. No tracking.**

---

## Supported Cryptocurrencies

| Coin | Network | CoinGecko ID |
|------|---------|--------------|
| Bitcoin (BTC) | Bitcoin | bitcoin |
| Monero (XMR) | Monero | monero |
| Ethereum (ETH) | Ethereum | ethereum |
| Litecoin (LTC) | Litecoin | litecoin |
| Tether USDT | ERC-20 / TRC-20 | tether |
| Bitcoin Cash (BCH) | Bitcoin Cash | bitcoin-cash |
| Solana (SOL) | Solana | solana |
| Dogecoin (DOGE) | Dogecoin | dogecoin |
| USD Coin (USDC) | ERC-20 | usd-coin |

---

## Quick Start (Ubuntu 20.04)

```bash
git clone https://github.com/yourorg/crypto-pay
cd crypto-pay
sudo bash install.sh
sudo nano /opt/crypto-pay/.env          # set SECRET_KEY + ADMIN_API_KEY
sudo nano /opt/crypto-pay/config.yaml   # add your wallet addresses
sudo systemctl restart crypto-pay
```

---

## CLI Reference

```
sudo python3 crypto_pay.py --install           # Full install
sudo python3 crypto_pay.py --init              # Generate config
sudo python3 crypto_pay.py --validate          # Validate config
sudo python3 crypto_pay.py --serve             # Start payment server
sudo python3 crypto_pay.py --daemon            # Start as background daemon
sudo python3 crypto_pay.py --prices            # Show current prices
sudo python3 crypto_pay.py --convert --amount 49.99 --fiat USD --coin BTC
sudo python3 crypto_pay.py --orders
sudo python3 crypto_pay.py --orders --status pending
sudo python3 crypto_pay.py --mark-paid --order <uuid>
sudo python3 crypto_pay.py --validate-wallets
sudo python3 crypto_pay.py --add-wallet --coin BTC --address "bc1q..."
sudo python3 crypto_pay.py --inject --dry-run  # Preview site injection
sudo python3 crypto_pay.py --inject            # Inject into all sites
sudo python3 crypto_pay.py --inject --site main_site
sudo python3 crypto_pay.py --remove --site main_site
sudo python3 crypto_pay.py --reinject
sudo python3 crypto_pay.py --diagnose
sudo python3 crypto_pay.py --test-api
sudo python3 crypto_pay.py --backup
sudo python3 crypto_pay.py --restore --site main_site --backup ./backups/...
```

---

## API Endpoints

### Public

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/price?coin=bitcoin&fiat=usd&amount=49.99` | Price conversion |
| GET | `/api/prices?fiat=usd` | All prices |
| GET | `/api/supported-coins` | Supported coin list |
| POST | `/api/order/create` | Create payment order |
| GET | `/api/order/{id}/status` | Order status |
| POST | `/api/order/{id}/verify` | Submit TX hash |
| GET | `/api/qr/{id}` | QR code PNG |

### Admin (requires `X-Admin-API-Key` header)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/admin/orders` | List orders |
| GET | `/api/admin/orders/{id}` | Order details |
| POST | `/api/admin/orders/{id}/mark-paid` | Mark as paid |
| POST | `/api/admin/orders/{id}/cancel` | Cancel order |
| GET | `/api/admin/stats` | Revenue statistics |

---

## Frontend Integration

### Option 1 — Modal overlay

```html
<link rel="stylesheet" href="/crypto-pay/css/payment.css">
<script src="/crypto-pay/js/crypto-pay.js"></script>

<button onclick="CryptoPay.openModal({ fiat_amount: 49.99, fiat_currency: 'usd', crypto_currency: 'BTC' })">
  Pay with Crypto
</button>
```

### Option 2 — Standalone page (popup / iframe)

```
/crypto-pay/payment-widget.html?amount=49.99&fiat=USD&product=My+Product
```

### Option 3 — Site injector

```bash
sudo python3 crypto_pay.py --inject --site /var/www/html
```

---

## Security

- Flask binds to `127.0.0.1` only — expose through Nginx/Apache reverse proxy
- Secrets live in `.env` (chmod 600) — never committed to source control
- All SQL queries use parameterised statements
- All user input HTML-escaped before output
- Admin endpoints require API key header
- Per-IP rate limiting on all endpoints
- CORS restricted to configured origins
- No private keys ever stored or processed

---

## Nginx Reverse Proxy Example

```nginx
server {
    listen 443 ssl;
    server_name yoursite.example;

    location /api/ {
        proxy_pass http://127.0.0.1:5000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /crypto-pay/ {
        alias /opt/crypto-pay/frontend/;
    }
}
```

---

## License

MIT
