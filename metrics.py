"""Self-contained SQLite metrics/analytics store for the Telegram Flow bot.

This module deliberately imports only the Python standard library so it can be
unit-tested without ``aiogram``, ``playwright`` or any network access, and so a
metrics failure can never drag down the bot. It mirrors the robustness of the
atomic-JSON stores in :mod:`flow_core` (``CreditStore`` / ``PaymentStore``) but
uses SQLite instead of JSON because the analytics queries (funnel counts,
per-account rollups, revenue by day) are far cheaper as SQL aggregates than as
hand-rolled scans over a JSON blob.

Design contract (set by the product owner):

- **Logging never raises into the caller.** :func:`log_event` and
  :func:`log_flow_job` wrap all DB work in ``try/except``, log at WARNING via the
  module logger, and return normally. A metrics outage must never crash the bot.
- **Payment recording is idempotent.** A Telegram payment webhook can fire twice;
  :func:`record_transaction` uses a ``UNIQUE`` constraint on
  ``provider_payment_id`` + ``INSERT OR IGNORE`` and reports whether the row was
  newly inserted, so credits are never double-counted.
- **One lazy, thread-safe connection.** The bot is a single-process asyncio app
  but aiogram may touch the DB from a worker thread, so the connection is opened
  with ``check_same_thread=False`` and every read/write is guarded by a
  module-level :class:`threading.Lock`.
- **Reports are resilient to an empty DB** — they return zeros/empty lists, never
  errors.

Timestamps are stored as UTC text via SQLite's ``datetime('now')`` default;
"today"/"localtime" report windows convert with ``date(..., 'localtime')`` so the
operator sees their own day boundaries.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import closing

__all__ = [
    "init_db",
    "close",
    "log_event",
    "log_flow_job",
    "record_transaction",
    "record_referral_join",
    "mark_referral_rewarded",
    "report_today",
    "report_revenue",
    "report_flow",
    "report_accounts",
    "report_refs",
    "report_errors",
]

log = logging.getLogger("flow.metrics")

# Single module-level connection, created lazily by init_db(). All access is
# serialized through _LOCK so the connection can be safely shared across the
# asyncio loop thread and any aiogram worker threads.
_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None
_DB_PATH: str | None = None


def _default_path() -> str:
    """Resolve the DB path from ``METRICS_DB`` (default ``metrics.db``)."""
    return os.getenv("METRICS_DB", "metrics.db")


# ── schema ─────────────────────────────────────────────────────────────

# All tables use an AUTOINCREMENT id and a UTC text ``created_at`` default. The
# DDL is idempotent (IF NOT EXISTS) so init_db can run on every startup.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_name   TEXT NOT NULL,
    user_id      INTEGER,
    username     TEXT,
    source       TEXT,
    payload_json TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS flow_jobs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER,
    account_id          TEXT,
    operation_type      TEXT,
    model               TEXT,
    bot_credits_charged INTEGER DEFAULT 0,
    flow_credits_before INTEGER,
    flow_credits_after  INTEGER,
    flow_credits_delta  INTEGER,
    duration_ms         INTEGER,
    status              TEXT,
    error_type          TEXT,
    refund_amount       INTEGER DEFAULT 0,
    created_at          TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transactions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    provider             TEXT,
    provider_payment_id  TEXT UNIQUE,
    user_id              INTEGER,
    package_id           TEXT,
    amount_rub           REAL,
    stars_amount         INTEGER,
    credits_issued       INTEGER,
    status               TEXT,
    created_at           TEXT DEFAULT (datetime('now')),
    paid_at              TEXT
);

CREATE TABLE IF NOT EXISTS referrals (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_user_id            INTEGER,
    referred_user_id            INTEGER UNIQUE,
    status                      TEXT,
    reward_credits              INTEGER DEFAULT 0,
    first_payment_transaction_id INTEGER,
    created_at                  TEXT DEFAULT (datetime('now')),
    rewarded_at                 TEXT
);

CREATE TABLE IF NOT EXISTS referral_ongoing_rewards (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_user_id    INTEGER NOT NULL,
    referred_user_id    INTEGER NOT NULL,
    reward_credits      INTEGER NOT NULL,
    provider_payment_id TEXT UNIQUE,
    created_at          TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_name        ON events(event_name);
CREATE INDEX IF NOT EXISTS idx_events_created      ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_flow_jobs_created   ON flow_jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_flow_jobs_account   ON flow_jobs(account_id);
CREATE INDEX IF NOT EXISTS idx_transactions_created ON transactions(created_at);
CREATE INDEX IF NOT EXISTS idx_referrals_referrer  ON referrals(referrer_user_id);
CREATE INDEX IF NOT EXISTS idx_ror_referrer        ON referral_ongoing_rewards(referrer_user_id, created_at);
"""


