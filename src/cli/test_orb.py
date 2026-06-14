"""One-off ORB range test — compare exchange candle vs your threshold."""
from __future__ import annotations

from datetime import datetime

import pytz

from src import config
from src.broker import auth, fyers_client
from src.utils.orb_math import orb_range_pct

IST = pytz.timezone("Asia/Kolkata")


def _short(sym: str) -> str:
    return sym.replace("NSE:", "").replace("-EQ", "")


def run_test_orb(symbols: list[str] | None = None) -> int:
    """
    Fetch today's official 9:15–9:30 candle and print ORB range vs threshold.

    Returns 0 if at least one symbol returned candle data.
    """
    symbols = symbols or ["NSE:HDFCBANK-EQ"]
    today = datetime.now(IST).strftime("%Y-%m-%d")

    print("\n" + "=" * 60)
    print("ORB RANGE TEST — first 15-minute candle (9:15–9:30 IST)")
    print(f"Date: {today} | Threshold: ≤ {config.ORB_RANGE_THRESHOLD}%")
    print(f"Formula: (High − Low) / Low × 100  on that candle only")
    print("=" * 60)

    token = auth.get_access_token()
    client = fyers_client.get_client(token)

    found = 0
    for symbol in symbols:
        candle = fyers_client.get_opening_range_candle(client, symbol, today)
        name = _short(symbol)
        if candle is None:
            print(f"\n{name}: no 9:15–9:30 first candle from API yet")
            continue

        found += 1
        o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]
        range_pct = orb_range_pct(h, l)
        passed = range_pct <= config.ORB_RANGE_THRESHOLD

        print(f"\n{name}")
        print(f"  Open   ₹{o:.2f}")
        print(f"  High   ₹{h:.2f}")
        print(f"  Low    ₹{l:.2f}")
        print(f"  Close  ₹{c:.2f}")
        print(f"  Range  {range_pct:.2f}%  →  {'PASS (track)' if passed else 'DROP (exclude)'}")

    print("\n" + "=" * 60)
    if found == 0:
        print("No candle data yet. Run again after 9:31 AM on a trading day.")
        return 1
    print("Compare High/Low above with your TradingView 9:15–9:30 candle.")
    print("=" * 60 + "\n")
    return 0
