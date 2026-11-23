"""Workbook upload fills combination results and leaves formula columns alone."""
from datetime import date
from io import BytesIO

from openpyxl import Workbook, load_workbook

from src.backtest.sheet_batch import (
    COMBINATIONS_SHEET,
    MonthResult,
    fill_workbook,
    read_setups,
    sheet_windows,
)

COMBO_HEADERS = [
    "ID",
    "Entry",
    "Second break within",
    "Symbols",
    "Opening range (from 09:15)",
    "Breakout candle size",
    "ORB range %",
    "Range qualifies when",
    "Risk per trade %",
    "Risk per day %",
    "Max trades per day",
    "Reward : risk",
    "Trailing stop loss",
    "% of distance to target",
    "Add size % of open qty",
    "Capital",
    "Margin",
    "No entry after",
    "Price filter",
    "Brokerage open leg",
    "Brokerage add leg",
    "Jan P&L",
    "Feb P&L",
    "Mar P&L",
    "Apr P&L",
    "May P&L",
    "Jun P&L",
    "Jul P&L",
    "Aug P&L",
    "Sep P&L",
    "Oct P&L",
    "Nov P&L",
    "Total Trades",
    "Total Targets",
    "Total Stops",
    "Worst Max DD",
    "Total P&L",
    "Profitable months",
    "Worst month",
    "Win rate (T/(T+S))",
]

LEGACY_HEADERS = [
    "ID",
    "Entry",
    "Second break within",
    "Symbols",
    "Opening range (from 9:15)",
    "Breakout candle",
    "ORB range %",
    "Range qualifies when",
    "Risk/trade %",
    "Risk/day %",
    "Max trades/day",
    "Jan P&L",
    "Feb P&L",
    "Mar P&L",
    "Apr P&L",
    "May P&L",
    "Jun P&L",
    "Jul P&L",
    "Aug P&L",
    "Sep P&L",
    "Oct P&L",
    "Nov P&L",
    "Total Trades",
    "Total Targets",
    "Total Stops",
    "Worst Max DD",
    "Total P&L",
    "Profitable months",
    "Worst month",
    "Win rate (T/(T+S))",
]


def _combo_book() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = COMBINATIONS_SHEET
    ws.append(COMBO_HEADERS)
    ws.append(
        [
            "C-00001",
            "Direct breakout",
            "N/A",
            "Equity watchlist",
            "5 minutes",
            "1 minute",
            0.5,
            "Greater than or equal",
            1,
            3,
            2,
            2,
            "Off",
            "N/A",
            "N/A",
            100_000,
            "5x",
            "11:59",
            "Off",
            50,
            50,
        ]
    )
    ws.append(
        [
            "C-00002",
            "Retest",
            "30 minutes of the first break",
            "Indices",
            "1 hour",
            "5 minutes",
            0.7,
            "Less than or equal",
            1.5,
            3,
            2,
            2,
            "On",
            50,
            50,
            50_000,
            "5x",
            "11:59",
            "Off",
            50,
            50,
        ]
    )
    col_aa = COMBO_HEADERS.index("Total P&L") + 1
    ws.cell(2, col_aa).value = '=IF(COUNT(V2:AF2)=0,"",SUM(V2:AF2))'
    ws.cell(2, col_aa + 1).value = '=IF(COUNT(V2:AF2)=0,"",COUNTIF(V2:AF2,">0"))'
    ws.cell(2, col_aa + 2).value = '=IF(COUNT(V2:AF2)=0,"",MIN(V2:AF2))'
    ws.cell(2, col_aa + 3).value = '=IF(OR(AG2="",AH2=""),"",IF(AG2+AH2=0,"",AG2/(AG2+AH2)))'
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _legacy_book() -> bytes:
    wb = Workbook()
    direct = wb.active
    direct.title = "Stage1_Direct"
    retest = wb.create_sheet("Stage1B_Retest")
    for ws, row in (
        (direct, ["D-001", "Direct breakout", "N/A", "Equity watchlist", "5 minutes", "1 minute", 0.5, "Greater than or equal", 1, 3, 2]),
        (retest, ["R-001", "Retest", "15 minutes of the first break", "Indices", "1 hour", "5 minutes", 0.7, "Less than or equal", 1, 3, 2]),
    ):
        ws.append(LEGACY_HEADERS)
        ws.append(row)
        ws["AA2"] = '=IF(COUNT(L2:V2)=0,"",SUM(L2:V2))'
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _results(setup):
    pnl = 10 if setup.entry_mode == "direct" else -4
    return [
        MonthResult(1, pnl, trades=3, wins=1, losses=2, max_drawdown=50),
        MonthResult(2, pnl * 2, trades=1, wins=1, losses=0, max_drawdown=80),
    ]


