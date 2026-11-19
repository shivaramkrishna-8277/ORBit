"""Cached 1-minute NSE history for backtests. Fyers allows 100 days per request."""
from __future__ import annotations

import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pytz

from src.utils.logger import get_logger
from src.utils.market_calendar import trading_days

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")
_CACHE = Path(__file__).resolve().parents[2] / "data" / "fyers_1m_cache.db"
_CHUNK_DAYS = 100


def _connect() -> sqlite3.Connection:
    _CACHE.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(_CACHE), timeout=60)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS minute_bars (
            symbol TEXT NOT NULL,
            ts     TEXT NOT NULL,
            open   REAL NOT NULL,
            high   REAL NOT NULL,
            low    REAL NOT NULL,
            close  REAL NOT NULL,
            volume REAL NOT NULL,
            PRIMARY KEY (symbol, ts)
        )
        """
    )
    return con


def _stamp(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = IST.localize(ts)
    else:
        ts = ts.astimezone(IST)
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _parse(stamp: str) -> datetime:
    return IST.localize(datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S"))


def _chunks(start: date, end: date):
    cursor = start
    while cursor <= end:
        stop = min(end, cursor + timedelta(days=_CHUNK_DAYS - 1))
        yield cursor, stop
        cursor = stop + timedelta(days=1)


def _missing_days(con: sqlite3.Connection, symbol: str, days: list[date]) -> list[date]:
    missing: list[date] = []
    for day in days:
        open_ts = f"{day.isoformat()} 09:15:00"
        row = con.execute(
            "SELECT 1 FROM minute_bars WHERE symbol=? AND ts=? LIMIT 1",
            (symbol, open_ts),
        ).fetchone()
        if row is None:
            missing.append(day)
    return missing


def _store_frame(con: sqlite3.Connection, symbol: str, frame) -> None:
    if frame is None or frame.empty:
        return
    rows = []
    for _, row in frame.iterrows():
        rows.append(
            (
                symbol,
                _stamp(row["datetime"].to_pydatetime()),
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row["volume"]),
            )
        )
    con.executemany(
        """INSERT OR REPLACE INTO minute_bars
           (symbol, ts, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)""",
        rows,
    )


def load_watchlist_minutes(client, symbols: list[str], start: date, end: date, progress=None) -> dict[str, list[dict]]:
    """Return {symbol: minute bars} for the range, filling the cache from Fyers when needed."""
    days = trading_days(start, end)
    out: dict[str, list[dict]] = {}
    if not symbols or not days:
        return out

    con = _connect()
    try:
        for index, symbol in enumerate(symbols, start=1):
            if progress:
                progress(f"History {index}/{len(symbols)} · {symbol}")
            missing = _missing_days(con, symbol, days)
            if missing:
                for chunk_from, chunk_to in _chunks(missing[0], missing[-1]):
                    if not any(chunk_from <= day <= chunk_to for day in missing):
                        continue
                    from src.broker.fyers_client import get_historical_candles

                    frame = get_historical_candles(
                        client,
                        symbol,
                        "1",
                        chunk_from.isoformat(),
                        chunk_to.isoformat(),
                    )
                    _store_frame(con, symbol, frame)
                    con.commit()
                    time.sleep(0.25)
            rows = con.execute(
                """SELECT ts, open, high, low, close, volume FROM minute_bars
                   WHERE symbol=? AND ts>=? AND ts<=? ORDER BY ts""",
                (symbol, f"{start.isoformat()} 00:00:00", f"{end.isoformat()} 23:59:59"),
            ).fetchall()
            out[symbol] = [
                {
                    "datetime": _parse(row[0]),
                    "open": row[1],
                    "high": row[2],
                    "low": row[3],
                    "close": row[4],
                    "volume": row[5],
                }
                for row in rows
            ]
            logger.info("Backtest history %s: %d minute bars", symbol, len(out[symbol]))
        con.commit()
    finally:
        con.close()
    return out
