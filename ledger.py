"""
Local SQLite ledger — the single source of truth for realized P&L going
forward. Zerodha's API only exposes today's trades, so we record:

  - trades      : every fill polled from kite.trades() (deduped by trade_id)
  - position snapshots : net positions (with instrument metadata) recorded on
                         each refresh and a forced EOD snapshot
  - holdings snapshots : equity holdings on each refresh (+ EOD)
  - settlements : options/futures that expired (OTM settle at zero, ITM at
                  intrinsic) — these NEVER produce a trade, so they're
                  reconstructed here from snapshot diffs + expiry dates

The whole file is portable: copy pl.db to a new machine/server and you keep
all history (cloud sync can piggyback on this later).
"""
import sqlite3
import threading
import logging
from datetime import datetime, date

from config import config

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT UNIQUE,
    order_id TEXT,
    exchange TEXT,
    tradingsymbol TEXT,
    transaction_type TEXT,      -- BUY / SELL
    quantity REAL,
    price REAL,
    trade_date TEXT,            -- YYYY-MM-DD (local)
    exchange_timestamp TEXT     -- ISO timestamp from Kite
);

CREATE TABLE IF NOT EXISTS position_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,                    -- ISO datetime of the snapshot
    kind TEXT,                  -- 'interval' | 'eod'
    exchange TEXT,
    tradingsymbol TEXT,
    quantity REAL,              -- signed (short = negative)
    average_price REAL,
    last_price REAL,
    expiry TEXT,                -- YYYY-MM-DD or NULL
    instrument_type TEXT,       -- CE / PE / FUT or NULL
    name TEXT,                  -- underlying name or NULL
    strike REAL
);

CREATE TABLE IF NOT EXISTS holdings_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    kind TEXT,
    tradingsymbol TEXT,
    quantity REAL,
    average_price REAL,
    last_price REAL
);

CREATE TABLE IF NOT EXISTS settlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT,
    exchange TEXT,
    tradingsymbol TEXT,
    kind TEXT,                  -- 'otm_expiry' | 'manual'
    quantity REAL,              -- signed position quantity at expiry
    avg_premium REAL,
    settle_amount REAL,         -- per-unit settlement value (0 for OTM)
    realized_pnl REAL,
    estimated INTEGER DEFAULT 1,
    note TEXT,
    confirmed INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(trade_date);
