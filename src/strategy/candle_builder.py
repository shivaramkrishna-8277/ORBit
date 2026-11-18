"""OHLCV candle aggregator — builds candles from live ticks.

The first bucket is the opening range, which always starts at 09:15 IST.
Later buckets use the breakout timeframe and start when the opening range ends.
"""
from datetime import datetime, timedelta
from typing import Callable

import pytz

from src.strategy.intraday_agg import bucket_start
from src.utils import db
from src.utils.logger import get_logger
from src.utils.market_sessions import is_opening_range_candle, market_open_dt

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")


class CandleBuilder:
    """
    Aggregates incoming ticks into OHLCV candles per symbol.

    on_candle_close callback signature:
        on_candle_close(symbol: str, candle: dict)
        where candle has keys: candle_start, open, high, low, close, volume
    """

    def __init__(
        self,
        on_candle_close: Callable | None = None,
        orb_minutes: int = 15,
        breakout_minutes: int = 15,
    ):
        self._on_candle_close = on_candle_close
        self._orb_minutes = orb_minutes
        self._breakout_minutes = breakout_minutes
        # {symbol: {candle_start: datetime, open, high, low, close, volume}}
        self._candles: dict[str, dict] = {}
        # {symbol: completed opening-range candle}
        self._first_candles: dict[str, dict] = {}
        self._skip_until: datetime | None = None

    def set_intervals(self, orb_minutes: int, breakout_minutes: int) -> None:
        """Apply a new window. Drops in-progress buckets so two sizes are never mixed."""
        if (orb_minutes, breakout_minutes) == (self._orb_minutes, self._breakout_minutes):
            return
        logger.info(
            "Candle intervals changed to ORB %d min / breakout %d min. Dropping in-progress candles.",
            orb_minutes, breakout_minutes,
        )
        self._orb_minutes = orb_minutes
        self._breakout_minutes = breakout_minutes
        self._candles.clear()

    def skip_partial_bucket(self, now: datetime) -> None:
        """Drop a candle the stream did not see from its open, and wait for the next one."""
        if now.tzinfo is None:
            now = IST.localize(now)
        else:
            now = now.astimezone(IST)
        start = bucket_start(now, self._orb_minutes, self._breakout_minutes)
        if now - start <= timedelta(seconds=30):
            self._skip_until = None
            return
        self._candles.clear()
        self._skip_until = start + timedelta(minutes=self._breakout_minutes)

    def on_tick(self, symbol: str, price: float, timestamp: datetime) -> None:
        """Process a single tick and update the current in-progress candle."""
        if timestamp.tzinfo is None:
            ts = IST.localize(timestamp)
        else:
            ts = timestamp.astimezone(IST)

        if ts < market_open_dt(ts):
            return
        if self._skip_until is not None and ts < self._skip_until:
            return

        bucket = bucket_start(ts, self._orb_minutes, self._breakout_minutes)

        if symbol not in self._candles:
            self._candles[symbol] = {
                "candle_start": bucket,
                "open": price, "high": price, "low": price, "close": price,
                "volume": 0.0,
            }
            return

        current = self._candles[symbol]

        if bucket != current["candle_start"]:
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

        if is_opening_range_candle(candle["candle_start"]) and symbol not in self._first_candles:
            self._first_candles[symbol] = dict(candle)
            logger.debug(
                "Opening-range candle locked for %s: H=%.2f L=%.2f",
                symbol, candle["high"], candle["low"],
            )

        if self._on_candle_close:
            try:
                self._on_candle_close(symbol, dict(candle))
            except Exception:
                logger.exception("on_candle_close callback error for %s", symbol)

    def flush_completed(self, boundary: datetime) -> None:
        """Finalise candles that started before boundary. Leave the new bucket alone."""
        if boundary.tzinfo is None:
            boundary = IST.localize(boundary)
        else:
            boundary = boundary.astimezone(IST)
        boundary = boundary.replace(second=0, microsecond=0)

        for symbol, candle in list(self._candles.items()):
            if candle["candle_start"] < boundary:
                self._finalise(symbol, candle)
                del self._candles[symbol]

    def finalize_opening_range(self) -> None:
        """Close the in-progress opening-range candle. Does not touch later candles."""
        for symbol, candle in list(self._candles.items()):
            if is_opening_range_candle(candle["candle_start"]):
                self._finalise(symbol, candle)
                del self._candles[symbol]

    def flush_all(self) -> None:
        """Finalise every in-progress candle."""
        for symbol, candle in list(self._candles.items()):
            self._finalise(symbol, candle)
            del self._candles[symbol]

    def get_current_candle(self, symbol: str) -> dict | None:
        """Return the in-progress candle for symbol, or None."""
        return self._candles.get(symbol)

    def get_first_candle(self, symbol: str) -> dict | None:
        """Return the completed opening-range candle, or None if not ready."""
        return self._first_candles.get(symbol)

    def reset(self) -> None:
        """Clear all state — called at session end."""
        self._candles.clear()
        self._first_candles.clear()
        self._skip_until = None
