"""Retest entry: outside, back inside, then the same way again."""
from datetime import datetime, timedelta

import pytz

from src.strategy.retest import find_retest_candle

IST = pytz.timezone("Asia/Kolkata")


def _candle(minute: int, close: float) -> dict:
    start = IST.localize(datetime(2026, 4, 1, 9, 15)) + timedelta(minutes=minute)
    return {"candle_start": start, "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1}


def test_retest_enters_on_the_second_break_inside_the_window():
    candles = [
        _candle(0, 100),
        _candle(5, 102),   # first close above 101
        _candle(10, 100.5),  # back inside 99-101
        _candle(15, 103),  # second break, 15 min after the first close at 09:25
    ]
    chosen = find_retest_candle(
        candles, 99, 101, breakout_minutes=5, within_minutes=30, cutoff="15:15"
    )
    assert chosen is candles[3]


def test_retest_skips_when_the_second_break_is_outside_the_window():
    candles = [
        _candle(0, 102),
        _candle(5, 100),
        _candle(40, 103),
    ]
    chosen = find_retest_candle(
        candles, 99, 101, breakout_minutes=5, within_minutes=15, cutoff="15:15"
    )
    assert chosen is None


def test_retest_ignores_the_opposite_second_break():
    candles = [
        _candle(0, 102),
        _candle(5, 100),
        _candle(10, 98),
    ]
    chosen = find_retest_candle(
        candles, 99, 101, breakout_minutes=5, within_minutes=30, cutoff="15:15"
    )
    assert chosen is None
