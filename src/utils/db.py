"""Database layer — SQLite at data/orb_tracker.db.

All tables are created on first run via init_db().
Use the exposed functions; never import sqlite3 directly from other modules.
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger(__name__)

_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "orb_tracker.db"
_DB_PATH.parent.mkdir(exist_ok=True)


# ── Connection helper ─────────────────────────────────────────────────────────

@contextmanager
def _conn():
    """Yield a thread-safe SQLite connection with WAL mode enabled."""
    con = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS daily_watchlist (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        date        TEXT    NOT NULL,
        symbol      TEXT    NOT NULL,
        ltp         REAL    NOT NULL,
        UNIQUE(date, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orb_levels (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        date          TEXT    NOT NULL,
        symbol        TEXT    NOT NULL,
        orb_high      REAL    NOT NULL,
        orb_low       REAL    NOT NULL,
        range_pct     REAL    NOT NULL,
        passed_filter INTEGER NOT NULL DEFAULT 0,
        UNIQUE(date, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signals (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        date         TEXT    NOT NULL,
        symbol       TEXT    NOT NULL,
        signal_type  TEXT    NOT NULL,   -- BULLISH | BEARISH
        candle_close REAL    NOT NULL,
        orb_level    REAL    NOT NULL,
        move_pct     REAL    NOT NULL,
        triggered_at TEXT    NOT NULL,
        alerted      INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS candle_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        date        TEXT    NOT NULL,
        symbol      TEXT    NOT NULL,
        candle_time TEXT    NOT NULL,
        open        REAL    NOT NULL,
        high        REAL    NOT NULL,
        low         REAL    NOT NULL,
        close       REAL    NOT NULL,
        volume      REAL    NOT NULL DEFAULT 0
    )
    """,
]


def init_db() -> None:
    """Create all tables if they don't exist. Safe to call on every startup."""
    with _conn() as con:
        for ddl in _DDL:
            con.execute(ddl)
    logger.info("Database initialised at %s", _DB_PATH)


# ── daily_watchlist ───────────────────────────────────────────────────────────

def insert_watchlist(date: str, symbol: str, ltp: float) -> None:
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO daily_watchlist (date, symbol, ltp) VALUES (?,?,?)",
            (date, symbol, ltp),
        )


def get_watchlist(date: str) -> list[str]:
    with _conn() as con:
        rows = con.execute(
            "SELECT symbol FROM daily_watchlist WHERE date=? ORDER BY symbol",
            (date,),
        ).fetchall()
    return [r["symbol"] for r in rows]


# ── orb_levels ────────────────────────────────────────────────────────────────

def insert_orb_level(
    date: str, symbol: str, orb_high: float, orb_low: float, range_pct: float, passed: int
) -> None:
    with _conn() as con:
        con.execute(
            """INSERT OR REPLACE INTO orb_levels
               (date, symbol, orb_high, orb_low, range_pct, passed_filter)
               VALUES (?,?,?,?,?,?)""",
            (date, symbol, orb_high, orb_low, range_pct, passed),
        )


def get_orb_levels(date: str) -> dict[str, dict]:
    """Return {symbol: {orb_high, orb_low}} for passed symbols on date."""
    with _conn() as con:
        rows = con.execute(
            "SELECT symbol, orb_high, orb_low FROM orb_levels WHERE date=? AND passed_filter=1",
            (date,),
        ).fetchall()
    return {r["symbol"]: {"orb_high": r["orb_high"], "orb_low": r["orb_low"]} for r in rows}


# ── signals ───────────────────────────────────────────────────────────────────

def insert_signal(
    date: str,
    symbol: str,
    signal_type: str,
    candle_close: float,
    orb_level: float,
    move_pct: float,
    triggered_at: str,
) -> int:
    """Insert a new signal row and return its id."""
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO signals
               (date, symbol, signal_type, candle_close, orb_level, move_pct, triggered_at, alerted)
               VALUES (?,?,?,?,?,?,?,0)""",
            (date, symbol, signal_type, candle_close, orb_level, move_pct, triggered_at),
        )
        return cur.lastrowid


def has_signal_fired(date: str, symbol: str, signal_type: str) -> bool:
    """Return True if this symbol already fired a signal of this type today."""
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM signals WHERE date=? AND symbol=? AND signal_type=? LIMIT 1",
            (date, symbol, signal_type),
        ).fetchone()
    return row is not None


def mark_signal_alerted(signal_id: int) -> None:
    with _conn() as con:
        con.execute("UPDATE signals SET alerted=1 WHERE id=?", (signal_id,))


def get_signals(date: str) -> list[dict]:
    """Return all signals for a date as a list of dicts."""
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM signals WHERE date=? ORDER BY triggered_at",
            (date,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── candle_log ────────────────────────────────────────────────────────────────

def insert_candle(
    date: str,
    symbol: str,
    candle_time: str,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 0.0,
) -> None:
    with _conn() as con:
        con.execute(
            """INSERT INTO candle_log
               (date, symbol, candle_time, open, high, low, close, volume)
               VALUES (?,?,?,?,?,?,?,?)""",
            (date, symbol, candle_time, open_, high, low, close, volume),
        )
