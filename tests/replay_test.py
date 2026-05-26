"""Replay test — runs the full ORB pipeline on historical data.

Usage:
    python tests/replay_test.py --date 2026-05-21          # single day
    python tests/replay_test.py --month 2026-05            # full month

Prints all signals that would have fired without sending Telegram messages.
"""
import argparse
import calendar
import sys
from datetime import datetime
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytz

IST = pytz.timezone("Asia/Kolkata")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ORB strategy replay on historical data")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--date",  help="Single day  (YYYY-MM-DD, e.g. 2026-05-21)")
    group.add_argument("--month", help="Full month   (YYYY-MM,    e.g. 2026-05)")
    group.add_argument("--year",  help="Full year    (YYYY,       e.g. 2026)")
    return parser.parse_args()


def _authenticated_client():
    """
    Return a ready Fyers client, automatically re-running the OAuth flow if the
    cached token has expired (Fyers error code -16).

    Uses a direct history() probe (no retry) on a single symbol so we detect
    the same auth error that the actual data fetch would hit.
    """
    from datetime import date as _date
    from src.broker.auth import get_access_token, generate_access_token
    from src.broker.fyers_client import get_client

    for attempt in range(2):
        token = get_access_token() if attempt == 0 else generate_access_token()
        client = get_client(token)

        # One-shot history() probe — no retry, same endpoint as real fetches
        today = _date.today().strftime("%Y-%m-%d")
        probe = client.history({
            "symbol": "NSE:SBIN-EQ",
            "resolution": "D",
            "date_format": "1",
            "range_from": today,
            "range_to": today,
            "cont_flag": "1",
        })
        if isinstance(probe, dict) and probe.get("code", 200) == -16:
            if attempt == 0:
                print("\nAccess token expired — starting re-authentication…\n")
                continue
            raise RuntimeError("Authentication failed even after re-auth. Check your Fyers credentials.")

        return client

    raise RuntimeError("Authentication failed.")  # unreachable, satisfies type checkers


class _InMemoryDB:
    """In-memory DB stub for replay — no persistence, clean slate every run."""
    def __init__(self):
        self._fired: set[tuple] = set()
        self._next_id = 1

    def has_signal_fired(self, date, symbol, signal_type):
        return (date, symbol) in self._fired

    def insert_signal(self, date, symbol, signal_type, candle_close,
                      orb_level, move_pct, triggered_at):
        self._fired.add((date, symbol))
        sid = self._next_id
        self._next_id += 1
        return sid

    def mark_signal_alerted(self, signal_id): pass
    def insert_orb_level(self, *a, **kw): pass
    def insert_candle(self, *a, **kw): pass