CREATE INDEX IF NOT EXISTS idx_trades_sym ON trades(tradingsymbol);
CREATE INDEX IF NOT EXISTS idx_pos_ts ON position_snapshots(ts);
CREATE INDEX IF NOT EXISTS idx_pos_sym ON position_snapshots(tradingsymbol);
CREATE INDEX IF NOT EXISTS idx_hold_ts ON holdings_snapshots(ts);
CREATE INDEX IF NOT EXISTS idx_settle_date ON settlements(trade_date);
"""


class Ledger:
    def __init__(self, path=None):
        self.path = path or config.PL_DB_PATH
        # Scheduled jobs and Flask requests run in different threads; the
        # connection is guarded with a lock (check_same_thread=False).
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def fetch(self, sql, params=()):
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def _write(self, fn):
        with self._lock:
            result = fn(self.conn)
            self.conn.commit()
            return result

    # ---------- trades ----------

    def record_trades(self, trades):
        """Insert raw fills, skipping duplicates (trade_id is unique)."""
        if not trades:
            return 0
        rows = []
        for t in trades:
            ts = t.get("exchange_timestamp") or t.get("trade_timestamp") or ""
            rows.append((
                t.get("trade_id"),
                t.get("order_id"),
                t.get("exchange"),
                t.get("tradingsymbol"),
                t.get("transaction_type"),
                float(t.get("quantity") or 0),
                float(t.get("price") or 0),
                _local_date(ts),
                ts,
            ))

        def _insert(conn):
            n = 0
            for r in rows:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO trades "
                    "(trade_id, order_id, exchange, tradingsymbol, transaction_type, "
                    " quantity, price, trade_date, exchange_timestamp) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", r)
                n += cur.rowcount
            return n

        n = self._write(_insert)
        if n:
            logger.info("Recorded %d trade(s) into ledger", n)
        return n

    def trades_for(self, symbol, day):
        return [dict(r) for r in self.fetch(
            "SELECT * FROM trades WHERE tradingsymbol=? AND trade_date=? ORDER BY exchange_timestamp",
            (symbol, day))]

    # ---------- snapshots ----------

    def record_positions(self, positions, kind="interval"):
        """positions: list of dicts with instrument metadata (from scheduler)."""
        if not positions:
            return
        ts = datetime.now().isoformat(timespec="seconds")
        rows = [(
            ts, kind,
            p.get("exchange"), p.get("tradingsymbol"),
            float(p.get("quantity") or 0),
            _f(p.get("average_price")), _f(p.get("last_price")),
            _d(p.get("expiry")), p.get("instrument_type"),
            p.get("name"), _f(p.get("strike")),
        ) for p in positions if p.get("tradingsymbol")]

        def _insert(conn):
            conn.executemany(
                "INSERT INTO position_snapshots "
                "(ts, kind, exchange, tradingsymbol, quantity, average_price, last_price, "
                " expiry, instrument_type, name, strike) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)

        self._write(_insert)

    def record_holdings(self, holdings, kind="interval"):
        if not holdings:
            return
        ts = datetime.now().isoformat(timespec="seconds")
        rows = [(
            ts, kind, h.get("tradingsymbol"),
            float(h.get("quantity") or 0),
            _f(h.get("average_price")), _f(h.get("last_price")),
        ) for h in holdings if h.get("tradingsymbol")]

        def _insert(conn):
            conn.executemany(
                "INSERT INTO holdings_snapshots (ts, kind, tradingsymbol, quantity, average_price, last_price) "
                "VALUES (?,?,?,?,?,?)", rows)

        self._write(_insert)

    def latest_position_state(self, symbol, before_day):
        """(quantity, average_price) from the newest snapshot before a day."""
        row = self.fetch(
            "SELECT quantity, average_price, expiry, instrument_type FROM position_snapshots "
            "WHERE tradingsymbol=? AND ts < ? ORDER BY ts DESC LIMIT 1",
            (symbol, before_day))
        if not row:
            return None
        row = row[0]
        return {
            "quantity": row["quantity"],
            "average_price": row["average_price"],
            "expiry": row["expiry"],
            "instrument_type": row["instrument_type"],
        }

    def latest_holdings_state(self, symbol, before_day):
        row = self.fetch(
            "SELECT quantity, average_price FROM holdings_snapshots "
            "WHERE tradingsymbol=? AND ts < ? ORDER BY ts DESC LIMIT 1",
            (symbol, before_day))
        if not row:
            return None
        row = row[0]
        return {"quantity": row["quantity"], "average_price": row["average_price"]}

    def positions_on(self, day, kind="eod"):
        """Last position snapshot recorded on/after `day` (usually the EOD one)."""
        rows = self.fetch(
            "SELECT * FROM (SELECT * FROM position_snapshots WHERE ts BETWEEN ? AND ? "
            " ORDER BY ts DESC) WHERE kind=? LIMIT 1", (day, day + " 23:59:59", kind))
        return rows

    def snapshot_days(self):
        return [r["d"] for r in self.fetch("SELECT DISTINCT substr(ts,1,10) AS d FROM position_snapshots ORDER BY d")]

    def trade_days(self):
        return [r["d"] for r in self.fetch("SELECT DISTINCT trade_date AS d FROM trades ORDER BY d")]

    def first_snapshot_ts(self):
        row = self.fetch("SELECT MIN(ts) AS ts FROM position_snapshots")
        return row[0]["ts"] if row and row[0]["ts"] else None

    def recent_trades(self, limit=100):
        return [dict(r) for r in self.fetch(
            "SELECT trade_date, exchange_timestamp, exchange, tradingsymbol, transaction_type, "
            "quantity, price FROM trades ORDER BY exchange_timestamp DESC LIMIT ?", (limit,))]

    def count(self, table):
        return self.fetch("SELECT COUNT(*) AS n FROM " + table)[0]["n"]

    # ---------- settlements ----------

    def add_settlement(self, trade_date, exchange, symbol, kind, quantity, avg_premium,
                       settle_amount, realized_pnl, estimated=1, note=""):
        def _insert(conn):
            cur = conn.execute(
                "INSERT INTO settlements (trade_date, exchange, tradingsymbol, kind, quantity, "
                " avg_premium, settle_amount, realized_pnl, estimated, note) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (trade_date, exchange, symbol, kind, quantity, avg_premium,
                 settle_amount, realized_pnl, estimated, note))
            return cur.lastrowid
        return self._write(_insert)

    def settlements_on(self, day):
        return [dict(r) for r in self.fetch(
            "SELECT * FROM settlements WHERE trade_date=? ORDER BY tradingsymbol", (day,))]

    def settlements_for(self, symbol, day):
        return [dict(r) for r in self.fetch(
            "SELECT * FROM settlements WHERE tradingsymbol=? AND trade_date=?",
            (symbol, day))]

    def unconfirmed_settlements(self):
        return [dict(r) for r in self.fetch(
            "SELECT * FROM settlements WHERE estimated=1 AND confirmed=0 ORDER BY trade_date")]

    def confirm_settlement(self, sid):
        def _upd(conn):
            conn.execute("UPDATE settlements SET confirmed=1 WHERE id=?", (sid,))
        self._write(_upd)

    def delete_settlement(self, sid):
        def _del(conn):
            conn.execute("DELETE FROM settlements WHERE id=?", (sid,))
        self._write(_del)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _d(v):
    """Normalise expiry to YYYY-MM-DD."""
    if not v:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)[:10]


def _local_date(iso_ts):
    """Best-effort local calendar date from an ISO-ish timestamp."""
    if not iso_ts:
        return date.today().isoformat()
    return str(iso_ts)[:10]


ledger = Ledger()