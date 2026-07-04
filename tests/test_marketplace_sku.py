"""Tests for channels.telegram.marketplace_sku."""

from __future__ import annotations

import asyncio
import unittest

from aiogram import types

from channels.telegram.marketplace_sku import MarketplaceSku, MarketplaceSkuDeps


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Metrics:
    def __init__(self):
        self.skus = ["SKU-1", "SKU-2"]
        self.gallery = [{"file_id": "file", "token": "tok", "prompt": "prompt"}]
        self.saved = []
        self.events = []
        self.row_id = 1

    def recent_seller_skus(self, user_id, *, limit):
        return self.skus[:limit]

    def get_gallery(self, user_id, *, limit):
        return self.gallery[:limit]

    def save_seller_sku_item(self, *args, **kwargs):
        self.saved.append((args, kwargs))
        return self.row_id

    def log_event(self, *args, **kwargs):
        self.events.append((args, kwargs))


class _Message:
    def __init__(self):
        self.answers = []

    async def answer(self, text, **kw):
        self.answers.append((text, kw))


def _subject(*, workspace=None, metrics=None):
    workspace = workspace if workspace is not None else {}
    metrics = metrics or _Metrics()
    deps = MarketplaceSkuDeps(
        workspace=lambda uid: workspace.setdefault(uid, {}),
        metrics=metrics,
        menu_button=lambda text_key, data: types.InlineKeyboardButton(
            text=text_key, callback_data=data
        ),
    )
    return MarketplaceSku(deps), workspace, metrics


class MarketplaceSkuTests(unittest.TestCase):
    def test_choice_kb_stores_recent_choices(self):
        sku, workspace, metrics = _subject()
        kb = sku.choice_kb(7)
        self.assertEqual(workspace[7]["mp_sku_choices"], ["SKU-1", "SKU-2"])
        self.assertEqual(kb.inline_keyboard[0][0].callback_data, "mp:sku:0")
        self.assertEqual(kb.inline_keyboard[-1][0].callback_data, "m:menu")

    def test_latest_payload_uses_gallery_and_platform_override(self):
        sku, workspace, metrics = _subject(workspace={7: {"mp_platform": "wb"}})
        self.assertEqual(
            sku.latest_payload(7, platform="ozon"),
            {"file_id": "file", "token": "tok", "prompt": "prompt", "platform": "ozon"},
        )

    def test_save_pending_item_missing_payload_clears_pending(self):
        sku, workspace, metrics = _subject(workspace={7: {"mp_sku_pending": "bad"}})
        msg = _Message()
        self.assertFalse(run(sku.save_pending_item(msg, 7, "SKU")))
        self.assertNotIn("mp_sku_pending", workspace[7])
        self.assertTrue(msg.answers)

    def test_save_pending_item_saves_and_clears_state(self):
        sku, workspace, metrics = _subject(workspace={7: {
            "mp_sku_pending": {
                "file_id": "file",
                "token": "tok",
                "prompt": "prompt",
                "platform": "wb",
            },
            "mp_sku_choices": ["SKU"],
            "await": "mp_sku_name",
        }})
        msg = _Message()
        self.assertTrue(run(sku.save_pending_item(msg, 7, "SKU <A>")))
        self.assertEqual(metrics.saved[0][0][:2], (7, "SKU <A>"))
        self.assertEqual(metrics.events[0][0][0], "mp_sku_saved")
        self.assertNotIn("mp_sku_pending", workspace[7])
        self.assertNotIn("mp_sku_choices", workspace[7])
        self.assertIsNone(workspace[7]["await"])
        self.assertEqual(msg.answers[-1][1]["parse_mode"], "HTML")
        self.assertIn("&lt;A&gt;", msg.answers[-1][0])


if __name__ == "__main__":
    unittest.main()