def replay(date_str: str) -> None:
    from src import config
    from src.broker.fyers_client import get_historical_candles
    from src.strategy.candle_builder import CandleBuilder
    from src.strategy.orb_calculator import ORBCalculator
    from src.strategy.breakout_detector import BreakoutDetector
    from src.utils.db import init_db

    init_db()

    print(f"\n{'=' * 60}")
    print(f"ORB REPLAY — {date_str}")
    print(f"Price filter: ≤ ₹{config.MAX_STOCK_PRICE:.0f} | ORB threshold: ≤ {config.ORB_RANGE_THRESHOLD}%")
    print(f"{'=' * 60}\n")

    # Auth (auto re-runs OAuth flow if cached token is expired)
    client = _authenticated_client()

    # Fetch 15-minute candles for all Nifty 50 symbols for the date
    print(f"Fetching candles for {len(config.NIFTY50_SYMBOLS)} symbols…")
    all_candles: dict[str, list[dict]] = {}

    for symbol in config.NIFTY50_SYMBOLS:
        df = get_historical_candles(
            client=client,
            symbol=symbol,
            resolution="15",
            date_from=date_str,
            date_to=date_str,
        )
        if df.empty:
            continue
        # Convert DataFrame rows to candle dicts
        rows = []
        for _, row in df.iterrows():
            rows.append({
                "candle_start": row["datetime"],
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
            })
        all_candles[symbol] = rows

    if not all_candles:
        print("No candle data returned. Check your date (must be a trading day) and API connection.")
        return

    print(f"Received candles for {len(all_candles)} symbols.\n")

    # --- Price filter: keep only symbols whose first candle open < MAX_STOCK_PRICE ---
    orb_start = IST.localize(datetime.strptime(f"{date_str} 09:15", "%Y-%m-%d %H:%M"))
    price_filtered: dict[str, list[dict]] = {}
    for symbol, candles in all_candles.items():
        first = next((c for c in candles if c["candle_start"].astimezone(IST) == orb_start), None)
        if first and first["open"] < config.MAX_STOCK_PRICE:
            price_filtered[symbol] = candles
    print(
        f"Price filter (< ₹{config.MAX_STOCK_PRICE:.0f}): "
        f"{len(price_filtered)} of {len(all_candles)} symbols qualify.\n"
    )
    all_candles = price_filtered

    # --- Step 1: identify the first candle (9:15–9:30) for each symbol -------
    first_candles: dict[str, dict] = {}

    for symbol, candles in all_candles.items():
        for c in candles:
            if c["candle_start"].astimezone(IST) == orb_start:
                first_candles[symbol] = c
                break

    print(f"First candles found: {len(first_candles)}")

    # --- Patch modules with in-memory DB (avoids stale has_signal_fired hits) -
    import logging
    import src.strategy.breakout_detector as _bd_module
    import src.strategy.orb_calculator as _orb_module
    _replay_db = _InMemoryDB()
    _bd_module.db = _replay_db
    _orb_module.db = _replay_db
    logging.getLogger("src.strategy.orb_calculator").setLevel(logging.WARNING)
    logging.getLogger("src.strategy.breakout_detector").setLevel(logging.WARNING)

    # --- Step 2: ORB filter ---------------------------------------------------
    calc = ORBCalculator()
    orb_levels: dict[str, dict] = {}
    dropped = 0

    for symbol, candle in first_candles.items():
        result = calc.calculate_orb(symbol, candle)
        if result:
            orb_levels[symbol] = {"orb_high": result["orb_high"], "orb_low": result["orb_low"]}
        else:
            dropped += 1

    print(f"ORB filter: {len(orb_levels)} passed, {dropped} dropped\n")

    # --- Step 3: replay candles through breakout detector --------------------
    signals: list[dict] = []

    def on_breakout(signal: dict) -> None:
        signals.append(signal)

    detector = BreakoutDetector(orb_levels=orb_levels, on_breakout_callback=on_breakout)

    # Feed candles in chronological order (skip the 9:15 first candle)
    for symbol, candles in all_candles.items():
        if symbol not in orb_levels:
            continue
        for c in candles:
            candle_time = c["candle_start"].astimezone(IST)
            if candle_time <= orb_start:
                continue  # skip the ORB candle itself
            detector.check_candle(symbol, c)

    # --- Step 4: print results -----------------------------------------------
    print(f"{'─' * 60}")
    print(f"SIGNALS GENERATED: {len(signals)}")
    print(f"{'─' * 60}")

    if not signals:
        print("No breakout signals for this date.")
    else:
        header = f"{'Symbol':<22} {'Type':<10} {'Close':>8} {'ORB':>8} {'Move%':>7} {'Time':<12}"
        print(header)
        print("─" * len(header))
        for s in signals:
            sym = s["symbol"].replace("NSE:", "").replace("-EQ", "")
            direction = "+" if s["signal_type"] == "BULLISH" else "-"
            print(
                f"{sym:<22} {s['signal_type']:<10} "
                f"{s['candle_close']:>8.2f} {s['orb_level']:>8.2f} "
                f"{direction}{s['move_pct']:>6.2f}% {s['candle_time']:<12}"
            )

    print(f"\n{'─' * 60}")
    print("ORB LEVELS SUMMARY")
    print(f"{'─' * 60}")
    orb_header = f"{'Symbol':<22} {'ORB High':>10} {'ORB Low':>10} {'Range%':>8} {'Signal?':<10}"
    print(orb_header)
    print("─" * len(orb_header))
    fired_symbols = {s["symbol"] for s in signals}
    for symbol, levels in sorted(orb_levels.items()):
        sym = symbol.replace("NSE:", "").replace("-EQ", "")
        orb_h = levels["orb_high"]
        orb_l = levels["orb_low"]
        range_pct = (orb_h - orb_l) / orb_l * 100
        fired = "YES" if symbol in fired_symbols else "-"
        print(f"{sym:<22} {orb_h:>10.2f} {orb_l:>10.2f} {range_pct:>7.2f}% {fired:<10}")

    print()


