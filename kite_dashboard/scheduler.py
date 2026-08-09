import logging
import time
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from auth import kite_auth
from kite_client import get_holdings, get_positions, get_funds, get_instruments
from risk_engine import analyze_positions
from notifier import send_email, build_alert_email
from config import config

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


def refresh_and_check():
    kite = kite_auth.get_authenticated_kite() if kite_auth else None
    if kite is None:
        state["error"] = "Not logged in to Kite (or token expired for today). Click 'Login with Kite'."
        return

    def call(step_name, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            raise RuntimeError(f"Failed at step '{step_name}': {type(e).__name__}: {e}") from e

    try:
        holdings = call("get_holdings", get_holdings, kite)
        positions = call("get_positions", get_positions, kite)
        funds = call("get_funds", get_funds, kite)
        instruments_nfo = call("get_instruments(NFO)", get_instruments, kite, "NFO")

        risk_rows, alerts, funds_summary = call(
            "analyze_positions", analyze_positions, kite, positions, funds, instruments_nfo
        )

        state.update({
            "last_updated": datetime.now().isoformat(timespec="seconds"),
            "holdings": holdings,
            "positions": risk_rows,
            "funds_summary": funds_summary,
            "alerts": alerts,
            "error": None,
        })

        _maybe_email_alerts(alerts, funds_summary)

    except Exception as e:
        logger.exception("Refresh failed")
        state["error"] = str(e)


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
    _scheduler.start()
    logger.info("Scheduler started: checking every %s minute(s)", config.CHECK_INTERVAL_MINUTES)
    return _scheduler
