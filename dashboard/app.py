"""ORB control panel. Settings are stored in the same SQLite file the bot reads."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytz
import streamlit as st

from src.config import NIFTY50_SYMBOLS
from src.strategy.runtime_settings import (
    BREAKOUT_TIMEFRAMES,
    ORB_DURATIONS,
    StrategySettings,
    enqueue_recalc,
    load_settings,
    normalize_symbol,
    save_settings,
)
from src.utils import db

IST = pytz.timezone("Asia/Kolkata")


def _ticker(symbol: str) -> str:
    return symbol.replace("NSE:", "").replace("-EQ", "")


def _end_label(minutes: int) -> str:
    end = datetime(2000, 1, 1, 9, 15) + timedelta(minutes=minutes)
    return end.strftime("%H:%M")


def _load_symbols(settings: StrategySettings) -> None:
    st.session_state.symbols = list(settings.symbols)


st.set_page_config(page_title="ORB control", layout="wide")
db.init_db()
settings = load_settings()

if "symbols" not in st.session_state:
    _load_symbols(settings)

st.title("ORB control")
st.caption(
    "One set of rules for the whole list. The opening range always starts at 09:15. "
    "Save stores the rules for the next session. Recalculate applies them to today, "
    "clears today's breakout alerts, and sends each qualifying alert again."
)

left, right = st.columns([1.1, 1], gap="large")

with left:
    st.subheader(f"Tracking list ({len(st.session_state.symbols)})")
    add_col, add_btn = st.columns([3, 1])
    with add_col:
        new_symbol = st.text_input(
            "Add symbol",
            placeholder="RELIANCE or NSE:RELIANCE-EQ",
            label_visibility="collapsed",
        )
    with add_btn:
        st.write("")
        if st.button("Add", use_container_width=True):
            try:
                symbol = normalize_symbol(new_symbol)
            except ValueError as exc:
                st.error(str(exc))
            else:
                if symbol not in st.session_state.symbols:
                    st.session_state.symbols.append(symbol)
                    st.session_state.symbols.sort()
                st.rerun()

    nifty_col, reload_col = st.columns(2)
    with nifty_col:
        if st.button("Add Nifty 50", use_container_width=True):
            merged = sorted(set(st.session_state.symbols) | set(NIFTY50_SYMBOLS))
            st.session_state.symbols = merged
            st.rerun()
    with reload_col:
        if st.button("Reload saved list", use_container_width=True):
            _load_symbols(load_settings())
            st.rerun()

    removal = st.multiselect(
        "Symbols",
        options=st.session_state.symbols,
        format_func=_ticker,
        placeholder="Select symbols to remove",
    )
    if st.button("Remove selected", disabled=not removal):
        st.session_state.symbols = [s for s in st.session_state.symbols if s not in removal]
        st.rerun()

with right:
    st.subheader("Rules")
    price_on = st.toggle(
        "Price filter",
        value=settings.max_price_enabled,
        help="When on, a symbol is tracked only if its price is below the cap. Off tracks every symbol in the list.",
    )
    max_price = st.number_input(
        "Max price (₹)",
        min_value=1.0,
        value=float(settings.max_stock_price),
        step=10.0,
        disabled=not price_on,
    )
    threshold = st.number_input(
        "ORB percentage — max range to qualify (%)",
        min_value=0.05,
        max_value=20.0,
        value=float(settings.orb_range_threshold),
        step=0.05,
        format="%.2f",
    )
    duration = st.radio(
        "Opening range, starting at 09:15",
        options=list(ORB_DURATIONS),
        index=list(ORB_DURATIONS).index(settings.orb_duration_minutes),
        format_func=lambda minutes: f"{minutes} min, ends {_end_label(minutes)}",
        horizontal=True,
    )
    timeframe = st.radio(
        "Breakout candle after the opening range ends",
        options=list(BREAKOUT_TIMEFRAMES),
        index=list(BREAKOUT_TIMEFRAMES).index(settings.breakout_timeframe_minutes),
        format_func=lambda minutes: f"{minutes} min",
        horizontal=True,
    )

    def _current_settings() -> StrategySettings:
        return StrategySettings(
            symbols=list(st.session_state.symbols),
            max_price_enabled=price_on,
            max_stock_price=float(max_price),
            orb_range_threshold=float(threshold),
            orb_duration_minutes=int(duration),
            breakout_timeframe_minutes=int(timeframe),
        )

    if st.button("Save", type="primary"):
        try:
            saved = save_settings(_current_settings())
        except ValueError as exc:
            st.error(str(exc))
        else:
            _load_symbols(saved)
            st.success(
                f"Saved at {saved.updated_at}. "
                f"Opening range 09:15–{saved.orb_end_label}, "
                f"breakout candles {saved.breakout_timeframe_minutes} min. "
                "The bot picks this up within a few seconds."
            )

    st.divider()
    confirm = st.checkbox(
        "Clear today's signals and send every qualifying breakout again",
    )
    if st.button("Recalculate today", disabled=not confirm):
        try:
            save_settings(_current_settings())
            command_id = enqueue_recalc()
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.success(f"Recalculation #{command_id} queued. The bot runs it on its next check.")


@st.fragment(run_every=5)
def _status() -> None:
    command = db.latest_command()
    st.subheader("Last recalculation")
    if command is None:
        st.info("No recalculation has been requested yet.")
    else:
        status = command["status"]
        detail = command["detail"] or "Waiting for the bot."
        line = f"#{command['id']} · {status} · requested {command['created_at']}"
        if status == "done":
            st.success(f"{line}\n\n{detail}")
        elif status == "error":
            st.error(f"{line}\n\n{detail}")
        else:
            st.warning(f"{line}\n\n{detail}")

    today = datetime.now(IST).strftime("%Y-%m-%d")
    rows = db.get_orb_level_rows(today)
    signals = db.get_signals(today)
    levels, alerts = st.columns(2)
    with levels:
        st.subheader("Today's opening range")
        if not rows:
            st.caption("Nothing locked for today yet.")
        else:
            st.dataframe(
                [
                    {
                        "Symbol": _ticker(row["symbol"]),
                        "High": row["orb_high"],
                        "Low": row["orb_low"],
                        "Range %": round(row["range_pct"], 2),
                        "Qualified": "Yes" if row["passed_filter"] else "No",
                    }
                    for row in rows
                ],
                hide_index=True,
                use_container_width=True,
            )
    with alerts:
        st.subheader("Today's breakouts")
        if not signals:
            st.caption("No breakout alerts today.")
        else:
            st.dataframe(
                [
                    {
                        "Symbol": _ticker(row["symbol"]),
                        "Side": row["signal_type"],
                        "Close": row["candle_close"],
                        "Level": row["orb_level"],
                        "Move %": round(row["move_pct"], 2),
                        "Time": row["triggered_at"],
                    }
                    for row in signals
                ],
                hide_index=True,
                use_container_width=True,
            )


_status()
