from __future__ import annotations

import inspect
import unittest

from channels.base import Button, Keyboard
from channels.max import client, renderer, webhook


class MaxChannelTests(unittest.TestCase):
    def test_max_disabled_by_default(self) -> None:
        config = client.max_config_from_env({})

        self.assertFalse(config.enabled)
        self.assertEqual(config.bot_token, "")
        self.assertIsNone(client.build_client_from_env({}))

    def test_enabled_requires_token_without_live_call(self) -> None:
        with self.assertRaises(ValueError):
            client.build_client_from_env({"MAX_ENABLED": "1"})

        bot = client.build_client_from_env({"MAX_ENABLED": "1", "MAX_BOT_TOKEN": "test-token"})

        self.assertIsNotNone(bot)
        assert bot is not None
        self.assertEqual(bot.token, "test-token")

    def test_renderer_builds_inline_keyboard_attachment(self) -> None:
        keyboard = Keyboard.from_rows([
            [Button.callback("Create", "m:gen")],
            [Button.link("Site", "https://photozhab.ru")],
        ])

        attachment = renderer.render_keyboard(keyboard)

        self.assertEqual(attachment["type"], "inline_keyboard")
        buttons = attachment["payload"]["buttons"]
        self.assertEqual(buttons[0][0], {"type": "callback", "text": "Create", "payload": "m:gen"})
        self.assertEqual(buttons[1][0]["type"], "link")
        self.assertEqual(buttons[1][0]["url"], "https://photozhab.ru")

    def test_client_builds_message_payload_without_network(self) -> None:
        bot = client.MaxBotClient(token="test-token")
        keyboard = Keyboard.single(Button.callback("Menu", "m:menu"))

        payload = bot.build_send_message_payload(chat_id="chat-1", text="Hello", keyboard=keyboard)

        self.assertEqual(payload["chat_id"], "chat-1")
        self.assertEqual(payload["text"], "Hello")
        self.assertEqual(payload["attachments"][0]["type"], "inline_keyboard")

    def test_parse_fake_message_update(self) -> None:
        update = {
            "update_type": "message_created",
            "message": {
                "id": "msg-1",
                "chat_id": "chat-1",
                "text": "prompt",
                "sender": {"user_id": "u1", "username": "tema", "first_name": "Tema"},
                "attachments": [{"type": "image", "payload": {"file_id": "photo-1"}}],
            },
        }

        event = webhook.parse_update(update)

        self.assertEqual(event.platform, "max")
        self.assertEqual(event.user.platform_user_id, "u1")
        self.assertEqual(event.text, "prompt")
        self.assertEqual(event.photo_file_ids, ("photo-1",))

    def test_parse_fake_callback_update(self) -> None:
        update = {
            "update_type": "callback",
            "callback": {
                "chat_id": "chat-1",
                "message_id": "msg-1",
                "payload": "m:gen",
                "user": {"id": "u1"},
            },
        }

        event = webhook.parse_update(update)

        self.assertEqual(event.platform, "max")
        self.assertEqual(event.user.platform_user_id, "u1")
        self.assertEqual(event.data, "m:gen")

    def test_webhook_secret_compare(self) -> None:
        self.assertTrue(webhook.verify_webhook_secret({"X-Max-Bot-Secret": "s"}, "s"))
        self.assertFalse(webhook.verify_webhook_secret({"X-Max-Bot-Secret": "bad"}, "s"))
        self.assertFalse(webhook.verify_webhook_secret({"X-Max-Bot-Secret": "s"}, ""))

    def test_max_modules_do_not_import_telegram_or_flow_bot(self) -> None:
        src = "\n".join(
            inspect.getsource(module)
            for module in (client, renderer, webhook)
        )

        self.assertNotIn("aiogram", src)
        self.assertNotIn("flow_bot", src)


if __name__ == "__main__":
    unittest.main()

