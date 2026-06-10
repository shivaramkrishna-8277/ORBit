"""Fyers API client wrapper — quotes and historical candle data.

Usage:
    from src.broker.fyers_client import get_client, get_nifty50_prices
    client = get_client(access_token)
    prices = get_nifty50_prices(client)
"""
import time
from datetime import datetime

import pandas as pd
import pytz

from fyers_apiv3.fyersModel import FyersModel
from src import config
from src.utils.quote_price import quote_filter_price
from src.utils.logger import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")
_MAX_RETRIES = 3
_RETRY_DELAY = 1  # seconds


# ── Client factory ────────────────────────────────────────────────────────────

def get_client(access_token: str) -> FyersModel:
    """Build and return a synchronous FyersModel instance."""
    return FyersModel(
        client_id=config.FYERS_APP_ID,
        token=access_token,
        log_level="ERROR",
    )


# ── API helpers ───────────────────────────────────────────────────────────────

def _call_with_retry(fn, *args, **kwargs):
    """Call fn(*args, **kwargs); retry up to _MAX_RETRIES times on rate-limit errors."""
    for attempt in range(1, _MAX_RETRIES + 1):
        t0 = time.perf_counter()
        response = fn(*args, **kwargs)
        elapsed = (time.perf_counter() - t0) * 1000
        logger.debug("API call took %.0f ms (attempt %d)", elapsed, attempt)

        status = response.get("s") if isinstance(response, dict) else None
        if status == "ok":
            return response

        # Rate-limit or server error — retry after delay
        code = response.get("code", "")
        msg = response.get("message", response)
        if attempt < _MAX_RETRIES:
            logger.warning("API error (attempt %d/%d): %s — retrying in %ds", attempt, _MAX_RETRIES, msg, _RETRY_DELAY)
            time.sleep(_RETRY_DELAY)
        else:
            logger.error("API call failed after %d attempts: code=%s msg=%s", _MAX_RETRIES, code, msg)

    return response


# ── Public functions ──────────────────────────────────────────────────────────

def get_quotes(client: FyersModel, symbols: list[str]) -> dict[str, dict]:
    """
    Fetch live quotes for up to 50 symbols.

    Returns:
        {symbol: {"ltp": float, "open": float, "high": float, "low": float, "close": float}}
        Only symbols with a successful "s": "ok" sub-response are included.
    """
    # Fyers quotes endpoint accepts a comma-separated string, max 50 symbols
    payload = {"symbols": ",".join(symbols)}
    response = _call_with_retry(client.quotes, payload)

    result: dict[str, dict] = {}
    if response.get("s") != "ok":
        return result

    for item in response.get("d", []):
        if item.get("s") != "ok":
            continue
        sym = item["n"]
        v = item["v"]
        result[sym] = {
            "ltp":   v.get("ltp", 0.0),
            "open":  v.get("open", 0.0),
            "high":  v.get("high", 0.0),
            "low":   v.get("low", 0.0),
            "close": v.get("close", 0.0),
        }

    return result


def get_historical_candles(
    client: FyersModel,
    symbol: str,
    resolution: str,
    date_from: str,
    date_to: str,
) -> pd.DataFrame:
    """
    Fetch OHLCV history for a single symbol.

    Args:
        symbol:     Fyers symbol, e.g. "NSE:SBIN-EQ"
        resolution: "15" for 15-min candles, "D" for daily
        date_from:  "YYYY-MM-DD"
        date_to:    "YYYY-MM-DD"

    Returns:
        DataFrame with columns: datetime (IST-aware), open, high, low, close, volume
        Empty DataFrame on error.
    """
    payload = {
        "symbol":     symbol,
        "resolution": resolution,
        "date_format": "1",
        "range_from": date_from,
        "range_to":   date_to,
        "cont_flag":  "1",
    }
    response = _call_with_retry(client.history, payload)

    if response.get("s") != "ok":
        logger.warning("history() failed for %s: %s", symbol, response.get("message", response))
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])

    candles = response.get("candles", [])
    if not candles:
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(candles, columns=["epoch", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["epoch"], unit="s", utc=True).dt.tz_convert(IST)
    df.drop(columns=["epoch"], inplace=True)
    df = df[["datetime", "open", "high", "low", "close", "volume"]]
    return df.reset_index(drop=True)


def get_nifty50_prices(client: FyersModel) -> dict[str, dict]:
    """
    Fetch live prices for all Nifty 50 symbols and return only those
    with price strictly below MAX_STOCK_PRICE.

    Uses LTP when available; otherwise previous close (typical before 9:15).

    Returns:
        {symbol: {"ltp", "open", "high", "low", "close", "filter_price"}}
    """
    all_quotes = get_quotes(client, config.NIFTY50_SYMBOLS)
    filtered: dict[str, dict] = {}
    no_price = 0
    above_limit = 0

    for sym, data in all_quotes.items():
        price = quote_filter_price(data)
        if price is None:
            no_price += 1
            logger.warning("%s — no LTP or previous close; excluded from watchlist", sym)
            continue
        if price >= config.MAX_STOCK_PRICE:
            above_limit += 1
            continue
        filtered[sym] = {**data, "filter_price": price}

    logger.info(
        "Price filter: %d of %d Nifty 50 stocks under ₹%.0f "
        "(%d above limit, %d missing price data)",
        len(filtered), len(all_quotes), config.MAX_STOCK_PRICE, above_limit, no_price,
    )
    return filtered
