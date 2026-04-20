"""Loguru-based logging configuration for crypto-pay server."""

import sys
from pathlib import Path
from loguru import logger


def setup_logger(log_level: str = "INFO", log_dir: str = "/var/log/crypto-pay") -> None:
    """Configure loguru logger with file and console sinks.

    Args:
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_dir: Directory where log files are stored.
    """
    logger.remove()

    logger.add(
        sys.stderr,
        level=log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> — "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    log_path = Path(log_dir)
    try:
        log_path.mkdir(parents=True, exist_ok=True)

        logger.add(
            str(log_path / "crypto-pay.log"),
            level=log_level,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} — {message}",
            rotation="10 MB",
            retention="30 days",
            compression="gz",
            enqueue=True,
        )

        logger.add(
            str(log_path / "errors.log"),
            level="ERROR",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} — {message}",
            rotation="5 MB",
            retention="60 days",
            compression="gz",
            enqueue=True,
        )
    except PermissionError:
        logger.warning(f"Cannot write logs to {log_dir}. Console-only logging enabled.")


def get_logger(name: str):
    """Return a logger bound to the given module name.

    Args:
        name: Module or component name used in log records.

    Returns:
        A loguru logger instance.
    """
    return logger.bind(name=name)
