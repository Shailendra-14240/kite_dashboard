"""
Risk engine: looks at open F&O positions, figures out which ones are close to
(or already) ITM, and cross-checks that against available cash/margin.

IMPORTANT LIMITATIONS (read before trusting this blindly):
- This is a heuristic monitoring aid, not a margin calculator. Always verify
  actual margin/settlement obligations in Kite Console before acting.
- Underlying spot price is fetched via LTP; for indices we map common names
  (NIFTY, BANKNIFTY, FINNIFTY, SENSEX) to their index quote. Anything else
  falls back to "NSE:<name>" which works for most stock F&O.
- Margin-if-ITM is an *estimate* using Kite's order_margins() API against the
  existing position size, not a true stress test at a hypothetical price.
- Physical settlement risk (stock F&O settle in shares, not cash) is flagged
  near expiry but the exact obligation should be confirmed manually.
"""
from datetime import date, datetime
import logging

from kite_client import estimate_order_margin
from market_data import get_spot_prices
from config import config

logger = logging.getLogger(__name__)

INDEX_SPOT_MAP = {
    "NIFTY": "NSE:NIFTY 50",
    "BANKNIFTY": "NSE:NIFTY BANK",
    "FINNIFTY": "NSE:NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NSE:NIFTY MID SELECT",
    "SENSEX": "BSE:SENSEX",
    "BANKEX": "BSE:BANKEX",
}


def _underlying_info(name: str) -> dict:
    if name in INDEX_SPOT_MAP:
        exch, sym = INDEX_SPOT_MAP[name].split(":", 1)
        return {"exchange": exch, "symbol": sym, "scrip_type": "INDEX"}
    return {"exchange": "NSE", "symbol": name, "scrip_type": "EQUITY"}


def _key(info: dict) -> str:
    return f"{info['exchange']}:{info['symbol']}"


def _days_to_expiry(expiry) -> int:
    if expiry is None:
        return 999
    if isinstance(expiry, datetime):
        expiry = expiry.date()
    return (expiry - date.today()).days


