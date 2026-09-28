"""
Structured logging utility with file and console output.
Supports rotation and structured JSON for production.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from config.settings import get_settings


class StructuredFormatter(logging.Formatter):
    """Custom formatter that outputs structured JSON for log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra_data"):
            log_entry["extra"] = record.extra_data  # type: ignore[arg-type]
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, default=str)


class ColoredConsoleFormatter(logging.Formatter):
    """Console formatter with color-coded log levels."""

    COLORS = {
        "DEBUG": "\033[36m",      # Cyan
        "INFO": "\033[32m",       # Green
        "WARNING": "\033[33m",    # Yellow
        "ERROR": "\033[31m",      # Red
        "CRITICAL": "\033[1;31m", # Bold Red
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        level_color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{level_color}{record.levelname}{self.RESET}"
        return super().format(record)


def setup_logger(name: str = "dedan_remote") -> logging.Logger:
    """
    Configure and return a structured logger.

    Args:
        name: Logger name (typically __name__).

    Returns:
        Configured logger instance.
    """
    settings = get_settings()
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    # Prevent duplicate handlers
    if logger.handlers:
        return logger

    # ── File handler with rotation ─────────────────────────────────────
    log_path = Path(settings.LOG_FILE)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        filename=str(log_path),
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(StructuredFormatter())
    logger.addHandler(file_handler)

    # ── Console handler ────────────────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
    console_handler.setFormatter(ColoredConsoleFormatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(console_handler)

    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Get a configured logger. Uses module name if not specified.

    Args:
        name: Optional logger name. Auto-derived from caller if not provided.

    Returns:
        Logger instance.
    """
    if name:
        return setup_logger(name)
    # Auto-detect caller module
    frame = sys._getframe(1)
    module = frame.f_globals.get("__name__", "unknown")
    return setup_logger(module)


def log_extra(logger: logging.Logger, level: str, message: str, **extra: object) -> None:
    """
    Log a message with extra structured data.

    Args:
        logger: Logger instance.
        level: Log level (debug, info, warning, error, critical).
        message: Log message.
        **extra: Additional key-value pairs to include.
    """
    level = level.upper()
    log_method = getattr(logger, level.lower(), None)
    if log_method is None:
        raise ValueError(f"Invalid log level: {level}")

    record = logger.makeRecord(
        logger.name,
        getattr(logging, level),
        "", 0, message, (), None,
    )
    record.extra_data = extra  # type: ignore[attr-defined]
    log_method(message)