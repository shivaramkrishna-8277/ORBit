"""Candles and price levels for one backtest trade, from 09:15 through the exit."""
from __future__ import annotations

from datetime import datetime

import pytz

from src.strategy.intraday_agg import closed_breakout_candles, opening_range_candle

IST = pytz.timezone("Asia/Kolkata")

_EXIT_LABELS = {
    "target": "Target",
    "stop": "Stop",
    "session close": "Session close",
}


def exit_label(reason: str) -> str:
    return _EXIT_LABELS.get(reason, reason)


def trade_candles(
    trade: dict,
    minute_bars: list[dict],
    *,
    orb_minutes: int,
    breakout_minutes: int,
) -> list[dict]:
    """Opening-range candle plus breakout candles that finished by the exit."""
    day = datetime.strptime(trade["date"], "%Y-%m-%d")
    day_dt = IST.localize(day.replace(hour=9, minute=15))
    exit_dt = IST.localize(datetime.strptime(f"{trade['date']} {trade['exit_time']}", "%Y-%m-%d %H:%M"))
    day_bars = [bar for bar in minute_bars if bar["datetime"].astimezone(IST).date() == day_dt.date()]
    candles: list[dict] = []
    opening = opening_range_candle(day_bars, day_dt, orb_minutes)
    if opening is not None:
        candles.append(opening)
    candles.extend(
        closed_breakout_candles(day_bars, day_dt, orb_minutes, breakout_minutes, exit_dt)
    )
    return candles


def trade_levels(trade: dict) -> list[dict]:
    """Horizontal prices to draw on the trade chart."""
    levels = []
    if trade.get("orb_high") is not None:
        levels.append({"name": "ORB high", "price": float(trade["orb_high"])})
    if trade.get("orb_low") is not None:
        levels.append({"name": "ORB low", "price": float(trade["orb_low"])})
    levels.append({"name": "Entry", "price": float(trade["entry"])})
    levels.append({"name": "Stop", "price": float(trade["stop"])})
    levels.append({"name": "Target", "price": float(trade["target"])})
    return levels