def _process_date_range(
    client,
    config,
    date_from: str,
    date_to: str,
    replay_db: "_InMemoryDB",
) -> tuple[list[dict], list[dict]]:
    # Fetch 15-min candles for [date_from, date_to] and run the full ORB pipeline.
    # Returns (all_signals, day_rows).  Silent -- no printing.
    import logging
    import src.strategy.breakout_detector as _bd_module
    import src.strategy.orb_calculator as _orb_module
    from src.broker.fyers_client import get_historical_candles
    from src.strategy.orb_calculator import ORBCalculator
    from src.strategy.breakout_detector import BreakoutDetector

    _bd_module.db = replay_db
    _orb_module.db = replay_db
    logging.getLogger("src.strategy.orb_calculator").setLevel(logging.WARNING)
    logging.getLogger("src.strategy.breakout_detector").setLevel(logging.WARNING)

    symbol_candles: dict[str, list[dict]] = {}
    for symbol in config.NIFTY50_SYMBOLS:
        df = get_historical_candles(
            client=client, symbol=symbol, resolution="15",
            date_from=date_from, date_to=date_to,
        )
        if not df.empty:
            symbol_candles[symbol] = [
                {
                    "candle_start": row["datetime"],
                    "open":  float(row["open"]),
                    "high":  float(row["high"]),
                    "low":   float(row["low"]),
                    "close": float(row["close"]),
                    "volume": row["volume"],
                }
                for _, row in df.iterrows()
            ]

    dates_set: set[str] = set()
    for candles in symbol_candles.values():
        for c in candles:
            dates_set.add(c["candle_start"].astimezone(IST).strftime("%Y-%m-%d"))

    all_signals: list[dict] = []
    day_rows:    list[dict] = []

    for date_str in sorted(dates_set):
        orb_start = IST.localize(datetime.strptime(f"{date_str} 09:15", "%Y-%m-%d %H:%M"))

        day_candles: dict[str, list[dict]] = {
            sym: [c for c in clist
                  if c["candle_start"].astimezone(IST).strftime("%Y-%m-%d") == date_str]
            for sym, clist in symbol_candles.items()
        }
        day_candles = {k: v for k, v in day_candles.items() if v}

        price_ok: dict[str, list[dict]] = {}
        for sym, clist in day_candles.items():
            first = next((c for c in clist
                          if c["candle_start"].astimezone(IST) == orb_start), None)
            if first and first["open"] < config.MAX_STOCK_PRICE:
                price_ok[sym] = clist

        first_candles: dict[str, dict] = {
            sym: next((c for c in clist
                       if c["candle_start"].astimezone(IST) == orb_start), None)
            for sym, clist in price_ok.items()
        }
        first_candles = {k: v for k, v in first_candles.items() if v}

        calc = ORBCalculator()
        orb_levels: dict[str, dict] = {}
        for symbol, candle in first_candles.items():
            result = calc.calculate_orb(symbol, candle)
            if result:
                orb_levels[symbol] = {
                    "orb_high": result["orb_high"],
                    "orb_low":  result["orb_low"],
                }

        day_signals: list[dict] = []

        def _on_breakout(signal: dict, _ds: str = date_str) -> None:
            s = signal.copy()
            s["date"] = _ds
            day_signals.append(s)
            all_signals.append(s)

        detector = BreakoutDetector(orb_levels=orb_levels, on_breakout_callback=_on_breakout)
        for symbol, candles in price_ok.items():
            if symbol not in orb_levels:
                continue
            for c in candles:
                if c["candle_start"].astimezone(IST) <= orb_start:
                    continue
                detector.check_candle(symbol, c)

        day_rows.append({"date": date_str, "qualified": len(orb_levels), "signals": len(day_signals)})

    return all_signals, day_rows


def _print_signals_table(all_signals: list[dict], title: str, W: int = 72) -> None:
    print(f"{'─' * W}")
    print(f"SIGNALS — {title}  ({len(all_signals)} total)")
    print(f"{'─' * W}")
    if not all_signals:
        print("No breakout signals.")
        return
    hdr = (f"{'Date':<12} {'Symbol':<12} {'Type':<10}"
           f" {'Close':>8} {'ORB Lvl':>8} {'Move%':>7}  {'Alert Candle'}")
    print(hdr)
    print("─" * W)
    for s in all_signals:
        sym = s["symbol"].replace("NSE:", "").replace("-EQ", "")
        arrow     = "▲" if s["signal_type"] == "BULLISH" else "▼"
        direction = "+" if s["signal_type"] == "BULLISH" else "-"
        print(
            f"{s['date']:<12} {sym:<12} {arrow} {s['signal_type']:<8}"
            f" {s['candle_close']:>8.2f} {s['orb_level']:>8.2f}"
            f" {direction}{s['move_pct']:>5.2f}%  {s['candle_time']}"
        )


