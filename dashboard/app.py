"""ORB control panel. Settings are stored in the same SQLite file the bot reads."""
import sys
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytz
import streamlit as st
import streamlit.components.v1 as components

from src.backtest.months import MONTH_NAMES, month_window
from src.broker.auth import is_token_valid
from src.broker.symbol_search import load_master, resolve_nse_equity, search_nse_equity
from src.config import NIFTY50_SYMBOLS
from src.strategy.runtime_settings import (
    BREAKOUT_TIMEFRAMES,
    ORB_DURATIONS,
    RANGE_MODES,
    RETEST_WINDOWS,
    StrategySettings,
    enqueue_fyers_auth,
    enqueue_recalc,
    load_settings,
    normalize_symbol,
    save_settings,
    tracked_symbols,
    update_index_symbols,
    update_watchlist,
)
from src.strategy.indices import INDEX_CATALOG, index_label
from src.utils import db

IST = pytz.timezone("Asia/Kolkata")


def _ticker(symbol: str) -> str:
    text = symbol.replace("NSE:", "")
    for suffix in ("-EQ", "-INDEX"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _cutoff_value(value: str) -> dtime:
    hour, minute = (value or "15:15").split(":")
    return dtime(int(hour), int(minute))


def _end_label(minutes: int) -> str:
    end = datetime(2000, 1, 1, 9, 15) + timedelta(minutes=minutes)
    return end.strftime("%H:%M")


def _duration_label(minutes: int) -> str:
    name = "1 hour" if minutes == 60 else f"{minutes} min"
    return f"{name} (ends {_end_label(minutes)})"


def _range_mode_label(mode: str) -> str:
    return "Greater than or equal" if mode == "gte" else "Less than or equal"


def _load_symbols(settings: StrategySettings) -> None:
    st.session_state.symbols = list(settings.symbols)
    st.session_state.index_symbols = list(settings.index_symbols or [])


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


def _persist_watchlist() -> None:
    update_watchlist(list(st.session_state.symbols))


def _focus_ticker_search() -> None:
    """Focus the ticker search field (not the results table)."""
    components.html(
        """
        <script>
        const doc = window.parent.document;
        setTimeout(() => {
          let el = doc.querySelector('input[aria-label="Ticker search"]');
          if (!el) {
            const box = doc.getElementById("orb-ticker-search");
            if (box) {
              el = box.parentElement?.querySelector('input[type="text"]');
            }
          }
          if (el) {
            el.focus();
            el.select();
          }
        }, 80);
        </script>
        """,
        height=0,
    )


def _queue_symbol(sym: str) -> None:
    if sym in st.session_state.symbols or sym in st.session_state.modal_staging:
        return
    st.session_state.modal_staging.append(sym)
    st.session_state.clear_modal_search = True
    st.session_state.modal_focus_search = True


@st.fragment
def _symbol_add_panel() -> None:
    if st.session_state.pop("clear_modal_search", False):
        st.session_state.modal_live_search = ""

    if st.session_state.pop("modal_focus_search", False):
        _focus_ticker_search()

    st.markdown('<span id="orb-ticker-search"></span>', unsafe_allow_html=True)
    st.text_input(
        "Ticker search",
        key="modal_live_search",
        placeholder="e.g. kotak, tcs, hdfc",
        label_visibility="collapsed",
        type="search",
        live=True,
    )
    query = (st.session_state.get("modal_live_search") or "").strip()

    hits: list[dict] = []
    if len(query) >= 2:
        master = _ensure_sym_master()
        if master is not None:
            hits = search_nse_equity(query, master=master, limit=40)

    if hits:
        head = st.columns([1, 4])
        head[0].markdown("**Ticker**")
        head[1].markdown("**Company**")
        list_height = max(480, min(720, 52 * len(hits)))
        with st.container(height=list_height):
            for h in hits:
                sym = h["symbol"]
                on_watch = sym in st.session_state.symbols
                on_queue = sym in st.session_state.modal_staging
                name = (h.get("name") or "")[:56]
                if on_watch:
                    suffix = " (already on list)"
                elif on_queue:
                    suffix = " (queued)"
                else:
                    suffix = ""
                row_label = f"{h['ticker']:<10}  {name}{suffix}"
                if st.button(
                    row_label,
                    key=f"pick_{sym}",
                    disabled=on_watch or on_queue,
                    use_container_width=True,
                ):
                    _queue_symbol(sym)
                    st.rerun()
    elif len(query) >= 2:
        st.caption("No NSE EQ matches.")

    if st.session_state.modal_staging:
        st.info("Queued: " + ", ".join(_ticker(s) for s in st.session_state.modal_staging))

    with st.expander("Paste many tickers"):
        bulk = st.text_area(
            "Paste",
            height=72,
            placeholder="RELIANCE, TCS INFY or one per line",
            label_visibility="collapsed",
        )
        if st.button("Queue pasted", disabled=not bulk.strip(), key="queue_bulk"):
            master = _ensure_sym_master()
            resolved, errors = _resolve_many(_parse_symbol_list(bulk), master)
            for sym in resolved:
                if sym not in st.session_state.modal_staging and sym not in st.session_state.symbols:
                    st.session_state.modal_staging.append(sym)
            if errors:
                st.warning("Skipped: " + "; ".join(errors[:4]))

    b1, b2, b3 = st.columns(3)
    with b1:
        if st.button(
            "Add to watchlist",
            type="primary",
            disabled=not st.session_state.modal_staging,
            key="commit_staging",
        ):
            _add_symbols_bulk(st.session_state.modal_staging)
            _persist_watchlist()
            st.session_state.modal_staging = []
            st.session_state.add_symbols_open = False
            st.rerun()
    with b2:
        if st.button("Queue Nifty 50", key="queue_nifty"):
            for sym in NIFTY50_SYMBOLS:
                if sym not in st.session_state.modal_staging and sym not in st.session_state.symbols:
                    st.session_state.modal_staging.append(sym)
            st.rerun()
    with b3:
        if st.button("Cancel", key="cancel_add"):
            st.session_state.modal_staging = []
            st.session_state.add_symbols_open = False
            st.rerun()


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
if "index_symbols" not in st.session_state:
    st.session_state.index_symbols = list(settings.index_symbols or [])
if "sym_master" not in st.session_state:
    st.session_state.sym_master = None
if "add_symbols_open" not in st.session_state:
    st.session_state.add_symbols_open = False
if "modal_staging" not in st.session_state:
    st.session_state.modal_staging = []
if "modal_live_search" not in st.session_state:
    st.session_state.modal_live_search = ""

today = datetime.now(IST).strftime("%Y-%m-%d")
token_ok = is_token_valid()
_day_rows = db.get_orb_level_rows(today)
qualified = sum(1 for r in _day_rows if r["passed_filter"])

# ── Header ─────────────────────────────────────────────────────────────────────

st.title("ORB control")
m1, m2, m3, m4 = st.columns(4)
_preview = StrategySettings(
    symbols=list(st.session_state.symbols),
    max_price_enabled=settings.max_price_enabled,
    max_stock_price=settings.max_stock_price,
    orb_range_threshold=settings.orb_range_threshold,
    orb_duration_minutes=settings.orb_duration_minutes,
    breakout_timeframe_minutes=settings.breakout_timeframe_minutes,
    index_symbols=list(st.session_state.get("index_symbols") or []),
    universe=settings.universe,
)
m1.metric("Tracking", len(tracked_symbols(_preview)))
m2.metric("Fyers token", "OK" if token_ok else "Login needed")
m3.metric("Qualified today", qualified)
m4.metric("Breakouts today", len(db.get_signals(today)))

tab_list, tab_rules, tab_day, tab_backtest = st.tabs(["Watchlist", "Strategy", "Today", "Backtest"])

# ── Watchlist ──────────────────────────────────────────────────────────────────

with tab_list:
    st.caption("Watchlist is saved to disk when you add from the modal. ORB rules: **Strategy** tab.")

    head_l, head_r = st.columns([2, 3])
    with head_l:
        if st.button("Add symbols", type="primary", use_container_width=True):
            st.session_state.add_symbols_open = True
            st.session_state.modal_staging = []
            st.session_state.modal_live_search = ""
            st.session_state.modal_focus_search = True
            st.rerun()
    with head_r:
        if st.button("Reload watchlist from disk", use_container_width=True):
            _load_symbols(load_settings())
            st.rerun()

    if st.session_state.add_symbols_open:
        with st.container(border=True):
            t1, t2 = st.columns([8, 1])
            with t1:
                st.subheader("Add symbols")
                st.caption("Type in the box — results update as you type. Click a row to queue it.")
            with t2:
                if st.button("✕", key="close_add_panel"):
                    st.session_state.add_symbols_open = False
                    st.session_state.modal_staging = []
                    st.rerun()

            _symbol_add_panel()

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
            _persist_watchlist()
            st.rerun()
    else:
        st.info("List is empty. Use search or **More ways to add** → Nifty 50.")

    st.divider()
    st.subheader(f"Indices ({len(st.session_state.index_symbols)})")
    st.caption("Tracked every day together with the equity watchlist. The price cap does not apply to indices.")
    if st.session_state.index_symbols:
        st.dataframe(
            [{"Index": index_label(s)} for s in st.session_state.index_symbols],
            hide_index=True,
            use_container_width=True,
            height=min(220, 38 + len(st.session_state.index_symbols) * 35),
        )
    available = [symbol for symbol, _label in INDEX_CATALOG if symbol not in st.session_state.index_symbols]
    add_ix, drop_ix = st.columns(2)
    with add_ix:
        to_add = st.multiselect(
            "Add index",
            options=available,
            format_func=index_label,
            placeholder="Nifty, Bank Nifty, …",
        )
        if st.button("Add indices", disabled=not to_add):
            st.session_state.index_symbols = list(st.session_state.index_symbols) + list(to_add)
            update_index_symbols(st.session_state.index_symbols)
            st.rerun()
    with drop_ix:
        to_drop = st.multiselect(
            "Remove index",
            options=st.session_state.index_symbols,
            format_func=index_label,
            placeholder="Choose an index",
        )
        if st.button("Remove indices", disabled=not to_drop):
            st.session_state.index_symbols = [s for s in st.session_state.index_symbols if s not in to_drop]
            update_index_symbols(st.session_state.index_symbols)
            st.rerun()

# ── Strategy ───────────────────────────────────────────────────────────────────

with tab_rules:
    st.subheader("Entry")
    e1, e2 = st.columns(2)
    live_entry = e1.selectbox(
        "Entry",
        options=["direct", "retest"],
        index=0 if settings.entry_mode == "direct" else 1,
        format_func=lambda key: "Direct breakout" if key == "direct" else "Retest",
        key="live_entry",
    )
    live_retest_window = e2.selectbox(
        "Second break within",
        options=list(RETEST_WINDOWS),
        index=list(RETEST_WINDOWS).index(settings.retest_within_minutes)
        if settings.retest_within_minutes in RETEST_WINDOWS
        else 1,
        format_func=lambda m: f"{m} minutes of the first break",
        disabled=live_entry != "retest",
        key="live_retest_window",
    )
    with st.form("strategy_form", clear_on_submit=False):
        st.subheader("Symbols")
        s1, s2 = st.columns(2)
        live_universe = s1.selectbox(
            "Symbols",
            options=["watchlist", "indices", "both"],
            index=["watchlist", "indices", "both"].index(settings.universe)
            if settings.universe in ("watchlist", "indices", "both")
            else 0,
            format_func=lambda key: {"watchlist": "Equity watchlist", "indices": "Indices", "both": "Both lists"}[key],
            key="live_universe",
        )
        cutoff = s2.time_input(
            "No entry after",
            value=_cutoff_value(settings.entry_cutoff),
            step=timedelta(minutes=5),
            key="live_cutoff",
        )
        st.subheader("Opening range")
        o1, o2, o3 = st.columns(3)
        duration = o1.selectbox(
            "Opening range from 09:15",
            options=list(ORB_DURATIONS),
            index=list(ORB_DURATIONS).index(settings.orb_duration_minutes),
            format_func=_duration_label,
            key="live_duration",
        )
        timeframe = o2.selectbox(
            "Breakout candle size",
            options=list(BREAKOUT_TIMEFRAMES),
            index=list(BREAKOUT_TIMEFRAMES).index(settings.breakout_timeframe_minutes),
            format_func=lambda m: f"{m} minutes",
            key="live_timeframe",
        )
        threshold = o3.number_input(
            "ORB range (%)",
            min_value=0.05,
            max_value=20.0,
            value=float(settings.orb_range_threshold),
            step=0.05,
            format="%.2f",
            key="live_threshold",
        )
        range_mode = st.selectbox(
            "Range qualifies when",
            options=list(RANGE_MODES),
            index=list(RANGE_MODES).index(settings.orb_range_mode),
            format_func=_range_mode_label,
            key="live_range_mode",
        )
        st.subheader("Risk")
        k1, k2, k3, k4 = st.columns(4)
        live_capital = k1.number_input(
            "Capital (₹)", min_value=1000.0, value=float(settings.capital), step=10_000.0, key="live_capital"
        )
        live_margin = k2.selectbox(
            "Margin",
            options=[4, 5, 10],
            index=[4, 5, 10].index(int(settings.margin_multiplier)) if int(settings.margin_multiplier) in (4, 5, 10) else 1,
            format_func=lambda n: f"{n}×",
            key="live_margin",
        )
        live_risk = k3.number_input(
            "Risk per trade (%)", min_value=0.1, max_value=10.0, value=float(settings.risk_pct), step=0.1, key="live_risk"
        )
        live_day_risk = k4.number_input(
            "Risk per day (%)",
            min_value=0.1,
            max_value=20.0,
            value=float(settings.day_risk_pct),
            step=0.1,
            key="live_day_risk",
        )
        k5, k6, k7, k8 = st.columns(4)
        live_rrr = k5.number_input(
            "Reward : risk", min_value=0.5, max_value=10.0, value=float(settings.rrr), step=0.5, key="live_rrr"
        )
        live_max_trades = k6.number_input(
            "Max trades per day",
            min_value=1,
            max_value=20,
            value=int(settings.max_trades_per_day),
            step=1,
            key="live_max_trades",
        )
        price_on = k7.toggle("Price filter", value=settings.max_price_enabled, key="live_price_on")
        max_price = k8.number_input(
            "Max price (₹)",
            min_value=1.0,
            value=float(settings.max_stock_price),
            step=10.0,
            disabled=not price_on,
            key="live_max_price",
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
            orb_range_mode=range_mode,
            orb_duration_minutes=int(duration),
            breakout_timeframe_minutes=int(timeframe),
            entry_cutoff=cutoff.strftime("%H:%M"),
            index_symbols=list(st.session_state.index_symbols),
            entry_mode=live_entry,
            retest_within_minutes=int(live_retest_window),
            universe=live_universe,
            capital=float(live_capital),
            margin_multiplier=float(live_margin),
            risk_pct=float(live_risk),
            day_risk_pct=float(live_day_risk),
            max_trades_per_day=int(live_max_trades),
            rrr=float(live_rrr),
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
                            "Entry": "Retest" if r.get("entry_style") == "retest" else "Direct",
                            "Close": r["candle_close"],
                            "Swing SL": r["suggested_sl"],
                            "ORB SL": r["orb_stop"],
                            "SL basis": r.get("stop_basis") or "—",
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

def _run_combination_sheet(data: bytes, replace_filled: bool, progress) -> bytes:
    """Run the Combinations sheet (or legacy Stage 1 sheets). Each month starts at that row's capital."""
    from io import BytesIO

    from openpyxl import load_workbook

    from src.backtest.history import load_watchlist_minutes, merge_bar_store, range_is_cached, slice_bars
    from src.backtest.sheet_batch import fill_workbook, needed_universes, sheet_windows
    from src.backtest.sheet_fast import iter_sheet_results
    from src.broker.auth import get_access_token
    from src.broker.fyers_client import get_client

    windows = sheet_windows(datetime.now(IST).date())
    if not windows:
        raise ValueError("2026 has not started, so there are no months to fill.")
    token = get_access_token(allow_oauth=False)
    client = get_client(token)
    needed = needed_universes(load_workbook(BytesIO(data)), replace_filled)
    bars_by_universe: dict[str, dict] = {}
    for universe in sorted(needed):
        symbols = list(st.session_state.index_symbols if universe == "indices" else st.session_state.symbols)
        if not symbols:
            label = "index list" if universe == "indices" else "equity watchlist"
            raise ValueError(f"The {label} is empty.")
        start, end = windows[0][0], windows[-1][1]
        store = st.session_state.get("bt_hist_store")
        if range_is_cached(store, symbols, start, end):
            progress(f"Using cached {universe} history. No download.")
            bars = slice_bars(store["bars"], start, end)
        else:
            bars = load_watchlist_minutes(client, symbols, start, end, progress=progress)
            st.session_state.bt_hist_store = merge_bar_store(store, symbols, start, end, bars)
        bars_by_universe[universe] = bars
        progress(f"{universe} history ready ({len(symbols)} symbols).")

    progress("History loaded. Starting workbook scoring…")

    def _run_many(pending):
        return iter_sheet_results(pending, bars_by_universe, windows, progress)

    checkpoint_path = ROOT / "data" / "sheet_runs" / "combinations_filled.xlsx"

    def _checkpoint(payload: bytes) -> None:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_bytes(payload)
        st.session_state.sheet_batch_bytes = payload
        st.session_state.sheet_batch_note = "Progress is saved in the download. Upload that file again to continue."

    payload, done, skipped = fill_workbook(
        data,
        progress=progress,
        replace_filled=replace_filled,
        checkpoint=_checkpoint,
        run_many=_run_many,
    )
    st.session_state.sheet_batch_bytes = payload
    st.session_state.sheet_batch_note = f"Filled {done} setups. Left {skipped} that already had January."
    return payload


# ── Backtest ───────────────────────────────────────────────────────────────────

with tab_backtest:
    st.caption(
        "Replays the lists you choose on 1-minute history. Direct entry uses the first close outside the range. "
        "Retest waits for a close back inside, then a second close outside in the same direction. "
        "Stop is the latest swing or the opening-range extreme, and the target is entry ± RRR × risk. "
        "Size never exceeds the capital you enter here, and it shrinks after a losing day."
    )
    today = datetime.now(IST).date()
    st.subheader("Entry")
    e1, e2 = st.columns(2)
    bt_entry_mode = e1.selectbox(
        "Entry",
        options=["direct", "retest"],
        index=0 if settings.entry_mode == "direct" else 1,
        format_func=lambda key: "Direct breakout" if key == "direct" else "Retest",
        key="bt_entry",
    )
    bt_retest_window = e2.selectbox(
        "Second break within",
        options=list(RETEST_WINDOWS),
        index=list(RETEST_WINDOWS).index(settings.retest_within_minutes)
        if settings.retest_within_minutes in RETEST_WINDOWS
        else 1,
        format_func=lambda m: f"{m} minutes of the first break",
        disabled=bt_entry_mode != "retest",
        key="bt_retest_window",
    )
    st.subheader("Months")
    y1, y2 = st.columns([1, 3])
    year_choices = [today.year - 1, today.year]
    bt_year = y1.selectbox("Year", options=year_choices, index=len(year_choices) - 1)
    open_months = list(range(1, 13 if bt_year < today.year else today.month + 1))
    bt_months = y2.multiselect(
        "Months",
        options=open_months,
        default=[open_months[-1]],
        format_func=lambda month: MONTH_NAMES[month - 1],
    )
    st.caption(
        "Selected months are one backtest, and capital carries from one of those months into the next. "
        "This year runs every month on its own, each one starting again at the capital below."
    )
    with st.form("backtest_form"):
        st.subheader("Symbols")
        s1, s2 = st.columns(2)
        bt_universe = s1.selectbox(
            "Symbols",
            options=["watchlist", "indices", "both"],
            index=["watchlist", "indices", "both"].index(settings.universe)
            if settings.universe in ("watchlist", "indices", "both")
            else 0,
            format_func=lambda key: {"watchlist": "Equity watchlist", "indices": "Indices", "both": "Both lists"}[key],
            key="bt_universe",
        )
        bt_cutoff = s2.time_input(
            "No entry after",
            value=_cutoff_value(settings.entry_cutoff),
            step=timedelta(minutes=5),
            key="bt_cutoff",
        )

        st.subheader("Opening range")
        o1, o2, o3 = st.columns(3)
        bt_duration = o1.selectbox(
            "Opening range from 09:15",
            options=list(ORB_DURATIONS),
            index=list(ORB_DURATIONS).index(settings.orb_duration_minutes),
            format_func=_duration_label,
            key="bt_duration",
        )
        bt_timeframe = o2.selectbox(
            "Breakout candle size",
            options=list(BREAKOUT_TIMEFRAMES),
            index=list(BREAKOUT_TIMEFRAMES).index(settings.breakout_timeframe_minutes),
            format_func=lambda m: f"{m} minutes",
            key="bt_timeframe",
        )
        bt_threshold = o3.number_input(
            "ORB range (%)",
            min_value=0.05,
            max_value=20.0,
            value=float(settings.orb_range_threshold),
            step=0.05,
            format="%.2f",
            key="bt_threshold",
        )
        bt_range_mode = st.selectbox(
            "Range qualifies when",
            options=list(RANGE_MODES),
            index=list(RANGE_MODES).index(settings.orb_range_mode),
            format_func=_range_mode_label,
            key="bt_range_mode",
        )

        st.subheader("Risk")
        k1, k2, k3, k4 = st.columns(4)
        bt_capital = k1.number_input(
            "Capital (₹)", min_value=1000.0, value=float(settings.capital), step=10_000.0, key="bt_capital"
        )
        bt_margin = k2.selectbox(
            "Margin",
            options=[4, 5, 10],
            index=[4, 5, 10].index(int(settings.margin_multiplier)) if int(settings.margin_multiplier) in (4, 5, 10) else 1,
            format_func=lambda n: f"{n}×",
            key="bt_margin",
        )
        bt_risk = k3.number_input(
            "Risk per trade (%)", min_value=0.1, max_value=10.0, value=float(settings.risk_pct), step=0.1, key="bt_risk"
        )
        bt_day_risk = k4.number_input(
            "Risk per day (%)",
            min_value=0.1,
            max_value=20.0,
            value=float(settings.day_risk_pct),
            step=0.1,
            key="bt_day_risk",
        )
        k5, k6, k7, k8 = st.columns(4)
        bt_rrr = k5.number_input(
            "Reward : risk", min_value=0.5, max_value=10.0, value=float(settings.rrr), step=0.5, key="bt_rrr"
        )
        bt_max_trades = k6.number_input(
            "Max trades per day",
            min_value=1,
            max_value=20,
            value=int(settings.max_trades_per_day),
            step=1,
            key="bt_max_trades",
        )
        bt_price_on = k7.toggle("Price filter", value=settings.max_price_enabled, key="bt_price_on")
        bt_max_price = k8.number_input(
            "Max price (₹)",
            min_value=1.0,
            value=float(settings.max_stock_price),
            step=10.0,
            disabled=not bt_price_on,
            key="bt_max_price",
        )
        st.subheader("Trailing stop")
        t1, t2, t3 = st.columns([1, 1, 1])
        bt_trailing = t1.toggle("Trailing stop loss", value=False, key="bt_trailing")
        bt_trail_trigger = t2.number_input(
            "% of distance to target",
            min_value=1.0,
            max_value=99.0,
            value=50.0,
            step=1.0,
            disabled=not bt_trailing,
            help="When price reaches this % of the way from entry to target, stop moves to entry and the add is opened.",
            key="bt_trail_trigger",
        )
        bt_trail_add = t3.number_input(
            "Add size (% of open qty)",
            min_value=1.0,
            max_value=100.0,
            value=50.0,
            step=1.0,
            disabled=not bt_trailing,
            key="bt_trail_add",
        )
        b1, b2 = st.columns(2)
        bt_brokerage_open = b1.number_input(
            "Brokerage — open leg (₹, entry + exit)",
            min_value=0.0,
            value=50.0,
            step=10.0,
            key="bt_brokerage_open",
        )
        bt_brokerage_add = b2.number_input(
            "Brokerage — trailing add leg (₹, entry + exit)",
            min_value=0.0,
            value=50.0,
            step=10.0,
            key="bt_brokerage_add",
        )
        b3, b4 = st.columns(2)
        run_bt = b3.form_submit_button("Run selected months", type="primary")
        run_year = b4.form_submit_button("This year, fresh each month")

    if run_bt or run_year:
        if bt_universe == "indices":
            bt_symbols = list(st.session_state.index_symbols)
        elif bt_universe == "both":
            bt_symbols = list(dict.fromkeys(list(st.session_state.symbols) + list(st.session_state.index_symbols)))
        else:
            bt_symbols = list(st.session_state.symbols)
        if not bt_symbols:
            st.error("The chosen list is empty.")
        elif run_bt and not bt_months:
            st.error("Pick at least one month.")
        elif not is_token_valid():
            st.error("Fyers token is missing. Use Strategy → Telegram: new Fyers login, then run again.")
        else:
            from src.backtest.engine import BacktestRequest, run_backtest
            from src.backtest.history import load_watchlist_minutes, merge_bar_store, range_is_cached, slice_bars
            from src.broker.auth import get_access_token
            from src.broker.fyers_client import get_client

            def _request(start: date, end: date, months: tuple[tuple[int, int], ...] | None) -> BacktestRequest:
                return BacktestRequest(
                    symbols=bt_symbols,
                    date_from=start,
                    date_to=end,
                    orb_range_threshold=float(bt_threshold),
                    orb_range_mode=bt_range_mode,
                    orb_duration_minutes=int(bt_duration),
                    breakout_timeframe_minutes=int(bt_timeframe),
                    entry_cutoff=bt_cutoff.strftime("%H:%M"),
                    entry_mode=bt_entry_mode,
                    retest_within_minutes=int(bt_retest_window),
                    max_price_enabled=bool(bt_price_on),
                    max_stock_price=float(bt_max_price),
                    capital=float(bt_capital),
                    margin_multiplier=float(bt_margin),
                    risk_pct=float(bt_risk),
                    day_risk_pct=float(bt_day_risk),
                    max_trades_per_day=int(bt_max_trades),
                    rrr=float(bt_rrr),
                    include_months=months,
                    trailing_enabled=bool(bt_trailing),
                    trailing_trigger_pct=float(bt_trail_trigger),
                    trailing_add_pct=float(bt_trail_add),
                    brokerage_open=float(bt_brokerage_open),
                    brokerage_add=float(bt_brokerage_add),
                )

            if run_year:
                windows = [month_window(today.year, month, today) for month in range(1, today.month + 1)]
                windows = [window for window in windows if window is not None]
            else:
                windows = [month_window(int(bt_year), int(month), today) for month in bt_months]
                windows = [window for window in windows if window is not None]
            if not windows:
                st.error("Those months have not started yet.")
            else:
                try:
                    with st.status("Running backtest…", expanded=True) as status:
                        note = st.empty()

                        def _progress(message: str) -> None:
                            note.write(message)

                        span_start = min(window[0] for window in windows)
                        span_end = max(window[1] for window in windows)
                        token = get_access_token(allow_oauth=False)
                        client = get_client(token)
                        store = st.session_state.get("bt_hist_store")
                        if range_is_cached(store, bt_symbols, span_start, span_end):
                            _progress("Using cached history for this date range. No download.")
                            bars = slice_bars(store["bars"], span_start, span_end)
                        else:
                            bars = load_watchlist_minutes(
                                client, bt_symbols, span_start, span_end, progress=_progress
                            )
                            st.session_state.bt_hist_store = merge_bar_store(
                                store, bt_symbols, span_start, span_end, bars
                            )
                        if run_year:
                            month_reports = []
                            for start, end in windows:
                                _progress(f"{MONTH_NAMES[start.month - 1]} {start.year}")
                                month_report = run_backtest(
                                    client,
                                    _request(start, end, ((start.year, start.month),)),
                                    progress=_progress,
                                    minute_bars=slice_bars(bars, start, end),
                                )
                                month_report["label"] = f"{MONTH_NAMES[start.month - 1]} {start.year}"
                                month_reports.append(month_report)
                            st.session_state.backtest_months = month_reports
                            st.session_state.backtest_report = month_reports[-1]
                        else:
                            chosen = tuple(sorted((start.year, start.month) for start, _end in windows))
                            report = run_backtest(
                                client,
                                _request(span_start, span_end, chosen),
                                progress=_progress,
                                minute_bars=bars,
                            )
                            st.session_state.backtest_months = None
                            st.session_state.backtest_report = report
                        status.update(label="Backtest finished", state="complete")
                except Exception as exc:
                    st.error(str(exc))

    month_reports = st.session_state.get("backtest_months") or []
    if month_reports:
        wins = sum(row["wins"] for row in month_reports)
        losses = sum(row["losses"] for row in month_reports)
        resolved = wins + losses
        st.subheader(f"{today.year} by month")
        st.caption("Every month starts at the same capital. A loss in one month does not change the next month.")
        st.dataframe(
            [
                {
                    "Month": row["label"],
                    "Sessions": row["sessions"],
                    "Trades": row["trades"],
                    "Targets": row["wins"],
                    "Stops": row["losses"],
                    "Win rate": f"{row['win_rate']:.1f}%",
                    "Gross P&L": round(row["gross_pnl"], 2),
                    "Ending equity": round(row["ending_equity"], 2),
                    "Max drawdown": round(row["max_drawdown"], 2),
                    "Avg R": round(row["avg_r"], 2),
                }
                for row in month_reports
            ]
            + [
                {
                    "Month": "All months",
                    "Sessions": sum(row["sessions"] for row in month_reports),
                    "Trades": sum(row["trades"] for row in month_reports),
                    "Targets": wins,
                    "Stops": losses,
                    "Win rate": f"{(wins / resolved * 100) if resolved else 0:.1f}%",
                    "Gross P&L": round(sum(row["gross_pnl"] for row in month_reports), 2),
                    "Ending equity": None,
                    "Max drawdown": None,
                    "Avg R": round(
                        sum(row["avg_r"] * (row["wins"] + row["losses"]) for row in month_reports) / resolved, 2
                    )
                    if resolved
                    else 0,
                }
            ],
            hide_index=True,
            use_container_width=True,
        )
        labels = [row["label"] for row in month_reports]
        picked = st.selectbox("Trades for", options=labels, index=len(labels) - 1)
        report = month_reports[labels.index(picked)]
    else:
        report = st.session_state.get("backtest_report")
    if report:
        st.subheader("Report")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Gross P&L", f"₹{report['gross_pnl']:,.0f}")
        if report.get("total_brokerage"):
            open_fee = report.get("brokerage_open", 0)
            add_fee = report.get("brokerage_add", 0)
            st.caption(
                f"Brokerage included: ₹{report['total_brokerage']:,.0f} total "
                f"(open leg ₹{open_fee:,.0f}, trailing add leg ₹{add_fee:,.0f} per round trip)"
            )
        k2.metric("Trades", report["trades"])
        k3.metric("Win rate", f"{report['win_rate']:.1f}%")
        k4.metric("Max drawdown", f"₹{report['max_drawdown']:,.0f}")
        c1, c2, c3 = st.columns(3)
        c1.metric("Ending equity", f"₹{report['ending_equity']:,.0f}")
        c2.metric("Average R", f"{report['avg_r']:.2f}")
        c3.metric("Sessions", report["sessions"])
        st.caption(
            f"{report['wins']} targets · {report['losses']} stops · "
            f"{report.get('session_closes', 0)} session closes excluded from win rate and average R · "
            f"{report['symbols']} symbols"
        )

        trades_tab, days_tab, skips_tab = st.tabs(["Trades", "Days", "Skipped"])
        with trades_tab:
            rows = report["trade_rows"]
            if rows:
                from src.backtest.chart import exit_label, price_window, trade_candles, trade_levels

                reasons = sorted({row["exit_reason"] for row in rows})
                symbols = sorted({row["ticker"] for row in rows})
                f1, f2, f3 = st.columns(3)
                filter_key = f"{report.get('generated_at', '')}-{report['trades']}"
                picked_reasons = f1.multiselect(
                    "Exit",
                    options=reasons,
                    default=reasons,
                    format_func=exit_label,
                    key=f"bt_filter_exit_{filter_key}",
                )
                picked_sides = f2.multiselect(
                    "Side",
                    options=["BULLISH", "BEARISH"],
                    default=["BULLISH", "BEARISH"],
                    key=f"bt_filter_side_{filter_key}",
                )
                picked_symbols = f3.multiselect(
                    "Symbol",
                    options=symbols,
                    default=symbols,
                    key=f"bt_filter_symbol_{filter_key}",
                )
                shown = [
                    row for row in rows
                    if row["exit_reason"] in picked_reasons
                    and row["side"] in picked_sides
                    and row["ticker"] in picked_symbols
                ]

                def _trade_display_order(row: dict) -> tuple:
                    if row.get("trade_leg") == "add":
                        parent = row.get("parent_key") or ""
                        return (row["date"], parent, 1, row["entry_time"])
                    key = row.get("trade_key") or f"{row['date']}:{row['symbol']}:{row['entry_time']}"
                    return (row["date"], key, 0, row["entry_time"])

                shown.sort(key=_trade_display_order)
                st.caption(f"{len(shown)} of {len(rows)} trades")

                @st.dialog("Trade chart", width="large")
                def _trade_chart(trade: dict) -> None:
                    import altair as alt
                    import pandas as pd

                    store = st.session_state.get("bt_hist_store") or {}
                    series = (store.get("bars") or {}).get(trade["symbol"], [])
                    candles = trade_candles(
                        trade,
                        series,
                        orb_minutes=int(report.get("orb_minutes") or 15),
                        breakout_minutes=int(report.get("breakout_minutes") or 5),
                    )
                    st.markdown(
                        f"**{trade['ticker']}** · {trade['date']} · {trade['side']} · "
                        f"{exit_label(trade['exit_reason'])} at {trade['exit_time']}"
                    )
                    if not candles or trade.get("orb_high") is None:
                        st.info("Run this backtest again to load the chart for this trade.")
                        return
                    frame = pd.DataFrame(
                        [
                            {
                                "time": candle["candle_start"].strftime("%H:%M"),
                                "open": candle["open"],
                                "high": candle["high"],
                                "low": candle["low"],
                                "close": candle["close"],
                            }
                            for candle in candles
                        ]
                    )
                    levels = trade_levels(trade)
                    low, high = price_window(candles, levels)
                    scale = alt.Scale(domain=[low, high], zero=False, nice=False)
                    times = frame["time"].tolist()
                    up = alt.datum.close >= alt.datum.open
                    color = alt.condition(up, alt.value("#1f8a70"), alt.value("#c44536"))
                    x = alt.X("time:O", sort=times, title=None, axis=alt.Axis(labelAngle=0))
                    tip = ["time:O", "open:Q", "high:Q", "low:Q", "close:Q"]
                    wick = (
                        alt.Chart(frame)
                        .mark_rule()
                        .encode(x=x, y=alt.Y("low:Q", scale=scale, title="Price"), y2="high:Q", color=color, tooltip=tip)
                    )
                    body = (
                        alt.Chart(frame)
                        .mark_bar()
                        .encode(
                            x=x,
                            y=alt.Y("open:Q", scale=scale, axis=None),
                            y2="close:Q",
                            color=color,
                            tooltip=tip,
                        )
                    )
                    level_frame = pd.DataFrame(levels)
                    lines = (
                        alt.Chart(level_frame)
                        .mark_rule(strokeDash=[6, 4])
                        .encode(
                            y=alt.Y("price:Q", scale=scale, axis=None),
                            color=alt.Color(
                                "name:N",
                                scale=alt.Scale(
                                    domain=["ORB high", "ORB low", "Entry", "Stop", "Target"],
                                    range=["#3d8bfd", "#7eb6ff", "#f0c14b", "#c44536", "#1f8a70"],
                                ),
                                legend=alt.Legend(title=None, orient="top"),
                            ),
                        )
                    )
                    minutes = int(report.get("breakout_minutes") or 5)
                    st.caption(f"{minutes}-minute candles, 09:15 to {trade['exit_time']}")
                    st.altair_chart(
                        (wick + body + lines).resolve_scale(color="independent", y="shared").properties(height=420),
                        use_container_width=True,
                    )

                header = st.columns(
                    [1.1, 1.05, 0.75, 0.65, 0.72, 0.72, 0.72, 0.55, 0.65, 0.82, 0.65, 0.5, 0.62, 0.4]
                )
                for col, name in zip(
                    header,
                    [
                        "Date",
                        "Symbol",
                        "Side",
                        "Entry",
                        "Price",
                        "Stop",
                        "Target",
                        "Qty",
                        "Exit",
                        "Why",
                        "P&L",
                        "R",
                        "",
                    ],
                ):
                    col.caption(name)
                with st.container(height=460):
                    for trade in shown:
                        cols = st.columns(
                            [1.1, 1.05, 0.75, 0.65, 0.72, 0.72, 0.72, 0.55, 0.65, 0.82, 0.65, 0.5, 0.62, 0.4]
                        )
                        cols[0].write(trade["date"])
                        leg = trade.get("trade_leg", "open")
                        symbol_cell = trade["ticker"] if leg == "open" else f"↳ {trade['ticker']}"
                        cols[1].write(symbol_cell)
                        if trade.get("add_capped_by_margin"):
                            asked = int(trade.get("add_qty_requested") or trade["qty"])
                            cols[1].caption(f"Add capped by margin ({trade['qty']:,} of {asked:,} asked)")
                        cols[2].write(trade["side"].title())
                        cols[3].write(trade["entry_time"])
                        cols[4].write(f"{trade['entry']:.2f}")
                        cols[5].write(f"{trade['stop']:.2f}")
                        cols[6].write(f"{trade['target']:.2f}")
                        cols[7].write(f"{int(trade['qty']):,}")
                        cols[8].write(trade["exit_time"])
                        cols[9].write(exit_label(trade["exit_reason"]))
                        cols[10].write(f"{trade['pnl']:,.0f}")
                        cols[11].write(f"{trade['r_multiple']:.2f}")
                        chart_key = f"chart_{trade['date']}_{trade['symbol']}_{trade['entry_time']}"
                        if cols[13].button("📈", key=chart_key, help="Chart"):
                            _trade_chart(trade)
            else:
                st.caption("No trades.")
        with days_tab:
            st.dataframe(
                [
                    {
                        "Date": row["date"],
                        "Start": round(row["equity_start"], 2),
                        "Sizing capital": round(row["sizing_capital"], 2),
                        "Signals": row["signals"],
                        "Trades": row["trades"],
                        "P&L": round(row["pnl"], 2),
                        "End": round(row["equity_end"], 2),
                    }
                    for row in report["daily_rows"]
                ],
                hide_index=True,
                use_container_width=True,
                height=420,
            )
        with skips_tab:
            if report["skip_rows"]:
                st.dataframe(
                    [
                        {
                            "Date": row["date"],
                            "Symbol": row["ticker"],
                            "Side": row["side"],
                            "Time": row["time"],
                            "Entry": round(row["entry"], 2),
                            "Reason": row["reason"],
                        }
                        for row in report["skip_rows"]
                    ],
                    hide_index=True,
                    use_container_width=True,
                    height=420,
                )
            else:
                st.caption("No signals were skipped.")

    st.divider()
    st.subheader("Combination sheet")
    st.caption(
        "Upload ORB_Combinations.xlsx (Combinations sheet). January–November 2026 months are filled from each row's settings. "
        "Each row uses its own capital, margin, RRR, trailing, brokerage, and cutoff from the sheet; each month restarts at that row's capital. "
        "Total P&L, profitable months, worst month, and win rate stay as formulas. "
        "A finished row is skipped, so you can upload the same file again to continue."
    )
    sheet_file = st.file_uploader("Workbook", type=["xlsx"], key="bt_sheet_upload")
    replace_sheet = st.checkbox("Replace rows that already have January filled")
    if st.button("Run every setup in the sheet", type="primary", disabled=sheet_file is None):
        if not is_token_valid():
            st.error("Fyers token is missing. Use Strategy → Telegram: new Fyers login, then run again.")
        else:
            try:
                with st.status("Running the sheet…", expanded=True) as status:
                    note = st.empty()
                    _run_combination_sheet(sheet_file.getvalue(), replace_sheet, note.write)
                    status.update(label="Sheet finished", state="complete")
            except Exception as exc:
                st.error(str(exc))
    saved_sheet = ROOT / "data" / "sheet_runs" / "combinations_filled.xlsx"
    if not st.session_state.get("sheet_batch_bytes") and saved_sheet.exists():
        st.session_state.sheet_batch_bytes = saved_sheet.read_bytes()
        st.session_state.sheet_batch_note = "This is the last saved sheet. Rows with January filled are already done."
    if st.session_state.get("sheet_batch_note"):
        st.caption(st.session_state.sheet_batch_note)
    if st.session_state.get("sheet_batch_bytes"):
        st.download_button(
            "Download filled workbook",
            data=st.session_state.sheet_batch_bytes,
            file_name="ORB_Combinations_filled.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )