"""One-off watchlist / price-filter smoke test (works outside market hours)."""
from __future__ import annotations

from src import config
from src.broker import auth, fyers_client
from src.utils.logger import get_logger
from src.utils.quote_price import quote_filter_price

logger = get_logger(__name__)


def _short(sym: str) -> str:
    return sym.replace("NSE:", "").replace("-EQ", "")


def run_test_watchlist() -> int:
    """
    Authenticate, fetch Nifty 50 quotes, and print price-filter results.

    Returns 0 on success, 1 if no usable price data was returned.
    """
    print("\n" + "=" * 60)
    print("ORB WATCHLIST TEST")
    print(f"Price limit: ₹{config.MAX_STOCK_PRICE:.0f} | ORB threshold: {config.ORB_RANGE_THRESHOLD}%")
    print("=" * 60)

    token = auth.get_access_token()
    client = fyers_client.get_client(token)

    sample = config.NIFTY50_SYMBOLS[0]
    raw = client.quotes({"symbols": sample})
    raw_v = {}
    if raw.get("s") == "ok" and raw.get("d"):
        raw_v = raw["d"][0].get("v", {})

    print(f"\n--- Raw Fyers fields ({_short(sample)}) ---")
    for key in ("lp", "prev_close_price", "open_price", "ltp", "close"):
        if key in raw_v:
            print(f"  {key}: {raw_v[key]}")

    normalized = fyers_client.get_quotes(client, [sample]).get(sample, {})
    print(f"\n--- Normalized ({_short(sample)}) ---")
    print(f"  ltp={normalized.get('ltp')}  close(prev)={normalized.get('close')}  open={normalized.get('open')}")

    all_quotes = fyers_client.get_quotes(client, config.NIFTY50_SYMBOLS)
    if not all_quotes:
        print("\nERROR: Quotes API returned no symbols. Check token and market data access.")
        return 1

    under: list[tuple[str, float]] = []
    over: list[tuple[str, float]] = []
    missing: list[str] = []

    for sym in config.NIFTY50_SYMBOLS:
        data = all_quotes.get(sym)
        if not data:
            missing.append(sym)
            continue
        price = quote_filter_price(data)
        if price is None:
            missing.append(sym)
        elif price >= config.MAX_STOCK_PRICE:
            over.append((sym, price))
        else:
            under.append((sym, price))

    under.sort(key=lambda x: x[1])
    over.sort(key=lambda x: x[1], reverse=True)

    print(f"\n--- Price filter summary ---")
    print(f"  Under ₹{config.MAX_STOCK_PRICE:.0f}: {len(under)}")
    print(f"  Above ₹{config.MAX_STOCK_PRICE:.0f}: {len(over)}")
    print(f"  Missing price:    {len(missing)}")

    if missing:
        print("\n  Missing:", ", ".join(_short(s) for s in missing[:10]), end="")
        if len(missing) > 10:
            print(f" … +{len(missing) - 10} more", end="")
        print()

    print(f"\n--- Under ₹{config.MAX_STOCK_PRICE:.0f} ({len(under)} stocks) ---")
    for sym, price in under:
        print(f"  {_short(sym):<14} ₹{price:>8.2f}")

    print(f"\n--- Above ₹{config.MAX_STOCK_PRICE:.0f} (sample) ---")
    for sym, price in over[:10]:
        print(f"  {_short(sym):<14} ₹{price:>8.2f}")
    if len(over) > 10:
        print(f"  … and {len(over) - 10} more")

    print("\n" + "=" * 60)
    if len(under) == 0 and len(over) == 0:
        print("FAIL — no usable prices. Field mapping or API access may still be wrong.")
        return 1
    if len(missing) == len(config.NIFTY50_SYMBOLS):
        print("FAIL — all symbols missing price data.")
        return 1
    print("OK — price filter is working. Compare counts above with your expectations.")
    print("=" * 60 + "\n")
    return 0