# ── connection lifecycle ───────────────────────────────────────────────


def init_db(path: str | None = None) -> None:
    """Open (or reopen) the module connection and create tables IF NOT EXISTS.

    With no ``path`` the existing DB path is reused (a no-op reconnect if already
    open); passing a *different* path closes the current connection and reopens
    against the new file. Safe to call repeatedly, e.g. on every bot startup.
    """
    global _CONN, _DB_PATH
    target = path or _DB_PATH or _default_path()
    with _LOCK:
        if _CONN is not None and target == _DB_PATH:
            return
        if _CONN is not None:
            try:
                _CONN.close()
            except sqlite3.Error:
                pass
            _CONN = None
        conn = sqlite3.connect(target, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        conn.commit()
        _CONN = conn
        _DB_PATH = target


def close() -> None:
    """Close and forget the module connection (mainly for test isolation)."""
    global _CONN, _DB_PATH
    with _LOCK:
        if _CONN is not None:
            try:
                _CONN.close()
            except sqlite3.Error:
                pass
        _CONN = None
        _DB_PATH = None


def _conn() -> sqlite3.Connection:
    """Return the live connection, lazily initializing if the caller forgot.

    Callers must already hold ``_LOCK`` (every public function does), so this
    only initializes when ``_CONN`` is ``None``.
    """
    global _CONN, _DB_PATH
    if _CONN is None:
        path = _DB_PATH or _default_path()
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        conn.commit()
        _CONN = conn
        _DB_PATH = path
    return _CONN


# ── writers (never raise into the caller) ──────────────────────────────


def log_event(
    event_name: str,
    *,
    user_id: int | None = None,
    username: str | None = None,
    source: str | None = None,
    payload: dict | None = None,
) -> None:
    """Record a funnel/analytics event. Swallows all errors (logs at WARNING).

    ``payload`` is JSON-encoded (``ensure_ascii=False``) into ``payload_json``;
    a non-serializable payload is dropped (the row is still written without it)
    rather than allowed to raise.
    """
    try:
        payload_json = None
        if payload is not None:
            try:
                payload_json = json.dumps(payload, ensure_ascii=False)
            except (TypeError, ValueError):
                # Bad payload must never sink the event; store the event sans body.
                payload_json = None
        with _LOCK:
            conn = _conn()
            conn.execute(
                "INSERT INTO events (event_name, user_id, username, source, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (event_name, user_id, username, source, payload_json),
            )
            conn.commit()
    except Exception:  # noqa: BLE001 - metrics must never crash the bot
        log.warning("log_event failed for %r", event_name, exc_info=True)


def log_flow_job(
    *,
    user_id: int | None = None,
    account_id: str = "default",
    operation_type: str = "",
    model: str | None = None,
    bot_credits_charged: int = 0,
    flow_credits_before: int | None = None,
    flow_credits_after: int | None = None,
    duration_ms: int | None = None,
    status: str = "success",
    error_type: str | None = None,
    refund_amount: int = 0,
) -> None:
    """Record one Flow backend job (generation/edit/upscale/video).

    ``flow_credits_delta`` is computed as ``after - before`` when both are given,
    else ``NULL``. Swallows all errors (logs at WARNING) so a metrics write can
    never interrupt image/video delivery.
    """
    try:
        delta = None
        if flow_credits_before is not None and flow_credits_after is not None:
            delta = flow_credits_after - flow_credits_before
        with _LOCK:
            conn = _conn()
            conn.execute(
                "INSERT INTO flow_jobs ("
                "user_id, account_id, operation_type, model, bot_credits_charged, "
                "flow_credits_before, flow_credits_after, flow_credits_delta, "
                "duration_ms, status, error_type, refund_amount"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id, account_id, operation_type, model, bot_credits_charged,
                    flow_credits_before, flow_credits_after, delta,
                    duration_ms, status, error_type, refund_amount,
                ),
            )
            conn.commit()
    except Exception:  # noqa: BLE001 - metrics must never crash the bot
        log.warning("log_flow_job failed for op=%r", operation_type, exc_info=True)


