from __future__ import annotations

import asyncio
import base64
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import metrics
import prompts_lib
from channels.max import handler, webhook
from channels.max.handler import (
    CB_ANIMATE,
    CB_BALANCE,
    CB_CREATE_IMAGE,
    CB_CREATE_VIDEO,
    CB_GALLERY,
    CB_HISTORY,
    CB_IDEAS,
    CB_INVITE,
    CB_MY_PHOTO,
    CB_PROFILE,
    CB_RUN_READY,
    CB_SUPPORT,
    CB_SUPPORT_NEW,
    CB_SUPPORT_MY,
    CB_TOPUP,
    CB_VIDEO_TEXT,
    CB_VIDEO_FRAMES,
    CB_VIDEO_INGREDIENTS,
    MaxCopy,
    MaxMvpBot,
    MaxMvpConfig,
    MetricsWallet,
)
from channels.max.state import MaxUserStateStore


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class FakePlatform:
    """Records outbound calls instead of hitting the MAX API."""

    name = "max"

    def __init__(
        self,
        *,
        photo_error: Exception | None = None,
        answer_error: Exception | None = None,
    ) -> None:
        self.messages: list[dict] = []
        self.answers: list[dict] = []
        self.photos: list[dict] = []
        self.videos: list[dict] = []
        self.documents: list[dict] = []
        self.edits: list[dict] = []
        self.photo_error = photo_error
        self.answer_error = answer_error

    async def send_message(self, chat_id, text, keyboard=None):
        self.messages.append({"chat_id": chat_id, "text": text, "keyboard": keyboard})
        return {"ok": True}

    async def edit_message(self, chat_id, message_id, text, keyboard=None):
        item = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "keyboard": keyboard,
            "edited": True,
        }
        self.edits.append(item)
        self.messages.append(item)
        return {"ok": True}

    async def answer_callback(self, callback_id, text=None):
        if self.answer_error is not None:
            raise self.answer_error
        self.answers.append({"callback_id": callback_id, "text": text})
        return {"ok": True}

    async def send_photo(self, chat_id, media, keyboard=None):
        if self.photo_error is not None:
            raise self.photo_error
        self.photos.append({"chat_id": chat_id, "media": media, "keyboard": keyboard})
        return {"ok": True}

    async def send_video(self, chat_id, media, keyboard=None):
        self.videos.append({"chat_id": chat_id, "media": media, "keyboard": keyboard})
        return {"ok": True}

    async def send_document(self, chat_id, media, keyboard=None):
        self.documents.append({"chat_id": chat_id, "media": media, "keyboard": keyboard})
        return {"ok": True}

    async def get_file_bytes(self, file):
        return b""

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

    async def create_image(self, *, internal_user_id, prompt, **settings):
        return await self._run(
            "create_image", internal_user_id=internal_user_id, prompt=prompt, **settings
        )

    async def edit_photo(self, *, internal_user_id, prompt, photo_file_id, **settings):
        return await self._run(
            "edit_photo", internal_user_id=internal_user_id, prompt=prompt,
            photo_file_id=photo_file_id, **settings
        )

    async def animate_photo(self, *, internal_user_id, prompt, photo_file_id, **settings):
        return await self._run(
            "animate_photo", internal_user_id=internal_user_id, prompt=prompt,
            photo_file_id=photo_file_id, **settings
        )

    async def create_video(self, *, internal_user_id, prompt, **settings):
        return await self._run(
            "create_video", internal_user_id=internal_user_id, prompt=prompt, **settings
        )

    async def create_video_ingredients(
        self, *, internal_user_id, prompt, photo_file_ids, **settings
    ):
        return await self._run(
            "video_ingredients", internal_user_id=internal_user_id, prompt=prompt,
            photo_file_ids=photo_file_ids, **settings
        )

    async def create_video_frames(
        self, *, internal_user_id, prompt, photo_file_ids, **settings
    ):
        return await self._run(
            "video_frames", internal_user_id=internal_user_id, prompt=prompt,
            photo_file_ids=photo_file_ids, **settings
        )


