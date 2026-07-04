from __future__ import annotations

import unittest
from types import SimpleNamespace

from channels.telegram.marketplace_stale import MarketplaceStale, MarketplaceStaleDeps


class MarketplaceStaleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.state: dict[int, dict] = {}
        self.guard = MarketplaceStale(MarketplaceStaleDeps(
            workspace=lambda user_id: self.state.setdefault(user_id, {}),
            stale_text="old screen",
        ))

    def test_message_id_is_best_effort_int(self) -> None:
        self.assertEqual(self.guard.message_id(SimpleNamespace(message_id="42")), 42)
        self.assertEqual(self.guard.message_id(SimpleNamespace(message_id="bad")), 0)
        self.assertEqual(self.guard.message_id(None), 0)

    def test_stamp_and_stale_detection_use_active_message_id(self) -> None:
        self.guard.stamp_message(7, SimpleNamespace(message_id=100))

        stale = SimpleNamespace(message=SimpleNamespace(message_id=99))
        current = SimpleNamespace(message=SimpleNamespace(message_id=100))
        unknown = SimpleNamespace(message=SimpleNamespace(message_id=0))

        self.assertEqual(self.state[7]["mp_active_msg_id"], 100)
        self.assertTrue(self.guard.is_stale_callback(7, stale))
        self.assertFalse(self.guard.is_stale_callback(7, current))
        self.assertFalse(self.guard.is_stale_callback(7, unknown))

    async def test_reject_stale_callback_alerts_and_clears_markup(self) -> None:
        calls = []

        async def answer(*args, **kwargs):
            calls.append(("answer", args, kwargs))

        async def edit_reply_markup(*args, **kwargs):
            calls.append(("edit", args, kwargs))

        callback = SimpleNamespace(
            answer=answer,
            message=SimpleNamespace(edit_reply_markup=edit_reply_markup),
        )

        await self.guard.reject_stale_callback(callback)

        self.assertEqual(calls[0], ("answer", ("old screen",), {"show_alert": True}))
        self.assertEqual(calls[1], ("edit", (), {"reply_markup": None}))
