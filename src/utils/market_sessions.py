"""NSE cash-session timing constants (IST)."""
from datetime import datetime, time as dtime

import pytz

IST = pytz.timezone("Asia/Kolkata")

# First 15-minute candle: market opens 9:15, candle closes at 9:30
OPENING_RANGE_START = dtime(9, 15)
OPENING_RANGE_END = dtime(9, 30)


def market_open_dt(day: datetime) -> datetime:
    """9:15 AM IST on the same calendar date as *day*."""
    if day.tzinfo is None:
        day = IST.localize(day)
    else:
        day = day.astimezone(IST)
    return day.replace(hour=9, minute=15, second=0, microsecond=0)


def is_opening_range_candle(candle_start: datetime) -> bool:
    """True if *candle_start* is the 9:15–9:30 opening-range bucket."""
    if candle_start.tzinfo is None:
        t = candle_start.time()
    else:
        t = candle_start.astimezone(IST).time()
    return t == OPENING_RANGE_START


def is_within_opening_range(ts: datetime) -> bool:
    """True for ticks that belong to the first 15-minute candle (9:15 ≤ t < 9:30)."""
    if ts.tzinfo is None:
        ts = IST.localize(ts)
    else:
        ts = ts.astimezone(IST)
    t = ts.time()
    return OPENING_RANGE_START <= t < OPENING_RANGE_END
