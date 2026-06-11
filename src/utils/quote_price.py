"""Helpers for choosing a reliable price from Fyers quote payloads."""


def normalize_fyers_quote(raw: dict) -> dict:
    """
    Map Fyers v3 quote fields to a stable internal shape.

    Fyers uses lp / prev_close_price / open_price etc.; older code expected
    ltp / close / open. Accept both so callers always see the same keys.
    """
    return {
        "ltp": float(raw.get("lp") or raw.get("ltp") or 0.0),
        "open": float(
            raw.get("open_price") or raw.get("open") or raw.get("o") or 0.0
        ),
        "high": float(
            raw.get("high_price") or raw.get("high") or raw.get("h") or 0.0
        ),
        "low": float(
            raw.get("low_price") or raw.get("low") or raw.get("l") or 0.0
        ),
        "close": float(
            raw.get("prev_close_price") or raw.get("close") or raw.get("c") or 0.0
        ),
    }


def quote_filter_price(quote: dict) -> float | None:
    """
    Best available price for the pre-market watchlist filter.

    Before the 9:15 open, Fyers often returns lp=0; fall back to the
    previous session close so stocks above MAX_STOCK_PRICE are excluded.
    """
    normalized = normalize_fyers_quote(quote) if "lp" in quote or "prev_close_price" in quote else quote

    ltp = normalized.get("ltp") or 0.0
    if ltp > 0:
        return float(ltp)

    prev_close = normalized.get("close") or 0.0
    if prev_close > 0:
        return float(prev_close)

    open_price = normalized.get("open") or 0.0
    if open_price > 0:
        return float(open_price)

    return None
