"""
Wraps Paytm Money's security master + live price API so risk_engine can ask
for a spot price the same way regardless of which provider is configured.

IMPORTANT — read before trusting this blindly:
Paytm's exact security_master() response schema (column names, valid
file_name values) isn't something I could verify without live credentials.
This module is written defensively (tries several likely file_name values,
looks for several likely column-name variants) but if it can't confidently
map a symbol it will say so clearly rather than silently guessing wrong.

Run `python debug_paytm.py` once after logging in — it dumps the raw
security_master() response so field names can be locked down exactly if
the defaults below don't match.
"""
import logging
import time

logger = logging.getLogger(__name__)

# Best-effort guesses at valid file_name values for security_master().
# If none of these work, debug_paytm.py's error output will show what
# Paytm's API actually rejected/accepted.
CANDIDATE_MASTER_FILES = ["NSE_EQ", "NSE_FO", "BSE_EQ", "NSE_INDEX"]

# Common column-name variants seen across Paytm's docs/SDK examples.
SYMBOL_KEYS = ("trading_symbol", "tradingsymbol", "symbol", "scrip_name")
SECID_KEYS = ("security_id", "securityId", "scrip_id", "scripId")
EXCH_KEYS = ("exchange", "exchange_type", "exchangeType")
SEGMENT_KEYS = ("segment", "segment_type")
INSTRTYPE_KEYS = ("instrument_type", "series", "scrip_type", "scripType")
STRIKE_KEYS = ("strike_price", "strike")
EXPIRY_KEYS = ("expiry", "expiry_date")
NAME_KEYS = ("underlying_symbol", "name", "underlying")

_master_cache = {"rows": None, "fetched_at": 0}
MASTER_CACHE_TTL_SECONDS = 6 * 60 * 60


def _first(d: dict, keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def get_security_master(pm):
    """Fetches and caches the instrument master across the candidate files."""
    now = time.time()
    if _master_cache["rows"] is not None and (now - _master_cache["fetched_at"]) < MASTER_CACHE_TTL_SECONDS:
        return _master_cache["rows"]

    rows = []
    errors = []
    for file_name in CANDIDATE_MASTER_FILES:
        try:
            resp = pm.security_master(file_name)
            # Response could be a list of dicts, or a dict with a data/rows key, or CSV text.
            if isinstance(resp, list):
                rows.extend(resp)
            elif isinstance(resp, dict):
                for key in ("data", "rows", "result"):
                    if key in resp and isinstance(resp[key], list):
                        rows.extend(resp[key])
                        break
            logger.info("Loaded %s master file (%d rows so far)", file_name, len(rows))
        except Exception as e:
            errors.append(f"{file_name}: {e}")

    if not rows:
        raise RuntimeError(
            "Could not load any Paytm security_master() file. Tried: "
            f"{CANDIDATE_MASTER_FILES}. Errors: {errors}. "
            "Run debug_paytm.py to inspect the raw response and fix CANDIDATE_MASTER_FILES "
            "in paytm_market_data.py."
        )

    _master_cache["rows"] = rows
    _master_cache["fetched_at"] = now
    return rows


def find_security(pm, exchange: str, symbol: str, instrument_type: str = None):
    """
    Look up a security_id for a given exchange + tradingsymbol (and optionally
    instrument_type, e.g. 'EQUITY' / 'INDEX'). Returns a dict with normalized
    fields, or None if not found.
    """
    rows = get_security_master(pm)
    symbol_upper = symbol.strip().upper()
    for row in rows:
        row_symbol = _first(row, SYMBOL_KEYS)
        row_exchange = _first(row, EXCH_KEYS)
        if row_symbol is None or row_exchange is None:
            continue
        if row_symbol.strip().upper() == symbol_upper and row_exchange.strip().upper() == exchange.upper():
            return {
                "security_id": _first(row, SECID_KEYS),
                "exchange": row_exchange,
                "segment": _first(row, SEGMENT_KEYS),
                "instrument_type": _first(row, INSTRTYPE_KEYS),
                "strike": _first(row, STRIKE_KEYS),
                "expiry": _first(row, EXPIRY_KEYS),
                "underlying": _first(row, NAME_KEYS) or row_symbol,
            }
    return None


# Known-good index security IDs reported by users on Paytm's own forum.
# Treat as a fallback, not gospel — verify against debug_paytm.py output.
KNOWN_INDEX_IDS = {
    "NIFTY 50": {"security_id": "13", "exchange": "NSE"},
    "NIFTY BANK": {"security_id": "25", "exchange": "NSE"},
}


def get_ltp_paytm(pm, lookups):
    """
    lookups: list of dicts, each with exchange, security_id, scrip_type
             (scrip_type in EQUITY/INDEX/FUTURE/OPTION/ETF).
    Returns dict keyed by security_id -> last_price (float) or None if missing.
    """
    if not lookups:
        return {}
    preferences = [
        {
            "actionType": "ADD",
            "modeType": "LTP",
            "scripType": item["scrip_type"],
            "exchangeType": item["exchange"],
            "scripId": str(item["security_id"]),
        }
        for item in lookups
    ]
    try:
        resp = pm.get_live_market_data("LTP", preferences)
    except Exception as e:
        logger.error("Paytm get_live_market_data failed: %s", e)
        return {}

    result = {}
    # Response shape is unconfirmed — handle the common possibilities.
    items = resp if isinstance(resp, list) else resp.get("data", []) if isinstance(resp, dict) else []
    for item in items:
        sec_id = str(_first(item, SECID_KEYS) or item.get("scripId") or "")
        price = item.get("ltp") or item.get("last_price") or item.get("lastTradedPrice")
        if sec_id:
            result[sec_id] = price
    return result
