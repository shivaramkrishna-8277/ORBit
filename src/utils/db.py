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
    con = sqlite3.connect(str(_DB_PATH), check_same_thread=False, timeout=30)
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
    """
    CREATE TABLE IF NOT EXISTS daily_trading_capital (
        date          TEXT PRIMARY KEY,
        capital       REAL NOT NULL,
        margin        REAL NOT NULL,
        buying_power  REAL NOT NULL,
        risk_pct      REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS strategy_settings (
        id                           INTEGER PRIMARY KEY CHECK (id = 1),
        symbols_json                 TEXT    NOT NULL,
        max_price_enabled            INTEGER NOT NULL,
        max_stock_price              REAL    NOT NULL,
        orb_range_threshold          REAL    NOT NULL,
        orb_range_mode               TEXT    NOT NULL DEFAULT 'lte',
        entry_cutoff                 TEXT    NOT NULL DEFAULT '15:15',
        index_symbols_json           TEXT    NOT NULL DEFAULT '["NSE:NIFTY50-INDEX"]',
        track_retest                 INTEGER NOT NULL DEFAULT 0,
        retest_within_minutes        INTEGER NOT NULL DEFAULT 30,
        entry_mode                   TEXT    NOT NULL DEFAULT 'retest',
        universe                     TEXT    NOT NULL DEFAULT 'watchlist',
        capital                      REAL    NOT NULL DEFAULT 40000,
        margin_multiplier            REAL    NOT NULL DEFAULT 5,
        risk_pct                     REAL    NOT NULL DEFAULT 1.5,
        day_risk_pct                 REAL    NOT NULL DEFAULT 3,
        max_trades_per_day           INTEGER NOT NULL DEFAULT 2,
        rrr                          REAL    NOT NULL DEFAULT 2,
        orb_duration_minutes         INTEGER NOT NULL,
        breakout_timeframe_minutes   INTEGER NOT NULL,
        updated_at                   TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dashboard_commands (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        action      TEXT    NOT NULL,
        status      TEXT    NOT NULL,
        detail      TEXT    NOT NULL DEFAULT '',
        created_at  TEXT    NOT NULL,
        finished_at TEXT
    )
    """,
]


def _migrate_settings_columns(con: sqlite3.Connection) -> None:
    existing = {row[1] for row in con.execute("PRAGMA table_info(strategy_settings)")}
    if "orb_range_mode" not in existing:
        con.execute(
            "ALTER TABLE strategy_settings ADD COLUMN orb_range_mode TEXT NOT NULL DEFAULT 'lte'"
        )
    if "entry_cutoff" not in existing:
        con.execute(
            "ALTER TABLE strategy_settings ADD COLUMN entry_cutoff TEXT NOT NULL DEFAULT '15:15'"
        )
    if "index_symbols_json" not in existing:
        con.execute(
            """ALTER TABLE strategy_settings
               ADD COLUMN index_symbols_json TEXT NOT NULL DEFAULT '["NSE:NIFTY50-INDEX"]'"""
        )
    if "track_retest" not in existing:
        con.execute(
            "ALTER TABLE strategy_settings ADD COLUMN track_retest INTEGER NOT NULL DEFAULT 0"
        )
    if "retest_within_minutes" not in existing:
        con.execute(
            "ALTER TABLE strategy_settings ADD COLUMN retest_within_minutes INTEGER NOT NULL DEFAULT 30"
        )
    additions = (
        ("entry_mode", "TEXT NOT NULL DEFAULT 'retest'"),
        ("universe", "TEXT NOT NULL DEFAULT 'watchlist'"),
        ("capital", "REAL NOT NULL DEFAULT 40000"),
        ("margin_multiplier", "REAL NOT NULL DEFAULT 5"),
        ("risk_pct", "REAL NOT NULL DEFAULT 1.5"),
        ("day_risk_pct", "REAL NOT NULL DEFAULT 3"),
        ("max_trades_per_day", "INTEGER NOT NULL DEFAULT 2"),
        ("rrr", "REAL NOT NULL DEFAULT 2"),
    )
    for name, col_type in additions:
        if name not in existing:
            con.execute(f"ALTER TABLE strategy_settings ADD COLUMN {name} {col_type}")


def _migrate_signals_columns(con: sqlite3.Connection) -> None:
    existing = {row[1] for row in con.execute("PRAGMA table_info(signals)")}
    for name, col_type in (
        ("suggested_sl", "REAL"),
        ("orb_stop", "REAL"),
        ("stop_basis", "TEXT"),
        ("entry_style", "TEXT"),
    ):
        if name not in existing:
            con.execute(f"ALTER TABLE signals ADD COLUMN {name} {col_type}")


