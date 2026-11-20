"""Daily watchlist builder — uses the dashboard tracking list."""
from datetime import datetime

import pytz

from src.broker import fyers_client
from src.strategy.indices import is_index
from src.strategy.runtime_settings import load_settings
from src.utils import db
from src.utils.logger import get_logger
from src.utils.quote_price import quote_filter_price

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")


class WatchlistManager:
    def __init__(self, client):
        """
        Args:
            client: FyersModel instance (from fyers_client.get_client)
        """
        self._client = client

    def build_daily_watchlist(self) -> list[str]:
        """
        Take the dashboard symbol list, optionally drop names at or above the
        max price, persist the result, and return the symbols to track today.
        """
        today = datetime.now(IST).strftime("%Y-%m-%d")
        settings = load_settings()
        symbols = list(settings.symbols)
        for symbol in settings.index_symbols or []:
            if symbol not in symbols:
                symbols.append(symbol)
        db.delete_watchlist(today)

        if not symbols:
            logger.warning("Tracking list is empty. Add symbols in the dashboard.")
            return []

        quotes = fyers_client.get_quotes(self._client, symbols)
        kept: list[str] = []
        dropped_price = 0
        missing = 0

        for sym in symbols:
            if is_index(sym):
                price = quote_filter_price(quotes.get(sym)) if quotes.get(sym) else 0.0
                db.insert_watchlist(today, sym, price or 0.0)
                kept.append(sym)
                continue
            data = quotes.get(sym)
            price = quote_filter_price(data) if data else None
            if settings.max_price_enabled:
                if price is None:
                    missing += 1
                    logger.info("%s — no price; excluded by the price filter", sym)
                    continue
                if price >= settings.max_stock_price:
                    dropped_price += 1
                    continue
            db.insert_watchlist(today, sym, price or 0.0)
            kept.append(sym)

        if settings.max_price_enabled:
            logger.info(
                "%d of %d tracked symbols under ₹%.0f today (%s); %d at/above the cap, %d missing a price",
                len(kept), len(symbols), settings.max_stock_price, today, dropped_price, missing,
            )
        else:
            logger.info("Tracking %d symbols today (%s). Price filter is off.", len(kept), today)
        return sorted(kept)

    def get_todays_watchlist(self) -> list[str]:
        """
        Return today's watchlist from SQLite.
        Falls back to rebuilding via the API if today's data is not found.
        """
        today = datetime.now(IST).strftime("%Y-%m-%d")
        symbols = db.get_watchlist(today)
        if symbols:
            logger.info("Loaded %d symbols from cached watchlist for %s", len(symbols), today)
            return symbols

        logger.warning("No watchlist found for %s — rebuilding from the tracking list.", today)
        return self.build_daily_watchlist()
