"""Aggregate 1-minute bars into an opening range and later breakout candles.

The opening range always starts at 09:15 IST. Breakout buckets start when
that window ends, so a 5-minute opening range followed by 15-minute breakout
candles is 09:15–09:20, then 09:20–09:35, 09:35–09:50, and so on.
"""
from datetime import datetime, timedelta

import pytz

IST = pytz.timezone("Asia/Kolkata")
SESSION_END = (15, 15)


def market_open(day: datetime) -> datetime:
    if day.tzinfo is None:
        day = IST.localize(day)
    else:
        day = day.astimezone(IST)
    return day.replace(hour=9, minute=15, second=0, microsecond=0)


def session_end(day: datetime) -> datetime:
    if day.tzinfo is None:
        day = IST.localize(day)
    else:
        day = day.astimezone(IST)
    return day.replace(hour=SESSION_END[0], minute=SESSION_END[1], second=0, microsecond=0)


def bucket_start(ts: datetime, orb_minutes: int, breakout_minutes: int) -> datetime:
    """Candle start that contains ts. The first bucket is the whole opening range."""
    if ts.tzinfo is None:
        ts = IST.localize(ts)
    else:
        ts = ts.astimezone(IST)

    open_dt = market_open(ts)
    if ts < open_dt:
        return open_dt

    orb_end = open_dt + timedelta(minutes=orb_minutes)
    if ts < orb_end:
        return open_dt

    elapsed = int((ts - orb_end).total_seconds())
    step = breakout_minutes * 60
    return orb_end + timedelta(seconds=(elapsed // step) * step)


def _as_ist(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return IST.localize(ts)
    return ts.astimezone(IST)


def bars_from_frame(df) -> list[dict]:
    """Convert a Fyers history frame into IST minute bars."""
    if df is None or df.empty:
        return []
    bars: list[dict] = []
    for _, row in df.iterrows():
        ts = row["datetime"].to_pydatetime()
        ts = _as_ist(ts).replace(second=0, microsecond=0)
        bars.append(
            {
                "datetime": ts,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
        )
    bars.sort(key=lambda bar: bar["datetime"])
    return bars


def _aggregate(bars: list[dict], candle_start: datetime) -> dict | None:
    if not bars:
        return None
    ordered = sorted(bars, key=lambda bar: bar["datetime"])
    return {
        "candle_start": candle_start,
        "open": ordered[0]["open"],
        "high": max(bar["high"] for bar in ordered),
        "low": min(bar["low"] for bar in ordered),
        "close": ordered[-1]["close"],
        "volume": sum(bar["volume"] for bar in ordered),
    }


def _has_minute(bars: list[dict], minute: datetime) -> bool:
    return any(bar["datetime"] == minute for bar in bars)


def opening_range_candle(bars: list[dict], day: datetime, orb_minutes: int) -> dict | None:
    """High/low of 09:15 through the minute before the window ends.

    Returns None when the last minute of the window is missing, so the caller
    can fall back to the tick-built candle instead of using a partial range.
    """
    open_dt = market_open(day)
    orb_end = open_dt + timedelta(minutes=orb_minutes)
    last_minute = orb_end - timedelta(minutes=1)
    window = [bar for bar in bars if open_dt <= bar["datetime"] < orb_end]
    if not _has_minute(window, last_minute):
        return None
    return _aggregate(window, open_dt)


def closed_breakout_candles(
    bars: list[dict],
    day: datetime,
    orb_minutes: int,
    breakout_minutes: int,
    as_of: datetime,
) -> list[dict]:
    """Breakout candles that have fully closed at or before as_of and 15:15."""
    open_dt = market_open(day)
    orb_end = open_dt + timedelta(minutes=orb_minutes)
    as_of = _as_ist(as_of).replace(second=0, microsecond=0)
    limit = min(as_of, session_end(day))
    step = timedelta(minutes=breakout_minutes)

    candles: list[dict] = []
    start = orb_end
    while start + step <= limit:
        end = start + step
        last_minute = end - timedelta(minutes=1)
        window = [bar for bar in bars if start <= bar["datetime"] < end]
        if _has_minute(window, last_minute):
            candle = _aggregate(window, start)
            if candle is not None:
                candles.append(candle)
        start = end
    return candles
