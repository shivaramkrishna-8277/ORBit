"""Look up NSE equity symbols via Fyers Symbol Master (no API token needed).

Fyers v3 does not expose the old /symbols/search endpoint. The daily JSON master
is the supported source: https://public.fyers.in/sym_details/NSE_CM_sym_master.json
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytz
import requests

from src.config import CONFIG_DIR
from src.utils.logger import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")
MASTER_URL = "https://public.fyers.in/sym_details/NSE_CM_sym_master.json"
CACHE_DIR = CONFIG_DIR / "sym_master"
CACHE_PATH = CACHE_DIR / "NSE_CM_sym_master.json"
REQUEST_TIMEOUT = 120


def _cache_is_fresh(path: Path) -> bool:
    if not path.is_file():
        return False
    cached_day = datetime.fromtimestamp(path.stat().st_mtime, tz=IST).date()
    return cached_day >= datetime.now(IST).date()


def download_master(*, force: bool = False, cache_path: Path = CACHE_PATH) -> Path:
    """Download today's NSE cash-market master if the cache is missing or stale."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if not force and _cache_is_fresh(cache_path):
        return cache_path

    logger.info("Downloading Fyers NSE_CM symbol master…")
    resp = requests.get(MASTER_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    cache_path.write_bytes(resp.content)
    return cache_path


def load_master(*, force: bool = False, cache_path: Path = CACHE_PATH) -> dict:
    path = download_master(force=force, cache_path=cache_path)
    with path.open("rb") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("Unexpected symbol master shape — expected a JSON object.")
    return data


def _record_symbol(key: str, row: dict) -> str | None:
    sym = (row.get("symTicker") or key or "").strip()
    if not sym.startswith("NSE:") or not sym.endswith("-EQ"):
        return None
    return sym


def _haystack(sym: str, row: dict) -> str:
    name = row.get("exSymName") or row.get("symDetails") or ""
    ticker = sym.replace("NSE:", "").replace("-EQ", "")
    return f"{sym} {ticker} {name}".upper()


def _rank(term: str, sym: str, row: dict) -> int:
    term = term.strip().upper()
    if not term:
        return 99
    ticker = sym.replace("NSE:", "").replace("-EQ", "").upper()
    name = (row.get("exSymName") or row.get("symDetails") or "").upper()

    if term == ticker or term == sym.upper() or term == f"{ticker}-EQ":
        return 0
    if ticker.startswith(term):
        return 1
    if term in ticker:
        return 2
    if term in name:
        return 3
    if term in sym.upper():
        return 4
    return 99


def search_nse_equity(
    query: str,
    *,
    limit: int = 20,
    master: dict | None = None,
) -> list[dict]:
    """Substring search over NSE EQ symbols; returns symTicker + company name."""
    text = query.strip()
    if len(text) < 1:
        return []

    data = master if master is not None else load_master()
    matches: list[tuple[int, str, dict]] = []

    for key, row in data.items():
        if not isinstance(row, dict):
            continue
        sym = _record_symbol(key, row)
        if sym is None:
            continue
        rank = _rank(text, sym, row)
        if rank > 4:
            continue
        matches.append((rank, sym, row))

    matches.sort(key=lambda item: (item[0], item[1]))
    out: list[dict] = []
    for rank, sym, row in matches[:limit]:
        name = (row.get("exSymName") or row.get("symDetails") or "").strip()
        out.append({
            "symbol": sym,
            "name": name,
            "ticker": sym.replace("NSE:", "").replace("-EQ", ""),
            "rank": rank,
        })
    return out


def resolve_nse_equity(query: str, *, master: dict | None = None) -> str | None:
    """Return the official symTicker when the query is an exact ticker/symbol match."""
    hits = search_nse_equity(query, limit=5, master=master)
    if not hits:
        return None
    if hits[0]["rank"] == 0:
        return hits[0]["symbol"]
    text = query.strip().upper()
    if text.startswith("NSE:") and any(h["symbol"].upper() == text for h in hits):
        return next(h["symbol"] for h in hits if h["symbol"].upper() == text)
    return None