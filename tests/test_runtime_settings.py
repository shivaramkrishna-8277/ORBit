"""Dashboard setting validation. Does not touch the live database."""
import pytest

from src.strategy.runtime_settings import StrategySettings, normalize_symbol, validate_settings


def _settings(**overrides) -> StrategySettings:
    base = dict(
        symbols=["NSE:SBIN-EQ"],
        max_price_enabled=False,
        max_stock_price=800,
        orb_range_threshold=0.6,
        orb_duration_minutes=15,
        breakout_timeframe_minutes=15,
    )
    base.update(overrides)
    return StrategySettings(**base)


def test_normalize_symbol_accepts_ticker_and_fyers_form():
    assert normalize_symbol("reliance") == "NSE:RELIANCE-EQ"
    assert normalize_symbol("NSE:M&M-EQ") == "NSE:M&M-EQ"
    assert normalize_symbol("BAJAJ-AUTO") == "NSE:BAJAJ-AUTO-EQ"


def test_normalize_symbol_rejects_blank_and_junk():
    with pytest.raises(ValueError):
        normalize_symbol("   ")
    with pytest.raises(ValueError):
        normalize_symbol("REL IANCE")


def test_validate_dedupes_and_sorts():
    settings = validate_settings(_settings(symbols=["NSE:TCS-EQ", "sbin", "NSE:SBIN-EQ"]))
    assert settings.symbols == ["NSE:SBIN-EQ", "NSE:TCS-EQ"]


def test_validate_rejects_unknown_windows():
    with pytest.raises(ValueError):
        validate_settings(_settings(orb_duration_minutes=10))
    with pytest.raises(ValueError):
        validate_settings(_settings(breakout_timeframe_minutes=2))
    with pytest.raises(ValueError):
        validate_settings(_settings(orb_range_threshold=0))
