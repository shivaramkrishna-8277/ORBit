"""One-session ORB trade simulation with shared margin and day-risk limits."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import pytz

from src.backtest.rank import rank_signals, volume_interest
from src.strategy.intraday_agg import closed_breakout_candles, opening_range_candle, session_end
from src.strategy.structure_sl import suggest_stop_loss
from src.utils.orb_math import orb_range_pct

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
    return symbol.replace("NSE:", "").replace("-EQ", "")


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
    rrr: float,
) -> tuple[dict | None, list[dict]]:
    """First breakout of the day, plus the breakout-timeframe candles used to manage it."""
    orb = opening_range_candle(minute_bars, day, orb_minutes)
    if orb is None:
        return None, []
    if max_price_enabled and float(orb["open"]) >= max_stock_price:
        return None, []
    if float(orb["low"]) <= 0:
        return None, []
    range_pct = orb_range_pct(float(orb["high"]), float(orb["low"]))
    if range_pct > orb_range_threshold:
        return None, []

    candles = closed_breakout_candles(
        minute_bars, day, orb_minutes, breakout_minutes, session_end(day)
    )
    prior: list[dict] = []
    for candle in candles:
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
        }
        return signal, candles
    return None, candles


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
            rrr=rrr,
        )
        candles_by_symbol[symbol] = {c["candle_start"]: c for c in candles}
        if signal is not None:
            signals.append(signal)

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
        trades.append(
            {
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
            }
        )

    for stamp in times:
        still_open: list[dict] = []
        for pos in open_positions:
            bar = candles_by_symbol.get(pos["symbol"], {}).get(stamp)
            if bar is None or stamp <= pos["candle_start"]:
                still_open.append(pos)
                continue
            hit = _exit_on_bar(pos["side"], pos["stop"], pos["target"], bar)
            if hit is None:
                still_open.append(pos)
                continue
            exit_px, reason = hit
            _close(pos, exit_px, reason, stamp + timedelta(minutes=breakout_minutes))
        open_positions = still_open

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
            open_positions.append(
                {
                    **sig,
                    "qty": qty,
                    "planned_risk": planned,
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
