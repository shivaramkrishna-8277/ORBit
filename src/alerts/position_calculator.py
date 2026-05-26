"""Position sizing calculator — Telegram ConversationHandler."""
from __future__ import annotations

import logging
import math
import warnings

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

try:
    from telegram.warnings import PTBUserWarning as _PTBUserWarning
except ImportError:
    _PTBUserWarning = UserWarning  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)

# ── Conversation states ──────────────────────────────────────────────
CAPITAL, MARGIN, RISK_PCT, RISK_PER_SHARE = range(4)

# ── Module-level state ───────────────────────────────────────────────
_pending_signals: dict[int, dict] = {}   # chat_id → current signal
_last_values:     dict[int, dict] = {}   # chat_id → last used settings (persists in-process)

# ── Quick-pick presets ───────────────────────────────────────────────
_CAP_PRESETS = [100_000, 200_000, 500_000, 1_000_000]   # ₹1L  ₹2L  ₹5L  ₹10L
_MRG_PRESETS = [0, 50_000, 100_000, 250_000, 500_000]   # ₹0  ₹50K  ₹1L  ₹2.5L  ₹5L
_RSK_PRESETS = [0.5, 1.0, 1.5, 2.0]                     # 0.5%  1%  1.5%  2%
_RPS_PRESETS = [2.0, 5.0, 10.0, 25.0]                   # fallback when no entry price


# ── Formatting helpers ───────────────────────────────────────────────

def _fmt_inr(n: float) -> str:
    """Short Indian notation for button labels: ₹5L, ₹50K, ₹0."""
    if n == 0:
        return "₹0"
    if n >= 100_000:
        s = f"{n / 100_000:.2f}".rstrip("0").rstrip(".")
        return f"₹{s}L"
    if n >= 1_000:
        s = f"{n / 1_000:.2f}".rstrip("0").rstrip(".")
        return f"₹{s}K"
    return f"₹{n:g}"


# ── Keyboard builders ────────────────────────────────────────────────

def _last_row(key: str, label_fn, prefix: str, chat_id: int) -> list[InlineKeyboardButton]:
    """Return a one-button row showing the last used value, or empty list."""
    val = _last_values.get(chat_id, {}).get(key)
    if val is not None:
        return [InlineKeyboardButton(f"↩ Last: {label_fn(val)}", callback_data=f"{prefix}{val}")]
    return []


def _capital_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(_fmt_inr(v), callback_data=f"cap_{v}") for v in _CAP_PRESETS]]
    last = _last_row("capital", _fmt_inr, "cap_", chat_id)
    if last:
        rows.append(last)
    return InlineKeyboardMarkup(rows)


def _margin_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(_fmt_inr(v), callback_data=f"mrg_{v}") for v in _MRG_PRESETS[:3]],
        [InlineKeyboardButton(_fmt_inr(v), callback_data=f"mrg_{v}") for v in _MRG_PRESETS[3:]],
    ]
    last = _last_row("margin", _fmt_inr, "mrg_", chat_id)
    if last:
        rows.append(last)
    return InlineKeyboardMarkup(rows)


def _risk_pct_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"{v}%", callback_data=f"rsk_{v}") for v in _RSK_PRESETS]]
    last = _last_row("risk_pct", lambda v: f"{v}%", "rsk_", chat_id)
    if last:
        rows.append(last)
    return InlineKeyboardMarkup(rows)


def _rps_keyboard(chat_id: int, entry_price: float = 0.0) -> InlineKeyboardMarkup:
    """
    When entry_price is known, show 0.5/1/1.5/2% of entry translated to ₹.
    Otherwise fall back to fixed presets.
    """
    if entry_price:
        btns = [
            InlineKeyboardButton(
                f"{pct}%=₹{round(entry_price * pct / 100, 2):g}",
                callback_data=f"rps_{round(entry_price * pct / 100, 2)}",
            )
            for pct in [0.5, 1.0, 1.5, 2.0]
        ]
        rows = [btns[:2], btns[2:]]
    else:
        rows = [
            [InlineKeyboardButton(f"₹{v:g}", callback_data=f"rps_{v}") for v in _RPS_PRESETS[:2]],
            [InlineKeyboardButton(f"₹{v:g}", callback_data=f"rps_{v}") for v in _RPS_PRESETS[2:]],
        ]
    last = _last_row("risk_per_share", lambda v: f"₹{v:g}", "rps_", chat_id)
    if last:
        rows.append(last)
    return InlineKeyboardMarkup(rows)


