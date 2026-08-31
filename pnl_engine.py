"""
Computation layer over the ledger: realized P&L per symbol/day and the
OTM-expiry resolver.

Realized P&L uses a running-average model per tradingsymbol, anchored to the
latest snapshot before the day (so positions opened before the ledger existed
still settle correctly). Options/futures that expired OTM vanish without any
trade — the resolver reconstructs those from the snapshot diff + expiry date
and realizes the full premium at a settlement value of zero.
"""
import logging
from datetime import date, datetime, timedelta

from ledger import ledger

logger = logging.getLogger(__name__)


def _anchor_state(symbol, day):
    """Seed (quantity, average_price) for a symbol on `day` from the newest snapshot before it."""
    state = ledger.latest_position_state(symbol, day)
    if state and state["quantity"] is not None:
        return state["quantity"], state["average_price"] or 0
    state = ledger.latest_holdings_state(symbol, day)
    if state and state["quantity"] is not None:
        return state["quantity"], state["average_price"] or 0
    return 0.0, 0.0


def realized_for_symbol(symbol, day):
    """
    Realized P&L for one symbol on one day:
        running-average position + that day's trades (+ expiry settlements).
    Returns dict: pnl, trades, settlements, anchor.
    """
    trades = ledger.trades_for(symbol, day)
    settlements = ledger.settlements_for(symbol, day)

    net_settle_qty = sum(s["quantity"] or 0 for s in settlements)
    net_settle_pnl = sum(s["realized_pnl"] or 0 for s in settlements)

    pos, avg = _anchor_state(symbol, day)
    realized = net_settle_pnl

    if net_settle_qty:
        # Expired portion leaves the anchored position (e.g. short 100, 60
        # expired -> 40 still open and must keep the original avg price).
        pos = pos - net_settle_qty
        if abs(pos) < 1e-9:
            pos, avg = 0.0, 0.0

    for t in sorted(trades, key=lambda x: x.get("exchange_timestamp") or ""):
        qty = float(t["quantity"]) * (1 if t["transaction_type"] == "BUY" else -1)

        if pos == 0 or (pos > 0) == (qty > 0):
            # Opening or averaging in the same direction.
            total = pos + qty
            avg = (avg * pos + t["price"] * qty) / total if total else avg
            pos = total
        elif abs(qty) <= abs(pos):
            # Reducing an existing position.
            if pos > 0:
                realized += (t["price"] - avg) * abs(qty)
            else:
                realized += (avg - t["price"]) * abs(qty)
            pos += qty
        else:
            # Fully closing and flipping into the opposite direction.
            if pos > 0:
                realized += (t["price"] - avg) * abs(pos)
            else:
                realized += (avg - t["price"]) * abs(pos)
            pos = qty + pos  # flips sign
            avg = t["price"]

    return {
        "pnl": round(realized, 2),
        "trades": len(trades),
        "settlements": settlements,
        "end_qty": pos,
        "end_avg": round(avg, 2) if pos else None,
    }


def day_realized(day):
    """Total realized P&L for a calendar day across all symbols."""
    symbols = set()
    symbols.update(r["tradingsymbol"] for r in _rows("SELECT DISTINCT tradingsymbol FROM trades WHERE trade_date=?", (day,)))
    symbols.update(r["tradingsymbol"] for r in _rows("SELECT DISTINCT tradingsymbol FROM settlements WHERE trade_date=?", (day,)))
    total = 0.0
    detail = []
    for sym in sorted(symbols):
        r = realized_for_symbol(sym, day)
        if r["pnl"] or r["trades"] or r["settlements"]:
            detail.append({"symbol": sym, **r})
            total += r["pnl"]
    return {"day": day, "pnl": round(total, 2), "detail": detail}


def days_realized():
    """Per-day realized P&L for every day that has trades or settlements."""
    days = set()
    days.update(r["d"] for r in _rows("SELECT DISTINCT trade_date AS d FROM trades"))
    days.update(r["d"] for r in _rows("SELECT DISTINCT trade_date AS d FROM settlements"))
    out = []
    for d in sorted(days):
        out.append(day_realized(d))
    return out


def totals():
    rows = days_realized()
    return {
        "total": round(sum(r["pnl"] for r in rows), 2),
        "days": len(rows),
    }


# ---------------------------------------------------------------------------
# OTM expiry resolver
# ---------------------------------------------------------------------------

def resolve_expiries(today_positions, strategy="otm_default"):
    """
    Called after each refresh with the current net positions.

    For every option that existed in the latest prior snapshot but is ABSENT
    now with no recorded trade today, and whose expiry has passed — record an
    expiry settlement. Per the user's workflow, ITM positions are always
    closed with real trades, so a vanished-without-trade position is treated
    as OTM: full premium realized, settlement value 0.

    Returns the number of settlements recorded.
    """
    today = date.today().isoformat()
    now_keys = {p["tradingsymbol"]: p for p in today_positions if p.get("quantity", 0) != 0}

    # Find the prior snapshot set: the newest snapshot strictly before today.
    rows = _rows(
        "SELECT * FROM position_snapshots WHERE ts < ? ORDER BY ts DESC LIMIT 1",
        (today + " 00:00:00",))
    created = 0
    if not rows:
        return 0
    prior_ts = rows[0]["ts"]
    prior_rows = _rows(
        "SELECT * FROM position_snapshots WHERE ts=?", (prior_ts,))

    for r in prior_rows:
        symbol = r["tradingsymbol"]
        if symbol in now_keys:
            continue  # still open
        qty = r["quantity"]
        inst_type = r["instrument_type"]
        expiry = r["expiry"]
        if qty == 0 or inst_type not in ("CE", "PE", "FUT"):
            continue
        if not expiry or expiry > today:
            logger.debug("Position %s disappeared but expiry %s not passed — skipping", symbol, expiry)
            continue
        # Any trade since the prior snapshot means the position was closed by
        # a real fill (captured by polling), not by expiry.
        trades_since = _rows(
            "SELECT 1 FROM trades WHERE tradingsymbol=? AND exchange_timestamp >= ? LIMIT 1",
            (symbol, prior_ts))
        if trades_since:
            continue

        already = _rows(
            "SELECT 1 FROM settlements WHERE tradingsymbol=? AND trade_date=? LIMIT 1",
            (symbol, expiry))
        if already:
            continue

        avg = r["average_price"] or 0
        settle = 0.0  # OTM -> worthless
        realized = (settle - avg) * qty  # qty signed: short (+) keeps premium
        note = (
            f"Expired {expiry} without a closing trade — treated as OTM "
            f"(settles at zero). {'Short' if qty < 0 else 'Long'} option "
            f"realizes {realized:+.2f}."
        )
        # Attribute the P&L to the expiry date itself, not the day we
        # happened to notice the position vanish.
        ledger.add_settlement(
            trade_date=expiry,
            exchange=r["exchange"],
            symbol=symbol,
            kind="otm_expiry",
            quantity=qty,
            avg_premium=avg,
            settle_amount=settle,
            realized_pnl=round(realized, 2),
            estimated=1 if strategy == "otm_default" else 0,
            note=note,
        )
        logger.info("OTM expiry settlement recorded for %s (%+.2f)", symbol, realized)
        created += 1
    if created:
        logger.info("Recorded %d expiry settlement(s)", created)
    return created


def _rows(sql, params=()):
    return [dict(r) for r in ledger.fetch(sql, params)]