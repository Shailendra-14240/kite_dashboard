"""
Thin wrapper around the kiteconnect SDK calls we need. Keeping these in one
place makes it easy to add caching / retry / mocking later.
"""
import time
import logging

logger = logging.getLogger(__name__)

_instrument_cache = {"data": None, "fetched_at": 0}
INSTRUMENT_CACHE_TTL_SECONDS = 6 * 60 * 60  # instruments don't change intraday


def get_holdings(kite):
    """Equity holdings (long-term portfolio)."""
    return kite.holdings()


def get_positions(kite):
    """Returns dict with 'net' and 'day' position lists (this is where F&O positions live)."""
    return kite.positions()


def get_funds(kite):
    """Margins/funds available across segments (equity, commodity)."""
    return kite.margins()


def get_instruments(kite, exchange=None):
    """
    Cached instrument dump. Used to map a tradingsymbol -> strike, expiry,
    instrument_type (CE/PE/FUT), and underlying name.
    """
    now = time.time()
    if _instrument_cache["data"] is None or (now - _instrument_cache["fetched_at"]) > INSTRUMENT_CACHE_TTL_SECONDS:
        logger.info("Refreshing instrument cache...")
        instruments = kite.instruments(exchange) if exchange else kite.instruments()
        by_symbol = {(i["exchange"], i["tradingsymbol"]): i for i in instruments}
        _instrument_cache["data"] = by_symbol
        _instrument_cache["fetched_at"] = now
    return _instrument_cache["data"]


def get_ltp(kite, instrument_keys):
    """
    instrument_keys: list of strings like "NSE:INFY" or "NFO:NIFTY24AUGFUT"
    Returns dict keyed by the same strings -> {"last_price": ...}

    If the LTP call itself fails (e.g. market-data permission not active on
    this app yet), we log it and return {} rather than blowing up the whole
    refresh — callers treat a missing spot price as "can't assess ITM" per
    symbol instead of losing the entire dashboard.
    """
    if not instrument_keys:
        return {}
    result = {}
    chunk_size = 200
    for i in range(0, len(instrument_keys), chunk_size):
        chunk = instrument_keys[i:i + chunk_size]
        try:
            result.update(kite.ltp(chunk))
        except Exception as e:
            logger.error(
                "kite.ltp() failed (%s) — likely a market-data permission issue on the Kite Connect app. "
                "holdings/positions/funds will still work; ITM checks will show as UNKNOWN.", e
            )
    return result


def estimate_order_margin(kite, orders):
    """
    Uses Kite's order margin API to estimate required margin for a set of
    (possibly hypothetical) orders. `orders` is a list of dicts in the format
    kiteconnect expects for order_margins(). Falls back gracefully if the
    API call fails (e.g. not supported for the segment).
    """
    try:
        return kite.order_margins(orders)
    except Exception as e:
        logger.warning("order_margins call failed: %s", e)
        return None
