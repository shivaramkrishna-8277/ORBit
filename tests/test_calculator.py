"""Standalone calculator test — sends a fake breakout alert to Telegram
and starts the conversation handler so you can test the full flow.

Usage (from project root):
    PYTHONUTF8=1 python tests/test_calculator.py

What to do in Telegram after running:
  1. Tap "📊 Calculate"  (or "♻️ Repeat" if you've run before)
  2. Tap ₹5L  for capital   (or type 500000)
  3. Tap ₹2.5L for margin   (or type 250000)
  4. Tap 1%   for risk       (or type 1)
  5. Tap the  1%=₹1.88 option for SL  (or type 3.50 for a custom value)

Expected result (using typed values):
  Buying Power: ₹7,50,000
  Risk Amount:  ₹5,000  (1% of capital)
  Quantity:     1,428 shares
  Total Cost:   ₹2,67,750
  Max Loss:     ₹4,998
"""
import asyncio
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.alerts.position_calculator import build_calculator_handler, prompt_calculator
from telegram.ext import Application

# ── Windows-safe Ctrl+C flag ─────────────────────────────────────────
_stop = False

def _on_sigint(sig, frame):
    global _stop
    _stop = True

signal.signal(signal.SIGINT, _on_sigint)


async def main_async() -> None:
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(build_calculator_handler())

    fake_signal = {
        "symbol":       "NSE:TATASTEEL-EQ",
        "signal_type":  "BULLISH",
        "candle_close": 187.50,
        "orb_level":    186.20,
        "move_pct":     0.70,
        "candle_time":  "10:15",
    }

    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        print("Polling started.")

        # Use the app's own bot — avoids a second getUpdates session
        await app.bot.send_message(
            chat_id=config.TELEGRAM_CHAT_ID,
            text=(
                "🟢 [TEST] BULLISH BREAKOUT — TATASTEEL\n"
                "Close:  ₹187.50  |  ORB High: ₹186.20\n"
                "Move:   +0.70% above ORB\n"
                "Time:   10:15 IST"
            ),
        )
        await prompt_calculator(app.bot, config.TELEGRAM_CHAT_ID, fake_signal)

        print(
            "\nTest alert sent. Open Telegram and complete the calculator.\n"
            "Press Ctrl+C when done."
        )

        while not _stop:
            await asyncio.sleep(0.5)

        await app.updater.stop()
        await app.stop()

    print("\nTest ended.")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
