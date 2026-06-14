"""ORB level calculator — locks and filters the 9:15–9:30 first candle at 9:30 AM."""
from datetime import datetime

import pytz

from src import config
from src.broker import fyers_client
from src.strategy.candle_builder import CandleBuilder
from src.utils import db
from src.utils.logger import get_logger
from src.utils.market_sessions import is_opening_range_candle
from src.utils.orb_math import orb_range_pct
logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")


class ORBCalculator:

    def calculate_orb(self, symbol: str, first_candle: dict) -> dict | None:
        """
        Evaluate the **first 15-minute candle (9:15–9:30 IST)** and decide whether
        the stock qualifies.

        Range % = (High − Low) / Low × 100  using that candle's high and low only.
        """
        today = datetime.now(IST).strftime("%Y-%m-%d")
        candle_start = first_candle.get("candle_start")
        if candle_start is not None and not is_opening_range_candle(candle_start):
            logger.warning(
                "%s — skipped: ORB range must use the 9:15–9:30 first candle, got %s",
                symbol, candle_start,
            )
            return None

        orb_high = first_candle["high"]
        orb_low = first_candle["low"]
        open_price = first_candle.get("open", 0.0)

        if open_price >= config.MAX_STOCK_PRICE:
            db.insert_orb_level(today, symbol, orb_high, orb_low, 0.0, passed=0)
            logger.info(
                "%s DROPPED — open ₹%.2f exceeds ₹%.0f price limit",
                symbol, open_price, config.MAX_STOCK_PRICE,
            )
            return None

        if orb_low == 0:
            logger.warning("%s — ORB low is zero, skipping.", symbol)
            return None

        range_pct = orb_range_pct(orb_high, orb_low)

        if range_pct <= config.ORB_RANGE_THRESHOLD:
            db.insert_orb_level(today, symbol, orb_high, orb_low, range_pct, passed=1)
            logger.info(
                "%s PASSED ORB filter — first 15-min range %.2f%% (H=%.2f L=%.2f)",
                symbol, range_pct, orb_high, orb_low,
            )
            return {"symbol": symbol, "orb_high": orb_high, "orb_low": orb_low, "range_pct": range_pct}
        else:
            db.insert_orb_level(today, symbol, orb_high, orb_low, range_pct, passed=0)
            logger.info(
                "%s DROPPED — first 15-min range %.2f%% exceeds %.1f%% threshold",
                symbol, range_pct, config.ORB_RANGE_THRESHOLD,
            )
            return None

    def process_all_symbols(
        self,
        candle_builder: CandleBuilder,
        watchlist: list[str],
        client=None,
    ) -> dict[str, dict]:
        """
        Run the ORB filter on the **9:15–9:30 first 15-minute candle** for each symbol.

        Prefers the exchange OHLC for that exact candle (matches TradingView).
        Falls back to the tick-built first candle if the API is not ready yet.

        Returns:
            {symbol: {orb_high, orb_low}} for symbols that passed the filter.
        """
        candle_builder.finalize_opening_range()
        today = datetime.now(IST).strftime("%Y-%m-%d")

        passed: dict[str, dict] = {}
        dropped = 0

        for symbol in watchlist:
            tick_candle = candle_builder.get_first_candle(symbol)
            first_candle = None

            if client is not None:
                first_candle = fyers_client.get_opening_range_candle(
                    client, symbol, today
                )
                if first_candle and tick_candle:
                    api_range = orb_range_pct(first_candle["high"], first_candle["low"])
                    tick_range = orb_range_pct(tick_candle["high"], tick_candle["low"])
                    if abs(api_range - tick_range) > 0.15:
                        logger.warning(
                            "%s — tick first 15-min ORB %.2f%% (H=%.2f L=%.2f) vs "
                            "exchange %.2f%% (H=%.2f L=%.2f); using exchange candle",
                            symbol,
                            tick_range, tick_candle["high"], tick_candle["low"],
                            api_range, first_candle["high"], first_candle["low"],
                        )
                elif first_candle is None and tick_candle is not None:
                    logger.warning(
                        "%s — exchange 9:15–9:30 candle not ready; using tick-built OHLC",
                        symbol,
                    )

            if first_candle is None:
                first_candle = tick_candle

            if first_candle is None:
                logger.warning("%s — no 9:15–9:30 first candle at ORB lock, skipping.", symbol)
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
