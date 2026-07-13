"""Offline tests for the durable MAX webhook inbox."""
from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from channels.max.inbox import (
    MaxWebhookInbox,
    event_id_for,
    partition_key_for,
    run_inbox_worker,
)


def _payload(text: str = "hello", chat_id: str = "c1") -> dict:
    return {
        "update_type": "message_created",
        "message": {
            "sender": {"user_id": "u1"},
            "recipient": {"chat_id": chat_id},
            "body": {"mid": f"m1-{text}", "text": text},
        },
    }


class MaxWebhookInboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "inbox.db"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_event_id_is_canonical_and_duplicates_are_ignored(self) -> None:
        self.assertEqual(event_id_for({"a": 1, "b": 2}), event_id_for({"b": 2, "a": 1}))
        inbox = MaxWebhookInbox(self.path)
        first_id, first = inbox.enqueue(_payload())
        second_id, second = inbox.enqueue(_payload())
        self.assertEqual(first_id, second_id)
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(inbox.stats(), {"pending": 1, "processed": 0, "dead": 0})

    def test_partition_key_prefers_chat_and_worker_keeps_chat_order(self) -> None:
        inbox = MaxWebhookInbox(self.path)
        inbox.enqueue(_payload("first", "same"))
        inbox.enqueue(_payload("second", "same"))
        inbox.enqueue(_payload("parallel", "other"))
        first = inbox.claim_ready()
        parallel = inbox.claim_ready()
        self.assertIsNotNone(first)
        self.assertIsNotNone(parallel)
        assert first is not None and parallel is not None
        self.assertEqual(partition_key_for(first.payload), "chat:same")
        self.assertEqual(partition_key_for(parallel.payload), "chat:other")
        inbox.mark_done(first.event_id, first.claim_id)
        second = inbox.claim_ready()
        self.assertIsNotNone(second)
        assert second is not None
        self.assertEqual(second.payload["message"]["body"]["text"], "second")

    def test_claim_is_exclusive_and_stale_owner_cannot_finish_new_claim(self) -> None:
        first = MaxWebhookInbox(self.path, claim_lease_seconds=2)
        second = MaxWebhookInbox(self.path, claim_lease_seconds=2)
        first.enqueue(_payload())
        original = first.claim_ready(now=100.0)
        self.assertIsNotNone(original)
        self.assertIsNone(second.claim_ready(now=101.0))
        replacement = second.claim_ready(now=103.0)
        self.assertIsNotNone(replacement)
        assert original is not None and replacement is not None
        first.mark_done(original.event_id, original.claim_id)
        self.assertEqual(first.stats()["processed"], 0)
        second.mark_done(replacement.event_id, replacement.claim_id)
        self.assertEqual(first.stats()["processed"], 1)

    def test_failure_retries_then_dead_letters_without_error_text(self) -> None:
        inbox = MaxWebhookInbox(self.path, max_attempts=2)
        inbox.enqueue(_payload())
        first = inbox.claim_ready()
        assert first is not None
        inbox.mark_failed(first.event_id, first.claim_id, RuntimeError("private token text"))
        retry = inbox.claim_ready(now=time.time() + 10)
        self.assertIsNotNone(retry)
        assert retry is not None
        self.assertEqual(retry.attempts, 1)
        inbox.mark_failed(retry.event_id, retry.claim_id, ValueError("private user text"))
        self.assertEqual(inbox.stats(), {"pending": 0, "processed": 0, "dead": 1})
        with closing(sqlite3.connect(self.path)) as conn:
            stored = conn.execute(
                "SELECT last_error FROM max_webhook_inbox WHERE event_id=?", (retry.event_id,)
            ).fetchone()[0]
        self.assertEqual(stored, "ValueError")

    def test_worker_dispatches_and_marks_event_done(self) -> None:
        async def scenario() -> None:
            inbox = MaxWebhookInbox(self.path)
            inbox.enqueue(_payload())
            stop = asyncio.Event()
            seen = []

            async def dispatch(event) -> None:
                seen.append(event)
                stop.set()

            await asyncio.wait_for(
                run_inbox_worker(inbox, dispatch, stop=stop, idle_delay=0.01),
                timeout=1.0,
            )
            self.assertEqual(len(seen), 1)
            self.assertEqual(inbox.stats(), {"pending": 0, "processed": 1, "dead": 0})

        asyncio.run(scenario())

    def test_prune_bounds_processed_and_dead_letter_retention(self) -> None:
        inbox = MaxWebhookInbox(self.path, max_attempts=1)
        inbox.enqueue(_payload("done"))
        done = inbox.claim_ready()
        assert done is not None
        inbox.mark_done(done.event_id, done.claim_id)
        inbox.enqueue(_payload("dead"))
        dead = inbox.claim_ready()
        assert dead is not None
        inbox.mark_failed(dead.event_id, dead.claim_id, RuntimeError("redacted"))
        removed = inbox.prune(
            now=time.time() + 31 * 24 * 3600,
            processed_retention_seconds=7 * 24 * 3600,
            dead_retention_seconds=30 * 24 * 3600,
        )
        self.assertEqual(removed, 2)
        self.assertEqual(inbox.stats(), {"pending": 0, "processed": 0, "dead": 0})


if __name__ == "__main__":
    unittest.main()
