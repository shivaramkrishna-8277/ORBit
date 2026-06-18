"""ORB Bot — main entry point.

Usage:
    python main.py                  # Normal run
    python main.py --dry-run        # Alerts printed to console, not sent to Telegram
    python main.py --test-telegram  # Send a test Telegram message and exit
    python main.py --test-watchlist # Fetch quotes and print price-filter results, then exit
    python main.py --test-orb       # Fetch today's 9:15 ORB candle and print range, then exit
"""
import argparse
import sys
import time

from src.utils.logger import get_logger

logger = get_logger("main")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Nifty 50 ORB Alert Bot — tracks Opening Range Breakouts on Nifty 50 stocks."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the full pipeline but print alerts to console instead of sending Telegram messages.",
    )
    parser.add_argument(
        "--test-telegram",
        action="store_true",
        help="Send a test Telegram message to verify credentials, then exit.",
    )
    parser.add_argument(
        "--test-watchlist",
        action="store_true",
        help="Fetch live Fyers quotes and print the ₹800 price filter results, then exit.",
    )
    parser.add_argument(
        "--test-orb",
        nargs="*",
        metavar="SYMBOL",
        help="Fetch today's exchange 9:15 ORB candle (default: HDFCBANK). Optional Fyers symbols.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    session = None

    try:
        # 1. Validate all env vars at startup
        import src.config as cfg

        logger.info(
            "ORB Bot starting — %d symbols, price ≤ ₹%.0f, ORB threshold %.1f%%",
            len(cfg.NIFTY50_SYMBOLS), cfg.MAX_STOCK_PRICE, cfg.ORB_RANGE_THRESHOLD,
        )

        # 2. Initialise the database (idempotent — safe to run every startup)
        from src.utils import db
        db.init_db()

        # 3. Handle --test-telegram flag
        if args.test_telegram:
            from src.alerts.telegram_notifier import TelegramNotifier
            TelegramNotifier().send_test_message()
            logger.info("Test message sent. Exiting.")
            sys.exit(0)

        if args.test_watchlist:
            from src.cli.test_watchlist import run_test_watchlist
            sys.exit(run_test_watchlist())

        if args.test_orb is not None:
            from src.cli.test_orb import run_test_orb
            symbols = args.test_orb if args.test_orb else None
            sys.exit(run_test_orb(symbols))

        # 4. Keep running on non-trading days (Docker restart loop fix — no exit, no spam)
        from src.utils.market_calendar import is_market_open, next_trading_day
        if not is_market_open():
            from datetime import datetime
            import pytz
            today = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")
            next_day = next_trading_day()
            logger.info(
                "Market closed today (%s). Bot staying idle until next session (%s).",
                today, next_day,
            )

        if args.dry_run:
            logger.info("DRY RUN mode — Telegram alerts will print to console only.")

        # 5. Start Telegram Application polling so the ConversationHandler can receive
        #    button taps and typed replies for the position sizing calculator.
        #    Runs in a daemon thread alongside the APScheduler-based session manager.
        #    Skipped in dry-run mode (no real bot, no keyboard needed).
        if not args.dry_run:
            import asyncio
            import threading
            from telegram.ext import Application
            from telegram.request import HTTPXRequest
            from src.alerts.position_calculator import build_position_handlers
            _tg_request = HTTPXRequest(
                connect_timeout=30.0,
                read_timeout=30.0,
                write_timeout=30.0,
                pool_timeout=30.0,
            )
            _tg_app = (
                Application.builder()
                .token(cfg.TELEGRAM_BOT_TOKEN)
                .request(_tg_request)
                .build()
            )
            for handler in build_position_handlers():
                _tg_app.add_handler(handler)

            def _run_polling(app):
                """Run Telegram polling in its own event loop without installing
                signal handlers (avoids set_wakeup_fd error in non-main thread)."""
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                async def _inner():
                    async with app:
                        await app.start()
                        await app.updater.start_polling(drop_pending_updates=True)
                        await asyncio.Event().wait()  # block until daemon thread is killed
                loop.run_until_complete(_inner())

            threading.Thread(
                target=_run_polling,
                args=(_tg_app,),
                daemon=True,
                name="telegram-polling",
            ).start()
            logger.info("Telegram polling thread started (calculator ready).")

        # 6. Start the session manager (APScheduler keeps running until KeyboardInterrupt)
        from src.session_manager import SessionManager
        session = SessionManager(dry_run=args.dry_run)
        session.start()

        logger.info("Bot running. Press Ctrl+C to stop.")
        while True:
            time.sleep(1)

    except ValueError as exc:
        logger.error("Configuration error: %s", exc)
        logger.error("Copy .env.example to .env and fill in all required values.")
        _notify_startup_failure(str(exc), args)
        sys.exit(1)

    except KeyboardInterrupt:
        logger.info("Bot stopped by user (Ctrl+C).")

    except Exception as exc:
        logger.exception("Unexpected startup error: %s", exc)
        _notify_startup_failure(str(exc), args)
        sys.exit(1)

    finally:
        if session:
            session.stop()
        logger.info("Bot shut down cleanly.")


def _notify_startup_failure(error: str, args: argparse.Namespace) -> None:
    """Best-effort Telegram alert on startup failure."""
    try:
        from src.alerts.telegram_notifier import TelegramNotifier
        TelegramNotifier(dry_run=getattr(args, "dry_run", False)).send_message(
            f"⚠️ ORB Bot failed to start:\n{error[:300]}"
        )
    except Exception:
        pass  # Don't raise inside an error handler


if __name__ == "__main__":
    main()
