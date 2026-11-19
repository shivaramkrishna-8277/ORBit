"""Candles and price levels for one backtest trade, from 09:15 through the exit."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytz

from src.strategy.intraday_agg import market_open

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
    """Breakout-timeframe candles from 09:15 through the candle that ends at the exit."""
    del orb_minutes  # levels carry the opening range; every candle uses the breakout size
    day = datetime.strptime(trade["date"], "%Y-%m-%d")
    day_dt = IST.localize(day.replace(hour=9, minute=15))
    exit_dt = IST.localize(datetime.strptime(f"{trade['date']} {trade['exit_time']}", "%Y-%m-%d %H:%M"))
    day_bars = [bar for bar in minute_bars if bar["datetime"].astimezone(IST).date() == day_dt.date()]
    step = timedelta(minutes=breakout_minutes)
    start = market_open(day_dt)
    candles: list[dict] = []
    while start + step <= exit_dt:
        end = start + step
        last = end - timedelta(minutes=1)
        window = [bar for bar in day_bars if start <= bar["datetime"] < end]
        if any(bar["datetime"] == last for bar in window):
            ordered = sorted(window, key=lambda bar: bar["datetime"])
            candles.append(
                {
                    "candle_start": start,
                    "open": ordered[0]["open"],
                    "high": max(bar["high"] for bar in ordered),
                    "low": min(bar["low"] for bar in ordered),
                    "close": ordered[-1]["close"],
                }
            )
        start = end
    return candles


def price_window(candles: list[dict], levels: list[dict]) -> tuple[float, float]:
    """Y-axis bounds around the candles and trade levels, not down to zero."""
    values = [float(level["price"]) for level in levels]
    for candle in candles:
        values.append(float(candle["low"]))
        values.append(float(candle["high"]))
    low = min(values)
    high = max(values)
    pad = max((high - low) * 0.12, 0.05)
    return low - pad, high + pad


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
