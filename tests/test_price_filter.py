"""Tests for watchlist price filtering."""
from src.utils.quote_price import normalize_fyers_quote, quote_filter_price


class TestNormalizeFyersQuote:

    def test_maps_fyers_v3_fields(self):
        raw = {
            "lp": 751.5,
            "open_price": 748.0,
            "high_price": 755.0,
            "low_price": 745.0,
            "prev_close_price": 749.0,
        }
        assert normalize_fyers_quote(raw) == {
            "ltp": 751.5,
            "open": 748.0,
            "high": 755.0,
            "low": 745.0,
            "close": 749.0,
        }


class TestQuoteFilterPrice:

    def test_uses_ltp_when_available(self):
        assert quote_filter_price({"ltp": 750.0, "close": 700.0}) == 750.0

    def test_uses_fyers_lp_field(self):
        assert quote_filter_price({"lp": 750.0, "prev_close_price": 700.0}) == 750.0

    def test_falls_back_to_previous_close_when_ltp_zero(self):
        assert quote_filter_price({"ltp": 0.0, "close": 750.0}) == 750.0

    def test_falls_back_to_fyers_prev_close(self):
        assert quote_filter_price({"lp": 0.0, "prev_close_price": 750.0}) == 750.0

    def test_falls_back_to_open_before_market(self):
        assert quote_filter_price({"lp": 0.0, "prev_close_price": 0.0, "open_price": 740.0}) == 740.0

    def test_returns_none_when_no_valid_price(self):
        assert quote_filter_price({"ltp": 0.0, "close": 0.0}) is None
        assert quote_filter_price({}) is None
