"""Central session orchestrator — wires all components and manages the trading day.

Scheduled jobs (all times IST, Mon–Fri, non-holiday only):
  08:45  — refresh the Fyers token when it is expired
  09:10  — auth + build watchlist + session-start Telegram message
  09:15  — start WebSocket tick stream
  every minute — lock the opening range when it ends, then flush breakout candles
  15:15  — stop WebSocket, send session-end summary, reset state

Opening-range length and breakout candle size come from the dashboard settings.
A pending recalculation command clears today's signals and sends them again.
"""
import threading
from datetime import datetime, timedelta

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from src.alerts.telegram_notifier import TelegramNotifier
from src.broker import auth, fyers_client
from src.broker.auth import OAuthInProgressError, is_oauth_in_progress, is_token_valid
from src.broker.websocket_handler import TickStreamManager
from src.strategy.breakout_detector import BreakoutDetector
from src.strategy.candle_builder import CandleBuilder
from src.strategy.intraday_agg import (
    bars_from_frame,
    closed_breakout_candles,
    market_open,
    opening_range_candle,
)
from src.strategy.orb_calculator import ORBCalculator
from src.strategy.runtime_settings import StrategySettings, load_settings
from src.strategy.watchlist import WatchlistManager
from src.utils import db
from src.utils.logger import get_logger
from src.utils.market_calendar import is_market_open

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
        self._orb_locked_on: str = ""
        self._job_lock = threading.RLock()
        self._settings_stamp: str = ""

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

    def _trading_day_only(self, job_fn):
        """Skip scheduled jobs on weekends and NSE holidays."""
        def wrapped():
            if not is_market_open():
                return
            job_fn()
        return wrapped

    def _ensure_token(self, *, allow_oauth: bool) -> bool:
        """Load today's Fyers token into self._access_token. Returns True on success."""
        if self._access_token and is_token_valid():
            return True
        try:
            send_fn = self._notifier.send_message if (allow_oauth and not self._dry_run) else None
            self._access_token = auth.get_access_token(send_fn=send_fn, allow_oauth=allow_oauth)
            return True
        except OAuthInProgressError:
            logger.warning("Fyers OAuth already in progress — skipping this job.")
            return False
        except (RuntimeError, TimeoutError) as exc:
            logger.error("No Fyers token available: %s", exc)
            return False
        except Exception:
            logger.exception("Token load failed.")
            return False

    def _job_08_45(self) -> None:
        """At 8:45 AM check token validity; trigger OAuth refresh if expired."""
        if is_token_valid():
            logger.info("08:45 token check: valid, no refresh needed.")
            return

        if is_oauth_in_progress():
            logger.info("08:45 token check: OAuth already running.")
            return

        logger.info("08:45 token check: expired — triggering OAuth redirect refresh.")
        send_fn = self._notifier.send_message if not self._dry_run else None
        try:
            self._access_token = auth.generate_access_token_via_redirect(send_fn=send_fn)
            if not self._dry_run:
                self._notifier.prompt_daily_capital()
        except TimeoutError:
            logger.error("08:45 token refresh timed out.")
        except OAuthInProgressError:
            logger.warning("08:45 token refresh skipped — OAuth already in progress.")
        except Exception:
            logger.exception("08:45 token refresh failed.")

    def _job_09_10(self) -> None:
        """Auth + build watchlist + Telegram session-start message."""
        logger.info("09:10 job: authenticating…")
        if is_oauth_in_progress():
            logger.warning("09:10 job skipped — waiting for OAuth login to finish.")
            return
        try:
            if not self._ensure_token(allow_oauth=True):
                return
            if not self._dry_run:
                self._notifier.prompt_daily_capital()
            client = fyers_client.get_client(self._access_token)
            manager = WatchlistManager(client)
            self._watchlist = manager.build_daily_watchlist()
            self._notifier.send_session_start(self._watchlist, load_settings())
            logger.info("09:10 job complete. Watchlist: %d symbols.", len(self._watchlist))
        except Exception:
            logger.exception("09:10 job failed.")

    def _job_09_15(self) -> None:
        """Start WebSocket tick stream."""
        if self._ws:
            self._ws.disconnect()
            self._ws = None

        try:
            if self._ensure_token(allow_oauth=False):
                client = fyers_client.get_client(self._access_token)
                refreshed = WatchlistManager(client).build_daily_watchlist()
                self._watchlist = refreshed
                logger.info("09:15 watchlist loaded from dashboard settings: %d symbols.", len(self._watchlist))
            else:
                logger.error("09:15 watchlist refresh skipped — no valid token.")
        except Exception:
            logger.exception("09:15 watchlist refresh failed.")

        logger.info("09:15 job: starting WebSocket stream for %d symbols.", len(self._watchlist))
        if not self._watchlist:
            logger.warning("Watchlist is empty — WebSocket not started.")
            return
        if not self._access_token:
            logger.warning("09:15 job skipped — no access token.")
            return
        try:
            self._ws = TickStreamManager(
                symbols=self._watchlist,
                on_tick_callback=self._on_tick,
            )
            self._ws.connect(self._access_token)
        except Exception:
            logger.exception("09:15 job failed (WebSocket start).")

    def _apply_candle_intervals(self, settings: StrategySettings) -> None:
        self._candle_builder.set_intervals(
            settings.orb_duration_minutes,
            settings.breakout_timeframe_minutes,
        )
        self._settings_stamp = settings.updated_at

    def _restart_stream(self, symbols: list[str]) -> None:
        """Reconnect the tick stream when the tracking list changes mid-session."""
        now = datetime.now(IST)
        if now < market_open(now):
            return
        if self._ws is not None and set(self._ws._symbols) == set(symbols):
            return
        if self._ws:
            self._ws.disconnect()
            self._ws = None
        if not symbols or not self._access_token:
            return
        self._ws = TickStreamManager(symbols=symbols, on_tick_callback=self._on_tick)
        self._ws.connect(self._access_token)

    def _history_bars(self, client, symbol: str, today: str) -> list[dict]:
        frame = fyers_client.get_historical_candles(client, symbol, "1", today, today)
        return bars_from_frame(frame)

    def _run_orb(self, *, clear_signals: bool, rescan: bool) -> str:
        """Lock today's opening range and, when requested, replay breakout candles.

        clear_signals=True is the dashboard recalculation: today's alerts are
        deleted first, then every qualifying breakout is sent once.
        """
        settings = load_settings()
        self._apply_candle_intervals(settings)
        now = datetime.now(IST)
        today = now.strftime("%Y-%m-%d")
        orb_end = market_open(now) + timedelta(minutes=settings.orb_duration_minutes)

        if not is_market_open(now):
            raise RuntimeError(f"{today} is not an NSE trading day.")
        if now < orb_end:
            raise RuntimeError(
                f"The opening range is still forming. It ends at {settings.orb_end_label} IST."
            )
        if not self._ensure_token(allow_oauth=False):
            raise RuntimeError("No valid Fyers token. Log in from the bot and try again.")

        client = fyers_client.get_client(self._access_token)
        self._watchlist = WatchlistManager(client).build_daily_watchlist()
        if not self._watchlist:
            self._orb_locked_on = today
            self._breakout = None
            self._orb_levels = {}
            self._notifier.send_message(
                "Tracking list is empty. Add symbols in the dashboard, then recalculate."
            )
            return "Tracking list is empty."

        self._restart_stream(self._watchlist)
        # Live closes must not fire while today's candles are replayed.
        self._breakout = None
        self._candle_builder.flush_completed(orb_end)

        if clear_signals:
            db.delete_signals(today)
        db.delete_orb_levels(today)

        passed: dict[str, dict] = {}
        replay: dict[str, list[dict]] = {}
        missing = 0

        for symbol in self._watchlist:
            bars = self._history_bars(client, symbol, today)
            candle = opening_range_candle(bars, now, settings.orb_duration_minutes)
            if candle is None and not clear_signals:
                candle = self._candle_builder.get_first_candle(symbol)
                if candle is not None:
                    logger.warning("%s — exchange opening range incomplete; using tick candle.", symbol)
            if candle is None:
                missing += 1
                logger.warning("%s — no opening-range candle available.", symbol)
                continue

            result = self._orb_calc.calculate_orb(symbol, candle, settings=settings)
            if not result:
                continue
            passed[symbol] = {"orb_high": result["orb_high"], "orb_low": result["orb_low"]}
            if rescan:
                replay[symbol] = closed_breakout_candles(
                    bars,
                    now,
                    settings.orb_duration_minutes,
                    settings.breakout_timeframe_minutes,
                    now,
                )

        if not passed and missing == len(self._watchlist):
            logger.warning(
                "Opening range not ready for any of %d symbols. Will retry next minute.",
                missing,
            )
            return "Opening range candles are not ready yet. The bot will retry."

        self._candle_builder.skip_partial_bucket(now)
        dropped = len(self._watchlist) - len(passed)
        self._notifier.send_orb_summary(
            list(passed.keys()), dropped, settings=settings, recalc=clear_signals,
        )

        def _on_signal(signal: dict) -> None:
            if clear_signals:
                signal["recalc"] = True
            self._on_breakout(signal)

        detector = BreakoutDetector(
            orb_levels=passed,
            on_breakout_callback=_on_signal,
            breakout_minutes=settings.breakout_timeframe_minutes,
            entry_cutoff=settings.entry_cutoff,
            entry_mode=settings.entry_mode,
            retest_within_minutes=settings.retest_within_minutes,
        )
        sent = 0
        if rescan:
            for symbol, candles in replay.items():
                for candle in candles:
                    before = len(db.get_signals(today))
                    detector.check_candle(symbol, candle)
                    if len(db.get_signals(today)) > before:
                        sent += 1

        self._orb_levels = passed
        self._breakout = detector
        self._orb_locked_on = today
        logger.info(
            "ORB active for %d symbols. Breakout alerts sent this run: %d. Missing candles: %d.",
            len(passed), sent, missing,
        )
        return (
            f"{len(passed)} symbols qualify, {dropped} dropped, "
            f"{sent} breakout alert{'s' if sent != 1 else ''} sent, {missing} missing candles."
        )

    def _job_clock(self) -> None:
        """Lock the opening range once it ends, then flush each breakout candle."""
        if not self._job_lock.acquire(blocking=False):
            return
        try:
            if not is_market_open():
                return
            settings = load_settings()
            self._apply_candle_intervals(settings)
            now = datetime.now(IST)
            stamp = now.replace(second=0, microsecond=0)
            today = stamp.strftime("%Y-%m-%d")
            close = stamp.replace(hour=15, minute=15, second=0, microsecond=0)
            if stamp > close:
                return
            orb_end = market_open(now) + timedelta(minutes=settings.orb_duration_minutes)
            if stamp < orb_end:
                return
            if self._orb_locked_on != today:
                try:
                    self._run_orb(clear_signals=False, rescan=True)
                except Exception:
                    logger.exception("ORB lock failed.")
                return
            elapsed = int((stamp - orb_end).total_seconds())
            step = settings.breakout_timeframe_minutes * 60
            if elapsed > 0 and elapsed % step == 0:
                self._candle_builder.flush_completed(stamp)
        finally:
            self._job_lock.release()

    def _poll_dashboard(self) -> None:
        """Run recalc or Fyers login jobs queued from the dashboard."""
        try:
            settings = load_settings()
            self._apply_candle_intervals(settings)
        except Exception:
            logger.exception("Could not read dashboard settings.")
            return

        command = None
        if self._job_lock.acquire(blocking=False):
            try:
                command = db.claim_next_command()
            finally:
                self._job_lock.release()

        if command is None:
            return

        threading.Thread(
            target=self._handle_dashboard_command,
            args=(int(command["id"]), command["action"]),
            daemon=True,
            name=f"dashboard-{command['action']}-{command['id']}",
        ).start()

    def _finish_dashboard_command(self, command_id: int, status: str, detail: str) -> None:
        db.finish_command(
            command_id,
            status,
            detail,
            datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
        )

    def _handle_dashboard_command(self, command_id: int, action: str) -> None:
        try:
            if action == "recalc":
                if not self._job_lock.acquire(blocking=True, timeout=120):
                    raise RuntimeError("Bot is busy; try again in a minute.")
                try:
                    detail = self._run_orb(clear_signals=True, rescan=True)
                finally:
                    self._job_lock.release()
                self._finish_dashboard_command(command_id, "done", detail)
                logger.info("Dashboard recalculation finished: %s", detail)
            elif action == "fyers_auth":
                self._run_dashboard_fyers_auth(command_id)
            elif action == "sheet_batch":
                from src.backtest.sheet_job import run_sheet_batch_command

                detail = run_sheet_batch_command(command_id)
                self._finish_dashboard_command(command_id, "done", detail)
                if not self._dry_run:
                    self._notifier.send_sheet_batch_result(command_id, True, detail)
                logger.info("Combination sheet job finished: %s", detail)
            else:
                self._finish_dashboard_command(command_id, "error", f"Unknown action: {action}")
        except Exception as exc:
            logger.exception("Dashboard command %s failed", action)
            detail = str(exc)
            self._finish_dashboard_command(command_id, "error", detail)
            if action == "sheet_batch" and not self._dry_run:
                self._notifier.send_sheet_batch_result(command_id, False, detail)

    def _run_dashboard_fyers_auth(self, command_id: int) -> None:
        """Send a fresh Fyers login link on Telegram (up to ~10 min wait)."""
        if is_oauth_in_progress():
            raise OAuthInProgressError(
                "Fyers login is already in progress — open the link in Telegram."
            )
        if not self._dry_run:
            self._notifier.send_message(
                "🔑 Dashboard requested a new Fyers login. The link below is valid for ~10 minutes."
            )
        send_fn = self._notifier.send_message if not self._dry_run else None
        token = auth.generate_access_token_via_redirect(send_fn=send_fn, timeout_secs=600)
        self._access_token = token
        self._finish_dashboard_command(
            command_id,
            "done",
            "Login link sent to Telegram. Token saved for today.",
        )
        logger.info("Dashboard Fyers auth completed.")

    def _job_15_15(self) -> None:
        """Session end: stop stream, send summary, reset state."""
        logger.info("15:15 job: ending session.")
        with self._job_lock:
            self._end_session()

    def _end_session(self) -> None:
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
            self._orb_locked_on = ""
            logger.info("Session ended. %d signals today.", len(signals))
        except Exception:
            logger.exception("15:15 job failed.")

    # ── Scheduler lifecycle ────────────────────────────────────────────────────

    def _schedule_jobs(self) -> None:
        # Weekday-only cron trigger helper
        def cron(hour: int, minute: int, second: int = 0):
            return CronTrigger(
                day_of_week="mon-fri", hour=hour, minute=minute, second=second, timezone=IST
            )

        self._scheduler.add_job(self._trading_day_only(self._job_08_45), cron(8, 45), id="job_08_45", replace_existing=True)
        self._scheduler.add_job(self._trading_day_only(self._job_09_10), cron(9, 10), id="job_09_10", replace_existing=True)
        self._scheduler.add_job(self._trading_day_only(self._job_09_15), cron(9, 15), id="job_09_15", replace_existing=True)
        self._scheduler.add_job(
            self._trading_day_only(self._job_15_15), cron(15, 15, 20), id="job_15_15", replace_existing=True,
        )
        # Second 5 of every minute: lock the opening range, then close breakout candles.
        self._scheduler.add_job(
            self._job_clock,
            CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*", second=5, timezone=IST),
            id="job_clock",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self._scheduler.add_job(
            self._poll_dashboard,
            "interval",
            seconds=5,
            id="dashboard_poll",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    def start(self) -> None:
        """Start the scheduler. Blocks until stop() is called."""
        try:
            self._apply_candle_intervals(load_settings())
        except Exception:
            logger.exception("Could not load dashboard settings at startup. Using 15/15 until the next poll.")
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
