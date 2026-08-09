"""
Wraps Paytm Money's security master + live price API so risk_engine can ask
for a spot price of this underlying without caring whether it's coming from Kite or Paytm.
"""
import logging
import time
import csv
from io import StringIO
import re
from datetime import datetime, date # Import date as well

logger = logging.getLogger(__name__)

# Updated to only use "security_master.csv" as it contains all symbols
CANDIDATE_MASTER_FILES = [
    "security_master.csv",
]

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

def _parse_kite_fo_tradingsymbol(tradingsymbol: str, expiry_date_input) -> str:
    """
    Parses a Kite F&O tradingsymbol (e.g., "ABB26AUG6900PE") and an expiry date
    to construct a Paytm-style symbol (e.g., "ABB-Aug2026-6900-PE").
    Handles expiry_date_input as either datetime.datetime, datetime.date, or "YYYY-MM-DD" string.
    """
    # Ensure expiry_date_input is a datetime object
    if isinstance(expiry_date_input, str):
        try:
            expiry_date = datetime.strptime(expiry_date_input, "%Y-%m-%d")
        except ValueError:
            logger.error("Invalid expiry date string format: %s", expiry_date_input)
            return tradingsymbol
    elif isinstance(expiry_date_input, (datetime, date)): # Handle both datetime and date objects
        expiry_date = expiry_date_input
    else:
        logger.error("Unsupported expiry date type: %s", type(expiry_date_input))
        return tradingsymbol

    # Regex to extract components: Underlying, Day, Month, Strike, Type
    # Example: ABB26AUG6900PE
    match = re.match(r"([A-Z0-9]+)(\d{2})([A-Z]{3})(\d+)(CE|PE|FUT)", tradingsymbol)
    if not match:
        logger.warning("Could not parse Kite tradingsymbol: %s", tradingsymbol)
        return tradingsymbol # Return original if parsing fails

    underlying, day, month_abbr, strike, option_type = match.groups()

    # Format month abbreviation (e.g., AUG -> Aug)
    formatted_month = month_abbr.capitalize()

    # Get year from expiry_date
    expiry_year = expiry_date.year

    # Construct Paytm-style symbol
    # Example: ABB-Aug2026-6900-PE
    paytm_symbol = f"{underlying}-{formatted_month}{expiry_year}-{strike}-{option_type}"
    logger.debug("Parsed Kite symbol '%s' (expiry %s) to Paytm format: '%s'", tradingsymbol, expiry_date.strftime("%Y-%m-%d"), paytm_symbol)
    return paytm_symbol


def get_security_master(pm):
    """Fetches and caches the instrument master across the candidate files."""
    now = time.time()
    # The cache now stores a single list of rows from "security_master.csv"
    if _master_cache["rows"] is not None and (now - _master_cache["fetched_at"]) < MASTER_CACHE_TTL_SECONDS:
        return _master_cache["rows"]

    rows = []
    errors = []
    # Only fetch "security_master.csv"
    file_name = "security_master.csv"
    try:
        resp = pm.security_master(file_name)
        if isinstance(resp, str): # If response is CSV text
            logger.info("Raw response for %s: %s", file_name, resp[:500]) # Log first 500 chars of raw response
            csv_file = StringIO(resp)
            reader = csv.DictReader(csv_file)
            parsed_rows = list(reader)
            rows.extend(parsed_rows)
            logger.info("Parsed %s rows from %s", len(parsed_rows), file_name)
            if parsed_rows:
                logger.debug("First 5 rows of %s:", file_name)
                for i, row in enumerate(parsed_rows[:5]):
                    logger.debug("  %s", row)
        elif isinstance(resp, list):
            rows.extend(resp)
        elif isinstance(resp, dict):
            for key in ("data", "rows", "result"):
                if key in resp and isinstance(resp[key], list):
                    rows.extend(resp[key])
                    break
        logger.info("Loaded %s master file (%d rows so far)", file_name, len(rows))
    except Exception as e:
        errors.append(f"{file_name}: {e}")
        logger.error("Error loading %s: %s", file_name, e) # Log the error

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


def find_security(pm, exchange: str, symbol: str, instrument_type: str = None, kite_instrument: dict = None):
    """
    Look up a security_id for a given tradingsymbol. Returns a dict with normalized
    fields, or None if not found.
    If kite_instrument is provided, it will attempt to parse F&O symbols
    into Paytm's expected format for better matching.
    
    Matching is done purely on the symbol name (case-insensitive) against the
    security master CSV. Exchange and instrument_type are NOT used as filters
    since Paytm may use different values than Kite for those fields.
    """
    rows = get_security_master(pm)
    
    target_symbol_for_match = symbol.strip()

    if kite_instrument and kite_instrument.get("instrument_type") in ("CE", "PE", "FUT"):
        # Attempt to parse Kite F&O symbol into Paytm format
        try:
            expiry_val = kite_instrument.get("expiry")
            if expiry_val:
                paytm_formatted_symbol = _parse_kite_fo_tradingsymbol(symbol, expiry_val)
                target_symbol_for_match = paytm_formatted_symbol
                logger.debug("Mapped Kite F&O symbol '%s' to Paytm format '%s' for lookup.", symbol, target_symbol_for_match)
            else:
                logger.warning("Kite instrument for %s is missing expiry date.", symbol)
        except Exception as e:
            logger.warning("Error parsing Kite F&O symbol '%s': %s", symbol, e)

    logger.debug("Searching for security: TargetSymbol=%s (original exchange=%s, type=%s)", target_symbol_for_match, exchange, instrument_type)
    for row in rows:
        row_symbol = _first(row, SYMBOL_KEYS)
        
        if row_symbol is None:
            continue
        
        # Match purely on symbol name (case-insensitive)
        if row_symbol.strip().lower() == target_symbol_for_match.lower():
            row_exchange = _first(row, EXCH_KEYS)
            row_instrument_type = _first(row, INSTRTYPE_KEYS)
            logger.debug("Found match for '%s' in security master. Row symbol: %s, Row exchange: %s, Row instrument type: %s security_id: %s", target_symbol_for_match, row_symbol, row_exchange, row_instrument_type,_first(row, SECID_KEYS))
            return {
                "security_id": _first(row, SECID_KEYS),
                "exchange": row_exchange,
                "segment": _first(row, SEGMENT_KEYS),
                "instrument_type": row_instrument_type,
                "strike": _first(row, STRIKE_KEYS),
                "expiry": _first(row, EXPIRY_KEYS),
                "underlying": _first(row, NAME_KEYS) or row_symbol,
            }

    logger.debug("No security found for TargetSymbol=%s", target_symbol_for_match)
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

    # Construct preferences in the "exchange:scrip_id:scrip_type" string format
    preferences = [
        f"{item['exchange']}:{item['security_id']}:{item['scrip_type']}"
        for item in lookups
    ]
    logger.debug("Fetching LTP from Paytm with preferences: %s", preferences)

    resp = None
    try:
        resp = pm.get_live_market_data("LTP", preferences)
    except Exception as e:
        logger.error(
            "Paytm get_live_market_data failed with preferences %s: %s", preferences, e
        )
        return {}

    result = {}
    # Response shape is unconfirmed — handle the common possibilities.
    items = resp if isinstance(resp, list) else resp.get("data", []) if isinstance(resp, dict) else []
    for item in items:
        sec_id = str(_first(item, SECID_KEYS) or item.get("scripId") or "")
        price = item.get("ltp") or item.get("last_price") or item.get("lastTradedPrice")
        if sec_id:
            result[sec_id] = price
    logger.debug("Paytm LTP response: %s", result)
    return result
