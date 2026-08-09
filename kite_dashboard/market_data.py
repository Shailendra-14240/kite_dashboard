"""
Abstraction so risk_engine.py can ask "what's the spot price of this
underlying" without caring whether it's coming from Kite or Paytm.
"""
import logging
from config import config
from kite_client import get_ltp as kite_get_ltp

logger = logging.getLogger(__name__)


def get_spot_prices(kite, underlyings):
    """
    underlyings: list of dicts {"exchange": "NSE", "symbol": "INFY", "scrip_type": "EQUITY"}
                 scrip_type only matters for the Paytm path; EQUITY for stocks, INDEX for indices.
    Returns: dict keyed by f"{exchange}:{symbol}" -> last_price (float) or None
    """
    if not underlyings:
        return {}

    if config.MARKET_DATA_PROVIDER == "paytm":
        return _get_spot_prices_paytm(underlyings)
    return _get_spot_prices_kite(kite, underlyings)


def _get_spot_prices_kite(kite, underlyings):
    keys = [f"{u['exchange']}:{u['symbol']}" for u in underlyings]
    ltp_data = kite_get_ltp(kite, keys)
    return {k: (v.get("last_price") if v else None) for k, v in ltp_data.items()} | {
        k: None for k in keys if k not in ltp_data
    }


def _get_spot_prices_paytm(underlyings):
    from paytm_auth import paytm_auth
    from paytm_market_data import find_security, get_ltp_paytm, KNOWN_INDEX_IDS

    if paytm_auth is None:
        logger.error("Paytm provider selected but not configured/logged in.")
        return {f"{u['exchange']}:{u['symbol']}": None for u in underlyings}

    pm = paytm_auth.get_authenticated_client()
    if pm is None:
        logger.error("Not logged in to Paytm (or token expired for today).")
        return {f"{u['exchange']}:{u['symbol']}": None for u in underlyings}

    lookups = []
    key_by_secid = {}
    for u in underlyings:
        key = f"{u['exchange']}:{u['symbol']}"
        known = KNOWN_INDEX_IDS.get(u["symbol"])
        if known:
            sec_id, exch = known["security_id"], known["exchange"]
        else:
            info = find_security(pm, u["exchange"], u["symbol"])
            if not info or not info.get("security_id"):
                logger.warning("Could not resolve Paytm security_id for %s", key)
                continue
            sec_id, exch = info["security_id"], info["exchange"]

        lookups.append({"exchange": exch, "security_id": sec_id, "scrip_type": u.get("scrip_type", "EQUITY")})
        key_by_secid[str(sec_id)] = key

    prices_by_secid = get_ltp_paytm(pm, lookups)
    result = {f"{u['exchange']}:{u['symbol']}": None for u in underlyings}
    for sec_id, price in prices_by_secid.items():
        key = key_by_secid.get(sec_id)
        if key:
            result[key] = price
    return result
