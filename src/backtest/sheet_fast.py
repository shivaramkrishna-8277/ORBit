"""Run every sheet row from candles built once per opening-range and breakout size."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from src.backtest.engine import score_trades
from src.backtest.sheet_batch import MonthResult, SheetSetup
from src.backtest.simulate import _signal_from_candles, manage_day
from src.strategy.intraday_agg import market_open, session_end
from src.utils.market_calendar import trading_days
from src.utils.orb_math import orb_range_pct, range_passes

_RETEST_WINDOWS = (15, 30, 60)


@dataclass
class _Prepared:
    symbol: str
    range_pct: float
    candle_map: dict
    minutes: list[dict]
    signals: dict[str, dict | None]


def index_days(bars: dict[str, list[dict]]) -> dict[date, dict[str, list[dict]]]:
    """One pass from symbol history to {day: {symbol: minute bars}}."""
    by_day: dict[date, dict[str, list[dict]]] = defaultdict(dict)
    for symbol, series in bars.items():
        current: list[dict] = []
        current_day: date | None = None
        for bar in series:
            day = bar["datetime"].date()
            if day != current_day:
                if current_day is not None and current:
                    by_day[current_day][symbol] = current
                current_day = day
                current = [bar]
            else:
                current.append(bar)
        if current_day is not None and current:
            by_day[current_day][symbol] = current
    return by_day


def _last_minute(bars: list[dict], end: datetime) -> bool:
    target = end - timedelta(minutes=1)
    return any(bar["datetime"] == target for bar in bars)


def _aggregate(bars: list[dict], candle_start: datetime) -> dict:
    return {
        "candle_start": candle_start,
        "open": bars[0]["open"],
        "high": max(bar["high"] for bar in bars),
        "low": min(bar["low"] for bar in bars),
        "close": bars[-1]["close"],
        "volume": sum(bar["volume"] for bar in bars),
    }


def _session_candles(
    minutes: list[dict],
    day_dt: datetime,
    orb_minutes: int,
    breakout_minutes: int,
) -> tuple[dict | None, list[dict]]:
    """Opening range and closed breakout candles in one walk of the minute bars."""
    open_dt = market_open(day_dt)
    orb_end = open_dt + timedelta(minutes=orb_minutes)
    limit = session_end(day_dt)
    step = timedelta(minutes=breakout_minutes)
    orb_bars: list[dict] = []
    buckets: dict[datetime, list[dict]] = defaultdict(list)
    for bar in minutes:
        ts = bar["datetime"]
        if ts < open_dt or ts >= limit:
            continue
        if ts < orb_end:
            orb_bars.append(bar)
            continue
        elapsed = int((ts - orb_end).total_seconds())
        start = orb_end + timedelta(seconds=(elapsed // (breakout_minutes * 60)) * (breakout_minutes * 60))
        if start + step <= limit:
            buckets[start].append(bar)
    if not orb_bars or not _last_minute(orb_bars, orb_end):
        return None, []
    candles = []
    for start in sorted(buckets):
        window = buckets[start]
        if _last_minute(window, start + step):
            candles.append(_aggregate(window, start))
    return _aggregate(orb_bars, open_dt), candles


def _prepare_day(
    symbol_minutes: dict[str, list[dict]],
    day: date,
    orb_minutes: int,
    breakout_minutes: int,
    entry_cutoff: str,
    rrr: float,
) -> list[_Prepared]:
    day_dt = market_open(datetime(day.year, day.month, day.day))
    prepared: list[_Prepared] = []
    for symbol, minutes in symbol_minutes.items():
        orb, candles = _session_candles(minutes, day_dt, orb_minutes, breakout_minutes)
        if orb is None or float(orb["low"]) <= 0:
            continue
        range_pct = orb_range_pct(float(orb["high"]), float(orb["low"]))
        signals = {
            "direct": _signal_from_candles(
                symbol,
                orb,
                candles,
                breakout_minutes=breakout_minutes,
                entry_cutoff=entry_cutoff,
                entry_mode="direct",
                retest_within_minutes=30,
                rrr=rrr,
                range_pct=range_pct,
            )
        }
        for window in _RETEST_WINDOWS:
            signals[str(window)] = _signal_from_candles(
                symbol,
                orb,
                candles,
                breakout_minutes=breakout_minutes,
                entry_cutoff=entry_cutoff,
                entry_mode="retest",
                retest_within_minutes=window,
                rrr=rrr,
                range_pct=range_pct,
            )
        prepared.append(
            _Prepared(
                symbol=symbol,
                range_pct=range_pct,
                candle_map={candle["candle_start"]: candle for candle in candles},
                minutes=minutes,
                signals=signals,
            )
        )
    return prepared


def _month_result(setup: SheetSetup, packs: dict[date, list[_Prepared]], start: date, end: date) -> MonthResult:
    key = "direct" if setup.entry_mode == "direct" else str(setup.retest_within_minutes)
    equity = setup.capital
    peak = equity
    max_drawdown = 0.0
    trades: list[dict] = []
    for day in trading_days(start, end):
        if equity <= 0:
            continue
        signals = []
        candles_by_symbol: dict[str, dict] = {}
        minutes_by_symbol: dict[str, list[dict]] = {}
        for pack in packs.get(day, []):
            if not range_passes(pack.range_pct, setup.orb_range_threshold, setup.orb_range_mode):
                continue
            signal = pack.signals.get(key)
            if signal is None and key not in pack.signals:
                continue
            if signal is not None:
                if setup.max_price_enabled and signal["symbol"].endswith("-EQ"):
                    if float(signal.get("entry") or 0) >= setup.max_stock_price:
                        continue
                signals.append(signal)
                candles_by_symbol[pack.symbol] = pack.candle_map
                minutes_by_symbol[pack.symbol] = pack.minutes
        day_dt = market_open(datetime(day.year, day.month, day.day))
        result = manage_day(
            day,
            day_dt,
            signals,
            candles_by_symbol,
            minutes_by_symbol,
            sizing_capital=min(setup.capital, equity),
            margin_multiplier=setup.margin_multiplier,
            risk_pct=setup.risk_pct,
            day_risk_pct=setup.day_risk_pct,
            max_trades=setup.max_trades,
            breakout_minutes=setup.breakout_minutes,
            trailing_enabled=setup.trailing_enabled,
            trailing_trigger_pct=setup.trailing_trigger_pct,
            trailing_add_pct=setup.trailing_add_pct,
            brokerage_open=setup.brokerage_open,
            brokerage_add=setup.brokerage_add,
        )
        equity += result["pnl"]
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        trades.extend(result["trade_rows"])
    scored = score_trades(trades)
    return MonthResult(
        month=start.month,
        gross_pnl=equity - setup.capital,
        trades=len(trades),
        wins=scored["wins"],
        losses=scored["losses"],
        max_drawdown=max_drawdown,
    )


def iter_sheet_results(
    setups: list[SheetSetup],
    bars_by_universe: dict[str, dict[str, list[dict]]],
    windows: list[tuple[date, date]],
    progress=None,
):
    """Yield (setup id, month results). Candle building is shared when range, candle, cutoff, and RRR match."""
    groups: dict[tuple, list[SheetSetup]] = defaultdict(list)
    for setup in setups:
        groups[(setup.universe, setup.orb_minutes, setup.breakout_minutes, setup.entry_cutoff, setup.rrr)].append(setup)
    total_setups = len(setups)
    scored = 0
    if progress:
        progress("Indexing cached history by session date…")
    indexed = {name: index_days(bars) for name, bars in bars_by_universe.items()}
    total = len(groups)
    if progress:
        progress(f"Scoring {total_setups} setups in {total} shared candle groups…")
    for index, ((universe, orb_minutes, breakout_minutes, entry_cutoff, rrr), group) in enumerate(groups.items(), start=1):
        if progress:
            progress(
                f"Preparing {universe} · {orb_minutes}m range · {breakout_minutes}m candles · RRR {rrr} "
                f"({index}/{total})"
            )
        days = indexed.get(universe, {})
        packs: dict[date, list[_Prepared]] = {}
        day_count = 0
        for start, end in windows:
            for day in trading_days(start, end):
                if day not in days:
                    continue
                packs[day] = _prepare_day(days[day], day, orb_minutes, breakout_minutes, entry_cutoff, rrr)
                day_count += 1
                if progress and day_count % 25 == 0:
                    progress(
                        f"Preparing group {index}/{total} · built candles for {day_count} sessions through {day.isoformat()}"
                    )
        if progress:
            progress(f"Preparing group {index}/{total} · {day_count} sessions ready · scoring {len(group)} rows")
        for setup in group:
            scored += 1
            if progress:
                progress(f"Scoring {setup.setup_id} ({scored}/{total_setups})")
            yield setup.setup_id, [_month_result(setup, packs, start, end) for start, end in windows]