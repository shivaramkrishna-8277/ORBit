"""15-minute OHLCV candle aggregator — builds candles from live ticks.

Buckets are aligned to market open: 9:15, 9:30, 9:45, 10:00 … 15:15 IST.
"""
from datetime import datetime
from typing import Callable

import pytz

from src.utils import db
from src.utils.logger import get_logger
from src.utils.market_sessions import is_opening_range_candle, market_open_dt

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# Market open is 9:15 AM IST
_MARKET_OPEN_HOUR = 9
_MARKET_OPEN_MINUTE = 15
_CANDLE_MINUTES = 15


def _candle_bucket(ts: datetime) -> datetime:
    """
    Return the candle-start datetime for a given IST timestamp.
    Buckets start at 9:15 and repeat every 15 minutes: 9:15, 9:30, 9:45 …
    Ticks before 9:15 are placed in the 9:15 bucket.
    """
    if ts.tzinfo is None:
        ts = IST.localize(ts)
    else:
        ts = ts.astimezone(IST)

    # Minutes since market open
    market_open = ts.replace(
        hour=_MARKET_OPEN_HOUR, minute=_MARKET_OPEN_MINUTE, second=0, microsecond=0
    )
    if ts < market_open:
        return market_open  # pre-market ticks go into the first bucket

    minutes_since_open = (ts - market_open).seconds // 60
    bucket_offset = (minutes_since_open // _CANDLE_MINUTES) * _CANDLE_MINUTES
    return market_open.replace(minute=_MARKET_OPEN_MINUTE + bucket_offset % 60,
                                hour=_MARKET_OPEN_HOUR + (_MARKET_OPEN_MINUTE + bucket_offset) // 60,
                                second=0, microsecond=0)


def _bucket_start(ts: datetime) -> datetime:
    """Cleaner bucket calculation: floor timestamp to 15-min market-aligned buckets."""
    if ts.tzinfo is None:
        ts = IST.localize(ts)
    else:
        ts = ts.astimezone(IST)

    market_open = ts.replace(
        hour=_MARKET_OPEN_HOUR, minute=_MARKET_OPEN_MINUTE, second=0, microsecond=0
    )
    if ts < market_open:
        return market_open  # unused when pre-market ticks are ignored in on_tick

    elapsed_seconds = int((ts - market_open).total_seconds())
    bucket_seconds = (elapsed_seconds // (_CANDLE_MINUTES * 60)) * (_CANDLE_MINUTES * 60)
    return datetime.fromtimestamp(market_open.timestamp() + bucket_seconds, tz=IST)


class CandleBuilder:
    """
    Aggregates incoming ticks into 15-minute OHLCV candles per symbol.

    on_candle_close callback signature:
        on_candle_close(symbol: str, candle: dict)
        where candle has keys: candle_start, open, high, low, close, volume
    """

    def __init__(self, on_candle_close: Callable | None = None):
        self._on_candle_close = on_candle_close
        # {symbol: {candle_start: datetime, open, high, low, close, volume}}
        self._candles: dict[str, dict] = {}
        # {symbol: completed first candle (9:15–9:30)}
        self._first_candles: dict[str, dict] = {}

    def on_tick(self, symbol: str, price: float, timestamp: datetime) -> None:
        """Process a single tick and update the current in-progress candle."""
        if timestamp.tzinfo is None:
            ts = IST.localize(timestamp)
        else:
            ts = timestamp.astimezone(IST)

        # Only build candles from 9:15 AM onward (first bucket = 9:15–9:30)
        if ts < market_open_dt(ts):
            return

        bucket = _bucket_start(ts)

        if symbol not in self._candles:
            # First tick ever for this symbol
            self._candles[symbol] = {
                "candle_start": bucket,
                "open": price, "high": price, "low": price, "close": price,
                "volume": 0.0,
            }
            return

        current = self._candles[symbol]

        if bucket != current["candle_start"]:
            # New bucket: finalise the old candle
            self._finalise(symbol, current)
            self._candles[symbol] = {
                "candle_start": bucket,
                "open": price, "high": price, "low": price, "close": price,
                "volume": 0.0,
            }
        else:
            current["high"] = max(current["high"], price)
            current["low"] = min(current["low"], price)
            current["close"] = price

    def _finalise(self, symbol: str, candle: dict) -> None:
        """Persist a completed candle and fire the on_candle_close callback."""
        today = candle["candle_start"].strftime("%Y-%m-%d")
        candle_time_str = candle["candle_start"].strftime("%H:%M")

        db.insert_candle(
            date=today,
            symbol=symbol,
            candle_time=candle_time_str,
            open_=candle["open"],
            high=candle["high"],
            low=candle["low"],
            close=candle["close"],
            volume=candle["volume"],
        )

        # Cache the 9:15–9:30 first 15-minute candle (ORB window)
        if is_opening_range_candle(candle["candle_start"]) and symbol not in self._first_candles:
            self._first_candles[symbol] = dict(candle)
            logger.debug(
                "First 15-min candle (9:15–9:30) locked for %s: H=%.2f L=%.2f",
                symbol, candle["high"], candle["low"],
            )

        if self._on_candle_close:
            try:
                self._on_candle_close(symbol, dict(candle))
            except Exception:
                logger.exception("on_candle_close callback error for %s", symbol)

    def finalize_opening_range(self) -> None:
        """
        Close the in-progress 9:15–9:30 first candle at the 9:30 boundary.

        Called once after the opening range ends; does not touch later candles.
        """
        for symbol, candle in list(self._candles.items()):
            if is_opening_range_candle(candle["candle_start"]):
                self._finalise(symbol, candle)
                del self._candles[symbol]

    def flush_all(self) -> None:
        """
        Manually finalise all in-progress candles.
        Called at 9:30 (to lock the ORB candle) and at each subsequent
        15-minute boundary by the session scheduler.
        """
        for symbol, candle in list(self._candles.items()):
            self._finalise(symbol, candle)
            del self._candles[symbol]

    def get_current_candle(self, symbol: str) -> dict | None:
        """Return the in-progress candle for symbol, or None."""
        return self._candles.get(symbol)

    def get_first_candle(self, symbol: str) -> dict | None:
        """Return the completed 9:15–9:30 first candle, or None if not ready."""
        return self._first_candles.get(symbol)

    def reset(self) -> None:
        """Clear all state — called at session end."""
        self._candles.clear()
        self._first_candles.clear()
