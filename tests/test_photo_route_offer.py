"""Tests for channels.telegram.photo_route_offer."""

from __future__ import annotations

import asyncio
import unittest

from aiogram import types

from channels.telegram.photo_route_offer import PhotoRouteOffer, PhotoRouteOfferDeps


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Message:
    def __init__(self):
        self.photo = [type("P", (), {"file_id": "small"})(), type("P", (), {"file_id": "big"})()]
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


def _subject():
    pending = {}
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="image", callback_data="pr:img")]
    ])
    deps = PhotoRouteOfferDeps(
        pending_photo_routes=pending,
        photo_route_kb=lambda: kb,
    )
    return PhotoRouteOffer(deps), pending, kb


class PhotoRouteOfferTests(unittest.TestCase):
    def test_store_trims_caption_and_keeps_latest_photo_file_id(self):
        offer, pending, kb = _subject()
        offer.store(7, file_id="f", caption="  " + ("x" * 2100))
        self.assertEqual(pending[7]["file_id"], "f")
        self.assertEqual(len(pending[7]["caption"]), 2000)
        self.assertFalse(pending[7]["caption"].startswith(" "))

    def test_offer_saves_snapshot_and_answers_with_html_keyboard(self):
        offer, pending, kb = _subject()
        msg = _Message()
        run(offer.offer(msg, user_id=7, caption="<cat>"))
        self.assertEqual(pending[7], {"file_id": "big", "caption": "<cat>"})
        self.assertIs(msg.answers[0][1]["reply_markup"], kb)
        self.assertEqual(msg.answers[0][1]["parse_mode"], "HTML")
        self.assertIn("&lt;cat&gt;", msg.answers[0][0])


if __name__ == "__main__":
    unittest.main()
