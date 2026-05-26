# Nifty 50 ORB Alert System — Build Tasks

> **Strategy recap:** Track Nifty 50 stocks under ₹800. Capture the first 15-minute candle (9:15–9:30 AM IST). If the ORB range is ≤ 0.6%, keep the stock. Alert via Telegram when any subsequent candle closes above the ORB high or below the ORB low.

---

## Before you start — checklist

- [ ] Fyers App ID and Secret Key from [myapi.fyers.in](https://myapi.fyers.in)
- [ ] Fyers account with market data access enabled
- [ ] Telegram Bot Token from `@BotFather`
- [ ] Telegram Chat ID (from `api.telegram.org/bot<TOKEN>/getUpdates`)
- [ ] Python 3.11+ installed
- [ ] VS Code + GitHub Copilot extension active
- [ ] Git installed (optional but recommended)
- [ ] VPS or PC that stays on during 9:00 AM – 3:30 PM IST

---

## Phase 0 — Project setup

### Task 0.1 — Initialise project structure

**What to do:** Create the folder layout, virtual environment, and install all dependencies.

**Copilot prompt:**
```
Create a Python project structure for an algorithmic trading bot with the following folders:
- /config       → for .env and settings files
- /data         → for SQLite database files
- /logs         → for daily log files
- /src          → main source code
  - /src/broker     → Fyers API connection and data fetching
  - /src/strategy   → ORB logic and candle building
  - /src/alerts     → Telegram notification module
  - /src/utils      → helpers, logging, timezone utilities

Create a requirements.txt with these packages:
fyers-apiv3, pandas, python-dotenv, python-telegram-bot==20.7, 
APScheduler, pytz, sqlite3 (built-in), requests, schedule

Also create a main.py entry point that imports and runs the bot.
```

---

### Task 0.2 — Environment configuration file

**What to do:** Create a `.env` file template and a config loader module.

**Copilot prompt:**
```
Create a .env.example file for a trading bot with these variables:
FYERS_APP_ID=your_app_id_here
FYERS_SECRET_KEY=your_secret_key_here
FYERS_REDIRECT_URI=http://127.0.0.1
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_id
MAX_STOCK_PRICE=800
ORB_RANGE_THRESHOLD=0.6
LOG_LEVEL=INFO

Then create src/config.py that:
- Loads all variables from .env using python-dotenv
- Validates that none of the required variables are missing (raise ValueError with a clear message if any are absent)
- Exports typed constants (MAX_STOCK_PRICE as float, ORB_RANGE_THRESHOLD as float)
- Has a NIFTY50_SYMBOLS list with all 50 NSE symbols in the format "NSE:SYMBOL-EQ"
```

---

### Task 0.3 — Logging setup

**What to do:** Set up structured daily logging so every run has its own log file.

**Copilot prompt:**
```
Create src/utils/logger.py that:
- Sets up Python logging with two handlers:
  1. Console handler (INFO level, clean format)
  2. File handler writing to /logs/YYYY-MM-DD.log (DEBUG level, detailed format)
- Log format: [TIMESTAMP] [LEVEL] [MODULE] - message
- Uses IST timezone (Asia/Kolkata) for all timestamps via pytz
- Exposes a get_logger(name) function that other modules import
- Rotates: each day gets its own file (use TimedRotatingFileHandler)
```

---

## Phase 1A — Watchlist and price filter

### Task 1.1 — Fyers authentication module

**What to do:** Build the daily auth flow to generate a fresh access token each morning.

**Copilot prompt:**
```
Create src/broker/auth.py for Fyers API authentication using fyers-apiv3 SDK.

It should:
- Have a generate_access_token() function
- Use FyersModel with app_id and secret_key from config
- Open the Fyers auth URL in the browser automatically (use webbrowser.open)
- Prompt the user to paste the redirect URL after login (the URL contains auth_code as a query param)
- Parse the auth_code from that URL using urllib.parse
- Exchange auth_code for access_token using the SDK
- Save the access_token to /config/token.txt with today's date
- Have a get_access_token() function that reads from token.txt if it exists and was created today, otherwise calls generate_access_token()
- Print clear instructions at each step so the user knows what to do
```

---

### Task 1.2 — Fyers API client wrapper

**What to do:** Create a clean wrapper around the Fyers REST API for fetching quotes.

**Copilot prompt:**
```
Create src/broker/fyers_client.py that wraps fyers-apiv3 FyersModel.

Include these functions:
1. get_quotes(symbols: list[str]) -> dict
   - Calls Fyers /data/quotes endpoint
   - Returns a dict of {symbol: {"ltp": float, "open": float, "high": float, "low": float, "close": float}}
   - Handles API errors gracefully with logging

2. get_historical_candles(symbol: str, resolution: str, date_from: str, date_to: str) -> pd.DataFrame
   - Calls Fyers /data/history endpoint
   - Returns a DataFrame with columns: datetime, open, high, low, close, volume
   - resolution examples: "15" for 15-minute, "D" for daily

3. get_nifty50_prices() -> dict
   - Calls get_quotes() with the full NIFTY50_SYMBOLS list from config
   - Returns filtered dict with only symbols where ltp < MAX_STOCK_PRICE

Log every API call with its response time. Handle rate limits with a retry after 1 second.
```

---

### Task 1.3 — Daily watchlist builder

**What to do:** Run the price filter at market open and produce the day's active watchlist.

**Copilot prompt:**
```
Create src/strategy/watchlist.py with:

1. A WatchlistManager class with:
   - build_daily_watchlist() method:
     * Calls fyers_client.get_nifty50_prices()
     * Filters stocks with LTP < MAX_STOCK_PRICE (from config)
     * Saves result to SQLite table "daily_watchlist" with columns: date, symbol, ltp
     * Returns list of qualifying symbol strings
     * Logs how many stocks passed (e.g. "14 of 50 Nifty stocks under ₹800 today")
   
   - get_todays_watchlist() method:
     * Reads today's watchlist from SQLite
     * Returns list of symbols
     * Falls back to rebuild if today's data not found

2. SQLite helper that creates the table if it doesn't exist on first run

Use the date as YYYY-MM-DD string for the date column.
```

---

### Task 1.4 — SQLite database initialiser

**What to do:** Create all database tables upfront with a single init script.

**Copilot prompt:**
```
Create src/utils/db.py that manages the SQLite database at /data/orb_tracker.db.

Create these tables if they don't exist:
1. daily_watchlist: id, date TEXT, symbol TEXT, ltp REAL
2. orb_levels: id, date TEXT, symbol TEXT, orb_high REAL, orb_low REAL, range_pct REAL, passed_filter INTEGER (1 or 0)
3. signals: id, date TEXT, symbol TEXT, signal_type TEXT (BULLISH/BEARISH), candle_close REAL, orb_level REAL, move_pct REAL, triggered_at TEXT, alerted INTEGER (0 or 1)
4. candle_log: id, date TEXT, symbol TEXT, candle_time TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL

Expose clean functions:
- init_db() → creates all tables
- insert_orb_level(date, symbol, high, low, range_pct, passed)
- insert_signal(date, symbol, signal_type, close, orb_level, move_pct)
- get_orb_levels(date) → returns dict of {symbol: {high, low}}
- has_signal_fired(date, symbol, signal_type) → bool (for deduplication)
- mark_signal_alerted(signal_id)
```

---

## Phase 1B — ORB candle capture

### Task 2.1 — WebSocket tick stream handler

**What to do:** Connect to Fyers WebSocket and stream live ticks for the watchlist symbols.

**Copilot prompt:**
```
Create src/broker/websocket_handler.py using fyers-apiv3 FyersDataSocket.

Build a TickStreamManager class:
- __init__(self, symbols: list[str], on_tick_callback: callable)
  * stores symbols and callback
- connect(self)
  * creates FyersDataSocket with access_token from auth module
  * sets up onopen, onmessage, onerror, onclose callbacks
  * on connect: subscribes to all symbols with data_type="SymbolUpdate"
- onmessage(self, message)
  * parses incoming tick: extract symbol, ltp, timestamp
  * calls self.on_tick_callback(symbol, ltp, timestamp)
- disconnect(self)
  * cleanly closes the WebSocket connection
- Auto-reconnect: if connection drops, wait 5 seconds and reconnect (max 3 retries, then log and stop)
- Run the WebSocket in a separate thread so it doesn't block the main process

Log each connect, disconnect, and error event.
```

---

### Task 2.2 — 15-minute candle aggregator

**What to do:** Aggregate incoming ticks into 15-minute OHLC candles per symbol.

**Copilot prompt:**
```
Create src/strategy/candle_builder.py.

Build a CandleBuilder class:
- Maintains an in-memory dict: {symbol: {current_candle: {open, high, low, close, volume, candle_start_time}}}
- on_tick(self, symbol: str, price: float, timestamp: datetime)
  * determines which 15-minute bucket this tick belongs to (bucket to 9:15, 9:30, 9:45... etc)
  * if new bucket: finalise the previous candle and start a new one
  * updates current candle: high = max(high, price), low = min(low, price), close = price
  * if it's the very first tick for a symbol: set open = price
- on_candle_close(self, symbol: str, candle: dict) — callback hook
  * called when a candle is finalised
  * stores completed candle to SQLite candle_log table
- get_current_candle(symbol) → returns in-progress candle dict
- get_first_candle(symbol) → returns the 9:15–9:30 candle once it is complete

Use pytz Asia/Kolkata for all time bucketing. 
15-minute buckets should be aligned to market open: 9:15, 9:30, 9:45, 10:00, etc.
```

---

### Task 2.3 — ORB level calculator

**What to do:** At 9:30 AM, lock the ORB high/low and apply the 0.6% range filter.

**Copilot prompt:**
```
Create src/strategy/orb_calculator.py.

Build an ORBCalculator class:
- calculate_orb(self, symbol: str, first_candle: dict) -> dict | None
  * first_candle has keys: open, high, low, close
  * calculates: range_pct = (high - low) / low * 100
  * if range_pct <= ORB_RANGE_THRESHOLD (from config): 
    - saves to SQLite orb_levels (passed_filter=1)
    - returns {symbol, orb_high, orb_low, range_pct}
  * if range_pct > threshold:
    - saves to SQLite orb_levels (passed_filter=0)
    - logs "SYMBOL dropped: range X.XX% exceeds 0.6% threshold"
    - returns None

- process_all_symbols(self, candle_builder: CandleBuilder, watchlist: list[str]) -> dict
  * called at exactly 9:30 AM
  * iterates over watchlist, calls calculate_orb for each
  * returns dict of {symbol: {orb_high, orb_low}} for symbols that passed
  * logs summary: "X of Y stocks passed ORB range filter"

```

---

## Phase 1C — Breakout detection

### Task 3.1 — Breakout detector

**What to do:** After 9:30 AM, check every closed candle for breakouts above/below ORB levels.

**Copilot prompt:**
```
Create src/strategy/breakout_detector.py.

Build a BreakoutDetector class:
- __init__(self, orb_levels: dict, on_breakout_callback: callable)
  * orb_levels = {symbol: {orb_high, orb_low}}
  * on_breakout_callback is called when a signal fires
  
- check_candle(self, symbol: str, candle: dict)
  * only runs if symbol is in orb_levels
  * uses candle["close"] — never tick price — for comparison
  * BULLISH signal: candle close > orb_high
    - check db: has this symbol already fired a BULLISH signal today? (use db.has_signal_fired)
    - if not: calculate move_pct = (close - orb_high) / orb_high * 100
    - call on_breakout_callback with signal details
    - save to SQLite signals table
  * BEARISH signal: candle close < orb_low
    - same deduplication check for BEARISH
    - move_pct = (orb_low - close) / orb_low * 100
    - call on_breakout_callback

- Signal dict passed to callback:
  {symbol, signal_type, candle_close, orb_level, move_pct, candle_time}
```

---

### Task 3.2 — Telegram alert module

**What to do:** Send formatted Telegram messages when breakout signals fire.

**Copilot prompt:**
```
Create src/alerts/telegram_notifier.py using python-telegram-bot v20.

Build a TelegramNotifier class:
- __init__(self): loads token and chat_id from config
- send_message(self, text: str): sends plain text async
- send_breakout_alert(self, signal: dict):
  * formats a clear alert message:
    🟢 BULLISH BREAKOUT — SYMBOL
    Close: ₹XXX.XX | ORB High: ₹XXX.XX
    Move: +X.XX% above ORB
    Time: HH:MM IST
    
    OR for bearish:
    🔴 BEARISH BREAKOUT — SYMBOL
    Close: ₹XXX.XX | ORB Low: ₹XXX.XX
    Move: -X.XX% below ORB
    Time: HH:MM IST
    
  * calls send_message with this formatted string
  * on success: marks signal as alerted in SQLite

- send_session_start(watchlist: list[str]):
  * sends message at 9:15 AM: "📊 ORB session started. Tracking X stocks under ₹800"

- send_orb_summary(passed_symbols: list[str], dropped_count: int):
  * sends at 9:30 AM: "✅ ORB filter done. X stocks qualify. Y dropped (range > 0.6%)"
  * lists qualifying symbols

- send_session_end(signal_count: int):
  * sends at 3:15 PM: "🔔 Session ended. X breakout signals today."

Handle Telegram API errors silently (log the error, don't crash the bot).
```

---

## Phase 1D — Orchestration and scheduler

### Task 4.1 — Main scheduler and session manager

**What to do:** Wire everything together with a scheduler that manages market session lifecycle.

**Copilot prompt:**
```
Create src/session_manager.py — the central orchestrator.

Build a SessionManager class that manages a full trading day:

Scheduled jobs (all times IST using pytz):
- 09:10 AM: 
  * Call auth.get_access_token()
  * Call watchlist.build_daily_watchlist()
  * Send session start Telegram message

- 09:15 AM:
  * Start WebSocket tick stream for today's watchlist symbols
  * CandleBuilder starts accumulating ticks

- 09:30 AM (sharp):
  * Pause new ORB captures (first candle window closed)
  * Run ORBCalculator.process_all_symbols()
  * Send ORB summary to Telegram
  * Start BreakoutDetector with qualifying symbols' ORB levels
  * CandleBuilder.on_candle_close now routes to BreakoutDetector.check_candle

- Every candle close (9:45, 10:00, 10:15 ... 3:15 PM):
  * Trigger CandleBuilder to finalise the current candle for all symbols
  * BreakoutDetector checks each closed candle

- 03:15 PM:
  * Stop WebSocket stream cleanly
  * Send session end summary to Telegram
  * Reset all in-memory state (candles, orb levels, signals)

Use APScheduler with BackgroundScheduler and Asia/Kolkata timezone.
Only run Monday to Friday. Add a check: if today is a market holiday, send a Telegram message and skip.
```

---

### Task 4.2 — Market holiday guard

**What to do:** Prevent the bot from running on NSE holidays.

**Copilot prompt:**
```
Create src/utils/market_calendar.py.

- Maintain a hardcoded list of NSE holidays for the current year as date strings (YYYY-MM-DD format). 
  Include all official NSE holidays for 2025 and 2026.
- is_market_open(date: datetime = None) -> bool
  * returns False if the date is a Saturday, Sunday, or in the holiday list
  * defaults to today if date not passed
- next_trading_day(from_date: datetime = None) -> datetime
  * returns the next date when the market will be open
- Log clearly when market is closed and why ("Saturday", "Sunday", or the holiday name)
```

---

### Task 4.3 — Main entry point

**What to do:** Create `main.py` that starts the bot cleanly with error handling.

**Copilot prompt:**
```
Create main.py as the entry point for the entire ORB bot.

It should:
1. Call db.init_db() to ensure all tables exist
2. Load config and validate all env vars
3. Check market_calendar.is_market_open() — if not, log and exit gracefully
4. Instantiate SessionManager and start the scheduler
5. Keep the process alive with a while True loop that sleeps 1 second
6. Handle KeyboardInterrupt (Ctrl+C) gracefully: stop scheduler, disconnect WebSocket, log "Bot stopped"
7. Wrap the entire startup in try/except — on any startup error, send a Telegram message "⚠️ ORB Bot failed to start: {error}" and exit

Add a --dry-run CLI flag (using argparse) that runs the full pipeline but sends alerts to console only, not Telegram.
Also add a --test-telegram flag that sends a test message to Telegram and exits.
```

---

## Phase 1E — Testing and hardening

### Task 5.1 — Unit tests for core logic

**What to do:** Write tests for the most critical functions so regressions are caught early.

**Copilot prompt:**
```
Create /tests/test_orb_logic.py using pytest.

Write unit tests for:
1. CandleBuilder
   - test that ticks in the 9:15–9:30 window build one candle correctly (high, low, open, close)
   - test that a tick at 9:30:00 starts a new candle
   - test that on_candle_close is called exactly once when the bucket changes

2. ORBCalculator.calculate_orb
   - test: range = 0.5% → should pass filter, return ORB levels
   - test: range = 0.6% → should pass (boundary, inclusive)
   - test: range = 0.61% → should fail filter, return None
   - test: range = 2% → should fail filter

3. BreakoutDetector.check_candle
   - test: close above orb_high → BULLISH signal fires callback
   - test: close below orb_low → BEARISH signal fires callback
   - test: close between orb_high and orb_low → no callback
   - test: duplicate signal for same symbol and direction → callback fires only once

Use mock objects for the SQLite db calls (patch db.has_signal_fired and db.insert_signal).
```

---

### Task 5.2 — Replay test with historical data

**What to do:** Test the full pipeline using past data before going live.

**Copilot prompt:**
```
Create /tests/replay_test.py — a script that simulates a trading session using historical candle data.

It should:
1. Accept a date argument (e.g. python replay_test.py --date 2025-01-15)
2. Fetch historical 15-minute candles for that date for all Nifty 50 stocks using fyers_client.get_historical_candles()
3. Feed each candle in chronological order through CandleBuilder → ORBCalculator → BreakoutDetector
4. Print all signals that would have been generated that day (don't send Telegram messages, just print)
5. Print a summary table: symbol | ORB high | ORB low | range% | signal type | signal time | close price

This lets you validate the strategy on any past date without risking live alerts.
```

---

### Task 5.3 — WebSocket reconnect stress test

**What to do:** Test that the bot handles connection drops gracefully.

**Copilot prompt:**
```
Create /tests/test_websocket_resilience.py.

Write a test that:
1. Starts the TickStreamManager
2. After 10 seconds, simulates a network drop by calling the onerror callback manually
3. Verifies that the manager attempts to reconnect within 5 seconds
4. Verifies that after reconnection, it re-subscribes to the same symbols
5. Simulates 3 consecutive failures and verifies the manager stops after max retries and logs the error

Use unittest.mock to mock the FyersDataSocket and avoid real network calls.
```

---

## Phase 2 — Position sizing calculator

> Start Phase 2 only after 4–5 weeks of paper trading Phase 1 signals manually and verifying signal quality.

### Task 6.1 — Risk calculator module

**What to do:** Calculate position size based on risk per trade.

**Copilot prompt:**
```
Create src/strategy/position_sizer.py.

Build a PositionSizer class:
- __init__(self, capital: float, risk_pct: float = 1.0)
  * capital = total trading capital in ₹
  * risk_pct = max % of capital to risk per trade (default 1%)

- calculate(self, signal: dict, entry_price: float) -> dict:
  * For BULLISH: stop_loss = signal["orb_low"], entry = entry_price
  * For BEARISH: stop_loss = signal["orb_high"], entry = entry_price
  * risk_amount = capital * (risk_pct / 100)
  * risk_per_share = abs(entry - stop_loss)
  * quantity = floor(risk_amount / risk_per_share)
  * potential_reward = quantity * abs(entry - stop_loss) * 2  (2:1 RR assumption)
  * Returns: {quantity, entry, stop_loss, risk_amount, risk_per_share, potential_reward, rr_ratio}

- Add a validate(self, result: dict) -> bool:
  * Returns False if quantity < 1 (position too small)
  * Returns False if risk_per_share < 0.5 (SL too tight, likely noise)
  * Logs a warning if quantity * entry > capital * 0.2 (position > 20% of capital)
```

---

### Task 6.2 — Enhanced Telegram alert with sizing

**What to do:** Update the alert to include position sizing details.

**Copilot prompt:**
```
Update src/alerts/telegram_notifier.py to add a new method:

send_sized_alert(self, signal: dict, sizing: dict):
  * formats a detailed alert:
    🟢 BULLISH BREAKOUT — SYMBOL
    ━━━━━━━━━━━━━━━━━━
    Entry:     ₹XXX.XX
    Stop loss: ₹XXX.XX (ORB low)
    Quantity:  XX shares
    Risk:      ₹XXX (1% of capital)
    RR ratio:  1:2
    Time: HH:MM IST
    ━━━━━━━━━━━━━━━━━━
    ⚠️ Manual entry — no order placed

  * Include a disclaimer line: "⚠️ Manual entry — no order placed"
  * If sizing.validate() returns False, send the breakout alert without sizing and add "⚠️ Position size invalid — check manually"
```

---

## Ongoing maintenance tasks

### Task M.1 — Daily signal review script

**Copilot prompt:**
```
Create /scripts/daily_review.py — a script to run after market close.

It should query the SQLite database and print a daily P&L-style summary table:
- All signals that fired today
- Symbol, direction, ORB level, signal price, time
- Whether it was a potential winner (did price continue in signal direction for next 2 candles?)
- Export this as a CSV to /data/reviews/YYYY-MM-DD_review.csv

This is for manual backtesting purposes during the 4-5 month paper trading phase.
```

---

### Task M.2 — Weekly Telegram summary

**Copilot prompt:**
```
Add a weekly_summary() method to TelegramNotifier.

Every Friday at 3:30 PM IST:
- Query SQLite for the past 5 trading days of signals
- Count: total signals, bullish vs bearish, how many stocks passed the range filter on average
- Format as a clean weekly digest message and send to Telegram

Schedule this in SessionManager as an additional Friday-only job.
```

---

### Task M.3 — Update NSE holiday list

**Copilot prompt:**
```
Update the holiday list in src/utils/market_calendar.py for the upcoming year.
Go to the NSE India website (nseindia.com) holidays page, find all trading holidays, 
and update the HOLIDAYS list with the new dates in YYYY-MM-DD format.
Also add a check: if today's date is past December of the current year and no next-year holidays are defined, 
log a warning: "Holiday list may be outdated — please update market_calendar.py"
```

---

## Reference — file structure when complete

```
orb-bot/
├── main.py
├── requirements.txt
├── .env                    ← never commit this
├── .env.example
├── .gitignore
├── config/
│   └── token.txt           ← daily access token (auto-generated)
├── data/
│   └── orb_tracker.db      ← SQLite database
├── logs/
│   └── YYYY-MM-DD.log
├── src/
│   ├── config.py
│   ├── session_manager.py
│   ├── broker/
│   │   ├── auth.py
│   │   ├── fyers_client.py
│   │   └── websocket_handler.py
│   ├── strategy/
│   │   ├── watchlist.py
│   │   ├── candle_builder.py
│   │   ├── orb_calculator.py
│   │   ├── breakout_detector.py
│   │   └── position_sizer.py   ← Phase 2
│   ├── alerts/
│   │   └── telegram_notifier.py
│   └── utils/
│       ├── logger.py
│       ├── db.py
│       └── market_calendar.py
├── tests/
│   ├── test_orb_logic.py
│   ├── test_websocket_resilience.py
│   └── replay_test.py
└── scripts/
    └── daily_review.py
```

---

## Build order (recommended sequence)

1. Task 0.1 → 0.2 → 0.3 (project foundation)
2. Task 1.4 (database first — everything else depends on it)
3. Task 1.1 → 1.2 (broker connection)
4. Task 1.3 (watchlist)
5. Task 2.1 → 2.2 → 2.3 (ORB window)
6. Task 3.1 → 3.2 (breakout + alerts)
7. Task 4.2 → 4.1 → 4.3 (scheduler + main)
8. Task 5.1 → 5.2 (test before going live)
9. Run `replay_test.py` on 3–5 past dates, verify signals look correct
10. Go live in monitor-only mode for 1 week
11. Phase 2: Task 6.1 → 6.2 (after paper trading validation)
