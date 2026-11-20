"""Calendar months for the backtest date picker."""
from calendar import monthrange
from datetime import date

MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def month_window(year: int, month: int, today: date) -> tuple[date, date] | None:
    """Inclusive session window for one month, cut off at today. None if it has not started."""
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    if start > today:
        return None
    return start, min(end, today)
