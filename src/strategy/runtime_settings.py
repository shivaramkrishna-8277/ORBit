"""Live strategy settings shared by the bot and the Streamlit dashboard.

Stored in SQLite so a settings change survives a restart and is visible to
both processes. The first load seeds the previous Nifty 50 / ₹800 behaviour
so an upgrade does not silently change what is tracked.
"""
import json
import re
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta

import pytz

from src.config import CONFIG_DIR
from src.utils import db
from src.utils.orb_math import parse_cutoff
from src.utils.logger import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")
_SETTINGS_JSON = CONFIG_DIR / "strategy_settings.json"

ORB_DURATIONS = (5, 15, 30, 60)
RANGE_MODES = ("lte", "gte")
BREAKOUT_TIMEFRAMES = (1, 5, 15, 30)
RETEST_WINDOWS = (15, 30, 60)
_SYMBOL_BODY = re.compile(r"^[A-Z0-9&-]+$")


@dataclass
class StrategySettings:
    symbols: list[str]
    max_price_enabled: bool
    max_stock_price: float
    orb_range_threshold: float
    orb_duration_minutes: int
    breakout_timeframe_minutes: int
    orb_range_mode: str = "lte"
    entry_cutoff: str = "15:15"
    index_symbols: list[str] | None = None
    track_retest: bool = False
    retest_within_minutes: int = 30
    entry_mode: str = "retest"
    universe: str = "watchlist"
    capital: float = 40_000
    margin_multiplier: float = 5
    risk_pct: float = 1.5
    day_risk_pct: float = 3
    max_trades_per_day: int = 2
    rrr: float = 2
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
    if settings.orb_range_mode not in RANGE_MODES:
        raise ValueError("ORB range rule must be less than or greater than.")
    if not 0 < settings.orb_range_threshold <= 20:
        raise ValueError("ORB percentage must be greater than 0 and at most 20.")
    cutoff = parse_cutoff(settings.entry_cutoff)
    if not dtime(9, 15) < cutoff <= dtime(15, 15):
        raise ValueError("No-entry time must be after 09:15 and no later than 15:15.")
    settings.entry_cutoff = cutoff.strftime("%H:%M")
    if settings.retest_within_minutes not in RETEST_WINDOWS:
        raise ValueError("Retest window must be 15, 30, or 60 minutes.")
    if settings.entry_mode not in ("direct", "retest"):
        raise ValueError("Entry must be a direct breakout or a retest.")
    if settings.universe not in ("watchlist", "indices", "both"):
        raise ValueError("Symbols must be the equity watchlist, indices, or both.")
    if settings.capital <= 0:
        raise ValueError("Capital must be greater than 0.")
    if settings.margin_multiplier not in (4, 5, 10):
        raise ValueError("Margin must be 4×, 5×, or 10×.")
    if not 0 < settings.risk_pct <= 10:
        raise ValueError("Risk per trade must be greater than 0 and at most 10%.")
    if not 0 < settings.day_risk_pct <= 20:
        raise ValueError("Risk per day must be greater than 0 and at most 20%.")
    if settings.max_trades_per_day < 1:
        raise ValueError("Max trades per day must be at least 1.")
    if settings.rrr <= 0:
        raise ValueError("Reward to risk must be greater than 0.")
    settings.track_retest = settings.entry_mode == "retest"
    from src.strategy.indices import INDEX_CATALOG

    allowed = {symbol for symbol, _label in INDEX_CATALOG}
    indices: list[str] = []
    for symbol in settings.index_symbols or []:
        if symbol in allowed and symbol not in indices:
            indices.append(symbol)
    settings.index_symbols = indices
    if settings.max_price_enabled and settings.max_stock_price <= 0:
        raise ValueError("Max price must be greater than 0 when the price filter is on.")

    settings.symbols = symbols
    return settings


def default_settings() -> StrategySettings:
    """Match the bot's previous fixed rules until the dashboard changes them."""
    from src.config import MAX_STOCK_PRICE, NIFTY50_SYMBOLS

    return StrategySettings(
        symbols=sorted(NIFTY50_SYMBOLS),
        max_price_enabled=True,
        max_stock_price=float(MAX_STOCK_PRICE),
        orb_range_threshold=0.7,
        orb_range_mode="lte",
        orb_duration_minutes=60,
        breakout_timeframe_minutes=5,
        entry_cutoff="15:15",
        index_symbols=["NSE:NIFTY50-INDEX"],
        track_retest=True,
        retest_within_minutes=30,
        entry_mode="retest",
        universe="watchlist",
        capital=40_000,
        margin_multiplier=5,
        risk_pct=1.5,
        day_risk_pct=3,
        max_trades_per_day=2,
        rrr=2,
        updated_at="",
    )