def test_sheet_windows_cover_january_through_november_and_clip_the_current_month():
    windows = sheet_windows(date(2026, 11, 20))
    assert [window[0].month for window in windows] == list(range(1, 12))
    assert windows[-1] == (date(2026, 11, 1), date(2026, 11, 20))
    assert sheet_windows(date(2025, 12, 31)) == []


def test_fast_sheet_matches_the_month_by_month_run():
    from src.backtest.sheet_batch import SheetSetup, run_setup_months
    from src.backtest.sheet_fast import _month_result, _prepare_day
    from tests.test_backtest import DAY, _session

    bars = {"NSE:AAA-EQ": _session(101.0, 400, next_low=100.5, next_high=110.0)}
    window = (DAY, DAY)
    prepared = {DAY: _prepare_day(bars, DAY, 5, 5, "11:59", 2.0)}

    def _check(entry_mode: str, window_minutes: int) -> None:
        setup = SheetSetup(
            sheet=COMBINATIONS_SHEET,
            row=2,
            setup_id="C-00001",
            entry_mode=entry_mode,
            retest_within_minutes=window_minutes,
            universe="watchlist",
            orb_minutes=5,
            breakout_minutes=5,
            orb_range_threshold=5.0,
            orb_range_mode="lte",
            risk_pct=1,
            day_risk_pct=3,
            max_trades=2,
        )
        slow = run_setup_months(setup, ["NSE:AAA-EQ"], [window], bars)[0]
        fast = _month_result(setup, prepared, DAY, DAY)
        assert (fast.gross_pnl, fast.trades, fast.wins, fast.losses, round(fast.max_drawdown, 2)) == (
            slow.gross_pnl,
            slow.trades,
            slow.wins,
            slow.losses,
            round(slow.max_drawdown, 2),
        )

    _check("direct", 30)
    _check("retest", 30)


def test_orb_combinations_file_parses():
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "ORB_Combinations.xlsx"
    if not path.exists():
        return
    setups = read_setups(load_workbook(path))
    assert len(setups) == 11520
    assert all(setup.sheet == COMBINATIONS_SHEET for setup in setups)
    assert {item.entry_mode for item in setups} == {"direct", "retest"}


def test_fill_writes_combinations_sheet_and_keeps_formulas():
    filled, done, skipped = fill_workbook(_combo_book(), _results, replace_filled=False)
    wb = load_workbook(BytesIO(filled))
    ws = wb[COMBINATIONS_SHEET]
    assert done == 2 and skipped == 0
    jan_col = COMBO_HEADERS.index("Jan P&L") + 1
    assert ws.cell(2, jan_col).value == 10
    assert ws.cell(3, jan_col).value == -4
    total_trades_col = COMBO_HEADERS.index("Total Trades") + 1
    assert ws.cell(2, total_trades_col).value == 4
    formula_col = COMBO_HEADERS.index("Total P&L") + 1
    assert str(ws.cell(2, formula_col).value).startswith("=IF(COUNT")
    setups = read_setups(wb)
    assert setups[1].trailing_enabled is True
    assert setups[1].capital == 50_000


def test_legacy_stage_sheets_still_parse():
    setups = read_setups(load_workbook(BytesIO(_legacy_book())))
    assert len(setups) == 2
    assert setups[0].entry_mode == "direct"
    assert setups[1].universe == "indices"