# ── Prompt senders ───────────────────────────────────────────────────

async def _ask(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    text: str,
    kbd: InlineKeyboardMarkup,
) -> None:
    """Send a prompt with an inline keyboard and store message_id for later cleanup."""
    msg = await context.bot.send_message(
        chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=kbd
    )
    context.user_data["_kbd_msg"] = msg.message_id


async def _dismiss_keyboard(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """Remove the inline keyboard from the last prompt (when user typed instead of tapping)."""
    msg_id = context.user_data.pop("_kbd_msg", None)
    if msg_id:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=msg_id, reply_markup=None
            )
        except Exception:
            pass  # already gone or already modified — ignore


async def _send_capital_q(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    await _ask(context, chat_id, "1/4 · *Capital* (₹) — tap or type:", _capital_keyboard(chat_id))


async def _send_margin_q(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    await _ask(context, chat_id, "2/4 · *Margin* (₹) — tap or type:", _margin_keyboard(chat_id))


async def _send_risk_pct_q(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    await _ask(context, chat_id, "3/4 · *Risk %* on capital — tap or type:", _risk_pct_keyboard(chat_id))


async def _send_rps_q(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    entry     = context.user_data.get("_entry_price", 0.0)
    direction = context.user_data.get("_direction", "")
    note = ""
    if entry:
        emoji = {"BULLISH": "🟢", "BEARISH": "🔴"}.get(direction, "")
        note = f"\n_{emoji} Entry: ₹{entry:,.2f}_"
    await _ask(
        context, chat_id,
        f"4/4 · *Risk per Share* (₹ SL distance){note}\n_Tap a % of entry or type custom:_",
        _rps_keyboard(chat_id, entry),
    )


# ── Core calculation ─────────────────────────────────────────────────

async def _compute_and_send(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    capital        = context.user_data["capital"]
    margin         = context.user_data["margin"]
    risk_pct       = context.user_data["risk_pct"]
    risk_per_share = context.user_data["risk_per_share"]

    signal      = _pending_signals.pop(chat_id, {})
    symbol      = signal.get("symbol", "").replace("NSE:", "").replace("-EQ", "") or "—"
    entry_price = signal.get("candle_close", 0.0)
    direction   = signal.get("signal_type", "")

    buying_power = capital + margin
    risk_amount  = capital * (risk_pct / 100)
    quantity     = math.floor(risk_amount / risk_per_share)
    total_cost   = quantity * entry_price if entry_price else 0.0
    max_loss     = quantity * risk_per_share

    d_emoji = {"BULLISH": "🟢", "BEARISH": "🔴"}.get(direction, "")

    def fmt(n: float) -> str:
        return f"₹{n:,.2f}"

    header = f"{d_emoji} *{symbol}*"
    if entry_price:
        header += f" @ {fmt(entry_price)}"

    lines = [
        "━━━━━━━━━━━━━━━━━━━━",
        "📊 *POSITION SIZING*",
        header,
        "━━━━━━━━━━━━━━━━━━━━",
        f"Capital:          {fmt(capital)}",
        f"Margin:           {fmt(margin)}",
        f"Buying Power:     {fmt(buying_power)}",
        "",
        f"Risk on Capital:  {risk_pct}% → {fmt(risk_amount)}",
        f"Risk per Share:   {fmt(risk_per_share)}",
        "",
        f"*Quantity:        {quantity:,} shares*",
    ]
    if total_cost:
        lines.append(f"Total Cost:       {fmt(total_cost)}")
    lines += [
        f"Max Loss:         {fmt(max_loss)}",
        "━━━━━━━━━━━━━━━━━━━━",
        "⚠️ Manual trade — no order placed",
    ]

    # Persist values for next signal
    _last_values[chat_id] = {
        "capital":        capital,
        "margin":         margin,
        "risk_pct":       risk_pct,
        "risk_per_share": risk_per_share,
    }

    await context.bot.send_message(
        chat_id=chat_id, text="\n".join(lines), parse_mode="Markdown"
    )
    context.user_data.clear()


# ── Entry point (called externally by the notifier) ──────────────────

async def prompt_calculator(bot, chat_id: int, signal: dict) -> None:
    """
    Called by telegram_notifier right after the breakout alert.
    If the user has previous settings, surfaces a one-tap "Repeat last" button.
    """
    _pending_signals[chat_id] = signal
    last = _last_values.get(chat_id)

    rows: list[list[InlineKeyboardButton]] = []
    if last:
        label = (
            f"♻️ Repeat  {_fmt_inr(last['capital'])} · "
            f"M:{_fmt_inr(last['margin'])} · "
            f"{last['risk_pct']}% · ₹{last['risk_per_share']:g}/sh"
        )
        rows.append([InlineKeyboardButton(label, callback_data="calc_last")])

    rows.append([
        InlineKeyboardButton("📊 Calculate", callback_data="calc_yes"),
        InlineKeyboardButton("⏭ Skip",       callback_data="calc_skip"),
    ])

    await bot.send_message(
        chat_id=chat_id,
        text="Run position sizing calculator?",
        reply_markup=InlineKeyboardMarkup(rows),
    )


# ── Conversation entry ────────────────────────────────────────────────

async def calc_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id

    if query.data == "calc_skip":
        await query.edit_message_text("Calculator skipped.")
        return ConversationHandler.END

    # Store signal context so all prompt builders can reference entry price / direction
    signal = _pending_signals.get(chat_id, {})
    context.user_data["_entry_price"] = signal.get("candle_close", 0.0)
    context.user_data["_direction"]   = signal.get("signal_type", "")

    if query.data == "calc_last":
        last = _last_values.get(chat_id)
        if last:
            context.user_data.update(last)
            await query.edit_message_text("♻️ Using last settings…")
            await _compute_and_send(context, chat_id)
            return ConversationHandler.END

    # Fresh calculation
    await query.edit_message_text("📊 Position Sizing Calculator")
    await _send_capital_q(context, chat_id)
    return CAPITAL


# ── State: CAPITAL ────────────────────────────────────────────────────

async def _btn_capital(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    value = float(query.data[4:])   # strip "cap_"
    context.user_data["capital"] = value
    await query.edit_message_text(f"1/4 · Capital: *{_fmt_inr(value)}* ✓", parse_mode="Markdown")
    await _send_margin_q(context, update.effective_chat.id)
    return MARGIN


async def get_capital(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await _dismiss_keyboard(context, chat_id)
    try:
        capital = float(update.message.text.replace(",", "").strip())
        if capital <= 0:
            raise ValueError
        context.user_data["capital"] = capital
        await _send_margin_q(context, chat_id)
        return MARGIN
    except ValueError:
        msg = await update.message.reply_text(
            "❌ Invalid. Enter a positive number (e.g. 500000):",
            reply_markup=_capital_keyboard(chat_id),
        )
        context.user_data["_kbd_msg"] = msg.message_id
        return CAPITAL


# ── State: MARGIN ─────────────────────────────────────────────────────

async def _btn_margin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    value = float(query.data[4:])   # strip "mrg_"
    context.user_data["margin"] = value
    await query.edit_message_text(f"2/4 · Margin: *{_fmt_inr(value)}* ✓", parse_mode="Markdown")
    await _send_risk_pct_q(context, update.effective_chat.id)
    return RISK_PCT


async def get_margin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await _dismiss_keyboard(context, chat_id)
    try:
        margin = float(update.message.text.replace(",", "").strip())
        if margin < 0:
            raise ValueError
        context.user_data["margin"] = margin
        await _send_risk_pct_q(context, chat_id)
        return RISK_PCT
    except ValueError:
        msg = await update.message.reply_text(
            "❌ Invalid. Enter 0 or a positive number:",
            reply_markup=_margin_keyboard(chat_id),
        )
        context.user_data["_kbd_msg"] = msg.message_id
        return MARGIN


# ── State: RISK_PCT ───────────────────────────────────────────────────

async def _btn_risk_pct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    value = float(query.data[4:])   # strip "rsk_"
    context.user_data["risk_pct"] = value
    await query.edit_message_text(f"3/4 · Risk: *{value}%* ✓", parse_mode="Markdown")
    await _send_rps_q(context, update.effective_chat.id)
    return RISK_PER_SHARE


async def get_risk_pct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await _dismiss_keyboard(context, chat_id)
    try:
        risk_pct = float(update.message.text.replace("%", "").strip())
        if not (0 < risk_pct <= 100):
            raise ValueError
        context.user_data["risk_pct"] = risk_pct
        await _send_rps_q(context, chat_id)
        return RISK_PER_SHARE
    except ValueError:
        msg = await update.message.reply_text(
            "❌ Enter a % between 0–100 (e.g. 1 or 0.5):",
            reply_markup=_risk_pct_keyboard(chat_id),
        )
        context.user_data["_kbd_msg"] = msg.message_id
        return RISK_PCT


# ── State: RISK_PER_SHARE ─────────────────────────────────────────────

async def _btn_rps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    value = float(query.data[4:])   # strip "rps_"
    context.user_data["risk_per_share"] = value
    await query.edit_message_text(f"4/4 · Risk/Share: *₹{value:g}* ✓", parse_mode="Markdown")
    await _compute_and_send(context, update.effective_chat.id)
    return ConversationHandler.END


async def get_risk_per_share(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await _dismiss_keyboard(context, chat_id)
    try:
        risk_per_share = float(update.message.text.replace(",", "").strip())
        if risk_per_share <= 0:
            raise ValueError
        context.user_data["risk_per_share"] = risk_per_share
        await _compute_and_send(context, chat_id)
        return ConversationHandler.END
    except ValueError:
        msg = await update.message.reply_text(
            "❌ Enter a positive number (e.g. 3.50):",
            reply_markup=_rps_keyboard(chat_id, context.user_data.get("_entry_price", 0.0)),
        )
        context.user_data["_kbd_msg"] = msg.message_id
        return RISK_PER_SHARE


# ── Cancel ────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User typed /cancel mid-flow."""
    _pending_signals.pop(update.effective_chat.id, None)
    context.user_data.clear()
    await update.message.reply_text("Calculator cancelled.")
    return ConversationHandler.END


# ── Registration ──────────────────────────────────────────────────────

def build_calculator_handler() -> ConversationHandler:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=_PTBUserWarning)
        return ConversationHandler(
            entry_points=[CallbackQueryHandler(calc_start, pattern="^calc_")],
            states={
                CAPITAL: [
                    CallbackQueryHandler(_btn_capital,  pattern=r"^cap_"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, get_capital),
                ],
                MARGIN: [
                    CallbackQueryHandler(_btn_margin,   pattern=r"^mrg_"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, get_margin),
                ],
                RISK_PCT: [
                    CallbackQueryHandler(_btn_risk_pct, pattern=r"^rsk_"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, get_risk_pct),
                ],
                RISK_PER_SHARE: [
                    CallbackQueryHandler(_btn_rps,      pattern=r"^rps_"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, get_risk_per_share),
                ],
            },
            fallbacks=[MessageHandler(filters.COMMAND, cancel)],
            conversation_timeout=120,
        )