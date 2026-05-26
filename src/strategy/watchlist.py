"""Daily watchlist builder — filters Nifty 50 to stocks under MAX_STOCK_PRICE."""
from datetime import datetime

import pytz

from src.broker import fyers_client
from src.utils import db
from src.utils.logger import get_logger

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
        Fetch live prices, apply the price filter, persist to SQLite,
        and return the list of qualifying symbols.
        """
        today = datetime.now(IST).strftime("%Y-%m-%d")
        prices = fyers_client.get_nifty50_prices(self._client)

        symbols: list[str] = []
        for sym, data in prices.items():
            db.insert_watchlist(today, sym, data["ltp"])
            symbols.append(sym)

        logger.info(
            "%d of 50 Nifty stocks under ₹%.0f today (%s)",
            len(symbols), __import__("src.config", fromlist=["MAX_STOCK_PRICE"]).MAX_STOCK_PRICE, today,
        )
        return sorted(symbols)

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

        logger.warning("No watchlist found for %s — rebuilding from API.", today)
        return self.build_daily_watchlist()
