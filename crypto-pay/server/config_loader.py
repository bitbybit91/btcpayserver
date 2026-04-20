"""YAML + .env configuration loader for crypto-pay."""

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv

from logger_setup import get_logger

log = get_logger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
_DEFAULT_ENV_PATH = Path(__file__).parent.parent / ".env"


def load_config(
    config_path: Optional[str] = None,
    env_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Load and merge YAML config with .env secrets.

    The .env values override any matching keys in the YAML file so that
    secrets never have to live in version-controlled config files.

    Args:
        config_path: Path to the YAML config file.  Defaults to
            ``<repo-root>/config.yaml``.
        env_path: Path to the .env file.  Defaults to ``<repo-root>/.env``.

    Returns:
        Merged configuration dictionary.
    """
    env_file = Path(env_path) if env_path else _DEFAULT_ENV_PATH
    if env_file.exists():
        load_dotenv(dotenv_path=str(env_file), override=True)
        log.info(f"Loaded .env from {env_file}")
    else:
        load_dotenv(override=False)
        log.warning(f".env not found at {env_file}; relying on shell environment")

    yaml_file = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
    if not yaml_file.exists():
        log.warning(f"config.yaml not found at {yaml_file}; using defaults")
        config: Dict[str, Any] = {}
    else:
        with open(yaml_file, "r", encoding="utf-8") as fh:
            config = yaml.safe_load(fh) or {}
        log.info(f"Loaded config from {yaml_file}")

    config = _apply_env_overrides(config)
    config = _apply_defaults(config)
    return config


def _apply_env_overrides(config: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay selected environment variables onto the config dict.

    Args:
        config: Base configuration dictionary.

    Returns:
        Updated configuration dictionary.
    """
    env_map = {
        "CRYPTO_PAY_SECRET_KEY": ("flask", "secret_key"),
        "CRYPTO_PAY_ADMIN_API_KEY": ("admin", "api_key"),
        "CRYPTO_PAY_DB_PATH": ("database", "path"),
        "CRYPTO_PAY_HOST": ("server", "host"),
        "CRYPTO_PAY_PORT": ("server", "port"),
        "COINGECKO_API_KEY": ("price_engine", "coingecko_api_key"),
        "CRYPTO_PAY_WEBHOOK_SECRET": ("webhook", "secret"),
    }

    for env_var, (section, key) in env_map.items():
        value = os.environ.get(env_var)
        if value is not None:
            if not isinstance(config.get(section), dict):
                config[section] = {}
            config[section][key] = value

    return config


def _apply_defaults(config: Dict[str, Any]) -> Dict[str, Any]:
    """Fill in any missing keys with safe defaults.

    Args:
        config: Partially populated configuration dictionary.

    Returns:
        Configuration dictionary with all defaults applied.
    """
    defaults: Dict[str, Any] = {
        "flask": {
            "secret_key": "changeme-insecure-default",
            "debug": False,
        },
        "server": {
            "host": "127.0.0.1",
            "port": 5000,
        },
        "database": {
            "path": "/var/lib/crypto-pay/crypto_pay.db",
        },
        "admin": {
            "api_key": "",
        },
        "price_engine": {
            "cache_ttl": 60,
            "stale_warn_after": 300,
            "stale_hard_limit": 900,
            "coingecko_api_key": "",
            "fiat_currencies": ["usd", "eur", "gbp"],
        },
        "payment": {
            "expiry_minutes": 30,
            "confirmations_required": 1,
        },
        "cors": {
            "origins": ["http://127.0.0.1"],
        },
        "rate_limit": {
            "per_ip_per_minute": 30,
        },
        "webhook": {
            "secret": "",
            "timeout": 10,
            "retries": 3,
        },
        "logging": {
            "level": "INFO",
            "dir": "/var/log/crypto-pay",
        },
    }

    for section, values in defaults.items():
        config.setdefault(section, {})
        for key, default_value in values.items():
            config[section].setdefault(key, default_value)

    return config


def validate_config(config: Dict[str, Any]) -> bool:
    """Validate mandatory configuration fields.

    Args:
        config: Configuration dictionary to validate.

    Returns:
        True if the configuration is valid, False otherwise.
    """
    errors: list = []

    if config.get("flask", {}).get("secret_key") in ("", "changeme-insecure-default"):
        errors.append("flask.secret_key must be set to a strong random value")

    if not config.get("admin", {}).get("api_key"):
        errors.append("admin.api_key must be set")

    wallets = config.get("wallets", {})
    if not wallets:
        log.warning("No wallet addresses configured — orders cannot be created")

    for error in errors:
        log.error(f"Config validation error: {error}")

    return len(errors) == 0
