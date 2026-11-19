"""Post-ORB swing lows/highs for suggested stop loss (before breakout candle)."""
from __future__ import annotations

from datetime import datetime
from typing import Any


def _swing_low_indices(candles: list[dict], left: int, right: int) -> list[int]:
    n = len(candles)
    if n < left + right + 1:
        return []
    out: list[int] = []
    for i in range(left, n - right):
        low_i = float(candles[i]["low"])
        if any(low_i >= float(candles[i - j]["low"]) for j in range(1, left + 1)):
            continue
        if any(low_i >= float(candles[i + j]["low"]) for j in range(1, right + 1)):
            continue
        out.append(i)
    return out


def _swing_high_indices(candles: list[dict], left: int, right: int) -> list[int]:
    n = len(candles)
    if n < left + right + 1:
        return []
    out: list[int] = []
    for i in range(left, n - right):
        high_i = float(candles[i]["high"])
        if any(high_i <= float(candles[i - j]["high"]) for j in range(1, left + 1)):
            continue
        if any(high_i <= float(candles[i + j]["high"]) for j in range(1, right + 1)):
            continue
        out.append(i)
    return out


def _swing_time(candle: dict) -> datetime | None:
    ts = candle.get("candle_start")
    return ts if isinstance(ts, datetime) else None


def suggest_stop_loss(
    signal_type: str,
    entry: float,
    orb_low: float,
    orb_high: float,
    prior_candles: list[dict],
    *,
    swing_left: int = 1,
    swing_right: int = 1,
) -> dict[str, Any]:
    """
  Pick stop loss from the latest confirmed swing between ORB end and the breakout bar.

  prior_candles: fully closed breakout-timeframe bars before the signal candle (not inclusive).
  """
    entry = float(entry)
    orb_low = float(orb_low)
    orb_high = float(orb_high)

    if signal_type == "BULLISH":
        orb_stop = orb_low
        swings = _swing_low_indices(prior_candles, swing_left, swing_right)
        candidates: list[tuple[int, float]] = []
        for i in swings:
            low = float(prior_candles[i]["low"])
            if low < entry:
                candidates.append((i, low))
        if candidates:
            idx, level = candidates[-1]
            return {
                "suggested_sl": level,
                "orb_stop": orb_stop,
                "stop_basis": "swing",
                "swing_at": _swing_time(prior_candles[idx]),
            }
        return {
            "suggested_sl": orb_stop,
            "orb_stop": orb_stop,
            "stop_basis": "orb",
            "swing_at": None,
        }

    if signal_type == "BEARISH":
        orb_stop = orb_high
        swings = _swing_high_indices(prior_candles, swing_left, swing_right)
        candidates = []
        for i in swings:
            high = float(prior_candles[i]["high"])
            if high > entry:
                candidates.append((i, high))
        if candidates:
            idx, level = candidates[-1]
            return {
                "suggested_sl": level,
                "orb_stop": orb_stop,
                "stop_basis": "swing",
                "swing_at": _swing_time(prior_candles[idx]),
            }
        return {
            "suggested_sl": orb_stop,
            "orb_stop": orb_stop,
            "stop_basis": "orb",
            "swing_at": None,
        }

    raise ValueError(f"Unknown signal_type: {signal_type!r}")