def record_transaction(
    *,
    provider: str,
    provider_payment_id: str,
    user_id: int,
    package_id: str | None = None,
    amount_rub: float | None = None,
    stars_amount: int | None = None,
    credits_issued: int | None = None,
    status: str = "paid",
    paid_at: str | None = None,
) -> bool:
    """Idempotently record a payment, returning True only on a *new* row.

    Idempotency is enforced by the ``provider_payment_id UNIQUE`` constraint plus
    ``INSERT OR IGNORE``: a duplicate webhook delivery is silently ignored and
    returns ``False``, so credits are never granted twice. ``paid_at`` defaults to
    now when ``status == "paid"`` and no explicit timestamp is supplied.

    On any unexpected DB error this returns ``False`` (the safe default: do not
    treat a failed record as a fresh, credit-granting transaction).
    """
    try:
        with _LOCK:
            conn = _conn()
            if paid_at is None and status == "paid":
                paid_expr = "datetime('now')"
                params = (
                    provider, provider_payment_id, user_id, package_id,
                    amount_rub, stars_amount, credits_issued, status,
                )
            else:
                paid_expr = "?"
                params = (
                    provider, provider_payment_id, user_id, package_id,
                    amount_rub, stars_amount, credits_issued, status, paid_at,
                )
            cur = conn.execute(
                "INSERT OR IGNORE INTO transactions ("
                "provider, provider_payment_id, user_id, package_id, "
                "amount_rub, stars_amount, credits_issued, status, paid_at"
                f") VALUES (?, ?, ?, ?, ?, ?, ?, ?, {paid_expr})",
                params,
            )
            conn.commit()
            return cur.rowcount > 0
    except Exception:  # noqa: BLE001
        log.warning("record_transaction failed for %r", provider_payment_id, exc_info=True)
        return False


def record_referral_join(*, referrer_user_id: int, referred_user_id: int) -> bool:
    """Record that ``referred_user_id`` joined via ``referrer_user_id``.

    Idempotent on ``referred_user_id`` (a user can only be referred once). A
    self-referral (``referrer == referred``) is rejected without inserting a row.
    Returns ``True`` only when a new referral row was created.
    """
    try:
        if referrer_user_id == referred_user_id:
            return False
        with _LOCK:
            conn = _conn()
            cur = conn.execute(
                "INSERT OR IGNORE INTO referrals "
                "(referrer_user_id, referred_user_id, status) VALUES (?, ?, 'joined')",
                (referrer_user_id, referred_user_id),
            )
            conn.commit()
            return cur.rowcount > 0
    except Exception:  # noqa: BLE001
        log.warning("record_referral_join failed for %r", referred_user_id, exc_info=True)
        return False


def mark_referral_rewarded(
    *,
    referred_user_id: int,
    reward_credits: int,
    first_payment_transaction_id: int | None = None,
) -> None:
    """Mark a referral rewarded (e.g. after the referred user's first payment).

    Sets ``status='rewarded'``, the granted ``reward_credits``, the optional
    triggering ``first_payment_transaction_id`` and ``rewarded_at=now``. A no-op
    if the referral does not exist. Swallows errors.
    """
    try:
        with _LOCK:
            conn = _conn()
            conn.execute(
                "UPDATE referrals SET status='rewarded', reward_credits=?, "
                "first_payment_transaction_id=?, rewarded_at=datetime('now') "
                "WHERE referred_user_id=?",
                (reward_credits, first_payment_transaction_id, referred_user_id),
            )
            conn.commit()
    except Exception:  # noqa: BLE001
        log.warning("mark_referral_rewarded failed for %r", referred_user_id, exc_info=True)


