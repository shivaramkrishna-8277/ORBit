"""Breakout detector — checks closed candles for ORB breakouts after 9:30 AM."""
from datetime import datetime
from typing import Callable

import pytz

from src.utils import db
from src.utils.logger import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")


class BreakoutDetector:
    """
    Monitors closed candles for ORB breakouts.

    Args:
        orb_levels:           {symbol: {orb_high, orb_low}}
        on_breakout_callback: Callable(signal: dict) — called when a signal fires.
            signal dict: {symbol, signal_type, candle_close, orb_level, move_pct, candle_time}
    """

    def __init__(self, orb_levels: dict[str, dict], on_breakout_callback: Callable):
        self._orb = orb_levels
        self._callback = on_breakout_callback

    def check_candle(self, symbol: str, candle: dict) -> None:
        """
        Evaluate a completed candle for BULLISH or BEARISH breakout.
        Uses candle["close"] — never a tick price — for comparison.
        Each direction fires at most once per symbol per day (deduplication via DB).
        """
        if symbol not in self._orb:
            return

        today = (
            candle["candle_start"].strftime("%Y-%m-%d")
            if isinstance(candle.get("candle_start"), datetime)
            else datetime.now(IST).strftime("%Y-%m-%d")
        )
        candle_time_str = (
            candle["candle_start"].strftime("%H:%M IST")
            if isinstance(candle.get("candle_start"), datetime)
            else "??:??"
        )

        close = candle["close"]
        orb_high = self._orb[symbol]["orb_high"]
        orb_low = self._orb[symbol]["orb_low"]

        # ── BULLISH ──────────────────────────────────────────────────────────
        if close > orb_high:
            if not db.has_signal_fired(today, symbol, "BULLISH") and \
                    not db.has_signal_fired(today, symbol, "BEARISH"):
                move_pct = (close - orb_high) / orb_high * 100
                signal_id = db.insert_signal(
                    date=today,
                    symbol=symbol,
                    signal_type="BULLISH",
                    candle_close=close,
                    orb_level=orb_high,
                    move_pct=move_pct,
                    triggered_at=candle_time_str,
                )
                signal = {
                    "id": signal_id,
                    "symbol": symbol,
                    "signal_type": "BULLISH",
                    "candle_close": close,
                    "orb_level": orb_high,
                    "move_pct": move_pct,
                    "candle_time": candle_time_str,
                }
                logger.info(
                    "BULLISH breakout — %s | Close=%.2f > ORB High=%.2f (+%.2f%%)",
                    symbol, close, orb_high, move_pct,
                )
                self._callback(signal)

        # ── BEARISH ──────────────────────────────────────────────────────────
        elif close < orb_low:
            if not db.has_signal_fired(today, symbol, "BEARISH") and \
                    not db.has_signal_fired(today, symbol, "BULLISH"):
                move_pct = (orb_low - close) / orb_low * 100
                signal_id = db.insert_signal(
                    date=today,
                    symbol=symbol,
                    signal_type="BEARISH",
                    candle_close=close,
                    orb_level=orb_low,
                    move_pct=move_pct,
                    triggered_at=candle_time_str,
                )
                signal = {
                    "id": signal_id,
                    "symbol": symbol,
                    "signal_type": "BEARISH",
                    "candle_close": close,
                    "orb_level": orb_low,
                    "move_pct": move_pct,
                    "candle_time": candle_time_str,
                }
                logger.info(
                    "BEARISH breakout — %s | Close=%.2f < ORB Low=%.2f (-%.2f%%)",
                    symbol, close, orb_low, move_pct,
                )
                self._callback(signal)

    def update_orb_levels(self, orb_levels: dict[str, dict]) -> None:
        """Replace the ORB levels (called after 9:30 AM processing)."""
        self._orb = orb_levels
