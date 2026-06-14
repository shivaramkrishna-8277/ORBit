"""Position sizing — daily capital setup + entry/SL per breakout signal."""
from __future__ import annotations

import logging
from datetime import datetime

import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src import config
from src.utils import db
from src.utils.position_sizing import compute_position, margin_from_capital

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

_CAP_PRESETS = [100_000, 200_000, 500_000, 1_000_000]

# chat_id → {step, signal?, entry?, ...}
_trade_state: dict[int, dict] = {}
_daily_capital_pending: set[int] = set()


# ── Formatting ───────────────────────────────────────────────────────

def _fmt_inr(n: float) -> str:
    if n == 0:
        return "₹0"
    if n >= 100_000:
        s = f"{n / 100_000:.2f}".rstrip("0").rstrip(".")
        return f"₹{s}L"
    if n >= 1_000:
        s = f"{n / 1_000:.2f}".rstrip("0").rstrip(".")
        return f"₹{s}K"
    return f"₹{n:g}"


def _fmt_full(n: float) -> str:
    return f"₹{n:,.2f}"


def _short_symbol(symbol: str) -> str:
    return symbol.replace("NSE:", "").replace("-EQ", "")


def _today() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def _margin_from_capital(capital: float) -> tuple[float, float]:
    return margin_from_capital(capital)


def _compute_position(
    capital: float,
    entry: float,
    stop_loss: float,
    risk_pct: float | None = None,
) -> dict:
    return compute_position(capital, entry, stop_loss, risk_pct)


def _format_result(symbol: str, direction: str, entry: float, stop_loss: float, pos: dict) -> str:
    emoji = {"BULLISH": "🟢", "BEARISH": "🔴"}.get(direction, "")
    lines = [
        "━━━━━━━━━━━━━━━━━━━━",
        "📊 *POSITION SIZING*",
        f"{emoji} *{_short_symbol(symbol)}*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"Entry:            {_fmt_full(entry)}",
        f"Stop Loss:        {_fmt_full(stop_loss)}",
        f"Risk / Share:     {_fmt_full(pos['risk_per_share'])}",
        "",
        f"Capital:          {_fmt_full(pos['capital'])}",
        f"Margin ({config.MARGIN_MULTIPLIER:.0f}×):     {_fmt_full(pos['margin'])}",
        f"Buying Power:     {_fmt_full(pos['buying_power'])}",
        "",
        f"Risk on Capital:  {pos['risk_pct']}% → {_fmt_full(pos['risk_amount'])}",
        f"*Quantity:        {pos['quantity']:,} shares*",
        f"Total Cost:       {_fmt_full(pos['total_cost'])}",
        f"Max Loss:         {_fmt_full(pos['max_loss'])}",
        "━━━━━━━━━━━━━━━━━━━━",
        "⚠️ Manual trade — no order placed",
    ]
    return "\n".join(lines)


# ── Daily capital (once per day) ─────────────────────────────────────

async def prompt_daily_capital(bot, chat_id: int) -> None:
    """Ask for today's capital after token refresh / session start."""
    if db.get_daily_capital(_today()) is not None:
        return

    _daily_capital_pending.add(chat_id)
    rows = [
        [InlineKeyboardButton(_fmt_inr(v), callback_data=f"dcap_{v}") for v in _CAP_PRESETS]
    ]
    await bot.send_message(
        chat_id=chat_id,
        text=(
            "💰 *Daily setup* — enter total capital available today.\n"
            f"_Margin auto-applied: {config.MARGIN_MULTIPLIER:.0f}× buying power "
            f"({config.MARGIN_MULTIPLIER - 1:.0f}× margin on capital)._"
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _save_daily_capital(context: ContextTypes.DEFAULT_TYPE, chat_id: int, capital: float) -> None:
    _daily_capital_pending.discard(chat_id)
    margin, buying_power = _margin_from_capital(capital)
    db.save_daily_capital(_today(), capital, margin, buying_power, config.DEFAULT_RISK_PCT)
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "✅ *Daily capital set*\n"
            f"Capital:      {_fmt_full(capital)}\n"
            f"Margin:       {_fmt_full(margin)} ({config.MARGIN_MULTIPLIER:.0f}×)\n"
            f"Buying Power: {_fmt_full(buying_power)}\n"
            f"Risk / trade: {config.DEFAULT_RISK_PCT}% of capital"
        ),
        parse_mode="Markdown",
    )