def get_referrer_of(referred_user_id: int) -> int | None:
    """Return the referrer's user_id for ``referred_user_id`` (or None)."""
    try:
        with _LOCK:
            conn = _conn()
            row = conn.execute(
                "SELECT referrer_user_id FROM referrals WHERE referred_user_id=?",
                (referred_user_id,),
            ).fetchone()
            return int(row[0]) if row and row[0] is not None else None
    except Exception:  # noqa: BLE001
        log.warning("get_referrer_of failed", exc_info=True)
        return None


def referral_status(referred_user_id: int) -> str | None:
    """Return 'joined' | 'rewarded' | None for ``referred_user_id``."""
    try:
        with _LOCK:
            conn = _conn()
            row = conn.execute(
                "SELECT status FROM referrals WHERE referred_user_id=?",
                (referred_user_id,),
            ).fetchone()
            return row[0] if row else None
    except Exception:  # noqa: BLE001
        log.warning("referral_status failed", exc_info=True)
        return None


def get_referral_credits_today(referrer_user_id: int) -> int:
    """Total referral credits granted to ``referrer_user_id`` today (cap check)."""
    try:
        today = "date(created_at,'localtime') = date('now','localtime')"
        with _LOCK:
            conn = _conn()
            milestone = conn.execute(
                f"SELECT COALESCE(SUM(reward_credits),0) FROM referrals "
                f"WHERE referrer_user_id=? AND status='rewarded' "
                f"AND date(rewarded_at,'localtime') = date('now','localtime')",
                (referrer_user_id,),
            ).fetchone()[0] or 0
            ongoing = conn.execute(
                f"SELECT COALESCE(SUM(reward_credits),0) FROM referral_ongoing_rewards "
                f"WHERE referrer_user_id=? AND {today}",
                (referrer_user_id,),
            ).fetchone()[0] or 0
            return int(milestone) + int(ongoing)
    except Exception:  # noqa: BLE001
        log.warning("get_referral_credits_today failed", exc_info=True)
        return 0


def record_ongoing_reward(
    referrer_user_id: int, referred_user_id: int, reward_credits: int,
    provider_payment_id: str,
) -> bool:
    """Record an ongoing referral reward. Idempotent on provider_payment_id."""
    try:
        with _LOCK:
            conn = _conn()
            cur = conn.execute(
                "INSERT OR IGNORE INTO referral_ongoing_rewards "
                "(referrer_user_id, referred_user_id, reward_credits, provider_payment_id) "
                "VALUES (?, ?, ?, ?)",
                (referrer_user_id, referred_user_id, reward_credits, provider_payment_id),
            )
            conn.commit()
            return cur.rowcount > 0
    except Exception:  # noqa: BLE001
        log.warning("record_ongoing_reward failed", exc_info=True)
        return False


def get_ongoing_reward_by_payment(provider_payment_id: str) -> dict | None:
    """Look up an ongoing reward row by triggering payment (for refund clawback)."""
    try:
        with _LOCK:
            conn = _conn()
            row = conn.execute(
                "SELECT referrer_user_id, referred_user_id, reward_credits "
                "FROM referral_ongoing_rewards WHERE provider_payment_id=?",
                (provider_payment_id,),
            ).fetchone()
            if not row:
                return None
            return {"referrer_user_id": row[0], "referred_user_id": row[1],
                    "reward_credits": row[2]}
    except Exception:  # noqa: BLE001
        log.warning("get_ongoing_reward_by_payment failed", exc_info=True)
        return None


def get_milestone_by_referred(referred_user_id: int) -> dict | None:
    """Return the milestone referral row for refund clawback (or None)."""
    try:
        with _LOCK:
            conn = _conn()
            row = conn.execute(
                "SELECT referrer_user_id, reward_credits, status "
                "FROM referrals WHERE referred_user_id=?",
                (referred_user_id,),
            ).fetchone()
            if not row:
                return None
            return {"referrer_user_id": row[0], "reward_credits": row[1], "status": row[2]}
    except Exception:  # noqa: BLE001
        log.warning("get_milestone_by_referred failed", exc_info=True)
        return None


