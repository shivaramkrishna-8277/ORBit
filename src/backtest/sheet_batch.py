"""Fill Stage 1 backtest workbooks. Formula columns are left untouched."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from io import BytesIO

from openpyxl import load_workbook

from src.backtest.months import month_window

RESULT_SHEETS = ("Stage1_Direct", "Stage1B_Retest")
SHEET_YEAR = 2026
SHEET_LAST_MONTH = 11
CAPITAL = 100_000.0
MARGIN = 5.0
RRR = 2.0
ENTRY_CUTOFF = "11:59"

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


def _headers(ws) -> dict[str, int]:
    found = {}
    for cell in ws[1]:
        if cell.value:
            found[str(cell.value).strip()] = cell.column
    missing = [name for name in ("ID", "Entry", *_MONTH_HEADERS.values(), "Total Trades", "Worst Max DD") if name not in found]
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


def read_setups(workbook) -> list[SheetSetup]:
    setups: list[SheetSetup] = []
    for name in RESULT_SHEETS:
        if name not in workbook.sheetnames:
            continue
        ws = workbook[name]
        headers = _headers(ws)
        for cells in ws.iter_rows(min_row=2, max_row=ws.max_row):
            row_values = tuple(cell.value for cell in cells)
            setup_id = _cell(row_values, headers, "ID")
            if setup_id is None or str(setup_id).strip() == "":
                continue
            setup_id = str(setup_id).strip()
            entry = _choice(_cell(row_values, headers, "Entry"), _ENTRY, "Entry", setup_id)
            window = _choice(_cell(row_values, headers, "Second break within"), _WINDOW, "Second break within", setup_id)
            setups.append(
                SheetSetup(
                    sheet=name,
                    row=cells[0].row,
                    setup_id=setup_id,
                    entry_mode=entry,
                    retest_within_minutes=window,
                    universe=_choice(_cell(row_values, headers, "Symbols"), _UNIVERSE, "Symbols", setup_id),
                    orb_minutes=_choice(
                        _cell(row_values, headers, "Opening range (from 9:15)"), _ORB, "Opening range", setup_id
                    ),
                    breakout_minutes=_choice(
                        _cell(row_values, headers, "Breakout candle"), _CANDLE, "Breakout candle", setup_id
                    ),
                    orb_range_threshold=float(_cell(row_values, headers, "ORB range %")),
                    orb_range_mode=_choice(
                        _cell(row_values, headers, "Range qualifies when"), _RANGE, "Range qualifies when", setup_id
                    ),
                    risk_pct=float(_cell(row_values, headers, "Risk/trade %")),
                    day_risk_pct=float(_cell(row_values, headers, "Risk/day %")),
                    max_trades=int(_cell(row_values, headers, "Max trades/day")),
                )
            )
    return setups


def _filled(ws, headers: dict[str, int], row: int) -> bool:
    value = ws.cell(row, headers["Jan P&L"]).value
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def run_setup_months(setup: SheetSetup, symbols: list[str], windows: list[tuple[date, date]], bars: dict) -> list[MonthResult]:
    """Fresh capital each month. Price filter stays off, matching the sheet's fixed settings."""
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
                max_price_enabled=False,
                max_stock_price=1,
                capital=CAPITAL,
                margin_multiplier=MARGIN,
                risk_pct=setup.risk_pct,
                day_risk_pct=setup.day_risk_pct,
                max_trades_per_day=setup.max_trades,
                rrr=RRR,
                orb_range_mode=setup.orb_range_mode,
                entry_cutoff=ENTRY_CUTOFF,
                entry_mode=setup.entry_mode,
                retest_within_minutes=setup.retest_within_minutes,
                include_months=((start.year, start.month),),
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


def fill_workbook(
    data: bytes,
    run_one,
    progress=None,
    replace_filled: bool = False,
    checkpoint=None,
) -> tuple[bytes, int, int]:
    """
    Run every Stage1_Direct and Stage1B_Retest row.

    `run_one(setup)` returns one MonthResult per month that has started.
    Columns Total P&L, Profitable months, Worst month, and Win rate are formulas and stay blank.
    """
    workbook = load_workbook(BytesIO(data))
    present = [name for name in RESULT_SHEETS if name in workbook.sheetnames]
    if not present:
        raise ValueError("The file needs a Stage1_Direct or Stage1B_Retest sheet.")
    setups = read_setups(workbook)
    if not setups:
        raise ValueError("Those sheets have no setups to run.")
    done = 0
    skipped = 0
    for index, setup in enumerate(setups, start=1):
        ws = workbook[setup.sheet]
        headers = _headers(ws)
        if not replace_filled and _filled(ws, headers, setup.row):
            skipped += 1
            if progress:
                progress(f"Skipped {setup.setup_id} ({index}/{len(setups)}), January is already filled.")
            continue
        if progress:
            progress(f"Running {setup.setup_id} ({index}/{len(setups)})")
        results = list(run_one(setup))
        write_results(ws, headers, setup.row, results)
        done += 1
        if checkpoint:
            snapshot = BytesIO()
            workbook.save(snapshot)
            checkpoint(snapshot.getvalue())
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue(), done, skipped
