#!/usr/bin/env python3
"""Fill ORB_Combinations.xlsx from the command line (needs a valid Fyers token and watchlist in SQLite)."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytz

from src.backtest.history import load_watchlist_minutes, merge_bar_store, range_is_cached, slice_bars
from src.backtest.sheet_batch import fill_workbook, needed_universes, sheet_windows
from src.backtest.sheet_fast import iter_sheet_results
from src.broker.auth import get_access_token
from src.broker.fyers_client import get_client
from src.strategy.runtime_settings import load_settings

IST = pytz.timezone("Asia/Kolkata")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill the ORB Combinations workbook.")
    parser.add_argument(
        "workbook",
        nargs="?",
        default=str(ROOT / "ORB_Combinations.xlsx"),
        help="Input xlsx path (default: ORB_Combinations.xlsx in repo root)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=str(ROOT / "data" / "sheet_runs" / "ORB_Combinations_filled.xlsx"),
        help="Output path",
    )
    parser.add_argument("--replace", action="store_true", help="Replace rows that already have January P&L")
    args = parser.parse_args()

    source = Path(args.workbook)
    if not source.is_file():
        raise SystemExit(f"Workbook not found: {source}")

    from io import BytesIO

    from openpyxl import load_workbook

    data = source.read_bytes()
    windows = sheet_windows(datetime.now(IST).date())
    if not windows:
        raise SystemExit("No 2026 months are available yet.")
    token = get_access_token(allow_oauth=False)
    client = get_client(token)
    settings = load_settings()
    symbols_equity = list(settings.symbols)
    symbols_indices = list(settings.index_symbols or [])
    needed = needed_universes(load_workbook(BytesIO(data)), args.replace)
    bars_by_universe: dict[str, dict] = {}
    start, end = windows[0][0], windows[-1][1]
    store: dict | None = None

    def _progress(msg: str) -> None:
        print(msg, flush=True)

    for universe in sorted(needed):
        symbols = symbols_indices if universe == "indices" else symbols_equity
        if not symbols:
            raise SystemExit(f"The {universe} list is empty in strategy settings.")
        if store and range_is_cached(store, symbols, start, end):
            _progress(f"Using cached {universe} history.")
            bars = slice_bars(store["bars"], start, end)
        else:
            bars = load_watchlist_minutes(client, symbols, start, end, progress=_progress)
            store = merge_bar_store(store, symbols, start, end, bars)
        bars_by_universe[universe] = bars

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def _checkpoint(payload: bytes, *, written: int = 0) -> None:
        out_path.write_bytes(payload)
        _progress(f"Checkpoint saved -> {out_path}")

    payload, done, skipped = fill_workbook(
        data,
        replace_filled=args.replace,
        progress=_progress,
        checkpoint=_checkpoint,
        run_many=lambda pending: iter_sheet_results(pending, bars_by_universe, windows, _progress),
    )
    out_path.write_bytes(payload)
    print(f"Done. Filled {done} setups, skipped {skipped}. Output: {out_path}", flush=True)


if __name__ == "__main__":
    main()