"""Workbook upload fills Stage 1 results and leaves formula columns alone."""
from datetime import date
from io import BytesIO

from openpyxl import Workbook, load_workbook

from src.backtest.sheet_batch import (
    MonthResult,
    fill_workbook,
    read_setups,
    sheet_windows,
)

HEADERS = [
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


def _book() -> bytes:
    wb = Workbook()
    direct = wb.active
    direct.title = "Stage1_Direct"
    retest = wb.create_sheet("Stage1B_Retest")
    risk = wb.create_sheet("Risk_Grid")
    risk["A1"] = "Risk ID"
    risk["A2"] = "R-01"
    for ws, row in (
        (direct, ["D-001", "Direct breakout", "N/A", "Equity watchlist", "5 minutes", "1 minute", 0.5, "Greater than or equal", 1, 3, 2]),
        (retest, ["R-001", "Retest", "15 minutes of the first break", "Indices", "1 hour", "5 minutes", 0.7, "Less than or equal", 1, 3, 2]),
    ):
        ws.append(HEADERS)
        ws.append(row)
        ws["AA2"] = '=IF(COUNT(L2:V2)=0,"",SUM(L2:V2))'
        ws["AB2"] = '=IF(COUNT(L2:V2)=0,"",COUNTIF(L2:V2,">0"))'
        ws["AC2"] = '=IF(COUNT(L2:V2)=0,"",MIN(L2:V2))'
        ws["AD2"] = '=IF(OR(X2="",Y2=""),"",IF(X2+Y2=0,"",X2/(X2+Y2)))'
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


def test_real_stage_file_parses_both_sheets():
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "Backtest ReadMe.xlsx"
    if not path.exists():
        return
    setups = read_setups(load_workbook(path))
    assert len([item for item in setups if item.sheet == "Stage1_Direct"]) == 96
    assert len([item for item in setups if item.sheet == "Stage1B_Retest"]) == 288
    assert {item.entry_mode for item in setups} == {"direct", "retest"}


def test_fill_writes_stage_sheets_and_keeps_formulas():
    filled, done, skipped = fill_workbook(_book(), _results, replace_filled=False)
    wb = load_workbook(BytesIO(filled))
    direct = wb["Stage1_Direct"]
    retest = wb["Stage1B_Retest"]
    assert done == 2 and skipped == 0
    assert direct["L2"].value == 10
    assert direct["M2"].value == 20
    assert direct["W2"].value == 4
    assert direct["X2"].value == 2
    assert direct["Y2"].value == 2
    assert direct["Z2"].value == 80
    assert direct["AA2"].value.startswith("=IF(COUNT")
    assert direct["AD2"].value.startswith("=IF(OR")
    assert retest["L2"].value == -4
    assert retest["C2"].value == "15 minutes of the first break"
    assert wb["Risk_Grid"]["A2"].value == "R-01"
    setups = read_setups(wb)
    assert [item.setup_id for item in setups] == ["D-001", "R-001"]
    assert setups[0].entry_mode == "direct"
    assert setups[1].universe == "indices"
    assert setups[1].orb_minutes == 60
    assert setups[1].retest_within_minutes == 15

    again, done_again, skipped_again = fill_workbook(filled, _results)
    assert done_again == 0 and skipped_again == 2
    replaced, done_replaced, skipped_replaced = fill_workbook(filled, _results, replace_filled=True)
    assert done_replaced == 2 and skipped_replaced == 0
    assert load_workbook(BytesIO(replaced))["Stage1_Direct"]["AA2"].value.startswith("=")
