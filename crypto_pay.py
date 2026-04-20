#!/usr/bin/env python3
"""
crypto_pay.py — Main CLI entry point and static-site injector for crypto-pay.

Usage examples:
  sudo python3 crypto_pay.py --install
  sudo python3 crypto_pay.py --init
  sudo python3 crypto_pay.py --validate
  sudo python3 crypto_pay.py --serve
  sudo python3 crypto_pay.py --prices
  sudo python3 crypto_pay.py --convert --amount 49.99 --fiat USD --coin BTC
  sudo python3 crypto_pay.py --orders
  sudo python3 crypto_pay.py --mark-paid --order <uuid>
  sudo python3 crypto_pay.py --validate-wallets
  sudo python3 crypto_pay.py --add-wallet --coin BTC --address "bc1q..."
  sudo python3 crypto_pay.py --inject --dry-run
  sudo python3 crypto_pay.py --inject --site /var/www/html
  sudo python3 crypto_pay.py --remove --site /var/www/html
  sudo python3 crypto_pay.py --reinject
  sudo python3 crypto_pay.py --diagnose
  sudo python3 crypto_pay.py --test-api
  sudo python3 crypto_pay.py --backup
  sudo python3 crypto_pay.py --restore --site /var/www/html --backup ./backups/...
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Resolve paths relative to this script
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_CRYPTO_PAY_DIR = _SCRIPT_DIR / "crypto-pay"
_SERVER_DIR = _CRYPTO_PAY_DIR / "server"
_INSTALL_DIR = Path("/opt/crypto-pay")
_DATA_DIR = Path("/var/lib/crypto-pay")

# Ensure server modules are importable
sys.path.insert(0, str(_SERVER_DIR))


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
_RESET = "\033[0m"
_BOLD = "\033[1m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_CYAN = "\033[36m"


def _ok(msg: str) -> None:
    print(f"{_GREEN}✓{_RESET} {msg}")


def _warn(msg: str) -> None:
    print(f"{_YELLOW}⚠{_RESET} {msg}", file=sys.stderr)


def _err(msg: str) -> None:
    print(f"{_RED}✗{_RESET} {msg}", file=sys.stderr)


def _info(msg: str) -> None:
    print(f"{_CYAN}→{_RESET} {msg}")


def _header(msg: str) -> None:
    print(f"\n{_BOLD}{msg}{_RESET}")


# ---------------------------------------------------------------------------
# Config / DB helpers (lazy-imported to avoid import errors when not installed)
# ---------------------------------------------------------------------------

def _load_cfg() -> Dict[str, Any]:
    """Load configuration, falling back gracefully if modules are absent."""
    try:
        from config_loader import load_config
        return load_config()
    except ImportError:
        _warn("Server modules not found — run --install first")
        return {}


def _get_db():
    """Return an initialised Database instance."""
    cfg = _load_cfg()
    from database import Database  # type: ignore
    return Database(cfg.get("database", {}).get("path", str(_DATA_DIR / "crypto_pay.db")))


def _get_price_engine(db=None):
    """Return an initialised PriceEngine instance."""
    cfg = _load_cfg()
    if db is None:
        db = _get_db()
    from price_engine import PriceEngine  # type: ignore
    pe_cfg = cfg.get("price_engine", {})
    return PriceEngine(
        db=db,
        cache_ttl=pe_cfg.get("cache_ttl", 60),
        stale_warn_after=pe_cfg.get("stale_warn_after", 300),
        stale_hard_limit=pe_cfg.get("stale_hard_limit", 900),
        api_key=pe_cfg.get("coingecko_api_key", ""),
    )


# ===========================================================================
# Command handlers
# ===========================================================================

def cmd_install(args: argparse.Namespace) -> int:
    """Run the install.sh script."""
    install_sh = _CRYPTO_PAY_DIR / "install.sh"
    if not install_sh.exists():
        _err(f"install.sh not found at {install_sh}")
        return 1
    _info(f"Running installer: {install_sh}")
    result = subprocess.run(["bash", str(install_sh)], check=False)
    return result.returncode


def cmd_init(args: argparse.Namespace) -> int:
    """Generate a default config.yaml and .env in the current directory."""
    cfg_src = _CRYPTO_PAY_DIR / "config.yaml"
    env_src = _CRYPTO_PAY_DIR / ".env.example"

    for src, dst_name in [(cfg_src, "config.yaml"), (env_src, ".env")]:
        dst = Path(dst_name)
        if dst.exists():
            _warn(f"{dst} already exists — skipping")
        else:
            shutil.copy(src, dst)
            _ok(f"Created {dst}")
            if dst.name == ".env":
                dst.chmod(0o600)
                _info("Set .env permissions to 600")

    _info("Edit config.yaml and .env, then run: python3 crypto_pay.py --serve")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate the current configuration."""
    try:
        from config_loader import load_config, validate_config  # type: ignore
        cfg = load_config()
        ok = validate_config(cfg)
        if ok:
            _ok("Configuration is valid")
            return 0
        _err("Configuration has errors — see messages above")
        return 1
    except Exception as exc:
        _err(f"Validation failed: {exc}")
        return 1