def replay_month(month_str: str) -> None:
    """Replay every trading day in a month.  One API call per symbol."""
    from src import config
    from src.utils.db import init_db
    import src.strategy.breakout_detector as _bd_module
    import src.strategy.orb_calculator as _orb_module

    init_db()

    year, month_num = int(month_str[:4]), int(month_str[5:7])
    last_day = calendar.monthrange(year, month_num)[1]
    date_from = f"{year}-{month_num:02d}-01"
    date_to   = f"{year}-{month_num:02d}-{last_day:02d}"
    month_label = datetime.strptime(month_str, "%Y-%m").strftime("%B %Y")

    W = 72
    print(f"\n{'=' * W}")
    print(f"ORB MONTHLY REPLAY — {month_label}")
    print(f"Price filter: ≤ ₹{config.MAX_STOCK_PRICE:.0f} | ORB threshold: ≤ {config.ORB_RANGE_THRESHOLD}%")
    print(f"{'=' * W}\n")

    client = _authenticated_client()
    _replay_db = _InMemoryDB()
    _bd_module.db = _replay_db
    _orb_module.db = _replay_db

    print(f"Fetching 15-min candles for {len(config.NIFTY50_SYMBOLS)} symbols "
          f"({date_from} → {date_to})…")
    all_signals, day_rows = _process_date_range(client, config, date_from, date_to, _replay_db)
    print(f"Processed {len(day_rows)} trading days.\n")

    _print_signals_table(all_signals, month_label, W)

    print(f"\n{'─' * W}")
    print(f"DAILY SUMMARY — {month_label}")
    print(f"{'─' * W}")
    print(f"{'Date':<12} {'Qualified':>10} {'Signals':>8}  Detail")
    print("─" * W)
    for row in day_rows:
        fired_today = [s for s in all_signals if s["date"] == row["date"]]
        detail = (
            "—" if not fired_today else
            "  ".join(
                f"{s['symbol'].replace('NSE:','').replace('-EQ','')}"
                f" ({'▲' if s['signal_type']=='BULLISH' else '▼'})"
                for s in fired_today
            )
        )
        print(f"{row['date']:<12} {row['qualified']:>10} {row['signals']:>8}  {detail}")

    hit_days = sum(1 for r in day_rows if r["signals"] > 0)
    print(f"\n{'─' * W}")
    print(f"Total: {len(all_signals)} signal(s) across {len(day_rows)} trading day(s) "
          f"| Signal days: {hit_days}/{len(day_rows)}")
    print()


def replay_year(year_str: str) -> None:
    """Replay all trading days in a year, split into 4 quarterly API batches."""
    from src import config
    from src.utils.db import init_db
    from collections import defaultdict
    import src.strategy.breakout_detector as _bd_module
    import src.strategy.orb_calculator as _orb_module

    init_db()

    year = int(year_str)
    W = 72
    print(f"\n{'=' * W}")
    print(f"ORB YEARLY REPLAY — {year}")
    print(f"Price filter: ≤ ₹{config.MAX_STOCK_PRICE:.0f} | ORB threshold: ≤ {config.ORB_RANGE_THRESHOLD}%")
    print(f"{'=' * W}\n")

    client = _authenticated_client()
    _replay_db = _InMemoryDB()
    _bd_module.db = _replay_db
    _orb_module.db = _replay_db

    # Four quarterly batches to respect Fyers' ~100-day limit for 15-min data
    quarters = [
        (f"{year}-01-01", f"{year}-03-31", "Q1 (Jan–Mar)"),
        (f"{year}-04-01", f"{year}-06-30", "Q2 (Apr–Jun)"),
        (f"{year}-07-01", f"{year}-09-30", "Q3 (Jul–Sep)"),
        (f"{year}-10-01", f"{year}-12-31", "Q4 (Oct–Dec)"),
    ]

    all_signals: list[dict] = []
    day_rows:    list[dict] = []

    for q_from, q_to, label in quarters:
        print(f"Fetching {label} ({q_from} → {q_to}) for {len(config.NIFTY50_SYMBOLS)} symbols…")
        q_signals, q_rows = _process_date_range(client, config, q_from, q_to, _replay_db)
        all_signals.extend(q_signals)
        day_rows.extend(q_rows)
        print(f"  → {len(q_rows)} trading days, {len(q_signals)} signal(s)")

    print(f"\nAll quarters done: {len(day_rows)} trading days total.\n")

    _print_signals_table(all_signals, str(year), W)

    month_days:    defaultdict = defaultdict(int)
    month_signals: defaultdict = defaultdict(int)
    month_hit:     defaultdict = defaultdict(int)
    for row in day_rows:
        m = row["date"][:7]
        month_days[m]    += 1
        month_signals[m] += row["signals"]
        if row["signals"] > 0:
            month_hit[m] += 1

    print(f"\n{'─' * W}")
    print(f"MONTHLY SUMMARY — {year}")
    print(f"{'─' * W}")
    print(f"{'Month':<12} {'Trade Days':>11} {'Signal Days':>12} {'Signals':>9}")
    print("─" * W)
    for m in sorted(month_days.keys()):
        print(f"{m:<12} {month_days[m]:>11} {month_hit[m]:>12} {month_signals[m]:>9}")

    hit_days = sum(1 for r in day_rows if r["signals"] > 0)
    print(f"\n{'─' * W}")
    print(f"Total: {len(all_signals)} signal(s) across {len(day_rows)} trading day(s) "
          f"| Signal days: {hit_days}/{len(day_rows)}")
    print()


if __name__ == "__main__":
    args = parse_args()
    if args.date:
        replay(args.date)
    elif args.month:
        replay_month(args.month)
    else:
        replay_year(args.year)
