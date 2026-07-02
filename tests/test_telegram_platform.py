from __future__ import annotations

import asyncio
import io
import unittest

from aiogram.types import BufferedInputFile

from channels.base import (
    BotPlatform,
    Button,
    Keyboard,
    PlatformFile,
    PlatformMedia,
)
from channels.telegram.platform import TelegramPlatform, media_filename


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class FakeBot:
    """Records outbound aiogram-style calls instead of hitting Telegram."""

    def __init__(self, download_result: bytes = b"FILE") -> None:
        self.calls: list[tuple[str, dict]] = []
        self._download_result = download_result

    def _record(self, method: str, kwargs: dict):
        self.calls.append((method, kwargs))
        return {"ok": True, "method": method}

    async def send_message(self, **kwargs):
        return self._record("send_message", kwargs)

    async def edit_message_text(self, **kwargs):
        return self._record("edit_message_text", kwargs)

    async def answer_callback_query(self, **kwargs):
        return self._record("answer_callback_query", kwargs)

    async def send_photo(self, **kwargs):
        return self._record("send_photo", kwargs)

    async def send_video(self, **kwargs):
        return self._record("send_video", kwargs)

    async def send_document(self, **kwargs):
        return self._record("send_document", kwargs)

    async def download(self, file_id):
        self._record("download", {"file_id": file_id})
        return io.BytesIO(self._download_result)

    @property
    def last(self) -> tuple[str, dict]:
        return self.calls[-1]


class TelegramPlatformProtocolTests(unittest.TestCase):
    def test_satisfies_bot_platform_runtime_protocol(self) -> None:
        platform = TelegramPlatform(FakeBot())
        self.assertIsInstance(platform, BotPlatform)
        self.assertEqual(platform.name, "telegram")


class TelegramPlatformTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bot = FakeBot()
        self.platform = TelegramPlatform(self.bot)
        self.keyboard = Keyboard.single(Button.callback("Menu", "m:menu"))

    def test_send_message_uses_html_and_renders_keyboard(self) -> None:
        run(self.platform.send_message("c1", "<b>hi</b>", self.keyboard))

        method, kwargs = self.bot.last
        self.assertEqual(method, "send_message")
        self.assertEqual(kwargs["chat_id"], "c1")
        self.assertEqual(kwargs["text"], "<b>hi</b>")
        self.assertEqual(kwargs["parse_mode"], "HTML")
        markup = kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "m:menu")

    def test_send_message_without_keyboard_passes_none(self) -> None:
        run(self.platform.send_message("c1", "hi"))

        self.assertIsNone(self.bot.last[1]["reply_markup"])

    def test_edit_message_converts_message_id(self) -> None:
        run(self.platform.edit_message("c1", "100", "new", self.keyboard))

        method, kwargs = self.bot.last
        self.assertEqual(method, "edit_message_text")
        self.assertEqual(kwargs["message_id"], 100)
        self.assertEqual(kwargs["text"], "new")
        self.assertEqual(kwargs["parse_mode"], "HTML")
        self.assertIsNotNone(kwargs["reply_markup"])

    def test_answer_callback_maps_callback_id(self) -> None:
        run(self.platform.answer_callback("cb-1", "done"))

        method, kwargs = self.bot.last
        self.assertEqual(method, "answer_callback_query")
        self.assertEqual(kwargs["callback_query_id"], "cb-1")
        self.assertEqual(kwargs["text"], "done")


class TelegramPlatformMediaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bot = FakeBot()
        self.platform = TelegramPlatform(self.bot)
        self.keyboard = Keyboard.single(Button.callback("Menu", "m:menu"))

    def test_send_photo_by_url_passes_url_string(self) -> None:
        media = PlatformMedia(kind="photo", url="https://img/1.png", caption="ready")
        run(self.platform.send_photo("c1", media, self.keyboard))

        method, kwargs = self.bot.last
        self.assertEqual(method, "send_photo")
        self.assertEqual(kwargs["photo"], "https://img/1.png")
        self.assertEqual(kwargs["caption"], "ready")
        self.assertEqual(kwargs["parse_mode"], "HTML")
        markup = kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "m:menu")

    def test_send_photo_without_caption_skips_parse_mode(self) -> None:
        media = PlatformMedia(kind="photo", url="https://img/1.png")
        run(self.platform.send_photo("c1", media))

        kwargs = self.bot.last[1]
        self.assertIsNone(kwargs["caption"])
        self.assertIsNone(kwargs["parse_mode"])
        self.assertIsNone(kwargs["reply_markup"])

    def test_send_photo_by_file_id_passes_file_id_string(self) -> None:
        media = PlatformMedia(kind="photo", file=PlatformFile(file_id="tg-file-1"))
        run(self.platform.send_photo("c1", media))

        self.assertEqual(self.bot.last[1]["photo"], "tg-file-1")

    def test_send_photo_bytes_wrapped_in_buffered_input_file(self) -> None:
        media = PlatformMedia(kind="photo", bytes_data=b"PNG")
        run(self.platform.send_photo("c1", media))

        photo = self.bot.last[1]["photo"]
        self.assertIsInstance(photo, BufferedInputFile)
        self.assertEqual(photo.data, b"PNG")
        self.assertEqual(photo.filename, "photo.jpg")

    def test_bytes_beat_file_id_and_url_as_source(self) -> None:
        media = PlatformMedia(
            kind="photo",
            file=PlatformFile(file_id="tg-file-1"),
            url="https://img/1.png",
            bytes_data=b"PNG",
        )
        run(self.platform.send_photo("c1", media))

        self.assertIsInstance(self.bot.last[1]["photo"], BufferedInputFile)

    def test_send_video_maps_kind_and_filename(self) -> None:
        media = PlatformMedia(kind="video", bytes_data=b"MP4", caption="clip")
        run(self.platform.send_video("c1", media, self.keyboard))

        method, kwargs = self.bot.last
        self.assertEqual(method, "send_video")
        video = kwargs["video"]
        self.assertIsInstance(video, BufferedInputFile)
        self.assertEqual(video.filename, "video.mp4")
        self.assertEqual(kwargs["caption"], "clip")
        self.assertEqual(kwargs["parse_mode"], "HTML")

    def test_send_document_defaults_to_generic_filename(self) -> None:
        media = PlatformMedia(kind="document", bytes_data=b"BIN")
        run(self.platform.send_document("c1", media))

        method, kwargs = self.bot.last
        self.assertEqual(method, "send_document")
        self.assertEqual(kwargs["document"].filename, "file.bin")

    def test_mime_type_refines_bytes_filename(self) -> None:
        media = PlatformMedia(
            kind="photo",
            file=PlatformFile(file_id="tg-file-1", mime_type="image/png"),
            bytes_data=b"PNG",
        )
        self.assertEqual(media_filename(media), "photo.png")

        pdf = PlatformMedia(
            kind="document",
            file=PlatformFile(file_id="tg-file-2", mime_type="application/pdf"),
            bytes_data=b"%PDF",
        )
        self.assertEqual(media_filename(pdf), "file.pdf")


class TelegramPlatformDownloadTests(unittest.TestCase):
    def test_get_file_bytes_downloads_by_file_id(self) -> None:
        bot = FakeBot(download_result=b"IMAGE-BYTES")
        platform = TelegramPlatform(bot)

        data = run(platform.get_file_bytes(PlatformFile(file_id="tg-file-9")))

        self.assertEqual(data, b"IMAGE-BYTES")
        self.assertEqual(bot.calls[0], ("download", {"file_id": "tg-file-9"}))


if __name__ == "__main__":
    unittest.main()