def reset_referral_to_joined(referred_user_id: int) -> None:
    """Reset a refunded referral back to 'joined' so a later payment can re-trigger."""
    try:
        with _LOCK:
            conn = _conn()
            conn.execute(
                "UPDATE referrals SET status='joined', reward_credits=0, "
                "first_payment_transaction_id=NULL, rewarded_at=NULL "
                "WHERE referred_user_id=?",
                (referred_user_id,),
            )
            conn.commit()
    except Exception:  # noqa: BLE001
        log.warning("reset_referral_to_joined failed", exc_info=True)


def referral_stats(referrer_user_id: int) -> dict:
    """For the user's own referral screen: invited count + total credits earned."""
    try:
        with _LOCK:
            conn = _conn()
            invited = conn.execute(
                "SELECT COUNT(*) FROM referrals WHERE referrer_user_id=?",
                (referrer_user_id,),
            ).fetchone()[0] or 0
            earned_m = conn.execute(
                "SELECT COALESCE(SUM(reward_credits),0) FROM referrals "
                "WHERE referrer_user_id=? AND status='rewarded'",
                (referrer_user_id,),
            ).fetchone()[0] or 0
            earned_o = conn.execute(
                "SELECT COALESCE(SUM(reward_credits),0) FROM referral_ongoing_rewards "
                "WHERE referrer_user_id=?",
                (referrer_user_id,),
            ).fetchone()[0] or 0
            return {"invited": int(invited), "earned": int(earned_m) + int(earned_o)}
    except Exception:  # noqa: BLE001
        log.warning("referral_stats failed", exc_info=True)
        return {"invited": 0, "earned": 0}


# ── report helpers ─────────────────────────────────────────────────────


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> object:
    """Run a single-value query, returning the cell (or ``None`` if no row)."""
    with closing(conn.execute(sql, params)) as cur:
        row = cur.fetchone()
    return row[0] if row is not None else None


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with closing(conn.execute(sql, params)) as cur:
        return cur.fetchall()


# ── reports ────────────────────────────────────────────────────────────


