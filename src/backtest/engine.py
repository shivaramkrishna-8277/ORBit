"""Run an ORB backtest over a watchlist and a date range."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from src.backtest.history import load_watchlist_minutes
from src.backtest.simulate import simulate_day
from src.utils.market_calendar import trading_days


@dataclass
class BacktestRequest:
    symbols: list[str]
    date_from: date
    date_to: date
    orb_range_threshold: float
    orb_duration_minutes: int
    breakout_timeframe_minutes: int
    max_price_enabled: bool
    max_stock_price: float
    capital: float
    margin_multiplier: float
    risk_pct: float
    day_risk_pct: float
    max_trades_per_day: int
    rrr: float


def run_backtest(client, request: BacktestRequest, progress=None) -> dict:
    """Fetch 1-minute history, simulate each session, and return a report dict."""
    if request.date_to < request.date_from:
        raise ValueError("End date is before the start date.")
    span = (request.date_to - request.date_from).days
    if span > 366:
        raise ValueError("Date range is limited to 366 days per run.")
    if not request.symbols:
        raise ValueError("The watchlist is empty.")
    if request.capital <= 0 or request.rrr <= 0:
        raise ValueError("Capital and RRR must be positive.")
    if request.max_trades_per_day < 1:
        raise ValueError("Max trades per day must be at least 1.")

    days = trading_days(request.date_from, request.date_to)
    if not days:
        raise ValueError("No NSE sessions in that date range.")

    bars = load_watchlist_minutes(
        client, request.symbols, request.date_from, request.date_to, progress=progress
    )

    equity = request.capital
    peak = equity
    max_drawdown = 0.0
    trades: list[dict] = []
    skipped: list[dict] = []
    daily: list[dict] = []

    for index, day in enumerate(days, start=1):
        if progress:
            progress(f"Simulating {day.isoformat()} ({index}/{len(days)})")
        if equity <= 0:
            daily.append(
                {
                    "date": day.isoformat(),
                    "equity_start": equity,
                    "sizing_capital": 0.0,
                    "signals": 0,
                    "trades": 0,
                    "pnl": 0.0,
                    "equity_end": equity,
                }
            )
            continue
        sizing = min(request.capital, equity)
        day_bars = {
            symbol: [bar for bar in series if bar["datetime"].date() == day]
            for symbol, series in bars.items()
        }
        day_bars = {symbol: series for symbol, series in day_bars.items() if series}
        result = simulate_day(
            day,
            day_bars,
            sizing_capital=sizing,
            margin_multiplier=request.margin_multiplier,
            risk_pct=request.risk_pct,
            day_risk_pct=request.day_risk_pct,
            max_trades=request.max_trades_per_day,
            rrr=request.rrr,
            orb_minutes=request.orb_duration_minutes,
            breakout_minutes=request.breakout_timeframe_minutes,
            max_price_enabled=request.max_price_enabled,
            max_stock_price=request.max_stock_price,
            orb_range_threshold=request.orb_range_threshold,
        )
        equity_end = equity + result["pnl"]
        daily.append(
            {
                "date": day.isoformat(),
                "equity_start": equity,
                "sizing_capital": sizing,
                "signals": result["signals"],
                "trades": result["trades"],
                "pnl": result["pnl"],
                "equity_end": equity_end,
            }
        )
        trades.extend(result["trade_rows"])
        skipped.extend(result["skip_rows"])
        equity = equity_end
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)

    wins = [row for row in trades if row["pnl"] > 0]
    losses = [row for row in trades if row["pnl"] < 0]
    gross = equity - request.capital
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sessions": len(days),
        "symbols": len(request.symbols),
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(trades) * 100) if trades else 0.0,
        "gross_pnl": gross,
        "ending_equity": equity,
        "max_drawdown": max_drawdown,
        "avg_r": (sum(row["r_multiple"] for row in trades) / len(trades)) if trades else 0.0,
        "trade_rows": trades,
        "skip_rows": skipped,
        "daily_rows": daily,
    }