def init_db() -> None:
    """Create all tables if they don't exist. Safe to call on every startup."""
    with _conn() as con:
        for ddl in _DDL:
            con.execute(ddl)
        _migrate_settings_columns(con)
        _migrate_signals_columns(con)
    logger.info("Database initialised at %s", _DB_PATH)


# ── daily_watchlist ───────────────────────────────────────────────────────────

def insert_watchlist(date: str, symbol: str, ltp: float) -> None:
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO daily_watchlist (date, symbol, ltp) VALUES (?,?,?)",
            (date, symbol, ltp),
        )


def delete_watchlist(date: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM daily_watchlist WHERE date=?", (date,))


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


def delete_orb_levels(date: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM orb_levels WHERE date=?", (date,))


def get_orb_levels(date: str) -> dict[str, dict]:
    """Return {symbol: {orb_high, orb_low}} for passed symbols on date."""
    with _conn() as con:
        rows = con.execute(
            "SELECT symbol, orb_high, orb_low FROM orb_levels WHERE date=? AND passed_filter=1",
            (date,),
        ).fetchall()
    return {r["symbol"]: {"orb_high": r["orb_high"], "orb_low": r["orb_low"]} for r in rows}


def get_orb_level_rows(date: str) -> list[dict]:
    """Return every ORB row for a date, including symbols that failed the filter."""
    with _conn() as con:
        rows = con.execute(
            """SELECT symbol, orb_high, orb_low, range_pct, passed_filter
               FROM orb_levels WHERE date=? ORDER BY symbol""",
            (date,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── signals ───────────────────────────────────────────────────────────────────

def insert_signal(
    date: str,
    symbol: str,
    signal_type: str,
    candle_close: float,
    orb_level: float,
    move_pct: float,
    triggered_at: str,
    suggested_sl: float | None = None,
    orb_stop: float | None = None,
    stop_basis: str | None = None,
    entry_style: str = "direct",
) -> int:
    """Insert a new signal row and return its id."""
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO signals
               (date, symbol, signal_type, candle_close, orb_level, move_pct, triggered_at,
                alerted, suggested_sl, orb_stop, stop_basis, entry_style)
               VALUES (?,?,?,?,?,?,?,0,?,?,?,?)""",
            (
                date,
                symbol,
                signal_type,
                candle_close,
                orb_level,
                move_pct,
                triggered_at,
                suggested_sl,
                orb_stop,
                stop_basis,
                entry_style,
            ),
        )
        return cur.lastrowid


def has_signal_fired(date: str, symbol: str, signal_type: str, entry_style: str = "direct") -> bool:
    """Return True if this symbol already fired this direction for this entry style today."""
    with _conn() as con:
        row = con.execute(
            """SELECT 1 FROM signals
               WHERE date=? AND symbol=? AND signal_type=?
                 AND IFNULL(entry_style, 'direct')=?
               LIMIT 1""",
            (date, symbol, signal_type, entry_style),
        ).fetchone()
    return row is not None


def has_entry_fired(date: str, symbol: str, entry_style: str = "direct") -> bool:
    """Return True if this symbol already has a direct or retest entry today."""
    with _conn() as con:
        row = con.execute(
            """SELECT 1 FROM signals
               WHERE date=? AND symbol=? AND IFNULL(entry_style, 'direct')=?
               LIMIT 1""",
            (date, symbol, entry_style),
        ).fetchone()
    return row is not None


def mark_signal_alerted(signal_id: int) -> None:
    with _conn() as con:
        con.execute("UPDATE signals SET alerted=1 WHERE id=?", (signal_id,))


def delete_signals(date: str) -> None:
    """Remove every signal for a date so a recalculation can send them again."""
    with _conn() as con:
        con.execute("DELETE FROM signals WHERE date=?", (date,))


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


# ── daily_trading_capital ─────────────────────────────────────────────────────

def save_daily_capital(
    date: str,
    capital: float,
    margin: float,
    buying_power: float,
    risk_pct: float,
) -> None:
    with _conn() as con:
        con.execute(
            """INSERT OR REPLACE INTO daily_trading_capital
               (date, capital, margin, buying_power, risk_pct)
               VALUES (?,?,?,?,?)""",
            (date, capital, margin, buying_power, risk_pct),
        )


def get_strategy_settings() -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM strategy_settings WHERE id=1").fetchone()
    return dict(row) if row else None


def save_strategy_settings(
    symbols_json: str,
    max_price_enabled: int,
    max_stock_price: float,
    orb_range_threshold: float,
    orb_duration_minutes: int,
    breakout_timeframe_minutes: int,
    updated_at: str,
    orb_range_mode: str = "lte",
    entry_cutoff: str = "15:15",
    index_symbols_json: str = '["NSE:NIFTY50-INDEX"]',
    track_retest: int = 0,
    retest_within_minutes: int = 30,
    entry_mode: str = "retest",
    universe: str = "watchlist",
    capital: float = 40_000,
    margin_multiplier: float = 5,
    risk_pct: float = 1.5,
    day_risk_pct: float = 3,
    max_trades_per_day: int = 2,
    rrr: float = 2,
) -> None:
    with _conn() as con:
        con.execute(
            """INSERT INTO strategy_settings (
                   id, symbols_json, max_price_enabled, max_stock_price,
                   orb_range_threshold, orb_range_mode, entry_cutoff, index_symbols_json,
                   track_retest, retest_within_minutes, entry_mode, universe, capital,
                   margin_multiplier, risk_pct, day_risk_pct, max_trades_per_day, rrr,
                   orb_duration_minutes, breakout_timeframe_minutes, updated_at
               ) VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   symbols_json=excluded.symbols_json,
                   max_price_enabled=excluded.max_price_enabled,
                   max_stock_price=excluded.max_stock_price,
                   orb_range_threshold=excluded.orb_range_threshold,
                   orb_range_mode=excluded.orb_range_mode,
                   entry_cutoff=excluded.entry_cutoff,
                   index_symbols_json=excluded.index_symbols_json,
                   track_retest=excluded.track_retest,
                   retest_within_minutes=excluded.retest_within_minutes,
                   entry_mode=excluded.entry_mode,
                   universe=excluded.universe,
                   capital=excluded.capital,
                   margin_multiplier=excluded.margin_multiplier,
                   risk_pct=excluded.risk_pct,
                   day_risk_pct=excluded.day_risk_pct,
                   max_trades_per_day=excluded.max_trades_per_day,
                   rrr=excluded.rrr,
                   orb_duration_minutes=excluded.orb_duration_minutes,
                   breakout_timeframe_minutes=excluded.breakout_timeframe_minutes,
                   updated_at=excluded.updated_at""",
            (
                symbols_json,
                max_price_enabled,
                max_stock_price,
                orb_range_threshold,
                orb_range_mode,
                entry_cutoff,
                index_symbols_json,
                track_retest,
                retest_within_minutes,
                entry_mode,
                universe,
                capital,
                margin_multiplier,
                risk_pct,
                day_risk_pct,
                max_trades_per_day,
                rrr,
                orb_duration_minutes,
                breakout_timeframe_minutes,
                updated_at,
            ),
        )


def enqueue_command(action: str, created_at: str, detail: str = "") -> int:
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO dashboard_commands (action, status, detail, created_at)
               VALUES (?, 'pending', ?, ?)""",
            (action, detail, created_at),
        )
        return int(cur.lastrowid)


def update_command_detail(command_id: int, detail: str) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE dashboard_commands SET detail=? WHERE id=?",
            (detail, command_id),
        )


def claim_next_command() -> dict | None:
    """Mark the oldest pending command as running and return it."""
    with _conn() as con:
        row = con.execute(
            """SELECT * FROM dashboard_commands
               WHERE status='pending' ORDER BY id LIMIT 1"""
        ).fetchone()
        if row is None:
            return None
        cur = con.execute(
            """UPDATE dashboard_commands SET status='running'
               WHERE id=? AND status='pending'""",
            (row["id"],),
        )
        if cur.rowcount != 1:
            return None
        return dict(row)


def finish_command(command_id: int, status: str, detail: str, finished_at: str) -> None:
    with _conn() as con:
        con.execute(
            """UPDATE dashboard_commands
               SET status=?, detail=?, finished_at=? WHERE id=?""",
            (status, detail, finished_at, command_id),
        )


def latest_command() -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM dashboard_commands ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def get_command(command_id: int) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM dashboard_commands WHERE id=?",
            (command_id,),
        ).fetchone()
    return dict(row) if row else None


def latest_command_for(action: str) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM dashboard_commands WHERE action=? ORDER BY id DESC LIMIT 1",
            (action,),
        ).fetchone()
    return dict(row) if row else None


def has_active_command(action: str | None = None) -> bool:
    """True if a command is still pending or running."""
    with _conn() as con:
        if action:
            row = con.execute(
                """SELECT 1 FROM dashboard_commands
                   WHERE action=? AND status IN ('pending', 'running') LIMIT 1""",
                (action,),
            ).fetchone()
        else:
            row = con.execute(
                """SELECT 1 FROM dashboard_commands
                   WHERE status IN ('pending', 'running') LIMIT 1"""
            ).fetchone()
    return row is not None


def get_daily_capital(date: str) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT capital, margin, buying_power, risk_pct FROM daily_trading_capital WHERE date=?",
            (date,),
        ).fetchone()
    if not row:
        return None
    return {
        "capital": row["capital"],
        "margin": row["margin"],
        "buying_power": row["buying_power"],
        "risk_pct": row["risk_pct"],
    }
