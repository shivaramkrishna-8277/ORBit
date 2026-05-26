"""Unit tests for core ORB logic — CandleBuilder, ORBCalculator, BreakoutDetector."""
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch, call

import pytz

IST = pytz.timezone("Asia/Kolkata")


def ist(hour: int, minute: int, second: int = 0) -> datetime:
    return IST.localize(datetime(2025, 1, 15, hour, minute, second))


# ══════════════════════════════════════════════════════════════════════════════
# CandleBuilder
# ══════════════════════════════════════════════════════════════════════════════

class TestCandleBuilder:

    def _make_builder(self):
        from src.strategy.candle_builder import CandleBuilder
        cb = CandleBuilder.__new__(CandleBuilder)
        cb._on_candle_close = None
        cb._candles = {}
        cb._first_candles = {}
        return cb

    @patch("src.strategy.candle_builder.db")
    def test_first_tick_starts_candle(self, mock_db):
        from src.strategy.candle_builder import CandleBuilder
        cb = CandleBuilder()
        cb.on_tick("NSE:SBIN-EQ", 500.0, ist(9, 15, 30))
        c = cb.get_current_candle("NSE:SBIN-EQ")
        assert c["open"] == 500.0
        assert c["high"] == 500.0
        assert c["low"] == 500.0
        assert c["close"] == 500.0

    @patch("src.strategy.candle_builder.db")
    def test_ticks_in_same_bucket_build_candle(self, mock_db):
        from src.strategy.candle_builder import CandleBuilder
        cb = CandleBuilder()
        symbol = "NSE:SBIN-EQ"
        cb.on_tick(symbol, 500.0, ist(9, 15, 0))   # open
        cb.on_tick(symbol, 510.0, ist(9, 17, 0))   # high
        cb.on_tick(symbol, 495.0, ist(9, 20, 0))   # low
        cb.on_tick(symbol, 505.0, ist(9, 29, 59))  # close

        c = cb.get_current_candle(symbol)
        assert c["open"] == 500.0
        assert c["high"] == 510.0
        assert c["low"] == 495.0
        assert c["close"] == 505.0

    @patch("src.strategy.candle_builder.db")
    def test_tick_at_9_30_starts_new_candle(self, mock_db):
        from src.strategy.candle_builder import CandleBuilder
        on_close = MagicMock()
        cb = CandleBuilder(on_candle_close=on_close)
        symbol = "NSE:SBIN-EQ"

        cb.on_tick(symbol, 500.0, ist(9, 15, 0))
        cb.on_tick(symbol, 510.0, ist(9, 29, 59))
        # This tick is in the 9:30 bucket — should finalise the 9:15 candle
        cb.on_tick(symbol, 520.0, ist(9, 30, 0))

        on_close.assert_called_once()
        closed_candle = on_close.call_args[0][1]
        assert closed_candle["open"] == 500.0
        assert closed_candle["close"] == 510.0

        new_candle = cb.get_current_candle(symbol)
        assert new_candle["open"] == 520.0

    @patch("src.strategy.candle_builder.db")
    def test_on_candle_close_called_exactly_once_per_bucket(self, mock_db):
        from src.strategy.candle_builder import CandleBuilder
        on_close = MagicMock()
        cb = CandleBuilder(on_candle_close=on_close)
        symbol = "NSE:INFY-EQ"

        for minute in [15, 17, 22, 28]:
            cb.on_tick(symbol, 1500.0 + minute, ist(9, minute, 0))
        # Move to next bucket
        cb.on_tick(symbol, 1600.0, ist(9, 30, 0))

        assert on_close.call_count == 1


# ══════════════════════════════════════════════════════════════════════════════
# ORBCalculator
# ══════════════════════════════════════════════════════════════════════════════

