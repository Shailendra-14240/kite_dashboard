import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Kite Connect
    KITE_API_KEY = os.getenv("KITE_API_KEY", "")
    KITE_API_SECRET = os.getenv("KITE_API_SECRET", "")
    KITE_REDIRECT_URL = os.getenv("KITE_REDIRECT_URL", "http://127.0.0.1:5000/callback")

    # Email (SMTP) alerts
    SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")  # use an app password, not your real password
    EMAIL_FROM = os.getenv("EMAIL_FROM", os.getenv("SMTP_USER", ""))
    EMAIL_TO = os.getenv("EMAIL_TO", "")

    # Risk thresholds
    ITM_WARNING_PCT = float(os.getenv("ITM_WARNING_PCT", "3.0"))   # flag "near ITM" within this % of spot
    MARGIN_BUFFER_PCT = float(os.getenv("MARGIN_BUFFER_PCT", "15.0"))  # want at least this much spare margin
    CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "5"))
    ALERT_COOLDOWN_MINUTES = int(os.getenv("ALERT_COOLDOWN_MINUTES", "30"))  # don't re-email the same flag too often

    FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
    FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))

    TOKEN_STORE_PATH = os.getenv("TOKEN_STORE_PATH", "token.json")

    # --- P&L ledger (future realized P&L tracking) ---
    PL_DB_PATH = os.getenv("PL_DB_PATH", "pl.db")
    TRADE_POLL_SECONDS = int(os.getenv("TRADE_POLL_SECONDS", "60"))  # kite.trades() poll cadence in market hours
    EOD_TRIGGER_TIME = os.getenv("EOD_TRIGGER_TIME", "15:35")  # IST; forced end-of-day snapshot anchor

    # --- Market data provider ---
    # "kite" uses kite.ltp() (needs paid Kite Connect subscription).
    # "paytm" uses your free Paytm Money Open API for spot prices, while
    # everything else (holdings/positions/funds) still comes from Kite.
    MARKET_DATA_PROVIDER = os.getenv("MARKET_DATA_PROVIDER", "kite").lower()

    PAYTM_API_KEY = os.getenv("PAYTM_API_KEY", "")
    PAYTM_API_SECRET = os.getenv("PAYTM_API_SECRET", "")
    PAYTM_REDIRECT_URL = os.getenv("PAYTM_REDIRECT_URL", "http://127.0.0.1:5000/paytm_callback")
    PAYTM_TOKEN_STORE_PATH = os.getenv("PAYTM_TOKEN_STORE_PATH", "paytm_token.json")


config = Config()
