"""Tests for watchlist price filtering."""
from src.utils.quote_price import quote_filter_price


class TestQuoteFilterPrice:

    def test_uses_ltp_when_available(self):
        assert quote_filter_price({"ltp": 750.0, "close": 700.0}) == 750.0

    def test_falls_back_to_previous_close_when_ltp_zero(self):
        assert quote_filter_price({"ltp": 0.0, "close": 750.0}) == 750.0

    def test_returns_none_when_no_valid_price(self):
        assert quote_filter_price({"ltp": 0.0, "close": 0.0}) is None
        assert quote_filter_price({}) is None