class TestORBCalculator:

    @patch("src.strategy.orb_calculator.db")
    def test_range_within_threshold_passes(self, mock_db):
        from src.strategy.orb_calculator import ORBCalculator
        calc = ORBCalculator()
        # 0.5% range — should pass
        candle = {"open": 100.0, "high": 100.5, "low": 100.0, "close": 100.4}
        result = calc.calculate_orb("NSE:SBIN-EQ", candle)
        assert result is not None
        assert abs(result["range_pct"] - 0.5) < 0.01
        mock_db.insert_orb_level.assert_called_once()
        passed = mock_db.insert_orb_level.call_args.kwargs["passed"]
        assert passed == 1

    @patch("src.strategy.orb_calculator.db")
    def test_range_at_exact_threshold_passes(self, mock_db):
        from src.strategy.orb_calculator import ORBCalculator
        calc = ORBCalculator()
        # Exactly 0.6% range — should pass (inclusive boundary)
        candle = {"open": 100.0, "high": 100.6, "low": 100.0, "close": 100.5}
        result = calc.calculate_orb("NSE:SBIN-EQ", candle)
        assert result is not None
        assert abs(result["range_pct"] - 0.6) < 0.01

    @patch("src.strategy.orb_calculator.db")
    def test_range_just_over_threshold_fails(self, mock_db):
        from src.strategy.orb_calculator import ORBCalculator
        calc = ORBCalculator()
        # 0.61% range — should fail
        candle = {"open": 100.0, "high": 100.61, "low": 100.0, "close": 100.5}
        result = calc.calculate_orb("NSE:SBIN-EQ", candle)
        assert result is None
        passed = mock_db.insert_orb_level.call_args.kwargs["passed"]
        assert passed == 0

    @patch("src.strategy.orb_calculator.db")
    def test_wide_range_fails(self, mock_db):
        from src.strategy.orb_calculator import ORBCalculator
        calc = ORBCalculator()
        candle = {"open": 100.0, "high": 102.0, "low": 100.0, "close": 101.0}
        result = calc.calculate_orb("NSE:SBIN-EQ", candle)
        assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# BreakoutDetector
# ══════════════════════════════════════════════════════════════════════════════

class TestBreakoutDetector:

    def _make_candle(self, close: float, candle_start: datetime | None = None) -> dict:
        if candle_start is None:
            candle_start = ist(9, 45)
        return {
            "candle_start": candle_start,
            "open": close - 1,
            "high": close + 1,
            "low": close - 2,
            "close": close,
        }

    @patch("src.strategy.breakout_detector.db")
    def test_close_above_orb_high_fires_bullish(self, mock_db):
        from src.strategy.breakout_detector import BreakoutDetector
        mock_db.has_signal_fired.return_value = False
        mock_db.insert_signal.return_value = 1

        callback = MagicMock()
        detector = BreakoutDetector(
            orb_levels={"NSE:SBIN-EQ": {"orb_high": 500.0, "orb_low": 497.0}},
            on_breakout_callback=callback,
        )
        detector.check_candle("NSE:SBIN-EQ", self._make_candle(close=501.0))

        callback.assert_called_once()
        signal = callback.call_args[0][0]
        assert signal["signal_type"] == "BULLISH"
        assert signal["candle_close"] == 501.0
        assert signal["orb_level"] == 500.0

    @patch("src.strategy.breakout_detector.db")
    def test_close_below_orb_low_fires_bearish(self, mock_db):
        from src.strategy.breakout_detector import BreakoutDetector
        mock_db.has_signal_fired.return_value = False
        mock_db.insert_signal.return_value = 1

        callback = MagicMock()
        detector = BreakoutDetector(
            orb_levels={"NSE:SBIN-EQ": {"orb_high": 500.0, "orb_low": 497.0}},
            on_breakout_callback=callback,
        )
        detector.check_candle("NSE:SBIN-EQ", self._make_candle(close=496.0))

        callback.assert_called_once()
        signal = callback.call_args[0][0]
        assert signal["signal_type"] == "BEARISH"
        assert signal["orb_level"] == 497.0

    @patch("src.strategy.breakout_detector.db")
    def test_close_inside_orb_range_no_signal(self, mock_db):
        from src.strategy.breakout_detector import BreakoutDetector
        callback = MagicMock()
        detector = BreakoutDetector(
            orb_levels={"NSE:SBIN-EQ": {"orb_high": 500.0, "orb_low": 497.0}},
            on_breakout_callback=callback,
        )
        detector.check_candle("NSE:SBIN-EQ", self._make_candle(close=498.5))
        callback.assert_not_called()

    @patch("src.strategy.breakout_detector.db")
    def test_duplicate_bullish_signal_fires_only_once(self, mock_db):
        from src.strategy.breakout_detector import BreakoutDetector
        # First candle: BULLISH=False, BEARISH=False → fires.  Second candle: BULLISH=True → blocked.
        mock_db.has_signal_fired.side_effect = [False, False, True]
        mock_db.insert_signal.return_value = 1

        callback = MagicMock()
        detector = BreakoutDetector(
            orb_levels={"NSE:SBIN-EQ": {"orb_high": 500.0, "orb_low": 497.0}},
            on_breakout_callback=callback,
        )
        detector.check_candle("NSE:SBIN-EQ", self._make_candle(close=502.0))
        detector.check_candle("NSE:SBIN-EQ", self._make_candle(close=503.0, candle_start=ist(10, 0)))

        # Callback should have fired exactly once
        assert callback.call_count == 1
