"""ORB control panel. Settings are stored in the same SQLite file the bot reads."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytz
import streamlit as st
import streamlit.components.v1 as components

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
    update_watchlist,
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
m1.metric("Tracking", len(st.session_state.symbols))
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

# ── Backtest ───────────────────────────────────────────────────────────────────

with tab_backtest:
    st.caption(
        "Replays the watchlist on 1-minute history. Entry is the breakout close, "
        "stop is the latest swing or the opening-range extreme, and the target is entry ± RRR × risk. "
        "Same-time names are ranked by volume versus the average of earlier breakout candles since the range ended. "
        "Size never exceeds the capital you enter here, and it shrinks after a losing day."
    )
    default_to = datetime.now(IST).date()
    default_from = default_to - timedelta(days=30)
    with st.form("backtest_form"):
        d1, d2, d3 = st.columns(3)
        with d1:
            bt_from = st.date_input("From", value=default_from)
            bt_capital = st.number_input("Capital (₹)", min_value=1000.0, value=100_000.0, step=10_000.0)
            bt_margin = st.selectbox("Margin", options=[4, 5, 10], index=1, format_func=lambda n: f"{n}×")
        with d2:
            bt_to = st.date_input("To", value=default_to)
            bt_risk = st.number_input("Risk per trade (%)", min_value=0.1, max_value=10.0, value=1.0, step=0.1)
            bt_day_risk = st.number_input("Risk per day (%)", min_value=0.1, max_value=20.0, value=3.0, step=0.1)
        with d3:
            bt_rrr = st.number_input("Reward : risk", min_value=0.5, max_value=10.0, value=2.0, step=0.5)
            bt_max_trades = st.number_input("Max trades per day", min_value=1, max_value=20, value=3, step=1)
            bt_price_on = st.toggle("Price filter", value=settings.max_price_enabled)

        r1, r2, r3 = st.columns(3)
        with r1:
            bt_max_price = st.number_input(
                "Max price (₹)",
                min_value=1.0,
                value=float(settings.max_stock_price),
                step=10.0,
                disabled=not bt_price_on,
            )
        with r2:
            bt_threshold = st.number_input(
                "Max ORB range (%)",
                min_value=0.05,
                max_value=20.0,
                value=float(settings.orb_range_threshold),
                step=0.05,
                format="%.2f",
            )
            bt_duration = st.selectbox(
                "Opening range from 09:15",
                options=list(ORB_DURATIONS),
                index=list(ORB_DURATIONS).index(settings.orb_duration_minutes),
                format_func=lambda m: f"{m} min (ends {_end_label(m)})",
            )
        with r3:
            bt_timeframe = st.selectbox(
                "Breakout candle size",
                options=list(BREAKOUT_TIMEFRAMES),
                index=list(BREAKOUT_TIMEFRAMES).index(settings.breakout_timeframe_minutes),
                format_func=lambda m: f"{m} minutes",
            )
        run_bt = st.form_submit_button("Run backtest", type="primary")

    if run_bt:
        if not st.session_state.symbols:
            st.error("Add symbols on the Watchlist tab first.")
        elif not is_token_valid():
            st.error("Fyers token is missing. Use Strategy → Telegram: new Fyers login, then run again.")
        else:
            from src.backtest.engine import BacktestRequest, run_backtest
            from src.broker.auth import get_access_token
            from src.broker.fyers_client import get_client

            request = BacktestRequest(
                symbols=list(st.session_state.symbols),
                date_from=bt_from,
                date_to=bt_to,
                orb_range_threshold=float(bt_threshold),
                orb_duration_minutes=int(bt_duration),
                breakout_timeframe_minutes=int(bt_timeframe),
                max_price_enabled=bool(bt_price_on),
                max_stock_price=float(bt_max_price),
                capital=float(bt_capital),
                margin_multiplier=float(bt_margin),
                risk_pct=float(bt_risk),
                day_risk_pct=float(bt_day_risk),
                max_trades_per_day=int(bt_max_trades),
                rrr=float(bt_rrr),
            )
            try:
                with st.status("Running backtest…", expanded=True) as status:
                    note = st.empty()

                    def _progress(message: str) -> None:
                        note.write(message)

                    from src.backtest.history import load_watchlist_minutes, merge_bar_store, range_is_cached, slice_bars

                    token = get_access_token(allow_oauth=False)
                    client = get_client(token)
                    store = st.session_state.get("bt_hist_store")
                    if range_is_cached(store, request.symbols, request.date_from, request.date_to):
                        _progress("Using cached history for this date range. No download.")
                        bars = slice_bars(store["bars"], request.date_from, request.date_to)
                    else:
                        bars = load_watchlist_minutes(
                            client,
                            request.symbols,
                            request.date_from,
                            request.date_to,
                            progress=_progress,
                        )
                        st.session_state.bt_hist_store = merge_bar_store(
                            store, request.symbols, request.date_from, request.date_to, bars
                        )
                    report = run_backtest(client, request, progress=_progress, minute_bars=bars)
                    st.session_state.backtest_report = report
                    status.update(label="Backtest finished", state="complete")
            except Exception as exc:
                st.error(str(exc))

    report = st.session_state.get("backtest_report")
    if report:
        st.subheader("Report")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Gross P&L", f"₹{report['gross_pnl']:,.0f}")
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

                header = st.columns([1.15, 0.9, 0.85, 0.7, 0.8, 0.8, 0.8, 0.7, 0.95, 0.7, 0.55, 0.45])
                for col, name in zip(
                    header,
                    ["Date", "Symbol", "Side", "Entry", "Price", "Stop", "Target", "Exit", "Why", "P&L", "R", ""],
                ):
                    col.caption(name)
                with st.container(height=460):
                    for trade in shown:
                        cols = st.columns([1.15, 0.9, 0.85, 0.7, 0.8, 0.8, 0.8, 0.7, 0.95, 0.7, 0.55, 0.45])
                        cols[0].write(trade["date"])
                        cols[1].write(trade["ticker"])
                        cols[2].write(trade["side"].title())
                        cols[3].write(trade["entry_time"])
                        cols[4].write(f"{trade['entry']:.2f}")
                        cols[5].write(f"{trade['stop']:.2f}")
                        cols[6].write(f"{trade['target']:.2f}")
                        cols[7].write(trade["exit_time"])
                        cols[8].write(exit_label(trade["exit_reason"]))
                        cols[9].write(f"{trade['pnl']:,.0f}")
                        cols[10].write(f"{trade['r_multiple']:.2f}")
                        chart_key = f"chart_{trade['date']}_{trade['symbol']}_{trade['entry_time']}"
                        if cols[11].button("📈", key=chart_key, help="Chart"):
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