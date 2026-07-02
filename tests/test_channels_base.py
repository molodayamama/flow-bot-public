from __future__ import annotations

import unittest

from channels.base import (
    BotPlatform,
    Button,
    IncomingCallback,
    IncomingMessage,
    Keyboard,
    PlatformFile,
    PlatformMedia,
    PlatformUser,
)
from channels.telegram.renderer import render_button, render_keyboard


class ChannelBaseTests(unittest.TestCase):
    def test_button_requires_exactly_one_action(self) -> None:
        self.assertEqual(Button.callback("Go", "m:go").callback_data, "m:go")
        self.assertEqual(Button.link("Open", "https://photozhab.ru").url, "https://photozhab.ru")
        with self.assertRaises(ValueError):
            Button("Broken")
        with self.assertRaises(ValueError):
            Button("Broken", callback_data="x", url="https://photozhab.ru")

    def test_keyboard_normalizes_rows_to_immutable_tuples(self) -> None:
        keyboard = Keyboard.from_rows([
            [Button.callback("Create", "m:gen"), Button.callback("Video", "m:vid")],
            [Button.link("Site", "https://photozhab.ru")],
        ])

        self.assertFalse(keyboard.is_empty)
        self.assertIsInstance(keyboard.rows, tuple)
        self.assertIsInstance(keyboard.rows[0], tuple)
        self.assertEqual(keyboard.rows[0][1].callback_data, "m:vid")
        self.assertTrue(Keyboard().is_empty)

    def test_incoming_events_keep_platform_identity(self) -> None:
        user = PlatformUser(
            platform="telegram",
            platform_user_id="42",
            username="tema",
            language_code="ru",
        )
        message = IncomingMessage(
            platform="telegram",
            user=user,
            chat_id="42",
            message_id="100",
            text="prompt",
            photo_file_ids=["file-a", "file-b"],
        )
        callback = IncomingCallback(
            platform="telegram",
            user=user,
            chat_id="42",
            message_id="100",
            data="m:gen",
        )

        self.assertEqual(message.user.platform_user_id, "42")
        self.assertEqual(message.photo_file_ids, ("file-a", "file-b"))
        self.assertEqual(callback.data, "m:gen")

    def test_bot_platform_is_runtime_protocol(self) -> None:
        class FakePlatform:
            name = "fake"

            async def send_message(self, chat_id, text, keyboard=None):
                return None

            async def edit_message(self, chat_id, message_id, text, keyboard=None):
                return None

            async def answer_callback(self, callback_id, text=None):
                return None

            async def send_photo(self, chat_id, media, keyboard=None):
                return None

            async def send_video(self, chat_id, media, keyboard=None):
                return None

            async def send_document(self, chat_id, media, keyboard=None):
                return None

            async def get_file_bytes(self, file):
                return b""

        self.assertIsInstance(FakePlatform(), BotPlatform)

    def test_platform_file_holds_optional_metadata(self) -> None:
        file = PlatformFile(file_id="abc")
        self.assertIsNone(file.url)
        self.assertIsNone(file.size)
        self.assertIsNone(file.mime_type)

        full = PlatformFile(file_id="abc", url="https://x/1.png", size=123, mime_type="image/png")
        self.assertEqual(full.url, "https://x/1.png")
        self.assertEqual(full.size, 123)
        self.assertEqual(full.mime_type, "image/png")

    def test_platform_media_accepts_any_single_source(self) -> None:
        by_url = PlatformMedia(kind="photo", url="https://x/1.png")
        self.assertEqual(by_url.kind, "photo")
        self.assertEqual(by_url.url, "https://x/1.png")

        by_file = PlatformMedia(kind="video", file=PlatformFile(file_id="f1"))
        self.assertEqual(by_file.file.file_id, "f1")

        by_bytes = PlatformMedia(kind="document", bytes_data=b"data")
        self.assertEqual(by_bytes.bytes_data, b"data")

    def test_platform_media_requires_a_source(self) -> None:
        with self.assertRaises(ValueError):
            PlatformMedia(kind="photo")


class TelegramRendererTests(unittest.TestCase):
    def test_renders_callback_and_url_buttons(self) -> None:
        button = render_button(Button.callback("Create", "m:gen"))
        self.assertEqual(button.text, "Create")
        self.assertEqual(button.callback_data, "m:gen")
        self.assertIsNone(button.url)

        link = render_button(Button.link("Site", "https://photozhab.ru"))
        self.assertEqual(link.text, "Site")
        self.assertEqual(link.url, "https://photozhab.ru")
        self.assertIsNone(link.callback_data)

    def test_renders_keyboard_rows(self) -> None:
        keyboard = Keyboard.from_rows([
            [Button.callback("Create", "m:gen"), Button.callback("Video", "m:vid")],
            [Button.link("Site", "https://photozhab.ru")],
        ])

        markup = render_keyboard(keyboard)

        self.assertIsNotNone(markup)
        assert markup is not None
        self.assertEqual(len(markup.inline_keyboard), 2)
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "m:gen")
        self.assertEqual(markup.inline_keyboard[0][1].callback_data, "m:vid")
        self.assertEqual(markup.inline_keyboard[1][0].url, "https://photozhab.ru")

    def test_none_keyboard_stays_none(self) -> None:
        self.assertIsNone(render_keyboard(None))


if __name__ == "__main__":
    unittest.main()

