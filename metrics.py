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
import hashlib
import hmac
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import closing

from core.user_identity import platform_identity, telegram_legacy_internal_id, uses_telegram_legacy_id

__all__ = [
    "init_db",
    "close",
    "log_event",
    "log_flow_job",
    "record_transaction",
    "record_transaction_status",
    "record_referral_join",
    "mark_referral_rewarded",
    "grant_milestone_if_joined",
    "record_acquisition",
    "report_today",
    "report_revenue",
    "report_flow",
    "report_accounts",
    "report_refs",
    "report_sellers",
    "report_channels",
    "report_errors",
    "report_ops_health",
    "report_video_health",
    "report_video_account_scores",
    "report_activation_cold",
    "report_payment_repeat",
    "report_margin",
    "report_referral_quality",
    "report_landing_hero_experiment",
    # credits store
    "ensure_user_identity",
    "get_user_identity",
    "bind_web_auth_session",
    "get_web_auth_session",
    "delete_web_auth_session",
    "create_web_login_challenge",
    "claim_web_login_challenge",
    "complete_web_login_challenge",
    "create_web_oauth_state",
    "consume_web_oauth_state",
    "consume_web_auth_assertion",
    "grant_identity_welcome_credits",
    "credits_balance_for_identity",
    "credits_charge_for_identity",
    "credits_refund_for_identity",
    "credits_add_for_identity",
    "admin_add_user_credits",
    "admin_set_user_channel",
    "admin_clear_user_channel",
    "admin_delete_user",
    "credits_balance",
    "credits_charge",
    "credits_refund",
    "credits_add",
    "credits_migrate_from_json",
    # users profile
    "upsert_user",
    "touch_user",
    "mark_user_blocked",
    "get_user_profile",
    "report_active_users",
    "report_top_users",
    "report_top_referrers",
    "backfill_users_from_metrics",
    "user_exists",
    "get_admin_user_detail",
    "list_support_tickets",
    "get_support_ticket_detail",
    "set_support_ticket_status",
    # promo codes
    "create_promo_code",
    "redeem_promo",
    "list_promo_codes",
    # prompt history
    "save_prompt_history",
    "get_prompt_history",
    "create_web_chat",
    "list_web_chats",
    "get_web_chat",
    "append_web_chat_message",
    "get_seller_history",
    # seller SKU projects
    "create_seller_sku_project",
    "get_seller_sku_project",
    "rename_seller_sku_project",
    "delete_seller_sku_project",
    "save_seller_sku_item",
    "list_seller_sku_projects",
    "recent_seller_skus",
    "save_seller_profile",
    "get_seller_profile",
    "has_user_event",
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


def _events_retention_days() -> int:
    """How long to keep ``events`` rows (they carry usernames = PII).

    ``METRICS_EVENTS_RETENTION_DAYS`` env, default 90; ``0`` disables the purge.
    Financial tables (transactions/referrals/flow_jobs) are never purged.
    """
    try:
        return max(0, int(os.getenv("METRICS_EVENTS_RETENTION_DAYS", "90")))
    except ValueError:
        return 90


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

CREATE TABLE IF NOT EXISTS referral_first_generation_rewards (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_user_id    INTEGER NOT NULL UNIQUE,
    referred_user_id    INTEGER NOT NULL UNIQUE,
    reward_credits      INTEGER NOT NULL,
    created_at          TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS acquisitions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER UNIQUE,
    channel     TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_name        ON events(event_name);
CREATE INDEX IF NOT EXISTS idx_events_created      ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_flow_jobs_created   ON flow_jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_flow_jobs_account   ON flow_jobs(account_id);
CREATE INDEX IF NOT EXISTS idx_transactions_created ON transactions(created_at);
CREATE INDEX IF NOT EXISTS idx_referrals_referrer  ON referrals(referrer_user_id);
CREATE INDEX IF NOT EXISTS idx_ror_referrer        ON referral_ongoing_rewards(referrer_user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_rfgr_created        ON referral_first_generation_rewards(created_at);
CREATE INDEX IF NOT EXISTS idx_acquisitions_channel ON acquisitions(channel);

CREATE TABLE IF NOT EXISTS credits (
    user_id    INTEGER PRIMARY KEY,
    balance    INTEGER NOT NULL DEFAULT 0,
    granted    INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT DEFAULT (datetime('now'))
);

-- ── users: profile + denormalized snapshot for fast per-user reports ──────
-- One row per Telegram user. Money, credits, requests, and referrals live in
-- their own tables and are JOIN/aggregated; balances are NOT duplicated here.
CREATE TABLE IF NOT EXISTS users (
    user_id        INTEGER PRIMARY KEY,
    username       TEXT,
    first_name     TEXT,
    first_seen     TEXT DEFAULT (datetime('now')),
    last_active    TEXT DEFAULT (datetime('now')),
    acq_channel    TEXT,
    is_blocked     INTEGER NOT NULL DEFAULT 0,
    updated_at     TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_users_last_active ON users(last_active);
CREATE INDEX IF NOT EXISTS idx_users_first_seen  ON users(first_seen);
CREATE INDEX IF NOT EXISTS idx_users_channel     ON users(acq_channel);

CREATE TABLE IF NOT EXISTS user_identities (
    platform         TEXT NOT NULL,
    platform_user_id TEXT NOT NULL,
    internal_user_id INTEGER NOT NULL UNIQUE,
    created_at       TEXT DEFAULT (datetime('now')),
    last_seen_at     TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (platform, platform_user_id)
);
CREATE INDEX IF NOT EXISTS idx_user_identities_internal ON user_identities(internal_user_id);

-- Browser cookies remain opaque capabilities.  These tables bind only a hash
-- of that capability to a provider identity and keep short-lived login state.
CREATE TABLE IF NOT EXISTS web_auth_sessions (
    session_hash      TEXT PRIMARY KEY,
    platform          TEXT NOT NULL,
    platform_user_id  TEXT NOT NULL,
    internal_user_id  INTEGER NOT NULL,
    display_name      TEXT,
    created_at        INTEGER NOT NULL,
    last_seen_at      INTEGER NOT NULL,
    expires_at        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_web_auth_sessions_expiry
    ON web_auth_sessions(expires_at);

CREATE TABLE IF NOT EXISTS web_login_challenges (
    challenge_hash    TEXT PRIMARY KEY,
    session_hash      TEXT NOT NULL,
    platform          TEXT NOT NULL,
    platform_user_id  TEXT,
    display_name      TEXT,
    confirmation_hash TEXT,
    attempts          INTEGER NOT NULL DEFAULT 0,
    created_at        INTEGER NOT NULL,
    expires_at        INTEGER NOT NULL,
    consumed_at       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_web_login_challenges_session
    ON web_login_challenges(session_hash, created_at DESC);

CREATE TABLE IF NOT EXISTS web_oauth_states (
    state_hash        TEXT PRIMARY KEY,
    session_hash      TEXT NOT NULL,
    provider          TEXT NOT NULL,
    code_verifier     TEXT NOT NULL,
    created_at        INTEGER NOT NULL,
    expires_at        INTEGER NOT NULL,
    consumed_at       INTEGER
);

CREATE TABLE IF NOT EXISTS web_auth_assertions (
    provider          TEXT NOT NULL,
    assertion_hash    TEXT NOT NULL,
    used_at           INTEGER NOT NULL,
    expires_at        INTEGER NOT NULL,
    PRIMARY KEY (provider, assertion_hash)
);

-- Provider-scoped welcome credits.  The ledger row and balance mutation are
-- committed in one transaction so retries/concurrent OAuth callbacks cannot
-- grant the same identity twice.
CREATE TABLE IF NOT EXISTS identity_welcome_grants (
    platform          TEXT NOT NULL,
    platform_user_id  TEXT NOT NULL,
    internal_user_id  INTEGER NOT NULL,
    credits           INTEGER NOT NULL,
    created_at        TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (platform, platform_user_id)
);
CREATE INDEX IF NOT EXISTS idx_identity_welcome_grants_internal
    ON identity_welcome_grants(internal_user_id);

CREATE TABLE IF NOT EXISTS user_gallery (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    file_id    TEXT NOT NULL,
    token      TEXT,
    prompt     TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gallery_user ON user_gallery(user_id, id);

CREATE TABLE IF NOT EXISTS seller_sku_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    sku        TEXT NOT NULL,
    platform   TEXT,
    file_id    TEXT NOT NULL,
    token      TEXT,
    prompt     TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_seller_sku_user ON seller_sku_items(user_id, sku, id);

CREATE TABLE IF NOT EXISTS seller_sku_projects (
    user_id    INTEGER NOT NULL,
    sku        TEXT NOT NULL,
    platform   TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, sku)
);
CREATE INDEX IF NOT EXISTS idx_seller_sku_projects_user ON seller_sku_projects(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS seller_profiles (
    user_id    INTEGER PRIMARY KEY,
    brand_kit  TEXT,
    niche      TEXT,
    sku_volume TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS support_tickets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    username      TEXT,
    status        TEXT NOT NULL DEFAULT 'open',
    message_text  TEXT NOT NULL,
    reply_text    TEXT,
    admin_msg_id  INTEGER,
    created_at    TEXT DEFAULT (datetime('now')),
    replied_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_tickets_user   ON support_tickets(user_id);
CREATE INDEX IF NOT EXISTS idx_tickets_admin  ON support_tickets(admin_msg_id);

CREATE TABLE IF NOT EXISTS prompt_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    prompt     TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_prompt_history_user ON prompt_history(user_id, id DESC);

-- Authenticated website conversations.  Chat ids are opaque capabilities;
-- every query below also scopes by internal user id.
CREATE TABLE IF NOT EXISTS web_chats (
    chat_id     TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_web_chats_user ON web_chats(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS web_chat_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT NOT NULL,
    user_id     INTEGER NOT NULL,
    role        TEXT NOT NULL,
    text        TEXT NOT NULL DEFAULT '',
    media_json  TEXT,
    mode        TEXT,
    model       TEXT,
    aspect      TEXT,
    charged     INTEGER NOT NULL DEFAULT 0,
    balance     INTEGER,
    created_at  TEXT DEFAULT (datetime('now')),
    FOREIGN KEY(chat_id) REFERENCES web_chats(chat_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_web_chat_messages_chat ON web_chat_messages(chat_id, id ASC);

CREATE TABLE IF NOT EXISTS promo_codes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT UNIQUE NOT NULL COLLATE NOCASE,
    credits      INTEGER NOT NULL,
    max_uses     INTEGER NOT NULL DEFAULT 1,
    uses         INTEGER NOT NULL DEFAULT 0,
    created_by   INTEGER,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS promo_redemptions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT NOT NULL COLLATE NOCASE,
    user_id      INTEGER NOT NULL,
    redeemed_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(code, user_id)
);

CREATE TABLE IF NOT EXISTS user_streaks (
    user_id          INTEGER PRIMARY KEY,
    last_active_date TEXT NOT NULL,
    current_streak   INTEGER NOT NULL DEFAULT 1,
    max_streak       INTEGER NOT NULL DEFAULT 1
);
"""


# ── schema migrations ─────────────────────────────────────────────────

# Columns added after the initial release.  Each entry is one ALTER TABLE
# statement; we try every statement on every startup and silently swallow
# "duplicate column name" (the column already exists from a previous run).
_COLUMN_MIGRATIONS: list[str] = [
    # flow_jobs — added in 2025-Q4
    "ALTER TABLE flow_jobs ADD COLUMN status TEXT DEFAULT 'success'",
    "ALTER TABLE flow_jobs ADD COLUMN error_type TEXT",
    "ALTER TABLE flow_jobs ADD COLUMN refund_amount INTEGER DEFAULT 0",
    # events — source column added for filtering
    "ALTER TABLE events ADD COLUMN source TEXT",
]


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """Apply additive column migrations (idempotent — ignores duplicate-column errors)."""
    for sql in _COLUMN_MIGRATIONS:
        try:
            conn.execute(sql)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # "duplicate column name" — already present, skip


def _identity_channel(platform: str) -> str:
    """Return the first-touch channel used by the shared admin projection."""
    value = str(platform or "").strip().lower()
    if value == "yandex":
        return "web_yandex"
    return value or "unknown"


def _project_identity_user(
    conn: sqlite3.Connection,
    *,
    internal_user_id: int,
    platform: str,
    username: str | None = None,
    first_name: str | None = None,
) -> None:
    """Upsert one identity into the channel-neutral ``users`` projection.

    The projection powers admin/analytics only; identity ownership and balances
    remain in their dedicated tables.  Keeping this helper connection-local
    avoids recursively acquiring the module's non-reentrant lock.
    """
    conn.execute(
        """
        INSERT INTO users (user_id, username, first_name, acq_channel)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
          username = CASE WHEN excluded.username IS NOT NULL
                           THEN excluded.username ELSE users.username END,
          first_name = CASE WHEN excluded.first_name IS NOT NULL
                             THEN excluded.first_name ELSE users.first_name END,
          acq_channel = CASE WHEN users.acq_channel IS NULL
                             THEN excluded.acq_channel ELSE users.acq_channel END,
          last_active = datetime('now'),
          updated_at = datetime('now')
        """,
        (
            int(internal_user_id),
            str(username).strip()[:64] if username else None,
            str(first_name).strip()[:120] if first_name else None,
            _identity_channel(platform),
        ),
    )


def _backfill_identity_users(conn: sqlite3.Connection) -> None:
    """Idempotently expose historical website/MAX identities in admin."""
    conn.execute(
        """
        INSERT OR IGNORE INTO users
          (user_id, first_name, first_seen, last_active, acq_channel, updated_at)
        SELECT
          i.internal_user_id,
          (SELECT s.display_name
             FROM web_auth_sessions s
            WHERE s.internal_user_id=i.internal_user_id
              AND s.display_name IS NOT NULL
            ORDER BY s.last_seen_at DESC LIMIT 1),
          COALESCE(i.created_at, datetime('now')),
          COALESCE(i.last_seen_at, datetime('now')),
          CASE i.platform WHEN 'yandex' THEN 'web_yandex' ELSE i.platform END,
          COALESCE(i.last_seen_at, datetime('now'))
        FROM user_identities i
        """
    )


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
        _migrate_columns(conn)
        _backfill_identity_users(conn)
        # PII-ретеншн: username в events — персональные данные; чистим старое
        # при каждом старте. Денежные таблицы не трогаем (нужны для сверки).
        days = _events_retention_days()
        if days:
            try:
                conn.execute(
                    "DELETE FROM events WHERE created_at < datetime('now', ?)",
                    (f"-{days} day",),
                )
            except sqlite3.Error:
                log.warning("events retention purge failed", exc_info=True)
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
        _backfill_identity_users(conn)
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


def record_transaction_status(
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
) -> str:
    """Idempotently record a payment; return ``'new' | 'duplicate' | 'error'``.

    Tri-state так, чтобы платёжный хендлер мог различать подтверждённый дубль
    (кредиты НЕ зачислять) и сбой метрик-БД (кредиты зачислить — пользователь
    заплатил звёзды, недоступность аналитики не повод их не отдавать).
    Idempotency is enforced by the ``provider_payment_id UNIQUE`` constraint plus
    ``INSERT OR IGNORE``. ``paid_at`` defaults to now when ``status == "paid"``.
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
            return "new" if cur.rowcount > 0 else "duplicate"
    except Exception:  # noqa: BLE001
        log.warning("record_transaction failed for provider=%s", provider, exc_info=True)
        return "error"


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

    Thin bool wrapper over :func:`record_transaction_status` (kept for old
    callers/tests). On a DB error this returns ``False`` (the safe default: do
    not treat a failed record as a fresh, credit-granting transaction).
    """
    return record_transaction_status(
        provider=provider, provider_payment_id=provider_payment_id,
        user_id=user_id, package_id=package_id, amount_rub=amount_rub,
        stars_amount=stars_amount, credits_issued=credits_issued,
        status=status, paid_at=paid_at,
    ) == "new"


def record_transaction_and_credit_status(
    *,
    provider: str,
    provider_payment_id: str,
    user_id: int,
    starter_credits: int,
    package_id: str | None = None,
    amount_rub: float | None = None,
    stars_amount: int | None = None,
    credits_issued: int = 0,
    status: str = "paid",
) -> tuple[str, int | None]:
    """Atomically record a payment and credit a metrics-backed balance.

    Returns ``(new|duplicate|error, balance)``. A failed credit rolls back the
    transaction row, so a provider retry can safely attempt settlement again.
    """
    uid = int(user_id)
    starter = int(starter_credits)
    issued = int(credits_issued)
    try:
        with _LOCK:
            conn = _conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO transactions ("
                    "provider, provider_payment_id, user_id, package_id, "
                    "amount_rub, stars_amount, credits_issued, status, paid_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, "
                    "CASE WHEN ?='paid' THEN datetime('now') ELSE NULL END)",
                    (
                        provider,
                        provider_payment_id,
                        uid,
                        package_id,
                        amount_rub,
                        stars_amount,
                        issued,
                        status,
                        status,
                    ),
                )
                if cur.rowcount == 0:
                    row = conn.execute(
                        "SELECT balance FROM credits WHERE user_id=?", (uid,)
                    ).fetchone()
                    conn.commit()
                    return "duplicate", int(row[0]) if row else None
                credit_insert = conn.execute(
                    "INSERT OR IGNORE INTO credits "
                    "(user_id, balance, granted, updated_at) "
                    "VALUES (?, ?, 1, datetime('now'))",
                    (uid, starter + issued),
                )
                if credit_insert.rowcount == 0:
                    conn.execute(
                        "UPDATE credits SET "
                        "balance=balance+?+CASE WHEN granted=0 THEN ? ELSE 0 END, "
                        "granted=1, updated_at=datetime('now') WHERE user_id=?",
                        (issued, starter, uid),
                    )
                row = conn.execute(
                    "SELECT balance FROM credits WHERE user_id=?", (uid,)
                ).fetchone()
                if row is None:
                    raise RuntimeError("credit row missing after settlement")
                balance = int(row[0])
                conn.commit()
                return "new", balance
            except BaseException:
                conn.rollback()
                raise
    except Exception:  # noqa: BLE001
        log.warning(
            "atomic payment settlement failed for provider=%s",
            provider,
            exc_info=True,
        )
        return "error", None


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


def record_acquisition(*, user_id: int, channel: str) -> bool:
    """First-touch атрибуция: запомнить, с какого канала пришёл ``user_id``.

    Идемпотентно по ``user_id`` (UNIQUE + INSERT OR IGNORE) — первый канал,
    приведший юзера, и остаётся источником; повторные клики по другим ссылкам
    его не перезаписывают. Возвращает ``True`` только при новой строке (т.е. это
    реально новый привлечённый юзер). Никогда не бросает в вызывающего.
    """
    try:
        channel = (channel or "").strip()
        if not channel:
            return False
        with _LOCK:
            conn = _conn()
            cur = conn.execute(
                "INSERT OR IGNORE INTO acquisitions (user_id, channel) VALUES (?, ?)",
                (user_id, channel),
            )
            if cur.rowcount > 0:
                conn.execute(
                    "UPDATE users SET acq_channel=COALESCE(acq_channel, ?), "
                    "updated_at=datetime('now') WHERE user_id=?",
                    (channel, user_id),
                )
            conn.commit()
            return cur.rowcount > 0
    except Exception:  # noqa: BLE001
        log.warning("record_acquisition failed", exc_info=True)
        return False


_ADMIN_CHANNEL_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


def admin_add_user_credits(user_id: int, amount: int, *, max_amount: int = 100_000) -> dict | None:
    """Grant credits to an existing user from the admin UI.

    Returns the new balance, or ``None`` when the user/amount is invalid.
    The starter bonus flag is not changed; this is a manual top-up.
    """
    try:
        uid = int(user_id)
        value = int(amount)
    except (TypeError, ValueError):
        return None
    if value <= 0 or value > int(max_amount):
        return None
    try:
        with _LOCK:
            conn = _conn()
            if conn.execute("SELECT 1 FROM users WHERE user_id=?", (uid,)).fetchone() is None:
                return None
            conn.execute(
                "INSERT INTO credits (user_id, balance, granted, updated_at) "
                "VALUES (?, ?, 0, datetime('now')) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "balance=balance+excluded.balance, updated_at=datetime('now')",
                (uid, value),
            )
            row = conn.execute(
                "SELECT balance FROM credits WHERE user_id=?", (uid,)
            ).fetchone()
            conn.commit()
            return {"user_id": uid, "balance": int(row[0]) if row else value}
    except Exception:  # noqa: BLE001
        log.warning("admin_add_user_credits failed for user_id=%r", user_id, exc_info=True)
        return None


def admin_set_user_channel(user_id: int, channel: str) -> dict | None:
    """Override a user's acquisition channel with a validated seed slug."""
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return None
    value = str(channel or "").strip().lower()
    if not _ADMIN_CHANNEL_RE.fullmatch(value):
        return None
    try:
        with _LOCK:
            conn = _conn()
            if conn.execute("SELECT 1 FROM users WHERE user_id=?", (uid,)).fetchone() is None:
                return None
            conn.execute(
                "INSERT INTO acquisitions (user_id, channel) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET channel=excluded.channel",
                (uid, value),
            )
            conn.execute(
                "UPDATE users SET acq_channel=?, updated_at=datetime('now') WHERE user_id=?",
                (value, uid),
            )
            conn.commit()
            return {"user_id": uid, "acq_channel": value}
    except Exception:  # noqa: BLE001
        log.warning("admin_set_user_channel failed for user_id=%r", user_id, exc_info=True)
        return None


def admin_clear_user_channel(user_id: int) -> dict | None:
    """Detach first-touch seed attribution from a user."""
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return None
    try:
        with _LOCK:
            conn = _conn()
            if conn.execute("SELECT 1 FROM users WHERE user_id=?", (uid,)).fetchone() is None:
                return None
            conn.execute("DELETE FROM acquisitions WHERE user_id=?", (uid,))
            conn.execute(
                "UPDATE users SET acq_channel=NULL, updated_at=datetime('now') WHERE user_id=?",
                (uid,),
            )
            conn.commit()
            return {"user_id": uid, "acq_channel": None}
    except Exception:  # noqa: BLE001
        log.warning("admin_clear_user_channel failed for user_id=%r", user_id, exc_info=True)
        return None


def admin_delete_user(user_id: int) -> dict | None:
    """Hard-delete a user and every row scoped to that user id."""
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return None

    def _rowcount(cur: sqlite3.Cursor) -> int:
        return max(int(cur.rowcount or 0), 0)

    def _delete(
        conn: sqlite3.Connection,
        deleted: dict[str, int],
        table: str,
        where: str,
        params: tuple,
    ) -> int:
        cur = conn.execute(f"DELETE FROM {table} WHERE {where}", params)
        count = _rowcount(cur)
        deleted[table] = deleted.get(table, 0) + count
        return count

    try:
        with _LOCK:
            conn = _conn()
            if conn.execute("SELECT 1 FROM users WHERE user_id=?", (uid,)).fetchone() is None:
                return None

            identities = [
                {
                    "platform": str(r["platform"] or ""),
                    "platform_user_id": str(r["platform_user_id"] or ""),
                }
                for r in _rows(
                    conn,
                    "SELECT platform, platform_user_id FROM user_identities WHERE internal_user_id=?",
                    (uid,),
                )
            ]
            session_hashes = [
                str(r["session_hash"] or "")
                for r in _rows(
                    conn,
                    "SELECT session_hash FROM web_auth_sessions WHERE internal_user_id=?",
                    (uid,),
                )
                if r["session_hash"]
            ]
            deleted: dict[str, int] = {}

            _delete(conn, deleted, "web_chat_messages", "user_id=?", (uid,))
            _delete(conn, deleted, "web_chats", "user_id=?", (uid,))
            _delete(conn, deleted, "seller_sku_items", "user_id=?", (uid,))
            _delete(conn, deleted, "seller_sku_projects", "user_id=?", (uid,))
            _delete(conn, deleted, "seller_profiles", "user_id=?", (uid,))
            _delete(conn, deleted, "user_gallery", "user_id=?", (uid,))
            _delete(conn, deleted, "prompt_history", "user_id=?", (uid,))
            _delete(conn, deleted, "support_tickets", "user_id=?", (uid,))
            _delete(conn, deleted, "promo_redemptions", "user_id=?", (uid,))
            _delete(conn, deleted, "user_streaks", "user_id=?", (uid,))
            _delete(conn, deleted, "acquisitions", "user_id=?", (uid,))
            _delete(conn, deleted, "credits", "user_id=?", (uid,))
            _delete(conn, deleted, "events", "user_id=?", (uid,))
            _delete(conn, deleted, "flow_jobs", "user_id=?", (uid,))
            _delete(conn, deleted, "transactions", "user_id=?", (uid,))
            _delete(conn, deleted, "referrals", "referrer_user_id=?", (uid,))
            _delete(conn, deleted, "referrals", "referred_user_id=?", (uid,))
            _delete(conn, deleted, "referral_ongoing_rewards", "referrer_user_id=?", (uid,))
            _delete(conn, deleted, "referral_ongoing_rewards", "referred_user_id=?", (uid,))
            _delete(conn, deleted, "referral_first_generation_rewards", "referrer_user_id=?", (uid,))
            _delete(conn, deleted, "referral_first_generation_rewards", "referred_user_id=?", (uid,))
            _delete(conn, deleted, "identity_welcome_grants", "internal_user_id=?", (uid,))
            if session_hashes:
                placeholders = ",".join("?" for _ in session_hashes)
                _delete(
                    conn,
                    deleted,
                    "web_oauth_states",
                    f"session_hash IN ({placeholders})",
                    tuple(session_hashes),
                )
                _delete(
                    conn,
                    deleted,
                    "web_login_challenges",
                    f"session_hash IN ({placeholders})",
                    tuple(session_hashes),
                )
            _delete(conn, deleted, "web_auth_sessions", "internal_user_id=?", (uid,))

            if identities:
                clauses = []
                params: list[str] = []
                for item in identities:
                    platform = item["platform"]
                    platform_user_id = item["platform_user_id"]
                    if not platform or not platform_user_id:
                        continue
                    clauses.append("(platform=? AND platform_user_id=?)")
                    params.extend([platform, platform_user_id])
                if clauses:
                    cur = conn.execute(
                        "DELETE FROM web_login_challenges WHERE " + " OR ".join(clauses),
                        tuple(params),
                    )
                    deleted["web_login_challenges"] = max(int(cur.rowcount or 0), 0)

            _delete(conn, deleted, "user_identities", "internal_user_id=?", (uid,))
            _delete(conn, deleted, "users", "user_id=?", (uid,))
            conn.commit()

        return {
            "user_id": uid,
            "identities_deleted": len(identities),
            "deleted": deleted,
            "rows_deleted": int(sum(deleted.values())),
        }
    except Exception:  # noqa: BLE001
        log.warning("admin_delete_user failed for user_id=%r", user_id, exc_info=True)
        return None


def grant_milestone_if_joined(
    *,
    referred_user_id: int,
    reward_credits: int,
    first_payment_transaction_id: int | None = None,
) -> bool:
    """Атомарно «забрать» milestone-награду: joined → rewarded одним UPDATE.

    Возвращает ``True`` только если строка реально перешла из ``joined`` в
    ``rewarded`` (rowcount > 0). Двум конкурентным платежам приглашённого SQLite
    отдаст переход ровно одному — без TOCTOU-окна между чтением статуса и
    начислением. Начислять кредиты рефереру следует ТОЛЬКО при ``True``.
    """
    try:
        with _LOCK:
            conn = _conn()
            cur = conn.execute(
                "UPDATE referrals SET status='rewarded', reward_credits=?, "
                "first_payment_transaction_id=?, rewarded_at=datetime('now') "
                "WHERE referred_user_id=? AND status='joined'",
                (reward_credits, first_payment_transaction_id, referred_user_id),
            )
            conn.commit()
            return cur.rowcount > 0
    except Exception:  # noqa: BLE001
        log.warning("grant_milestone_if_joined failed for %r", referred_user_id, exc_info=True)
        return False


# NB: the per-referral "+50 for first generation" reward was removed (referral
# rewards to the referrer now fire only in on_successful_payment — anti-farm,
# REFERRAL.md §3). The referral_first_generation_rewards table is kept read-only
# for historical analytics; nothing writes to it anymore.


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


def referral_is_active(referred_user_id: int, window_days: int) -> bool:
    """Whether the referral attribution is still inside its reward window.

    Привязка живёт ``window_days`` с момента приглашения (``created_at``). После
    этого рефереру ничего не начисляется — ни разовый бонус, ни проценты. При
    ошибке/отсутствии записи возвращаем False (консервативно — не платим).
    """
    try:
        with _LOCK:
            conn = _conn()
            row = conn.execute(
                "SELECT 1 FROM referrals "
                "WHERE referred_user_id=? "
                "AND created_at >= datetime('now', ?)",
                (referred_user_id, f"-{max(0, int(window_days))} days"),
            ).fetchone()
            return row is not None
    except Exception:  # noqa: BLE001
        log.warning("referral_is_active failed", exc_info=True)
        return False


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
            first_generation = conn.execute(
                f"SELECT COALESCE(SUM(reward_credits),0) FROM referral_first_generation_rewards "
                f"WHERE referrer_user_id=? AND {today}",
                (referrer_user_id,),
            ).fetchone()[0] or 0
            return int(milestone) + int(ongoing) + int(first_generation)
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
            earned_g = conn.execute(
                "SELECT COALESCE(SUM(reward_credits),0) FROM referral_first_generation_rewards "
                "WHERE referrer_user_id=?",
                (referrer_user_id,),
            ).fetchone()[0] or 0
            return {"invited": int(invited), "earned": int(earned_m) + int(earned_o) + int(earned_g)}
    except Exception:  # noqa: BLE001
        log.warning("referral_stats failed", exc_info=True)
        return {"invited": 0, "earned": 0}


# ── credits store ──────────────────────────────────────────────────────
#
# These functions mirror the CreditStore (JSON) API but persist to the
# ``credits`` SQLite table. They follow the same contract as the rest of
# this module: never raise into the caller, swallow errors at WARNING level.


def ensure_user_identity(
    platform: str,
    platform_user_id: str | int,
    *,
    legacy_user_id: int | None = None,
) -> int:
    """Return the internal user id for one platform identity.

    Telegram keeps the historical positive Telegram user id so existing
    balances/referrals/payments remain compatible. New platforms are assigned
    negative ids, keeping Telegram ``42`` and MAX ``42`` in separate credit
    namespaces by default.
    """
    try:
        identity = platform_identity(platform, platform_user_id)
        preferred_id = (
            telegram_legacy_internal_id(identity.platform_user_id, legacy_user_id)
            if uses_telegram_legacy_id(identity.platform)
            else None
        )
        with _LOCK:
            conn = _conn()
            row = conn.execute(
                "SELECT internal_user_id FROM user_identities "
                "WHERE platform=? AND platform_user_id=?",
                (identity.platform, identity.platform_user_id),
            ).fetchone()
            if row is not None:
                internal_id = int(row[0])
                conn.execute(
                    "UPDATE user_identities SET last_seen_at=datetime('now') "
                    "WHERE platform=? AND platform_user_id=?",
                    (identity.platform, identity.platform_user_id),
                )
                _project_identity_user(
                    conn,
                    internal_user_id=internal_id,
                    platform=identity.platform,
                )
                conn.commit()
                return internal_id

            internal_id = preferred_id if preferred_id is not None else _next_external_identity_id(conn)
            conn.execute(
                "INSERT INTO user_identities "
                "(platform, platform_user_id, internal_user_id, created_at, last_seen_at) "
                "VALUES (?, ?, ?, datetime('now'), datetime('now'))",
                (identity.platform, identity.platform_user_id, internal_id),
            )
            _project_identity_user(
                conn,
                internal_user_id=internal_id,
                platform=identity.platform,
            )
            conn.commit()
            return int(internal_id)
    except Exception:  # noqa: BLE001
        log.warning(
            "ensure_user_identity failed for platform=%r platform_user_id=%r",
            platform,
            platform_user_id,
            exc_info=True,
        )
        return 0


def get_user_identity(platform: str, platform_user_id: str | int) -> dict | None:
    """Return a stored identity mapping, or ``None`` when absent."""
    try:
        identity = platform_identity(platform, platform_user_id)
        with _LOCK:
            conn = _conn()
            row = conn.execute(
                "SELECT platform, platform_user_id, internal_user_id, created_at, last_seen_at "
                "FROM user_identities WHERE platform=? AND platform_user_id=?",
                (identity.platform, identity.platform_user_id),
            ).fetchone()
            return dict(row) if row else None
    except Exception:  # noqa: BLE001
        log.warning("get_user_identity failed", exc_info=True)
        return None


def get_identity_by_internal_id(internal_user_id: int) -> dict | None:
    """Return the platform identity owning an internal id, or ``None``."""
    try:
        with _LOCK:
            conn = _conn()
            row = conn.execute(
                "SELECT platform, platform_user_id, internal_user_id, created_at, last_seen_at "
                "FROM user_identities WHERE internal_user_id=?",
                (int(internal_user_id),),
            ).fetchone()
            return dict(row) if row else None
    except Exception:  # noqa: BLE001
        log.warning("get_identity_by_internal_id failed", exc_info=True)
        return None


def _web_auth_digest(namespace: str, value: str) -> str:
    raw = f"{namespace}\0{value}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def bind_web_auth_session(
    session_id: str,
    platform: str,
    platform_user_id: str | int,
    display_name: str | None,
    *,
    expires_at: int,
    now: int | None = None,
) -> dict | None:
    """Bind an opaque browser session to one verified provider identity."""
    try:
        identity = platform_identity(platform, platform_user_id)
        internal_id = ensure_user_identity(identity.platform, identity.platform_user_id)
        if not internal_id:
            return None
        timestamp = int(time.time() if now is None else now)
        expiry = int(expires_at)
        if expiry <= timestamp:
            return None
        session_hash = _web_auth_digest("session", str(session_id))
        with _LOCK:
            conn = _conn()
            conn.execute(
                "INSERT INTO web_auth_sessions "
                "(session_hash, platform, platform_user_id, internal_user_id, display_name, "
                " created_at, last_seen_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(session_hash) DO UPDATE SET "
                "platform=excluded.platform, platform_user_id=excluded.platform_user_id, "
                "internal_user_id=excluded.internal_user_id, display_name=excluded.display_name, "
                "last_seen_at=excluded.last_seen_at, expires_at=excluded.expires_at",
                (
                    session_hash,
                    identity.platform,
                    identity.platform_user_id,
                    int(internal_id),
                    (str(display_name).strip()[:120] if display_name else None),
                    timestamp,
                    timestamp,
                    expiry,
                ),
            )
            _project_identity_user(
                conn,
                internal_user_id=internal_id,
                platform=identity.platform,
                first_name=(str(display_name).strip()[:120] if display_name else None),
            )
            conn.execute("DELETE FROM web_auth_sessions WHERE expires_at <= ?", (timestamp,))
            conn.commit()
        return {
            "platform": identity.platform,
            "platform_user_id": identity.platform_user_id,
            "internal_user_id": int(internal_id),
            "display_name": str(display_name).strip()[:120] if display_name else None,
            "expires_at": expiry,
        }
    except Exception:  # noqa: BLE001 - authentication must fail closed
        log.warning("bind_web_auth_session failed", exc_info=True)
        return None


def get_web_auth_session(session_id: str, *, now: int | None = None) -> dict | None:
    """Return a non-expired browser authentication binding."""
    try:
        timestamp = int(time.time() if now is None else now)
        session_hash = _web_auth_digest("session", str(session_id))
        with _LOCK:
            conn = _conn()
            row = conn.execute(
                "SELECT platform, platform_user_id, internal_user_id, display_name, expires_at "
                "FROM web_auth_sessions WHERE session_hash=? AND expires_at>?",
                (session_hash, timestamp),
            ).fetchone()
            if row is None:
                conn.execute("DELETE FROM web_auth_sessions WHERE session_hash=?", (session_hash,))
                conn.commit()
                return None
            conn.execute(
                "UPDATE web_auth_sessions SET last_seen_at=? WHERE session_hash=?",
                (timestamp, session_hash),
            )
            conn.commit()
            return dict(row)
    except Exception:  # noqa: BLE001
        log.warning("get_web_auth_session failed", exc_info=True)
        return None


def delete_web_auth_session(session_id: str) -> bool:
    try:
        session_hash = _web_auth_digest("session", str(session_id))
        with _LOCK:
            conn = _conn()
            changed = conn.execute(
                "DELETE FROM web_auth_sessions WHERE session_hash=?", (session_hash,)
            ).rowcount
            conn.commit()
            return bool(changed)
    except Exception:  # noqa: BLE001
        log.warning("delete_web_auth_session failed", exc_info=True)
        return False


def grant_identity_welcome_credits(
    platform: str,
    platform_user_id: str | int,
    internal_user_id: int,
    credits: int,
) -> dict | None:
    """Atomically grant provider-scoped welcome credits at most once.

    The verified identity must already be present in ``user_identities`` and
    own ``internal_user_id``.  A successful retry returns the current balance
    with ``granted=False``; storage/identity errors return ``None`` so callers
    can fail closed instead of promising a bonus that was not persisted.
    """
    try:
        identity = platform_identity(platform, platform_user_id)
        uid = int(internal_user_id)
        amount = int(credits)
        if not uid or amount <= 0:
            return None
        with _LOCK:
            conn = _conn()
            conn.execute("BEGIN IMMEDIATE")
            owner = conn.execute(
                "SELECT internal_user_id FROM user_identities "
                "WHERE platform=? AND platform_user_id=?",
                (identity.platform, identity.platform_user_id),
            ).fetchone()
            if owner is None or int(owner[0]) != uid:
                conn.rollback()
                return None
            inserted = conn.execute(
                "INSERT OR IGNORE INTO identity_welcome_grants "
                "(platform, platform_user_id, internal_user_id, credits) "
                "VALUES (?, ?, ?, ?)",
                (identity.platform, identity.platform_user_id, uid, amount),
            ).rowcount
            if inserted:
                conn.execute(
                    "INSERT INTO credits (user_id, balance, granted, updated_at) "
                    "VALUES (?, ?, 1, datetime('now')) "
                    "ON CONFLICT(user_id) DO UPDATE SET "
                    "balance=balance+excluded.balance, granted=1, "
                    "updated_at=datetime('now')",
                    (uid, amount),
                )
            row = conn.execute(
                "SELECT balance FROM credits WHERE user_id=?", (uid,)
            ).fetchone()
            conn.commit()
            return {
                "granted": bool(inserted),
                "balance": int(row[0]) if row else 0,
            }
    except Exception:  # noqa: BLE001 - welcome credit must fail closed
        try:
            if _CONN is not None:
                _CONN.rollback()
        except Exception:  # noqa: BLE001
            pass
        log.warning("grant_identity_welcome_credits failed", exc_info=True)
        return None


def create_web_login_challenge(
    session_id: str,
    challenge: str,
    *,
    expires_at: int,
    now: int | None = None,
) -> bool:
    """Create one short-lived Telegram bot handshake for this browser."""
    try:
        timestamp = int(time.time() if now is None else now)
        expiry = int(expires_at)
        if expiry <= timestamp:
            return False
        session_hash = _web_auth_digest("session", str(session_id))
        challenge_hash = _web_auth_digest("challenge", str(challenge))
        with _LOCK:
            conn = _conn()
            conn.execute(
                "DELETE FROM web_login_challenges "
                "WHERE session_hash=? OR expires_at<=?",
                (session_hash, timestamp),
            )
            conn.execute(
                "INSERT INTO web_login_challenges "
                "(challenge_hash, session_hash, platform, created_at, expires_at) "
                "VALUES (?, ?, 'telegram', ?, ?)",
                (challenge_hash, session_hash, timestamp, expiry),
            )
            conn.commit()
        return True
    except Exception:  # noqa: BLE001
        log.warning("create_web_login_challenge failed", exc_info=True)
        return False


def claim_web_login_challenge(
    challenge: str,
    platform: str,
    platform_user_id: str | int,
    display_name: str | None,
    confirmation_code: str,
    *,
    now: int | None = None,
) -> bool:
    """Attach the Telegram user who opened a deep link to its challenge."""
    try:
        identity = platform_identity(platform, platform_user_id)
        if identity.platform != "telegram":
            return False
        timestamp = int(time.time() if now is None else now)
        challenge_hash = _web_auth_digest("challenge", str(challenge))
        confirmation_hash = _web_auth_digest(
            f"confirmation:{challenge_hash}", str(confirmation_code)
        )
        with _LOCK:
            conn = _conn()
            changed = conn.execute(
                "UPDATE web_login_challenges SET platform_user_id=?, display_name=?, "
                "confirmation_hash=? WHERE challenge_hash=? AND platform='telegram' "
                "AND platform_user_id IS NULL AND consumed_at IS NULL AND expires_at>?",
                (
                    identity.platform_user_id,
                    str(display_name).strip()[:120] if display_name else None,
                    confirmation_hash,
                    challenge_hash,
                    timestamp,
                ),
            ).rowcount
            conn.commit()
            return changed == 1
    except Exception:  # noqa: BLE001
        log.warning("claim_web_login_challenge failed", exc_info=True)
        return False


def complete_web_login_challenge(
    session_id: str,
    confirmation_code: str,
    *,
    now: int | None = None,
    max_attempts: int = 5,
) -> dict | None:
    """Consume a claimed Telegram challenge after the browser enters its code."""
    try:
        timestamp = int(time.time() if now is None else now)
        session_hash = _web_auth_digest("session", str(session_id))
        with _LOCK:
            conn = _conn()
            row = conn.execute(
                "SELECT challenge_hash, platform, platform_user_id, display_name, "
                "confirmation_hash, attempts FROM web_login_challenges "
                "WHERE session_hash=? AND platform_user_id IS NOT NULL "
                "AND consumed_at IS NULL AND expires_at>? "
                "ORDER BY created_at DESC LIMIT 1",
                (session_hash, timestamp),
            ).fetchone()
            if row is None or int(row["attempts"] or 0) >= int(max_attempts):
                return None
            attempts = int(row["attempts"] or 0) + 1
            supplied_hash = _web_auth_digest(
                f"confirmation:{row['challenge_hash']}", str(confirmation_code)
            )
            valid = bool(row["confirmation_hash"]) and hmac.compare_digest(
                supplied_hash, str(row["confirmation_hash"])
            )
            if valid:
                changed = conn.execute(
                    "UPDATE web_login_challenges SET attempts=?, consumed_at=? "
                    "WHERE challenge_hash=? AND consumed_at IS NULL",
                    (attempts, timestamp, row["challenge_hash"]),
                ).rowcount
                conn.commit()
                if changed != 1:
                    return None
                return {
                    "platform": str(row["platform"]),
                    "platform_user_id": str(row["platform_user_id"]),
                    "display_name": row["display_name"],
                }
            conn.execute(
                "UPDATE web_login_challenges SET attempts=? WHERE challenge_hash=?",
                (attempts, row["challenge_hash"]),
            )
            conn.commit()
            return None
    except Exception:  # noqa: BLE001
        log.warning("complete_web_login_challenge failed", exc_info=True)
        return None


def create_web_oauth_state(
    session_id: str,
    state: str,
    provider: str,
    code_verifier: str,
    *,
    expires_at: int,
    now: int | None = None,
) -> bool:
    try:
        timestamp = int(time.time() if now is None else now)
        expiry = int(expires_at)
        normalized_provider = str(provider).strip().lower()
        if not normalized_provider or expiry <= timestamp:
            return False
        session_hash = _web_auth_digest("session", str(session_id))
        state_hash = _web_auth_digest(f"oauth:{normalized_provider}", str(state))
        with _LOCK:
            conn = _conn()
            conn.execute(
                "DELETE FROM web_oauth_states WHERE session_hash=? OR expires_at<=?",
                (session_hash, timestamp),
            )
            conn.execute(
                "INSERT INTO web_oauth_states "
                "(state_hash, session_hash, provider, code_verifier, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (state_hash, session_hash, normalized_provider, str(code_verifier), timestamp, expiry),
            )
            conn.commit()
        return True
    except Exception:  # noqa: BLE001
        log.warning("create_web_oauth_state failed", exc_info=True)
        return False


def consume_web_oauth_state(
    session_id: str,
    state: str,
    provider: str,
    *,
    now: int | None = None,
) -> str | None:
    """Atomically consume OAuth state and return its server-side PKCE verifier."""
    try:
        timestamp = int(time.time() if now is None else now)
        normalized_provider = str(provider).strip().lower()
        session_hash = _web_auth_digest("session", str(session_id))
        state_hash = _web_auth_digest(f"oauth:{normalized_provider}", str(state))
        with _LOCK:
            conn = _conn()
            row = conn.execute(
                "SELECT code_verifier FROM web_oauth_states WHERE state_hash=? "
                "AND session_hash=? AND provider=? AND consumed_at IS NULL AND expires_at>?",
                (state_hash, session_hash, normalized_provider, timestamp),
            ).fetchone()
            if row is None:
                return None
            changed = conn.execute(
                "UPDATE web_oauth_states SET consumed_at=? "
                "WHERE state_hash=? AND consumed_at IS NULL",
                (timestamp, state_hash),
            ).rowcount
            conn.commit()
            return str(row["code_verifier"]) if changed == 1 else None
    except Exception:  # noqa: BLE001
        log.warning("consume_web_oauth_state failed", exc_info=True)
        return None


def consume_web_auth_assertion(
    provider: str,
    assertion: str,
    *,
    expires_at: int,
    now: int | None = None,
) -> bool:
    """Record a signed provider assertion once to prevent replay."""
    try:
        timestamp = int(time.time() if now is None else now)
        expiry = int(expires_at)
        normalized_provider = str(provider).strip().lower()
        if not normalized_provider or expiry <= timestamp:
            return False
        assertion_hash = _web_auth_digest(
            f"assertion:{normalized_provider}", str(assertion)
        )
        with _LOCK:
            conn = _conn()
            conn.execute("DELETE FROM web_auth_assertions WHERE expires_at<=?", (timestamp,))
            changed = conn.execute(
                "INSERT OR IGNORE INTO web_auth_assertions "
                "(provider, assertion_hash, used_at, expires_at) VALUES (?, ?, ?, ?)",
                (normalized_provider, assertion_hash, timestamp, expiry),
            ).rowcount
            conn.commit()
            return changed == 1
    except Exception:  # noqa: BLE001
        log.warning("consume_web_auth_assertion failed", exc_info=True)
        return False


def credits_balance_for_identity(platform: str, platform_user_id: str | int, starter: int) -> int:
    internal_id = ensure_user_identity(platform, platform_user_id)
    return credits_balance(internal_id, starter) if internal_id else 0


def credits_charge_for_identity(platform: str, platform_user_id: str | int, amount: int, starter: int) -> bool:
    internal_id = ensure_user_identity(platform, platform_user_id)
    return credits_charge(internal_id, amount, starter) if internal_id else False


def credits_refund_for_identity(platform: str, platform_user_id: str | int, amount: int) -> None:
    internal_id = ensure_user_identity(platform, platform_user_id)
    if internal_id:
        credits_refund(internal_id, amount)


def credits_add_for_identity(platform: str, platform_user_id: str | int, amount: int, starter: int) -> int:
    internal_id = ensure_user_identity(platform, platform_user_id)
    return credits_add(internal_id, amount, starter) if internal_id else 0


def _next_external_identity_id(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT MIN(internal_user_id) FROM user_identities WHERE internal_user_id < 0"
    ).fetchone()
    current_min = row[0] if row else None
    return -1 if current_min is None else int(current_min) - 1


def credits_balance(user_id: int, starter: int) -> int:
    """Return the user's balance, granting ``starter`` on first access (atomic).

    If the user has no row yet (``granted=0``), the starter bonus is added
    atomically via a single UPSERT so the grant is idempotent across concurrent
    calls (only the first write wins). Returns 0 on any DB error.
    """
    try:
        uid = int(user_id)
        st = int(starter)
        with _LOCK:
            conn = _conn()
            row = conn.execute(
                "SELECT balance, granted FROM credits WHERE user_id=?", (uid,)
            ).fetchone()
            if row is None:
                # New user — insert with starter already applied and granted=1.
                conn.execute(
                    "INSERT INTO credits (user_id, balance, granted, updated_at) "
                    "VALUES (?, ?, 1, datetime('now'))",
                    (uid, st),
                )
                conn.commit()
                return st
            if row[1] == 0:
                # Existing row but starter not yet granted (edge case: row created
                # before this migration, or by a manual INSERT).
                new_bal = row[0] + st
                conn.execute(
                    "UPDATE credits SET balance=?, granted=1, updated_at=datetime('now') "
                    "WHERE user_id=?",
                    (new_bal, uid),
                )
                conn.commit()
                return new_bal
            return int(row[0])
    except Exception:  # noqa: BLE001
        log.warning("credits_balance failed for user_id=%r", user_id, exc_info=True)
        return 0


def credits_charge(user_id: int, amount: int, starter: int) -> bool:
    """Deduct ``amount`` if the user can afford it; return True on success.

    If ``amount <= 0`` the call is a no-op and returns True. Grants the
    starter bonus first (via :func:`credits_balance`) if not yet applied.
    Returns False on insufficient funds or on DB error (safe default).
    """
    try:
        amount = int(amount)
        if amount <= 0:
            return True
        uid = int(user_id)
        # Ensure starter is applied and we have a fresh balance.
        bal = credits_balance(uid, starter)
        if bal < amount:
            return False
        with _LOCK:
            conn = _conn()
            # UPDATE only if balance is still sufficient (avoids races).
            cur = conn.execute(
                "UPDATE credits SET balance=balance-?, updated_at=datetime('now') "
                "WHERE user_id=? AND balance>=?",
                (amount, uid, amount),
            )
            conn.commit()
            return cur.rowcount > 0
    except Exception:  # noqa: BLE001
        log.warning("credits_charge failed for user_id=%r", user_id, exc_info=True)
        return False


def credits_refund(user_id: int, amount: int) -> None:
    """Add ``amount`` back to the user's balance (refund). Never raises.

    A no-op when ``amount <= 0``. The row must exist (charge was called
    first), but if it somehow doesn't the refund is still applied via UPSERT
    with granted=1 so no accidental starter re-grant occurs.
    """
    try:
        amount = int(amount)
        if amount <= 0:
            return
        uid = int(user_id)
        with _LOCK:
            conn = _conn()
            conn.execute(
                "INSERT INTO credits (user_id, balance, granted, updated_at) "
                "VALUES (?, ?, 1, datetime('now')) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "balance=balance+excluded.balance, updated_at=datetime('now')",
                (uid, amount),
            )
            conn.commit()
    except Exception:  # noqa: BLE001
        log.warning("credits_refund failed for user_id=%r", user_id, exc_info=True)


def credits_add(user_id: int, amount: int, starter: int) -> int:
    """Top up by ``amount`` and return the new balance.

    Grants the starter bonus first if not yet applied. Returns 0 on error.
    """
    try:
        uid = int(user_id)
        # Ensure starter is applied.
        credits_balance(uid, starter)
        with _LOCK:
            conn = _conn()
            conn.execute(
                "UPDATE credits SET balance=balance+?, updated_at=datetime('now') "
                "WHERE user_id=?",
                (int(amount), uid),
            )
            conn.commit()
            row = conn.execute(
                "SELECT balance FROM credits WHERE user_id=?", (uid,)
            ).fetchone()
            return int(row[0]) if row else 0
    except Exception:  # noqa: BLE001
        log.warning("credits_add failed for user_id=%r", user_id, exc_info=True)
        return 0


def credits_migrate_from_json(path) -> int:
    """Read ``user_credits.json`` and upsert all rows into the credits table.

    Uses ``INSERT OR IGNORE`` so it is safe to call repeatedly (idempotent).
    Already-migrated users are skipped. Returns the count of newly inserted
    rows. Returns 0 on a missing/empty/invalid JSON file (not an error).
    """
    import json as _json
    from pathlib import Path as _Path

    try:
        data = _json.loads(_Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0

    balances = data.get("balances", {})
    granted_set = set(data.get("granted", []))
    if not isinstance(balances, dict):
        return 0

    count = 0
    try:
        with _LOCK:
            conn = _conn()
            for key, value in balances.items():
                try:
                    uid = int(key)
                    bal = int(value)
                except (TypeError, ValueError):
                    continue
                granted_flag = 1 if str(key) in granted_set else 0
                cur = conn.execute(
                    "INSERT OR IGNORE INTO credits (user_id, balance, granted) "
                    "VALUES (?, ?, ?)",
                    (uid, bal, granted_flag),
                )
                count += cur.rowcount
            conn.commit()
    except Exception:  # noqa: BLE001
        log.warning("credits_migrate_from_json failed", exc_info=True)
        return 0
    return count


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
                "SELECT COUNT(*) FROM users "
                "WHERE date(first_seen,'localtime') = date('now','localtime')",
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
                    "model": r["model"] or "—",
                    "jobs": int(r["jobs"]),
                    "flow_credits_delta_sum": int(r["delta_sum"] or 0),
                    "bot_credits_sum": int(r["bot_sum"] or 0),
                }
                for r in _rows(
                    conn,
                    "SELECT model, COUNT(*) AS jobs, "
                    "COALESCE(SUM(flow_credits_delta),0) AS delta_sum, "
                    "COALESCE(SUM(bot_credits_charged),0) AS bot_sum "
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
            total_first_generation_credits = _scalar(
                conn, "SELECT COALESCE(SUM(reward_credits),0) "
                "FROM referral_first_generation_rewards"
            ) or 0
            total_ongoing_credits = _scalar(
                conn, "SELECT COALESCE(SUM(reward_credits),0) "
                "FROM referral_ongoing_rewards"
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
                "total_reward_credits": int(total_reward_credits)
                + int(total_first_generation_credits)
                + int(total_ongoing_credits),
                "top_referrers": top_referrers,
            }
    except Exception:  # noqa: BLE001
        log.warning("report_refs failed", exc_info=True)
        return {
            "total_referrals": 0, "joined": 0, "rewarded": 0,
            "total_reward_credits": 0, "top_referrers": [],
        }


def report_sellers(limit: int = 100) -> dict:
    """Сводка по селлерам (юзерам, нажимавшим меню «Маркетплейсы»).

    Селлер = пользователь, у которого есть события ``mp_*`` (``mp_platform``,
    ``mp_job``, ``mp_done4you_open``). Для каждого: всего событий, сколько задач
    (``mp_job``), сколько заявок «под ключ» (``mp_done4you_open``), первый/последний
    контакт, и платежи (paid). Баланс кредитов живёт в отдельном credit store и
    здесь не считается. PII-ретеншн событий — как в остальной аналитике.
    """
    try:
        with _LOCK:
            conn = _conn()
            limit = max(1, min(int(limit), 1000))
            total_sellers = _scalar(
                conn,
                "SELECT COUNT(DISTINCT user_id) FROM events "
                "WHERE event_name LIKE 'mp\\_%' ESCAPE '\\' AND user_id IS NOT NULL",
            ) or 0
            totals = _rows(
                conn,
                "SELECT COUNT(*) AS events, "
                "SUM(CASE WHEN event_name='mp_job' THEN 1 ELSE 0 END) AS jobs, "
                "SUM(CASE WHEN event_name='mp_done4you_open' THEN 1 ELSE 0 END) AS done4you "
                "FROM events WHERE event_name LIKE 'mp\\_%' ESCAPE '\\' "
                "AND user_id IS NOT NULL",
            )
            total_events = int((totals[0]["events"] if totals else 0) or 0)
            total_jobs = int((totals[0]["jobs"] if totals else 0) or 0)
            total_done4you = int((totals[0]["done4you"] if totals else 0) or 0)
            # Платежи (paid) по всем юзерам — мёрджим в питоне, чтобы JOIN не
            # размножал счётчик событий.
            rev_by_user: dict[int, dict] = {}
            for r in _rows(
                conn,
                "SELECT user_id, COUNT(*) AS c, "
                "COALESCE(SUM(stars_amount),0) AS s, COALESCE(SUM(amount_rub),0) AS rub "
                "FROM transactions WHERE status='paid' AND user_id IS NOT NULL "
                "GROUP BY user_id",
            ):
                rev_by_user[int(r["user_id"])] = {
                    "paid_count": int(r["c"]),
                    "revenue_stars": int(r["s"] or 0),
                    "revenue_rub": float(r["rub"] or 0.0),
                }
            sku_by_user: dict[int, int] = {
                int(r["user_id"]): int(r["sku_projects"] or 0)
                for r in _rows(
                    conn,
                    "SELECT user_id, COUNT(*) AS sku_projects FROM ("
                    "SELECT user_id, sku FROM seller_sku_projects "
                    "UNION "
                    "SELECT user_id, sku FROM seller_sku_items"
                    ") GROUP BY user_id",
                )
            }
            total_sku_projects = _scalar(
                conn,
                "SELECT COUNT(*) FROM ("
                "SELECT user_id, sku FROM seller_sku_projects "
                "UNION "
                "SELECT user_id, sku FROM seller_sku_items"
                ")",
            ) or 0
            sellers = []
            for r in _rows(
                conn,
                "SELECT e.user_id, "
                "(SELECT e2.username FROM events e2 "
                " WHERE e2.user_id=e.user_id AND e2.username IS NOT NULL "
                " ORDER BY e2.id DESC LIMIT 1) AS username, "
                "COUNT(*) AS events, "
                "SUM(CASE WHEN event_name='mp_job' THEN 1 ELSE 0 END) AS jobs, "
                "SUM(CASE WHEN event_name='mp_done4you_open' THEN 1 ELSE 0 END) AS done4you, "
                "MIN(created_at) AS first_seen, MAX(created_at) AS last_seen "
                "FROM events e WHERE event_name LIKE 'mp\\_%' ESCAPE '\\' "
                "AND e.user_id IS NOT NULL "
                "GROUP BY e.user_id ORDER BY last_seen DESC LIMIT ?",
                (limit,),
            ):
                uid = int(r["user_id"])
                rev = rev_by_user.get(uid, {"paid_count": 0, "revenue_stars": 0, "revenue_rub": 0.0})
                recent_events = [
                    {
                        "id": int(er["id"]),
                        "event_name": er["event_name"],
                        "source": er["source"],
                        "created_at": er["created_at"],
                    }
                    for er in _rows(
                        conn,
                        "SELECT id, event_name, source, created_at FROM events "
                        "WHERE user_id=? AND event_name LIKE 'mp\\_%' ESCAPE '\\' "
                        "ORDER BY id DESC LIMIT 8",
                        (uid,),
                    )
                ]
                sellers.append({
                    "user_id": uid,
                    "username": r["username"],
                    "events": int(r["events"]),
                    "jobs": int(r["jobs"] or 0),
                    "done4you": int(r["done4you"] or 0),
                    "first_seen": r["first_seen"],
                    "last_seen": r["last_seen"],
                    "sku_projects": sku_by_user.get(uid, 0),
                    "recent_events": recent_events,
                    **rev,
                })
            total_paid_count = sum(int(s["paid_count"]) for s in sellers)
            total_revenue_stars = sum(int(s["revenue_stars"]) for s in sellers)
            total_revenue_rub = sum(float(s["revenue_rub"]) for s in sellers)
            return {
                "total_sellers": int(total_sellers),
                "total_events": total_events,
                "total_jobs": total_jobs,
                "total_done4you": total_done4you,
                "total_sku_projects": int(total_sku_projects),
                "total_paid_count": total_paid_count,
                "total_revenue_stars": total_revenue_stars,
                "total_revenue_rub": total_revenue_rub,
                "sellers": sellers,
            }
    except Exception:  # noqa: BLE001
        log.warning("report_sellers failed", exc_info=True)
        return {"total_sellers": 0, "sellers": []}


def _payload_dict(raw: str | None) -> dict:
    try:
        loaded = json.loads(raw) if raw else {}
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _video_family_guess(model_key: str | None, fallback: str = "unknown") -> str:
    key = (model_key or "").lower()
    if "veo" in key:
        return "veo"
    if "abra" in key or "omni" in key:
        return "omni-flash"
    return fallback


def _video_unusual_403(payload: dict, text: str | None = None) -> bool:
    if payload.get("unusual_403") or payload.get("public_error_unusual_activity_403"):
        return True
    haystack = " ".join(
        str(v or "") for v in (
            text,
            payload.get("body_preview"),
            payload.get("error"),
            payload.get("reason"),
        )
    )
    return "PUBLIC_ERROR_UNUSUAL_ACTIVITY" in haystack or "unusual activity" in haystack.lower()


def _video_event_samples(row: sqlite3.Row) -> list[dict]:
    payload = _payload_dict(row["payload_json"])
    event_name = row["event_name"]
    account = str(payload.get("account") or row["source"] or "?")
    created_at = str(row["created_at"] or "")
    if event_name == "video_ab":
        arms = payload.get("arms")
        if not isinstance(arms, list):
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            arms = result.get("arms") if isinstance(result.get("arms"), list) else []
        model_key = str(payload.get("model_key") or payload.get("model") or "unknown")
        family = str(payload.get("model_family") or _video_family_guess(model_key))
        endpoint = str(payload.get("endpoint") or payload.get("endpoint_name") or "video:batchAsyncGenerateVideoText")
        mode = str(payload.get("mode") or "text")
        out = []
        for idx, arm in enumerate(arms):
            if not isinstance(arm, dict):
                continue
            status = arm.get("status")
            try:
                status_int = int(status) if status is not None else None
            except (TypeError, ValueError):
                status_int = None
            ok = bool(arm.get("ok")) or status_int == 200
            had_403 = status_int == 403 or bool(arm.get("had_403"))
            attempts = max(1, idx + 1)
            out.append({
                "account": account,
                "model_key": model_key,
                "model_family": family,
                "endpoint": endpoint,
                "mode": mode,
                "transport": str(arm.get("transport") or "unknown"),
                "ok": ok,
                "attempts": attempts,
                "had_403": had_403,
                "unusual_403": had_403 and _video_unusual_403(payload, str(arm.get("body_preview") or "")),
                "success_after_retry": ok and attempts > 1,
                "created_at": created_at,
            })
        return out

    model_key = str(payload.get("model_key") or payload.get("model") or "unknown")
    family = str(payload.get("model_family") or _video_family_guess(model_key))
    transport = str(payload.get("transport") or ("browser_fetch" if payload.get("browser_fallback") else "direct_http"))
    try:
        attempts = max(1, int(payload.get("attempts") or 1))
    except (TypeError, ValueError):
        attempts = 1
    had_403 = bool(payload.get("had_403"))
    unusual_403 = _video_unusual_403(payload)
    return [{
        "account": account,
        "model_key": model_key,
        "model_family": family,
        "endpoint": str(payload.get("endpoint") or payload.get("endpoint_name") or payload.get("mode") or "unknown"),
        "mode": str(payload.get("mode") or "unknown"),
        "transport": transport,
        "ok": bool(payload.get("ok")),
        "attempts": attempts,
        "had_403": had_403 or unusual_403,
        "unusual_403": unusual_403,
        "success_after_retry": bool(payload.get("success_after_retry")) or (bool(payload.get("ok")) and attempts > 1),
        "created_at": created_at,
    }]


def _video_health_rows(conn: sqlite3.Connection, hours: int) -> list[dict]:
    rows = _rows(
        conn,
        "SELECT event_name, source, payload_json, created_at FROM events "
        "WHERE event_name IN ('video_outcome', 'video_ab') "
        "AND created_at >= datetime('now', ?)",
        (f"-{int(hours)} hours",),
    )
    buckets: dict[tuple[str, str, str, str, str, str], dict] = {}
    for row in rows:
        for sample in _video_event_samples(row):
            key = (
                sample["account"],
                sample["model_key"],
                sample["model_family"],
                sample["endpoint"],
                sample["mode"],
                sample["transport"],
            )
            d = buckets.setdefault(key, {
                "account": sample["account"],
                "model_key": sample["model_key"],
                "model_family": sample["model_family"],
                "endpoint": sample["endpoint"],
                "mode": sample["mode"],
                "transport": sample["transport"],
                "attempts": 0,
                "attempts_total": 0,
                "403": 0,
                "403_public_error_unusual_activity": 0,
                "success": 0,
                "success_after_retry": 0,
                "final_fail": 0,
                "_attempts_sum_ok": 0,
                "last_success_at": None,
                "last_403_at": None,
            })
            d["attempts"] += 1
            d["attempts_total"] += int(sample.get("attempts") or 1)
            if sample.get("had_403"):
                d["403"] += 1
                d["last_403_at"] = max(d["last_403_at"] or "", sample["created_at"]) or None
            if sample.get("unusual_403"):
                d["403_public_error_unusual_activity"] += 1
            if sample.get("ok"):
                d["success"] += 1
                d["_attempts_sum_ok"] += int(sample.get("attempts") or 1)
                d["last_success_at"] = max(d["last_success_at"] or "", sample["created_at"]) or None
                if sample.get("success_after_retry"):
                    d["success_after_retry"] += 1
            else:
                d["final_fail"] += 1

    out = []
    for key, d in sorted(buckets.items()):
        total = int(d["attempts"])
        succ = int(d["success"])
        total_403 = int(d["403"])
        unusual = int(d["403_public_error_unusual_activity"])
        row = {
            "account": d["account"],
            "model_key": d["model_key"],
            "model_family": d["model_family"],
            "endpoint": d["endpoint"],
            "mode": d["mode"],
            "transport": d["transport"],
            "attempts": total,
            "attempts_total": int(d["attempts_total"]),
            "403": total_403,
            "403_public_error_unusual_activity": unusual,
            "unusual_403": unusual,
            "success": succ,
            "success_after_retry": int(d["success_after_retry"]),
            "final_fail": int(d["final_fail"]),
            "avg_attempts_before_200": round(d["_attempts_sum_ok"] / succ, 2) if succ else None,
            "last_success_at": d["last_success_at"],
            "last_403_at": d["last_403_at"],
            "success_rate": round(succ / total, 3) if total else None,
            "403_rate": round(total_403 / total, 3) if total else None,
            "unusual_403_rate": round(unusual / total, 3) if total else None,
            # Back-compat aliases used by existing admin/test code.
            "video_attempts": total,
            "video_403": total_403,
            "video_success": succ,
            "video_success_after_retry": int(d["success_after_retry"]),
            "video_final_fail": int(d["final_fail"]),
        }
        out.append(row)
    return out


def report_video_health(hours_list: tuple[int, ...] = (1, 24)) -> dict:
    """Per-account/model/endpoint/transport video health from video events."""
    out: dict = {"windows": {}}
    try:
        with _LOCK:
            conn = _conn()
            key_rates: dict[str, dict[tuple, float | None]] = {}
            for hours in hours_list:
                suffix = f"{int(hours)}h"
                rows = _video_health_rows(conn, int(hours))
                out["windows"][suffix] = rows
                key_rates[suffix] = {
                    (
                        r["account"], r["model_key"], r["model_family"],
                        r["endpoint"], r["mode"], r["transport"],
                    ): r.get("success_rate")
                    for r in rows
                }
        for rows in out["windows"].values():
            for r in rows:
                key = (
                    r["account"], r["model_key"], r["model_family"],
                    r["endpoint"], r["mode"], r["transport"],
                )
                r["success_rate_1h"] = key_rates.get("1h", {}).get(key)
                r["success_rate_24h"] = key_rates.get("24h", {}).get(key)
        out["rows"] = out["windows"].get("24h", next(iter(out["windows"].values()), []))
        return out
    except Exception:  # noqa: BLE001
        log.warning("report_video_health failed", exc_info=True)
        return {"windows": {}}


def _proxy_check_failed(payload: dict) -> bool:
    if payload.get("error") or payload.get("browser_error") or payload.get("api_error"):
        return True
    if payload.get("match") is False:
        return True
    return False


def _latest_proxy_checks(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = _rows(
        conn,
        "SELECT source, payload_json, created_at FROM events "
        "WHERE event_name='proxy_check' ORDER BY id DESC LIMIT 500",
    )
    latest: dict[str, dict] = {}
    for r in rows:
        payload = _payload_dict(r["payload_json"])
        account = str(payload.get("account") or r["source"] or "")
        if not account or account in latest:
            continue
        latest[account] = {
            "proxy_failed": _proxy_check_failed(payload),
            "proxy_checked_at": str(r["created_at"] or ""),
        }
    return latest


def _collapse_video_rows(rows: list[dict], model_family: str | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        if model_family and r.get("model_family") != model_family:
            continue
        account = str(r.get("account") or "")
        if not account:
            continue
        d = out.setdefault(account, {
            "attempts": 0, "success": 0, "403": 0, "unusual_403": 0, "attempts_ok_sum": 0,
            "last_success_at": None, "last_403_at": None,
        })
        attempts = int(r.get("attempts") or 0)
        success = int(r.get("success") or r.get("video_success") or 0)
        d["attempts"] += attempts
        d["success"] += success
        d["403"] += int(r.get("403") or r.get("video_403") or 0)
        d["unusual_403"] += int(r.get("unusual_403") or r.get("403_public_error_unusual_activity") or 0)
        avg = r.get("avg_attempts_before_200")
        if success and isinstance(avg, (int, float)):
            d["attempts_ok_sum"] += float(avg) * success
        if r.get("last_success_at"):
            d["last_success_at"] = max(d["last_success_at"] or "", str(r["last_success_at"])) or None
        if r.get("last_403_at"):
            d["last_403_at"] = max(d["last_403_at"] or "", str(r["last_403_at"])) or None
    return out


def report_video_account_scores(
    *,
    model_family: str | None = None,
    credit_hints: dict | None = None,
    min_credits: int = 0,
) -> dict:
    """Health score used by video routing; never performs network I/O."""
    try:
        health = report_video_health((1, 24))
        rows_1h = _collapse_video_rows(health.get("windows", {}).get("1h", []), model_family)
        rows_24h = _collapse_video_rows(health.get("windows", {}).get("24h", []), model_family)
        with _LOCK:
            proxy = _latest_proxy_checks(_conn())
        accounts = set(rows_1h) | set(rows_24h) | set(proxy) | set((credit_hints or {}).keys())
        scores = {}
        for account in sorted(accounts):
            h1 = rows_1h.get(account, {})
            h24 = rows_24h.get(account, {})
            attempts24 = int(h24.get("attempts") or 0)
            success24 = int(h24.get("success") or 0)
            total403_24 = int(h24.get("403") or 0)
            unusual24 = int(h24.get("unusual_403") or 0)
            success_rate = (success24 / attempts24) if attempts24 else None
            total_403_rate = (total403_24 / attempts24) if attempts24 else 0.0
            avg_attempts = (
                round(float(h24.get("attempts_ok_sum") or 0) / success24, 2)
                if success24 else None
            )
            recent_unusual = int(h1.get("unusual_403") or 0) > 0
            recent_403 = int(h1.get("403") or 0) > 0
            proxy_failed = bool((proxy.get(account) or {}).get("proxy_failed"))
            # "Clean" = succeeds on (near) first try. A retry-heavy account that
            # only gets a 200 after several captcha retries is fragile and wastes
            # provider attempts, so it must not outrank a clean idle account.
            clean = (not isinstance(avg_attempts, (int, float))) or avg_attempts <= 1.5
            score = 50.0
            if success_rate is not None:
                # Reward success weighted by efficiency: an account needing
                # avg N attempts earns ~1/N of the success bonus.
                efficiency = 1.0
                if isinstance(avg_attempts, (int, float)) and avg_attempts > 1:
                    efficiency = 1.0 / float(avg_attempts)
                score += 35.0 * success_rate * efficiency
                score -= 40.0 * total_403_rate
            if recent_unusual:
                score -= 45.0
            elif recent_403:
                score -= 20.0
            # Stale-success bonus only for clean accounts, so a flaky account
            # cannot ride one lucky retry-success above a clean idle one.
            if h24.get("last_success_at") and clean:
                score += 10.0
            if isinstance(avg_attempts, (int, float)) and avg_attempts > 1:
                score -= min(30.0, (float(avg_attempts) - 1.0) * 8.0)
            if proxy_failed:
                score -= 100.0
            credits = None
            is_paid = False
            hint = (credit_hints or {}).get(account)
            if isinstance(hint, dict):
                is_paid = bool(hint.get("is_paid"))
                try:
                    credits = int(float(hint.get("credits")))
                except (TypeError, ValueError):
                    credits = None
            if is_paid and (not min_credits or (credits is not None and credits >= int(min_credits))):
                score += 12.0
            elif min_credits and credits is not None and credits < int(min_credits):
                score -= 8.0
            scores[account] = {
                "account": account,
                "model_family": model_family,
                "score": round(score, 2),
                "success_rate_24h": round(success_rate, 3) if success_rate is not None else None,
                "403_rate_24h": round(total_403_rate, 3) if attempts24 else None,
                "avg_attempts_before_200": avg_attempts,
                "recent_unusual_403": recent_unusual,
                "proxy_failed": proxy_failed,
                "proxy_checked_at": (proxy.get(account) or {}).get("proxy_checked_at"),
                "last_success_at": h24.get("last_success_at"),
                "last_403_at": h24.get("last_403_at"),
                "paid_enough_credits": bool(
                    is_paid and (not min_credits or (credits is not None and credits >= int(min_credits)))
                ),
            }
        return scores
    except Exception:  # noqa: BLE001
        log.warning("report_video_account_scores failed", exc_info=True)
        return {}


_SEED_PASSIVE_EVENTS = {
    "user_started",
    "new_user",
    "acquired_from_channel",
    "channel_seed_clicked",
    "channel_seed_created",
    "channel_seed_new",
    "channel_seed_returning",
}


def _seed_user_stage(*, paid: bool, generated: bool, requested: bool, interacted: bool) -> str:
    if paid:
        return "paid"
    if generated:
        return "generated"
    if requested:
        return "requested"
    if interacted:
        return "interacted"
    return "started_only"


def report_channels() -> dict:
    """Атрибуция трафика по рекламным каналам (deep-link ``seed_<канал>``).

    Для каждого канала: сколько юзеров привлечено (all-time), сколько из них
    заплатили хоть раз и суммарная выручка (звёзды + ₽), приписанная их
    платежам. Выручка считается по платящим юзерам канала (LEFT JOIN
    acquisitions→transactions), поэтому ноль платежей даёт нули, а не пропуск.
    """
    try:
        with _LOCK:
            conn = _conn()
            total_acquired = _scalar(conn, "SELECT COUNT(*) FROM acquisitions") or 0
            by_channel: dict[str, dict] = {}

            def bucket(channel: str | None) -> dict | None:
                channel = (channel or "").strip()
                if not channel:
                    return None
                if channel not in by_channel:
                    by_channel[channel] = {
                        "channel": channel,
                        "seed_links_created": 0,
                        "seed_clicks": 0,
                        "unique_click_users": 0,
                        "returning_clicks": 0,
                        "users": 0,
                        "started_only": 0,
                        "interacted_users": 0,
                        "requested_users": 0,
                        "generated_users": 0,
                        "paid_users": 0,
                        "revenue_stars": 0,
                        "revenue_rub": 0.0,
                        "recent_users": [],
                    }
                return by_channel[channel]

            for r in _rows(
                conn,
                "SELECT a.channel AS channel, "
                "COUNT(DISTINCT a.user_id) AS users, "
                "COUNT(DISTINCT CASE WHEN t.status='paid' THEN t.user_id END) AS paid_users, "
                "COALESCE(SUM(CASE WHEN t.status='paid' THEN t.stars_amount END),0) AS revenue_stars, "
                "COALESCE(SUM(CASE WHEN t.status='paid' THEN t.amount_rub END),0) AS revenue_rub "
                "FROM acquisitions a "
                "LEFT JOIN transactions t ON t.user_id = a.user_id "
                "GROUP BY a.channel",
            ):
                item = bucket(r["channel"])
                if item is None:
                    continue
                item["users"] = int(r["users"])
                item["paid_users"] = int(r["paid_users"])
                item["revenue_stars"] = int(r["revenue_stars"] or 0)
                item["revenue_rub"] = float(r["revenue_rub"] or 0.0)

            for r in _rows(
                conn,
                "SELECT source AS channel, COUNT(*) AS created "
                "FROM events WHERE event_name='channel_seed_created' "
                "AND source IS NOT NULL AND source!='' GROUP BY source",
            ):
                item = bucket(r["channel"])
                if item is not None:
                    item["seed_links_created"] = int(r["created"] or 0)

            for r in _rows(
                conn,
                "SELECT source AS channel, COUNT(*) AS clicks, "
                "COUNT(DISTINCT user_id) AS unique_users "
                "FROM events WHERE event_name='channel_seed_clicked' "
                "AND source IS NOT NULL AND source!='' GROUP BY source",
            ):
                item = bucket(r["channel"])
                if item is None:
                    continue
                item["seed_clicks"] = int(r["clicks"] or 0)
                item["unique_click_users"] = int(r["unique_users"] or 0)

            for r in _rows(
                conn,
                "SELECT source AS channel, COUNT(*) AS clicks "
                "FROM events WHERE event_name='channel_seed_returning' "
                "AND source IS NOT NULL AND source!='' GROUP BY source",
            ):
                item = bucket(r["channel"])
                if item is not None:
                    item["returning_clicks"] = int(r["clicks"] or 0)

            passive = ",".join(f"'{name}'" for name in sorted(_SEED_PASSIVE_EVENTS))
            for r in _rows(
                conn,
                "SELECT a.channel, a.user_id, a.created_at AS acquired_at, "
                "u.username, u.first_name, u.first_seen, u.last_active, "
                f"EXISTS(SELECT 1 FROM events e WHERE e.user_id=a.user_id "
                f"       AND e.event_name NOT IN ({passive})) AS interacted, "
                "EXISTS(SELECT 1 FROM flow_jobs fj WHERE fj.user_id=a.user_id) AS requested, "
                "EXISTS(SELECT 1 FROM flow_jobs fj WHERE fj.user_id=a.user_id "
                "       AND fj.status='success') AS generated, "
                "EXISTS(SELECT 1 FROM transactions t WHERE t.user_id=a.user_id "
                "       AND t.status='paid') AS paid, "
                "(SELECT e.event_name FROM events e WHERE e.user_id=a.user_id "
                " ORDER BY e.id DESC LIMIT 1) AS last_event, "
                "(SELECT e.source FROM events e WHERE e.user_id=a.user_id "
                " ORDER BY e.id DESC LIMIT 1) AS last_source, "
                "(SELECT e.created_at FROM events e WHERE e.user_id=a.user_id "
                " ORDER BY e.id DESC LIMIT 1) AS last_event_at "
                "FROM acquisitions a LEFT JOIN users u ON u.user_id=a.user_id "
                "ORDER BY a.created_at DESC, a.id DESC",
            ):
                item = bucket(r["channel"])
                if item is None:
                    continue
                requested = bool(r["requested"])
                generated = bool(r["generated"])
                paid = bool(r["paid"])
                interacted = bool(r["interacted"])
                stage = _seed_user_stage(
                    paid=paid, generated=generated, requested=requested, interacted=interacted
                )
                if stage == "started_only":
                    item["started_only"] += 1
                if interacted:
                    item["interacted_users"] += 1
                if requested:
                    item["requested_users"] += 1
                if generated:
                    item["generated_users"] += 1
                if len(item["recent_users"]) < 8:
                    item["recent_users"].append({
                        "user_id": int(r["user_id"]),
                        "username": r["username"],
                        "first_name": r["first_name"],
                        "stage": stage,
                        "first_seen": r["first_seen"] or r["acquired_at"],
                        "last_active": r["last_active"] or r["last_event_at"] or r["acquired_at"],
                        "last_event": r["last_event"],
                        "last_source": r["last_source"],
                        "last_event_at": r["last_event_at"],
                    })

            channels = []
            for item in by_channel.values():
                # Older rows predate channel_seed_clicked events. Keep their
                # tables visible by treating a cold acquisition as at least one click.
                item["seed_clicks"] = max(int(item["seed_clicks"]), int(item["users"]))
                item["unique_click_users"] = max(
                    int(item["unique_click_users"]), int(item["users"])
                )
                channels.append(item)
            channels.sort(
                key=lambda c: (
                    int(c["users"]),
                    int(c["seed_clicks"]),
                    int(c["revenue_stars"]),
                    float(c["revenue_rub"]),
                ),
                reverse=True,
            )
            total_seed_clicks = sum(int(c["seed_clicks"]) for c in channels)
            return {
                "total_acquired": int(total_acquired),
                "total_seed_clicks": int(total_seed_clicks),
                "channels": channels,
            }
    except Exception:  # noqa: BLE001
        log.warning("report_channels failed", exc_info=True)
        return {"total_acquired": 0, "total_seed_clicks": 0, "channels": []}


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
                    "account_id": r["account_id"],
                    "user_id": r["user_id"],
                }
                for r in _rows(
                    conn,
                    f"SELECT created_at, operation_type, model, error_type, "
                    f"account_id, user_id "
                    f"FROM flow_jobs WHERE status!='success' AND {since} "
                    f"ORDER BY id DESC LIMIT 10",
                    (window,),
                )
            ]
            return {"errors_by_type": errors_by_type, "recent": recent}
    except Exception:  # noqa: BLE001
        log.warning("report_errors failed", exc_info=True)
        return {"errors_by_type": [], "recent": []}


# ── users profile writers ──────────────────────────────────────────────


def has_user_event(user_id: int, event_name: str) -> bool:
    """True if the user already logged at least one event with this name.

    Used for one-shot nudges (e.g. the post-first-generation brand-kit prompt).
    Never raises."""
    try:
        with _LOCK:
            conn = _conn()
            row = conn.execute(
                "SELECT 1 FROM events WHERE user_id=? AND event_name=? LIMIT 1",
                (user_id, event_name),
            ).fetchone()
            return row is not None
    except Exception:  # noqa: BLE001
        log.warning("has_user_event failed user=%r event=%r", user_id, event_name, exc_info=True)
        return False


def user_exists(user_id: int) -> bool:
    """Return True if ``user_id`` already has a row in the ``users`` table.

    Used in ``cmd_start`` to distinguish genuinely new users from returning
    ones after a bot restart (in-memory sets reset on restart, the DB does not).
    Never raises.
    """
    try:
        with _LOCK:
            conn = _conn()
            row = conn.execute(
                "SELECT 1 FROM users WHERE user_id = ? LIMIT 1", (user_id,)
            ).fetchone()
            return row is not None
    except Exception:  # noqa: BLE001
        log.warning("user_exists failed for user_id=%r", user_id, exc_info=True)
        return False


def upsert_user(
    user_id: int,
    *,
    username: str | None = None,
    first_name: str | None = None,
    channel: str | None = None,
) -> None:
    """Insert the user on first sight, else refresh snapshot + last_active.

    Idempotent and safe to call on EVERY interaction (cheap single UPSERT).
    On first insert sets first_seen=last_active=now and acq_channel=channel
    (first-touch). On conflict: updates username/first_name if non-None,
    always bumps last_active and updated_at, and sets acq_channel only if it
    is still NULL (first-touch wins). Never raises.
    """
    try:
        with _LOCK:
            conn = _conn()
            conn.execute(
                """
                INSERT INTO users (user_id, username, first_name, acq_channel)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                  username    = CASE WHEN excluded.username IS NOT NULL
                                     THEN excluded.username
                                     ELSE users.username END,
                  first_name  = CASE WHEN excluded.first_name IS NOT NULL
                                     THEN excluded.first_name
                                     ELSE users.first_name END,
                  acq_channel = CASE WHEN users.acq_channel IS NULL
                                     THEN excluded.acq_channel
                                     ELSE users.acq_channel END,
                  last_active = datetime('now'),
                  updated_at  = datetime('now')
                """,
                (user_id, username, first_name, channel),
            )
            conn.commit()
    except Exception:  # noqa: BLE001
        log.warning("upsert_user failed for user_id=%r", user_id, exc_info=True)


def touch_user(user_id: int) -> None:
    """Bump only last_active=now for an existing user (lighter than upsert).

    No-op if the user row does not exist yet. Never raises.
    """
    try:
        with _LOCK:
            conn = _conn()
            conn.execute(
                "UPDATE users SET last_active=datetime('now'), updated_at=datetime('now') "
                "WHERE user_id=?",
                (user_id,),
            )
            conn.commit()
    except Exception:  # noqa: BLE001
        log.warning("touch_user failed for user_id=%r", user_id, exc_info=True)


def mark_user_blocked(user_id: int, blocked: bool = True) -> None:
    """Set users.is_blocked. Call when a send raises TelegramForbidden.

    Also accepts blocked=False to clear the flag when the user resumes.
    No-op if no row exists. Never raises.
    """
    try:
        flag = 1 if blocked else 0
        with _LOCK:
            conn = _conn()
            conn.execute(
                "UPDATE users SET is_blocked=?, updated_at=datetime('now') WHERE user_id=?",
                (flag, user_id),
            )
            conn.commit()
    except Exception:  # noqa: BLE001
        log.warning("mark_user_blocked failed for user_id=%r", user_id, exc_info=True)


# ── users profile reader ───────────────────────────────────────────────


def get_user_profile(user_id: int) -> dict:
    """Full per-user dossier answering business questions 1-8 for ONE user.

    Returns a dict (zeros/None on missing data, never raises). All queries
    run under a single _LOCK acquisition.
    """
    _empty = {
        "user_id": user_id,
        "username": None,
        "first_name": None,
        "first_seen": None,
        "last_active": None,
        "is_blocked": False,
        "balance": 0,
        "starter_granted": False,
        "acq_channel": None,
        "requests": {"image": 0, "video": 0, "edit": 0, "upscale": 0, "total": 0},
        "spend": {"stars": 0, "rub": 0.0, "payments": 0},
        "referrals": {
            "invited": 0,
            "invited_paid": 0,
            "referred_revenue_rub": 0.0,
            "referred_revenue_stars": 0,
            "reward_credits_earned": 0,
        },
    }
    try:
        with _LOCK:
            conn = _conn()

            # Q1 — user profile row
            row = conn.execute(
                "SELECT user_id, username, first_name, first_seen, last_active, "
                "is_blocked, acq_channel "
                "FROM users WHERE user_id=?",
                (user_id,),
            ).fetchone()

            # Q2 — credit balance + starter grant (Q8)
            credits_row = conn.execute(
                "SELECT balance, granted FROM credits WHERE user_id=?",
                (user_id,),
            ).fetchone()

            # Q3 — requests by type
            req_row = conn.execute(
                """
                SELECT
                  SUM(CASE WHEN operation_type IN ('gen','regen','revary')
                           THEN 1 ELSE 0 END) AS image,
                  SUM(CASE WHEN operation_type LIKE 'video%'
                           THEN 1 ELSE 0 END) AS video,
                  SUM(CASE WHEN operation_type IN ('edit','myphoto')
                           THEN 1 ELSE 0 END) AS edit,
                  SUM(CASE WHEN operation_type IN ('up2x','realup','upscale')
                           THEN 1 ELSE 0 END) AS upscale,
                  COUNT(*) AS total
                FROM flow_jobs
                WHERE user_id=? AND status='success'
                """,
                (user_id,),
            ).fetchone()

            # Q4 — money brought in
            spend_row = conn.execute(
                "SELECT COALESCE(SUM(stars_amount),0) AS stars, "
                "       COALESCE(SUM(amount_rub),0)   AS rub, "
                "       COUNT(*)                      AS payments "
                "FROM transactions WHERE user_id=? AND status='paid'",
                (user_id,),
            ).fetchone()

            # Q5 — referral count (how many this user invited)
            invited_row = conn.execute(
                "SELECT COUNT(*) AS invited FROM referrals WHERE referrer_user_id=?",
                (user_id,),
            ).fetchone()

            # Q6 — revenue from referrals
            ref_rev_row = conn.execute(
                "SELECT COALESCE(SUM(t.amount_rub),0)   AS referred_revenue_rub, "
                "       COALESCE(SUM(t.stars_amount),0) AS referred_revenue_stars, "
                "       COUNT(DISTINCT t.user_id)       AS invited_paid "
                "FROM referrals r "
                "JOIN transactions t "
                "  ON t.user_id = r.referred_user_id AND t.status='paid' "
                "WHERE r.referrer_user_id=?",
                (user_id,),
            ).fetchone()

            # Q7 — acquisition channel (authoritative from acquisitions)
            acq_row = conn.execute(
                "SELECT channel FROM acquisitions WHERE user_id=?",
                (user_id,),
            ).fetchone()

            # Reward credits (milestone + ongoing + first-generation combined)
            reward_m = conn.execute(
                "SELECT COALESCE(SUM(reward_credits),0) FROM referrals "
                "WHERE referrer_user_id=? AND status='rewarded'",
                (user_id,),
            ).fetchone()
            reward_o = conn.execute(
                "SELECT COALESCE(SUM(reward_credits),0) FROM referral_ongoing_rewards "
                "WHERE referrer_user_id=?",
                (user_id,),
            ).fetchone()
            reward_g = conn.execute(
                "SELECT COALESCE(SUM(reward_credits),0) FROM referral_first_generation_rewards "
                "WHERE referrer_user_id=?",
                (user_id,),
            ).fetchone()

        result = dict(_empty)
        if row:
            result["user_id"] = int(row["user_id"])
            result["username"] = row["username"]
            result["first_name"] = row["first_name"]
            result["first_seen"] = row["first_seen"]
            result["last_active"] = row["last_active"]
            result["is_blocked"] = bool(row["is_blocked"])

        if credits_row:
            result["balance"] = int(credits_row["balance"] or 0)
            result["starter_granted"] = bool(credits_row["granted"])

        result["acq_channel"] = (
            acq_row["channel"] if acq_row else (row["acq_channel"] if row else None)
        )

        result["requests"] = {
            "image":   int(req_row["image"]   or 0) if req_row else 0,
            "video":   int(req_row["video"]   or 0) if req_row else 0,
            "edit":    int(req_row["edit"]    or 0) if req_row else 0,
            "upscale": int(req_row["upscale"] or 0) if req_row else 0,
            "total":   int(req_row["total"]   or 0) if req_row else 0,
        }

        result["spend"] = {
            "stars":    int(spend_row["stars"]    or 0)   if spend_row else 0,
            "rub":      float(spend_row["rub"]    or 0.0) if spend_row else 0.0,
            "payments": int(spend_row["payments"] or 0)   if spend_row else 0,
        }

        result["referrals"] = {
            "invited":               int(invited_row["invited"]              or 0) if invited_row else 0,
            "invited_paid":          int(ref_rev_row["invited_paid"]         or 0) if ref_rev_row else 0,
            "referred_revenue_rub":  float(ref_rev_row["referred_revenue_rub"]  or 0.0) if ref_rev_row else 0.0,
            "referred_revenue_stars": int(ref_rev_row["referred_revenue_stars"] or 0)   if ref_rev_row else 0,
            "reward_credits_earned": (
                int(reward_m[0] or 0) + int(reward_o[0] or 0) + int(reward_g[0] or 0)
            ) if reward_m and reward_o and reward_g else 0,
        }
        return result
    except Exception:  # noqa: BLE001
        log.warning("get_user_profile failed for user_id=%r", user_id, exc_info=True)
        return _empty


# ── operator rollup reports (new) ─────────────────────────────────────


def report_active_users(now: bool = False) -> dict:  # noqa: ARG001 — `now` reserved
    """DAU/WAU/MAU from users.last_active (local-day windows).

    Returns {"dau": int, "wau": int, "mau": int}. Counts only is_blocked=0.
    Never raises; returns zeros on error.
    """
    try:
        with _LOCK:
            conn = _conn()
            row = conn.execute(
                """
                SELECT
                  SUM(CASE WHEN date(last_active,'localtime') = date('now','localtime')
                           THEN 1 ELSE 0 END) AS dau,
                  SUM(CASE WHEN last_active >= datetime('now','-7 days')
                           THEN 1 ELSE 0 END) AS wau,
                  SUM(CASE WHEN last_active >= datetime('now','-30 days')
                           THEN 1 ELSE 0 END) AS mau
                FROM users WHERE is_blocked = 0
                """,
            ).fetchone()
            if row is None:
                return {"dau": 0, "wau": 0, "mau": 0}
            return {
                "dau": int(row["dau"] or 0),
                "wau": int(row["wau"] or 0),
                "mau": int(row["mau"] or 0),
            }
    except Exception:  # noqa: BLE001
        log.warning("report_active_users failed", exc_info=True)
        return {"dau": 0, "wau": 0, "mau": 0}


def report_top_users(limit: int = 20, days: int | None = None) -> dict:
    """Top users by RUB spend, optionally restricted to the last ``days``.

    Returns {"users": [...]} ordered by rub DESC. Never raises; empty list on error.
    """
    try:
        params: tuple
        if days is not None:
            window = f"-{max(0, int(days))} days"
            date_filter = "AND t.created_at >= datetime('now', ?)"
            params = ("paid", window, int(limit))
        else:
            date_filter = ""
            params = ("paid", int(limit))

        sql = (
            "SELECT t.user_id, u.username, "
            "       COALESCE(SUM(t.amount_rub),0)   AS rub, "
            "       COALESCE(SUM(t.stars_amount),0) AS stars, "
            "       COUNT(*)                        AS payments "
            "FROM transactions t "
            "LEFT JOIN users u ON u.user_id = t.user_id "
            f"WHERE t.status=? {date_filter} "
            "GROUP BY t.user_id ORDER BY rub DESC LIMIT ?"
        )
        with _LOCK:
            conn = _conn()
            rows = _rows(conn, sql, params)
        return {
            "users": [
                {
                    "user_id":  int(r["user_id"]),
                    "username": r["username"],
                    "rub":      float(r["rub"] or 0.0),
                    "stars":    int(r["stars"] or 0),
                    "payments": int(r["payments"] or 0),
                }
                for r in rows
            ]
        }
    except Exception:  # noqa: BLE001
        log.warning("report_top_users failed", exc_info=True)
        return {"users": []}


def report_top_referrers(limit: int = 20) -> dict:
    """Top referrers by referred-revenue (RUB their invitees brought in).

    Returns {"referrers": [...]} ordered by referred_revenue_rub DESC.
    Never raises; empty list on error.
    """
    try:
        sql = (
            "SELECT r.referrer_user_id, u.username, "
            "       COUNT(DISTINCT r.referred_user_id) AS invited, "
            "       COALESCE(SUM(CASE WHEN t.status='paid' THEN t.amount_rub END),0) "
            "           AS referred_revenue_rub, "
            "       COALESCE(("
            "           SELECT SUM(rl.reward_credits) FROM referrals rl "
            "           WHERE rl.referrer_user_id = r.referrer_user_id AND rl.status='rewarded'"
            "       ),0) + COALESCE(("
            "           SELECT SUM(ror.reward_credits) FROM referral_ongoing_rewards ror "
            "           WHERE ror.referrer_user_id = r.referrer_user_id"
            "       ),0) + COALESCE(("
            "           SELECT SUM(rfg.reward_credits) FROM referral_first_generation_rewards rfg "
            "           WHERE rfg.referrer_user_id = r.referrer_user_id"
            "       ),0) AS reward_credits_earned "
            "FROM referrals r "
            "LEFT JOIN transactions t ON t.user_id = r.referred_user_id "
            "LEFT JOIN users u ON u.user_id = r.referrer_user_id "
            "GROUP BY r.referrer_user_id "
            "ORDER BY referred_revenue_rub DESC LIMIT ?"
        )
        with _LOCK:
            conn = _conn()
            rows = _rows(conn, sql, (int(limit),))
        return {
            "referrers": [
                {
                    "referrer_user_id":    int(r["referrer_user_id"]),
                    "username":            r["username"],
                    "invited":             int(r["invited"] or 0),
                    "referred_revenue_rub": float(r["referred_revenue_rub"] or 0.0),
                    "reward_credits_earned": int(r["reward_credits_earned"] or 0),
                }
                for r in rows
            ]
        }
    except Exception:  # noqa: BLE001
        log.warning("report_top_referrers failed", exc_info=True)
        return {"referrers": []}


def backfill_users_from_metrics() -> int:
    """One-time idempotent backfill of the users table from existing tables.

    For every distinct user_id seen in credits / transactions / acquisitions,
    INSERT OR IGNORE a users row with best-effort first_seen (MIN created_at)
    and the latest username from events. acq_channel is filled from acquisitions.
    Safe to run repeatedly (INSERT OR IGNORE never overwrites a live row).
    Returns the count of rows inserted. Never raises; returns 0 on error.
    """
    try:
        with _LOCK:
            conn = _conn()
            # Collect all known user_ids from financial/credit tables.
            all_users_sql = (
                "SELECT DISTINCT user_id FROM credits WHERE user_id IS NOT NULL "
                "UNION "
                "SELECT DISTINCT user_id FROM transactions WHERE user_id IS NOT NULL "
                "UNION "
                "SELECT DISTINCT user_id FROM acquisitions WHERE user_id IS NOT NULL"
            )
            user_ids = [r[0] for r in conn.execute(all_users_sql).fetchall()]

            count = 0
            for uid in user_ids:
                # Best-effort first_seen: MIN(created_at) across tables.
                # credits table has updated_at, not created_at, so we skip it.
                candidates = []
                for tbl, col in (("transactions", "created_at"), ("acquisitions", "created_at")):
                    r = conn.execute(
                        f"SELECT MIN({col}) FROM {tbl} WHERE user_id=?", (uid,)
                    ).fetchone()
                    if r and r[0]:
                        candidates.append(r[0])
                # Also check events table for first_seen.
                r = conn.execute(
                    "SELECT MIN(created_at) FROM events WHERE user_id=?", (uid,)
                ).fetchone()
                if r and r[0]:
                    candidates.append(r[0])

                first_seen = min(candidates) if candidates else None

                # Latest username from events.
                u_row = conn.execute(
                    "SELECT username FROM events WHERE user_id=? AND username IS NOT NULL "
                    "ORDER BY id DESC LIMIT 1",
                    (uid,),
                ).fetchone()
                username = u_row[0] if u_row else None

                # Channel from acquisitions (first-touch).
                a_row = conn.execute(
                    "SELECT channel FROM acquisitions WHERE user_id=?", (uid,)
                ).fetchone()
                channel = a_row[0] if a_row else None

                if first_seen:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO users "
                        "(user_id, username, acq_channel, first_seen, last_active, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (uid, username, channel, first_seen, first_seen, first_seen),
                    )
                else:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO users (user_id, username, acq_channel) "
                        "VALUES (?, ?, ?)",
                        (uid, username, channel),
                    )
                count += cur.rowcount

            conn.commit()
            return count
    except Exception:  # noqa: BLE001
        log.warning("backfill_users_from_metrics failed", exc_info=True)
        return 0


# ── admin panel convenience helpers ────────────────────────────────────────────

def report_admin_stats() -> dict:
    """Summary KPIs for the admin Overview tab.

    Returns ``{users, gens_today, credits_sold}`` — the three numbers shown in
    the header stats-grid of admin.html.  Falls back to zeros on any error.
    """
    try:
        with _LOCK:
            conn = _conn()
            ev_today = "date(created_at,'localtime') = date('now','localtime')"
            tx_today = "date(created_at,'localtime') = date('now','localtime')"

            total_users = _scalar(conn, "SELECT COUNT(*) FROM users") or 0
            gens_today = (
                (_scalar(conn, f"SELECT COUNT(*) FROM events WHERE event_name='image_success' AND {ev_today}") or 0)
                + (_scalar(conn, f"SELECT COUNT(*) FROM events WHERE event_name='video_success' AND {ev_today}") or 0)
            )
            credits_sold = _scalar(
                conn,
                f"SELECT COALESCE(SUM(bot_credits_charged),0) FROM flow_jobs WHERE {tx_today}",
            ) or 0

            # Today's finished-job split by media type (image vs video) and
            # outcome, sourced from flow_jobs (the single source of truth for
            # what actually ran). Video ops are operation_type LIKE 'video%';
            # everything else (image, edit, upscale, variations, enhance) is
            # image-side.
            fj_today = "date(created_at,'localtime') = date('now','localtime')"

            def _job_count(media_sql: str, status_sql: str) -> int:
                return _scalar(
                    conn,
                    f"SELECT COUNT(*) FROM flow_jobs "
                    f"WHERE {fj_today} AND {media_sql} AND {status_sql}",
                ) or 0

            _img = "operation_type NOT LIKE 'video%'"
            _vid = "operation_type LIKE 'video%'"
            _ok = "status='success'"
            _bad = "status IN ('fail','error')"
            image_success = _job_count(_img, _ok)
            video_success = _job_count(_vid, _ok)
            image_fail = _job_count(_img, _bad)
            video_fail = _job_count(_vid, _bad)

            revenue_today_rub = _scalar(
                conn,
                f"SELECT COALESCE(SUM(amount_rub),0) FROM transactions "
                f"WHERE status='paid' AND {tx_today}",
            ) or 0
            revenue_total_rub = _scalar(
                conn,
                "SELECT COALESCE(SUM(amount_rub),0) FROM transactions WHERE status='paid'",
            ) or 0

        return {
            "users": int(total_users),
            "gens_today": int(gens_today),
            "credits_sold": int(credits_sold),
            "revenue_today_rub": float(revenue_today_rub),
            "revenue_total_rub": float(revenue_total_rub),
            "image_success": int(image_success),
            "video_success": int(video_success),
            "image_fail": int(image_fail),
            "video_fail": int(video_fail),
        }
    except Exception:  # noqa: BLE001
        log.warning("report_admin_stats failed", exc_info=True)
        return {"users": 0, "gens_today": 0, "credits_sold": 0,
                "image_success": 0, "video_success": 0,
                "image_fail": 0, "video_fail": 0}


def report_landing_hero_experiment() -> dict:
    """Aggregate the public landing hero A/B test by assigned variant."""
    try:
        with _LOCK:
            conn = _conn()
            rows = _rows(
                conn,
                """
                SELECT COALESCE(json_extract(payload_json, '$.variant'), '?') AS variant,
                       SUM(CASE WHEN event_name='landing_hero_exposure' THEN 1 ELSE 0 END) AS exposures,
                       SUM(CASE WHEN event_name='landing_hero_cta' THEN 1 ELSE 0 END) AS ctas,
                       MIN(created_at) AS first_seen,
                       MAX(created_at) AS last_seen
                FROM events
                WHERE event_name IN ('landing_hero_exposure', 'landing_hero_cta')
                GROUP BY COALESCE(json_extract(payload_json, '$.variant'), '?')
                ORDER BY variant
                """,
            )

        variants = []
        total_exposures = 0
        total_ctas = 0
        for r in rows:
            exposures = int(r["exposures"] or 0)
            ctas = int(r["ctas"] or 0)
            total_exposures += exposures
            total_ctas += ctas
            variants.append({
                "variant": str(r["variant"] or "?"),
                "exposures": exposures,
                "ctas": ctas,
                "ctr": round((ctas / exposures) * 100, 2) if exposures else None,
                "first_seen": r["first_seen"],
                "last_seen": r["last_seen"],
            })

        return {
            "experiment": "landing_hero",
            "total_exposures": total_exposures,
            "total_ctas": total_ctas,
            "ctr": round((total_ctas / total_exposures) * 100, 2) if total_exposures else None,
            "variants": variants,
        }
    except Exception:  # noqa: BLE001
        log.warning("report_landing_hero_experiment failed", exc_info=True)
        return {
            "experiment": "landing_hero",
            "total_exposures": 0,
            "total_ctas": 0,
            "ctr": None,
            "variants": [],
        }


def report_recent_events(limit: int = 50) -> list:
    """Recent events for the admin Overview log panel.

    Sourced from flow_jobs (has account_id) joined with latest username from
    events.  Returns ``{time, created_at, text, chip, color, account, source, kind}`` dicts.
    Newest first.  Falls back to ``[]`` on any error.
    """
    _OP_CHIP = {
        "image":              ("🖼",  "image"),
        "image_edit":         ("🖼",  "image"),
        "image_upscale":      ("🖼",  "image"),
        "video":              ("🎬",  "video"),
        "video_ingredients":  ("🎬",  "video"),
        "video_frames":       ("🎬",  "video"),
        "video_text":         ("🎬",  "video"),
        "video_extend":       ("🎬",  "video"),
        "video_edit":         ("🎬",  "video"),
    }
    def _identity_label(
        uid: int | str | None,
        username: str | None,
        first_name: str | None = None,
        platform: str | None = None,
        platform_user_id: str | None = None,
    ) -> str:
        username = str(username or "").strip()
        if username:
            return f"@{username}"
        name = str(first_name or "").strip()
        if name:
            return name[:48]
        platform_value = str(platform or "").strip().lower()
        platform_id = str(platform_user_id or "").strip()
        if platform_value and platform_value != "telegram":
            label = {"max": "MAX", "yandex": "Yandex ID"}.get(platform_value, platform_value)
            return f"{label}:{platform_id or uid}"
        return f"#{uid}" if uid else "—"

    def _source_label(platform: str | None = None, fallback: str | None = None) -> str:
        value = str(platform or "").strip().lower()
        if value:
            return {
                "telegram": "Telegram",
                "max": "MAX",
                "yandex": "Yandex ID",
                "web_yandex": "Yandex ID",
                "web": "web",
            }.get(value, value)
        fallback_value = str(fallback or "").strip()
        return fallback_value or "—"

    try:
        with _LOCK:
            conn = _conn()
            rows = _rows(
                conn,
                """
                SELECT fj.account_id,
                       fj.operation_type,
                       fj.model,
                       fj.status,
                       fj.bot_credits_charged,
                       fj.duration_ms,
                       fj.created_at,
                       fj.user_id,
                       (SELECT e.username FROM events e
                        WHERE e.user_id = fj.user_id AND e.username IS NOT NULL
                        ORDER BY e.id DESC LIMIT 1) AS username,
                       u.first_name,
                       (SELECT ui.platform FROM user_identities ui
                        WHERE ui.internal_user_id=fj.user_id LIMIT 1) AS platform,
                       (SELECT ui.platform_user_id FROM user_identities ui
                        WHERE ui.internal_user_id=fj.user_id LIMIT 1) AS platform_user_id
                FROM flow_jobs fj
                LEFT JOIN users u ON u.user_id=fj.user_id
                ORDER BY fj.id DESC LIMIT ?
                """,
                (int(limit),),
            )
            # Системные события из таблицы events (фейловер, новый юзер, кулдаун, оплата)
            system_event_rows = _rows(
                conn,
                """
                SELECT e.event_name, e.user_id, e.username, e.source, e.payload_json,
                       e.created_at, u.first_name,
                       (SELECT ui.platform FROM user_identities ui
                        WHERE ui.internal_user_id=e.user_id LIMIT 1) AS platform,
                       (SELECT ui.platform_user_id FROM user_identities ui
                        WHERE ui.internal_user_id=e.user_id LIMIT 1) AS platform_user_id
                FROM events e
                LEFT JOIN users u ON u.user_id=e.user_id
                WHERE e.event_name IN (
                    'gen_failover', 'user_started', 'new_user',
                    'account_cooldown', 'payment_success',
                    'video_ab', 'web_generation_success', 'prompt_improve',
                    'landing_hero_exposure', 'landing_hero_cta'
                )
                ORDER BY e.id DESC LIMIT ?
                """,
                (int(limit),),
            )

        result = []
        for r in rows:
            op   = r["operation_type"] or ""
            icon, kind = _OP_CHIP.get(op, ("⚙", "op"))
            status = r["status"] or ""
            if status == "success":
                chip_label = f"{icon} ok"
                color = "lime"
            elif status in ("fail", "error"):
                chip_label = f"{icon} fail"
                color = "coral"
            else:
                chip_label = f"{icon} {status[:4]}"
                color = "muted"

            user_label = _identity_label(
                r["user_id"] or "", r["username"], r["first_name"], r["platform"], r["platform_user_id"]
            )
            acc = r["account_id"] or "?"
            model = r["model"] or ""
            dur_s = f"{r['duration_ms'] / 1000:.1f}s" if r["duration_ms"] else ""
            credits = r["bot_credits_charged"]
            cost_s = f"{credits}кр" if credits else ""
            text = f"{user_label}  {op}"
            if model:
                text += f"  [{model}]"
            parts = [p for p in [dur_s, cost_s] if p]
            if parts:
                text += "  " + " · ".join(parts)

            ts = str(r["created_at"] or "")
            time_str = ts[11:16] if len(ts) >= 16 else ts
            result.append({
                "time":     time_str,
                "created_at": ts,
                "text":     text,
                "chip":     chip_label,
                "color":    color,
                "account":  acc,
                "source":   _source_label(r["platform"], "Telegram" if r["user_id"] else None),
                "kind":     kind,
                "_sort_ts": ts,
            })

        import json as _json
        new_user_seen: set[int] = set()
        for fr in system_event_rows:
            try:
                payload = _json.loads(fr["payload_json"] or "{}")
            except Exception:
                payload = {}
            username = fr["username"] or ""
            uid = fr["user_id"] or ""
            user_label = f"@{username}" if username else f"#{uid}" if uid else "—"
            user_label = _identity_label(
                uid, fr["username"], fr["first_name"], fr["platform"], fr["platform_user_id"]
            )
            ts = str(fr["created_at"] or "")
            time_str = ts[11:16] if len(ts) >= 16 else ts
            ev = fr["event_name"]

            if ev == "gen_failover":
                from_acc = payload.get("from_account", "?")
                reason = payload.get("reason", "")[:40]
                text = f"{user_label}  → фейловер с {from_acc}"
                if reason:
                    text += f"  ({reason})"
                result.append({
                    "time": time_str, "created_at": ts, "text": text,
                    "chip": "⚠ failover", "color": "warn",
                    "account": from_acc, "source": "failover", "kind": "system", "_sort_ts": ts,
                })

            elif ev in ("user_started", "new_user"):
                is_new = (ev == "new_user") or payload.get("is_new", False)
                if is_new:
                    user_key = fr["user_id"]
                    if ev == "user_started" and user_key in new_user_seen:
                        continue
                    if user_key is not None:
                        new_user_seen.add(user_key)
                    channel = fr["source"] or payload.get("seed_channel") or payload.get("channel") or ""
                    text = f"new user  {user_label}"
                    if channel and channel not in ("command", "organic"):
                        text += f"  seed:{channel}"
                    result.append({
                        "time": time_str, "created_at": ts, "text": text,
                        "chip": "user new", "color": "cyan",
                        "account": user_label, "source": channel or "organic", "kind": "new", "_sort_ts": ts,
                    })

            elif ev == "account_cooldown":
                acc = payload.get("account", "?")
                reason = payload.get("reason", "")[:30]
                op = payload.get("op", "")
                text = f"❄️ кулдаун  {acc}  ({op})"
                if reason:
                    text += f"  · {reason}"
                result.append({
                    "time": time_str, "created_at": ts, "text": text,
                    "chip": "❄ cooldown", "color": "coral",
                    "account": acc, "source": op or "cooldown", "kind": "system", "_sort_ts": ts,
                })

            elif ev == "payment_success":
                credits = payload.get("credits", "?")
                source = fr["source"] or payload.get("source", "")
                stars = payload.get("stars")
                rub = payload.get("amount_rub")
                amount_str = f"{stars}⭐" if stars else (f"{rub}₽" if rub else "")
                text = f"💳 пополнение  {user_label}  +{credits}кр"
                if amount_str:
                    text += f"  ({amount_str})"
                result.append({
                    "time": time_str, "created_at": ts, "text": text,
                    "chip": "💳 topup", "color": "lime",
                    "account": user_label, "source": source or "payment", "kind": "topup", "_sort_ts": ts,
                })

            elif ev == "video_ab":
                acc = fr["source"] or payload.get("account", "?")
                model = payload.get("model") or payload.get("model_key") or "?"
                statuses = payload.get("statuses") if isinstance(payload.get("statuses"), dict) else {}
                status_bits = ", ".join(f"{k}:{v}" for k, v in statuses.items())[:60]
                text = f"🎬 video A/B  {acc}  [{model}]"
                if status_bits:
                    text += f"  {status_bits}"
                result.append({
                    "time": time_str, "created_at": ts, "text": text,
                    "chip": "🎬 video_ab", "color": "cyan",
                    "account": acc, "source": model, "kind": "video", "_sort_ts": ts,
                })

            elif ev in ("web_generation_success", "prompt_improve"):
                mode = str(payload.get("mode") or ("prompt" if ev == "prompt_improve" else "web"))
                model = str(payload.get("model") or "")
                price = payload.get("price")
                count = payload.get("count")
                icon, kind = _OP_CHIP.get(mode, ("✦", "web"))
                text = f"{user_label}  {mode}"
                if model:
                    text += f"  [{model}]"
                details = []
                if count:
                    details.append(f"x{count}")
                if price:
                    details.append(f"{price}кр")
                if details:
                    text += "  " + " · ".join(details)
                result.append({
                    "time": time_str, "created_at": ts, "text": text,
                    "chip": f"{icon} web", "color": "lime",
                    "account": user_label, "source": fr["source"] or "web", "kind": kind, "_sort_ts": ts,
                })

            elif ev in ("landing_hero_exposure", "landing_hero_cta"):
                variant = str(payload.get("variant") or "?")[:8]
                action = "cta" if ev == "landing_hero_cta" else "exposure"
                result.append({
                    "time": time_str,
                    "created_at": ts,
                    "text": f"landing hero {action}",
                    "chip": "🧪 hero",
                    "color": "lime" if action == "cta" else "cyan",
                    "account": "web",
                    "source": variant,
                    "kind": "system",
                    "_sort_ts": ts,
                })

        # Сортируем по времени (новейшие первыми), обрезаем до limit
        result.sort(key=lambda x: x.get("_sort_ts", ""), reverse=True)
        for item in result:
            item.pop("_sort_ts", None)
        return result[:limit]
    except Exception:  # noqa: BLE001
        log.warning("report_recent_events failed", exc_info=True)
        return []


def report_account_stats() -> dict:
    """Per-account job counters for the admin Overview health panel.

    Returns ``{account_id: {total, success, fail}}`` covering all time.
    Falls back to ``{}`` on any error.
    """
    try:
        with _LOCK:
            conn = _conn()
            rows = _rows(
                conn,
                """
                SELECT account_id,
                       COUNT(*) AS total,
                       SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success,
                       SUM(CASE WHEN status IN ('fail','error') THEN 1 ELSE 0 END) AS fail,
                       MAX(created_at) AS last_activity,
                       (
                         SELECT fj2.error_type FROM flow_jobs fj2
                         WHERE fj2.account_id = flow_jobs.account_id
                           AND fj2.status IN ('fail','error')
                           AND fj2.error_type IS NOT NULL
                         ORDER BY fj2.id DESC LIMIT 1
                       ) AS last_error
                FROM flow_jobs
                WHERE account_id IS NOT NULL
                GROUP BY account_id
                """,
            )
        return {
            r["account_id"]: {
                "total":   int(r["total"]   or 0),
                "success": int(r["success"] or 0),
                "fail":    int(r["fail"]    or 0),
                "last_activity": r["last_activity"],
                "last_error": r["last_error"],
            }
            for r in rows
        }
    except Exception:  # noqa: BLE001
        log.warning("report_account_stats failed", exc_info=True)
        return {}


def report_ops_health() -> dict:
    """Operator health snapshot from local telemetry only.

    No live provider/bot calls are made here; this report answers what the bot
    has observed recently from ``flow_jobs``/``support_tickets``.
    """
    def _window(conn: sqlite3.Connection, modifier: str) -> dict:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success,
                   SUM(CASE WHEN status!='success' THEN 1 ELSE 0 END) AS fail
            FROM flow_jobs
            WHERE created_at >= datetime('now', ?)
            """,
            (modifier,),
        ).fetchone()
        total = int(row["total"] or 0) if row else 0
        success = int(row["success"] or 0) if row else 0
        fail = int(row["fail"] or 0) if row else 0
        return {
            "total": total,
            "success": success,
            "fail": fail,
            "success_rate": (success / total) if total else None,
            "fail_rate": (fail / total) if total else None,
        }

    try:
        with _LOCK:
            conn = _conn()
            jobs_1h = _window(conn, "-1 hour")
            jobs_24h = _window(conn, "-24 hours")
            critical_errors = [
                {
                    "created_at": r["created_at"],
                    "account_id": r["account_id"],
                    "operation_type": r["operation_type"],
                    "model": r["model"],
                    "error_type": r["error_type"] or r["status"],
                    "user_id": r["user_id"],
                }
                for r in _rows(
                    conn,
                    """
                    SELECT created_at, account_id, operation_type, model,
                           error_type, status, user_id
                    FROM flow_jobs
                    WHERE status!='success'
                    ORDER BY id DESC LIMIT 8
                    """,
                )
            ]
            last_jobs = [
                {
                    "id": int(r["id"]),
                    "created_at": r["created_at"],
                    "account_id": r["account_id"],
                    "operation_type": r["operation_type"],
                    "model": r["model"],
                    "status": r["status"],
                    "error_type": r["error_type"],
                    "duration_ms": r["duration_ms"],
                    "user_id": r["user_id"],
                }
                for r in _rows(
                    conn,
                    """
                    SELECT id, created_at, account_id, operation_type, model,
                           status, error_type, duration_ms, user_id
                    FROM flow_jobs
                    ORDER BY id DESC LIMIT 12
                    """,
                )
            ]
            open_tickets = _scalar(
                conn, "SELECT COUNT(*) FROM support_tickets WHERE status='open'"
            ) or 0
        return {
            "jobs_1h": jobs_1h,
            "jobs_24h": jobs_24h,
            "critical_errors": critical_errors,
            "last_jobs": last_jobs,
            "open_tickets": int(open_tickets),
        }
    except Exception:  # noqa: BLE001
        log.warning("report_ops_health failed", exc_info=True)
        return {
            "jobs_1h": {"total": 0, "success": 0, "fail": 0, "success_rate": None, "fail_rate": None},
            "jobs_24h": {"total": 0, "success": 0, "fail": 0, "success_rate": None, "fail_rate": None},
            "critical_errors": [],
            "last_jobs": [],
            "open_tickets": 0,
        }


# ── gallery ────────────────────────────────────────────────────────────


def save_to_gallery(
    user_id: int,
    file_id: str,
    *,
    token: str | None = None,
    prompt: str | None = None,
) -> None:
    """Persist an image file_id to the user's personal gallery (last 20 kept)."""
    try:
        with _LOCK:
            conn = _conn()
            conn.execute(
                "INSERT INTO user_gallery (user_id, file_id, token, prompt) VALUES (?,?,?,?)",
                (user_id, file_id, token, (prompt or "")[:400]),
            )
            # Prune to last 20 entries for this user.
            conn.execute(
                "DELETE FROM user_gallery WHERE user_id=? AND id NOT IN "
                "(SELECT id FROM user_gallery WHERE user_id=? ORDER BY id DESC LIMIT 20)",
                (user_id, user_id),
            )
            conn.commit()
    except Exception:  # noqa: BLE001
        log.warning("save_to_gallery failed for user_id=%r", user_id, exc_info=True)


def get_gallery(user_id: int, limit: int = 20) -> list:
    """Return last ``limit`` gallery entries for a user; newest first."""
    try:
        with _LOCK:
            conn = _conn()
            return [
                {
                    "file_id":    r["file_id"],
                    "token":      r["token"],
                    "prompt":     r["prompt"],
                    "created_at": r["created_at"],
                }
                for r in _rows(
                    conn,
                    "SELECT file_id, token, prompt, created_at "
                    "FROM user_gallery WHERE user_id=? ORDER BY id DESC LIMIT ?",
                    (user_id, int(limit)),
                )
            ]
    except Exception:  # noqa: BLE001
        log.warning("get_gallery failed for user_id=%r", user_id, exc_info=True)
        return []


# ── seller SKU projects ───────────────────────────────────────────────


def _normalize_sku(sku: str) -> str:
    """Small user-facing SKU label normalization, not a secret transform."""
    return " ".join((sku or "").strip().split())[:80]


def create_seller_sku_project(user_id: int, sku: str, *, platform: str | None = None) -> bool:
    """Create or refresh an empty seller SKU project."""
    sku_norm = _normalize_sku(sku)
    if not sku_norm:
        return False
    try:
        with _LOCK:
            conn = _conn()
            conn.execute(
                """
                INSERT INTO seller_sku_projects (user_id, sku, platform)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, sku) DO UPDATE SET
                    platform=COALESCE(excluded.platform, seller_sku_projects.platform),
                    updated_at=datetime('now')
                """,
                (int(user_id), sku_norm, (platform or "")[:40] or None),
            )
            conn.commit()
            return True
    except Exception:  # noqa: BLE001
        log.warning("create_seller_sku_project failed for user_id=%r", user_id, exc_info=True)
        return False


def get_seller_sku_project(user_id: int, sku: str) -> dict | None:
    """Return one grouped seller SKU project by name."""
    sku_norm = _normalize_sku(sku)
    if not sku_norm:
        return None
    try:
        with _LOCK:
            conn = _conn()
            rows = _seller_sku_project_rows(conn, int(user_id), limit=200, sku=sku_norm)
            return rows[0] if rows else None
    except Exception:  # noqa: BLE001
        log.warning("get_seller_sku_project failed for user_id=%r", user_id, exc_info=True)
        return None


def rename_seller_sku_project(user_id: int, old_sku: str, new_sku: str) -> bool:
    """Rename a seller SKU project and all saved slide rows."""
    old_norm = _normalize_sku(old_sku)
    new_norm = _normalize_sku(new_sku)
    if not old_norm or not new_norm:
        return False
    if old_norm == new_norm:
        return True
    try:
        with _LOCK:
            conn = _conn()
            exists = _scalar(
                conn,
                """
                SELECT 1 FROM seller_sku_projects WHERE user_id=? AND sku=?
                UNION
                SELECT 1 FROM seller_sku_items WHERE user_id=? AND sku=?
                LIMIT 1
                """,
                (int(user_id), old_norm, int(user_id), old_norm),
            )
            if not exists:
                return False
            old_project = conn.execute(
                "SELECT platform FROM seller_sku_projects WHERE user_id=? AND sku=?",
                (int(user_id), old_norm),
            ).fetchone()
            old_platform = old_project["platform"] if old_project else None
            conn.execute(
                """
                INSERT INTO seller_sku_projects (user_id, sku, platform)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, sku) DO UPDATE SET
                    platform=COALESCE(seller_sku_projects.platform, excluded.platform),
                    updated_at=datetime('now')
                """,
                (int(user_id), new_norm, old_platform),
            )
            conn.execute(
                "UPDATE seller_sku_items SET sku=? WHERE user_id=? AND sku=?",
                (new_norm, int(user_id), old_norm),
            )
            conn.execute(
                "DELETE FROM seller_sku_projects WHERE user_id=? AND sku=?",
                (int(user_id), old_norm),
            )
            conn.commit()
            return True
    except Exception:  # noqa: BLE001
        log.warning("rename_seller_sku_project failed for user_id=%r", user_id, exc_info=True)
        return False


def delete_seller_sku_project(user_id: int, sku: str) -> int:
    """Delete a seller SKU project and its saved slide rows; returns deleted rows."""
    sku_norm = _normalize_sku(sku)
    if not sku_norm:
        return 0
    try:
        with _LOCK:
            conn = _conn()
            cur_items = conn.execute(
                "DELETE FROM seller_sku_items WHERE user_id=? AND sku=?",
                (int(user_id), sku_norm),
            )
            cur_project = conn.execute(
                "DELETE FROM seller_sku_projects WHERE user_id=? AND sku=?",
                (int(user_id), sku_norm),
            )
            conn.commit()
            return max(0, int(cur_items.rowcount or 0)) + max(0, int(cur_project.rowcount or 0))
    except Exception:  # noqa: BLE001
        log.warning("delete_seller_sku_project failed for user_id=%r", user_id, exc_info=True)
        return 0


def save_seller_sku_item(
    user_id: int,
    sku: str,
    *,
    file_id: str,
    token: str | None = None,
    prompt: str | None = None,
    platform: str | None = None,
) -> int:
    """Persist one generated result under a seller SKU; returns row id or 0."""
    sku_norm = _normalize_sku(sku)
    if not sku_norm or not file_id:
        return 0
    try:
        with _LOCK:
            conn = _conn()
            conn.execute(
                """
                INSERT INTO seller_sku_projects (user_id, sku, platform)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, sku) DO UPDATE SET
                    platform=COALESCE(excluded.platform, seller_sku_projects.platform),
                    updated_at=datetime('now')
                """,
                (int(user_id), sku_norm, (platform or "")[:40] or None),
            )
            cur = conn.execute(
                "INSERT INTO seller_sku_items "
                "(user_id, sku, platform, file_id, token, prompt) VALUES (?,?,?,?,?,?)",
                (
                    int(user_id), sku_norm, (platform or "")[:40], file_id,
                    (token or "")[:80], (prompt or "")[:400],
                ),
            )
            # Keep the most recent 80 generated assets per SKU to bound DB growth.
            conn.execute(
                "DELETE FROM seller_sku_items WHERE user_id=? AND sku=? AND id NOT IN "
                "(SELECT id FROM seller_sku_items WHERE user_id=? AND sku=? ORDER BY id DESC LIMIT 80)",
                (int(user_id), sku_norm, int(user_id), sku_norm),
            )
            conn.commit()
            return cur.lastrowid or 0
    except Exception:  # noqa: BLE001
        log.warning("save_seller_sku_item failed for user_id=%r", user_id, exc_info=True)
        return 0


def _seller_sku_project_rows(
    conn: sqlite3.Connection, user_id: int, *, limit: int, sku: str | None = None
) -> list[dict]:
    where = "WHERE n.user_id=?"
    params: list[object] = [int(user_id), int(user_id), int(user_id)]
    if sku:
        where += " AND n.sku=?"
        params.append(sku)
    params.append(max(1, int(limit)))
    rows = _rows(
        conn,
        f"""
        WITH names AS (
            SELECT user_id, sku FROM seller_sku_projects WHERE user_id=?
            UNION
            SELECT user_id, sku FROM seller_sku_items WHERE user_id=?
        )
        SELECT n.sku,
               COUNT(i.id) AS items,
               COALESCE(MAX(i.created_at), p.updated_at) AS updated_at,
               (SELECT file_id FROM seller_sku_items last
                WHERE last.user_id=n.user_id AND last.sku=n.sku
                ORDER BY last.id DESC LIMIT 1) AS latest_file_id,
               (SELECT prompt FROM seller_sku_items last
                WHERE last.user_id=n.user_id AND last.sku=n.sku
                ORDER BY last.id DESC LIMIT 1) AS latest_prompt,
               COALESCE(
                   (SELECT platform FROM seller_sku_items last
                    WHERE last.user_id=n.user_id AND last.sku=n.sku
                    ORDER BY last.id DESC LIMIT 1),
                   p.platform
               ) AS platform,
               COALESCE(MAX(i.id), 0) AS sort_item_id
        FROM names n
        LEFT JOIN seller_sku_projects p ON p.user_id=n.user_id AND p.sku=n.sku
        LEFT JOIN seller_sku_items i ON i.user_id=n.user_id AND i.sku=n.sku
        {where}
        GROUP BY n.sku
        ORDER BY sort_item_id DESC, p.updated_at DESC, n.sku COLLATE NOCASE ASC
        LIMIT ?
        """,
        tuple(params),
    )
    return [
        {
            "sku": r["sku"],
            "items": int(r["items"] or 0),
            "updated_at": r["updated_at"],
            "latest_file_id": r["latest_file_id"],
            "latest_prompt": r["latest_prompt"],
            "platform": r["platform"],
        }
        for r in rows
    ]


def list_seller_sku_projects(user_id: int, limit: int = 20) -> list[dict]:
    """Return grouped seller SKU projects, newest activity first."""
    try:
        with _LOCK:
            conn = _conn()
            return _seller_sku_project_rows(conn, int(user_id), limit=max(1, int(limit)))
    except Exception:  # noqa: BLE001
        log.warning("list_seller_sku_projects failed for user_id=%r", user_id, exc_info=True)
        return []


def recent_seller_skus(user_id: int, limit: int = 5) -> list[str]:
    """Return recent SKU names for quick inline selection."""
    try:
        projects = list_seller_sku_projects(user_id, limit=max(1, int(limit)))
        return [p["sku"] for p in projects if p.get("sku")]
    except Exception:  # noqa: BLE001
        log.warning("recent_seller_skus failed for user_id=%r", user_id, exc_info=True)
        return []


def save_seller_profile(
    user_id: int,
    *,
    brand_kit: str | None = None,
    niche: str | None = None,
    sku_volume: str | None = None,
) -> bool:
    """Upsert lightweight seller profile fields."""
    try:
        with _LOCK:
            conn = _conn()
            conn.execute(
                """
                INSERT INTO seller_profiles (user_id, brand_kit, niche, sku_volume)
                VALUES (?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                    brand_kit=COALESCE(excluded.brand_kit, seller_profiles.brand_kit),
                    niche=COALESCE(excluded.niche, seller_profiles.niche),
                    sku_volume=COALESCE(excluded.sku_volume, seller_profiles.sku_volume),
                    updated_at=datetime('now')
                """,
                (
                    int(user_id),
                    (brand_kit or "").strip()[:1000] if brand_kit is not None else None,
                    (niche or "").strip()[:120] if niche is not None else None,
                    (sku_volume or "").strip()[:80] if sku_volume is not None else None,
                ),
            )
            conn.commit()
            return True
    except Exception:  # noqa: BLE001
        log.warning("save_seller_profile failed for user_id=%r", user_id, exc_info=True)
        return False


def get_seller_profile(user_id: int) -> dict:
    """Return seller profile fields, or an empty dict when absent."""
    try:
        with _LOCK:
            conn = _conn()
            row = conn.execute(
                "SELECT user_id, brand_kit, niche, sku_volume, created_at, updated_at "
                "FROM seller_profiles WHERE user_id=?",
                (int(user_id),),
            ).fetchone()
            return dict(row) if row else {}
    except Exception:  # noqa: BLE001
        log.warning("get_seller_profile failed for user_id=%r", user_id, exc_info=True)
        return {}


# ── support tickets ────────────────────────────────────────────────────


def create_ticket(user_id: int, username: str | None, text: str) -> int:
    """Create a new support ticket; returns the ticket id (0 on error)."""
    try:
        with _LOCK:
            conn = _conn()
            cur = conn.execute(
                "INSERT INTO support_tickets (user_id, username, message_text) VALUES (?,?,?)",
                (user_id, username, text[:2000]),
            )
            conn.commit()
            return cur.lastrowid or 0
    except Exception:  # noqa: BLE001
        log.warning("create_ticket failed for user_id=%r", user_id, exc_info=True)
        return 0


def set_ticket_admin_msg(ticket_id: int, admin_msg_id: int) -> None:
    """Store the admin-facing Telegram message id so we can resolve replies."""
    try:
        with _LOCK:
            conn = _conn()
            conn.execute(
                "UPDATE support_tickets SET admin_msg_id=? WHERE id=?",
                (admin_msg_id, ticket_id),
            )
            conn.commit()
    except Exception:  # noqa: BLE001
        log.warning("set_ticket_admin_msg failed", exc_info=True)


def get_ticket_by_admin_msg(admin_msg_id: int) -> dict | None:
    """Look up a ticket by the message id we sent to the admin."""
    try:
        with _LOCK:
            conn = _conn()
            row = conn.execute(
                "SELECT id, user_id, username, message_text, status "
                "FROM support_tickets WHERE admin_msg_id=?",
                (admin_msg_id,),
            ).fetchone()
            return dict(row) if row else None
    except Exception:  # noqa: BLE001
        log.warning("get_ticket_by_admin_msg failed", exc_info=True)
        return None


def reply_ticket(ticket_id: int, reply_text: str) -> dict | None:
    """Mark ticket as replied; returns {user_id, username, message_text} or None."""
    try:
        with _LOCK:
            conn = _conn()
            conn.execute(
                "UPDATE support_tickets SET reply_text=?, status='replied', "
                "replied_at=datetime('now') WHERE id=?",
                (reply_text[:2000], ticket_id),
            )
            conn.commit()
            row = conn.execute(
                "SELECT user_id, username, message_text FROM support_tickets WHERE id=?",
                (ticket_id,),
            ).fetchone()
            return dict(row) if row else None
    except Exception:  # noqa: BLE001
        log.warning("reply_ticket failed for ticket_id=%r", ticket_id, exc_info=True)
        return None


# ── promo codes ───────────────────────────────────────────────────────────


def create_promo_code(code: str, credits: int, max_uses: int = 1, created_by: int | None = None) -> bool:
    """Insert a new promo code; returns True on success, False if code already exists."""
    try:
        with _LOCK:
            conn = _conn()
            conn.execute(
                "INSERT INTO promo_codes (code, credits, max_uses, created_by) VALUES (?,?,?,?)",
                (code.upper().strip(), credits, max(1, max_uses), created_by),
            )
            conn.commit()
            return True
    except sqlite3.IntegrityError:
        return False
    except Exception:  # noqa: BLE001
        log.warning("create_promo_code failed for code=%r", code, exc_info=True)
        return False


def redeem_promo(code: str, user_id: int) -> int | None:
    """Attempt to redeem a promo code for a user.

    Returns the number of credits if successful, or None if:
    - code not found / exhausted
    - user already redeemed this code
    """
    code = code.upper().strip()
    try:
        with _LOCK:
            conn = _conn()
            row = conn.execute(
                "SELECT credits, max_uses, uses FROM promo_codes WHERE code=?",
                (code,),
            ).fetchone()
            if not row:
                return None
            if row["uses"] >= row["max_uses"]:
                return None
            # Check if user already redeemed
            dup = conn.execute(
                "SELECT 1 FROM promo_redemptions WHERE code=? AND user_id=?",
                (code, user_id),
            ).fetchone()
            if dup:
                return None
            # Claim redemption + increment counter atomically
            conn.execute(
                "INSERT INTO promo_redemptions (code, user_id) VALUES (?,?)",
                (code, user_id),
            )
            conn.execute(
                "UPDATE promo_codes SET uses = uses + 1 WHERE code=?",
                (code,),
            )
            conn.commit()
            return row["credits"]
    except Exception:  # noqa: BLE001
        log.warning("redeem_promo failed for code=%r user_id=%r", code, user_id, exc_info=True)
        return None


def list_promo_codes() -> list[dict]:
    """Return all promo codes for admin reporting."""
    try:
        with _LOCK:
            conn = _conn()
            rows = _rows(conn, "SELECT code, credits, max_uses, uses, created_at FROM promo_codes ORDER BY created_at DESC")
            return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001
        log.warning("list_promo_codes failed", exc_info=True)
        return []


# ── prompt history ────────────────────────────────────────────────────────────

_PROMPT_HISTORY_KEEP = 20  # max stored per user (trim older ones)

_WEB_CHAT_ID_MAX = 96
_WEB_CHAT_TEXT_MAX = 2000
_WEB_CHAT_MEDIA_MAX = 24_000


def create_web_chat(user_id: int, chat_id: str, title: str = "") -> bool:
    """Create an opaque, user-owned website conversation idempotently."""
    value = str(chat_id or "").strip()
    if not value or len(value) > _WEB_CHAT_ID_MAX:
        return False
    try:
        with _LOCK:
            conn = _conn()
            conn.execute(
                "INSERT OR IGNORE INTO web_chats (chat_id, user_id, title) VALUES (?,?,?)",
                (value, int(user_id), str(title or "").strip()[:120]),
            )
            conn.commit()
            row = conn.execute(
                "SELECT user_id FROM web_chats WHERE chat_id=?", (value,)
            ).fetchone()
            return bool(row and int(row[0]) == int(user_id))
    except Exception:  # noqa: BLE001
        log.warning("create_web_chat failed", exc_info=True)
        return False


def list_web_chats(user_id: int, limit: int = 30) -> list[dict]:
    """Return the newest conversations for one authenticated website user."""
    try:
        with _LOCK:
            conn = _conn()
            rows = _rows(
                conn,
                """
                SELECT c.chat_id, c.title, c.created_at, c.updated_at,
                       (SELECT COUNT(*) FROM web_chat_messages m
                        WHERE m.chat_id=c.chat_id AND m.user_id=c.user_id) AS messages
                FROM web_chats c
                WHERE c.user_id=?
                ORDER BY c.updated_at DESC, c.chat_id DESC LIMIT ?
                """,
                (int(user_id), max(1, min(int(limit), 50))),
            )
            return [dict(row) for row in rows]
    except Exception:  # noqa: BLE001
        log.warning("list_web_chats failed for user_id=%r", user_id, exc_info=True)
        return []


def get_web_chat(user_id: int, chat_id: str, limit: int = 40) -> dict | None:
    """Load one conversation only when it belongs to ``user_id``."""
    value = str(chat_id or "").strip()
    if not value or len(value) > _WEB_CHAT_ID_MAX:
        return None
    try:
        with _LOCK:
            conn = _conn()
            chat = conn.execute(
                "SELECT chat_id, user_id, title, created_at, updated_at "
                "FROM web_chats WHERE chat_id=? AND user_id=?",
                (value, int(user_id)),
            ).fetchone()
            if chat is None:
                return None
            rows = _rows(
                conn,
                """
                SELECT role, text, media_json, mode, model, aspect,
                       charged, balance, created_at
                FROM web_chat_messages
                WHERE chat_id=? AND user_id=?
                ORDER BY id ASC LIMIT ?
                """,
                (value, int(user_id), max(1, min(int(limit), 80))),
            )
            messages = []
            for row in rows:
                media = []
                if row["media_json"]:
                    try:
                        parsed = json.loads(row["media_json"])
                        if isinstance(parsed, list):
                            media = parsed[:4]
                    except (TypeError, ValueError):
                        media = []
                messages.append({
                    "role": row["role"],
                    "text": row["text"],
                    "media": media,
                    "mode": row["mode"],
                    "model": row["model"],
                    "aspect": row["aspect"],
                    "charged": int(row["charged"] or 0),
                    "balance": row["balance"],
                    "created_at": row["created_at"],
                })
            return {"chat": dict(chat), "messages": messages}
    except Exception:  # noqa: BLE001
        log.warning("get_web_chat failed for user_id=%r", user_id, exc_info=True)
        return None


def append_web_chat_message(
    user_id: int,
    chat_id: str,
    *,
    role: str,
    text: str = "",
    media: list[dict] | None = None,
    mode: str | None = None,
    model: str | None = None,
    aspect: str | None = None,
    charged: int = 0,
    balance: int | None = None,
) -> bool:
    """Append one bounded message to an owned web chat."""
    value = str(chat_id or "").strip()
    role_value = str(role or "").strip().lower()
    if role_value not in {"user", "assistant"} or not value:
        return False
    try:
        encoded_media = json.dumps((media or [])[:4], ensure_ascii=False, separators=(",", ":"))
        if len(encoded_media) > _WEB_CHAT_MEDIA_MAX:
            encoded_media = "[]"
        with _LOCK:
            conn = _conn()
            owned = conn.execute(
                "SELECT 1 FROM web_chats WHERE chat_id=? AND user_id=?",
                (value, int(user_id)),
            ).fetchone()
            if owned is None:
                return False
            clean_text = str(text or "")[:_WEB_CHAT_TEXT_MAX]
            conn.execute(
                """
                INSERT INTO web_chat_messages
                  (chat_id, user_id, role, text, media_json, mode, model, aspect, charged, balance)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    value, int(user_id), role_value, clean_text, encoded_media,
                    str(mode or "")[:32] or None, str(model or "")[:64] or None,
                    str(aspect or "")[:32] or None, max(0, int(charged or 0)),
                    int(balance) if balance is not None else None,
                ),
            )
            if role_value == "user":
                conn.execute(
                    "UPDATE web_chats SET title=CASE WHEN title='' THEN ? ELSE title END, "
                    "updated_at=datetime('now') WHERE chat_id=? AND user_id=?",
                    (clean_text[:120], value, int(user_id)),
                )
            else:
                conn.execute(
                    "UPDATE web_chats SET updated_at=datetime('now') WHERE chat_id=? AND user_id=?",
                    (value, int(user_id)),
                )
            conn.commit()
            return True
    except Exception:  # noqa: BLE001
        log.warning("append_web_chat_message failed for user_id=%r", user_id, exc_info=True)
        return False


def save_prompt_history(user_id: int, prompt: str) -> None:
    """Persist a generated prompt; trims to keep only the latest N per user."""
    if not prompt or len(prompt) < 3:
        return
    try:
        with _LOCK:
            conn = _conn()
            conn.execute(
                "INSERT INTO prompt_history (user_id, prompt) VALUES (?,?)",
                (user_id, prompt[:500]),
            )
            # Trim to N most recent
            conn.execute(
                """
                DELETE FROM prompt_history
                WHERE user_id=? AND id NOT IN (
                    SELECT id FROM prompt_history
                    WHERE user_id=?
                    ORDER BY id DESC
                    LIMIT ?
                )
                """,
                (user_id, user_id, _PROMPT_HISTORY_KEEP),
            )
            conn.commit()
    except Exception:  # noqa: BLE001
        log.warning("save_prompt_history failed for user_id=%r", user_id, exc_info=True)


def get_prompt_history(user_id: int, limit: int = 10) -> list[str]:
    """Return the user's most recent prompts, newest first."""
    try:
        with _LOCK:
            conn = _conn()
            rows = conn.execute(
                "SELECT prompt FROM prompt_history WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [r["prompt"] for r in rows]
    except Exception:  # noqa: BLE001
        log.warning("get_prompt_history failed for user_id=%r", user_id, exc_info=True)
        return []


def get_seller_history(user_id: int, limit: int = 10) -> list[dict]:
    """Return recent seller jobs with the nearest marketplace source event."""
    try:
        with _LOCK:
            conn = _conn()
            return [
                {
                    "id": int(r["id"]),
                    "created_at": r["created_at"],
                    "operation_type": r["operation_type"],
                    "model": r["model"],
                    "status": r["status"],
                    "error_type": r["error_type"],
                    "bot_credits_charged": int(r["bot_credits_charged"] or 0),
                    "refund_amount": int(r["refund_amount"] or 0),
                    "mp_source": r["mp_source"],
                }
                for r in _rows(
                    conn,
                    """
                    SELECT fj.id, fj.created_at, fj.operation_type, fj.model, fj.status,
                           fj.error_type, fj.bot_credits_charged, fj.refund_amount,
                           (
                               SELECT e.source FROM events e
                               WHERE e.user_id=fj.user_id
                                 AND e.event_name='mp_job'
                                 AND e.created_at <= fj.created_at
                               ORDER BY e.id DESC LIMIT 1
                           ) AS mp_source
                    FROM flow_jobs fj
                    WHERE fj.user_id=?
                    ORDER BY fj.id DESC LIMIT ?
                    """,
                    (int(user_id), max(1, int(limit))),
                )
            ]
    except Exception:  # noqa: BLE001
        log.warning("get_seller_history failed for user_id=%r", user_id, exc_info=True)
        return []


# ── daily streaks ─────────────────────────────────────────────────────────────


def update_streak(user_id: int) -> tuple[int, int, bool]:
    """Update the daily generation streak for *user_id*.

    Returns ``(current_streak, max_streak, is_new_day)`` where *is_new_day* is
    ``True`` when this call represents the user's first generation today (i.e.
    the streak counter just changed).  Consecutive calls on the same UTC date
    are no-ops and return ``is_new_day=False``.  Never raises.
    """
    from datetime import datetime as _dt, timedelta as _td  # local to avoid circular

    try:
        today = _dt.utcnow().strftime("%Y-%m-%d")
        yesterday = (_dt.utcnow() - _td(days=1)).strftime("%Y-%m-%d")
        with _LOCK:
            con = _conn()
            row = con.execute(
                "SELECT last_active_date, current_streak, max_streak FROM user_streaks WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if row is None:
                con.execute(
                    "INSERT INTO user_streaks(user_id, last_active_date, current_streak, max_streak)"
                    " VALUES(?,?,1,1)",
                    (user_id, today),
                )
                con.commit()
                return (1, 1, True)
            last_date = row["last_active_date"]
            current = row["current_streak"]
            maximum = row["max_streak"]
            if last_date == today:
                return (current, maximum, False)
            if last_date == yesterday:
                current += 1
            else:
                current = 1  # gap → reset
            maximum = max(maximum, current)
            con.execute(
                "UPDATE user_streaks SET last_active_date=?, current_streak=?, max_streak=? WHERE user_id=?",
                (today, current, maximum, user_id),
            )
            con.commit()
            return (current, maximum, True)
    except Exception:  # noqa: BLE001
        log.warning("update_streak failed for user_id=%r", user_id, exc_info=True)
        return (1, 1, False)


def get_streak(user_id: int) -> tuple[int, int]:
    """Return ``(current_streak, max_streak)`` without mutating anything."""
    try:
        with _LOCK:
            con = _conn()
            row = con.execute(
                "SELECT current_streak, max_streak FROM user_streaks WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if row is None:
                return (0, 0)
            return (row["current_streak"], row["max_streak"])
    except Exception:  # noqa: BLE001
        log.warning("get_streak failed for user_id=%r", user_id, exc_info=True)
        return (0, 0)


# ── retention cohorts ─────────────────────────────────────────────────────────

#: Events that count as "user was active on that day".
_ACTIVE_EVENTS = ("wizard_completed", "image_success", "video_completed")


def report_cohort_retention(
    periods: tuple[int, ...] = (1, 7, 30),
    cohorts_per_period: int = 7,
) -> list[dict]:
    """D1 / D7 / D30 retention per cohort (daily).

    For each *period* p we look at the most recent ``cohorts_per_period`` cohort
    days for which the full window has elapsed (i.e. cohort_date ≤ today − p−1).
    Each cohort is users whose *first* ``user_started`` event occurred on
    ``cohort_date``.  A user "retained" if they had any active event exactly on
    ``cohort_date + p`` (calendar day).

    Returns a list of dicts sorted by period asc, then cohort_date desc:
      ``{period, cohort_date, cohort_size, retained, rate}``
    Never raises.
    """
    placeholders = "(" + ", ".join(f"'{e}'" for e in _ACTIVE_EVENTS) + ")"
    rows_out: list[dict] = []
    try:
        from datetime import datetime as _dt, timedelta as _td  # local import
        with _LOCK:
            con = _conn()
            for p in periods:
                for offset in range(cohorts_per_period):
                    # Cohort date: far enough back that day+p has fully elapsed.
                    cohort_date = (
                        _dt.utcnow() - _td(days=p + 1 + offset)
                    ).strftime("%Y-%m-%d")
                    active_date = (
                        _dt.utcnow() - _td(days=1 + offset)
                    ).strftime("%Y-%m-%d")
                    sql = f"""
                    WITH cohort AS (
                        SELECT DISTINCT user_id
                        FROM   events
                        WHERE  event_name = 'user_started'
                          AND  date(created_at) = ?
                    ),
                    active AS (
                        SELECT DISTINCT e.user_id
                        FROM   cohort c
                        JOIN   events e ON c.user_id = e.user_id
                        WHERE  date(e.created_at) = ?
                          AND  e.event_name IN {placeholders}
                    )
                    SELECT
                        (SELECT COUNT(*) FROM cohort) AS cohort_size,
                        (SELECT COUNT(*) FROM active)  AS retained
                    """
                    row = con.execute(sql, (cohort_date, active_date)).fetchone()
                    if row is None:
                        continue
                    size = row["cohort_size"] or 0
                    ret = row["retained"] or 0
                    rows_out.append({
                        "period": p,
                        "cohort_date": cohort_date,
                        "cohort_size": size,
                        "retained": ret,
                        "rate": round(ret / size, 3) if size else 0.0,
                    })
    except Exception:  # noqa: BLE001
        log.warning("report_cohort_retention failed", exc_info=True)
    return rows_out


# ── qualitative analytics (activation / repeat-purchase / margin / k) ──────
#
# These four are the "is the business working" reports, separate from the
# operational dashboards above. See docs/METRICS_ANALYTICS.md for what each
# number means and what threshold is healthy.

# First-year blended provider cost per G-credit (docs/MONETIZATION.md → Provider
# Economics). Used to convert measured G-credit consumption into RUB COGS.
G_CREDIT_COST_RUB = 0.053


def report_activation_cold(window_hours: int = 24) -> dict:
    """Cold-start activation: share of new users who reached their first
    image/video success within ``window_hours`` of their first ``user_started``,
    segmented by acquisition channel.

    Only users whose full window has already elapsed are counted, so the rate is
    not dragged down by users who just signed up. Source events are subject to
    the events-retention purge (default 90 days), so this looks back ≤90 days.
    """
    window_hours = max(1, int(window_hours))
    empty = {
        "window_hours": window_hours, "cohort": 0, "activated": 0,
        "rate": 0.0, "by_channel": [],
    }
    try:
        with _LOCK:
            conn = _conn()
            sql = """
            WITH starts AS (
                SELECT user_id, MIN(created_at) AS started_at
                FROM   events
                WHERE  event_name='user_started' AND user_id IS NOT NULL
                GROUP  BY user_id
            ),
            eligible AS (
                SELECT user_id, started_at FROM starts
                WHERE  started_at <= datetime('now', ?)
            ),
            firstwow AS (
                SELECT user_id, MIN(created_at) AS wow_at
                FROM   events
                WHERE  event_name IN ('image_success','video_success')
                  AND  user_id IS NOT NULL
                GROUP  BY user_id
            )
            SELECT
                COALESCE(a.channel, u.acq_channel, 'unknown') AS channel,
                COUNT(*) AS cohort,
                SUM(CASE WHEN f.wow_at IS NOT NULL
                          AND f.wow_at <= datetime(e.started_at, ?)
                         THEN 1 ELSE 0 END) AS activated
            FROM eligible e
            LEFT JOIN firstwow f     ON f.user_id = e.user_id
            LEFT JOIN acquisitions a ON a.user_id = e.user_id
            LEFT JOIN users u        ON u.user_id = e.user_id
            GROUP BY channel
            ORDER BY cohort DESC, channel
            """
            rows = _rows(conn, sql, (f"-{window_hours} hours", f"+{window_hours} hours"))
            by_channel = []
            tot_cohort = tot_act = 0
            for r in rows:
                c = int(r["cohort"] or 0)
                a = int(r["activated"] or 0)
                tot_cohort += c
                tot_act += a
                by_channel.append({
                    "channel": r["channel"] or "unknown",
                    "cohort": c, "activated": a,
                    "rate": round(a / c, 3) if c else 0.0,
                })
            return {
                "window_hours": window_hours,
                "cohort": tot_cohort,
                "activated": tot_act,
                "rate": round(tot_act / tot_cohort, 3) if tot_cohort else 0.0,
                "by_channel": by_channel,
            }
    except Exception:  # noqa: BLE001
        log.warning("report_activation_cold failed", exc_info=True)
        return empty


def report_payment_repeat(days: int = 30) -> dict:
    """Repeat-PURCHASE cohort over ``days`` — distinct from return-activity.

    Among users whose FIRST paid transaction is at least ``days`` old (so the
    repeat window has fully elapsed), the share who made a SECOND paid
    transaction within ``days`` of the first, plus the median days-to-second for
    those who did. Payment time uses ``paid_at`` with a ``created_at`` fallback.
    """
    days = max(1, int(days))
    empty = {
        "days": days, "first_payers": 0, "eligible_first_payers": 0,
        "repeated": 0, "repeat_rate": 0.0, "median_days_to_second": None,
    }
    try:
        with _LOCK:
            conn = _conn()
            sql = """
            WITH firstpay AS (
                SELECT user_id, MIN(COALESCE(paid_at, created_at)) AS first_at
                FROM   transactions
                WHERE  status='paid' AND user_id IS NOT NULL
                GROUP  BY user_id
            ),
            secondpay AS (
                SELECT t.user_id, MIN(COALESCE(t.paid_at, t.created_at)) AS second_at
                FROM   transactions t
                JOIN   firstpay f ON t.user_id = f.user_id
                WHERE  t.status='paid'
                  AND  COALESCE(t.paid_at, t.created_at) > f.first_at
                GROUP  BY t.user_id
            )
            SELECT f.user_id, f.first_at, s.second_at,
                   CASE WHEN s.second_at IS NOT NULL
                        THEN julianday(s.second_at) - julianday(f.first_at)
                   END AS days_to_second
            FROM firstpay f
            LEFT JOIN secondpay s ON s.user_id = f.user_id
            """
            rows = _rows(conn, sql)
            cutoff = _scalar(conn, "SELECT datetime('now', ?)", (f"-{days} days",))
            cutoff = str(cutoff or "")
            first_payers = len(rows)
            eligible = 0
            repeated = 0
            deltas: list[float] = []
            for r in rows:
                first_at = r["first_at"]
                # Only count first-payers whose repeat window has fully elapsed.
                if first_at is None or str(first_at) > cutoff:
                    continue
                eligible += 1
                d = r["days_to_second"]
                if d is not None and d <= days:
                    repeated += 1
                    deltas.append(float(d))
            median = None
            if deltas:
                deltas.sort()
                n = len(deltas)
                median = deltas[n // 2] if n % 2 else (deltas[n // 2 - 1] + deltas[n // 2]) / 2
                median = round(median, 1)
            return {
                "days": days,
                "first_payers": first_payers,
                "eligible_first_payers": eligible,
                "repeated": repeated,
                "repeat_rate": round(repeated / eligible, 3) if eligible else 0.0,
                "median_days_to_second": median,
            }
    except Exception:  # noqa: BLE001
        log.warning("report_payment_repeat failed", exc_info=True)
        return empty


def report_margin(days: int = 90, g_credit_cost_rub: float | None = None) -> dict:
    """Unit economics over ``days``: real provider COGS vs revenue.

    COGS is measured (not guessed): ``flow_jobs.flow_credits_delta`` is the
    provider balance change per job (``after - before``); consumption is the
    negative part, summed as G-credits. RUB COGS = G-credits × ``g_credit_cost_rub``
    (default :data:`G_CREDIT_COST_RUB`). Revenue is paid transactions in the same
    window. Credits can be bought and spent in different windows, so treat the
    margin as directional over 30–90 days, not to-the-ruble.
    """
    days = max(1, int(days))
    cost = G_CREDIT_COST_RUB if g_credit_cost_rub is None else max(0.0, float(g_credit_cost_rub))
    window = f"-{days} days"
    empty = {
        "days": days, "g_credit_cost_rub": cost,
        "revenue_rub": 0.0, "credits_sold": 0, "paying_users": 0,
        "g_credits_consumed": 0, "bot_credits_charged": 0,
        "cogs_rub": 0.0, "gross_margin_rub": 0.0, "gross_margin_pct": 0.0,
        "rub_per_bot_credit": 0.0, "cogs_rub_per_bot_credit": 0.0,
        "contribution_margin_per_payer_rub": 0.0, "by_model": [],
    }
    try:
        with _LOCK:
            conn = _conn()
            tx_since = "status='paid' AND created_at >= datetime('now', ?)"
            revenue_rub = float(_scalar(conn, f"SELECT COALESCE(SUM(amount_rub),0) FROM transactions WHERE {tx_since}", (window,)) or 0)
            credits_sold = int(_scalar(conn, f"SELECT COALESCE(SUM(credits_issued),0) FROM transactions WHERE {tx_since}", (window,)) or 0)
            paying_users = int(_scalar(conn, f"SELECT COUNT(DISTINCT user_id) FROM transactions WHERE {tx_since}", (window,)) or 0)

            fj_since = "created_at >= datetime('now', ?)"
            consumed_expr = "CASE WHEN flow_credits_delta < 0 THEN -flow_credits_delta ELSE 0 END"
            g_consumed = int(_scalar(conn, f"SELECT COALESCE(SUM({consumed_expr}),0) FROM flow_jobs WHERE {fj_since}", (window,)) or 0)
            bot_charged = int(_scalar(conn, f"SELECT COALESCE(SUM(bot_credits_charged),0) FROM flow_jobs WHERE {fj_since}", (window,)) or 0)

            by_model = [
                {
                    "model": r["model"] or "—",
                    "jobs": int(r["jobs"]),
                    "g_credits": int(r["g"] or 0),
                    "bot_credits": int(r["bot"] or 0),
                    "cogs_rub": round(int(r["g"] or 0) * cost, 2),
                }
                for r in _rows(
                    conn,
                    f"SELECT model, COUNT(*) AS jobs, "
                    f"COALESCE(SUM({consumed_expr}),0) AS g, "
                    f"COALESCE(SUM(bot_credits_charged),0) AS bot "
                    f"FROM flow_jobs WHERE {fj_since} GROUP BY model ORDER BY g DESC",
                    (window,),
                )
            ]

            cogs_rub = round(g_consumed * cost, 2)
            gross_margin_rub = round(revenue_rub - cogs_rub, 2)
            return {
                "days": days,
                "g_credit_cost_rub": cost,
                "revenue_rub": round(revenue_rub, 2),
                "credits_sold": credits_sold,
                "paying_users": paying_users,
                "g_credits_consumed": g_consumed,
                "bot_credits_charged": bot_charged,
                "cogs_rub": cogs_rub,
                "gross_margin_rub": gross_margin_rub,
                "gross_margin_pct": round(gross_margin_rub / revenue_rub, 3) if revenue_rub else 0.0,
                "rub_per_bot_credit": round(revenue_rub / credits_sold, 4) if credits_sold else 0.0,
                "cogs_rub_per_bot_credit": round(cogs_rub / bot_charged, 4) if bot_charged else 0.0,
                "contribution_margin_per_payer_rub": round(gross_margin_rub / paying_users, 2) if paying_users else 0.0,
                "by_model": by_model,
            }
    except Exception:  # noqa: BLE001
        log.warning("report_margin failed", exc_info=True)
        return empty


def report_referral_quality() -> dict:
    """Honest referral virality (k) and referral-share of activations.

    "Generated something" uses successful ``flow_jobs`` rows (durable; not
    purged like events). ``k`` is invited users who generated divided by distinct
    referrers — not the exponential viral coefficient, just activations produced
    per referrer. ``referral_share`` is the fraction of all activated users that
    came in through a referral; 15–30%+ meaningfully lowers blended CAC.
    """
    empty = {
        "referrers": 0, "invited": 0, "invited_generated": 0, "k": 0.0,
        "activated_total": 0, "activated_referred": 0, "referral_share": 0.0,
        "first_generation_rewards": 0,
    }
    try:
        with _LOCK:
            conn = _conn()
            referrers = int(_scalar(conn, "SELECT COUNT(DISTINCT referrer_user_id) FROM referrals WHERE referrer_user_id IS NOT NULL") or 0)
            invited = int(_scalar(conn, "SELECT COUNT(DISTINCT referred_user_id) FROM referrals WHERE referred_user_id IS NOT NULL") or 0)
            generated_users = "SELECT user_id FROM flow_jobs WHERE status='success' AND user_id IS NOT NULL"
            invited_generated = int(_scalar(
                conn,
                f"SELECT COUNT(DISTINCT referred_user_id) FROM referrals "
                f"WHERE referred_user_id IN ({generated_users})",
            ) or 0)
            activated_total = int(_scalar(conn, f"SELECT COUNT(DISTINCT user_id) FROM ({generated_users})") or 0)
            activated_referred = int(_scalar(
                conn,
                f"SELECT COUNT(DISTINCT user_id) FROM flow_jobs "
                f"WHERE status='success' AND user_id IS NOT NULL "
                f"AND user_id IN (SELECT referred_user_id FROM referrals WHERE referred_user_id IS NOT NULL)",
            ) or 0)
            first_gen = int(_scalar(conn, "SELECT COUNT(*) FROM referral_first_generation_rewards") or 0)
            return {
                "referrers": referrers,
                "invited": invited,
                "invited_generated": invited_generated,
                "k": round(invited_generated / referrers, 3) if referrers else 0.0,
                "activated_total": activated_total,
                "activated_referred": activated_referred,
                "referral_share": round(activated_referred / activated_total, 3) if activated_total else 0.0,
                "first_generation_rewards": first_gen,
            }
    except Exception:  # noqa: BLE001
        log.warning("report_referral_quality failed", exc_info=True)
        return empty


def get_users_for_digest(min_days: int = 3, max_days: int = 7, limit: int = 100) -> list[dict]:
    """Return users inactive for min_days..max_days who haven't received a digest recently.

    «Last activity» is the latest event row for the user.
    Excludes users who already got a ``digest_sent`` event within ``max_days`` days.
    """
    try:
        with _LOCK:
            conn = _conn()
            rows = _rows(
                conn,
                """
                SELECT u.user_id,
                       MAX(e.created_at) AS last_event
                FROM users u
                LEFT JOIN events e ON e.user_id = u.user_id
                WHERE u.user_id IS NOT NULL
                GROUP BY u.user_id
                HAVING last_event IS NOT NULL
                   AND last_event <= datetime('now', ? || ' days')
                   AND last_event >= datetime('now', ? || ' days')
                   AND u.user_id NOT IN (
                       SELECT user_id FROM events
                       WHERE event_name = 'digest_sent'
                         AND created_at >= datetime('now', ? || ' days')
                   )
                LIMIT ?
                """,
                (f"-{min_days}", f"-{max_days}", f"-{max_days}", limit),
            )
            return [{"user_id": r["user_id"], "last_event": r["last_event"]} for r in rows]
    except Exception:  # noqa: BLE001
        log.warning("get_users_for_digest failed", exc_info=True)
        return []


def get_user_tickets(user_id: int) -> list:
    """Return all tickets for a user (newest first, up to 20)."""
    try:
        with _LOCK:
            conn = _conn()
            return [
                {
                    "id":           r["id"],
                    "status":       r["status"],
                    "message_text": r["message_text"],
                    "reply_text":   r["reply_text"],
                    "created_at":   r["created_at"],
                }
                for r in _rows(
                    conn,
                    "SELECT id, status, message_text, reply_text, created_at "
                    "FROM support_tickets WHERE user_id=? ORDER BY id DESC LIMIT 20",
                    (user_id,),
                )
            ]
    except Exception:  # noqa: BLE001
        log.warning("get_user_tickets failed for user_id=%r", user_id, exc_info=True)
        return []


def _recent_payments(conn: sqlite3.Connection, user_id: int, limit: int = 10) -> list[dict]:
    return [
        {
            "id": int(r["id"]),
            "provider": r["provider"],
            "package_id": r["package_id"],
            "amount_rub": float(r["amount_rub"] or 0.0),
            "stars_amount": int(r["stars_amount"] or 0),
            "credits_issued": int(r["credits_issued"] or 0),
            "status": r["status"],
            "created_at": r["created_at"],
        }
        for r in _rows(
            conn,
            """
            SELECT id, provider, package_id, amount_rub, stars_amount,
                   credits_issued, status, created_at
            FROM transactions
            WHERE user_id=?
            ORDER BY id DESC LIMIT ?
            """,
            (user_id, int(limit)),
        )
    ]


def _recent_flow_jobs(conn: sqlite3.Connection, user_id: int, limit: int = 12) -> list[dict]:
    return [
        {
            "id": int(r["id"]),
            "created_at": r["created_at"],
            "account_id": r["account_id"],
            "operation_type": r["operation_type"],
            "model": r["model"],
            "status": r["status"],
            "error_type": r["error_type"],
            "bot_credits_charged": int(r["bot_credits_charged"] or 0),
            "refund_amount": int(r["refund_amount"] or 0),
            "duration_ms": r["duration_ms"],
        }
        for r in _rows(
            conn,
            """
            SELECT id, created_at, account_id, operation_type, model, status,
                   error_type, bot_credits_charged, refund_amount, duration_ms
            FROM flow_jobs
            WHERE user_id=?
            ORDER BY id DESC LIMIT ?
            """,
            (user_id, int(limit)),
        )
    ]


def _support_tickets_for_admin(conn: sqlite3.Connection, user_id: int, limit: int = 20) -> list[dict]:
    return [
        {
            "id": int(r["id"]),
            "status": r["status"],
            "message_text": r["message_text"],
            "reply_text": r["reply_text"],
            "created_at": r["created_at"],
            "replied_at": r["replied_at"],
        }
        for r in _rows(
            conn,
            """
            SELECT id, status, message_text, reply_text, created_at, replied_at
            FROM support_tickets
            WHERE user_id=?
            ORDER BY id DESC LIMIT ?
            """,
            (user_id, int(limit)),
        )
    ]


def get_admin_user_detail(user_id: int) -> dict:
    """Return an operator/debug dossier for one cross-platform user."""
    profile = get_user_profile(int(user_id))
    identity = get_identity_by_internal_id(int(user_id))
    try:
        with _LOCK:
            conn = _conn()
            recent_jobs = _recent_flow_jobs(conn, int(user_id), 12)
            recent_payments = _recent_payments(conn, int(user_id), 10)
            support_tickets = _support_tickets_for_admin(conn, int(user_id), 20)
            credit_events = [
                {
                    "id": int(r["id"]),
                    "created_at": r["created_at"],
                    "operation_type": r["operation_type"],
                    "charged": int(r["bot_credits_charged"] or 0),
                    "refunded": int(r["refund_amount"] or 0),
                    "status": r["status"],
                }
                for r in _rows(
                    conn,
                    """
                    SELECT id, created_at, operation_type, bot_credits_charged,
                           refund_amount, status
                    FROM flow_jobs
                    WHERE user_id=?
                      AND (bot_credits_charged != 0 OR refund_amount != 0)
                    ORDER BY id DESC LIMIT 20
                    """,
                    (int(user_id),),
                )
            ]
            prompt_history = [
                {
                    "prompt": str(r["prompt"] or ""),
                    "created_at": r["created_at"],
                }
                for r in _rows(
                    conn,
                    """
                    SELECT prompt, created_at
                    FROM prompt_history
                    WHERE user_id=?
                    ORDER BY id DESC LIMIT 20
                    """,
                    (int(user_id),),
                )
            ]
        return {
            "profile": profile,
            "identity": identity,
            "recent_payments": recent_payments,
            "recent_jobs": recent_jobs,
            "credit_events": credit_events,
            "support_tickets": support_tickets,
            "prompt_history": prompt_history,
        }
    except Exception:  # noqa: BLE001
        log.warning("get_admin_user_detail failed for user_id=%r", user_id, exc_info=True)
        return {
            "profile": profile,
            "identity": identity,
            "recent_payments": [],
            "recent_jobs": [],
            "credit_events": [],
            "support_tickets": [],
            "prompt_history": [],
        }


def list_support_tickets(status: str = "open", limit: int = 100) -> list[dict]:
    """Return support tickets enriched with user/payment/generation context."""
    allowed = {"open", "in_work", "done", "replied", "closed", "all", "done4you"}
    status = status if status in allowed else "open"
    limit = max(1, min(int(limit), 200))
    if status == "all":
        where = ""
        params: tuple = (limit,)
    elif status == "done4you":
        where = "WHERE st.message_text LIKE ?"
        params = ("🙌 Заявка под ключ%", limit)
    else:
        where = "WHERE st.status=?"
        params = (status, limit)
    try:
        with _LOCK:
            conn = _conn()
            rows = _rows(
                conn,
                f"""
                SELECT st.id, st.user_id, st.username, st.status,
                       st.message_text, st.reply_text, st.created_at, st.replied_at,
                       CASE WHEN st.message_text LIKE '🙌 Заявка под ключ%' THEN 'done4you'
                            ELSE 'support' END AS kind,
                       COALESCE(c.balance, 0) AS balance,
                       u.first_name, u.last_active, u.is_blocked,
                       (SELECT COUNT(*) FROM transactions t
                        WHERE t.user_id=st.user_id AND t.status='paid') AS payments_count,
                       (SELECT COALESCE(SUM(t.amount_rub),0) FROM transactions t
                        WHERE t.user_id=st.user_id AND t.status='paid') AS rub_total,
                       (SELECT COALESCE(SUM(t.stars_amount),0) FROM transactions t
                        WHERE t.user_id=st.user_id AND t.status='paid') AS stars_total,
                       (SELECT fj.created_at FROM flow_jobs fj
                        WHERE fj.user_id=st.user_id ORDER BY fj.id DESC LIMIT 1) AS last_job_at,
                       (SELECT fj.error_type FROM flow_jobs fj
                        WHERE fj.user_id=st.user_id AND fj.status!='success'
                        ORDER BY fj.id DESC LIMIT 1) AS last_error
                FROM support_tickets st
                LEFT JOIN credits c ON c.user_id = st.user_id
                LEFT JOIN users u ON u.user_id = st.user_id
                {where}
                ORDER BY CASE st.status
                           WHEN 'open' THEN 0
                           WHEN 'in_work' THEN 1
                           WHEN 'done' THEN 2
                           WHEN 'replied' THEN 3
                           WHEN 'closed' THEN 4
                           ELSE 5
                         END,
                         st.id DESC
                LIMIT ?
                """,
                params,
            )
        return [
            {
                "id": int(r["id"]),
                "user_id": int(r["user_id"]),
                "username": r["username"],
                "first_name": r["first_name"],
                "status": r["status"],
                "kind": r["kind"],
                "message_text": r["message_text"],
                "reply_text": r["reply_text"],
                "created_at": r["created_at"],
                "replied_at": r["replied_at"],
                "balance": int(r["balance"] or 0),
                "last_active": r["last_active"],
                "is_blocked": bool(r["is_blocked"]),
                "payments_count": int(r["payments_count"] or 0),
                "rub_total": float(r["rub_total"] or 0.0),
                "stars_total": int(r["stars_total"] or 0),
                "last_job_at": r["last_job_at"],
                "last_error": r["last_error"],
            }
            for r in rows
        ]
    except Exception:  # noqa: BLE001
        log.warning("list_support_tickets failed", exc_info=True)
        return []


def get_support_ticket_detail(ticket_id: int) -> dict | None:
    """Return one support ticket with the related user dossier."""
    try:
        with _LOCK:
            conn = _conn()
            row = conn.execute(
                """
                SELECT id, user_id, username, status, message_text, reply_text,
                       CASE WHEN message_text LIKE '🙌 Заявка под ключ%' THEN 'done4you'
                            ELSE 'support' END AS kind,
                       admin_msg_id, created_at, replied_at
                FROM support_tickets
                WHERE id=?
                """,
                (int(ticket_id),),
            ).fetchone()
        if not row:
            return None
        ticket = {
            "id": int(row["id"]),
            "user_id": int(row["user_id"]),
            "username": row["username"],
            "status": row["status"],
            "kind": row["kind"],
            "message_text": row["message_text"],
            "reply_text": row["reply_text"],
            "admin_msg_id": row["admin_msg_id"],
            "created_at": row["created_at"],
            "replied_at": row["replied_at"],
        }
        return {"ticket": ticket, "user": get_admin_user_detail(int(row["user_id"]))}
    except Exception:  # noqa: BLE001
        log.warning("get_support_ticket_detail failed for ticket_id=%r", ticket_id, exc_info=True)
        return None


def set_support_ticket_status(ticket_id: int, status: str) -> bool:
    """Set a support ticket status without sending Telegram messages."""
    if status not in {"open", "in_work", "done", "replied", "closed"}:
        return False
    try:
        with _LOCK:
            conn = _conn()
            cur = conn.execute(
                """
                UPDATE support_tickets
                SET status=?,
                    replied_at=CASE
                      WHEN ?='replied' AND replied_at IS NULL THEN datetime('now')
                      ELSE replied_at
                    END
                WHERE id=?
                """,
                (status, status, int(ticket_id)),
            )
            conn.commit()
            return cur.rowcount > 0
    except Exception:  # noqa: BLE001
        log.warning("set_support_ticket_status failed for ticket_id=%r", ticket_id, exc_info=True)
        return False
