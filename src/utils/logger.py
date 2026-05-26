"""Logging setup — dual handler (console + IST-stamped daily rotating file).

Usage:
    from src.utils.logger import get_logger
    logger = get_logger(__name__)
    logger.info("Bot started")
"""
import logging
import sys
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import pytz

# ── Constants ─────────────────────────────────────────────────────────────────
IST = pytz.timezone("Asia/Kolkata")

_LOGS_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
_LOGS_DIR.mkdir(exist_ok=True)

_CONSOLE_FORMAT = "[%(asctime)s] [%(levelname)-8s] [%(name)s] - %(message)s"
_FILE_FORMAT = (
    "[%(asctime)s] [%(levelname)-8s] [%(name)s] "
    "[%(funcName)s:%(lineno)d] - %(message)s"
)

# Cache loggers so duplicate handlers are never added on repeat calls
_loggers: dict[str, logging.Logger] = {}


# ── Custom formatter (IST timestamps) ─────────────────────────────────────────

class _ISTFormatter(logging.Formatter):
    """Formatter that renders timestamps in Asia/Kolkata (IST) timezone."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        ist_time = datetime.fromtimestamp(record.created, tz=IST)
        if datefmt:
            return ist_time.strftime(datefmt)
        return ist_time.strftime("%Y-%m-%d %H:%M:%S IST")


# ── Handler builders ───────────────────────────────────────────────────────────

def _build_console_handler(level: int) -> logging.StreamHandler:
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(_ISTFormatter(_CONSOLE_FORMAT))
    return handler


def _build_file_handler() -> TimedRotatingFileHandler:
    """
    Creates a TimedRotatingFileHandler that writes to logs/YYYY-MM-DD.log.
    The file is named for the current IST date at startup. At midnight the
    handler rotates and appends the old date as a suffix on the archived file.
    """
    today = datetime.now(IST).strftime("%Y-%m-%d")
    log_path = _LOGS_DIR / f"{today}.log"

    handler = TimedRotatingFileHandler(
        filename=str(log_path),
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
        utc=False,
    )
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(_ISTFormatter(_FILE_FORMAT))
    # Archived (rotated) files get the old date appended: YYYY-MM-DD.log.YYYY-MM-DD
    handler.suffix = "%Y-%m-%d"
    return handler


# ── Public API ─────────────────────────────────────────────────────────────────

def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger configured with a console handler (INFO level, or
    the LOG_LEVEL from .env) and a daily rotating file handler (DEBUG level).

    Safe to call multiple times with the same name — returns the cached instance.

    Args:
        name: Logger name, typically ``__name__`` of the calling module.

    Returns:
        Configured :class:`logging.Logger` instance.
    """
    if name in _loggers:
        return _loggers[name]

    # Resolve console level from config (fall back to INFO if config not yet loaded)
    try:
        from src.config import LOG_LEVEL  # noqa: PLC0415
        console_level = getattr(logging, LOG_LEVEL, logging.INFO)
    except Exception:
        console_level = logging.INFO

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)  # capture everything; handlers apply their own levels
    logger.propagate = False

    if not logger.handlers:
        logger.addHandler(_build_console_handler(console_level))
        logger.addHandler(_build_file_handler())

    _loggers[name] = logger
    return logger
