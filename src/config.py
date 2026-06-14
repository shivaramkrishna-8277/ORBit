"""Configuration module — loads and validates all environment variables.

Usage:
    from src.config import FYERS_APP_ID, MAX_STOCK_PRICE, NIFTY50_SYMBOLS
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Project root is two levels above this file: src/config.py → src/ → root
PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


def _require(var: str) -> str:
    """Return the env var value, or raise a clear ValueError if it is absent."""
    value = os.getenv(var, "").strip()
    if not value:
        raise ValueError(
            f"Missing required environment variable: '{var}'. "
            f"Copy .env.example to .env and fill in all values."
        )
    return value


# ── Fyers API ─────────────────────────────────────────────────────────────────
FYERS_APP_ID: str = _require("FYERS_APP_ID")
FYERS_SECRET_KEY: str = _require("FYERS_SECRET_KEY")
FYERS_REDIRECT_URI: str = os.getenv("FYERS_REDIRECT_URI", "http://127.0.0.1")
# Port the temporary OAuth callback server listens on (VPS deployment)
OAUTH_CALLBACK_PORT: int = int(os.getenv("OAUTH_CALLBACK_PORT", "8080"))

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID: str = _require("TELEGRAM_CHAT_ID")

# ── Strategy parameters ───────────────────────────────────────────────────────
MAX_STOCK_PRICE: float = float(os.getenv("MAX_STOCK_PRICE", "800"))
ORB_RANGE_THRESHOLD: float = float(os.getenv("ORB_RANGE_THRESHOLD", "0.6"))
MARGIN_MULTIPLIER: float = float(os.getenv("MARGIN_MULTIPLIER", "5"))
DEFAULT_RISK_PCT: float = float(os.getenv("DEFAULT_RISK_PCT", "1.0"))

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

# ── Paths ─────────────────────────────────────────────────────────────────────
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"

# Ensure runtime directories exist
for _d in (CONFIG_DIR, DATA_DIR, LOGS_DIR):
    _d.mkdir(exist_ok=True)

# ── Nifty 50 Symbols ──────────────────────────────────────────────────────────
# Format: "NSE:<TICKER>-EQ"  (Fyers symbol convention)
#
# NOTE: The Nifty 50 composition changes periodically.
#       Verify against the latest NSE index fact-sheet before going live:
#       https://www.nseindia.com/products-services/indices-nifty50-index
NIFTY50_SYMBOLS: list[str] = [
    "NSE:ADANIENT-EQ",
    "NSE:ADANIPORTS-EQ",
    "NSE:APOLLOHOSP-EQ",
    "NSE:ASIANPAINT-EQ",
    "NSE:AXISBANK-EQ",
    "NSE:BAJAJ-AUTO-EQ",
    "NSE:BAJAJFINSV-EQ",
    "NSE:BAJFINANCE-EQ",
    "NSE:BEL-EQ",
    "NSE:BHARTIARTL-EQ",
    "NSE:CIPLA-EQ",
    "NSE:COALINDIA-EQ",
    "NSE:DRREDDY-EQ",
    "NSE:EICHERMOT-EQ",
    "NSE:ETERNAL-EQ",
    "NSE:GRASIM-EQ",
    "NSE:HCLTECH-EQ",
    "NSE:HDFCBANK-EQ",
    "NSE:HDFCLIFE-EQ",
    "NSE:HINDALCO-EQ",
    "NSE:HINDUNILVR-EQ",
    "NSE:ICICIBANK-EQ",
    "NSE:INDIGO-EQ",
    "NSE:INFY-EQ",
    "NSE:ITC-EQ",
    "NSE:JIOFIN-EQ",
    "NSE:JSWSTEEL-EQ",
    "NSE:KOTAKBANK-EQ",
    "NSE:LT-EQ",
    "NSE:M&M-EQ",
    "NSE:MARUTI-EQ",
    "NSE:MAXHEALTH-EQ",
    "NSE:NESTLEIND-EQ",
    "NSE:NTPC-EQ",
    "NSE:ONGC-EQ",
    "NSE:POWERGRID-EQ",
    "NSE:RELIANCE-EQ",
    "NSE:SBILIFE-EQ",
    "NSE:SBIN-EQ",
    "NSE:SHRIRAMFIN-EQ",
    "NSE:SUNPHARMA-EQ",
    "NSE:TATACONSUM-EQ",
    "NSE:TATASTEEL-EQ",
    "NSE:TCS-EQ",
    "NSE:TECHM-EQ",
    "NSE:TITAN-EQ",
    "NSE:TMPV-EQ",
    "NSE:TRENT-EQ",
    "NSE:ULTRACEMCO-EQ",
    "NSE:WIPRO-EQ",
]

assert len(NIFTY50_SYMBOLS) == 50, f"Expected 50 symbols, got {len(NIFTY50_SYMBOLS)}"
