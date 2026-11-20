"""Backtest ranking, exits, day-risk, and capital cap."""
from datetime import date, datetime, timedelta

import pytz

from src.backtest.engine import BacktestRequest, run_backtest
from src.backtest.rank import rank_signals, volume_interest
from src.backtest.simulate import simulate_day

IST = pytz.timezone("Asia/Kolkata")
DAY = date(2026, 1, 5)


def _minutes(symbol_prices: list[tuple[int, int, float, float, float, float, float]]) -> list[dict]:
    """(hour, minute, open, high, low, close, volume) for one session."""
    bars = []
    for hour, minute, open_, high, low, close, volume in symbol_prices:
        bars.append(
            {
                "datetime": IST.localize(datetime(DAY.year, DAY.month, DAY.day, hour, minute)),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
    return bars


def _flat(start: tuple[int, int], count: int, price: float, volume: float, **overrides) -> list[tuple]:
    hour, minute = start
    cursor = datetime(DAY.year, DAY.month, DAY.day, hour, minute)
    rows = []
    for _ in range(count):
        bar = {
            "hour": cursor.hour,
            "minute": cursor.minute,
            "open": price,
            "high": price + 0.2,
            "low": price - 0.2,
            "close": price,
            "volume": volume,
        }
        bar.update(overrides)
        rows.append((bar["hour"], bar["minute"], bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"]))
        cursor += timedelta(minutes=1)
    return rows


def _session(breakout_close: float, breakout_volume: float, next_low: float, next_high: float, next_open: float | None = None) -> list[dict]:
    """5-minute ORB, two inside candles, then a breakout and an exit candle."""
    orb = _flat((9, 15), 5, 100.0, 100)
    orb[-1] = (9, 19, 100.0, 100.4, 99.4, 100.0, 100)
    inside_a = _flat((9, 20), 5, 100.1, 100)
    inside_b = _flat((9, 25), 5, 100.1, 100)
    burst = _flat((9, 30), 5, 100.2, breakout_volume)
    burst[-1] = (9, 34, 100.2, max(100.6, breakout_close), 100.1, breakout_close, breakout_volume)
    nxt_open = breakout_close if next_open is None else next_open
    nxt = _flat((9, 35), 5, nxt_open, 80)
    nxt[0] = (9, 35, nxt_open, next_high, next_low, nxt_open, 80)
    return _minutes(orb + inside_a + inside_b + burst + nxt)


def _run(bars_by_symbol, **overrides):
    params = dict(
        sizing_capital=100_000,
        margin_multiplier=5,
        risk_pct=1.0,
        day_risk_pct=1.0,
        max_trades=3,
        rrr=2.0,
        orb_minutes=5,
        breakout_minutes=5,
        max_price_enabled=False,
        max_stock_price=10_000,
        orb_range_threshold=5.0,
    )
    params.update(overrides)
    return simulate_day(DAY, bars_by_symbol, **params)


def test_session_close_is_excluded_from_win_rate():
    from src.backtest.engine import score_trades

    scored = score_trades(
        [
            {"exit_reason": "target", "r_multiple": 2.0, "pnl": 10},
            {"exit_reason": "stop", "r_multiple": -1.0, "pnl": -5},
            {"exit_reason": "session close", "r_multiple": 0.4, "pnl": 4},
        ]
    )
    assert scored["win_rate"] == 50
    assert scored["wins"] == 1
    assert scored["losses"] == 1
    assert scored["session_closes"] == 1
    assert scored["avg_r"] == 0.5


def test_subset_range_uses_the_wider_cache():
    from src.backtest.history import range_is_cached

    store = {
        "symbols": ("NSE:AAA-EQ", "NSE:BBB-EQ"),
        "start": date(2026, 4, 1),
        "end": date(2026, 10, 31),
        "bars": {},
    }
    assert range_is_cached(store, ["NSE:AAA-EQ", "NSE:BBB-EQ"], date(2026, 4, 1), date(2026, 4, 30))
    assert not range_is_cached(store, ["NSE:AAA-EQ"], date(2026, 4, 1), date(2026, 11, 2))


def test_trade_chart_runs_from_the_open_until_the_exit():
    from src.backtest.chart import price_window, trade_candles, trade_levels

    bars = []
    cursor = IST.localize(datetime(2026, 4, 1, 9, 15))
    for _ in range(50):
        bars.append(
            {
                "datetime": cursor,
                "open": 100.0,
                "high": 100.4,
                "low": 99.6,
                "close": 100.1,
                "volume": 10,
            }
        )
        cursor += timedelta(minutes=1)
    trade = {
        "date": "2026-04-01",
        "exit_time": "09:30",
        "entry": 101.0,
        "stop": 99.4,
        "target": 104.0,
        "orb_high": 100.4,
        "orb_low": 99.6,
    }
    candles = trade_candles(trade, bars, orb_minutes=15, breakout_minutes=5)
    assert [candle["candle_start"].strftime("%H:%M") for candle in candles] == ["09:15", "09:20", "09:25"]
    low, high = price_window(candles, trade_levels(trade))
    assert low > 90
    assert high < 120
    assert [level["name"] for level in trade_levels(trade)] == [
        "ORB high",
        "ORB low",
        "Entry",
        "Stop",
        "Target",
    ]


def test_cached_days_are_not_downloaded_again():
    from src.backtest.history import missing_spans

    days = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]
    covered = {day.isoformat() for day in days}
    assert missing_spans(days, covered) == []
    assert missing_spans(days, {days[0].isoformat(), days[2].isoformat()}) == [
        (days[1], days[1])
    ]


def test_breakout_after_cutoff_is_not_taken():
    blocked = _run(
        {"NSE:AAA-EQ": _session(101.0, 400, next_low=100.5, next_high=110.0)},
        entry_cutoff="09:30",
    )
    assert blocked["trades"] == 0
    allowed = _run(
        {"NSE:AAA-EQ": _session(101.0, 400, next_low=100.5, next_high=110.0)},
        entry_cutoff="09:35",
    )
    assert allowed["trades"] == 1


def test_volume_interest_uses_every_prior_breakout_candle():
    interested, ratio = volume_interest(300, [100, 100, 100, 100])
    assert interested is True
    assert ratio == 3

    quiet, _ = volume_interest(90, [100, 100, 100, 100, 100])
    assert quiet is False

    rising, _ = volume_interest(150, [200, 140, 130, 120, 110])
    assert rising is True


def test_same_time_breakouts_prefer_higher_volume_ratio():
    ordered = rank_signals(
        [
            {"symbol": "NSE:BBB-EQ", "ticker": "BBB", "interested": True, "volume_ratio": 1.2},
            {"symbol": "NSE:AAA-EQ", "ticker": "AAA", "interested": False, "volume_ratio": 4},
            {"symbol": "NSE:CCC-EQ", "ticker": "CCC", "interested": True, "volume_ratio": 2},
        ]
    )
    assert [row["ticker"] for row in ordered] == ["CCC", "BBB", "AAA"]


def test_day_risk_blocks_the_second_same_time_trade():
    result = _run(
        {
            "NSE:BBB-EQ": _session(101.0, 500, next_low=98.0, next_high=101.2),
            "NSE:AAA-EQ": _session(101.0, 100, next_low=98.0, next_high=101.2),
        }
    )
    assert result["trades"] == 1
    assert result["trade_rows"][0]["ticker"] == "BBB"
    assert result["skip_rows"][0]["ticker"] == "AAA"
    assert result["skip_rows"][0]["reason"] == "day risk"


def test_stop_fills_before_target_and_gap_uses_the_open():
    stopped = _run({"NSE:AAA-EQ": _session(101.0, 400, next_low=90.0, next_high=120.0)})
    assert stopped["trade_rows"][0]["exit_reason"] == "stop"
    assert stopped["trade_rows"][0]["exit"] == 99.4

    gapped = _run({"NSE:AAA-EQ": _session(101.0, 400, next_low=90.0, next_high=90.5, next_open=98.0)})
    assert gapped["trade_rows"][0]["exit"] == 98.0
    assert gapped["trade_rows"][0]["exit_reason"] == "stop"


def test_target_exit():
    result = _run({"NSE:AAA-EQ": _session(101.0, 400, next_low=100.5, next_high=110.0)})
    trade = result["trade_rows"][0]
    assert trade["exit_reason"] == "target"
    assert trade["exit"] == trade["entry"] + 2 * (trade["entry"] - trade["stop"])


def test_retest_enters_on_the_second_break_not_the_first():
    orb = _flat((9, 15), 5, 100.0, 100)
    orb[-1] = (9, 19, 100.0, 100.4, 99.4, 100.0, 100)
    first = _flat((9, 20), 5, 100.2, 200)
    first[-1] = (9, 24, 100.2, 101.2, 100.1, 101.0, 200)
    back = _flat((9, 25), 5, 100.2, 80)
    back[-1] = (9, 29, 100.2, 100.4, 99.8, 100.0, 80)
    second = _flat((9, 30), 5, 100.3, 300)
    second[-1] = (9, 34, 100.3, 102.2, 100.2, 102.0, 300)
    after = _flat((9, 35), 5, 102.0, 50)
    bars = {"NSE:AAA-EQ": _minutes(orb + first + back + second + after)}

    direct = _run(bars, entry_mode="direct")
    retest = _run(bars, entry_mode="retest", retest_within_minutes=30)
    assert direct["trade_rows"][0]["entry"] == 101.0
    assert retest["trade_rows"][0]["entry"] == 102.0
    assert str(retest["trade_rows"][0]["entry_time"]).startswith("09:35")


def test_index_ignores_the_equity_price_cap():
    price = 24000.0
    orb = _flat((9, 15), 5, price, 100)
    orb[-1] = (9, 19, price, price + 40, price - 40, price, 100)
    inside_a = _flat((9, 20), 5, price + 5, 100)
    inside_b = _flat((9, 25), 5, price + 5, 100)
    burst = _flat((9, 30), 5, price + 10, 400)
    burst[-1] = (9, 34, price + 10, price + 80, price + 10, price + 70, 400)
    nxt = _flat((9, 35), 5, price + 70, 80)
    nxt[0] = (9, 35, price + 70, price + 200, price + 60, price + 70, 80)
    bars = {"NSE:NIFTY50-INDEX": _minutes(orb + inside_a + inside_b + burst + nxt)}
    kept = _run(bars, max_price_enabled=True, max_stock_price=900.0)
    blocked = _run(
        {"NSE:AAA-EQ": _minutes(orb + inside_a + inside_b + burst + nxt)},
        max_price_enabled=True,
        max_stock_price=900.0,
    )
    assert kept["trades"] == 1
    assert kept["trade_rows"][0]["ticker"] == "NIFTY50"
    assert blocked["trades"] == 0


def test_include_months_skips_sessions_outside_the_selection(monkeypatch):
    monkeypatch.setattr(
        "src.backtest.engine.trading_days",
        lambda _start, _end: [date(2026, 1, 5), date(2026, 2, 2)],
    )
    report = run_backtest(
        None,
        BacktestRequest(
            symbols=["NSE:AAA-EQ"],
            date_from=date(2026, 1, 1),
            date_to=date(2026, 2, 28),
            orb_range_threshold=5,
            orb_duration_minutes=5,
            breakout_timeframe_minutes=5,
            max_price_enabled=False,
            max_stock_price=10000,
            capital=100_000,
            margin_multiplier=5,
            risk_pct=1,
            day_risk_pct=3,
            max_trades_per_day=2,
            rrr=2,
            include_months=((2026, 1),),
        ),
        minute_bars={"NSE:AAA-EQ": []},
    )
    assert report["sessions"] == 1
    assert report["daily_rows"][0]["date"] == "2026-01-05"


def test_month_window_stops_at_today_and_skips_a_future_month():
    from src.backtest.months import month_window

    assert month_window(2026, 10, date(2026, 11, 20)) == (date(2026, 10, 1), date(2026, 10, 31))
    assert month_window(2026, 11, date(2026, 11, 20)) == (date(2026, 11, 1), date(2026, 11, 20))
    assert month_window(2026, 12, date(2026, 11, 20)) is None


def test_profits_do_not_increase_next_day_size(monkeypatch):
    day_bars = {"NSE:AAA-EQ": _session(101.0, 400, next_low=100.5, next_high=110.0)}

    def _load(*_args, **_kwargs):
        return day_bars

    monkeypatch.setattr("src.backtest.engine.load_watchlist_minutes", _load)
    monkeypatch.setattr("src.backtest.engine.trading_days", lambda _a, _b: [DAY, date(2026, 1, 6)])
    report = run_backtest(
        None,
        BacktestRequest(
            symbols=["NSE:AAA-EQ"],
            date_from=DAY,
            date_to=date(2026, 1, 6),
            orb_range_threshold=5,
            orb_duration_minutes=5,
            breakout_timeframe_minutes=5,
            max_price_enabled=False,
            max_stock_price=10000,
            capital=100_000,
            margin_multiplier=5,
            risk_pct=1,
            day_risk_pct=3,
            max_trades_per_day=2,
            rrr=2,
        ),
    )
    assert report["daily_rows"][0]["pnl"] > 0
    assert report["daily_rows"][1]["sizing_capital"] == 100_000
    assert report["ending_equity"] > 100_000