def cmd_serve(args: argparse.Namespace) -> int:
    """Start the Flask payment server in the foreground."""
    app_py = _SERVER_DIR / "app.py"
    if not app_py.exists():
        _err(f"app.py not found at {app_py}")
        return 1
    _info(f"Starting payment server (Ctrl+C to stop)…")
    venv_python = _INSTALL_DIR / "venv" / "bin" / "python"
    python = str(venv_python) if venv_python.exists() else sys.executable
    result = subprocess.run([python, str(app_py)], cwd=str(_SERVER_DIR), check=False)
    return result.returncode


def cmd_daemon(args: argparse.Namespace) -> int:
    """Start the server as a systemd-managed daemon (or background process)."""
    unit = Path("/etc/systemd/system/crypto-pay.service")
    if unit.exists():
        _info("Starting crypto-pay via systemd…")
        result = subprocess.run(
            ["systemctl", "start", "crypto-pay"], check=False
        )
        if result.returncode == 0:
            _ok("Started crypto-pay service")
        return result.returncode
    else:
        _warn("systemd unit not found — starting in background process")
        app_py = _SERVER_DIR / "app.py"
        venv_python = _INSTALL_DIR / "venv" / "bin" / "python"
        python = str(venv_python) if venv_python.exists() else sys.executable
        proc = subprocess.Popen(
            [python, str(app_py)],
            cwd=str(_SERVER_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _ok(f"Payment server started (PID {proc.pid})")
        return 0


def cmd_prices(args: argparse.Namespace) -> int:
    """Display current cryptocurrency prices."""
    try:
        db = _get_db()
        engine = _get_price_engine(db)
        fiat = (args.fiat or "usd").lower()
        _header(f"Current prices in {fiat.upper()}")
        all_prices = engine.get_all_prices(fiat)
        if not all_prices:
            _warn("No prices available — check your internet connection")
            return 1
        for coin_id, price in sorted(all_prices.items()):
            print(f"  {coin_id:<20} {price:>20,.8f} {fiat.upper()}")
        return 0
    except Exception as exc:
        _err(f"Failed to fetch prices: {exc}")
        return 1


def cmd_convert(args: argparse.Namespace) -> int:
    """Convert a fiat amount to cryptocurrency."""
    if not args.amount or not args.fiat or not args.coin:
        _err("--convert requires --amount, --fiat, and --coin")
        return 1
    try:
        db = _get_db()
        engine = _get_price_engine(db)
        crypto_str = engine.convert(float(args.amount), args.coin.upper(), args.fiat.lower())
        if not crypto_str:
            _err("Could not convert — price unavailable")
            return 1
        price = engine.get_price(args.coin.upper(), args.fiat.lower())
        print(
            f"{args.amount} {args.fiat.upper()} = "
            f"{_BOLD}{crypto_str} {args.coin.upper()}{_RESET} "
            f"(rate: 1 {args.coin.upper()} = {price:,.2f} {args.fiat.upper()})"
        )
        return 0
    except Exception as exc:
        _err(f"Conversion failed: {exc}")
        return 1


def cmd_orders(args: argparse.Namespace) -> int:
    """List orders, optionally filtered by status."""
    try:
        db = _get_db()
        from order_manager import OrderManager  # type: ignore
        from wallet_manager import WalletManager  # type: ignore
        wallet = WalletManager(db)
        engine = _get_price_engine(db)
        cfg = _load_cfg()
        om = OrderManager(
            db=db,
            wallet_manager=wallet,
            price_engine=engine,
            expiry_minutes=cfg.get("payment", {}).get("expiry_minutes", 30),
        )
        order_list = om.list_orders(status=args.status)
        if not order_list:
            _info(f"No orders found{' with status=' + args.status if args.status else ''}")
            return 0

        _header(f"Orders ({len(order_list)} found)")
        for o in order_list:
            status_colour = {
                "completed": _GREEN,
                "expired": _RED,
                "cancelled": _RED,
                "pending": _YELLOW,
                "awaiting_confirmation": _CYAN,
            }.get(o["status"], "")
            print(
                f"  {o['order_id'][:8]}… "
                f"{status_colour}{o['status']:<25}{_RESET} "
                f"{o['fiat_amount']:>8.2f} {(o['fiat_currency'] or '').upper():<5} "
                f"{o['crypto_amount']:<20} {o['crypto_currency']}"
            )
        return 0
    except Exception as exc:
        _err(f"Failed to list orders: {exc}")
        return 1


def cmd_mark_paid(args: argparse.Namespace) -> int:
    """Mark an order as paid (admin action)."""
    if not args.order:
        _err("--mark-paid requires --order <uuid>")
        return 1
    try:
        db = _get_db()
        from order_manager import OrderManager  # type: ignore
        from wallet_manager import WalletManager  # type: ignore
        wallet = WalletManager(db)
        engine = _get_price_engine(db)
        cfg = _load_cfg()
        om = OrderManager(
            db=db,
            wallet_manager=wallet,
            price_engine=engine,
            expiry_minutes=cfg.get("payment", {}).get("expiry_minutes", 30),
        )
        order = om.mark_paid(args.order)
        if not order:
            _err(f"Order not found: {args.order}")
            return 1
        _ok(f"Order {order['order_id']} marked as {order['status']}")
        return 0
    except Exception as exc:
        _err(f"Failed to mark order as paid: {exc}")
        return 1


def cmd_validate_wallets(args: argparse.Namespace) -> int:
    """Validate all wallet addresses stored in the database."""
    try:
        db = _get_db()
        from wallet_manager import WalletManager  # type: ignore
        wallet = WalletManager(db)
        results = wallet.validate_all()
        if not results:
            _warn("No wallet addresses configured")
            return 0
        ok_count = sum(1 for v in results.values() if v)
        fail_count = len(results) - ok_count
        _header("Wallet address validation")
        for key, valid in results.items():
            if valid:
                _ok(key)
            else:
                _err(f"{key} — INVALID")
        print(f"\n  {ok_count} valid, {fail_count} invalid")
        return 0 if fail_count == 0 else 1
    except Exception as exc:
        _err(f"Validation failed: {exc}")
        return 1


def cmd_add_wallet(args: argparse.Namespace) -> int:
    """Add a wallet address to the database."""
    if not args.coin or not args.address:
        _err("--add-wallet requires --coin and --address")
        return 1
    try:
        db = _get_db()
        from wallet_manager import WalletManager  # type: ignore
        wallet = WalletManager(db)
        success = wallet.add_address(args.coin.upper(), args.address, label=args.label or "")
        if success:
            _ok(f"Added {args.coin.upper()} address: {args.address}")
            return 0
        _err(f"Address validation failed for {args.coin}: {args.address}")
        return 1
    except Exception as exc:
        _err(f"Failed to add wallet: {exc}")
        return 1


def cmd_diagnose(args: argparse.Namespace) -> int:
    """Run system diagnostics."""
    _header("crypto-pay system diagnostics")
    issues = 0

    # Python version
    pv = sys.version_info
    if pv >= (3, 8):
        _ok(f"Python {pv.major}.{pv.minor}.{pv.micro}")
    else:
        _err(f"Python {pv.major}.{pv.minor} — requires 3.8+")
        issues += 1

    # Required modules
    for mod in ["flask", "flask_cors", "flask_limiter", "requests", "yaml",
                "dotenv", "loguru", "qrcode"]:
        try:
            __import__(mod.replace("-", "_"))
            _ok(f"Module: {mod}")
        except ImportError:
            _err(f"Module missing: {mod}")
            issues += 1

    # Config file
    cfg_path = _CRYPTO_PAY_DIR / "config.yaml"
    if cfg_path.exists():
        _ok(f"config.yaml found at {cfg_path}")
    else:
        _warn(f"config.yaml not found at {cfg_path} — run --init")

    # .env file
    env_path = _CRYPTO_PAY_DIR / ".env"
    if env_path.exists():
        _ok(f".env found at {env_path}")
        st = env_path.stat()
        if oct(st.st_mode)[-3:] not in ("600", "640"):
            _warn(f".env permissions are {oct(st.st_mode)[-3:]} — should be 600")
    else:
        _warn(".env not found — copy .env.example to .env and fill in secrets")

    # Database directory
    if _DATA_DIR.exists():
        _ok(f"Data directory: {_DATA_DIR}")
    else:
        _warn(f"Data directory not found: {_DATA_DIR} — run --install")

    print(f"\n  {issues} issue(s) found")
    return 0 if issues == 0 else 1


def cmd_test_api(args: argparse.Namespace) -> int:
    """Test the local API endpoints."""
    import urllib.request
    import urllib.error

    cfg = _load_cfg()
    host = cfg.get("server", {}).get("host", "127.0.0.1")
    port = cfg.get("server", {}).get("port", 5000)
    base = f"http://{host}:{port}"

    _header(f"Testing API at {base}")
    endpoints = [
        "/api/health",
        "/api/supported-coins",
        "/api/prices?fiat=usd",
    ]
    ok_count = 0
    for path in endpoints:
        url = base + path
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read())
                _ok(f"GET {path} → {resp.status}")
                ok_count += 1
        except urllib.error.URLError as exc:
            _err(f"GET {path} → {exc}")

    print(f"\n  {ok_count}/{len(endpoints)} endpoints reachable")
    return 0 if ok_count == len(endpoints) else 1


def cmd_backup(args: argparse.Namespace) -> int:
    """Create a backup of the database and configuration."""
    backup_dir = Path("./backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = backup_dir / f"crypto-pay-backup-{ts}.tar.gz"

    with tarfile.open(str(archive_path), "w:gz") as tar:
        for src in [
            _CRYPTO_PAY_DIR / "config.yaml",
            _CRYPTO_PAY_DIR / ".env",
        ]:
            if src.exists():
                tar.add(str(src), arcname=src.name)

        db_path_candidates = [
            _DATA_DIR / "crypto_pay.db",
            _CRYPTO_PAY_DIR / "crypto_pay.db",
        ]
        for db_path in db_path_candidates:
            if db_path.exists():
                tar.add(str(db_path), arcname="crypto_pay.db")
                break

    _ok(f"Backup created: {archive_path}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    """Restore configuration and database from a backup archive."""
    backup_file = args.backup  # set by --backup-path
    if not backup_file:
        _err("--restore requires --backup-path <path>")
        return 1

    backup_path = Path(backup_file)
    if not backup_path.exists():
        _err(f"Backup file not found: {backup_path}")
        return 1

    restore_dir = Path("/tmp/crypto-pay-restore")
    if restore_dir.exists():
        shutil.rmtree(restore_dir)
    restore_dir.mkdir(parents=True)

    with tarfile.open(str(backup_path), "r:gz") as tar:
        tar.extractall(str(restore_dir))

    for name, dst in [
        ("config.yaml", _CRYPTO_PAY_DIR / "config.yaml"),
        (".env", _CRYPTO_PAY_DIR / ".env"),
        ("crypto_pay.db", _DATA_DIR / "crypto_pay.db"),
    ]:
        src = restore_dir / name
        if src.exists():
            shutil.copy2(str(src), str(dst))
            _ok(f"Restored: {dst}")
            if name == ".env":
                dst.chmod(0o600)

    shutil.rmtree(restore_dir)
    _ok("Restore complete")
    return 0


# ===========================================================================
# Site injector
# ===========================================================================

_INJECTION_MARKER_START = "<!-- crypto-pay-injected -->"
_INJECTION_MARKER_END = "<!-- /crypto-pay-injected -->"

# Injection snippet appended before </body>
_INJECT_SNIPPET = """\
{marker_start}
<link rel="stylesheet" href="{css_url}">
<script src="{js_url}"></script>
<script>
(function(){{
  if(typeof CryptoPay==='undefined') return;
  CryptoPay.init({{apiBase:'{api_base}'}});
  document.querySelectorAll('[data-crypto-pay-amount]').forEach(function(el){{
    var btn=document.createElement('button');
    btn.className='crypto-pay-trigger-btn';
    btn.textContent='Pay with Crypto';
    btn.style.marginLeft='8px';
    btn.addEventListener('click',function(){{
      CryptoPay.openModal({{
        fiat_amount:parseFloat(el.dataset.cryptoPayAmount)||0,
        fiat_currency:(el.dataset.cryptoPayFiat||'usd').toLowerCase(),
        crypto_currency:(el.dataset.cryptoPayCoin||'BTC').toUpperCase(),
        product_name:el.dataset.cryptoPayProduct||'',
        page_url:window.location.href
      }});
    }});
    el.parentNode.insertBefore(btn,el.nextSibling);
  }});
}});
</script>
{marker_end}
"""


class SiteInjector:
    """Detect and inject crypto-pay into static websites.

    Supports HTML files, PHP files, WordPress themes, and SPA index files.
    All modifications are idempotent and fully reversible.
    """

    def __init__(
        self,
        site_path: str,
        api_base: str = "/api",
        css_url: str = "/crypto-pay/css/payment.css",
        js_url: str = "/crypto-pay/js/crypto-pay.js",
        dry_run: bool = False,
        backup_dir: Optional[str] = None,
    ) -> None:
        """Initialise the injector.

        Args:
            site_path: Root directory of the site to inject.
            api_base: URL prefix for the crypto-pay API.
            css_url: URL to payment.css.
            js_url: URL to crypto-pay.js.
            dry_run: If True, show what would be done without modifying files.
            backup_dir: Directory for backups (default: <site_path>/../backups).
        """
        self.site_path = Path(site_path).resolve()
        self.api_base = api_base
        self.css_url = css_url
        self.js_url = js_url
        self.dry_run = dry_run
        self.backup_dir = (
            Path(backup_dir) if backup_dir
            else self.site_path.parent / "backups"
        )
        self._modified: List[Path] = []
        self._skipped: List[Path] = []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def inject(self) -> Tuple[int, int]:
        """Inject crypto-pay into all eligible files under site_path.

        Returns:
            Tuple of (modified_count, skipped_count).
        """
        if not self.site_path.is_dir():
            _err(f"Site path not found: {self.site_path}")
            return 0, 0

        html_files = list(self.site_path.rglob("*.html")) + list(self.site_path.rglob("*.php"))
        if not html_files:
            _warn(f"No HTML/PHP files found under {self.site_path}")
            return 0, 0

        for fpath in html_files:
            self._process_file(fpath)

        return len(self._modified), len(self._skipped)

    def remove(self) -> int:
        """Remove previously injected crypto-pay snippets.

        Returns:
            Number of files cleaned.
        """
        cleaned = 0
        for fpath in list(self.site_path.rglob("*.html")) + list(self.site_path.rglob("*.php")):
            content = fpath.read_text(encoding="utf-8", errors="replace")
            if _INJECTION_MARKER_START not in content:
                continue
            new_content = re.sub(
                re.escape(_INJECTION_MARKER_START) + r".*?" + re.escape(_INJECTION_MARKER_END),
                "",
                content,
                flags=re.DOTALL,
            )
            if new_content != content:
                if not self.dry_run:
                    fpath.write_text(new_content, encoding="utf-8")
                _ok(f"{'[DRY RUN] ' if self.dry_run else ''}Removed injection from {fpath}")
                cleaned += 1
        return cleaned

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _process_file(self, fpath: Path) -> None:
        """Inject snippet into a single HTML/PHP file if eligible.

        Args:
            fpath: Path to the file to process.
        """
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            _warn(f"Cannot read {fpath}: {exc}")
            self._skipped.append(fpath)
            return

        # Skip if already injected (idempotent)
        if _INJECTION_MARKER_START in content:
            self._skipped.append(fpath)
            return

        # Only inject into files with a </body> tag
        if "</body>" not in content.lower():
            self._skipped.append(fpath)
            return

        snippet = _INJECT_SNIPPET.format(
            marker_start=_INJECTION_MARKER_START,
            marker_end=_INJECTION_MARKER_END,
            css_url=self.css_url,
            js_url=self.js_url,
            api_base=self.api_base,
        )

        # Insert snippet before the LAST </body> tag
        lower_content = content.lower()
        last_body_pos = lower_content.rfind("</body>")
        if last_body_pos == -1:
            self._skipped.append(fpath)
            return

        new_content = content[:last_body_pos] + snippet + "\n" + content[last_body_pos:]

        if new_content == content:
            self._skipped.append(fpath)
            return

        if not self.dry_run:
            self._backup_file(fpath)
            fpath.write_text(new_content, encoding="utf-8")

        _ok(f"{'[DRY RUN] ' if self.dry_run else ''}Injected: {fpath}")
        self._modified.append(fpath)

    def _backup_file(self, fpath: Path) -> None:
        """Create a backup copy of a file before modifying it.

        Args:
            fpath: Path to the file to back up.
        """
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        rel = fpath.relative_to(self.site_path)
        backup_path = self.backup_dir / ts / rel
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(fpath), str(backup_path))


def cmd_inject(args: argparse.Namespace) -> int:
    """Inject crypto-pay into a site (or all configured sites)."""
    site = args.site or str(Path("/var/www/html"))
    dry = getattr(args, "dry_run", False)

    _header(f"{'[DRY RUN] ' if dry else ''}Injecting into: {site}")
    injector = SiteInjector(
        site_path=site,
        dry_run=dry,
    )
    modified, skipped = injector.inject()
    print(f"\n  Modified: {modified}  Skipped (already injected / no </body>): {skipped}")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    """Remove previously injected snippets from a site."""
    site = args.site or str(Path("/var/www/html"))
    _header(f"Removing injection from: {site}")
    injector = SiteInjector(site_path=site)
    cleaned = injector.remove()
    print(f"\n  Cleaned: {cleaned} file(s)")
    return 0


def cmd_reinject(args: argparse.Namespace) -> int:
    """Remove and re-inject into all configured sites."""
    site = args.site or str(Path("/var/www/html"))
    _header(f"Re-injecting: {site}")
    injector = SiteInjector(site_path=site)
    removed = injector.remove()
    modified, skipped = injector.inject()
    print(f"\n  Removed: {removed}  Modified: {modified}  Skipped: {skipped}")
    return 0


# ===========================================================================
# Argument parser
# ===========================================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crypto_pay.py",
        description="crypto-pay — self-hosted cryptocurrency payment gateway CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Actions
    act = parser.add_mutually_exclusive_group()
    act.add_argument("--install",         action="store_true", help="Run full installer")
    act.add_argument("--init",            action="store_true", help="Generate config files")
    act.add_argument("--validate",        action="store_true", help="Validate configuration")
    act.add_argument("--serve",           action="store_true", help="Start payment server")
    act.add_argument("--daemon",          action="store_true", help="Start as daemon")
    act.add_argument("--prices",          action="store_true", help="Show current prices")
    act.add_argument("--convert",         action="store_true", help="Convert fiat to crypto")
    act.add_argument("--orders",          action="store_true", help="List orders")
    act.add_argument("--mark-paid",       action="store_true", help="Mark order as paid")
    act.add_argument("--validate-wallets",action="store_true", help="Validate all wallet addresses")
    act.add_argument("--add-wallet",      action="store_true", help="Add a wallet address")
    act.add_argument("--inject",          action="store_true", help="Inject into sites")
    act.add_argument("--remove",          action="store_true", help="Remove injection from site")
    act.add_argument("--reinject",        action="store_true", help="Remove and re-inject")
    act.add_argument("--diagnose",        action="store_true", help="System diagnostics")
    act.add_argument("--test-api",        action="store_true", help="Test API endpoints")
    act.add_argument("--backup",          action="store_true", help="Backup database + config")
    act.add_argument("--restore",         action="store_true", help="Restore from backup")

    # Shared options
    parser.add_argument("--fiat",         help="Fiat currency code (e.g. USD)")
    parser.add_argument("--coin",         help="Coin ticker (e.g. BTC)")
    parser.add_argument("--amount",       help="Fiat amount for conversion")
    parser.add_argument("--order",        help="Order UUID for --mark-paid")
    parser.add_argument("--status",       help="Order status filter for --orders")
    parser.add_argument("--site",         help="Site path for injection commands")
    parser.add_argument("--backup-path",  dest="backup", help="Backup file path for --restore")
    parser.add_argument("--address",      help="Wallet address for --add-wallet")
    parser.add_argument("--label",        help="Label for --add-wallet")
    parser.add_argument("--dry-run",      action="store_true", help="Preview changes without applying")

    return parser


# ===========================================================================
# Entry point
# ===========================================================================

def main() -> int:
    """Parse arguments and dispatch to the appropriate command handler.

    Returns:
        Exit code.
    """
    parser = _build_parser()
    args = parser.parse_args()

    dispatch = {
        "install":          cmd_install,
        "init":             cmd_init,
        "validate":         cmd_validate,
        "serve":            cmd_serve,
        "daemon":           cmd_daemon,
        "prices":           cmd_prices,
        "convert":          cmd_convert,
        "orders":           cmd_orders,
        "mark_paid":        cmd_mark_paid,
        "validate_wallets": cmd_validate_wallets,
        "add_wallet":       cmd_add_wallet,
        "inject":           cmd_inject,
        "remove":           cmd_remove,
        "reinject":         cmd_reinject,
        "diagnose":         cmd_diagnose,
        "test_api":         cmd_test_api,
        "backup":           cmd_backup,
        "restore":          cmd_restore,
    }

    for flag, handler in dispatch.items():
        if getattr(args, flag.replace("-", "_"), False):
            return handler(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