def report_today() -> dict:
    """Funnel snapshot for the operator's local "today".

    Credit charged/refunded totals come from ``flow_jobs`` (``bot_credits_charged``
    / ``refund_amount``) rather than from event payloads: the flow_jobs rows are
    written on every real backend job and are the single source of truth for what
    the bot actually charged, so they cannot drift from generation reality the way
    optional event payloads can.

    Returns zeros / empty lists on an empty DB.
    """
    try:
        with _LOCK:
            conn = _conn()
            ev_today = "date(created_at,'localtime') = date('now','localtime')"
            tx_today = "date(created_at,'localtime') = date('now','localtime')"
            fj_today = "date(created_at,'localtime') = date('now','localtime')"

            new_users = _scalar(
                conn,
                f"SELECT COUNT(DISTINCT user_id) FROM events "
                f"WHERE event_name='user_started' AND user_id IS NOT NULL AND {ev_today}",
            ) or 0
            active_users = _scalar(
                conn,
                f"SELECT COUNT(DISTINCT user_id) FROM events "
                f"WHERE user_id IS NOT NULL AND {ev_today}",
            ) or 0
            paying_users = _scalar(
                conn,
                f"SELECT COUNT(DISTINCT user_id) FROM transactions "
                f"WHERE status='paid' AND user_id IS NOT NULL AND {tx_today}",
            ) or 0

            image_generations = _scalar(
                conn,
                f"SELECT COUNT(*) FROM events WHERE event_name='image_success' AND {ev_today}",
            ) or 0
            video_generations = _scalar(
                conn,
                f"SELECT COUNT(*) FROM events WHERE event_name='video_success' AND {ev_today}",
            ) or 0
            image_requested = _scalar(
                conn,
                f"SELECT COUNT(*) FROM events WHERE event_name='image_requested' AND {ev_today}",
            ) or 0
            video_requested = _scalar(
                conn,
                f"SELECT COUNT(*) FROM events WHERE event_name='video_requested' AND {ev_today}",
            ) or 0

            # Success rate = successful image+video generations over all finished
            # flow_jobs (status success or fail) today.
            finished = _scalar(
                conn,
                f"SELECT COUNT(*) FROM flow_jobs "
                f"WHERE status IN ('success','fail','error') AND {fj_today}",
            ) or 0
            succeeded = _scalar(
                conn,
                f"SELECT COUNT(*) FROM flow_jobs WHERE status='success' AND {fj_today}",
            ) or 0
            success_rate = (succeeded / finished) if finished else 0.0

            revenue_rub = _scalar(
                conn,
                f"SELECT COALESCE(SUM(amount_rub),0) FROM transactions "
                f"WHERE status='paid' AND {tx_today}",
            ) or 0
            revenue_stars = _scalar(
                conn,
                f"SELECT COALESCE(SUM(stars_amount),0) FROM transactions "
                f"WHERE status='paid' AND {tx_today}",
            ) or 0

            credits_charged = _scalar(
                conn,
                f"SELECT COALESCE(SUM(bot_credits_charged),0) FROM flow_jobs WHERE {fj_today}",
            ) or 0
            credits_refunded = _scalar(
                conn,
                f"SELECT COALESCE(SUM(refund_amount),0) FROM flow_jobs WHERE {fj_today}",
            ) or 0

            top_actions = [
                {"event_name": r["event_name"], "count": r["count"]}
                for r in _rows(
                    conn,
                    f"SELECT event_name, COUNT(*) AS count FROM events WHERE {ev_today} "
                    f"GROUP BY event_name ORDER BY count DESC, event_name LIMIT 5",
                )
            ]

            return {
                "new_users": int(new_users),
                "active_users": int(active_users),
                "paying_users": int(paying_users),
                "image_generations": int(image_generations),
                "video_generations": int(video_generations),
                "image_requested": int(image_requested),
                "video_requested": int(video_requested),
                "success_rate": float(success_rate),
                "revenue_rub": float(revenue_rub),
                "revenue_stars": int(revenue_stars),
                "credits_charged": int(credits_charged),
                "credits_refunded": int(credits_refunded),
                "top_actions": top_actions,
            }
    except Exception:  # noqa: BLE001
        log.warning("report_today failed", exc_info=True)
        return _empty_today()


def _empty_today() -> dict:
    return {
        "new_users": 0, "active_users": 0, "paying_users": 0,
        "image_generations": 0, "video_generations": 0,
        "image_requested": 0, "video_requested": 0,
        "success_rate": 0.0, "revenue_rub": 0.0, "revenue_stars": 0,
        "credits_charged": 0, "credits_refunded": 0, "top_actions": [],
    }


