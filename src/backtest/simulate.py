"""One-session ORB trade simulation with shared margin and day-risk limits."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import pytz

from src.backtest.rank import rank_signals, volume_interest
from src.strategy.intraday_agg import closed_breakout_candles, opening_range_candle, session_end
from src.strategy.retest import find_retest_candle
from src.strategy.structure_sl import suggest_stop_loss
from src.utils.orb_math import entry_is_allowed, orb_range_pct, range_passes

IST = pytz.timezone("Asia/Kolkata")


def size_quantity(
    sizing_capital: float,
    risk_pct: float,
    entry: float,
    stop: float,
    buying_power_left: float,
) -> tuple[int, float]:
    """Shares from risk, capped by leftover buying power. Returns (qty, planned loss)."""
    distance = abs(entry - stop)
    if distance <= 0 or entry <= 0 or sizing_capital <= 0 or buying_power_left < entry:
        return 0, 0.0
    risk_amount = sizing_capital * (risk_pct / 100.0)
    by_risk = math.floor(risk_amount / distance)
    by_margin = math.floor(buying_power_left / entry)
    qty = max(0, min(by_risk, by_margin))
    return qty, qty * distance


def _ticker(symbol: str) -> str:
    text = symbol.replace("NSE:", "")
    for suffix in ("-EQ", "-INDEX"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _milestone_price(side: str, entry: float, target: float, trigger_pct: float) -> float:
    """Price at trigger_pct of the way from entry to target."""
    fraction = trigger_pct / 100.0
    if side == "BULLISH":
        return entry + fraction * (target - entry)
    return entry - fraction * (entry - target)


def _milestone_touched(side: str, entry: float, target: float, trigger_pct: float, bar: dict) -> bool:
    level = _milestone_price(side, entry, target, trigger_pct)
    if side == "BULLISH":
        return float(bar["high"]) >= level
    return float(bar["low"]) <= level


def _exit_on_bar(side: str, stop: float, target: float, bar: dict) -> tuple[float, str] | None:
    """Stop is checked before target. A gap through the stop fills at the open."""
    open_ = float(bar["open"])
    high = float(bar["high"])
    low = float(bar["low"])
    if side == "BULLISH":
        if open_ <= stop:
            return open_, "stop"
        if low <= stop:
            return stop, "stop"
        if high >= target:
            return target, "target"
        return None
    if open_ >= stop:
        return open_, "stop"
    if high >= stop:
        return stop, "stop"
    if low <= target:
        return target, "target"
    return None


def _pnl(side: str, entry: float, exit_px: float, qty: int) -> float:
    if side == "BULLISH":
        return (exit_px - entry) * qty
    return (entry - exit_px) * qty


def _eod_price(minute_bars: list[dict], day: datetime) -> float | None:
    limit = session_end(day)
    last = [bar for bar in minute_bars if bar["datetime"] < limit]
    if not last:
        return None
    return float(max(last, key=lambda bar: bar["datetime"])["close"])


def _signals_for_symbol(
    symbol: str,
    day: datetime,
    minute_bars: list[dict],
    *,
    orb_minutes: int,
    breakout_minutes: int,
    max_price_enabled: bool,
    max_stock_price: float,
    orb_range_threshold: float,
    orb_range_mode: str,
    entry_cutoff: str,
    entry_mode: str,
    retest_within_minutes: int,
    rrr: float,
) -> tuple[dict | None, list[dict]]:
    """First breakout of the day, plus the breakout-timeframe candles used to manage it."""
    orb = opening_range_candle(minute_bars, day, orb_minutes)
    if orb is None:
        return None, []
    if max_price_enabled and not symbol.endswith("-INDEX") and float(orb["open"]) >= max_stock_price:
        return None, []
    if float(orb["low"]) <= 0:
        return None, []
    range_pct = orb_range_pct(float(orb["high"]), float(orb["low"]))
    if not range_passes(range_pct, orb_range_threshold, orb_range_mode):
        return None, []

    candles = closed_breakout_candles(
        minute_bars, day, orb_minutes, breakout_minutes, session_end(day)
    )
    signal = _signal_from_candles(
        symbol,
        orb,
        candles,
        breakout_minutes=breakout_minutes,
        entry_cutoff=entry_cutoff,
        entry_mode=entry_mode,
        retest_within_minutes=retest_within_minutes,
        rrr=rrr,
        range_pct=range_pct,
    )
    return signal, candles


def _signal_from_candles(
    symbol: str,
    orb: dict,
    candles: list[dict],
    *,
    breakout_minutes: int,
    entry_cutoff: str,
    entry_mode: str,
    retest_within_minutes: int,
    rrr: float,
    range_pct: float,
) -> dict | None:
    """Entry signal from candles that are already built. The range filter is the caller's job."""
    chosen: dict | None = None
    if entry_mode == "retest":
        chosen = find_retest_candle(
            candles,
            float(orb["low"]),
            float(orb["high"]),
            breakout_minutes=breakout_minutes,
            within_minutes=retest_within_minutes,
            cutoff=entry_cutoff,
        )
    prior: list[dict] = []
    for candle in candles:
        if entry_mode == "retest" and candle is not chosen:
            prior.append(candle)
            continue
        close = float(candle["close"])
        if close > float(orb["high"]):
            side = "BULLISH"
        elif close < float(orb["low"]):
            side = "BEARISH"
        else:
            prior.append(candle)
            continue
        sl = suggest_stop_loss(side, close, float(orb["low"]), float(orb["high"]), prior)
        stop = float(sl["suggested_sl"])
        distance = abs(close - stop)
        if distance <= 0:
            prior.append(candle)
            continue
        if not entry_is_allowed(candle["candle_start"], breakout_minutes, entry_cutoff):
            return None
        target = close + rrr * distance if side == "BULLISH" else close - rrr * distance
        interested, ratio = volume_interest(
            float(candle.get("volume") or 0),
            [float(bar.get("volume") or 0) for bar in prior],
        )
        signal = {
            "symbol": symbol,
            "ticker": _ticker(symbol),
            "side": side,
            "entry": close,
            "stop": stop,
            "target": target,
            "stop_basis": sl["stop_basis"],
            "orb_stop": float(sl["orb_stop"]),
            "candle_start": candle["candle_start"],
            "entry_time": candle["candle_start"] + timedelta(minutes=breakout_minutes),
            "interested": interested,
            "volume_ratio": ratio,
            "range_pct": range_pct,
            "orb_high": float(orb["high"]),
            "orb_low": float(orb["low"]),
        }
        return signal
    return None


