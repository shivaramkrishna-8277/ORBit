"""Telegram notification module — sends ORB alerts and session summaries."""
import asyncio
from datetime import datetime
from src.alerts.position_calculator import prompt_calculator

import pytz
from telegram import Bot
from telegram.error import TelegramError

from src import config
from src.utils import db
from src.utils.logger import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")


class TelegramNotifier:
    def __init__(self, dry_run: bool = False):
        """
        Args:
            dry_run: If True, print messages to console instead of sending to Telegram.
        """
        self._token = config.TELEGRAM_BOT_TOKEN
        self._chat_id = config.TELEGRAM_CHAT_ID
        self._dry_run = dry_run
        self._bot = Bot(token=self._token) if not dry_run else None

    # ── Core send ─────────────────────────────────────────────────────────────

    def send_message(self, text: str) -> None:
        """Send plain text. Errors are logged but never raised (non-blocking)."""
        if self._dry_run:
            print(f"[TELEGRAM DRY-RUN]\n{text}\n{'─' * 50}")
            return
        try:
            asyncio.run(self._bot.send_message(chat_id=self._chat_id, text=text))
        except TelegramError as exc:
            logger.error("Telegram send failed: %s", exc)
        except Exception as exc:
            logger.error("Unexpected Telegram error: %s", exc)

    # ── Formatted alerts ──────────────────────────────────────────────────────

    def send_breakout_alert(self, signal: dict) -> None:
        """
        Send a breakout alert and prompt the position sizing calculator.

        signal dict: {id, symbol, signal_type, candle_close, orb_level, move_pct, candle_time}
        """
        sym = signal["symbol"].replace("NSE:", "").replace("-EQ", "")
        close = signal["candle_close"]
        orb = signal["orb_level"]
        move = signal["move_pct"]
        ts = signal["candle_time"]

        if signal["signal_type"] == "BULLISH":
            msg = (
                f"🟢 BULLISH BREAKOUT — {sym}\n"
                f"Close:    ₹{close:.2f}  |  ORB High: ₹{orb:.2f}\n"
                f"Move:     +{move:.2f}% above ORB\n"
                f"Time:     {ts}"
            )
        else:
            msg = (
                f"🔴 BEARISH BREAKOUT — {sym}\n"
                f"Close:    ₹{close:.2f}  |  ORB Low:  ₹{orb:.2f}\n"
                f"Move:     -{move:.2f}% below ORB\n"
                f"Time:     {ts}"
            )

        self.send_message(msg)

        # Prompt position calculator (live mode only)
        if not self._dry_run and self._bot:
            try:
                asyncio.run(prompt_calculator(self._bot, self._chat_id, signal))
            except Exception as exc:
                logger.error("Calculator prompt failed: %s", exc)

        # Mark as alerted in the database
        if signal.get("id"):
            try:
                db.mark_signal_alerted(signal["id"])
            except Exception as exc:
                logger.error("Failed to mark signal %s alerted: %s", signal["id"], exc)

    def send_session_start(self, watchlist: list[str]) -> None:
        """Sent at 9:15 AM with today's tracked stocks count."""
        msg = (
            f"📊 ORB session started.\n"
            f"Tracking {len(watchlist)} stocks under ₹{config.MAX_STOCK_PRICE:.0f} today.\n"
            f"Watching: {', '.join(s.replace('NSE:', '').replace('-EQ', '') for s in watchlist[:10])}"
            + (f" … +{len(watchlist) - 10} more" if len(watchlist) > 10 else "")
        )
        self.send_message(msg)

    def send_orb_summary(self, passed_symbols: list[str], dropped_count: int) -> None:
        """Sent at 9:30 AM with the ORB filter result."""
        names = [s.replace("NSE:", "").replace("-EQ", "") for s in passed_symbols]
        msg = (
            f"✅ ORB filter done.\n"
            f"{len(passed_symbols)} stocks qualify | {dropped_count} dropped (range > {config.ORB_RANGE_THRESHOLD}%)\n"
        )
        if names:
            msg += "Qualifying: " + ", ".join(names)
        self.send_message(msg)

    def send_session_end(self, signal_count: int) -> None:
        """Sent at 3:15 PM with a day summary."""
        msg = (
            f"🔔 Session ended.\n"
            f"{signal_count} breakout signal{'s' if signal_count != 1 else ''} today."
        )
        self.send_message(msg)

    def send_test_message(self) -> None:
        """Send a test message to verify credentials."""
        now = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
        self.send_message(f"✅ ORB Bot — test message OK\n{now}")
        logger.info("Test Telegram message sent.")
