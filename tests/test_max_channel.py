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

    def test_enabled_rejects_unknown_mode(self) -> None:
        config = client.max_config_from_env({
            "MAX_ENABLED": "1",
            "MAX_BOT_TOKEN": "test-token",
            "MAX_MODE": "weebhook",
        })
        with self.assertRaisesRegex(ValueError, "MAX_MODE"):
            client.validate_max_config(config)

    def test_webhook_mode_requires_production_https_contract(self) -> None:
        base = {
            "MAX_ENABLED": "1",
            "MAX_BOT_TOKEN": "test-token",
            "MAX_MODE": "webhook",
        }
        with self.assertRaisesRegex(ValueError, "MAX_WEBHOOK_URL"):
            client.validate_max_config(client.max_config_from_env(base), production=True)

        valid = dict(base, **{
            "MAX_WEBHOOK_URL": "https://bot.example/max/webhook",
            "MAX_WEBHOOK_SECRET": "safe_secret-1",
        })
        client.validate_max_config(client.max_config_from_env(valid), production=True)

    def test_webhook_rejects_non_443_and_invalid_secret(self) -> None:
        config = client.max_config_from_env({
            "MAX_ENABLED": "1",
            "MAX_BOT_TOKEN": "test-token",
            "MAX_MODE": "webhook",
            "MAX_WEBHOOK_URL": "https://bot.example:8443/max/webhook",
            "MAX_WEBHOOK_SECRET": "bad secret",
        })
        with self.assertRaisesRegex(ValueError, "port 443"):
            client.validate_max_config(config, production=True)

    def test_webhook_requires_durable_inbox_and_bounded_workers(self) -> None:
        base = {
            "MAX_ENABLED": "1",
            "MAX_BOT_TOKEN": "test-token",
            "MAX_MODE": "webhook",
            "MAX_WEBHOOK_URL": "https://bot.example/max/webhook",
            "MAX_WEBHOOK_SECRET": "safe_secret-1",
        }
        memory = client.max_config_from_env(dict(base, MAX_INBOX_DB=":memory:"))
        with self.assertRaisesRegex(ValueError, "MAX_INBOX_DB"):
            client.validate_max_config(memory, production=True)
        workers = client.max_config_from_env(dict(base, MAX_INBOX_WORKERS="many"))
        with self.assertRaisesRegex(ValueError, "MAX_INBOX_WORKERS"):
            client.validate_max_config(workers, production=True)

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

        self.assertNotIn("chat_id", payload)
        self.assertEqual(payload["text"], "Hello")
        self.assertEqual(payload["attachments"][0]["type"], "inline_keyboard")

    def test_parse_official_message_update(self) -> None:
        update = {
            "update_type": "message_created",
            "message": {
                "sender": {"user_id": "u1", "username": "tema", "first_name": "Tema"},
                "recipient": {"chat_id": "chat-1", "chat_type": "dialog", "user_id": "u1"},
                "body": {
                    "mid": "msg-1",
                    "text": "prompt",
                    "attachments": [{"type": "image", "payload": {"url": "https://cdn/p.png"}}],
                },
            },
        }

        event = webhook.parse_update(update)

        self.assertEqual(event.platform, "max")
        self.assertEqual(event.user.platform_user_id, "u1")
        self.assertEqual(event.chat_id, "chat-1")
        self.assertEqual(event.message_id, "msg-1")
        self.assertEqual(event.text, "prompt")
        self.assertEqual(event.photo_file_ids, ("https://cdn/p.png",))

    def test_parse_photo_prefers_downloadable_url(self) -> None:
        # When an image attachment carries a url, the parser captures it (the
        # photo bridge needs a downloadable ref, not just a file id).
        update = {
            "update_type": "message_created",
            "message": {
                "sender": {"user_id": "u1"},
                "recipient": {"chat_id": "chat-1"},
                "body": {"mid": "msg-2", "attachments": [
                    {"type": "image", "payload": {"url": "https://cdn/p.png", "token": "photo-9"}}
                ]},
            },
        }
        event = webhook.parse_update(update)
        self.assertEqual(event.photo_file_ids, ("https://cdn/p.png",))

    def test_parse_official_callback_update(self) -> None:
        update = {
            "update_type": "message_callback",
            "chat_id": "chat-1",
            "message_id": "msg-1",
            "callback": {
                "callback_id": "cb-1",
                "payload": "m:gen",
                "user": {"user_id": "u1"},
            },
        }

        event = webhook.parse_update(update)

        self.assertEqual(event.platform, "max")
        self.assertEqual(event.user.platform_user_id, "u1")
        self.assertEqual(event.chat_id, "chat-1")
        self.assertEqual(event.message_id, "msg-1")
        self.assertEqual(event.data, "m:gen")

    def test_parse_bot_started_as_start_message(self) -> None:
        event = webhook.parse_update({
            "update_type": "bot_started",
            "chat_id": 123,
            "user": {"user_id": 7, "first_name": "Tema"},
        })
        self.assertEqual(event.chat_id, "123")
        self.assertEqual(event.user.platform_user_id, "7")
        self.assertEqual(event.text, "/start")

    def test_parse_rejects_missing_identity_or_chat(self) -> None:
        self.assertIsNone(webhook.parse_update({
            "update_type": "message_created",
            "message": {"sender": {"first_name": "No id"}, "body": {"text": "x"}},
        }))

    def test_webhook_secret_compare(self) -> None:
        header = "X-Max-Bot-Api-Secret"
        self.assertTrue(webhook.verify_webhook_secret({header: "s"}, "s"))
        self.assertFalse(webhook.verify_webhook_secret({header: "bad"}, "s"))
        self.assertFalse(webhook.verify_webhook_secret({header: "s"}, ""))

    def test_max_modules_do_not_import_telegram_or_flow_bot(self) -> None:
        src = "\n".join(
            inspect.getsource(module)
            for module in (client, renderer, webhook)
        )

        self.assertNotIn("aiogram", src)
        self.assertNotIn("flow_bot", src)


if __name__ == "__main__":
    unittest.main()
