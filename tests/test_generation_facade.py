from __future__ import annotations

import asyncio
import base64
import tempfile
import unittest
from pathlib import Path

import metrics
from channels.base import IncomingCallback, IncomingMessage, PlatformUser
from channels.max.handler import (
    CB_ANIMATE,
    CB_CREATE_IMAGE,
    MaxMvpBot,
    MaxMvpConfig,
    MetricsWallet,
)
from generation.edit_service import EditService
from generation.image_service import ImageService
from generation.service import BackendGenerationService
from generation.video_service import VideoService


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class RecordingBackend:
    def __init__(self, raw):
        self.raw = raw
        self.calls: list[tuple] = []

    async def __call__(self, deps, req):
        self.calls.append((deps, req))
        return self.raw


DEPS = object()


def _facade(*, image_raw=None, video_raw=None, fetch_result=b"IMG"):
    img_backend = RecordingBackend(image_raw or {"images": [{"url": "u1"}], "account_id": "a1"})
    edit_backend = RecordingBackend(image_raw or {"images": [{"url": "e1"}]})
    vid_backend = RecordingBackend(video_raw or {"videos": [{"video_b64": "AAA", "media_id": "m"}]})
    fetched: list[str] = []

    async def fetch_bytes(file_id):
        fetched.append(file_id)
        return fetch_result

    facade = BackendGenerationService(
        ImageService(DEPS, backend=img_backend),
        EditService(DEPS, backend=edit_backend),
        VideoService(DEPS, backend=vid_backend),
        fetch_bytes=fetch_bytes,
        source="max",
    )
    return facade, img_backend, edit_backend, vid_backend, fetched


class FacadeUnitTests(unittest.TestCase):
    def test_create_image_returns_backend_dict(self) -> None:
        facade, img, _, _, _ = _facade()
        out = run(facade.create_image(internal_user_id=-5, prompt="cat"))
        self.assertEqual(out["images"], [{"url": "u1", "img": None}])
        self.assertEqual(img.calls[0][1]["user_id"], -5)
        self.assertEqual(img.calls[0][1]["prompt"], "cat")

    def test_edit_photo_fetches_bytes_and_b64_encodes(self) -> None:
        facade, _, edit, _, fetched = _facade()
        out = run(facade.edit_photo(internal_user_id=-5, prompt="fix", photo_file_id="ph1"))
        self.assertEqual(fetched, ["ph1"])
        self.assertEqual(edit.calls[0][1]["image_b64"], base64.b64encode(b"IMG").decode())
        self.assertIn("images", out)

    def test_animate_photo_routes_to_video(self) -> None:
        facade, _, _, vid, fetched = _facade()
        out = run(facade.animate_photo(internal_user_id=-5, prompt="go", photo_file_id="ph2"))
        self.assertEqual(fetched, ["ph2"])
        self.assertEqual(vid.calls[0][1]["image_b64"], base64.b64encode(b"IMG").decode())
        self.assertEqual(out["videos"][0]["media_id"], "m")

    def test_photo_flow_upload_failure_returns_error(self) -> None:
        facade, _, edit, _, _ = _facade(fetch_result=None)
        out = run(facade.edit_photo(internal_user_id=-5, prompt="fix", photo_file_id="ph1"))
        self.assertEqual(out, {"error": "upload failed"})
        self.assertEqual(edit.calls, [])  # backend never invoked without bytes

    def test_missing_fetch_bytes_is_upload_failure(self) -> None:
        facade = BackendGenerationService(
            ImageService(DEPS, backend=RecordingBackend({})),
            EditService(DEPS, backend=RecordingBackend({})),
            VideoService(DEPS, backend=RecordingBackend({})),
            fetch_bytes=None,
        )
        out = run(facade.animate_photo(internal_user_id=-1, prompt="p", photo_file_id="x"))
        self.assertEqual(out, {"error": "upload failed"})


