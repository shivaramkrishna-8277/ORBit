"""ORB range calculation helpers."""


def orb_range_pct(high: float, low: float) -> float:
    """Percentage range of the opening candle: (High − Low) / Low × 100."""
    if low <= 0:
        return float("inf")
    return (high - low) / low * 100


def range_passes(range_pct: float, threshold: float, mode: str = "lte") -> bool:
    """True when the opening range meets a less-than or greater-than rule."""
    if mode == "gte":
        return range_pct >= threshold
    return range_pct <= threshold
