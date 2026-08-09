"""
Handles the Kite Connect login flow.

Kite Connect does NOT support fully automated/headless login (this is by design,
per Zerodha's terms of service — login must go through their web page, including
2FA/TOTP). Access tokens also expire daily (around 6 AM IST). So the practical
flow is:

  1. You open the dashboard.
  2. If there's no valid token for today, you see a "Login with Kite" button.
  3. Clicking it sends you to Zerodha's login page (you log in there, normally).
  4. Zerodha redirects back to /callback with a request_token.
  5. We exchange that for an access_token and cache it (valid until it expires).

You'll need to do step 2-4 once per trading day.
"""
import json
import os
from datetime import datetime, date
from kiteconnect import KiteConnect
from config import config


class TokenStore:
    """Persists the access token + the date it was issued, so we know when it's stale."""

    def __init__(self, path: str):
        self.path = path

    def save(self, access_token: str):
        with open(self.path, "w") as f:
            json.dump({"access_token": access_token, "date": date.today().isoformat()}, f)

    def load(self):
        if not os.path.exists(self.path):
            return None
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        if data.get("date") != date.today().isoformat():
            return None  # token is from a previous day -> treat as expired
        return data.get("access_token")

    def clear(self):
        if os.path.exists(self.path):
            os.remove(self.path)


token_store = TokenStore(config.TOKEN_STORE_PATH)


class KiteAuth:
    def __init__(self):
        if not config.KITE_API_KEY or not config.KITE_API_SECRET:
            raise RuntimeError(
                "KITE_API_KEY / KITE_API_SECRET are not set. Copy .env.example to .env "
                "and fill them in (see README.md for how to get them)."
            )
        self.kite = KiteConnect(api_key=config.KITE_API_KEY)

    def login_url(self) -> str:
        return self.kite.login_url()

    def generate_session(self, request_token: str) -> str:
        """Exchange the request_token from the redirect for an access_token."""
        data = self.kite.generate_session(request_token, api_secret=config.KITE_API_SECRET)
        access_token = data["access_token"]
        token_store.save(access_token)
        return access_token

    def get_authenticated_kite(self):
        """Returns a KiteConnect instance with today's access token set, or None if not logged in."""
        access_token = token_store.load()
        if not access_token:
            return None
        self.kite.set_access_token(access_token)
        return self.kite


kite_auth = KiteAuth() if (config.KITE_API_KEY and config.KITE_API_SECRET) else None
