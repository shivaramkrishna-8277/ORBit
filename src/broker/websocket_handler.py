"""Fyers WebSocket tick stream — streams live LTP ticks for watchlist symbols.

The FyersDataSocket has built-in reconnect support (reconnect=True).
We wrap it in a thread and provide clean connect/disconnect semantics.
"""
import threading
import time
from datetime import datetime
from typing import Callable

import pytz

from fyers_apiv3.FyersWebsocket import data_ws
from src.utils.logger import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")


class TickStreamManager:
    """
    Streams live ticks from the Fyers WebSocket for a given symbol list.

    Args:
        symbols:            List of Fyers symbols, e.g. ["NSE:SBIN-EQ", ...]
        on_tick_callback:   Callable(symbol: str, ltp: float, timestamp: datetime)
    """

    def __init__(self, symbols: list[str], on_tick_callback: Callable):
        self._symbols = symbols
        self._on_tick = on_tick_callback
        self._ws: data_ws.FyersDataSocket | None = None
        self._thread: threading.Thread | None = None
        self._connected = False

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_message(self, message: dict) -> None:
        """Parse incoming tick and forward to callback."""
        try:
            # FyersDataSocket delivers a list of tick dicts in "SymbolUpdate" mode
            ticks = message if isinstance(message, list) else [message]
            for tick in ticks:
                symbol = tick.get("symbol") or tick.get("n")
                ltp = tick.get("ltp")
                if not symbol or ltp is None:
                    continue
                ts = datetime.now(IST)
                self._on_tick(symbol, float(ltp), ts)
        except Exception:
            logger.exception("Error processing tick: %s", message)

    def _on_connect(self) -> None:
        logger.info("WebSocket connected — subscribing to %d symbols", len(self._symbols))
        self._connected = True
        if self._ws:
            self._ws.subscribe(symbols=self._symbols, data_type="SymbolUpdate")

    def _on_error(self, message: dict) -> None:
        logger.error("WebSocket error: %s", message)

    def _on_close(self, message: dict) -> None:
        logger.warning("WebSocket closed: %s", message)
        self._connected = False

    # ── Public API ─────────────────────────────────────────────────────────────

    def connect(self, access_token: str) -> None:
        """
        Initialise the WebSocket and start it in a background thread.
        The SDK handles reconnects internally (reconnect=True).
        """
        self._ws = data_ws.FyersDataSocket(
            access_token=access_token,
            write_to_file=False,
            litemode=False,
            reconnect=True,
            reconnect_retry=3,
            on_connect=self._on_connect,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

        self._thread = threading.Thread(
            target=self._ws.connect,
            name="FyersWS",
            daemon=True,
        )
        self._thread.start()
        logger.info("WebSocket thread started.")

    def disconnect(self) -> None:
        """Cleanly close the WebSocket connection."""
        if self._ws:
            try:
                self._ws.unsubscribe(symbols=self._symbols, data_type="SymbolUpdate")
            except Exception:
                pass
            self._connected = False
            self._ws = None
            logger.info("WebSocket disconnected.")

    @property
    def is_connected(self) -> bool:
        return self._connected