def report_revenue(days: int = 30) -> dict:
    """Revenue rollup over the last ``days`` (paid transactions only)."""
    try:
        window = f"-{max(0, int(days))} days"
        since = "created_at >= datetime('now', ?)"
        with _LOCK:
            conn = _conn()
            base = f"FROM transactions WHERE status='paid' AND {since}"
            revenue_rub = _scalar(conn, f"SELECT COALESCE(SUM(amount_rub),0) {base}", (window,)) or 0
            revenue_stars = _scalar(conn, f"SELECT COALESCE(SUM(stars_amount),0) {base}", (window,)) or 0
            paying_users = _scalar(conn, f"SELECT COUNT(DISTINCT user_id) {base}", (window,)) or 0
            transactions_count = _scalar(conn, f"SELECT COUNT(*) {base}", (window,)) or 0

            by_package = [
                {
                    "package_id": r["package_id"],
                    "count": int(r["count"]),
                    "stars": int(r["stars"] or 0),
                    "rub": float(r["rub"] or 0),
                }
                for r in _rows(
                    conn,
                    f"SELECT package_id, COUNT(*) AS count, "
                    f"COALESCE(SUM(stars_amount),0) AS stars, "
                    f"COALESCE(SUM(amount_rub),0) AS rub "
                    f"{base} GROUP BY package_id ORDER BY count DESC",
                    (window,),
                )
            ]
            by_day = [
                {
                    "day": r["day"],
                    "rub": float(r["rub"] or 0),
                    "stars": int(r["stars"] or 0),
                    "count": int(r["count"]),
                }
                for r in _rows(
                    conn,
                    f"SELECT date(created_at,'localtime') AS day, "
                    f"COALESCE(SUM(amount_rub),0) AS rub, "
                    f"COALESCE(SUM(stars_amount),0) AS stars, "
                    f"COUNT(*) AS count "
                    f"{base} GROUP BY day ORDER BY day DESC",
                    (window,),
                )
            ]
            return {
                "revenue_rub": float(revenue_rub),
                "revenue_stars": int(revenue_stars),
                "paying_users": int(paying_users),
                "transactions_count": int(transactions_count),
                "by_package": by_package,
                "by_day": by_day,
            }
    except Exception:  # noqa: BLE001
        log.warning("report_revenue failed", exc_info=True)
        return {
            "revenue_rub": 0.0, "revenue_stars": 0, "paying_users": 0,
            "transactions_count": 0, "by_package": [], "by_day": [],
        }


def report_flow() -> dict:
    """Backend-job health from ``flow_jobs`` (all-time)."""
    try:
        with _LOCK:
            conn = _conn()
            by_operation = [
                {
                    "operation_type": r["operation_type"],
                    "count": int(r["count"]),
                    "success": int(r["success"]),
                    "fail": int(r["fail"]),
                    "avg_duration_ms": (float(r["avg_duration_ms"]) if r["avg_duration_ms"] is not None else None),
                }
                for r in _rows(
                    conn,
                    "SELECT operation_type, COUNT(*) AS count, "
                    "SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success, "
                    "SUM(CASE WHEN status!='success' THEN 1 ELSE 0 END) AS fail, "
                    "AVG(duration_ms) AS avg_duration_ms "
                    "FROM flow_jobs GROUP BY operation_type ORDER BY count DESC",
                )
            ]
            total = _scalar(conn, "SELECT COUNT(*) FROM flow_jobs") or 0
            succeeded = _scalar(
                conn, "SELECT COUNT(*) FROM flow_jobs WHERE status='success'"
            ) or 0
            success_rate = (succeeded / total) if total else 0.0

            by_model_credits = [
                {
                    "model": r["model"],
                    "jobs": int(r["jobs"]),
                    "flow_credits_delta_sum": int(r["delta_sum"] or 0),
                }
                for r in _rows(
                    conn,
                    "SELECT model, COUNT(*) AS jobs, "
                    "COALESCE(SUM(flow_credits_delta),0) AS delta_sum "
                    "FROM flow_jobs GROUP BY model ORDER BY jobs DESC",
                )
            ]
            errors_by_type = [
                {"error_type": r["error_type"], "count": int(r["count"])}
                for r in _rows(
                    conn,
                    "SELECT error_type, COUNT(*) AS count FROM flow_jobs "
                    "WHERE status!='success' AND error_type IS NOT NULL "
                    "GROUP BY error_type ORDER BY count DESC",
                )
            ]
            return {
                "by_operation": by_operation,
                "success_rate": float(success_rate),
                "by_model_credits": by_model_credits,
                "errors_by_type": errors_by_type,
            }
    except Exception:  # noqa: BLE001
        log.warning("report_flow failed", exc_info=True)
        return {
            "by_operation": [], "success_rate": 0.0,
            "by_model_credits": [], "errors_by_type": [],
        }


