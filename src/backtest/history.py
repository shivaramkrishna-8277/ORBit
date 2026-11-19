"""Cached 1-minute NSE history for backtests. Fyers allows 100 days per request."""
from __future__ import annotations

import sqlite3
import time
from datetime import date, datetime
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
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS fetch_log (
            symbol TEXT NOT NULL,
            day    TEXT NOT NULL,
            PRIMARY KEY (symbol, day)
        )
        """
    )
    con.execute(
        """
        INSERT OR IGNORE INTO fetch_log (symbol, day)
        SELECT symbol, substr(ts, 1, 10)
        FROM minute_bars
        WHERE substr(ts, 12, 2) BETWEEN '09' AND '15'
        GROUP BY symbol, substr(ts, 1, 10)
        """
    )
    return con


def missing_spans(days: list[date], covered: set[str], *, refresh: date | None = None) -> list[tuple[date, date]]:
    """Group days that still need a download. Weekends stay inside one request."""
    missing = [day for day in days if day.isoformat() not in covered or day == refresh]
    if not missing:
        return []
    missing.sort()
    spans: list[tuple[date, date]] = []
    start = prev = missing[0]
    for day in missing[1:]:
        if (day - prev).days > 4 or (day - start).days >= _CHUNK_DAYS - 1:
            spans.append((start, prev))
            start = day
        prev = day
    spans.append((start, prev))
    return spans


def _stamp(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = IST.localize(ts)
    else:
        ts = ts.astimezone(IST)
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _parse(stamp: str) -> datetime:
    return IST.localize(datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S"))


def _covered_days(con: sqlite3.Connection, symbol: str, start: date, end: date) -> set[str]:
    """Days already stored for this symbol, from the fetch log or from the bars themselves."""
    logged = con.execute(
        "SELECT day FROM fetch_log WHERE symbol=? AND day>=? AND day<=?",
        (symbol, start.isoformat(), end.isoformat()),
    ).fetchall()
    stored = con.execute(
        """SELECT DISTINCT substr(ts, 1, 10) FROM minute_bars
           WHERE symbol=? AND ts>=? AND ts<=?""",
        (symbol, f"{start.isoformat()} 00:00:00", f"{end.isoformat()} 23:59:59"),
    ).fetchall()
    return {row[0] for row in logged} | {row[0] for row in stored}


def _session_complete(con: sqlite3.Connection, symbol: str, day: date) -> bool:
    row = con.execute(
        """SELECT 1 FROM minute_bars
           WHERE symbol=? AND ts>=? AND ts<? LIMIT 1""",
        (symbol, f"{day.isoformat()} 15:10:00", f"{day.isoformat()} 15:30:00"),
    ).fetchone()
    return row is not None


def range_is_cached(store: dict | None, symbols: list[str], start: date, end: date) -> bool:
    """True when an in-memory download already covers this watchlist and date range."""
    if not store:
        return False
    if set(symbols) - set(store.get("symbols") or ()):
        return False
    return store["start"] <= start and end <= store["end"]


def slice_bars(bars: dict[str, list[dict]], start: date, end: date) -> dict[str, list[dict]]:
    """Return minute bars inside an inclusive date range."""
    return {
        symbol: [bar for bar in series if start <= bar["datetime"].date() <= end]
        for symbol, series in bars.items()
    }


def merge_bar_store(previous: dict | None, symbols: list[str], start: date, end: date, bars: dict[str, list[dict]]) -> dict:
    """Keep the widest downloaded range for this watchlist."""
    symbols = tuple(symbols)
    if previous and set(symbols) <= set(previous.get("symbols") or ()):
        start = min(previous["start"], start)
        end = max(previous["end"], end)
        base = previous["bars"]
    else:
        base = {}
    merged: dict[str, list[dict]] = {}
    for symbol in symbols:
        by_ts = {bar["datetime"]: bar for bar in base.get(symbol, [])}
        by_ts.update({bar["datetime"]: bar for bar in bars.get(symbol, [])})
        merged[symbol] = [by_ts[key] for key in sorted(by_ts)]
    return {"symbols": symbols, "start": start, "end": end, "bars": merged}


def _mark_fetched(con: sqlite3.Connection, symbol: str, start: date, end: date) -> None:
    con.executemany(
        "INSERT OR IGNORE INTO fetch_log (symbol, day) VALUES (?, ?)",
        [(symbol, day.isoformat()) for day in trading_days(start, end)],
    )


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
            today = datetime.now(IST).date()
            covered = _covered_days(con, symbol, start, end)
            refresh = today if today in days and not _session_complete(con, symbol, today) else None
            spans = missing_spans(days, covered, refresh=refresh)
            if progress:
                state = "download" if spans else "cache"
                progress(f"History {index}/{len(symbols)} · {symbol} · {state}")
            if spans:
                from src.broker.fyers_client import fetch_historical_candles

                for chunk_from, chunk_to in spans:
                    frame, ok = fetch_historical_candles(
                        client,
                        symbol,
                        "1",
                        chunk_from.isoformat(),
                        chunk_to.isoformat(),
                    )
                    if not ok:
                        logger.warning("Backtest history left uncached for %s %s–%s", symbol, chunk_from, chunk_to)
                        continue
                    _store_frame(con, symbol, frame)
                    _mark_fetched(con, symbol, chunk_from, chunk_to)
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
