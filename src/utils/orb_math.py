"""ORB range calculation helpers."""
from datetime import datetime, time as dtime, timedelta


def orb_range_pct(high: float, low: float) -> float:
    """Percentage range of the opening candle: (High − Low) / Low × 100."""
    if low <= 0:
        return float("inf")
    return (high - low) / low * 100


def parse_cutoff(value: str) -> dtime:
    """Accept HH:MM. Invalid values fall back to the session close."""
    try:
        parsed = datetime.strptime(str(value).strip(), "%H:%M")
    except ValueError:
        return dtime(15, 15)
    return parsed.time()


def entry_is_allowed(candle_start: datetime, breakout_minutes: int, cutoff: str) -> bool:
    """True when the breakout candle closes at or before the entry cutoff."""
    if candle_start.tzinfo is None:
        entry = candle_start + timedelta(minutes=breakout_minutes)
        clock = entry.time()
    else:
        entry = candle_start + timedelta(minutes=breakout_minutes)
        clock = entry.timetz().replace(tzinfo=None)
    return clock <= parse_cutoff(cutoff)


def range_passes(range_pct: float, threshold: float, mode: str = "lte") -> bool:
    """True when the opening range meets a less-than or greater-than rule."""
    if mode == "gte":
        return range_pct >= threshold
    return range_pct <= threshold
