"""Fyers WebSocket tick stream — streams live LTP ticks for watchlist symbols."""
import threading
from datetime import datetime
from typing import Callable

import pytz

from fyers_apiv3.FyersWebsocket import data_ws
from src.utils.logger import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")


class TickStreamManager:
    """Streams live ticks from the Fyers WebSocket for a given symbol list."""

    def __init__(self, symbols: list[str], on_tick_callback: Callable):
        self._symbols = symbols
        self._on_tick = on_tick_callback
        self._ws: data_ws.FyersDataSocket | None = None
        self._thread: threading.Thread | None = None
        self._connected = False
        self._active = False

    def _on_message(self, message: dict) -> None:
        if not self._active:
            return
        try:
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
        if not self._active:
            return
        logger.info("WebSocket connected — subscribing to %d symbols", len(self._symbols))
        self._connected = True
        if self._ws:
            self._ws.subscribe(symbols=self._symbols, data_type="SymbolUpdate")

    def _on_error(self, message: dict) -> None:
        if not self._active:
            return
        logger.error("WebSocket error: %s", message)

    def _on_close(self, message: dict) -> None:
        logger.warning("WebSocket closed: %s", message)
        self._connected = False

    def connect(self, access_token: str) -> None:
        """Start the WebSocket in a background thread."""
        self._active = True
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
        """Stop ticks and tear down the SDK reconnect loop."""
        self._active = False
        self._connected = False
        ws = self._ws
        self._ws = None

        if ws is None:
            return

        for attr in ("keep_running", "Keep_running"):
            if hasattr(ws, attr):
                try:
                    setattr(ws, attr, False)
                except Exception:
                    pass

        for method_name in ("close_connection", "close", "disconnect", "stop"):
            method = getattr(ws, method_name, None)
            if callable(method):
                try:
                    method()
                    logger.debug("WebSocket %s() called", method_name)
                    break
                except Exception:
                    logger.debug("WebSocket %s() failed", method_name, exc_info=True)

        logger.info("WebSocket disconnected.")

    @property
    def is_connected(self) -> bool:
        return self._connected and self._active
