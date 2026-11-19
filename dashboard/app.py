"""ORB control panel. Settings are stored in the same SQLite file the bot reads."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytz
import streamlit as st

from src.broker.auth import is_token_valid
from src.broker.symbol_search import load_master, resolve_nse_equity, search_nse_equity
from src.config import NIFTY50_SYMBOLS
from src.strategy.runtime_settings import (
    BREAKOUT_TIMEFRAMES,
    ORB_DURATIONS,
    StrategySettings,
    enqueue_fyers_auth,
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


def _ensure_sym_master() -> dict | None:
    if st.session_state.sym_master is not None:
        return st.session_state.sym_master
    try:
        st.session_state.sym_master = load_master()
        return st.session_state.sym_master
    except Exception as exc:
        st.error(f"Could not load Fyers symbol list: {exc}")
        return None


def _parse_symbol_list(text: str) -> list[str]:
    tokens: list[str] = []
    for line in text.replace(",", " ").replace(";", " ").splitlines():
        for piece in line.split():
            piece = piece.strip()
            if piece:
                tokens.append(piece)
    return tokens


def _resolve_many(raw_tokens: list[str], master: dict | None) -> tuple[list[str], list[str]]:
    resolved: list[str] = []
    errors: list[str] = []
    for raw in raw_tokens:
        sym = resolve_nse_equity(raw, master=master) if master else None
        if sym is None:
            try:
                sym = normalize_symbol(raw)
            except ValueError as exc:
                errors.append(f"{raw}: {exc}")
                continue
        if sym not in resolved:
            resolved.append(sym)
    return resolved, errors


def _add_symbols_bulk(symbols: list[str]) -> int:
    added = 0
    for sym in symbols:
        if sym not in st.session_state.symbols:
            st.session_state.symbols.append(sym)
            added += 1
    if added:
        st.session_state.symbols.sort()
    return added


def _command_line(command: dict | None) -> str:
    if command is None:
        return "—"
    status = command["status"]
    detail = (command["detail"] or "Waiting…")[:80]
    return f"#{command['id']} {status} · {detail}"


# ── Session bootstrap ──────────────────────────────────────────────────────────

st.set_page_config(page_title="ORB control", layout="wide", initial_sidebar_state="collapsed")
db.init_db()
settings = load_settings()

if "symbols" not in st.session_state:
    _load_symbols(settings)
if "sym_master" not in st.session_state:
    st.session_state.sym_master = None
if "sym_search_hits" not in st.session_state:
    st.session_state.sym_search_hits = []
if "sym_search_for" not in st.session_state:
    st.session_state.sym_search_for = ""

today = datetime.now(IST).strftime("%Y-%m-%d")
token_ok = is_token_valid()
_day_rows = db.get_orb_level_rows(today)
qualified = sum(1 for r in _day_rows if r["passed_filter"])

# ── Header ─────────────────────────────────────────────────────────────────────

st.title("ORB control")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Tracking", len(st.session_state.symbols))
m2.metric("Fyers token", "OK" if token_ok else "Login needed")
m3.metric("Qualified today", qualified)
m4.metric("Breakouts today", len(db.get_signals(today)))

tab_list, tab_rules, tab_day = st.tabs(["Watchlist", "Strategy", "Today"])

# ── Watchlist ──────────────────────────────────────────────────────────────────

with tab_list:
    st.markdown("Search as you type, add several symbols, then **Save strategy** on the Strategy tab.")

    query = st.text_input(
        "Search",
        key="symbol_search_query",
        placeholder="Ticker or company name (e.g. TCS, reliance, bank)",
        label_visibility="collapsed",
    ).strip()

    if len(query) >= 2 and query != st.session_state.sym_search_for:
        master = _ensure_sym_master()
        if master is not None:
            st.session_state.sym_search_hits = search_nse_equity(query, master=master, limit=30)
            st.session_state.sym_search_for = query
    elif len(query) < 2:
        st.session_state.sym_search_hits = []
        st.session_state.sym_search_for = ""

    hits = st.session_state.sym_search_hits
    if len(query) >= 2 and hits:
        labels = {
            h["symbol"]: f"{h['ticker']} — {h['name']}" if h.get("name") else h["symbol"]
            for h in hits
        }
        addable = [s for s in labels if s not in st.session_state.symbols]
        picked = st.multiselect(
            "Search results",
            options=addable,
            format_func=lambda sym: labels[sym],
            placeholder="Pick symbols to add",
            label_visibility="visible",
        )
        b1, b2, b3 = st.columns([2, 2, 3])
        with b1:
            if st.button("Add picked", type="primary", disabled=not picked, use_container_width=True):
                _add_symbols_bulk(picked)
                st.rerun()
        with b2:
            if st.button(
                f"Add all ({len(addable)})",
                disabled=not addable,
                use_container_width=True,
            ):
                _add_symbols_bulk(addable)
                st.rerun()
        with b3:
            with st.popover("More ways to add"):
                if st.button("Add full Nifty 50", use_container_width=True):
                    st.session_state.symbols = sorted(set(st.session_state.symbols) | set(NIFTY50_SYMBOLS))
                    st.rerun()
                if st.button("Discard unsaved edits — reload from disk", use_container_width=True):
                    _load_symbols(load_settings())
                    st.rerun()
                bulk = st.text_area(
                    "Paste tickers",
                    height=100,
                    placeholder="RELIANCE, TCS INFY or one per line",
                )
                if st.button("Add pasted tickers", disabled=not bulk.strip(), use_container_width=True):
                    master = _ensure_sym_master()
                    resolved, errors = _resolve_many(_parse_symbol_list(bulk), master)
                    added = _add_symbols_bulk(resolved)
                    if errors:
                        st.warning("Some lines skipped: " + "; ".join(errors[:5]))
                    if added:
                        st.rerun()
    elif len(query) >= 2:
        st.caption("No matches.")

    st.divider()
    st.subheader(f"On your list ({len(st.session_state.symbols)})")
    if st.session_state.symbols:
        st.dataframe(
            [{"Ticker": _ticker(s)} for s in st.session_state.symbols],
            hide_index=True,
            use_container_width=True,
            height=min(420, 38 + len(st.session_state.symbols) * 35),
        )
        to_remove = st.multiselect(
            "Remove from list",
            options=st.session_state.symbols,
            format_func=_ticker,
            placeholder="Choose tickers to remove",
            label_visibility="collapsed",
        )
        if st.button("Remove picked", disabled=not to_remove):
            st.session_state.symbols = [s for s in st.session_state.symbols if s not in to_remove]
            st.rerun()
    else:
        st.info("List is empty. Use search or **More ways to add** → Nifty 50.")

# ── Strategy ───────────────────────────────────────────────────────────────────

with tab_rules:
    with st.form("strategy_form", clear_on_submit=False):
        st.subheader("ORB rules")
        c1, c2 = st.columns(2)
        with c1:
            price_on = st.toggle("Price filter", value=settings.max_price_enabled)
            max_price = st.number_input(
                "Max price (₹)",
                min_value=1.0,
                value=float(settings.max_stock_price),
                step=10.0,
                disabled=not price_on,
            )
            threshold = st.number_input(
                "Max ORB range (%)",
                min_value=0.05,
                max_value=20.0,
                value=float(settings.orb_range_threshold),
                step=0.05,
                format="%.2f",
            )
        with c2:
            duration = st.selectbox(
                "Opening range from 09:15",
                options=list(ORB_DURATIONS),
                index=list(ORB_DURATIONS).index(settings.orb_duration_minutes),
                format_func=lambda m: f"{m} min (ends {_end_label(m)})",
            )
            timeframe = st.selectbox(
                "Breakout candle size",
                options=list(BREAKOUT_TIMEFRAMES),
                index=list(BREAKOUT_TIMEFRAMES).index(settings.breakout_timeframe_minutes),
                format_func=lambda m: f"{m} minutes",
            )

        st.divider()
        st.subheader("Actions")
        a1, a2 = st.columns(2)
        with a1:
            st.caption("Sends a Fyers login link on Telegram (~10 min to finish).")
            request_login = st.form_submit_button(
                "Telegram: new Fyers login",
                disabled=db.has_active_command("fyers_auth"),
            )
        with a2:
            recalc_ok = st.checkbox("I want to clear today's alerts and resend breakouts")
            recalc = st.form_submit_button("Recalculate today", disabled=not recalc_ok)

        save = st.form_submit_button("Save watchlist + rules", type="primary")

    def _draft() -> StrategySettings:
        return StrategySettings(
            symbols=list(st.session_state.symbols),
            max_price_enabled=price_on,
            max_stock_price=float(max_price),
            orb_range_threshold=float(threshold),
            orb_duration_minutes=int(duration),
            breakout_timeframe_minutes=int(timeframe),
        )

    if save:
        try:
            saved = _draft()
            save_settings(saved)
            _load_symbols(saved)
            st.success(f"Saved {len(saved.symbols)} symbols · ORB 09:15–{saved.orb_end_label}")
        except ValueError as exc:
            st.error(str(exc))

    if request_login:
        try:
            enqueue_fyers_auth()
            st.success("Login queued — check Telegram.")
        except ValueError as exc:
            st.error(str(exc))

    if recalc:
        try:
            save_settings(_draft())
            cid = enqueue_recalc()
            st.success(f"Recalculation #{cid} queued.")
        except ValueError as exc:
            st.error(str(exc))

# ── Today ──────────────────────────────────────────────────────────────────────

with tab_day:

    @st.fragment(run_every=5)
    def _live_status() -> None:
        auth_cmd = db.latest_command_for("fyers_auth")
        recalc_cmd = db.latest_command_for("recalc")
        st.caption(
            f"Fyers login: {_command_line(auth_cmd)}  ·  "
            f"Recalc: {_command_line(recalc_cmd)}"
        )
        rows = db.get_orb_level_rows(today)
        signals = db.get_signals(today)
        t1, t2 = st.tabs(["Opening range", "Breakouts"])
        with t1:
            if rows:
                st.dataframe(
                    [
                        {
                            "Symbol": _ticker(r["symbol"]),
                            "High": r["orb_high"],
                            "Low": r["orb_low"],
                            "Range %": round(r["range_pct"], 2),
                            "OK": "Yes" if r["passed_filter"] else "No",
                        }
                        for r in rows
                    ],
                    hide_index=True,
                    use_container_width=True,
                )
            else:
                st.caption("Nothing locked yet today.")
        with t2:
            if signals:
                st.dataframe(
                    [
                        {
                            "Symbol": _ticker(r["symbol"]),
                            "Side": r["signal_type"],
                            "Close": r["candle_close"],
                            "Move %": round(r["move_pct"], 2),
                            "Time": r["triggered_at"],
                        }
                        for r in signals
                    ],
                    hide_index=True,
                    use_container_width=True,
                )
            else:
                st.caption("No breakout alerts yet.")

    _live_status()