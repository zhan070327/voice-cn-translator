"""SQLite transactions for sessions, quotas, throttling and payment settlement."""
from calendar import monthrange
from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
import sqlite3
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

TRIAL_MS = 5 * 60 * 60 * 1000
SHANGHAI = ZoneInfo("Asia/Shanghai")

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
    trial_ms INTEGER NOT NULL, blocked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, account TEXT NOT NULL REFERENCES accounts(id),
    refresh_hash TEXT UNIQUE NOT NULL, expires INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS rate_windows (
    scope TEXT NOT NULL, window INTEGER NOT NULL, hits INTEGER NOT NULL,
    PRIMARY KEY(scope, window)
);
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY, account TEXT NOT NULL REFERENCES accounts(id),
    plan TEXT NOT NULL, channel TEXT NOT NULL, cents INTEGER NOT NULL,
    created INTEGER NOT NULL, transaction_id TEXT, paid_at INTEGER,
    UNIQUE(channel, transaction_id)
);
CREATE TABLE IF NOT EXISTS subscription_periods (
    order_id TEXT PRIMARY KEY REFERENCES orders(id), account TEXT NOT NULL REFERENCES accounts(id),
    starts INTEGER NOT NULL, ends INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS periods_account ON subscription_periods(account, ends);
CREATE TABLE IF NOT EXISTS usage (
    id TEXT PRIMARY KEY, account TEXT NOT NULL REFERENCES accounts(id),
    kind TEXT NOT NULL, units INTEGER NOT NULL, equivalent_ms INTEGER NOT NULL,
    period TEXT REFERENCES subscription_periods(order_id), started INTEGER NOT NULL,
    confirmed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS usage_account ON usage(account, started);
CREATE INDEX IF NOT EXISTS usage_pending ON usage(confirmed, started);
"""


class Denied(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


def digest(token):
    return sha256(token.encode()).hexdigest()


def next_month(epoch):
    date = datetime.fromtimestamp(epoch, SHANGHAI)
    year, month = (date.year + 1, 1) if date.month == 12 else (date.year, date.month + 1)
    return int(date.replace(year=year, month=month, day=min(date.day, monthrange(year, month)[1])).timestamp())


class Store:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        try:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(SCHEMA)
        finally:
            db.close()

    @contextmanager
    def transaction(self, *, write=True):
        db = sqlite3.connect(self.path, timeout=3, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        finally:
            db.close()

    def rate(self, *limits):
        window = int(time.time()) // 60
        with self.transaction() as db:
            db.execute("DELETE FROM rate_windows WHERE window < ?", (window - 2,))
            for scope, limit in limits:
                row = db.execute("SELECT hits FROM rate_windows WHERE scope=? AND window=?", (scope, window)).fetchone()
                if row and row["hits"] >= limit:
                    raise Denied(429, "rate_limited")
                db.execute(
                    "INSERT INTO rate_windows VALUES (?, ?, 1) ON CONFLICT(scope, window) DO UPDATE SET hits=hits+1",
                    (scope, window),
                )

    def register(self, email, password):
        account = uuid4().hex
        with self.transaction() as db:
            try:
                db.execute("INSERT INTO accounts(id,email,password,trial_ms) VALUES(?,?,?,?)",
                           (account, email, password, TRIAL_MS))
            except sqlite3.IntegrityError:
                raise Denied(409, "email_in_use") from None
        return account

    def credentials(self, email):
        with self.transaction(write=False) as db:
            return db.execute("SELECT id,password FROM accounts WHERE email=?", (email,)).fetchone()

    def new_session(self, account, refresh_hash, expires):
        session = uuid4().hex
        with self.transaction() as db:
            db.execute("DELETE FROM sessions WHERE expires <= ?", (int(time.time()),))
            db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (session, account, refresh_hash, expires))
        return session

    def rotate(self, old_hash, new_hash, now):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM sessions WHERE refresh_hash=? AND expires>?", (old_hash, now)).fetchone()
            if row is None:
                raise Denied(401, "unauthenticated")
            db.execute("UPDATE sessions SET refresh_hash=? WHERE id=?", (new_hash, row["id"]))
            return row["account"], row["id"]

    def revoke(self, refresh_hash):
        with self.transaction() as db:
            db.execute("DELETE FROM sessions WHERE refresh_hash=?", (refresh_hash,))

    def valid_session(self, account, session, now):
        with self.transaction(write=False) as db:
            return db.execute("SELECT 1 FROM sessions WHERE account=? AND id=? AND expires>?",
                              (account, session, now)).fetchone() is not None

    def _active_period(self, db, account, now):
        return db.execute(
            "SELECT order_id, ends FROM subscription_periods WHERE account=? AND starts<=? AND ends>? "
            "ORDER BY ends DESC LIMIT 1", (account, now, now),
        ).fetchone()

    def _restore_stale(self, db, now):
        expired = db.execute(
            "SELECT id,account,equivalent_ms,period FROM usage WHERE confirmed=0 AND started<=?", (now - 120,)
        ).fetchall()
        for row in expired:
            self._release(db, row)

    def _release(self, db, row):
        if row["period"] is None:
            db.execute("UPDATE accounts SET trial_ms=trial_ms+? WHERE id=?",
                       (row["equivalent_ms"], row["account"]))
        db.execute("DELETE FROM usage WHERE id=?", (row["id"],))

    def entitlement(self, account, now):
        with self.transaction() as db:
            self._restore_stale(db, now)
            row = db.execute("SELECT trial_ms,blocked FROM accounts WHERE id=?", (account,)).fetchone()
            period = self._active_period(db, account, now)
            end = db.execute("SELECT MAX(ends) FROM subscription_periods WHERE account=? AND ends>?",
                             (account, now)).fetchone()[0]
            blocked = bool(row["blocked"])
            return {
                "trial_remaining_ms": row["trial_ms"], "subscribed": period is not None,
                "subscription_expires_at": end, "blocked": blocked,
                "online_allowed": (period is not None or row["trial_ms"] > 0) and not blocked,
            }

    def reserve(self, account, kind, units, equivalent_ms, now):
        entry = uuid4().hex
        with self.transaction() as db:
            self._restore_stale(db, now)
            row = db.execute("SELECT trial_ms,blocked FROM accounts WHERE id=?", (account,)).fetchone()
            if row["blocked"]:
                raise Denied(403, "account_blocked")
            period = self._active_period(db, account, now)
            if period is None:
                if row["trial_ms"] < equivalent_ms:
                    raise Denied(402, "quota_exhausted")
                db.execute("UPDATE accounts SET trial_ms=trial_ms-? WHERE id=?", (equivalent_ms, account))
            db.execute("INSERT INTO usage VALUES(?,?,?,?,?,?,?,0)",
                       (entry, account, kind, units, equivalent_ms, period["order_id"] if period else None, now))
        return entry

    def finalize(self, entry, success):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM usage WHERE id=? AND confirmed=0", (entry,)).fetchone()
            if row and success:
                db.execute("UPDATE usage SET confirmed=1 WHERE id=?", (entry,))
            elif row:
                self._release(db, row)

    def order(self, account, plan, channel, now):
        order_id = uuid4().hex
        with self.transaction() as db:
            db.execute("INSERT INTO orders(id,account,plan,channel,cents,created) VALUES(?,?,?,?,?,?)",
                       (order_id, account, plan.id, channel, plan.price_cents, now))
        return order_id

    def order_status(self, account, order_id):
        with self.transaction(write=False) as db:
            row = db.execute("SELECT id,channel,paid_at FROM orders WHERE account=? AND id=?",
                             (account, order_id)).fetchone()
            if not row:
                raise Denied(404, "order_not_found")
            return dict(row)

    def settle(self, channel, notice, now):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM orders WHERE id=?", (notice.order_id,)).fetchone()
            if not row or row["channel"] != channel or row["cents"] != notice.cents:
                raise Denied(400, "payment_mismatch")
            if row["paid_at"] is not None:
                if row["transaction_id"] != notice.transaction_id:
                    raise Denied(400, "payment_mismatch")
                return False
            try:
                db.execute("UPDATE orders SET paid_at=?,transaction_id=? WHERE id=?",
                           (now, notice.transaction_id, notice.order_id))
            except sqlite3.IntegrityError:
                raise Denied(400, "payment_mismatch") from None
            end = db.execute("SELECT MAX(ends) FROM subscription_periods WHERE account=?",
                             (row["account"],)).fetchone()[0]
            starts = max(now, end or 0)
            db.execute("INSERT INTO subscription_periods VALUES(?,?,?,?)",
                       (row["id"], row["account"], starts, next_month(starts)))
            return True