class FakePlatform:
    name = "max"

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.answers: list[dict] = []
        self.photos: list[dict] = []
        self.videos: list[dict] = []

    async def send_message(self, chat_id, text, keyboard=None):
        self.messages.append({"chat_id": chat_id, "text": text, "keyboard": keyboard})
        return {"ok": True}

    async def answer_callback(self, callback_id, text=None):
        self.answers.append({"callback_id": callback_id})
        return {"ok": True}

    async def send_photo(self, chat_id, media, keyboard=None):
        self.photos.append({"chat_id": chat_id, "media": media, "keyboard": keyboard})
        return {"ok": True}

    async def send_video(self, chat_id, media, keyboard=None):
        self.videos.append({"chat_id": chat_id, "media": media, "keyboard": keyboard})
        return {"ok": True}

    async def send_document(self, chat_id, media, keyboard=None):
        return {"ok": True}

    async def get_file_bytes(self, file):
        return b""

    @property
    def last_text(self) -> str:
        return self.messages[-1]["text"] if self.messages else ""


def _cb(data, uid="u1"):
    return IncomingCallback(
        platform="max", user=PlatformUser("max", uid), chat_id="c1", data=data, message_id="m1", raw={}
    )


def _msg(text, uid="u1", photos=()):
    return IncomingMessage(
        platform="max", user=PlatformUser("max", uid), chat_id="c1", message_id="m1",
        text=text, photo_file_ids=photos, raw={},
    )


class MaxOnSharedEngineTests(unittest.TestCase):
    """MAX MVP runs its generation through the shared generation core."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        metrics.close()
        metrics.init_db(str(Path(self._tmp.name) / "metrics.db"))
        self.platform = FakePlatform()
        self.config = MaxMvpConfig(starter_credits=200, image_price=10, edit_price=8, animate_price=100)
        self.wallet = MetricsWallet(self.config.starter_credits)
        self.facade, self.img, self.edit, self.vid, self.fetched = _facade()
        self.bot = MaxMvpBot(
            platform=self.platform, service=self.facade, config=self.config, wallet=self.wallet
        )

    def tearDown(self) -> None:
        metrics.close()
        self._tmp.cleanup()

    def _balance(self, uid="u1"):
        return metrics.credits_balance_for_identity("max", uid, self.config.starter_credits)

    def test_create_image_flows_through_generation_core(self) -> None:
        run(self.bot.handle(_cb(CB_CREATE_IMAGE)))
        run(self.bot.handle(_msg("a red cat")))

        # Reached the shared image backend with the MAX internal (negative) id.
        self.assertEqual(len(self.img.calls), 1)
        internal_id = metrics.ensure_user_identity("max", "u1")
        self.assertLess(internal_id, 0)
        self.assertEqual(self.img.calls[0][1]["user_id"], internal_id)
        self.assertEqual(self.img.calls[0][1]["prompt"], "a red cat")
        # Delivered through the media API (not a text message) and charged.
        self.assertEqual(len(self.platform.photos), 1)
        self.assertEqual(self.platform.photos[-1]["media"].url, "u1")
        self.assertEqual(self._balance(), 190)

    def test_animate_flows_through_video_core_with_photo_bytes(self) -> None:
        run(self.bot.handle(_cb(CB_ANIMATE)))
        run(self.bot.handle(_msg("go", photos=("photo-9",))))
        self.assertEqual(self.fetched, ["photo-9"])
        self.assertEqual(len(self.vid.calls), 1)
        self.assertEqual(self._balance(), 100)  # 200 - 100

    def test_generation_failure_refunds_via_shared_engine(self) -> None:
        facade, *_ = _facade(image_raw={"error": "generation failed"})
        bot = MaxMvpBot(platform=self.platform, service=facade, config=self.config, wallet=self.wallet)
        run(bot.handle(_cb(CB_CREATE_IMAGE)))
        run(bot.handle(_msg("a red cat")))
        self.assertEqual(self._balance(), 200)  # charged then refunded


if __name__ == "__main__":
    unittest.main()
