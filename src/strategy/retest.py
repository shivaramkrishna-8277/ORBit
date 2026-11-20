"""Second-breakout entry: close outside, close back inside, then close outside again."""
from __future__ import annotations

from datetime import datetime, timedelta

from src.utils.orb_math import entry_is_allowed


def find_retest_candle(
    candles: list[dict],
    orb_low: float,
    orb_high: float,
    *,
    breakout_minutes: int,
    within_minutes: int,
    cutoff: str,
) -> dict | None:
    """
    First candle that closes outside the opening range, then a later close back
    inside, then a later close outside in the same direction.

    The second close must be within `within_minutes` of the first breakout close
    and no later than the entry cutoff. Otherwise there is no retest trade.
    """
    direction: str | None = None
    first_end: datetime | None = None
    back_inside = False
    limit = timedelta(minutes=within_minutes)

    for candle in candles:
        close = float(candle["close"])
        end = candle["candle_start"] + timedelta(minutes=breakout_minutes)
        if direction is None:
            if close > orb_high:
                direction = "BULLISH"
                first_end = end
            elif close < orb_low:
                direction = "BEARISH"
                first_end = end
            continue
        if first_end is not None and end > first_end + limit:
            return None
        if not back_inside:
            if orb_low <= close <= orb_high:
                back_inside = True
            continue
        same_way = (direction == "BULLISH" and close > orb_high) or (
            direction == "BEARISH" and close < orb_low
        )
        if not same_way:
            continue
        if entry_is_allowed(candle["candle_start"], breakout_minutes, cutoff):
            return candle
        return None
    return None
