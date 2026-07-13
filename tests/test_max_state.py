"""Offline tests for durable MAX wizard state."""
from __future__ import annotations

import tempfile
import unittest
import sqlite3
from contextlib import closing
from pathlib import Path

from channels.max.state import MaxUserStateStore


class MaxUserStateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "max.db"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_state_survives_store_recreation_and_can_be_cleared(self) -> None:
        MaxUserStateStore(self.path).set("u1", "create_image")

        recreated = MaxUserStateStore(self.path)
        self.assertEqual(recreated.get("u1"), {"await": "create_image"})
        recreated.clear("u1")
        self.assertIsNone(MaxUserStateStore(self.path).get("u1"))

    def test_expired_state_is_removed_on_read(self) -> None:
        store = MaxUserStateStore(self.path, ttl_seconds=60)
        store.set("u1", "create_image")

        self.assertIsNone(store.get("u1", now=10**12))
        self.assertIsNone(store.get("u1"))

    def test_settings_survive_restart_and_corrupt_json_is_ignored(self) -> None:
        store = MaxUserStateStore(self.path)
        store.set("u1", "create_image", {"image_model": "nbpro", "count": 4})
        self.assertEqual(
            MaxUserStateStore(self.path).get("u1"),
            {"await": "create_image", "image_model": "nbpro", "count": 4},
        )
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute(
                "UPDATE max_user_state SET data_json='not-json' WHERE user_id='u1'"
            )
        self.assertEqual(store.get("u1"), {"await": "create_image"})

    def test_old_action_only_schema_is_migrated(self) -> None:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute(
                "CREATE TABLE max_user_state ("
                "user_id TEXT PRIMARY KEY, action TEXT NOT NULL, updated_at REAL NOT NULL)"
            )
            conn.execute(
                "INSERT INTO max_user_state VALUES ('legacy','edit_photo',9999999999)"
            )
        store = MaxUserStateStore(self.path)
        self.assertEqual(store.get("legacy"), {"await": "edit_photo"})
        store.set("legacy", "create_video", {"video_model": "veo-lite"})
        self.assertEqual(
            store.get("legacy"),
            {"await": "create_video", "video_model": "veo-lite"},
        )


if __name__ == "__main__":
    unittest.main()
