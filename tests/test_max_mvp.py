from __future__ import annotations

import asyncio
import inspect
import tempfile
import unittest
from pathlib import Path

import metrics
from channels.max import handler, webhook
from channels.max.handler import (
    CB_ANIMATE,
    CB_BALANCE,
    CB_CREATE_IMAGE,
    CB_TOPUP,
    MaxCopy,
    MaxMvpBot,
    MaxMvpConfig,
    MetricsWallet,
)


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class FakePlatform:
    """Records outbound calls instead of hitting the MAX API."""

    name = "max"

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.answers: list[dict] = []

    async def send_message(self, chat_id, text, keyboard=None):
        self.messages.append({"chat_id": chat_id, "text": text, "keyboard": keyboard})
        return {"ok": True}

    async def answer_callback(self, callback_id, text=None):
        self.answers.append({"callback_id": callback_id, "text": text})
        return {"ok": True}

    @property
    def last_text(self) -> str:
        return self.messages[-1]["text"] if self.messages else ""

    @property
    def last_keyboard(self):
        return self.messages[-1]["keyboard"] if self.messages else None


class FakeService:
    """Deterministic generation service with no network access."""

    def __init__(self, *, result=None, error=None, raises=False):
        self.result = result if result is not None else {"images": [{"url": "https://img/1.png"}]}
        self.error = error
        self.raises = raises
        self.calls: list[dict] = []

    async def _run(self, kind, **kwargs):
        self.calls.append({"kind": kind, **kwargs})
        if self.raises:
            raise RuntimeError("boom")
        if self.error:
            return {"error": self.error}
        return self.result

    async def create_image(self, *, internal_user_id, prompt):
        return await self._run("create_image", internal_user_id=internal_user_id, prompt=prompt)

    async def edit_photo(self, *, internal_user_id, prompt, photo_file_id):
        return await self._run(
            "edit_photo", internal_user_id=internal_user_id, prompt=prompt, photo_file_id=photo_file_id
        )

    async def animate_photo(self, *, internal_user_id, prompt, photo_file_id):
        return await self._run(
            "animate_photo", internal_user_id=internal_user_id, prompt=prompt, photo_file_id=photo_file_id
        )


def _msg(text="", user_id="u1", chat_id="c1", photos=()):
    return webhook.parse_update(
        {
            "update_type": "message_created",
            "message": {
                "id": "m1",
                "chat_id": chat_id,
                "text": text,
                "sender": {"user_id": user_id},
                "attachments": [
                    {"type": "image", "payload": {"file_id": fid}} for fid in photos
                ],
            },
        }
    )


def _cb(data, user_id="u1", chat_id="c1"):
    return webhook.parse_update(
        {
            "update_type": "callback",
            "callback": {
                "chat_id": chat_id,
                "message_id": "m1",
                "callback_id": "cbid-1",
                "payload": data,
                "user": {"id": user_id},
            },
        }
    )


class MaxMvpTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        metrics.close()
        metrics.init_db(str(Path(self._tmp.name) / "metrics.db"))
        self.platform = FakePlatform()
        self.service = FakeService()
        self.config = MaxMvpConfig(
            starter_credits=30, image_price=10, edit_price=8, animate_price=100
        )
        self.wallet = MetricsWallet(self.config.starter_credits)

    def tearDown(self) -> None:
        metrics.close()
        self._tmp.cleanup()

    def _bot(self):
        return MaxMvpBot(
            platform=self.platform,
            service=self.service,
            config=self.config,
            wallet=self.wallet,
        )

    def _balance(self, user_id="u1") -> int:
        return metrics.credits_balance_for_identity("max", user_id, self.config.starter_credits)

    # -- menu / start -----------------------------------------------------

    def test_start_shows_menu_with_all_actions(self) -> None:
        bot = self._bot()
        run(bot.handle(_msg("/start")))

        kb = self.platform.last_keyboard
        payloads = {b.callback_data for row in kb.rows for b in row}
        self.assertIn(CB_CREATE_IMAGE, payloads)
        self.assertIn(CB_ANIMATE, payloads)
        self.assertIn(CB_BALANCE, payloads)
        self.assertIn(CB_TOPUP, payloads)

    def test_callback_is_answered(self) -> None:
        bot = self._bot()
        run(bot.handle(_cb(CB_BALANCE)))

        self.assertEqual(self.platform.answers[-1]["callback_id"], "cbid-1")

    # -- create image -----------------------------------------------------

    def test_create_image_happy_path_charges_and_delivers(self) -> None:
        bot = self._bot()
        run(bot.handle(_cb(CB_CREATE_IMAGE)))
        run(bot.handle(_msg("рыжий кот в шляпе")))

        self.assertEqual(self._balance(), 20)  # 30 starter - 10
        self.assertEqual(self.service.calls[-1]["kind"], "create_image")
        self.assertIn("https://img/1.png", self.platform.last_text)

    def test_create_image_failure_refunds(self) -> None:
        self.service.error = "generation failed"
        bot = self._bot()
        run(bot.handle(_cb(CB_CREATE_IMAGE)))
        run(bot.handle(_msg("рыжий кот в шляпе")))

        self.assertEqual(self._balance(), 30)  # charged then refunded
        self.assertIn("возвращены", self.platform.last_text)

    def test_create_image_exception_refunds(self) -> None:
        self.service.raises = True
        bot = self._bot()
        run(bot.handle(_cb(CB_CREATE_IMAGE)))
        run(bot.handle(_msg("рыжий кот в шляпе")))

        self.assertEqual(self._balance(), 30)

    def test_short_prompt_does_not_charge(self) -> None:
        bot = self._bot()
        run(bot.handle(_cb(CB_CREATE_IMAGE)))
        run(bot.handle(_msg("ok")))

        self.assertEqual(self._balance(), 30)
        self.assertEqual(self.service.calls, [])

    def test_low_balance_blocks_generation(self) -> None:
        # Drain the starter grant to 0.
        metrics.credits_charge_for_identity("max", "u1", 30, self.config.starter_credits)
        bot = self._bot()
        run(bot.handle(_cb(CB_CREATE_IMAGE)))
        run(bot.handle(_msg("рыжий кот в шляпе")))

        self.assertEqual(self.service.calls, [])
        self.assertIn("Недостаточно", self.platform.last_text)

    # -- photo flows ------------------------------------------------------

    def test_animate_photo_charges_animate_price(self) -> None:
        bot = self._bot()
        # top up so animate (100) is affordable
        metrics.credits_add_for_identity("max", "u1", 100, self.config.starter_credits)
        run(bot.handle(_cb(CB_ANIMATE)))
        run(bot.handle(_msg("оживи", photos=("photo-1",))))

        self.assertEqual(self.service.calls[-1]["kind"], "animate_photo")
        self.assertEqual(self.service.calls[-1]["photo_file_id"], "photo-1")
        self.assertEqual(self._balance(), 30)  # 130 - 100

    def test_edit_photo_without_photo_asks_for_photo(self) -> None:
        bot = self._bot()
        run(bot.handle(_cb(handler.CB_EDIT_PHOTO)))
        run(bot.handle(_msg("сделай ярче")))  # no photo attached

        self.assertEqual(self.service.calls, [])
        self.assertEqual(self._balance(), 30)
        self.assertIn("фото", self.platform.last_text.lower())

    # -- balance / topup --------------------------------------------------

    def test_balance_reports_starter_grant(self) -> None:
        bot = self._bot()
        run(bot.handle(_cb(CB_BALANCE)))

        self.assertIn("30", self.platform.last_text)

    def test_topup_shows_external_link(self) -> None:
        bot = self._bot()
        run(bot.handle(_cb(CB_TOPUP)))

        kb = self.platform.last_keyboard
        urls = [b.url for row in kb.rows for b in row if b.url]
        self.assertIn(self.config.topup_url, urls)

    # -- identity separation ----------------------------------------------

    def test_max_balance_does_not_leak_into_telegram(self) -> None:
        bot = self._bot()
        run(bot.handle(_cb(CB_CREATE_IMAGE)))
        run(bot.handle(_msg("рыжий кот в шляпе", user_id="42")))

        # MAX user "42" charged, but Telegram legacy id 42 is untouched.
        max_internal = metrics.ensure_user_identity("max", "42")
        self.assertLess(max_internal, 0)
        tg_row = metrics._conn().execute(
            "SELECT balance FROM credits WHERE user_id=42"
        ).fetchone()
        self.assertIsNone(tg_row)

    # -- purity guard -----------------------------------------------------

    def test_handler_does_not_import_aiogram_or_flow_bot(self) -> None:
        src = inspect.getsource(handler)
        self.assertNotIn("aiogram", src)
        self.assertNotIn("flow_bot", src)


if __name__ == "__main__":
    unittest.main()
