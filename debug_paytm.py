"""
Run this once, after logging in via the dashboard, to see the *real* shape of
Paytm's security_master() and get_live_market_data() responses. Paste the
output back and the field-name guesses in paytm_market_data.py can be
corrected precisely instead of guessed.

Usage:
    python debug_paytm.py
"""
import json
import traceback # Import the traceback module
from datetime import datetime # Import datetime for simulating expiry
import logging # Import logging module

from paytm_auth import PaytmAuth, paytm_token_store
from paytm_market_data import find_security, get_security_master, get_ltp_paytm # Import get_security_master and get_ltp_paytm

# Set logging level for paytm_market_data to DEBUG
logging.getLogger('paytm_market_data').setLevel(logging.DEBUG)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main():
    tokens = paytm_token_store.load()
    if not tokens:
        print("Not logged in to Paytm yet — log in via the dashboard first, then re-run this.")
        return

    auth = PaytmAuth()
    pm = auth.get_authenticated_client()

    # Load all security master data once
    try:
        all_master_rows = get_security_master(pm)
        print(f"\n--- Loaded total {len(all_master_rows)} security master rows ---")
        # The detailed logging for individual master files is now in paytm_market_data.py
    except Exception as e:
        print(f"FAILED to load security master data: {e}")
        traceback.print_exc()
        return # Exit if master data cannot be loaded

    # Test find_security for a specific problematic F&O symbol
    print("\n--- Testing find_security for a specific F&O symbol ---")
    # Simulate a Kite instrument object for ABB26AUG6900PE
    # Assuming expiry year is 2026 based on previous context (ABB-Aug2026-6900-PE)
    sample_kite_instrument = {
        "tradingsymbol": "ABB26AUG6900PE",
        "exchange": "NSE",
        "instrument_type": "PE",
        "name": "ABB", # Underlying name
        "expiry": datetime(2026, 8, 26), # Assuming 26th August 2026
        "strike": 6900.0,
    }
    
    exchange = sample_kite_instrument["exchange"]
    symbol = sample_kite_instrument["tradingsymbol"]
    instrument_type = sample_kite_instrument["instrument_type"]

    print(f"\nSearching for: Exchange={exchange}, Symbol={symbol}, InstrumentType={instrument_type} (with kite_instrument)")
    found_info = find_security(pm, exchange, symbol, instrument_type=instrument_type, kite_instrument=sample_kite_instrument)
    if found_info:
        print(f"FOUND: {json.dumps(found_info, indent=2, default=str)}")

        # Now try to fetch LTP for this found F&O contract
        print(f"\n--- Fetching LTP for {symbol} using found info ---")
        paytm_scrip_type = ""
        if instrument_type in ("CE", "PE"):
            paytm_scrip_type = "OPTION"
        elif instrument_type == "FUT":
            paytm_scrip_type = "FUTURE"
        
        if paytm_scrip_type:
            ltp_lookups = [{
                "exchange": found_info["exchange"],
                "security_id": found_info["security_id"],
                "scrip_type": paytm_scrip_type
            }]
            ltp_result = get_ltp_paytm(pm, ltp_lookups)
            print(f"LTP for {symbol}: {json.dumps(ltp_result, indent=2, default=str)}")
        else:
            print(f"Could not determine Paytm scrip_type for {instrument_type}")

    else:
        print(f"NOT FOUND: No security info for {symbol} on {exchange} with type {instrument_type}")
    print("-" * 50)

    print("\n--- get_live_market_data('LTP', [NIFTY 50 guess]) ---")
    try:
        # Corrected preferences format: "exchange:scrip_id:scrip_type"
        resp = pm.get_live_market_data("LTP", ["NSE:13:INDEX"])
        print(json.dumps(resp, indent=2, default=str))
    except Exception as e:
        print(f"FAILED: {e}")
        traceback.print_exc() # Print full traceback here

if __name__ == "__main__":
    main()