def simulate_day(
    day: date,
    bars_by_symbol: dict[str, list[dict]],
    *,
    sizing_capital: float,
    margin_multiplier: float,
    risk_pct: float,
    day_risk_pct: float,
    max_trades: int,
    rrr: float,
    orb_minutes: int,
    breakout_minutes: int,
    max_price_enabled: bool,
    max_stock_price: float,
    orb_range_threshold: float,
    orb_range_mode: str = "lte",
    entry_cutoff: str = "15:15",
    entry_mode: str = "direct",
    retest_within_minutes: int = 30,
    trailing_enabled: bool = False,
    trailing_trigger_pct: float = 50.0,
    trailing_add_pct: float = 50.0,
) -> dict:
    """Simulate one session. Sizing capital is already capped by the caller."""
    day_dt = IST.localize(datetime(day.year, day.month, day.day, 9, 15))
    signals: list[dict] = []
    candles_by_symbol: dict[str, dict] = {}
    minutes_by_symbol: dict[str, list[dict]] = {}

    for symbol, bars in bars_by_symbol.items():
        day_bars = [bar for bar in bars if bar["datetime"].astimezone(IST).date() == day]
        if not day_bars:
            continue
        minutes_by_symbol[symbol] = day_bars
        signal, candles = _signals_for_symbol(
            symbol,
            day_dt,
            day_bars,
            orb_minutes=orb_minutes,
            breakout_minutes=breakout_minutes,
            max_price_enabled=max_price_enabled,
            max_stock_price=max_stock_price,
            orb_range_threshold=orb_range_threshold,
            orb_range_mode=orb_range_mode,
            entry_cutoff=entry_cutoff,
            entry_mode=entry_mode,
            retest_within_minutes=retest_within_minutes,
            rrr=rrr,
        )
        candles_by_symbol[symbol] = {c["candle_start"]: c for c in candles}
        if signal is not None:
            signals.append(signal)

    return manage_day(
        day,
        day_dt,
        signals,
        candles_by_symbol,
        minutes_by_symbol,
        sizing_capital=sizing_capital,
        margin_multiplier=margin_multiplier,
        risk_pct=risk_pct,
        day_risk_pct=day_risk_pct,
        max_trades=max_trades,
        breakout_minutes=breakout_minutes,
        trailing_enabled=trailing_enabled,
        trailing_trigger_pct=trailing_trigger_pct,
        trailing_add_pct=trailing_add_pct,
    )


