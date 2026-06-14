"""Tests for position sizing math."""
from src.utils.position_sizing import compute_position, margin_from_capital


class TestMarginFromCapital:

    def test_five_x_multiplier(self):
        margin, buying_power = margin_from_capital(100_000)
        assert buying_power == 500_000
        assert margin == 400_000


class TestComputePosition:

    def test_quantity_from_risk_and_margin_cap(self):
        pos = compute_position(capital=100_000, entry=500, stop_loss=495, risk_pct=1.0)
        # 1% risk = ₹1000, ₹5/share → 200 shares; margin cap 500k/500 = 1000 shares
        assert pos["quantity"] == 200
        assert pos["max_loss"] == 1000
