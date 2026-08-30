import logging
import time
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from auth import kite_auth
from kite_client import get_holdings, get_positions, get_funds, get_instruments
from risk_engine import analyze_positions
from notifier import send_email, build_alert_email
from config import config
from ledger import ledger
from pnl_engine import resolve_expiries

logger = logging.getLogger(__name__)

# Shared in-memory state the Flask routes read from
state = {
    "last_updated": None,
    "holdings": [],
    "positions": [],
    "funds_summary": {},
    "alerts": [],
    "error": None,
}

_last_alert_sent = {}  # symbol+level -> datetime, for cooldown dedupe

MARKET_OPEN = (9, 15)
MARKET_CLOSE = (15, 40)  # a few minutes past close, to catch expiry settlements


def _in_market_hours() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    hm = (now.hour, now.minute)
    return MARKET_OPEN <= hm <= MARKET_CLOSE


def poll_trades():
    """Capture every fill while the market is open (kite.trades() only returns today's)."""
    kite = kite_auth.get_authenticated_kite() if kite_auth else None
    if kite is None or not _in_market_hours():
        return
    try:
        ledger.record_trades(kite.trades())
    except Exception as e:
        logger.warning("Trade poll failed: %s", e)


def _enriched_positions(positions, instruments):
    """Raw net positions + instrument metadata (expiry/type/strike) for ledger snapshots."""
    rows = []
    for p in positions.get("net", []):
        if p.get("quantity", 0) == 0:
            continue
        info = instruments.get((p["exchange"], p["tradingsymbol"])) if instruments else None
        rows.append({
            "exchange": p["exchange"],
            "tradingsymbol": p["tradingsymbol"],
            "quantity": p["quantity"],
            "average_price": p.get("average_price"),
            "last_price": p.get("last_price"),
            "expiry": (info or {}).get("expiry"),
            "instrument_type": (info or {}).get("instrument_type"),
            "name": (info or {}).get("name"),
            "strike": (info or {}).get("strike"),
        })
    return rows


def _record_ledger(positions, holdings, instruments_nfo, kind="interval"):
    """Snapshot positions/holdings into the ledger and resolve OTM expiries."""
    try:
        enriched = _enriched_positions(positions, instruments_nfo)
        ledger.record_positions(enriched, kind=kind)
        ledger.record_holdings(holdings, kind=kind)
        resolve_expiries(enriched)
    except Exception as e:
        logger.warning("Ledger snapshot failed: %s", e)


def eod_snapshot():
    """Forced end-of-day snapshot (~15:35 IST) — the anchor for next day's P&L."""
    logger.info("EOD snapshot: refreshing + recording")
    kite = kite_auth.get_authenticated_kite() if kite_auth else None
    if kite is None:
        return
    try:
        holdings = get_holdings(kite)
        positions = get_positions(kite)
        instruments_nfo = get_instruments(kite, "NFO")
        _record_ledger(positions, holdings, instruments_nfo, kind="eod")
    except Exception as e:
        logger.warning("EOD snapshot failed: %s", e)


def refresh_and_check():
    kite = kite_auth.get_authenticated_kite() if kite_auth else None
    if kite is None:
        state["error"] = "Not logged in to Kite (or token expired for today). Click 'Login with Kite'."
        return

    # Each step runs independently: a failure in one (e.g. funds, market data)
    # must not blank out holdings/positions. Failures land in `errors`, which
    # the dashboard shows as a banner while still rendering whatever succeeded.
    errors = []

    def step(step_name, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            msg = f"'{step_name}': {type(e).__name__}: {e}"
            errors.append(msg)
            logger.warning("Step %s failed: %s", step_name, e)
            return None

    holdings = step("get_holdings", get_holdings, kite) or []
    positions = step("get_positions", get_positions, kite) or {}
    funds = step("get_funds", get_funds, kite)
    instruments_nfo = step("get_instruments(NFO)", get_instruments, kite, "NFO")

    _record_ledger(positions, holdings, instruments_nfo)

    risk_rows, alerts, funds_summary = _fallback_rows(positions), [], _funds_summary_none()
    if instruments_nfo is not None:
        try:
            risk_rows, alerts, funds_summary = analyze_positions(kite, positions, funds, instruments_nfo, holdings=holdings)
        except Exception as e:
            msg = f"'analyze_positions': {type(e).__name__}: {e}"
            errors.append(msg)
            logger.exception("analyze_positions failed")

    state.update({
        "last_updated": datetime.now().isoformat(timespec="seconds"),
        "holdings": holdings,
        "positions": risk_rows,
        "funds_summary": funds_summary,
        "alerts": alerts,
        "error": "; ".join(errors) if errors else None,
    })

    _maybe_email_alerts(alerts, funds_summary)


def _fallback_rows(positions):
    """Minimal display rows (with Kite's own last_price) when risk analysis can't run."""
    rows = []
    for p in positions.get("net", []):
        if p.get("quantity", 0) == 0:
            continue
        rows.append({
            "tradingsymbol": p["tradingsymbol"],
            "exchange": p["exchange"],
            "quantity": p["quantity"],
            "side": "SHORT" if p["quantity"] < 0 else "LONG",
            "average_price": p.get("average_price"),
            "last_price": p.get("last_price"),
            "pnl": p.get("pnl"),
            "is_option": False,
            "status": "OK",
            "notes": [],
        })
    return rows


def _funds_summary_none():
    return {"available_cash": None, "used_margin": None, "net": None}


def _maybe_email_alerts(alerts, funds_summary):
    now = datetime.now()
    cooldown = timedelta(minutes=config.ALERT_COOLDOWN_MINUTES)
    to_send = []
    for a in alerts:
        if a["level"] not in ("CRITICAL", "WARNING"):
            continue
        key = f"{a['symbol']}:{a['level']}"
        last_sent = _last_alert_sent.get(key)
        if last_sent is None or (now - last_sent) > cooldown:
            to_send.append(a)
            _last_alert_sent[key] = now

    if to_send:
        html = build_alert_email(to_send, funds_summary)
        subject = f"[Kite Risk Alert] {len(to_send)} position(s) need attention"
        send_email(subject, html)


_scheduler = None


def start_scheduler():
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        refresh_and_check,
        "interval",
        minutes=config.CHECK_INTERVAL_MINUTES,
        next_run_time=datetime.now(),  # run once immediately
        id="refresh_and_check",
    )
    _scheduler.add_job(
        poll_trades,
        "interval",
        seconds=config.TRADE_POLL_SECONDS,
        id="poll_trades",
    )
    try:
        eod_hh, eod_mm = (int(x) for x in config.EOD_TRIGGER_TIME.split(":"))
        _scheduler.add_job(
            eod_snapshot,
            "cron",
            day_of_week="mon-fri",
            hour=eod_hh,
            minute=eod_mm,
            id="eod_snapshot",
        )
    except ValueError:
        logger.error("Invalid EOD_TRIGGER_TIME '%s' — EOD snapshot disabled", config.EOD_TRIGGER_TIME)
    _scheduler.start()
    logger.info("Scheduler started: checking every %s minute(s)", config.CHECK_INTERVAL_MINUTES)
    return _scheduler
