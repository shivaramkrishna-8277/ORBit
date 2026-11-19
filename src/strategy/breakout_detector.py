"""Breakout detector — checks closed candles for ORB breakouts after 9:30 AM."""
from collections import defaultdict
from datetime import datetime
from typing import Callable

import pytz

from src.strategy.structure_sl import suggest_stop_loss
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
            signal dict: {symbol, signal_type, candle_close, orb_level, move_pct, candle_time,
                          suggested_sl, orb_stop, stop_basis, ...}
    """

    def __init__(self, orb_levels: dict[str, dict], on_breakout_callback: Callable):
        self._orb = orb_levels
        self._callback = on_breakout_callback
        self._prior_candles: dict[str, list[dict]] = defaultdict(list)

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
        prior = list(self._prior_candles[symbol])

        def _emit(signal_type: str, orb_level: float, move_pct: float) -> None:
            sl = suggest_stop_loss(
                signal_type,
                close,
                orb_low,
                orb_high,
                prior,
            )
            signal_id = db.insert_signal(
                date=today,
                symbol=symbol,
                signal_type=signal_type,
                candle_close=close,
                orb_level=orb_level,
                move_pct=move_pct,
                triggered_at=candle_time_str,
                suggested_sl=sl["suggested_sl"],
                orb_stop=sl["orb_stop"],
                stop_basis=sl["stop_basis"],
            )
            swing_at = sl.get("swing_at")
            signal = {
                "id": signal_id,
                "symbol": symbol,
                "signal_type": signal_type,
                "candle_close": close,
                "orb_level": orb_level,
                "move_pct": move_pct,
                "candle_time": candle_time_str,
                "suggested_sl": sl["suggested_sl"],
                "orb_stop": sl["orb_stop"],
                "stop_basis": sl["stop_basis"],
                "swing_at": swing_at.strftime("%H:%M IST") if isinstance(swing_at, datetime) else None,
            }
            self._callback(signal)

        # ── BULLISH ──────────────────────────────────────────────────────────
        if close > orb_high:
            if not db.has_signal_fired(today, symbol, "BULLISH") and \
                    not db.has_signal_fired(today, symbol, "BEARISH"):
                move_pct = (close - orb_high) / orb_high * 100
                logger.info(
                    "BULLISH breakout — %s | Close=%.2f > ORB High=%.2f (+%.2f%%)",
                    symbol, close, orb_high, move_pct,
                )
                _emit("BULLISH", orb_high, move_pct)

        # ── BEARISH ──────────────────────────────────────────────────────────
        elif close < orb_low:
            if not db.has_signal_fired(today, symbol, "BEARISH") and \
                    not db.has_signal_fired(today, symbol, "BULLISH"):
                move_pct = (orb_low - close) / orb_low * 100
                logger.info(
                    "BEARISH breakout — %s | Close=%.2f < ORB Low=%.2f (-%.2f%%)",
                    symbol, close, orb_low, move_pct,
                )
                _emit("BEARISH", orb_low, move_pct)

        self._prior_candles[symbol].append(dict(candle))

    def update_orb_levels(self, orb_levels: dict[str, dict]) -> None:
        """Replace the ORB levels (called after 9:30 AM processing)."""
        self._orb = orb_levels