def _settings_from_row(row: dict) -> StrategySettings:
    return StrategySettings(
        symbols=json.loads(row["symbols_json"]),
        max_price_enabled=bool(row["max_price_enabled"]),
        max_stock_price=float(row["max_stock_price"]),
        orb_range_threshold=float(row["orb_range_threshold"]),
        orb_range_mode=str(row["orb_range_mode"]) if row["orb_range_mode"] else "lte",
        orb_duration_minutes=int(row["orb_duration_minutes"]),
        breakout_timeframe_minutes=int(row["breakout_timeframe_minutes"]),
        entry_cutoff=str(row["entry_cutoff"] or "15:15") if "entry_cutoff" in row.keys() else "15:15",
        index_symbols=json.loads(row["index_symbols_json"]) if row.get("index_symbols_json") else ["NSE:NIFTY50-INDEX"],
        track_retest=bool(row.get("track_retest") or 0),
        retest_within_minutes=int(row.get("retest_within_minutes") or 30),
        entry_mode=str(row.get("entry_mode") or ("retest" if row.get("track_retest") else "direct")),
        universe=str(row.get("universe") or "watchlist"),
        capital=float(row.get("capital") or 40_000),
        margin_multiplier=float(row.get("margin_multiplier") or 5),
        risk_pct=float(row.get("risk_pct") or 1.5),
        day_risk_pct=float(row.get("day_risk_pct") or 3),
        max_trades_per_day=int(row.get("max_trades_per_day") or 2),
        rrr=float(row.get("rrr") or 2),
        updated_at=row["updated_at"],
    )


def _settings_to_dict(settings: StrategySettings) -> dict:
    return {
        "symbols": settings.symbols,
        "max_price_enabled": settings.max_price_enabled,
        "max_stock_price": settings.max_stock_price,
        "orb_range_threshold": settings.orb_range_threshold,
        "orb_range_mode": settings.orb_range_mode,
        "orb_duration_minutes": settings.orb_duration_minutes,
        "breakout_timeframe_minutes": settings.breakout_timeframe_minutes,
        "entry_cutoff": settings.entry_cutoff,
        "index_symbols": list(settings.index_symbols or []),
        "track_retest": settings.entry_mode == "retest",
        "retest_within_minutes": settings.retest_within_minutes,
        "entry_mode": settings.entry_mode,
        "universe": settings.universe,
        "capital": settings.capital,
        "margin_multiplier": settings.margin_multiplier,
        "risk_pct": settings.risk_pct,
        "day_risk_pct": settings.day_risk_pct,
        "max_trades_per_day": settings.max_trades_per_day,
        "rrr": settings.rrr,
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
            orb_range_mode=str(data.get("orb_range_mode") or "lte"),
            orb_duration_minutes=int(data["orb_duration_minutes"]),
            breakout_timeframe_minutes=int(data["breakout_timeframe_minutes"]),
            entry_cutoff=str(data.get("entry_cutoff") or "15:15"),
            index_symbols=list(data.get("index_symbols") or ["NSE:NIFTY50-INDEX"]),
            track_retest=bool(data.get("track_retest") or False),
            retest_within_minutes=int(data.get("retest_within_minutes") or 30),
            entry_mode=str(data.get("entry_mode") or ("retest" if data.get("track_retest") else "direct")),
            universe=str(data.get("universe") or "watchlist"),
            capital=float(data.get("capital") or 40_000),
            margin_multiplier=float(data.get("margin_multiplier") or 5),
            risk_pct=float(data.get("risk_pct") or 1.5),
            day_risk_pct=float(data.get("day_risk_pct") or 3),
            max_trades_per_day=int(data.get("max_trades_per_day") or 2),
            rrr=float(data.get("rrr") or 2),
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
        orb_range_mode=settings.orb_range_mode,
        orb_duration_minutes=int(settings.orb_duration_minutes),
        breakout_timeframe_minutes=int(settings.breakout_timeframe_minutes),
        updated_at=settings.updated_at,
        entry_cutoff=settings.entry_cutoff,
        index_symbols_json=json.dumps(list(settings.index_symbols or [])),
        track_retest=int(settings.entry_mode == "retest"),
        retest_within_minutes=int(settings.retest_within_minutes),
        entry_mode=settings.entry_mode,
        universe=settings.universe,
        capital=float(settings.capital),
        margin_multiplier=float(settings.margin_multiplier),
        risk_pct=float(settings.risk_pct),
        day_risk_pct=float(settings.day_risk_pct),
        max_trades_per_day=int(settings.max_trades_per_day),
        rrr=float(settings.rrr),
    )
    _write_json_backup(settings)
    return settings


def tracked_symbols(settings: StrategySettings) -> list[str]:
    """Equity watchlist, indices, or both, following the saved symbol choice."""
    chosen: list[str] = []
    if settings.universe in ("watchlist", "both"):
        chosen.extend(settings.symbols)
    if settings.universe in ("indices", "both"):
        for symbol in settings.index_symbols or []:
            if symbol not in chosen:
                chosen.append(symbol)
    return chosen


def update_index_symbols(symbols: list[str]) -> StrategySettings:
    """Persist the index list without touching the equity watchlist or ORB rules."""
    current = load_settings()
    current.index_symbols = list(symbols)
    return save_settings(current)


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
