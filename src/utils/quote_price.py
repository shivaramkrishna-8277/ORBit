"""Helpers for choosing a reliable price from Fyers quote payloads."""


def quote_filter_price(quote: dict) -> float | None:
    """
    Best available price for the pre-market watchlist filter.

    Before the 9:15 open, Fyers often returns ltp=0; fall back to the
    previous session close so stocks above MAX_STOCK_PRICE are excluded.
    """
    ltp = quote.get("ltp") or 0.0
    if ltp > 0:
        return float(ltp)
    prev_close = quote.get("close") or 0.0
    if prev_close > 0:
        return float(prev_close)
    return None
