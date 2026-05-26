"""ORB level calculator — locks and filters the 9:15–9:30 first candle at 9:30 AM."""
from datetime import datetime

import pytz

from src import config
from src.strategy.candle_builder import CandleBuilder
from src.utils import db
from src.utils.logger import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")


class ORBCalculator:

    def calculate_orb(self, symbol: str, first_candle: dict) -> dict | None:
        """
        Evaluate a stock's ORB range and decide whether it qualifies.

        Args:
            symbol:       Fyers symbol string
            first_candle: dict with keys open, high, low, close

        Returns:
            {symbol, orb_high, orb_low, range_pct} if the range ≤ threshold,
            else None.
        """
        today = datetime.now(IST).strftime("%Y-%m-%d")
        orb_high = first_candle["high"]
        orb_low = first_candle["low"]

        if orb_low == 0:
            logger.warning("%s — ORB low is zero, skipping.", symbol)
            return None

        range_pct = (orb_high - orb_low) / orb_low * 100

        if range_pct <= config.ORB_RANGE_THRESHOLD:
            db.insert_orb_level(today, symbol, orb_high, orb_low, range_pct, passed=1)
            logger.info(
                "%s PASSED ORB filter — range %.2f%% (H=%.2f L=%.2f)",
                symbol, range_pct, orb_high, orb_low,
            )
            return {"symbol": symbol, "orb_high": orb_high, "orb_low": orb_low, "range_pct": range_pct}
        else:
            db.insert_orb_level(today, symbol, orb_high, orb_low, range_pct, passed=0)
            logger.info(
                "%s DROPPED — range %.2f%% exceeds %.1f%% threshold",
                symbol, range_pct, config.ORB_RANGE_THRESHOLD,
            )
            return None

    def process_all_symbols(
        self, candle_builder: CandleBuilder, watchlist: list[str]
    ) -> dict[str, dict]:
        """
        Called at exactly 9:30 AM — flushes candles, evaluates every symbol,
        and returns the qualifying ORB levels.

        Returns:
            {symbol: {orb_high, orb_low}} for symbols that passed the filter.
        """
        # Flush any in-progress ticks so first candles are complete
        candle_builder.flush_all()

        passed: dict[str, dict] = {}
        dropped = 0

        for symbol in watchlist:
            first_candle = candle_builder.get_first_candle(symbol)
            if first_candle is None:
                logger.warning("%s — no first candle data at 9:30, skipping.", symbol)
                dropped += 1
                continue

            result = self.calculate_orb(symbol, first_candle)
            if result:
                passed[symbol] = {"orb_high": result["orb_high"], "orb_low": result["orb_low"]}
            else:
                dropped += 1

        logger.info(
            "ORB filter complete: %d passed, %d dropped (of %d total watchlist stocks)",
            len(passed), dropped, len(watchlist),
        )
        return passed
