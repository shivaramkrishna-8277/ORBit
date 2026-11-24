"""Fill ORB combination workbooks. Formula columns are left untouched."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from io import BytesIO

from openpyxl import load_workbook

from src.backtest.months import month_window
from src.utils.logger import get_logger

logger = get_logger(__name__)

COMBINATIONS_SHEET = "Combinations"
LEGACY_SHEETS = ("Stage1_Direct", "Stage1B_Retest")
SHEET_YEAR = 2026
SHEET_LAST_MONTH = 11
DEFAULT_MAX_PRICE = 900.0

_ENTRY = {"direct breakout": "direct", "retest": "retest"}
_WINDOW = {
    "n/a": 30,
    "15 minutes of the first break": 15,
    "30 minutes of the first break": 30,
    "60 minutes of the first break": 60,
}
_UNIVERSE = {"equity watchlist": "watchlist", "indices": "indices"}
_ORB = {"5 minutes": 5, "15 minutes": 15, "30 minutes": 30, "1 hour": 60}
_CANDLE = {"1 minute": 1, "5 minutes": 5}
_RANGE = {"greater than or equal": "gte", "less than or equal": "lte"}
_TOGGLE = {"off": False, "on": True}

_MONTH_HEADERS = {
    1: "Jan P&L",
    2: "Feb P&L",
    3: "Mar P&L",
    4: "Apr P&L",
    5: "May P&L",
    6: "Jun P&L",
    7: "Jul P&L",
    8: "Aug P&L",
    9: "Sep P&L",
    10: "Oct P&L",
    11: "Nov P&L",
}
_FORMULA_HEADERS = ("Total P&L", "Profitable months", "Worst month", "Win rate (T/(T+S))")

# Backward-compatible aliases used in older two-sheet workbooks.
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "Opening range (from 9:15)": ("Opening range (from 09:15)", "Opening range (from 9:15)"),
    "Breakout candle": ("Breakout candle size", "Breakout candle"),
    "Risk/trade %": ("Risk per trade %", "Risk/trade %"),
    "Risk/day %": ("Risk per day %", "Risk/day %"),
    "Max trades/day": ("Max trades per day", "Max trades/day"),
}

_MARGIN_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*x$", re.I)


@dataclass
class SheetSetup:
    sheet: str
    row: int
    setup_id: str
    entry_mode: str
    retest_within_minutes: int
    universe: str
    orb_minutes: int
    breakout_minutes: int
    orb_range_threshold: float
    orb_range_mode: str
    risk_pct: float
    day_risk_pct: float
    max_trades: int
    rrr: float = 2.0
    capital: float = 100_000.0
    margin_multiplier: float = 5.0
    entry_cutoff: str = "11:59"
    max_price_enabled: bool = False
    max_stock_price: float = DEFAULT_MAX_PRICE
    trailing_enabled: bool = False
    trailing_trigger_pct: float = 50.0
    trailing_add_pct: float = 50.0
    brokerage_open: float = 50.0
    brokerage_add: float = 50.0


@dataclass
class MonthResult:
    month: int
    gross_pnl: float
    trades: int
    wins: int
    losses: int
    max_drawdown: float


def sheet_asof(today: date, year: int = SHEET_YEAR) -> date:
    """Last session the sheet can record: 30 Nov of the sheet year, or today if earlier."""
    last = date(year, SHEET_LAST_MONTH, 30)
    if today < date(year, 1, 1):
        return date(year, 1, 1) - timedelta(days=1)
    return min(today, last)


def sheet_windows(today: date, year: int = SHEET_YEAR) -> list[tuple[date, date]]:
    """January through November of the sheet year, each cut off at today."""
    asof = sheet_asof(today, year)
    windows = []
    for month in range(1, SHEET_LAST_MONTH + 1):
        window = month_window(year, month, asof)
        if window is not None:
            windows.append(window)
    return windows


def _header_row(ws) -> dict[str, int]:
    return {str(cell.value).strip(): cell.column for cell in ws[1] if cell.value is not None}


def _resolve(headers: dict[str, int], canonical: str) -> str:
    if canonical in headers:
        return canonical
    for alias in _COLUMN_ALIASES.get(canonical, ()):
        if alias in headers:
            return alias
    raise KeyError(canonical)


def _headers(ws, *, combinations: bool) -> dict[str, int]:
    found = _header_row(ws)
    required = ["ID", "Entry", *_MONTH_HEADERS.values(), "Total Trades", "Worst Max DD"]
    if combinations:
        required.extend(
            [
                "Second break within",
                "Symbols",
                _resolve(found, "Opening range (from 9:15)"),
                _resolve(found, "Breakout candle"),
                "ORB range %",
                "Range qualifies when",
                _resolve(found, "Risk/trade %"),
                _resolve(found, "Risk/day %"),
                _resolve(found, "Max trades/day"),
            ]
        )
    else:
        required.extend(
            [
                "Second break within",
                "Symbols",
                _resolve(found, "Opening range (from 9:15)"),
                _resolve(found, "Breakout candle"),
                "ORB range %",
                "Range qualifies when",
                _resolve(found, "Risk/trade %"),
                _resolve(found, "Risk/day %"),
                _resolve(found, "Max trades/day"),
            ]
        )
    missing = [name for name in required if name not in found]
    if missing:
        raise ValueError(f"{ws.title} is missing columns: {', '.join(missing)}")
    return found


def _cell(row_values: tuple, headers: dict[str, int], name: str):
    index = headers[name] - 1
    if index >= len(row_values):
        return None
    return row_values[index]


def _choice(raw, mapping: dict, label: str, setup_id: str):
    key = str(raw or "").strip().lower()
    if key not in mapping:
        raise ValueError(f"{setup_id}: {label} '{raw}' is not one of the sheet choices.")
    return mapping[key]


def _margin_multiplier(raw, setup_id: str) -> float:
    text = str(raw or "5x").strip().lower()
    match = _MARGIN_RE.match(text)
    if match:
        return float(match.group(1))
    if text in ("4", "5", "10"):
        return float(text)
    raise ValueError(f"{setup_id}: Margin '{raw}' is not understood.")


def _cutoff_text(raw) -> str:
    if isinstance(raw, datetime):
        return raw.strftime("%H:%M")
    if isinstance(raw, time):
        return raw.strftime("%H:%M")
    text = str(raw or "11:59").strip()
    if len(text) >= 5 and text[2] == ":":
        return text[:5]
    return "11:59"


def _optional_number(raw, default: float) -> float:
    text = str(raw or "").strip().lower()
    if text in ("", "n/a"):
        return default
    return float(raw)


def _parse_setup_row(
    sheet: str,
    row: int,
    row_values: tuple,
    headers: dict[str, int],
    *,
    combinations: bool,
) -> SheetSetup:
    setup_id = _cell(row_values, headers, "ID")
    if setup_id is None or str(setup_id).strip() == "":
        raise ValueError("blank id")
    setup_id = str(setup_id).strip()
    entry = _choice(_cell(row_values, headers, "Entry"), _ENTRY, "Entry", setup_id)
    window = _choice(_cell(row_values, headers, "Second break within"), _WINDOW, "Second break within", setup_id)
    orb_key = _resolve(headers, "Opening range (from 9:15)")
    candle_key = _resolve(headers, "Breakout candle")
    risk_key = _resolve(headers, "Risk/trade %")
    day_risk_key = _resolve(headers, "Risk/day %")
    max_trades_key = _resolve(headers, "Max trades/day")

    trailing_enabled = False
    trailing_trigger_pct = 50.0
    trailing_add_pct = 50.0
    capital = 100_000.0
    margin_multiplier = 5.0
    entry_cutoff = "11:59"
    rrr = 2.0
    max_price_enabled = False
    brokerage_open = 0.0
    brokerage_add = 0.0

    if combinations:
        trailing_enabled = _choice(_cell(row_values, headers, "Trailing stop loss"), _TOGGLE, "Trailing stop loss", setup_id)
        if trailing_enabled:
            trailing_trigger_pct = _optional_number(_cell(row_values, headers, "% of distance to target"), 50.0)
            trailing_add_pct = _optional_number(_cell(row_values, headers, "Add size % of open qty"), 50.0)
        capital = float(_cell(row_values, headers, "Capital"))
        margin_multiplier = _margin_multiplier(_cell(row_values, headers, "Margin"), setup_id)
        entry_cutoff = _cutoff_text(_cell(row_values, headers, "No entry after"))
        max_price_enabled = _choice(_cell(row_values, headers, "Price filter"), _TOGGLE, "Price filter", setup_id)
        rrr = float(_cell(row_values, headers, "Reward : risk"))
        brokerage_open = float(_cell(row_values, headers, "Brokerage open leg"))
        brokerage_add = float(_cell(row_values, headers, "Brokerage add leg"))

    return SheetSetup(
        sheet=sheet,
        row=row,
        setup_id=setup_id,
        entry_mode=entry,
        retest_within_minutes=window,
        universe=_choice(_cell(row_values, headers, "Symbols"), _UNIVERSE, "Symbols", setup_id),
        orb_minutes=_choice(_cell(row_values, headers, orb_key), _ORB, "Opening range", setup_id),
        breakout_minutes=_choice(_cell(row_values, headers, candle_key), _CANDLE, "Breakout candle", setup_id),
        orb_range_threshold=float(_cell(row_values, headers, "ORB range %")),
        orb_range_mode=_choice(_cell(row_values, headers, "Range qualifies when"), _RANGE, "Range qualifies when", setup_id),
        risk_pct=float(_cell(row_values, headers, risk_key)),
        day_risk_pct=float(_cell(row_values, headers, day_risk_key)),
        max_trades=int(_cell(row_values, headers, max_trades_key)),
        rrr=rrr,
        capital=capital,
        margin_multiplier=margin_multiplier,
        entry_cutoff=entry_cutoff,
        max_price_enabled=max_price_enabled,
        trailing_enabled=trailing_enabled,
        trailing_trigger_pct=trailing_trigger_pct,
        trailing_add_pct=trailing_add_pct,
        brokerage_open=brokerage_open,
        brokerage_add=brokerage_add,
    )


def _result_sheets(workbook) -> tuple[str, ...]:
    if COMBINATIONS_SHEET in workbook.sheetnames:
        return (COMBINATIONS_SHEET,)
    present = tuple(name for name in LEGACY_SHEETS if name in workbook.sheetnames)
    return present


def read_setups(workbook, progress=None) -> list[SheetSetup]:
    setups: list[SheetSetup] = []
    header_cache: dict[tuple[str, bool], dict[str, int]] = {}
    for name in _result_sheets(workbook):
        ws = workbook[name]
        combinations = name == COMBINATIONS_SHEET
        cache_key = (name, combinations)
        if cache_key not in header_cache:
            header_cache[cache_key] = _headers(ws, combinations=combinations)
        headers = header_cache[cache_key]
        last_row = int(ws.max_row or 1)
        parsed = 0
        for row_num, row_values in enumerate(
            ws.iter_rows(min_row=2, max_row=last_row, values_only=True),
            start=2,
        ):
            if not row_values or row_values[0] in (None, ""):
                continue
            try:
                setups.append(_parse_setup_row(name, row_num, row_values, headers, combinations=combinations))
            except ValueError as exc:
                if "blank id" in str(exc):
                    continue
                raise
            parsed += 1
            if progress and parsed % 2000 == 0:
                progress(f"Loaded {parsed} combination rows…")
            if parsed % 2000 == 0:
                logger.info("Parsed %d combination rows", parsed)
    if progress:
        progress(f"Loaded {len(setups)} combination rows.")
    logger.info("Parsed %d combination rows total", len(setups))
    return setups


def _filled(ws, headers: dict[str, int], row: int) -> bool:
    value = ws.cell(row, headers["Jan P&L"]).value
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def run_setup_months(setup: SheetSetup, symbols: list[str], windows: list[tuple[date, date]], bars: dict) -> list[MonthResult]:
    """Fresh capital each month."""
    from src.backtest.engine import BacktestRequest, run_backtest
    from src.backtest.history import slice_bars

    results: list[MonthResult] = []
    for start, end in windows:
        report = run_backtest(
            None,
            BacktestRequest(
                symbols=list(symbols),
                date_from=start,
                date_to=end,
                orb_range_threshold=setup.orb_range_threshold,
                orb_duration_minutes=setup.orb_minutes,
                breakout_timeframe_minutes=setup.breakout_minutes,
                max_price_enabled=setup.max_price_enabled,
                max_stock_price=setup.max_stock_price,
                capital=setup.capital,
                margin_multiplier=setup.margin_multiplier,
                risk_pct=setup.risk_pct,
                day_risk_pct=setup.day_risk_pct,
                max_trades_per_day=setup.max_trades,
                rrr=setup.rrr,
                orb_range_mode=setup.orb_range_mode,
                entry_cutoff=setup.entry_cutoff,
                entry_mode=setup.entry_mode,
                retest_within_minutes=setup.retest_within_minutes,
                include_months=((start.year, start.month),),
                trailing_enabled=setup.trailing_enabled,
                trailing_trigger_pct=setup.trailing_trigger_pct,
                trailing_add_pct=setup.trailing_add_pct,
                brokerage_open=setup.brokerage_open,
                brokerage_add=setup.brokerage_add,
            ),
            minute_bars=slice_bars(bars, start, end),
        )
        results.append(
            MonthResult(
                month=start.month,
                gross_pnl=float(report["gross_pnl"]),
                trades=int(report["trades"]),
                wins=int(report["wins"]),
                losses=int(report["losses"]),
                max_drawdown=float(report["max_drawdown"]),
            )
        )
    return results


def write_results(ws, headers: dict[str, int], row: int, results: list[MonthResult]) -> None:
    """Write month P&L and the four totals. Formula columns are not assigned."""
    for result in results:
        header = _MONTH_HEADERS.get(result.month)
        if header is None or header not in headers:
            continue
        ws.cell(row, headers[header]).value = round(float(result.gross_pnl), 2)
    ws.cell(row, headers["Total Trades"]).value = sum(result.trades for result in results)
    ws.cell(row, headers["Total Targets"]).value = sum(result.wins for result in results)
    ws.cell(row, headers["Total Stops"]).value = sum(result.losses for result in results)
    ws.cell(row, headers["Worst Max DD"]).value = round(max((result.max_drawdown for result in results), default=0.0), 2)


def needed_universes(
    workbook,
    replace_filled: bool,
    setups: list[SheetSetup] | None = None,
) -> set[str]:
    """Symbol lists still required. A row with January filled is skipped unless replace is on."""
    if setups is None:
        setups = read_setups(workbook)
    if replace_filled:
        return {setup.universe for setup in setups}
    header_cache: dict[tuple[str, bool], dict[str, int]] = {}
    needed: set[str] = set()
    for setup in setups:
        combinations = setup.sheet == COMBINATIONS_SHEET
        cache_key = (setup.sheet, combinations)
        if cache_key not in header_cache:
            header_cache[cache_key] = _headers(workbook[setup.sheet], combinations=combinations)
        if not _filled(workbook[setup.sheet], header_cache[cache_key], setup.row):
            needed.add(setup.universe)
    return needed


def fill_workbook(
    data: bytes | None = None,
    run_one=None,
    progress=None,
    replace_filled: bool = False,
    checkpoint=None,
    run_many=None,
    *,
    workbook=None,
    setups: list[SheetSetup] | None = None,
) -> tuple[bytes, int, int]:
    """
    Run every row on the Combinations sheet (or legacy Stage 1 sheets).

    `run_one(setup)` returns one MonthResult per month that has started.
    Columns Total P&L, Profitable months, Worst month, and Win rate are formulas and stay blank.
    """
    if workbook is None:
        if data is None:
            raise ValueError("Either workbook bytes or an open workbook is required.")
        if progress:
            progress("Opening workbook…")
        logger.info("Opening combination workbook")
        workbook = load_workbook(BytesIO(data), keep_links=False)
    present = _result_sheets(workbook)
    if not present:
        raise ValueError(f"The file needs a {COMBINATIONS_SHEET} sheet or the legacy Stage 1 sheets.")
    if setups is None:
        if progress:
            progress("Reading combination rows from the workbook…")
        setups = read_setups(workbook, progress=progress)
    if not setups:
        raise ValueError("That sheet has no setups to run.")
    done = 0
    skipped = 0
    header_cache: dict[tuple[str, bool], dict[str, int]] = {}
    pending: list[SheetSetup] = []
    if replace_filled:
        pending = list(setups)
        if progress:
            progress(f"Replace is on · {len(pending)} rows queued.")
    else:
        if progress:
            progress("Checking which rows still need January–November results…")
        for index, setup in enumerate(setups, start=1):
            combinations = setup.sheet == COMBINATIONS_SHEET
            cache_key = (setup.sheet, combinations)
            if cache_key not in header_cache:
                header_cache[cache_key] = _headers(workbook[setup.sheet], combinations=combinations)
            if _filled(workbook[setup.sheet], header_cache[cache_key], setup.row):
                skipped += 1
            else:
                pending.append(setup)
            if progress and index % 2000 == 0:
                progress(f"Checked {index}/{len(setups)} rows · {len(pending)} to run · {skipped} skipped")
            if index % 2000 == 0:
                logger.info(
                    "Checked %d/%d rows · %d to run · %d skipped",
                    index,
                    len(setups),
                    len(pending),
                    skipped,
                )

    if progress:
        progress(f"Queued {len(pending)} setups to run · skipped {skipped} already filled.")
    if not pending:
        buffer = BytesIO()
        workbook.save(buffer)
        return buffer.getvalue(), done, skipped

    def _write(setup: SheetSetup, results) -> None:
        nonlocal done
        ws = workbook[setup.sheet]
        combinations = setup.sheet == COMBINATIONS_SHEET
        cache_key = (setup.sheet, combinations)
        if cache_key not in header_cache:
            header_cache[cache_key] = _headers(ws, combinations=combinations)
        write_results(ws, header_cache[cache_key], setup.row, list(results))
        done += 1
        if checkpoint and (done % 10 == 0 or done == len(pending)):
            snapshot = BytesIO()
            workbook.save(snapshot)
            checkpoint(snapshot.getvalue(), written=done)

    if run_many is not None:
        by_id = {setup.setup_id: setup for setup in pending}
        for setup_id, results in run_many(pending):
            _write(by_id[setup_id], results)
    else:
        for setup in pending:
            if progress:
                progress(f"Running {setup.setup_id}")
            _write(setup, run_one(setup))
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue(), done, skipped