"""
Run this once, after logging in via the dashboard, to see the *real* shape of
Paytm's security_master() and get_live_market_data() responses. Paste the
output back and the field-name guesses in paytm_market_data.py can be
corrected precisely instead of guessed.

Usage:
    python debug_paytm.py
"""
import json
from paytm_auth import PaytmAuth, paytm_token_store

def main():
    tokens = paytm_token_store.load()
    if not tokens:
        print("Not logged in to Paytm yet — log in via the dashboard first, then re-run this.")
        return

    auth = PaytmAuth()
    pm = auth.get_authenticated_client()

    for file_name in ["NSE_EQ", "NSE_FO", "BSE_EQ", "NSE_INDEX"]:
        print(f"\n--- security_master('{file_name}') ---")
        try:
            resp = pm.security_master(file_name)
            if isinstance(resp, list):
                print(f"Got list of {len(resp)} rows. First row:")
                print(json.dumps(resp[0], indent=2, default=str) if resp else "(empty)")
            else:
                print(f"Got type {type(resp)}:")
                print(str(resp)[:1000])
        except Exception as e:
            print(f"FAILED: {e}")

    print("\n--- get_live_market_data('LTP', [NIFTY 50 guess]) ---")
    try:
        resp = pm.get_live_market_data("LTP", [{
            "actionType": "ADD", "modeType": "LTP",
            "scripType": "INDEX", "exchangeType": "NSE", "scripId": "13"
        }])
        print(json.dumps(resp, indent=2, default=str))
    except Exception as e:
        print(f"FAILED: {e}")

if __name__ == "__main__":
    main()
