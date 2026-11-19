"""Live strategy settings shared by the bot and the Streamlit dashboard.

Stored in SQLite so a settings change survives a restart and is visible to
both processes. The first load seeds the previous Nifty 50 / ₹800 behaviour
so an upgrade does not silently change what is tracked.
"""
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytz

from src.config import CONFIG_DIR
from src.utils import db
from src.utils.logger import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")
_SETTINGS_JSON = CONFIG_DIR / "strategy_settings.json"

ORB_DURATIONS = (5, 15, 30)
BREAKOUT_TIMEFRAMES = (1, 5, 15, 30)
_SYMBOL_BODY = re.compile(r"^[A-Z0-9&-]+$")


@dataclass
class StrategySettings:
    symbols: list[str]
    max_price_enabled: bool
    max_stock_price: float
    orb_range_threshold: float
    orb_duration_minutes: int
    breakout_timeframe_minutes: int
    updated_at: str = ""

    @property
    def orb_end_label(self) -> str:
        """Clock time when the opening range ends, always starting at 09:15."""
        end = datetime(2000, 1, 1, 9, 15) + timedelta(minutes=self.orb_duration_minutes)
        return end.strftime("%H:%M")


def normalize_symbol(raw: str) -> str:
    """Accept RELIANCE, NSE:RELIANCE-EQ, or reliance and return NSE:RELIANCE-EQ."""
    text = raw.strip().upper()
    if not text:
        raise ValueError("Enter a symbol.")
    if text.startswith("NSE:"):
        text = text[4:]
    if text.endswith("-EQ"):
        text = text[:-3]
    if not text or not _SYMBOL_BODY.fullmatch(text):
        raise ValueError(
            f"'{raw.strip()}' is not a usable NSE equity symbol. "
            "Use the ticker, for example RELIANCE or NSE:RELIANCE-EQ."
        )
    return f"NSE:{text}-EQ"


def validate_settings(settings: StrategySettings) -> StrategySettings:
    symbols: list[str] = []
    for raw in settings.symbols:
        symbol = raw if raw.startswith("NSE:") else normalize_symbol(raw)
        if symbol not in symbols:
            symbols.append(symbol)
    symbols.sort()

    if settings.orb_duration_minutes not in ORB_DURATIONS:
        raise ValueError(f"Opening range must be one of {', '.join(map(str, ORB_DURATIONS))} minutes.")
    if settings.breakout_timeframe_minutes not in BREAKOUT_TIMEFRAMES:
        raise ValueError(
            f"Breakout timeframe must be one of {', '.join(map(str, BREAKOUT_TIMEFRAMES))} minutes."
        )
    if not 0 < settings.orb_range_threshold <= 20:
        raise ValueError("ORB percentage must be greater than 0 and at most 20.")
    if settings.max_price_enabled and settings.max_stock_price <= 0:
        raise ValueError("Max price must be greater than 0 when the price filter is on.")

    settings.symbols = symbols
    return settings


def default_settings() -> StrategySettings:
    """Match the bot's previous fixed rules until the dashboard changes them."""
    from src.config import MAX_STOCK_PRICE, NIFTY50_SYMBOLS, ORB_RANGE_THRESHOLD

    return StrategySettings(
        symbols=sorted(NIFTY50_SYMBOLS),
        max_price_enabled=True,
        max_stock_price=float(MAX_STOCK_PRICE),
        orb_range_threshold=float(ORB_RANGE_THRESHOLD),
        orb_duration_minutes=15,
        breakout_timeframe_minutes=15,
        updated_at="",
    )


def _settings_from_row(row: dict) -> StrategySettings:
    return StrategySettings(
        symbols=json.loads(row["symbols_json"]),
        max_price_enabled=bool(row["max_price_enabled"]),
        max_stock_price=float(row["max_stock_price"]),
        orb_range_threshold=float(row["orb_range_threshold"]),
        orb_duration_minutes=int(row["orb_duration_minutes"]),
        breakout_timeframe_minutes=int(row["breakout_timeframe_minutes"]),
        updated_at=row["updated_at"],
    )


def _settings_to_dict(settings: StrategySettings) -> dict:
    return {
        "symbols": settings.symbols,
        "max_price_enabled": settings.max_price_enabled,
        "max_stock_price": settings.max_stock_price,
        "orb_range_threshold": settings.orb_range_threshold,
        "orb_duration_minutes": settings.orb_duration_minutes,
        "breakout_timeframe_minutes": settings.breakout_timeframe_minutes,
        "updated_at": settings.updated_at,
    }


def _write_json_backup(settings: StrategySettings) -> None:
    """Mirror settings on the mounted config volume (survives SQLite issues / redeploys)."""
    CONFIG_DIR.mkdir(exist_ok=True)
    payload = _settings_to_dict(settings)
    tmp = _SETTINGS_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(_SETTINGS_JSON)


def _read_json_backup() -> StrategySettings | None:
    if not _SETTINGS_JSON.is_file():
        return None
    try:
        data = json.loads(_SETTINGS_JSON.read_text(encoding="utf-8"))
        return StrategySettings(
            symbols=list(data["symbols"]),
            max_price_enabled=bool(data["max_price_enabled"]),
            max_stock_price=float(data["max_stock_price"]),
            orb_range_threshold=float(data["orb_range_threshold"]),
            orb_duration_minutes=int(data["orb_duration_minutes"]),
            breakout_timeframe_minutes=int(data["breakout_timeframe_minutes"]),
            updated_at=str(data.get("updated_at", "")),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("Could not read %s: %s", _SETTINGS_JSON, exc)
        return None


def load_settings() -> StrategySettings:
    db.init_db()
    row = db.get_strategy_settings()
    if row is not None:
        return _settings_from_row(row)

    backup = _read_json_backup()
    if backup is not None:
        logger.info("Restored strategy settings from %s", _SETTINGS_JSON)
        return save_settings(backup)

    settings = default_settings()
    logger.info("No saved strategy found — seeding Nifty 50 defaults.")
    return save_settings(settings)


def save_settings(settings: StrategySettings) -> StrategySettings:
    settings = validate_settings(settings)
    settings.updated_at = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    db.init_db()
    db.save_strategy_settings(
        symbols_json=json.dumps(settings.symbols),
        max_price_enabled=int(settings.max_price_enabled),
        max_stock_price=float(settings.max_stock_price),
        orb_range_threshold=float(settings.orb_range_threshold),
        orb_duration_minutes=int(settings.orb_duration_minutes),
        breakout_timeframe_minutes=int(settings.breakout_timeframe_minutes),
        updated_at=settings.updated_at,
    )
    _write_json_backup(settings)
    return settings


def update_watchlist(symbols: list[str]) -> StrategySettings:
    """Persist symbol list without touching ORB rule fields."""
    current = load_settings()
    current.symbols = list(symbols)
    return save_settings(current)


def enqueue_recalc() -> int:
    db.init_db()
    created = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    return db.enqueue_command("recalc", created)


def enqueue_fyers_auth() -> int:
    db.init_db()
    if db.has_active_command("fyers_auth"):
        raise ValueError("A Fyers login request is already pending or in progress.")
    created = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    return db.enqueue_command("fyers_auth", created)
