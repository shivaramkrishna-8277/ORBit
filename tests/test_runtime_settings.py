"""Dashboard setting validation. Does not touch the live database."""
import pytest

from src.strategy.runtime_settings import (
    StrategySettings,
    default_settings,
    normalize_symbol,
    tracked_symbols,
    validate_settings,
)


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


def test_defaults_match_the_shared_live_and_backtest_rules():
    settings = default_settings()
    assert settings.entry_mode == "retest"
    assert settings.retest_within_minutes == 30
    assert settings.universe == "watchlist"
    assert settings.orb_duration_minutes == 60
    assert settings.breakout_timeframe_minutes == 5
    assert settings.orb_range_threshold == 0.7
    assert settings.orb_range_mode == "lte"
    assert settings.capital == 40_000
    assert settings.risk_pct == 1.5
    assert settings.day_risk_pct == 3
    assert settings.max_trades_per_day == 2


def test_universe_picks_the_saved_list():
    settings = _settings(
        symbols=["NSE:SBIN-EQ"],
        index_symbols=["NSE:NIFTY50-INDEX"],
        universe="indices",
    )
    assert tracked_symbols(settings) == ["NSE:NIFTY50-INDEX"]
    settings.universe = "both"
    assert tracked_symbols(settings) == ["NSE:SBIN-EQ", "NSE:NIFTY50-INDEX"]


def test_validate_rejects_unknown_entry_and_risk():
    with pytest.raises(ValueError):
        validate_settings(_settings(entry_mode="both"))
    with pytest.raises(ValueError):
        validate_settings(_settings(risk_pct=0))
    saved = validate_settings(_settings(entry_mode="retest"))
    assert saved.track_retest is True


def test_validate_rejects_unknown_windows():
    with pytest.raises(ValueError):
        validate_settings(_settings(orb_duration_minutes=10))
    with pytest.raises(ValueError):
        validate_settings(_settings(breakout_timeframe_minutes=2))
    with pytest.raises(ValueError):
        validate_settings(_settings(orb_range_threshold=0))