async def _on_daily_capital_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    if chat_id not in _daily_capital_pending:
        return
    capital = float(query.data[5:])
    await query.edit_message_text(f"Capital: *{_fmt_inr(capital)}* ✓", parse_mode="Markdown")
    await _save_daily_capital(context, chat_id, capital)


async def _on_daily_capital_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    try:
        capital = float(update.message.text.replace(",", "").strip())
        if capital <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a positive number (e.g. 500000):")
        return

    await _save_daily_capital(context, chat_id, capital)


# ── Per-breakout entry + stop loss ────────────────────────────────────

async def prompt_trade_setup(bot, chat_id: int, signal: dict) -> None:
    """After a breakout alert, ask entry then stop loss."""
    settings = db.get_daily_capital(_today())
    if settings is None:
        _daily_capital_pending.add(chat_id)
        await bot.send_message(
            chat_id=chat_id,
            text="⚠️ Set today's capital first before sizing trades.",
        )
        await prompt_daily_capital(bot, chat_id)
        return

    sym = _short_symbol(signal.get("symbol", ""))
    close = float(signal.get("candle_close", 0))
    _trade_state[chat_id] = {"step": "entry", "signal": signal}

    rows = []
    if close > 0:
        rows.append([InlineKeyboardButton(f"Use close {_fmt_full(close)}", callback_data="tentry_close")])

    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"📐 *{sym}* — 1/2 · *Entry price*\n"
            f"_Breakout close: {_fmt_full(close)}_ — tap or type:"
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows) if rows else None,
    )


async def _ask_stop_loss(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    state = _trade_state.get(chat_id, {})
    signal = state.get("signal", {})
    sym = _short_symbol(signal.get("symbol", ""))
    entry = state.get("entry", 0)
    direction = signal.get("signal_type", "")
    hint = "below entry" if direction == "BULLISH" else "above entry"

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"📐 *{sym}* — 2/2 · *Stop loss*\n"
            f"Entry: {_fmt_full(entry)} — enter SL ({hint}):"
        ),
        parse_mode="Markdown",
    )


async def _finish_trade(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    state = _trade_state.pop(chat_id, {})
    signal = state.get("signal", {})
    entry = state.get("entry")
    stop_loss = state.get("stop_loss")
    settings = db.get_daily_capital(_today())

    if not settings or entry is None or stop_loss is None:
        return

    try:
        pos = _compute_position(
            settings["capital"], entry, stop_loss, settings.get("risk_pct")
        )
    except ValueError as exc:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ {exc}")
        return

    text = _format_result(
        signal.get("symbol", ""),
        signal.get("signal_type", ""),
        entry,
        stop_loss,
        pos,
    )
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")


async def _on_trade_entry_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    state = _trade_state.get(chat_id)
    if not state or state.get("step") != "entry":
        return

    close = float(state["signal"].get("candle_close", 0))
    state["entry"] = close
    state["step"] = "stop_loss"
    await query.edit_message_text(f"Entry: *{_fmt_full(close)}* ✓", parse_mode="Markdown")
    await _ask_stop_loss(context, chat_id)


async def _on_trade_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    state = _trade_state.get(chat_id)
    if not state:
        return

    try:
        value = float(update.message.text.replace(",", "").strip())
        if value <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a valid price (e.g. 741.50):")
        return

    step = state.get("step")
    if step == "entry":
        state["entry"] = value
        state["step"] = "stop_loss"
        await _ask_stop_loss(context, chat_id)
    elif step == "stop_loss":
        entry = state.get("entry", 0)
        direction = state.get("signal", {}).get("signal_type", "")
        if direction == "BULLISH" and value >= entry:
            await update.message.reply_text("❌ Stop loss must be *below* entry for longs.", parse_mode="Markdown")
            return
        if direction == "BEARISH" and value <= entry:
            await update.message.reply_text("❌ Stop loss must be *above* entry for shorts.", parse_mode="Markdown")
            return
        state["stop_loss"] = value
        await _finish_trade(context, chat_id)


# ── Handler registration ──────────────────────────────────────────────

async def _on_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if chat_id in _daily_capital_pending:
        await _on_daily_capital_text(update, context)
        return
    if chat_id in _trade_state:
        await _on_trade_text(update, context)


def build_position_handlers() -> list:
    """Return Telegram handlers for daily capital + per-trade sizing."""
    return [
        CallbackQueryHandler(_on_daily_capital_button, pattern=r"^dcap_"),
        CallbackQueryHandler(_on_trade_entry_button, pattern=r"^tentry_close$"),
        MessageHandler(filters.TEXT & ~filters.COMMAND, _on_text_input),
    ]
