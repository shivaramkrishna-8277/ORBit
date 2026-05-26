"""Fyers API authentication — daily OAuth token flow.

Run once per trading day at ~9:10 AM. Opens a browser for login, then
prompts the user to paste the redirect URL containing the auth_code.
Saves the token to config/token.txt with today's date.
"""
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

import pytz

from fyers_apiv3 import fyersModel
from src import config
from src.utils.logger import get_logger

logger = get_logger(__name__)

IST = pytz.timezone("Asia/Kolkata")
_TOKEN_FILE = config.CONFIG_DIR / "token.txt"


# ── Token persistence ─────────────────────────────────────────────────────────

def _save_token(token: str) -> None:
    today = datetime.now(IST).strftime("%Y-%m-%d")
    _TOKEN_FILE.write_text(f"{today}\n{token}", encoding="utf-8")
    logger.info("Access token saved to %s", _TOKEN_FILE)


def _load_token_if_valid() -> str | None:
    """Return today's cached token, or None if absent/stale."""
    if not _TOKEN_FILE.exists():
        return None
    lines = _TOKEN_FILE.read_text(encoding="utf-8").strip().splitlines()
    if len(lines) < 2:
        return None
    saved_date, token = lines[0], lines[1]
    today = datetime.now(IST).strftime("%Y-%m-%d")
    if saved_date == today and token:
        logger.info("Using cached access token from %s", _TOKEN_FILE)
        return token
    return None


# ── OAuth flow ────────────────────────────────────────────────────────────────

def generate_access_token() -> str:
    """
    Run the full Fyers OAuth flow interactively:
      1. Build the auth URL and open it in the default browser.
      2. Prompt the user to paste the redirect URL after login.
      3. Extract auth_code, exchange it for an access token.
      4. Cache the token to config/token.txt.

    Returns:
        access_token (str) in the format  "<app_id>:<token>"
    """
    session = fyersModel.SessionModel(
        client_id=config.FYERS_APP_ID,
        redirect_uri=config.FYERS_REDIRECT_URI,
        response_type="code",
        grant_type="authorization_code",
        secret_key=config.FYERS_SECRET_KEY,
        state="ORBit_auth",
    )

    auth_url = session.generate_authcode()
    logger.info("Opening Fyers login page in browser…")
    print("\n" + "=" * 60)
    print("FYERS AUTHENTICATION — follow these steps:")
    print("=" * 60)
    print(f"\n1.  Your browser will open: {auth_url[:80]}…")
    print("2.  Log in with your Fyers credentials.")
    print("3.  After login you will be redirected to a URL like:")
    print("      http://127.0.0.1?auth_code=XXXXXXXXXX&state=ORBit_auth")
    print("4.  Copy that FULL URL and paste it below.\n")
    webbrowser.open(auth_url)

    redirect_url = input("Paste the redirect URL here: ").strip()
    if not redirect_url:
        raise ValueError("No redirect URL provided — authentication aborted.")

    # Extract auth_code from the redirect URL
    try:
        parsed = urlparse(redirect_url)
        params = parse_qs(parsed.query)
        auth_code = params["auth_code"][0]
    except (KeyError, IndexError) as exc:
        raise ValueError(
            f"Could not parse auth_code from URL: '{redirect_url}'. "
            "Make sure you copied the full redirect URL."
        ) from exc

    # Exchange auth_code for access_token
    session.set_token(auth_code)
    response = session.generate_token()

    if response.get("s") != "ok":
        raise RuntimeError(
            f"Token generation failed: {response.get('message', response)}"
        )

    token = response["access_token"]
    _save_token(token)
    logger.info("Access token generated successfully.")
    return token


def get_access_token() -> str:
    """
    Return a valid access token.
    Uses today's cached token if available; otherwise runs the OAuth flow.

    Returns:
        access_token (str) in the format  "<app_id>:<token>"
    """
    cached = _load_token_if_valid()
    if cached:
        return cached
    logger.info("No valid cached token found — starting auth flow.")
    return generate_access_token()


def is_token_valid() -> bool:
    """Return True if a valid token for today is already cached."""
    return _load_token_if_valid() is not None


def generate_access_token_via_redirect(
    send_fn: Optional[Callable[[str], None]] = None,
    timeout_secs: int = 600,
) -> str:
    """VPS-friendly automated token refresh.

    Starts a temporary HTTP server on OAUTH_CALLBACK_PORT, sends you the
    Fyers login URL via Telegram (or logs it), then waits for Fyers to
    redirect back with the auth_code.  The exchange is completed
    automatically and the new token is saved.

    Requires:
        * FYERS_REDIRECT_URI = http://<VPS_IP>:<OAUTH_CALLBACK_PORT>/callback
          set in .env AND in your Fyers developer portal at myapi.fyers.in.
        * Port OAUTH_CALLBACK_PORT reachable from the internet.

    Args:
        send_fn:      Callable that sends a Telegram message (e.g.
                      notifier.send_message).  If None, logs instead.
        timeout_secs: Seconds to wait for the callback (default 10 min).
    """
    from src import config

    port     = config.OAUTH_CALLBACK_PORT
    received: dict = {}
    done     = threading.Event()

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            params = parse_qs(urlparse(self.path).query)
            code   = params.get("auth_code", [None])[0]
            if code:
                received["auth_code"] = code
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<html><body style='font-family:sans-serif;padding:40px'>"
                    b"<h1>&#10003; Login successful!</h1>"
                    b"<p>Return to Telegram. The ORB Bot is now authenticated.</p>"
                    b"</body></html>"
                )
                done.set()
            else:
                self.send_response(400)
                self.end_headers()

        def log_message(self, *args):
            pass  # suppress HTTP access logs

    # Build Fyers auth URL
    session = fyersModel.SessionModel(
        client_id=config.FYERS_APP_ID,
        redirect_uri=config.FYERS_REDIRECT_URI,
        response_type="code",
        grant_type="authorization_code",
        secret_key=config.FYERS_SECRET_KEY,
        state="ORBit_auth",
    )
    auth_url = session.generate_authcode()

    msg = (
        "🔑 *Fyers token expired — tap to login:*\n\n"
        f"{auth_url}\n\n"
        "_After logging in you will see 'Login successful'. No further action needed._"
    )
    if send_fn:
        send_fn(msg)
    else:
        logger.info("Fyers auth URL: %s", auth_url)
        print(f"\nFyers login URL:\n{auth_url}\n")

    # Start temporary HTTP server (polls with 1 s timeout so we can check deadline)
    server = HTTPServer(("0.0.0.0", port), _Handler)
    server.timeout = 1

    logger.info("Waiting for OAuth callback on port %d (timeout: %ds)…", port, timeout_secs)
    deadline = time.monotonic() + timeout_secs
    while not done.is_set():
        server.handle_request()
        if time.monotonic() > deadline:
            server.server_close()
            err_msg = "⏱ Token refresh timed out (10 min). Please restart the bot and try again."
            if send_fn:
                send_fn(err_msg)
            raise TimeoutError("OAuth callback not received within timeout.")

    server.server_close()

    # Exchange auth_code for access_token
    auth_code = received["auth_code"]
    session.set_token(auth_code)
    response = session.generate_token()

    if response.get("s") != "ok":
        raise RuntimeError(f"Token generation failed: {response.get('message', response)}")

    token = response["access_token"]
    _save_token(token)
    logger.info("Access token generated successfully via OAuth redirect.")

    if send_fn:
        send_fn("✅ *Token refreshed!* Trading session will start at 09:10 IST.")

    return token
