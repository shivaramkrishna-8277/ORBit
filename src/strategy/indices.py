"""NSE index symbols that can be tracked beside the equity watchlist."""

INDEX_CATALOG: tuple[tuple[str, str], ...] = (
    ("NSE:NIFTY50-INDEX", "Nifty 50"),
    ("NSE:NIFTYBANK-INDEX", "Bank Nifty"),
    ("NSE:FINNIFTY-INDEX", "Fin Nifty"),
    ("NSE:MIDCPNIFTY-INDEX", "Midcap Nifty"),
    ("NSE:NIFTYNXT50-INDEX", "Nifty Next 50"),
)

DEFAULT_INDEX_SYMBOLS = ["NSE:NIFTY50-INDEX"]
_LABELS = dict(INDEX_CATALOG)


def is_index(symbol: str) -> bool:
    return symbol.endswith("-INDEX")


def index_label(symbol: str) -> str:
    return _LABELS.get(symbol, symbol.replace("NSE:", "").replace("-INDEX", ""))
