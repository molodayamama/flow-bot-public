"""Unit tests for channels.telegram.image_delivery (fakes, no network)."""

from __future__ import annotations

import asyncio
import unittest

from channels.telegram.image_delivery import ImageDelivery, ImageDeliveryDeps


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Keeper:
    def __init__(self):
        self.noted = []

    def note_image(self, img):
        self.noted.append(img)


class _Registry:
    def __init__(self):
        self.added = []

    def add(self, ref):
        self.added.append(ref)
        return f"tok{len(self.added)}"


class _Metrics:
    def __init__(self):
        self.gallery = []

    def save_to_gallery(self, user_id, file_id, *, token, prompt):
        self.gallery.append((user_id, file_id, token, prompt))


class _NullLog:
    def error(self, *a, **k):
        pass


class _SentPhoto:
    def __init__(self):
        self.photo = [type("P", (), {"file_id": "fid"})()]


class _Message:
    def __init__(self):
        self.sent = []

    async def reply_photo(self, *, photo, caption, reply_markup, parse_mode):
        self.sent.append({"photo": photo, "caption": caption, "kb": reply_markup, "pm": parse_mode})
        return _SentPhoto()


def _deps(is_seller=False, keeper=None, registry=None, metrics=None):
    return ImageDeliveryDeps(
        keeper_for_acc=lambda _acc: keeper or _Keeper(),
        image_registry=registry or _Registry(),
        workspace=lambda _uid: {"mp_platform": "wb"},
        is_seller=lambda: is_seller,
        seller_image_keyboard=lambda tok: f"seller-kb:{tok}",
        image_keyboard=lambda tok: f"kb:{tok}",
        metrics=metrics or _Metrics(),
        log=_NullLog(),
        referral_link=lambda uid: f"https://t.me/bot?start=r{uid}",
        bot_username=lambda: "photozhab_bot",
    )


class SendOneImageTests(unittest.TestCase):
    def test_consumer_uses_plain_keyboard_and_saves_gallery(self):
        keeper, reg, metrics, msg = _Keeper(), _Registry(), _Metrics(), _Message()
        d = ImageDelivery(_deps(is_seller=False, keeper=keeper, registry=reg, metrics=metrics))
        run(d.send_one_image(
            msg, url="http://x/i.png", img={"mediaId": "m"}, index=1, total=1,
            caption="cap", user_id=7, project_id="p", prompt="cat", aspect_ratio="square",
        ))
        self.assertEqual(msg.sent[0]["kb"], "kb:tok1")
        self.assertEqual(keeper.noted, [{"mediaId": "m"}])
        self.assertEqual(metrics.gallery[0][0], 7)
        # consumer mode → ImageRef.platform stays empty
        self.assertEqual(reg.added[0].platform, "")

    def test_seller_uses_seller_keyboard_and_platform(self):
        reg, msg = _Registry(), _Message()
        d = ImageDelivery(_deps(is_seller=True, registry=reg))
        run(d.send_one_image(
            msg, url="http://x/i.png", img={}, index=1, total=1, caption="c",
            user_id=1, project_id=None, prompt="p", aspect_ratio="square",
        ))
        self.assertEqual(msg.sent[0]["kb"], "seller-kb:tok1")
        self.assertEqual(reg.added[0].platform, "wb")


class SendResultPairsTests(unittest.TestCase):
    def test_referral_footer_and_counter(self):
        msg = _Message()
        d = ImageDelivery(_deps())
        pairs = [("http://x/1.png", {"a": 1}), ("http://x/2.png", {"b": 2})]
        run(d.send_result_pairs(
            msg, pairs, user_id=9, project_id="p", prompt="dog", aspect_ratio="square", emoji="🐶",
        ))
        self.assertEqual(len(msg.sent), 2)
        self.assertIn("1/2", msg.sent[0]["caption"])
        self.assertIn("photozhab_bot", msg.sent[0]["caption"])

    def test_no_bot_username_omits_footer(self):
        deps = _deps()
        object.__setattr__(deps, "bot_username", lambda: "")
        msg = _Message()
        d = ImageDelivery(deps)
        run(d.send_result_pairs(
            msg, [("u", {})], user_id=1, project_id=None, prompt="p", aspect_ratio="square", emoji="✨",
        ))
        self.assertNotIn("Создай своё", msg.sent[0]["caption"])


if __name__ == "__main__":
    unittest.main()