def analyze_positions(kite, positions, funds, instruments_nfo, instruments_bfo=None):
    """
    Returns a list of position-risk dicts and a list of alert dicts.
    """
    net_positions = positions.get("net", [])
    fo_positions = [p for p in net_positions if p.get("exchange") in ("NFO", "BFO") and p.get("quantity", 0) != 0]

    if not fo_positions:
        return [], [], _funds_summary(funds)

    # Map positions to instrument metadata
    instruments = dict(instruments_nfo)
    if instruments_bfo:
        instruments.update(instruments_bfo)

    underlying_keys = set()
    underlying_infos = {}
    enriched = []
    for p in fo_positions:
        info = instruments.get((p["exchange"], p["tradingsymbol"]))
        if not info or info.get("instrument_type") not in ("CE", "PE"):
            # Futures or unknown -> keep basic info, no ITM concept
            enriched.append({"position": p, "instrument": info, "is_option": False})
            continue
        u_info = _underlying_info(info["name"])
        underlying_keys.add(_key(u_info))
        underlying_infos[_key(u_info)] = u_info
        enriched.append({"position": p, "instrument": info, "is_option": True})

    spot_prices = get_spot_prices(kite, list(underlying_infos.values())) if underlying_infos else {}

    results = []
    alerts = []

    for item in enriched:
        p, info, is_option = item["position"], item["instrument"], item["is_option"]
        qty = p["quantity"]
        side = "SHORT" if qty < 0 else "LONG"

        row = {
            "tradingsymbol": p["tradingsymbol"],
            "exchange": p["exchange"],
            "quantity": qty,
            "side": side,
            "average_price": p.get("average_price"),
            "last_price": p.get("last_price"),
            "pnl": p.get("pnl"),
            "is_option": is_option,
            "status": "OK",
            "notes": [],
        }

        if not is_option:
            # Futures position -> just flag physical settlement proximity for stock futures
            if info and info.get("segment") == "NFO-FUT":
                dte = _days_to_expiry(info.get("expiry"))
                row["days_to_expiry"] = dte
                if dte <= 2:
                    row["status"] = "WARNING"
                    row["notes"].append(
                        f"Stock future expires in {dte} day(s). If not squared off, settlement is against "
                        f"the underlying share price — make sure you intend to hold to expiry."
                    )
            results.append(row)
            continue

        strike = info.get("strike")
        u_info = _underlying_info(info["name"])
        underlying_key = _key(u_info)
        print(underlying_key)
        spot = spot_prices.get(underlying_key)
        opt_type = info.get("instrument_type")
        dte = _days_to_expiry(info.get("expiry"))

        row.update({
            "underlying": info["name"],
            "strike": strike,
            "option_type": opt_type,
            "spot": spot,
            "days_to_expiry": dte,
        })

        if spot is None or not strike:
            row["status"] = "UNKNOWN"
            row["notes"].append("Could not fetch underlying spot price to assess ITM proximity.")
            results.append(row)
            continue

        if opt_type == "CE":
            distance_pct = (strike - spot) / spot * 100
        else:  # PE
            distance_pct = (spot - strike) / spot * 100

        row["distance_to_itm_pct"] = round(distance_pct, 2)
        is_itm = distance_pct <= 0
        is_near_itm = 0 < distance_pct <= config.ITM_WARNING_PCT

        if is_itm:
            row["status"] = "ITM"
        elif is_near_itm:
            row["status"] = "NEAR_ITM"

        # Risk is highest for SHORT option positions moving/already ITM
        if side == "SHORT" and (is_itm or is_near_itm):
            severity = "CRITICAL" if is_itm else "WARNING"
            row["status"] = severity
            msg = (
                f"{p['tradingsymbol']} (SHORT {opt_type}) is "
                f"{'already ITM' if is_itm else f'within {distance_pct:.2f}% of going ITM'} "
                f"(spot {spot:.2f} vs strike {strike})."
            )
            row["notes"].append(msg)

            # Estimate margin impact
            margin_est = estimate_order_margin(kite, [{
                "exchange": p["exchange"],
                "tradingsymbol": p["tradingsymbol"],
                "transaction_type": "BUY" if qty < 0 else "SELL",  # covering order direction
                "variety": "regular",
                "product": p.get("product", "NRML"),
                "order_type": "MARKET",
                "quantity": abs(qty),
            }])
            available_cash = _available_cash(funds)
            if margin_est:
                required = margin_est[0].get("total", 0) if isinstance(margin_est, list) else None
                row["est_cover_order_margin"] = required
                if required is not None and available_cash is not None:
                    buffer_needed = required * (1 + config.MARGIN_BUFFER_PCT / 100)
                    if available_cash < buffer_needed:
                        row["status"] = "CRITICAL"
                        alerts.append({
                            "level": "CRITICAL",
                            "symbol": p["tradingsymbol"],
                            "message": (
                                f"{msg} Estimated margin to cover/exit this position is ~₹{required:,.0f}, "
                                f"but available cash is only ~₹{available_cash:,.0f}. "
                                f"Consider closing or hedging this position, or adding funds."
                            ),
                        })
                    else:
                        alerts.append({"level": severity, "symbol": p["tradingsymbol"], "message": msg})
                else:
                    alerts.append({"level": severity, "symbol": p["tradingsymbol"], "message": msg})
            else:
                alerts.append({"level": severity, "symbol": p["tradingsymbol"], "message": msg})

        elif is_itm or is_near_itm:
            # LONG option near/at ITM -> not a loss risk, but flag physical settlement if stock option near expiry
            row["notes"].append(
                f"{p['tradingsymbol']} (LONG {opt_type}) is "
                f"{'ITM' if is_itm else 'near ITM'} (spot {spot:.2f} vs strike {strike})."
            )
            if dte <= 2 and info.get("segment", "").startswith("NFO-OPT") and info["name"] not in INDEX_SPOT_MAP:
                row["notes"].append(
                    "This is a stock option expiring within 2 days — stock options settle physically. "
                    "If ITM at expiry you may need to pay/receive full contract value in shares. Verify funds/holdings."
                )
                alerts.append({
                    "level": "INFO",
                    "symbol": p["tradingsymbol"],
                    "message": f"{p['tradingsymbol']} is ITM and expires in {dte} day(s) — physical settlement risk. Check you have funds/shares to settle, or plan to square off.",
                })

        results.append(row)

    return results, alerts, _funds_summary(funds)


def _available_cash(funds):
    try:
        return funds["equity"]["available"]["live_balance"]
    except (KeyError, TypeError):
        return None


def _funds_summary(funds):
    try:
        eq = funds["equity"]
        return {
            "available_cash": eq["available"]["live_balance"],
            "used_margin": eq["utilised"]["debits"],
            "net": eq["net"],
        }
    except (KeyError, TypeError):
        return {"available_cash": None, "used_margin": None, "net": None}
