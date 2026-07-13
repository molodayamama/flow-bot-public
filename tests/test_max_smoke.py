"""Offline tests for the approval-gated MAX smoke harness."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from channels.max.client import MaxConfig
from tools.max_smoke import run_smoke, validate_smoke_inputs


def _config(**overrides) -> MaxConfig:
    values = {
        "enabled": True,
        "bot_token": "test-token",
        "api_base_url": "https://platform-api2.max.ru",
    }
    values.update(overrides)
    return MaxConfig(**values)


class FakeClient:
    instances = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.calls = []
        self.closed = False
        self.__class__.instances.append(self)

    async def get_subscriptions(self):
        self.calls.append(("subscription",))

    async def send_message_to_user(self, user_id, text):
        self.calls.append(("message", user_id, text))

    async def send_photo(self, chat_id, media):
        self.calls.append(("media", chat_id, media))

    async def close(self):
        self.closed = True


class MaxSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeClient.instances.clear()

    def test_plan_is_offline_and_requires_valid_enabled_config(self) -> None:
        self.assertEqual(
            validate_smoke_inputs(_config(), mode="plan", approved=False), ()
        )
        errors = validate_smoke_inputs(
            _config(enabled=False, bot_token=""), mode="plan", approved=False
        )
        self.assertIn("MAX_ENABLED=1 is required", errors)
        errors = validate_smoke_inputs(
            _config(enabled=True, bot_token=""), mode="plan", approved=False
        )
        self.assertTrue(any("MAX_BOT_TOKEN" in error for error in errors))

    def test_external_modes_require_explicit_approval_and_target(self) -> None:
        errors = validate_smoke_inputs(
            _config(), mode="message", approved=False
        )
        self.assertIn("external mode requires --approve-external-action", errors)
        self.assertIn("MAX_SMOKE_USER_ID or --user-id is required", errors)

    def test_subscription_uses_client_and_closes_it(self) -> None:
        result = asyncio.run(
            run_smoke(_config(), mode="subscription", client_factory=FakeClient)
        )
        client = FakeClient.instances[-1]
        self.assertEqual(result, "subscription_ok")
        self.assertEqual(client.calls, [("subscription",)])
        self.assertTrue(client.closed)

    def test_message_target_is_not_returned_or_logged(self) -> None:
        private_id = "private-user-id"
        result = asyncio.run(
            run_smoke(
                _config(),
                mode="message",
                user_id=private_id,
                client_factory=FakeClient,
            )
        )
        client = FakeClient.instances[-1]
        self.assertEqual(result, "message_ok")
        self.assertEqual(client.calls[0][1], private_id)
        self.assertNotIn(private_id, result)
        self.assertTrue(client.closed)

    def test_media_reads_local_bytes_and_closes_client(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "smoke.png"
            path.write_bytes(b"not-a-real-network-image")
            result = asyncio.run(
                run_smoke(
                    _config(),
                    mode="media",
                    chat_id="private-chat",
                    media_file=path,
                    client_factory=FakeClient,
                )
            )
        client = FakeClient.instances[-1]
        self.assertEqual(result, "media_ok")
        self.assertEqual(client.calls[0][2].bytes_data, b"not-a-real-network-image")
        self.assertTrue(client.closed)

    def test_media_validation_rejects_missing_file_without_path_leak(self) -> None:
        errors = validate_smoke_inputs(
            _config(),
            mode="media",
            approved=True,
            chat_id="chat",
            media_file="private/missing/file.png",
        )
        rendered = "\n".join(errors)
        self.assertIn("--media-file", rendered)
        self.assertNotIn("private/missing", rendered)


if __name__ == "__main__":
    unittest.main()
