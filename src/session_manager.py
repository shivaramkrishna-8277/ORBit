"""Central session orchestrator — wires all components and manages the trading day.

Scheduled jobs (all times IST, Mon–Fri, non-holiday only):
  09:10  — auth + build watchlist + session-start Telegram message
  09:15  — start WebSocket tick stream
  09:30  — flush first candles, run ORB filter, send ORB summary
  09:45–15:15 (every 15 min) — flush candles, run breakout checks
  15:15  — stop WebSocket, send session-end summary, reset state

Use APScheduler BackgroundScheduler with Asia/Kolkata timezone.
"""
from datetime import datetime

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.alerts.telegram_notifier import TelegramNotifier
from src.broker import auth, fyers_client
from src.broker.websocket_handler import TickStreamManager
from src.strategy.breakout_detector import BreakoutDetector
from src.strategy.candle_builder import CandleBuilder
from src.strategy.orb_calculator import ORBCalculator
from src.strategy.watchlist import WatchlistManager
from src.utils import db
from src.utils.logger import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")


class SessionManager:
    def __init__(self, dry_run: bool = False):
        self._dry_run = dry_run
        self._scheduler = BackgroundScheduler(timezone=IST)

        # Component instances (populated during the session)
        self._notifier = TelegramNotifier(dry_run=dry_run)
        self._candle_builder = CandleBuilder(on_candle_close=self._on_candle_close)
        self._orb_calc = ORBCalculator()
        self._breakout: BreakoutDetector | None = None
        self._ws: TickStreamManager | None = None
        self._watchlist: list[str] = []
        self._orb_levels: dict[str, dict] = {}
        self._access_token: str = ""

    # ── Tick & candle routing ──────────────────────────────────────────────────

    def _on_tick(self, symbol: str, ltp: float, timestamp: datetime) -> None:
        self._candle_builder.on_tick(symbol, ltp, timestamp)

    def _on_candle_close(self, symbol: str, candle: dict) -> None:
        """Route completed candles to the BreakoutDetector (active after 9:30 AM)."""
        if self._breakout:
            self._breakout.check_candle(symbol, candle)

    def _on_breakout(self, signal: dict) -> None:
        self._notifier.send_breakout_alert(signal)

    # ── Scheduled jobs ─────────────────────────────────────────────────────────

    def _job_08_45(self) -> None:
        """At 8:45 AM check token validity; trigger OAuth refresh if expired."""
        from src.broker.auth import is_token_valid, generate_access_token_via_redirect

        if is_token_valid():
            logger.info("08:45 token check: valid, no refresh needed.")
            return

        logger.info("08:45 token check: expired — triggering OAuth redirect refresh.")
        send_fn = self._notifier.send_message if not self._dry_run else None
        try:
            generate_access_token_via_redirect(send_fn=send_fn)
        except TimeoutError:
            logger.error("08:45 token refresh timed out.")
        except Exception:
            logger.exception("08:45 token refresh failed.")

    def _job_09_10(self) -> None:
        """Auth + build watchlist + Telegram session-start message."""
        logger.info("09:10 job: authenticating…")
        try:
            self._access_token = auth.get_access_token()
            client = fyers_client.get_client(self._access_token)
            manager = WatchlistManager(client)
            self._watchlist = manager.build_daily_watchlist()
            self._notifier.send_session_start(self._watchlist)
            logger.info("09:10 job complete. Watchlist: %d symbols.", len(self._watchlist))
        except Exception:
            logger.exception("09:10 job failed.")

    def _job_09_15(self) -> None:
        """Start WebSocket tick stream."""
        logger.info("09:15 job: starting WebSocket stream for %d symbols.", len(self._watchlist))
        if not self._watchlist:
            logger.warning("Watchlist is empty — WebSocket not started.")
            return
        try:
            self._ws = TickStreamManager(
                symbols=self._watchlist,
                on_tick_callback=self._on_tick,
            )
            self._ws.connect(self._access_token)
        except Exception:
            logger.exception("09:15 job failed (WebSocket start).")

    def _job_09_30(self) -> None:
        """Lock ORB levels and activate breakout detection."""
        logger.info("09:30 job: locking ORB levels.")
        try:
            self._orb_levels = self._orb_calc.process_all_symbols(
                self._candle_builder, self._watchlist
            )
            self._breakout = BreakoutDetector(
                orb_levels=self._orb_levels,
                on_breakout_callback=self._on_breakout,
            )
            dropped = len(self._watchlist) - len(self._orb_levels)
            self._notifier.send_orb_summary(list(self._orb_levels.keys()), dropped)
            logger.info("09:30 job complete. ORB active for %d symbols.", len(self._orb_levels))
        except Exception:
            logger.exception("09:30 job failed.")

    def _job_candle_tick(self) -> None:
        """Flush current in-progress candles (runs every 15 min, 09:45–15:15)."""
        now_str = datetime.now(IST).strftime("%H:%M")
        logger.debug("Candle flush at %s", now_str)
        try:
            self._candle_builder.flush_all()
        except Exception:
            logger.exception("Candle flush failed at %s", now_str)

    def _job_15_15(self) -> None:
        """Session end: stop stream, send summary, reset state."""
        logger.info("15:15 job: ending session.")
        try:
            if self._ws:
                self._ws.disconnect()
                self._ws = None

            today = datetime.now(IST).strftime("%Y-%m-%d")
            signals = db.get_signals(today)
            self._notifier.send_session_end(len(signals))

            # Reset in-memory state for the next day
            self._candle_builder.reset()
            self._breakout = None
            self._orb_levels = {}
            self._watchlist = []
            self._access_token = ""
            logger.info("Session ended. %d signals today.", len(signals))
        except Exception:
            logger.exception("15:15 job failed.")

    # ── Scheduler lifecycle ────────────────────────────────────────────────────

    def _schedule_jobs(self) -> None:
        # Weekday-only cron trigger helper
        def cron(hour: int, minute: int):
            return CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=IST)

        self._scheduler.add_job(self._job_08_45,    cron(8, 45),  id="job_08_45",   replace_existing=True)
        self._scheduler.add_job(self._job_09_10,    cron(9, 10),  id="job_09_10",   replace_existing=True)
        self._scheduler.add_job(self._job_09_15,    cron(9, 15),  id="job_09_15",   replace_existing=True)
        self._scheduler.add_job(self._job_09_30,    cron(9, 30),  id="job_09_30",   replace_existing=True)
        self._scheduler.add_job(self._job_15_15,    cron(15, 15), id="job_15_15",   replace_existing=True)

        # Candle flush every 15 min from 09:45 to 15:15
        for minute_offset in range(45, 60, 15):  # 9:45
            self._scheduler.add_job(self._job_candle_tick, cron(9, minute_offset),
                                    id=f"flush_09_{minute_offset:02d}", replace_existing=True)
        for hour in range(10, 16):
            for minute in range(0, 60, 15):
                if hour == 15 and minute > 15:
                    break
                self._scheduler.add_job(self._job_candle_tick, cron(hour, minute),
                                        id=f"flush_{hour:02d}_{minute:02d}", replace_existing=True)

    def start(self) -> None:
        """Start the scheduler. Blocks until stop() is called."""
        self._schedule_jobs()
        self._scheduler.start()
        logger.info("SessionManager started. Waiting for scheduled jobs…")

    def stop(self) -> None:
        """Gracefully stop the scheduler and disconnect WebSocket."""
        logger.info("Stopping SessionManager…")
        if self._ws:
            self._ws.disconnect()
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        logger.info("SessionManager stopped.")
