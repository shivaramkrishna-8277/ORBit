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

from src.utils import db

IST = pytz.timezone("Asia/Kolkata")

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


def load_settings() -> StrategySettings:
    db.init_db()
    row = db.get_strategy_settings()
    if row is None:
        settings = default_settings()
        save_settings(settings)
        return load_settings()

    return StrategySettings(
        symbols=json.loads(row["symbols_json"]),
        max_price_enabled=bool(row["max_price_enabled"]),
        max_stock_price=float(row["max_stock_price"]),
        orb_range_threshold=float(row["orb_range_threshold"]),
        orb_duration_minutes=int(row["orb_duration_minutes"]),
        breakout_timeframe_minutes=int(row["breakout_timeframe_minutes"]),
        updated_at=row["updated_at"],
    )


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
    return settings


def enqueue_recalc() -> int:
    db.init_db()
    created = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    return db.enqueue_command("recalc", created)