def report_accounts() -> dict:
    """Per-account backend usage for today (one row per ``account_id``)."""
    try:
        fj_today = "date(created_at,'localtime') = date('now','localtime')"
        with _LOCK:
            conn = _conn()
            accounts = []
            for r in _rows(
                conn,
                f"SELECT account_id, COUNT(*) AS jobs, "
                f"SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success, "
                f"SUM(CASE WHEN status!='success' THEN 1 ELSE 0 END) AS fail "
                f"FROM flow_jobs WHERE {fj_today} "
                f"GROUP BY account_id ORDER BY jobs DESC",
            ):
                account_id = r["account_id"]
                credits_remaining = _scalar(
                    conn,
                    f"SELECT flow_credits_after FROM flow_jobs "
                    f"WHERE account_id IS ? AND flow_credits_after IS NOT NULL AND {fj_today} "
                    f"ORDER BY id DESC LIMIT 1",
                    (account_id,),
                )
                last_error = _scalar(
                    conn,
                    f"SELECT error_type FROM flow_jobs "
                    f"WHERE account_id IS ? AND error_type IS NOT NULL AND {fj_today} "
                    f"ORDER BY id DESC LIMIT 1",
                    (account_id,),
                )
                accounts.append({
                    "account_id": account_id,
                    "jobs": int(r["jobs"]),
                    "success": int(r["success"]),
                    "fail": int(r["fail"]),
                    "last_error": last_error,
                    "credits_remaining": (int(credits_remaining) if credits_remaining is not None else None),
                })
            return {"accounts": accounts}
    except Exception:  # noqa: BLE001
        log.warning("report_accounts failed", exc_info=True)
        return {"accounts": []}


def report_refs() -> dict:
    """Referral-program rollup (all-time)."""
    try:
        with _LOCK:
            conn = _conn()
            total_referrals = _scalar(conn, "SELECT COUNT(*) FROM referrals") or 0
            joined = _scalar(
                conn, "SELECT COUNT(*) FROM referrals WHERE status='joined'"
            ) or 0
            rewarded = _scalar(
                conn, "SELECT COUNT(*) FROM referrals WHERE status='rewarded'"
            ) or 0
            total_reward_credits = _scalar(
                conn, "SELECT COALESCE(SUM(reward_credits),0) FROM referrals"
            ) or 0
            top_referrers = [
                {"referrer_user_id": r["referrer_user_id"], "count": int(r["count"])}
                for r in _rows(
                    conn,
                    "SELECT referrer_user_id, COUNT(*) AS count FROM referrals "
                    "WHERE referrer_user_id IS NOT NULL "
                    "GROUP BY referrer_user_id ORDER BY count DESC LIMIT 10",
                )
            ]
            return {
                "total_referrals": int(total_referrals),
                "joined": int(joined),
                "rewarded": int(rewarded),
                "total_reward_credits": int(total_reward_credits),
                "top_referrers": top_referrers,
            }
    except Exception:  # noqa: BLE001
        log.warning("report_refs failed", exc_info=True)
        return {
            "total_referrals": 0, "joined": 0, "rewarded": 0,
            "total_reward_credits": 0, "top_referrers": [],
        }


def report_errors(days: int = 7) -> dict:
    """Recent backend errors over the last ``days`` (non-success flow_jobs)."""
    try:
        window = f"-{max(0, int(days))} days"
        since = "created_at >= datetime('now', ?)"
        with _LOCK:
            conn = _conn()
            errors_by_type = [
                {"error_type": r["error_type"], "count": int(r["count"])}
                for r in _rows(
                    conn,
                    f"SELECT error_type, COUNT(*) AS count FROM flow_jobs "
                    f"WHERE status!='success' AND {since} "
                    f"GROUP BY error_type ORDER BY count DESC",
                    (window,),
                )
            ]
            recent = [
                {
                    "created_at": r["created_at"],
                    "operation_type": r["operation_type"],
                    "model": r["model"],
                    "error_type": r["error_type"],
                }
                for r in _rows(
                    conn,
                    f"SELECT created_at, operation_type, model, error_type "
                    f"FROM flow_jobs WHERE status!='success' AND {since} "
                    f"ORDER BY id DESC LIMIT 10",
                    (window,),
                )
            ]
            return {"errors_by_type": errors_by_type, "recent": recent}
    except Exception:  # noqa: BLE001
        log.warning("report_errors failed", exc_info=True)
        return {"errors_by_type": [], "recent": []}
