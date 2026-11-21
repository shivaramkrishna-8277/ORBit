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
    confirming_candle: dict | None = None,
) -> dict[str, Any]:
    """
    Pick stop loss from the latest confirmed swing before the entry candle.

    prior_candles are the closed breakout bars before the entry. The entry candle
    can confirm the swing on the bar just before it, but it is not a swing itself.
    A swing beyond the opening range, below the low for a long or above the high
    for a short, falls back to that opening-range extreme.
    """
    entry = float(entry)
    orb_low = float(orb_low)
    orb_high = float(orb_high)
    scanned = list(prior_candles)
    if confirming_candle is not None:
        scanned.append(confirming_candle)
    last_prior = len(prior_candles) - 1

    if signal_type == "BULLISH":
        orb_stop = orb_low
        swings = [i for i in _swing_low_indices(scanned, swing_left, swing_right) if i <= last_prior]
        candidates: list[tuple[int, float]] = []
        for i in swings:
            low = float(scanned[i]["low"])
            if low < entry:
                candidates.append((i, low))
        if candidates:
            idx, level = candidates[-1]
            # A swing below the opening-range low is outside the range: use the range.
            if level >= orb_low:
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
        swings = [i for i in _swing_high_indices(scanned, swing_left, swing_right) if i <= last_prior]
        candidates = []
        for i in swings:
            high = float(scanned[i]["high"])
            if high > entry:
                candidates.append((i, high))
        if candidates:
            idx, level = candidates[-1]
            # A swing above the opening-range high is outside the range: use the range.
            if level <= orb_high:
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