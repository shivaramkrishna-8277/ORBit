"""Tests for post-ORB swing stop suggestions."""
from datetime import datetime

import pytz

from src.strategy.structure_sl import suggest_stop_loss

IST = pytz.timezone("Asia/Kolkata")


def _bar(h, l, c, minute: int) -> dict:
    start = IST.localize(datetime(2025, 9, 19, 9, 30 + minute))
    return {
        "candle_start": start,
        "open": c,
        "high": h,
        "low": l,
        "close": c,
    }


def test_bullish_uses_latest_swing_low_before_breakout():
    """ORB low 80.60; latest confirmed swing low 81.02 before long breakout."""
    prior = [
        _bar(81.40, 81.10, 81.20, 0),
        _bar(81.30, 81.02, 81.15, 5),
        _bar(81.70, 81.20, 81.65, 10),
        _bar(82.00, 81.50, 81.95, 15),
    ]
    info = suggest_stop_loss(
        "BULLISH",
        entry=82.10,
        orb_low=80.60,
        orb_high=81.00,
        prior_candles=prior,
    )
    assert info["stop_basis"] == "swing"
    assert info["suggested_sl"] == 81.02
    assert info["orb_stop"] == 80.60


def test_bullish_swing_below_orb_low_uses_orb():
    prior = [
        _bar(81.20, 80.90, 81.00, 0),
        _bar(81.10, 80.40, 80.80, 5),
        _bar(81.40, 80.95, 81.20, 10),
        _bar(82.00, 81.30, 81.80, 15),
    ]
    info = suggest_stop_loss(
        "BULLISH",
        entry=82.10,
        orb_low=80.60,
        orb_high=81.50,
        prior_candles=prior,
    )
    assert info["stop_basis"] == "orb"
    assert info["suggested_sl"] == 80.60


def test_bullish_falls_back_to_orb_when_no_swings():
    prior = [
        _bar(81.10, 81.05, 81.08, 0),
        _bar(81.12, 81.06, 81.10, 5),
    ]
    info = suggest_stop_loss(
        "BULLISH",
        entry=82.0,
        orb_low=80.60,
        orb_high=81.00,
        prior_candles=prior,
        swing_left=2,
        swing_right=2,
    )
    assert info["stop_basis"] == "orb"
    assert info["suggested_sl"] == 80.60


def test_bearish_uses_latest_swing_high():
    prior = [
        _bar(80.30, 80.00, 80.15, 0),
        _bar(80.55, 80.20, 80.40, 5),
        _bar(80.35, 79.90, 80.00, 10),
        _bar(80.20, 79.70, 79.75, 15),
    ]
    info = suggest_stop_loss(
        "BEARISH",
        entry=79.40,
        orb_low=79.00,
        orb_high=81.00,
        prior_candles=prior,
    )
    assert info["stop_basis"] == "swing"
    assert info["suggested_sl"] == 80.55
    assert info["orb_stop"] == 81.00