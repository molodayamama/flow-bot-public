"""Durable MAX webhook inbox with deduplication and retry/dead-letter state."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator, Mapping

from channels.max.webhook import parse_update

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class InboxEvent:
    event_id: str
    payload: dict[str, Any]
    attempts: int
    claim_id: str


def event_id_for(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def partition_key_for(payload: Mapping[str, Any]) -> str:
    """Return a stable ordering key, preferring chat and then sender identity."""
    for container_name in ("message", "callback"):
        value = payload.get(container_name)
        if not isinstance(value, Mapping):
            continue
        chat_id = value.get("chat_id")
        chat = value.get("chat")
        if chat_id is None and isinstance(chat, Mapping):
            chat_id = chat.get("chat_id") or chat.get("id")
        if chat_id is not None:
            return f"chat:{chat_id}"
        for user_name in ("sender", "user"):
            user = value.get(user_name)
            if isinstance(user, Mapping):
                user_id = user.get("user_id") or user.get("id")
                if user_id is not None:
                    return f"user:{user_id}"
    return event_id_for(payload)


class MaxWebhookInbox:
    def __init__(
        self,
        path: str | Path,
        *,
        max_attempts: int = 10,
        claim_lease_seconds: float = 3600.0,
    ) -> None:
        self.path = Path(path)
        self.max_attempts = max(1, int(max_attempts))
        self.claim_lease_seconds = max(1.0, float(claim_lease_seconds))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5.0)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS max_webhook_inbox (
                    event_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    received_at REAL NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    processed_at REAL,
                    dead_at REAL,
                    last_error TEXT,
                    claim_id TEXT,
                    claimed_until REAL,
                    partition_key TEXT
                )"""
            )
            # Forward-compatible for a database created by an earlier build.
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(max_webhook_inbox)")
            }
            if "claim_id" not in columns:
                conn.execute("ALTER TABLE max_webhook_inbox ADD COLUMN claim_id TEXT")
            if "claimed_until" not in columns:
                conn.execute("ALTER TABLE max_webhook_inbox ADD COLUMN claimed_until REAL")
            if "partition_key" not in columns:
                conn.execute("ALTER TABLE max_webhook_inbox ADD COLUMN partition_key TEXT")
            conn.execute(
                """UPDATE max_webhook_inbox SET partition_key=event_id
                   WHERE partition_key IS NULL OR partition_key=''"""
            )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS max_webhook_inbox_ready
                   ON max_webhook_inbox(
                       processed_at,dead_at,next_attempt_at,claimed_until,partition_key,received_at
                   )"""
            )

    def enqueue(self, payload: Mapping[str, Any]) -> tuple[str, bool]:
        event_id = event_id_for(payload)
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO max_webhook_inbox(
                       event_id,payload,received_at,partition_key
                   ) VALUES(?,?,?,?)""",
                (event_id, encoded, time.time(), partition_key_for(payload)),
            )
        return event_id, cursor.rowcount == 1

    def claim_ready(self, *, now: float | None = None) -> InboxEvent | None:
        """Atomically lease one ready event so concurrent workers cannot share it."""
        current = time.time() if now is None else float(now)
        claim_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT current.event_id,current.payload,current.attempts
                   FROM max_webhook_inbox AS current
                   WHERE current.processed_at IS NULL AND current.dead_at IS NULL
                     AND current.next_attempt_at<=?
                     AND (current.claimed_until IS NULL OR current.claimed_until<=?)
                     AND NOT EXISTS (
                         SELECT 1 FROM max_webhook_inbox AS older
                         WHERE older.partition_key=current.partition_key
                           AND older.rowid<current.rowid
                           AND older.processed_at IS NULL AND older.dead_at IS NULL
                     )
                   ORDER BY current.received_at,current.rowid LIMIT 1""",
                (current, current),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE max_webhook_inbox SET claim_id=?,claimed_until=? WHERE event_id=?",
                (claim_id, current + self.claim_lease_seconds, row["event_id"]),
            )
        return InboxEvent(
            row["event_id"], json.loads(row["payload"]), int(row["attempts"]), claim_id
        )

    def mark_done(self, event_id: str, claim_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE max_webhook_inbox
                   SET processed_at=?,last_error=NULL,claim_id=NULL,claimed_until=NULL
                   WHERE event_id=? AND claim_id=?""",
                (time.time(), event_id, claim_id),
            )

    def mark_failed(self, event_id: str, claim_id: str, error: BaseException) -> None:
        # Store only exception type: provider/user/token text must never enter the inbox DB.
        with self._connect() as conn:
            row = conn.execute(
                "SELECT attempts FROM max_webhook_inbox WHERE event_id=? AND claim_id=?",
                (event_id, claim_id),
            ).fetchone()
            if row is None:
                return
            attempts = int(row["attempts"]) + 1
            if attempts >= self.max_attempts:
                conn.execute(
                    """UPDATE max_webhook_inbox
                       SET attempts=?,dead_at=?,last_error=?,claim_id=NULL,claimed_until=NULL
                       WHERE event_id=? AND claim_id=?""",
                    (attempts, time.time(), error.__class__.__name__, event_id, claim_id),
                )
            else:
                delay = min(300.0, 2.0 ** min(attempts, 8))
                conn.execute(
                    """UPDATE max_webhook_inbox
                       SET attempts=?,next_attempt_at=?,last_error=?,claim_id=NULL,claimed_until=NULL
                       WHERE event_id=? AND claim_id=?""",
                    (
                        attempts,
                        time.time() + delay,
                        error.__class__.__name__,
                        event_id,
                        claim_id,
                    ),
                )

    def stats(self) -> dict[str, int]:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT
                    SUM(CASE WHEN processed_at IS NULL AND dead_at IS NULL THEN 1 ELSE 0 END) pending,
                    SUM(CASE WHEN processed_at IS NOT NULL THEN 1 ELSE 0 END) processed,
                    SUM(CASE WHEN dead_at IS NOT NULL THEN 1 ELSE 0 END) dead
                    FROM max_webhook_inbox"""
            ).fetchone()
        return {name: int(row[name] or 0) for name in ("pending", "processed", "dead")}

    def prune(
        self,
        *,
        now: float | None = None,
        processed_retention_seconds: float = 7 * 24 * 3600,
        dead_retention_seconds: float = 30 * 24 * 3600,
    ) -> int:
        """Bound disk growth while retaining a useful dedup/dead-letter window."""
        current = time.time() if now is None else float(now)
        with self._connect() as conn:
            cursor = conn.execute(
                """DELETE FROM max_webhook_inbox
                   WHERE (processed_at IS NOT NULL AND processed_at<?)
                      OR (dead_at IS NOT NULL AND dead_at<?)""",
                (
                    current - max(0.0, processed_retention_seconds),
                    current - max(0.0, dead_retention_seconds),
                ),
            )
            return cursor.rowcount


async def run_inbox_worker(
    inbox: MaxWebhookInbox,
    dispatch: Callable[[Any], Awaitable[None]],
    *,
    stop: asyncio.Event,
    idle_delay: float = 0.25,
) -> None:
    while not stop.is_set():
        item = inbox.claim_ready()
        if item is None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=idle_delay)
            except asyncio.TimeoutError:
                pass
            continue
        try:
            event = parse_update(item.payload)
            if event is not None:
                await dispatch(event)
            inbox.mark_done(item.event_id, item.claim_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("MAX inbox dispatch failed event=%s", item.event_id[:12])
            inbox.mark_failed(item.event_id, item.claim_id, exc)


async def run_inbox_maintenance(
    inbox: MaxWebhookInbox,
    *,
    stop: asyncio.Event,
    interval: float = 3600.0,
) -> None:
    while not stop.is_set():
        try:
            inbox.prune()
        except Exception:
            log.exception("MAX inbox retention cleanup failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(1.0, interval))
        except asyncio.TimeoutError:
            pass
