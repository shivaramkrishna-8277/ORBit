"""Position sizing calculations (no Telegram dependency)."""
from __future__ import annotations

import math

from src import config


def margin_from_capital(capital: float) -> tuple[float, float]:
    """Return (margin_added, buying_power) for configured multiplier."""
    buying_power = capital * config.MARGIN_MULTIPLIER
    margin = buying_power - capital
    return margin, buying_power


def compute_position(
    capital: float,
    entry: float,
    stop_loss: float,
    risk_pct: float | None = None,
) -> dict:
    risk_pct = risk_pct if risk_pct is not None else config.DEFAULT_RISK_PCT
    margin, buying_power = margin_from_capital(capital)
    risk_per_share = abs(entry - stop_loss)
    if risk_per_share <= 0:
        raise ValueError("Entry and stop loss must differ.")

    risk_amount = capital * (risk_pct / 100)
    qty_by_risk = math.floor(risk_amount / risk_per_share)
    qty_by_margin = math.floor(buying_power / entry) if entry > 0 else 0
    quantity = min(qty_by_risk, qty_by_margin) if qty_by_margin else qty_by_risk

    return {
        "capital": capital,
        "margin": margin,
        "buying_power": buying_power,
        "risk_pct": risk_pct,
        "risk_amount": risk_amount,
        "risk_per_share": risk_per_share,
        "quantity": quantity,
        "total_cost": quantity * entry,
        "max_loss": quantity * risk_per_share,
    }
