"""ORB range calculation helpers."""


def orb_range_pct(high: float, low: float) -> float:
    """Percentage range of the opening candle: (High − Low) / Low × 100."""
    if low <= 0:
        return float("inf")
    return (high - low) / low * 100
