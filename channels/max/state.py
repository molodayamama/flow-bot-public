"""Durable minimal wizard state for MAX users."""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class MaxUserStateStore:
    def __init__(
        self,
        path: str | Path,
        *,
        ttl_seconds: float = 24 * 3600,
    ) -> None:
        self.path = Path(path)
        self.ttl_seconds = max(60.0, float(ttl_seconds))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS max_user_state (
                    user_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS max_user_state_updated ON max_user_state(updated_at)"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5.0)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            with conn:
                yield conn
        finally:
            conn.close()

    def get(self, user_id: str, *, now: float | None = None) -> dict | None:
        current = time.time() if now is None else float(now)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT action,updated_at FROM max_user_state WHERE user_id=?",
                (str(user_id),),
            ).fetchone()
            if row is None:
                return None
            if float(row[1]) < current - self.ttl_seconds:
                conn.execute("DELETE FROM max_user_state WHERE user_id=?", (str(user_id),))
                return None
            return {"await": str(row[0])}

    def set(self, user_id: str, action: str) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM max_user_state WHERE updated_at<?",
                (now - self.ttl_seconds,),
            )
            conn.execute(
                """INSERT INTO max_user_state(user_id,action,updated_at) VALUES(?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       action=excluded.action,updated_at=excluded.updated_at""",
                (str(user_id), str(action), now),
            )

    def clear(self, user_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM max_user_state WHERE user_id=?", (str(user_id),))
