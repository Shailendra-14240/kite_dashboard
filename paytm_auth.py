"""
Paytm Money Open API login flow. Structurally identical to Kite's: you must
log in through Paytm's actual login page (their ToS doesn't allow headless
login either), you get a request_token back on redirect, and you exchange
that for session tokens.

NOTE: Paytm's SDK (pyPMClient) isn't on PyPI and its GitHub `setup.py` doesn't
build under modern setuptools, so it's vendored in this repo at `pyPMClient/`
and imported straight from the project root. Its runtime deps (`httpx`,
`websocket-client`) are in the `paytm` extra in pyproject.toml (`uv sync --extra paytm`).

We store 3 tokens Paytm issues: access_token, public_access_token (used for
the websocket), and read_access_token. We're assuming (not 100% confirmed)
these expire daily like Kite's — if a call starts failing with an auth error
after working fine earlier, that's the signal to re-login; the dashboard
will surface that as an error state and you'll see a stale token error.
"""
import json
import os
from datetime import date
from config import config

try:
    from pyPMClient.pmClient import PMClient
except ImportError:
    PMClient = None


class PaytmTokenStore:
    def __init__(self, path: str):
        self.path = path

    def save(self, tokens: dict):
        with open(self.path, "w") as f:
            json.dump({**tokens, "date": date.today().isoformat()}, f)

    def load(self):
        if not os.path.exists(self.path):
            return None
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        if data.get("date") != date.today().isoformat():
            return None
        return data

    def clear(self):
        if os.path.exists(self.path):
            os.remove(self.path)


paytm_token_store = PaytmTokenStore(config.PAYTM_TOKEN_STORE_PATH)


class PaytmAuth:
    def __init__(self):
        if PMClient is None:
            raise RuntimeError(
                "pyPMClient isn't importable. It's vendored at pyPMClient/ in the "
                "project root — run the app from there (e.g. `uv run python app.py`)."
            )
        if not config.PAYTM_API_KEY or not config.PAYTM_API_SECRET:
            raise RuntimeError(
                "PAYTM_API_KEY / PAYTM_API_SECRET are not set. Create an app at "
                "https://developer.paytmmoney.com and fill these into .env (see README)."
            )
        self.pm = PMClient(api_key=config.PAYTM_API_KEY, api_secret=config.PAYTM_API_SECRET)

    def login_url(self) -> str:
        # state_key is just an opaque string Paytm echoes back to you; not used here.
        return self.pm.login("risk-dashboard")

    def generate_session(self, request_token: str):
        data = self.pm.generate_session(request_token=request_token)
        tokens = {
            "access_token": data.get("access_token"),
            "public_access_token": data.get("public_access_token"),
            "read_access_token": data.get("read_access_token"),
        }
        paytm_token_store.save(tokens)
        return tokens

    def get_authenticated_client(self):
        tokens = paytm_token_store.load()
        if not tokens:
            return None
        self.pm.set_access_token(tokens.get("access_token"))
        if tokens.get("public_access_token"):
            self.pm.set_public_access_token(tokens["public_access_token"])
        if tokens.get("read_access_token"):
            self.pm.set_read_access_token(tokens["read_access_token"])
        return self.pm


paytm_auth = None
if config.MARKET_DATA_PROVIDER == "paytm":
    try:
        paytm_auth = PaytmAuth() if (config.PAYTM_API_KEY and config.PAYTM_API_SECRET) else None
    except RuntimeError:
        paytm_auth = None