def _msg(text="", user_id="u1", chat_id="c1", photos=(), username=None, first_name=None):
    return webhook.parse_update(
        {
            "update_type": "message_created",
            "message": {
                "sender": {
                    "user_id": user_id,
                    "username": username,
                    "first_name": first_name,
                },
                "recipient": {"chat_id": chat_id, "chat_type": "dialog", "user_id": user_id},
                "body": {
                    "mid": "m1",
                    "text": text,
                    "attachments": [
                        {"type": "image", "payload": {"url": fid}} for fid in photos
                    ],
                },
            },
        }
    )


def _cb(data, user_id="u1", chat_id="c1"):
    return webhook.parse_update(
        {
            "update_type": "message_callback",
            "chat_id": chat_id,
            "message_id": "m1",
            "callback": {
                "callback_id": "cbid-1",
                "payload": data,
                "user": {"user_id": user_id},
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
        self.assertIn(CB_CREATE_VIDEO, payloads)
        self.assertIn(CB_ANIMATE, payloads)
        self.assertIn(CB_BALANCE, payloads)
        self.assertIn(CB_MY_PHOTO, payloads)
        self.assertIn(CB_IDEAS, payloads)
        self.assertIn(CB_PROFILE, payloads)
        self.assertIn(CB_INVITE, payloads)
        self.assertNotIn(CB_VIDEO_INGREDIENTS, payloads)
        self.assertNotIn(CB_VIDEO_FRAMES, payloads)

    def test_max_interaction_projects_profile_into_shared_admin_users(self) -> None:
        bot = self._bot()
        run(bot.handle(_msg(
            "/start", user_id="max-profile", username="max_user", first_name="Максим"
        )))

        internal_id = metrics.ensure_user_identity("max", "max-profile")
        profile = metrics.get_user_profile(internal_id)
        detail = metrics.get_admin_user_detail(internal_id)
        self.assertEqual(profile["username"], "max_user")
        self.assertEqual(profile["first_name"], "Максим")
        self.assertEqual(profile["acq_channel"], "max")
        self.assertEqual(detail["identity"]["platform"], "max")

    def test_callback_is_answered(self) -> None:
        bot = self._bot()
        run(bot.handle(_cb(CB_BALANCE)))

        self.assertEqual(self.platform.answers[-1]["callback_id"], "cbid-1")

    def test_callback_ack_failure_does_not_replay_completed_action(self) -> None:
        platform = FakePlatform(answer_error=RuntimeError("expired callback"))
        bot = MaxMvpBot(
            platform=platform,
            service=self.service,
            config=self.config,
            wallet=self.wallet,
        )

        run(bot.handle(_cb(CB_CREATE_IMAGE)))

        self.assertEqual(len(platform.messages), 1)
        self.assertEqual(platform.answers, [])

    def test_callback_navigation_edits_the_existing_screen(self) -> None:
        bot = self._bot()

        run(bot.handle(_cb(CB_BALANCE)))

        self.assertEqual(len(self.platform.edits), 1)
        self.assertEqual(self.platform.edits[0]["message_id"], "m1")
        self.assertIn("30", self.platform.edits[0]["text"])

    def test_video_modes_are_grouped_below_the_consumer_menu(self) -> None:
        bot = self._bot()

        run(bot.handle(_cb(CB_CREATE_VIDEO)))

        payloads = {
            button.callback_data
            for row in self.platform.last_keyboard.rows
            for button in row
        }
        self.assertIn(CB_VIDEO_TEXT, payloads)
        self.assertIn(CB_VIDEO_INGREDIENTS, payloads)
        self.assertIn(CB_VIDEO_FRAMES, payloads)

    def test_photo_without_active_flow_offers_real_image_video_route(self) -> None:
        state = {}
        bot = MaxMvpBot(
            platform=self.platform, service=self.service, config=self.config,
            wallet=self.wallet, state=state,
        )

        run(bot.handle(_msg("сделай ярче", photos=("https://cdn/photo.png",))))
        route_payloads = {
            button.callback_data
            for row in self.platform.last_keyboard.rows
            for button in row
        }
        self.assertEqual(state["u1"]["await"], "photo_route")
        self.assertIn("pr:img", route_payloads)
        self.assertIn("pr:vid", route_payloads)

        run(bot.handle(_cb("pr:img")))
        self.assertEqual(state["u1"]["await"], "ready_image")
        self.assertEqual(self.service.calls, [])
        run(bot.handle(_cb(CB_RUN_READY)))

        self.assertEqual(self.service.calls[-1]["kind"], "edit_photo")
        self.assertEqual(
            self.service.calls[-1]["photo_file_id"], "https://cdn/photo.png"
        )

    def test_quick_idea_opens_confirmation_before_paid_generation(self) -> None:
        state = {}
        bot = MaxMvpBot(
            platform=self.platform, service=self.service, config=self.config,
            wallet=self.wallet, state=state,
        )

        run(bot.handle(_cb(CB_IDEAS)))
        run(bot.handle(_cb("idea:0")))

        self.assertEqual(state["u1"]["await"], "ready_image")
        self.assertEqual(self.service.calls, [])
        self.assertEqual(self._balance(), 30)
        run(bot.handle(_cb(CB_RUN_READY)))
        self.assertEqual(self.service.calls[-1]["kind"], "create_image")
        self.assertEqual(self._balance(), 20)

    def test_template_and_guided_idea_buttons_reach_ready_wizard(self) -> None:
        state = {}
        bot = MaxMvpBot(
            platform=self.platform, service=self.service, config=self.config,
            wallet=self.wallet, state=state,
        )

        run(bot.handle(_cb(CB_IDEAS)))
        run(bot.handle(_cb("ih:templates")))
        run(bot.handle(_cb("tp:pick:0")))
        run(bot.handle(_msg("рыжий кот")))
        run(bot.handle(_cb("tp:ans:0")))
        run(bot.handle(_cb("tp:ans:0")))
        run(bot.handle(_cb("tp:skip")))

        self.assertEqual(state["u1"]["await"], "ready_video")
        self.assertEqual(state["u1"]["ready_action"], "create_video")
        self.assertGreater(len(state["u1"]["prompt"]), 10)
        self.assertEqual(self.service.calls, [])

        run(bot.handle(_cb(CB_IDEAS)))
        run(bot.handle(_cb("ih:guided")))
        for _ in range(len(prompts_lib.guided_steps())):
            run(bot.handle(_cb("gp:opt:0")))
        self.assertIn(state["u1"]["await"], {"ready_image", "ready_video"})
        self.assertGreater(len(state["u1"]["prompt"]), 10)

    # -- create image -----------------------------------------------------

    def test_create_image_happy_path_charges_and_delivers(self) -> None:
        state = {}
        bot = MaxMvpBot(
            platform=self.platform,
            service=self.service,
            config=self.config,
            wallet=self.wallet,
            state=state,
        )
        run(bot.handle(_cb(CB_CREATE_IMAGE)))
        run(bot.handle(_msg("рыжий кот в шляпе")))
        run(bot.handle(_cb(CB_RUN_READY)))

        self.assertEqual(self._balance(), 20)  # 30 starter - 10
        self.assertEqual(self.service.calls[-1]["kind"], "create_image")
        # Delivery goes through the media API, not a text message with a URL.
        self.assertEqual(len(self.platform.photos), 1)
        photo = self.platform.photos[-1]
        self.assertEqual(photo["media"].kind, "photo")
        self.assertEqual(photo["media"].url, "https://img/1.png")
        self.assertIsNotNone(photo["keyboard"])
        self.assertNotIn("https://img/1.png", self.platform.last_text)
        self.assertEqual(state["u1"]["await"], "idle")
        self.assertEqual(state["u1"]["last_images"], ["https://img/1.png"])
        self.assertEqual(state["u1"]["last_job"]["prompt"], "рыжий кот в шляпе")

    def test_pending_action_survives_bot_recreation(self) -> None:
        state_store = MaxUserStateStore(Path(self._tmp.name) / "max-state.db")
        first = MaxMvpBot(
            platform=self.platform,
            service=self.service,
            config=self.config,
            wallet=self.wallet,
            state_store=state_store,
        )
        run(first.handle(_cb(CB_CREATE_IMAGE)))

        recreated = MaxMvpBot(
            platform=self.platform,
            service=self.service,
            config=self.config,
            wallet=self.wallet,
            state_store=MaxUserStateStore(Path(self._tmp.name) / "max-state.db"),
        )
        run(recreated.handle(_msg("durable prompt")))
        run(recreated.handle(_cb(CB_RUN_READY)))

        self.assertEqual(self.service.calls[-1]["prompt"], "durable prompt")
        self.assertEqual(self._balance(), 20)

    def test_delivery_exception_refunds_and_keeps_pending_action(self) -> None:
        platform = FakePlatform(photo_error=RuntimeError("delivery failed"))
        state = {}
        bot = MaxMvpBot(
            platform=platform,
            service=self.service,
            config=self.config,
            wallet=self.wallet,
            state=state,
        )
        run(bot.handle(_cb(CB_CREATE_IMAGE)))

        run(bot.handle(_msg("deliver this prompt")))
        with self.assertRaises(RuntimeError):
            run(bot.handle(_cb(CB_RUN_READY)))

        self.assertEqual(self._balance(), 30)
        self.assertEqual(state["u1"]["await"], "ready_image")
        self.assertEqual(state["u1"]["image_model"], "nb2")

    def test_create_image_failure_refunds(self) -> None:
        self.service.error = "generation failed"
        bot = self._bot()
        run(bot.handle(_cb(CB_CREATE_IMAGE)))
        run(bot.handle(_msg("рыжий кот в шляпе")))
        run(bot.handle(_cb(CB_RUN_READY)))

        self.assertEqual(self._balance(), 30)  # charged then refunded
        self.assertIn("возвращены", self.platform.last_text)

    def test_create_image_exception_refunds(self) -> None:
        self.service.raises = True
        bot = self._bot()
        run(bot.handle(_cb(CB_CREATE_IMAGE)))
        run(bot.handle(_msg("рыжий кот в шляпе")))
        run(bot.handle(_cb(CB_RUN_READY)))

        self.assertEqual(self._balance(), 30)

    def test_empty_generation_result_refunds_instead_of_claiming_success(self) -> None:
        self.service.result = {"images": []}
        bot = self._bot()

        run(bot.handle(_cb(CB_CREATE_IMAGE)))
        run(bot.handle(_msg("пустой результат")))
        run(bot.handle(_cb(CB_RUN_READY)))

        self.assertEqual(self.platform.photos, [])
        self.assertEqual(self._balance(), 30)
        self.assertIn("возвращены", self.platform.last_text)

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
        run(bot.handle(_cb(CB_RUN_READY)))

        self.assertEqual(self.service.calls, [])
        self.assertIn("Недостаточно", self.platform.last_text)

    def test_image_settings_are_persisted_priced_and_forwarded(self) -> None:
        state_store = MaxUserStateStore(Path(self._tmp.name) / "settings.db")
        bot = MaxMvpBot(
            platform=self.platform,
            service=self.service,
            config=self.config,
            wallet=self.wallet,
            state_store=state_store,
        )
        metrics.credits_add_for_identity("max", "u1", 100, self.config.starter_credits)

        run(bot.handle(_cb(CB_CREATE_IMAGE)))
        run(bot.handle(_cb("s:im:nbpro")))
        run(bot.handle(_cb("s:ia:landscape_43")))
        run(bot.handle(_cb("s:ic:3")))

        restored = MaxUserStateStore(Path(self._tmp.name) / "settings.db").get("u1")
        self.assertEqual(restored["image_model"], "nbpro")
        self.assertEqual(restored["aspect_ratio"], "landscape_43")
        self.assertEqual(restored["count"], 3)
        run(bot.handle(_msg("три рыжих кота")))
        run(bot.handle(_cb(CB_RUN_READY)))

        call = self.service.calls[-1]
        self.assertEqual(call["image_model"], "nbpro")
        self.assertEqual(call["aspect_ratio"], "landscape_43")
        self.assertEqual(call["count"], 3)
        self.assertEqual(self._balance(), 85)  # 130 - (10 + 5) * 3

    def test_unknown_setting_callback_does_not_mutate_state(self) -> None:
        state = {}
        bot = MaxMvpBot(
            platform=self.platform, service=self.service, config=self.config,
            wallet=self.wallet, state=state,
        )
        run(bot.handle(_cb(CB_CREATE_IMAGE)))
        before = dict(state["u1"])
        run(bot.handle(_cb("s:im:not-a-model")))
        self.assertEqual(state["u1"], before)

    def test_text_video_model_and_aspect_are_forwarded(self) -> None:
        self.service.result = {"videos": [{"url": "https://img/text.mp4"}]}
        metrics.credits_add_for_identity("max", "u1", 500, self.config.starter_credits)
        bot = self._bot()

        run(bot.handle(_cb(CB_CREATE_VIDEO)))
        run(bot.handle(_cb(CB_VIDEO_TEXT)))
        run(bot.handle(_cb("s:vm:veo-quality")))
        run(bot.handle(_cb("s:va:landscape")))
        run(bot.handle(_msg("камера летит над горами")))
        run(bot.handle(_cb(CB_RUN_READY)))

        call = self.service.calls[-1]
        self.assertEqual(call["kind"], "create_video")
        self.assertEqual(call["video_model"], "veo-quality")
        self.assertEqual(call["aspect_ratio"], "landscape")
        self.assertEqual(self._balance(), 80)  # 530 - 450

    def test_ingredients_accepts_up_to_four_photos(self) -> None:
        self.service.result = {"videos": [{"url": "https://img/ref.mp4"}]}
        metrics.credits_add_for_identity("max", "u1", 100, self.config.starter_credits)
        bot = self._bot()

        run(bot.handle(_cb(CB_VIDEO_INGREDIENTS)))
        run(bot.handle(_msg(
            "персонажи идут навстречу",
            photos=("p1", "p2", "p3", "p4", "p5"),
        )))
        run(bot.handle(_cb(CB_RUN_READY)))

        call = self.service.calls[-1]
        self.assertEqual(call["kind"], "video_ingredients")
        self.assertEqual(call["photo_file_ids"], ("p1", "p2", "p3", "p4"))
        self.assertEqual(self._balance(), 70)  # 130 - Veo Lite 60

    def test_frames_requires_exactly_two_photos(self) -> None:
        metrics.credits_add_for_identity("max", "u1", 100, self.config.starter_credits)
        bot = self._bot()
        run(bot.handle(_cb(CB_VIDEO_FRAMES)))
        run(bot.handle(_msg("плавный переход", photos=("start",))))
        self.assertEqual(self.service.calls, [])
        self.assertIn("ровно 2", self.platform.last_text)

        self.service.result = {"videos": [{"url": "https://img/frames.mp4"}]}
        run(bot.handle(_msg("плавный переход", photos=("start", "end"))))
        run(bot.handle(_cb(CB_RUN_READY)))
        call = self.service.calls[-1]
        self.assertEqual(call["kind"], "video_frames")
        self.assertEqual(call["photo_file_ids"], ("start", "end"))
        self.assertEqual(self._balance(), 45)  # 130 - (Veo Lite 60 + 25)

    # -- photo flows ------------------------------------------------------

    def test_animate_photo_charges_animate_price(self) -> None:
        self.service.result = {"videos": [{"url": "https://img/clip.mp4"}]}
        bot = self._bot()
        # top up so animate (100) is affordable
        metrics.credits_add_for_identity("max", "u1", 100, self.config.starter_credits)
        run(bot.handle(_cb(CB_ANIMATE)))
        run(bot.handle(_msg("оживи", photos=("photo-1",))))
        run(bot.handle(_cb(CB_RUN_READY)))

        self.assertEqual(self.service.calls[-1]["kind"], "animate_photo")
        self.assertEqual(self.service.calls[-1]["photo_file_id"], "photo-1")
        self.assertEqual(self._balance(), 30)  # 130 - 100
        # Video delivery goes through send_video, not a text message.
        self.assertEqual(len(self.platform.videos), 1)
        video = self.platform.videos[-1]
        self.assertEqual(video["media"].kind, "video")
        self.assertEqual(video["media"].url, "https://img/clip.mp4")
        self.assertEqual(self.platform.photos, [])

    def test_animate_photo_delivers_backend_video_b64_as_mp4(self) -> None:
        payload = b"synthetic-mp4-bytes"
        self.service.result = {
            "videos": [{"video_b64": base64.b64encode(payload).decode("ascii")}]
        }
        bot = self._bot()
        metrics.credits_add_for_identity("max", "u1", 100, self.config.starter_credits)

        run(bot.handle(_cb(CB_ANIMATE)))
        run(bot.handle(_msg("оживи", photos=("photo-1",))))
        run(bot.handle(_cb(CB_RUN_READY)))

        self.assertEqual(len(self.platform.videos), 1)
        media = self.platform.videos[0]["media"]
        self.assertEqual(media.kind, "video")
        self.assertEqual(media.bytes_data, payload)
        self.assertEqual(media.file.file_id, "generated-video.mp4")
        self.assertEqual(media.file.mime_type, "video/mp4")
        self.assertEqual(self._balance(), 30)

    def test_invalid_backend_video_b64_refunds_without_inbox_retry(self) -> None:
        self.service.result = {"videos": [{"video_b64": "not-base64"}]}
        state = {}
        bot = MaxMvpBot(
            platform=self.platform,
            service=self.service,
            config=self.config,
            wallet=self.wallet,
            state=state,
        )
        metrics.credits_add_for_identity("max", "u1", 100, self.config.starter_credits)

        run(bot.handle(_cb(CB_ANIMATE)))
        run(bot.handle(_msg("оживи", photos=("photo-1",))))
        run(bot.handle(_cb(CB_RUN_READY)))

        self.assertEqual(self.platform.videos, [])
        self.assertEqual(self._balance(), 130)
        self.assertIn("возвращены", self.platform.last_text)
        self.assertEqual(state["u1"], {"await": "idle"})

    def test_oversized_backend_video_b64_is_not_decoded(self) -> None:
        self.service.result = {"videos": [{"video_b64": "A" * 9}]}
        state = {}
        bot = MaxMvpBot(
            platform=self.platform,
            service=self.service,
            config=self.config,
            wallet=self.wallet,
            state=state,
        )
        metrics.credits_add_for_identity("max", "u1", 100, self.config.starter_credits)

        run(bot.handle(_cb(CB_ANIMATE)))
        with patch("channels.max.handler._MAX_VIDEO_B64_CHARS", 8), patch(
            "channels.max.handler.base64.b64decode"
        ) as decode:
            run(bot.handle(_msg("оживи", photos=("photo-1",))))
            run(bot.handle(_cb(CB_RUN_READY)))

        decode.assert_not_called()
        self.assertEqual(self.platform.videos, [])
        self.assertEqual(self._balance(), 130)
        self.assertEqual(state["u1"], {"await": "idle"})

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
        bot = MaxMvpBot(
            platform=self.platform,
            service=self.service,
            config=self.config,
            wallet=self.wallet,
            topup_options=lambda uid: ((f"Pack for {uid}", "https://pay.example/invoice"),),
        )
        run(bot.handle(_cb(CB_TOPUP)))

        kb = self.platform.last_keyboard
        urls = [b.url for row in kb.rows for b in row if b.url]
        self.assertEqual(urls, ["https://pay.example/invoice"])

    def test_topup_without_identity_aware_options_has_no_payment_link(self) -> None:
        bot = self._bot()
        run(bot.handle(_cb(CB_TOPUP)))

        kb = self.platform.last_keyboard
        self.assertFalse([b.url for row in kb.rows for b in row if b.url])
        self.assertEqual(self.platform.last_text, bot.copy.topup_unavailable)

    def test_topup_rejects_non_https_links(self) -> None:
        bot = MaxMvpBot(
            platform=self.platform,
            service=self.service,
            config=self.config,
            wallet=self.wallet,
            topup_options=lambda uid: (("Unsafe", "http://pay.example/invoice"),),
        )

        run(bot.handle(_cb(CB_TOPUP)))

        self.assertFalse([
            button.url
            for row in self.platform.last_keyboard.rows
            for button in row
            if button.url
        ])
        self.assertEqual(self.platform.last_text, bot.copy.topup_unavailable)

    # -- profile / library / support / referral --------------------------

    def test_successful_result_populates_gallery_history_and_result_actions(self) -> None:
        bot = self._bot()
        prompt = "рыжий кот в шляпе"
        run(bot.handle(_cb(CB_CREATE_IMAGE)))
        run(bot.handle(_msg(prompt)))
        run(bot.handle(_cb(CB_RUN_READY)))

        result_keyboard = self.platform.photos[-1]["keyboard"]
        payloads = {
            button.callback_data
            for row in result_keyboard.rows
            for button in row
            if button.callback_data
        }
        self.assertTrue({"r:edit:0", "r:up:0", "r:animate:0", "r:repeat"} <= payloads)
        self.assertEqual(
            [button.url for row in result_keyboard.rows for button in row if button.url],
            ["https://img/1.png"],
        )

        run(bot.handle(_cb(CB_GALLERY)))
        self.assertGreaterEqual(len(self.platform.photos), 2)
        gallery_payloads = {
            button.callback_data
            for row in self.platform.photos[-1]["keyboard"].rows
            for button in row
            if button.callback_data
        }
        self.assertNotIn("r:repeat", gallery_payloads)
        run(bot.handle(_cb(CB_HISTORY)))
        self.assertIn(prompt, self.platform.last_text)

    def test_repeat_result_reuses_exact_saved_job_and_charges_again(self) -> None:
        bot = self._bot()
        run(bot.handle(_cb(CB_CREATE_IMAGE)))
        run(bot.handle(_msg("repeatable prompt")))
        run(bot.handle(_cb(CB_RUN_READY)))
        first = dict(self.service.calls[-1])

        run(bot.handle(_cb("r:repeat")))

        second = dict(self.service.calls[-1])
        self.assertEqual(first, second)
        self.assertEqual(self._balance(), 10)

    def test_support_ticket_is_persisted_notified_and_listed(self) -> None:
        notifications = []

        async def notify(**kwargs):
            notifications.append(kwargs)

        bot = MaxMvpBot(
            platform=self.platform, service=self.service, config=self.config,
            wallet=self.wallet, support_notify=notify,
        )
        run(bot.handle(_cb(CB_SUPPORT)))
        run(bot.handle(_cb(CB_SUPPORT_NEW)))
        run(bot.handle(_msg("Не получается открыть результат")))

        self.assertEqual(len(notifications), 1)
        self.assertLess(notifications[0]["internal_user_id"], 0)
        self.assertIn("создано", self.platform.last_text)
        run(bot.handle(_cb(CB_SUPPORT_MY)))
        self.assertIn("Не получается", self.platform.last_text)

    def test_referral_share_deeplink_binds_only_existing_max_identity(self) -> None:
        bot = self._bot()
        referrer = metrics.ensure_user_identity("max", "u1")
        run(bot.handle(_cb(CB_INVITE)))
        links = [
            button.url
            for row in self.platform.last_keyboard.rows
            for button in row
            if button.url
        ]
        self.assertIn(f"ref_{abs(referrer)}", links[0])

        started = webhook.parse_update({
            "update_type": "bot_started",
            "chat_id": "c2",
            "user": {"user_id": "u2"},
            "payload": f"ref_{abs(referrer)}",
        })
        run(bot.handle(started))
        referred = metrics.ensure_user_identity("max", "u2")
        self.assertEqual(metrics.get_referrer_of(referred), referrer)

    # -- identity separation ----------------------------------------------

    def test_max_balance_does_not_leak_into_telegram(self) -> None:
        bot = self._bot()
        run(bot.handle(_cb(CB_CREATE_IMAGE)))
        run(bot.handle(_msg("рыжий кот в шляпе", user_id="42")))
        run(bot.handle(_cb(CB_RUN_READY, user_id="42")))

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
