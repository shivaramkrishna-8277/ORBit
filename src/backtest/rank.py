"""Order same-time breakouts by post-ORB volume interest, then ticker."""
from __future__ import annotations


def volume_interest(breakout_volume: float, prior_volumes: list[float]) -> tuple[bool, float]:
    """
    Interested when the breakout bar is above the average of every earlier
    breakout-timeframe candle since the opening range ended, or above each of
    the previous five of those candles.

    Returns (interested, breakout_volume / prior_average). The ratio is 0 when
    there is no prior volume.
    """
    prior = [float(v) for v in prior_volumes if v is not None]
    avg = sum(prior) / len(prior) if prior else 0.0
    surge = avg > 0 and breakout_volume > avg
    recent = prior[-5:]
    rising = bool(recent) and all(breakout_volume > v for v in recent)
    ratio = (breakout_volume / avg) if avg > 0 else 0.0
    return surge or rising, ratio


def rank_signals(signals: list[dict]) -> list[dict]:
    """Interested names first, then higher volume ratio, then ticker."""

    def key(sig: dict) -> tuple:
        ticker = sig.get("ticker") or sig["symbol"]
        return (0 if sig.get("interested") else 1, -float(sig.get("volume_ratio") or 0), ticker)

    return sorted(signals, key=key)
