"""Tests for ORB range math."""
import pytest

from src.utils.orb_math import orb_range_pct


class TestOrbRangePct:

    def test_hdfcbank_example_from_chart(self):
        # TradingView 9:15 candle: H=761.55, L=753.10 → ~1.12%
        assert abs(orb_range_pct(761.55, 753.10) - 1.12) < 0.02

    def test_passes_at_threshold(self):
        assert orb_range_pct(100.6, 100.0) == pytest.approx(0.6)

    def test_fails_above_threshold(self):
        assert orb_range_pct(100.61, 100.0) > 0.6