def manage_day(
    day: date,
    day_dt: datetime,
    signals: list[dict],
    candles_by_symbol: dict[str, dict],
    minutes_by_symbol: dict[str, list[dict]],
    *,
    sizing_capital: float,
    margin_multiplier: float,
    risk_pct: float,
    day_risk_pct: float,
    max_trades: int,
    breakout_minutes: int,
    trailing_enabled: bool = False,
    trailing_trigger_pct: float = 50.0,
    trailing_add_pct: float = 50.0,
) -> dict:
    """Turn ready signals into trades. Exits use the breakout candles, then the 15:15 close."""
    buying_power = sizing_capital * margin_multiplier
    day_risk_limit = sizing_capital * (day_risk_pct / 100.0)
    times = sorted({sig["candle_start"] for sig in signals})
    for candles in candles_by_symbol.values():
        times.extend(candles.keys())
    times = sorted(set(times))

    open_positions: list[dict] = []
    trades: list[dict] = []
    skipped: list[dict] = []
    realized_loss = 0.0
    taken = 0

    def _close(pos: dict, exit_px: float, reason: str, when: datetime) -> None:
        nonlocal realized_loss
        pnl = _pnl(pos["side"], pos["entry"], exit_px, pos["qty"])
        if pnl < 0:
            realized_loss += -pnl
        risk = pos["planned_risk"] or 1.0
        leg = pos.get("trade_leg", "open")
        row = {
            "date": day.isoformat(),
            "symbol": pos["symbol"],
            "ticker": pos["ticker"],
            "side": pos["side"],
            "entry_time": pos["entry_time"].strftime("%H:%M"),
            "entry": pos["entry"],
            "stop": pos["stop"],
            "stop_basis": pos["stop_basis"],
            "target": pos["target"],
            "exit_time": when.astimezone(IST).strftime("%H:%M"),
            "exit": exit_px,
            "exit_reason": reason,
            "qty": pos["qty"],
            "pnl": pnl,
            "r_multiple": pnl / risk,
            "orb_high": pos.get("orb_high"),
            "orb_low": pos.get("orb_low"),
            "trade_leg": leg,
        }
        if leg == "add":
            row["parent_key"] = pos.get("parent_key")
        else:
            row["trade_key"] = pos.get("trade_key")
        if pos.get("scaled"):
            row["trail_scaled"] = True
        trades.append(row)

    def _maybe_scale_in(pos: dict, bar: dict, stamp: datetime, used_margin: float) -> dict | None:
        """At the milestone, move stop to entry and open the add leg. Returns the child or None."""
        if not trailing_enabled or pos.get("scaled") or pos.get("trade_leg") == "add":
            return None
        if not _milestone_touched(pos["side"], pos["entry"], pos["target"], trailing_trigger_pct, bar):
            return None
        breakeven = float(pos["entry"])
        trigger_px = _milestone_price(pos["side"], pos["entry"], pos["target"], trailing_trigger_pct)
        add_qty = max(0, math.floor(pos["qty"] * (trailing_add_pct / 100.0)))
        if add_qty < 1:
            pos["scaled"] = True
            pos["stop"] = breakeven
            return None
        room = buying_power - used_margin
        by_margin = math.floor(room / trigger_px) if trigger_px > 0 else 0
        add_qty = min(add_qty, by_margin)
        if add_qty < 1:
            pos["scaled"] = True
            pos["stop"] = breakeven
            return None
        pos["scaled"] = True
        pos["stop"] = breakeven
        when = stamp + timedelta(minutes=breakout_minutes)
        distance = abs(trigger_px - breakeven)
        return {
            "symbol": pos["symbol"],
            "ticker": pos["ticker"],
            "side": pos["side"],
            "entry": trigger_px,
            "stop": breakeven,
            "target": pos["target"],
            "stop_basis": pos["stop_basis"],
            "orb_high": pos.get("orb_high"),
            "orb_low": pos.get("orb_low"),
            "candle_start": stamp,
            "entry_time": when,
            "qty": add_qty,
            "planned_risk": add_qty * distance,
            "trade_leg": "add",
            "parent_key": pos.get("trade_key"),
        }

    def _process_exits(positions: list[dict]) -> list[dict]:
        still: list[dict] = []
        used_margin = sum(p["qty"] * p["entry"] for p in positions)
        for pos in positions:
            bar = candles_by_symbol.get(pos["symbol"], {}).get(stamp)
            if bar is None or stamp <= pos["candle_start"]:
                still.append(pos)
                continue
            hit = _exit_on_bar(pos["side"], pos["stop"], pos["target"], bar)
            if hit is not None:
                exit_px, reason = hit
                _close(pos, exit_px, reason, stamp + timedelta(minutes=breakout_minutes))
                continue
            child = _maybe_scale_in(pos, bar, stamp, used_margin)
            if child is not None:
                used_margin += child["qty"] * child["entry"]
                hit_parent = _exit_on_bar(pos["side"], pos["stop"], pos["target"], bar)
                if hit_parent is not None:
                    exit_px, reason = hit_parent
                    _close(pos, exit_px, reason, stamp + timedelta(minutes=breakout_minutes))
                else:
                    still.append(pos)
                hit_child = _exit_on_bar(child["side"], child["stop"], child["target"], bar)
                if hit_child is not None:
                    exit_px, reason = hit_child
                    _close(child, exit_px, reason, stamp + timedelta(minutes=breakout_minutes))
                else:
                    still.append(child)
                continue
            still.append(pos)
        return still

    for stamp in times:
        open_positions = _process_exits(open_positions)

        batch = rank_signals([sig for sig in signals if sig["candle_start"] == stamp])
        for sig in batch:
            if taken >= max_trades:
                skipped.append({**_skip(sig, day), "reason": "max trades"})
                continue
            used = sum(pos["qty"] * pos["entry"] for pos in open_positions)
            qty, planned = size_quantity(
                sizing_capital,
                risk_pct,
                sig["entry"],
                sig["stop"],
                buying_power - used,
            )
            if qty < 1:
                skipped.append({**_skip(sig, day), "reason": "no quantity"})
                continue
            open_risk = sum(pos["planned_risk"] for pos in open_positions)
            if realized_loss + open_risk + planned > day_risk_limit + 1e-6:
                skipped.append({**_skip(sig, day), "reason": "day risk"})
                continue
            trade_key = f"{day.isoformat()}:{sig['symbol']}:{sig['entry_time'].strftime('%H:%M')}"
            open_positions.append(
                {
                    **sig,
                    "qty": qty,
                    "planned_risk": planned,
                    "trade_leg": "open",
                    "trade_key": trade_key,
                    "scaled": False,
                }
            )
            taken += 1

    for pos in open_positions:
        px = _eod_price(minutes_by_symbol.get(pos["symbol"], []), day_dt)
        if px is None:
            px = pos["entry"]
        _close(pos, px, "session close", session_end(day_dt))

    pnl = sum(trade["pnl"] for trade in trades)
    return {
        "date": day.isoformat(),
        "signals": len(signals),
        "trades": len(trades),
        "skipped": len(skipped),
        "pnl": pnl,
        "trade_rows": trades,
        "skip_rows": skipped,
    }


def _skip(sig: dict, day: date) -> dict:
    return {
        "date": day.isoformat(),
        "symbol": sig["symbol"],
        "ticker": sig["ticker"],
        "side": sig["side"],
        "time": sig["entry_time"].strftime("%H:%M"),
        "entry": sig["entry"],
    }
