"""Opening-range and breakout candle aggregation."""
from datetime import datetime, timedelta

import pytz

from src.strategy.intraday_agg import (
    bucket_start,
    closed_breakout_candles,
    opening_range_candle,
)

IST = pytz.timezone("Asia/Kolkata")


def ist(hour: int, minute: int) -> datetime:
    return IST.localize(datetime(2026, 1, 15, hour, minute))


def minute_bars(start: datetime, count: int, price: float = 100.0) -> list[dict]:
    bars = []
    for offset in range(count):
        ts = start + timedelta(minutes=offset)
        bars.append({
            "datetime": ts,
            "open": price,
            "high": price + 1,
            "low": price - 1,
            "close": price + 0.5,
            "volume": 1.0,
        })
    return bars


def test_bucket_keeps_the_whole_opening_range_together():
    assert bucket_start(ist(9, 19), 5, 15) == ist(9, 15)
    assert bucket_start(ist(9, 20), 5, 15) == ist(9, 20)
    assert bucket_start(ist(9, 34), 5, 15) == ist(9, 20)
    assert bucket_start(ist(9, 35), 5, 15) == ist(9, 35)


def test_default_15_minute_buckets_match_the_old_grid():
    assert bucket_start(ist(9, 29), 15, 15) == ist(9, 15)
    assert bucket_start(ist(9, 30), 15, 15) == ist(9, 30)
    assert bucket_start(ist(9, 44), 15, 15) == ist(9, 30)


def test_opening_range_requires_the_last_minute():
    day = ist(9, 15)
    partial = minute_bars(ist(9, 15), 4)
    assert opening_range_candle(partial, day, 5) is None

    full = minute_bars(ist(9, 15), 5)
    full[2]["high"] = 110
    full[1]["low"] = 90
    candle = opening_range_candle(full, day, 5)
    assert candle is not None
    assert candle["candle_start"] == ist(9, 15)
    assert candle["high"] == 110
    assert candle["low"] == 90
    assert candle["open"] == 100
    assert candle["close"] == 100.5


def test_breakout_candles_start_when_the_opening_range_ends():
    # 09:15–09:20 is the range. First 15-minute breakout candle is 09:20–09:35.
    bars = minute_bars(ist(9, 15), 25)
    bars[19]["close"] = 130  # 09:34, the close of the first breakout candle
    candles = closed_breakout_candles(bars, ist(9, 15), 5, 15, ist(9, 40))
    assert len(candles) == 1
    assert candles[0]["candle_start"] == ist(9, 20)
    assert candles[0]["close"] == 130


def test_incomplete_breakout_bucket_is_left_out():
    bars = minute_bars(ist(9, 30), 4)  # 09:30–09:33, missing 09:34
    candles = closed_breakout_candles(bars, ist(9, 15), 15, 5, ist(9, 40))
    assert candles == []
