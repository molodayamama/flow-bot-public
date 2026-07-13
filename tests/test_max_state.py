"""Offline tests for durable MAX wizard state."""
from __future__ import annotations

import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
