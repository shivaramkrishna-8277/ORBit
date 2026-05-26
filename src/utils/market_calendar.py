"""NSE market calendar — holiday list and trading day checks.

Holiday list covers 2025 and 2026 (official NSE trading holidays).
Source: https://www.nseindia.com/products-services/equity-market-timings-holidays
Update this file each December for the following year.
"""
import logging
from datetime import date, datetime, timedelta

import pytz

from src.utils.logger import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# ── NSE Holidays (YYYY-MM-DD) ─────────────────────────────────────────────────
# Each tuple: (date_string, holiday_name)

_HOLIDAYS: dict[str, str] = {
    # ── 2025 ──────────────────────────────────────────────────────────────────
    "2025-01-26": "Republic Day",
    "2025-02-26": "Mahashivratri",
    "2025-03-14": "Holi",
    "2025-03-31": "Id-ul-Fitr (Ramzan Eid)",
    "2025-04-10": "Shri Ram Navami",
    "2025-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2025-04-18": "Good Friday",
    "2025-05-01": "Maharashtra Day",
    "2025-08-15": "Independence Day",
    "2025-08-27": "Ganesh Chaturthi",
    "2025-10-02": "Gandhi Jayanti / Mahatma Gandhi Jayanti",
    "2025-10-02": "Dussehra",
    "2025-10-20": "Diwali — Laxmi Puja",
    "2025-10-21": "Diwali — Balipratipada",
    "2025-11-05": "Prakash Gurpurb Sri Guru Nanak Dev Ji",
    "2025-12-25": "Christmas",

    # ── 2026 ──────────────────────────────────────────────────────────────────
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-20": "Id-ul-Fitr (Ramzan Eid)",
    "2026-03-30": "Shri Ram Navami",
    "2026-04-02": "Dr. Baba Saheb Ambedkar Jayanti (observed)",
    "2026-04-03": "Good Friday",
    "2026-05-01": "Maharashtra Day",
    "2026-08-15": "Independence Day",
    "2026-09-16": "Ganesh Chaturthi",
    "2026-10-02": "Gandhi Jayanti",
    "2026-10-08": "Dussehra",
    "2026-11-09": "Diwali — Laxmi Puja",
    "2026-11-10": "Diwali — Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev Ji",
    "2026-12-25": "Christmas",
}


# ── Public API ─────────────────────────────────────────────────────────────────

def is_market_open(check_date: date | datetime | None = None) -> bool:
    """
    Return True if the NSE equity market trades on check_date.

    Args:
        check_date: date or datetime (IST assumed). Defaults to today IST.

    Returns:
        False on weekends and official holidays, True otherwise.
    """
    if check_date is None:
        check_date = datetime.now(IST).date()
    elif isinstance(check_date, datetime):
        check_date = check_date.astimezone(IST).date()

    # Warn if the holiday list may be stale
    if check_date.year > max(int(d[:4]) for d in _HOLIDAYS):
        logger.warning(
            "Holiday list may be outdated for %d — please update market_calendar.py",
            check_date.year,
        )

    date_str = check_date.strftime("%Y-%m-%d")

    if check_date.weekday() == 5:  # Saturday
        logger.info("Market closed on %s (Saturday)", date_str)
        return False
    if check_date.weekday() == 6:  # Sunday
        logger.info("Market closed on %s (Sunday)", date_str)
        return False
    if date_str in _HOLIDAYS:
        logger.info("Market closed on %s (%s)", date_str, _HOLIDAYS[date_str])
        return False

    return True


def next_trading_day(from_date: date | datetime | None = None) -> date:
    """
    Return the next calendar date on which NSE trades.

    Args:
        from_date: Start from this date (exclusive). Defaults to today IST.
    """
    if from_date is None:
        from_date = datetime.now(IST).date()
    elif isinstance(from_date, datetime):
        from_date = from_date.astimezone(IST).date()

    candidate = from_date + timedelta(days=1)
    while not is_market_open(candidate):
        candidate += timedelta(days=1)
    return candidate
