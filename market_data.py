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
            print("I already found the key and symbol")
            sec_id, exch = known["security_id"], known["exchange"]
        else:
            # Pass scrip_type as instrument_type for underlying lookup
            info = find_security(pm, u["exchange"], u["symbol"], instrument_type=u.get("scrip_type", "EQUITY"))
            if not info or not info.get("security_id"):
                logger.warning("Could not resolve Paytm security_id for %s", key)
                continue
            sec_id, exch = info["security_id"], info["exchange"]

        lookups.append({"exchange": exch, "security_id": sec_id, "scrip_type": u.get("scrip_type", "EQUITY")})
        key_by_secid[str(sec_id)] = key
        print(key_by_secid, key , sec_id , "PRinting the values")

    prices_by_secid = get_ltp_paytm(pm, lookups)
    result = {f"{u['exchange']}:{u['symbol']}": None for u in underlyings}
    for sec_id, price in prices_by_secid.items():
        print("I am here at Line 70: sec_id:  %s , price: %s",sec_id,price)
        key = key_by_secid.get(sec_id)
        print("Printing the Key %s",key)
        if key:
            result[key] = price
    return result


def get_fo_contract_ltp_paytm(kite_fo_instruments):
    """
    Fetches LTP for a list of Kite F&O instrument dictionaries using Paytm Money API.
    kite_fo_instruments: list of Kite instrument dicts (e.g., from kite.instruments())
    Returns: dict keyed by Kite tradingsymbol -> last_price (float) or None
    """
    from paytm_auth import paytm_auth
    from paytm_market_data import find_security, get_ltp_paytm

    if not kite_fo_instruments:
        return {}

    if paytm_auth is None:
        logger.error("Paytm provider selected but not configured/logged in.")
        return {inst["tradingsymbol"]: None for inst in kite_fo_instruments}

    pm = paytm_auth.get_authenticated_client()
    if pm is None:
        logger.error("Not logged in to Paytm (or token expired for today).")
        return {inst["tradingsymbol"]: None for inst in kite_fo_instruments}

    lookups = []
    key_by_secid = {}
    for inst in kite_fo_instruments:
        tradingsymbol = inst["tradingsymbol"]
        print("I am inside get_fo_contract_ltp_paytm %s",tradingsymbol)
        exchange = inst["exchange"]
        instrument_type = inst["instrument_type"] # CE, PE, FUT from Kite

        # Use the enhanced find_security to map Kite F&O instrument to Paytm security_id
        # This 'info' dict contains the Paytm-specific exchange and instrument_type
        info = find_security(pm, exchange, tradingsymbol, instrument_type=instrument_type, kite_instrument=inst)
        
        if not info or not info.get("security_id"):
            logger.warning("Could not resolve Paytm security_id for F&O contract %s:%s", exchange, tradingsymbol)
            continue
        
        sec_id = info["security_id"]
        # Use the exchange found in Paytm's security master
        paytm_exchange = info["exchange"]
        # Map NFO to NSE for live market data preferences, if applicable
        if paytm_exchange.upper() == "NFO":
            paytm_exchange = "NSE"

        # Map Paytm's instrument_type (e.g., OPTIDX, FUTSTK) to generic scrip_type (OPTION, FUTURE)
        paytm_master_instrument_type = info.get("instrument_type", "").upper()
        paytm_scrip_type = ""
        if "OPT" in paytm_master_instrument_type: # OPTIDX, OPTSTK
            paytm_scrip_type = "OPTION"
        elif "FUT" in paytm_master_instrument_type: # FUTIDX, FUTSTK
            paytm_scrip_type = "FUTURE"
        else:
            logger.warning("Unknown Paytm master instrument_type for scrip_type mapping: %s for %s", paytm_master_instrument_type, tradingsymbol)
            continue

        lookups.append({"exchange": paytm_exchange, "security_id": sec_id, "scrip_type": paytm_scrip_type})
        key_by_secid[str(sec_id)] = tradingsymbol # Map Paytm sec_id back to Kite tradingsymbol
        print("I am at Line 135",key_by_secid)
        logger.debug("Mapped sec_id '%s' -> Kite tradingsymbol '%s'", str(sec_id), tradingsymbol)

    logger.debug("key_by_secid mapping: %s", key_by_secid)
    prices_by_secid = get_ltp_paytm(pm, lookups)
    logger.debug("prices_by_secid keys: %s", list(prices_by_secid.keys()))
    
    result = {inst["tradingsymbol"]: None for inst in kite_fo_instruments}
    for sec_id, price in prices_by_secid.items():
        kite_tradingsymbol = key_by_secid.get(sec_id)
        if kite_tradingsymbol:
            result[kite_tradingsymbol] = price
            print(result,"Printing the RESULT")
            logger.debug("Mapped price: sec_id '%s' -> '%s' = %s", sec_id, kite_tradingsymbol, price)
        else:
            # Try to find a match by stripping/normalizing the sec_id
            logger.warning(
                "sec_id '%s' from LTP response not found in key_by_secid. "
                "Available keys: %s", sec_id, list(key_by_secid.keys())
            )
    
    # Log any positions that still have None prices
    missing = [sym for sym, price in result.items() if price is None]
    if missing:
        logger.warning("F&O contracts with no Paytm LTP: %s", missing)
    
    return result
