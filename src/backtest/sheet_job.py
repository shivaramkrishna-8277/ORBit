"""Background combination-sheet jobs (orbot, not the Streamlit browser session)."""
from __future__ import annotations

import json
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pytz
from openpyxl import load_workbook

from src.backtest.history import load_watchlist_minutes, merge_bar_store, range_is_cached, slice_bars
from src.backtest.sheet_batch import fill_workbook, needed_universes, read_setups, sheet_windows
from src.backtest.sheet_fast import iter_sheet_results
from src.broker.auth import get_access_token
from src.broker.fyers_client import get_client
from src.strategy.runtime_settings import load_settings
from src.utils import db
from src.utils.logger import get_logger

logger = get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")

_SHEET_RUNS = Path(__file__).resolve().parent.parent.parent / "data" / "sheet_runs"
_INCOMING = _SHEET_RUNS / "incoming"
_OUTPUT = _SHEET_RUNS / "combinations_filled.xlsx"


def enqueue_sheet_batch(upload: bytes, replace_filled: bool) -> int:
    """Save the workbook and queue a server-side fill job for the trading bot."""
    if db.has_active_command("sheet_batch"):
        raise ValueError("A combination sheet job is already running or waiting.")
    _INCOMING.mkdir(parents=True, exist_ok=True)
    _SHEET_RUNS.mkdir(parents=True, exist_ok=True)
    created = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    command_id = db.enqueue_command("sheet_batch", created)
    input_path = _INCOMING / f"job_{command_id}.xlsx"
    input_path.write_bytes(upload)
    db.update_command_detail(
        command_id,
        json.dumps(
            {
                "replace": replace_filled,
                "input": str(input_path),
                "output": str(_OUTPUT),
                "progress": "Queued on the server.",
            }
        ),
    )
    logger.info("Queued sheet batch job #%s (%d bytes)", command_id, len(upload))
    return command_id


def _progress_payload(
    command_id: int,
    meta: dict,
    message: str,
    *,
    done: int | None = None,
    skipped: int | None = None,
) -> dict:
    merged = dict(meta)
    merged["progress"] = message
    if done is not None:
        merged["done"] = done
    if skipped is not None:
        merged["skipped"] = skipped
    db.update_command_detail(command_id, json.dumps(merged))
    logger.info("Sheet job #%s: %s", command_id, message)
    return merged


def run_sheet_batch_command(command_id: int) -> str:
    """Fill the uploaded workbook. Call from orbot in a background thread."""
    row = db.get_command(command_id)
    if row is None:
        raise RuntimeError("Sheet job metadata is missing.")
    try:
        meta = json.loads(row.get("detail") or "{}")
    except json.JSONDecodeError:
        meta = {}
    input_path = Path(meta.get("input") or "")
    output_path = Path(meta.get("output") or _OUTPUT)
    replace_filled = bool(meta.get("replace"))
    if not input_path.is_file():
        raise FileNotFoundError(f"Upload not found: {input_path}")

    windows = sheet_windows(datetime.now(IST).date())
    if not windows:
        raise ValueError("2026 has not started, so there are no months to fill.")

    settings = load_settings()
    symbols_equity = list(settings.symbols)
    symbols_indices = list(settings.index_symbols or [])

    def _note(message: str, *, done: int | None = None, skipped: int | None = None) -> None:
        nonlocal meta
        meta = _progress_payload(command_id, meta, message, done=done, skipped=skipped)

    _note("Starting combination sheet on the server…")
    token = get_access_token(allow_oauth=False)
    client = get_client(token)
    data = input_path.read_bytes()
    workbook = load_workbook(BytesIO(data), keep_links=False)
    setups = read_setups(workbook, progress=_note)
    needed = needed_universes(workbook, replace_filled, setups=setups)
    bars_by_universe: dict[str, dict] = {}
    start, end = windows[0][0], windows[-1][1]
    store: dict | None = None

    for universe in sorted(needed):
        symbols = symbols_indices if universe == "indices" else symbols_equity
        if not symbols:
            label = "index list" if universe == "indices" else "equity watchlist"
            raise ValueError(f"The {label} is empty in saved strategy settings.")
        if store and range_is_cached(store, symbols, start, end):
            _note(f"Using cached {universe} history ({len(symbols)} symbols).")
            bars = slice_bars(store["bars"], start, end)
        else:
            bars = load_watchlist_minutes(client, symbols, start, end, progress=_note)
            store = merge_bar_store(store, symbols, start, end, bars)
        bars_by_universe[universe] = bars
        _note(f"{universe} history ready ({len(symbols)} symbols).")

    _note("History loaded. Scoring combinations…")
    done = 0
    skipped = 0

    def _checkpoint(payload: bytes, *, written: int = 0) -> None:
        output_path.write_bytes(payload)
        _note(f"Checkpoint saved ({written} rows written).", done=written)

    payload, done, skipped = fill_workbook(
        progress=_note,
        replace_filled=replace_filled,
        checkpoint=_checkpoint,
        run_many=lambda pending: iter_sheet_results(pending, bars_by_universe, windows, _note),
        workbook=workbook,
        setups=setups,
    )
    output_path.write_bytes(payload)
    summary = f"Filled {done} setups, skipped {skipped}. File: {output_path.name}"
    meta = _progress_payload(
        command_id,
        meta,
        summary,
        done=done,
        skipped=skipped,
    )
    meta["finished_file"] = str(output_path)
    db.update_command_detail(command_id, json.dumps(meta))
    return summary