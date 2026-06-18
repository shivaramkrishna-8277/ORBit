"""Telegram notification module — sends ORB alerts and session summaries."""
import asyncio
import time
from datetime import datetime

import pytz
from telegram import Bot
from telegram.error import NetworkError, TelegramError
from telegram.request import HTTPXRequest

from src import config
from src.utils import db
from src.utils.logger import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")

_TELEGRAM_RETRIES = 3
_TELEGRAM_RETRY_DELAY = 2  # seconds


def _build_bot() -> Bot:
    """Bot with generous timeouts for VPS networks."""
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )
    return Bot(token=config.TELEGRAM_BOT_TOKEN, request=request)


class TelegramNotifier:
    def __init__(self, dry_run: bool = False):
        self._token = config.TELEGRAM_BOT_TOKEN
        self._chat_id = config.TELEGRAM_CHAT_ID
        self._dry_run = dry_run

    def send_message(self, text: str) -> bool:
        """Send plain text. Returns True on success. Never raises."""
        if self._dry_run:
            print(f"[TELEGRAM DRY-RUN]\n{text}\n{'─' * 50}")
            return True

        async def _send() -> None:
            async with _build_bot() as bot:
                await bot.send_message(chat_id=self._chat_id, text=text)

        for attempt in range(1, _TELEGRAM_RETRIES + 1):
            try:
                asyncio.run(_send())
                return True
            except (TelegramError, NetworkError, OSError) as exc:
                logger.error(
                    "Telegram send failed (attempt %d/%d): %s",
                    attempt, _TELEGRAM_RETRIES, exc,
                )
                if attempt < _TELEGRAM_RETRIES:
                    time.sleep(_TELEGRAM_RETRY_DELAY)
            except Exception as exc:
                logger.error("Unexpected Telegram error: %s", exc)
                return False
        return False

    def send_breakout_alert(self, signal: dict) -> None:
        from src.alerts.position_calculator import prompt_trade_setup

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

        if not self.send_message(msg):
            logger.error("Breakout alert NOT delivered for %s", sym)
            return

        if not self._dry_run:
            try:
                async def _prompt():
                    async with _build_bot() as bot:
                        await prompt_trade_setup(bot, int(self._chat_id), signal)

                asyncio.run(_prompt())
            except Exception as exc:
                logger.error("Trade sizing prompt failed: %s", exc)

        if signal.get("id"):
            try:
                db.mark_signal_alerted(signal["id"])
            except Exception as exc:
                logger.error("Failed to mark signal %s alerted: %s", signal["id"], exc)

    def send_session_start(self, watchlist: list[str]) -> None:
        msg = (
            f"📊 ORB session started.\n"
            f"Tracking {len(watchlist)} stocks under ₹{config.MAX_STOCK_PRICE:.0f} today.\n"
            f"Watching: {', '.join(s.replace('NSE:', '').replace('-EQ', '') for s in watchlist[:10])}"
            + (f" … +{len(watchlist) - 10} more" if len(watchlist) > 10 else "")
        )
        self.send_message(msg)

    def send_orb_summary(self, passed_symbols: list[str], dropped_count: int) -> None:
        names = [s.replace("NSE:", "").replace("-EQ", "") for s in passed_symbols]
        msg = (
            f"✅ ORB filter done.\n"
            f"{len(passed_symbols)} stocks qualify | {dropped_count} dropped (range > {config.ORB_RANGE_THRESHOLD}%)\n"
        )
        if names:
            msg += "Qualifying: " + ", ".join(names)
        self.send_message(msg)

    def send_session_end(self, signal_count: int) -> None:
        msg = (
            f"🔔 Session ended.\n"
            f"{signal_count} breakout signal{'s' if signal_count != 1 else ''} today."
        )
        self.send_message(msg)

    def send_test_message(self) -> None:
        now = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
        self.send_message(f"✅ ORB Bot — test message OK\n{now}")
        logger.info("Test Telegram message sent.")

    def prompt_daily_capital(self) -> None:
        if self._dry_run:
            return
        try:
            from src.alerts.position_calculator import prompt_daily_capital

            async def _prompt():
                async with _build_bot() as bot:
                    await prompt_daily_capital(bot, int(self._chat_id))

            asyncio.run(_prompt())
        except Exception as exc:
            logger.error("Daily capital prompt failed: %s", exc)
