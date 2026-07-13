"""Unit tests for the MAX runtime startup — no network, no real sleep."""

from __future__ import annotations

import asyncio
import unittest

from channels.max import runtime
from channels.max.client import MaxBotClient
from channels.telegram.max_bootstrap import MaxBootstrap, MaxBootstrapDeps


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeService:
    async def create_image(self, *, internal_user_id, prompt):
        return {"images": [{"url": "http://u/i.png"}]}

    async def edit_photo(self, *, internal_user_id, prompt, photo_file_id):
        return {"images": [{"url": "http://u/e.png"}]}

    async def animate_photo(self, *, internal_user_id, prompt, photo_file_id):
        return {"videos": [{"url": "http://u/v.mp4"}]}


class _FakeClient:
    def __init__(self):
        self.calls = 0

    async def get_updates(self, marker=None, limit=100):
        self.calls += 1
        return {"updates": [], "marker": marker}


class _ClosingClient(_FakeClient):
    def __init__(self):
        super().__init__()
        self.closed = False

    async def close(self):
        self.closed = True


class _NotifyClient:
    def __init__(self):
        self.messages = []

    async def send_message_to_user(self, user_id, text):
        self.messages.append((user_id, text))


async def _nosleep(_):
    return None


DISABLED_ENV = {}
ENABLED_ENV = {"MAX_ENABLED": "1", "MAX_BOT_TOKEN": "TESTTOKEN"}


class BuildTests(unittest.TestCase):
    def test_disabled_returns_none(self):
        self.assertIsNone(runtime.build_max_bot(_FakeService(), env=DISABLED_ENV))

    def test_enabled_without_token_raises(self):
        with self.assertRaises(ValueError):
            runtime.build_max_bot(_FakeService(), env={"MAX_ENABLED": "1"})

    def test_enabled_builds_client_and_bot(self):
        built = runtime.build_max_bot(_FakeService(), env=ENABLED_ENV)
        self.assertIsNotNone(built)
        client, bot = built
        self.assertIsInstance(client, MaxBotClient)
        self.assertEqual(client.token, "TESTTOKEN")
        self.assertIs(bot.platform, client)

    def test_injected_client_is_used(self):
        fake = _FakeClient()
        built = runtime.build_max_bot(_FakeService(), env=ENABLED_ENV, client=fake)
        client, bot = built
        self.assertIs(client, fake)
        self.assertIs(bot.platform, fake)


class RunTests(unittest.TestCase):
    def test_run_max_disabled_is_noop(self):
        result = run(runtime.run_max(_FakeService(), env=DISABLED_ENV))
        self.assertIsNone(result)

    def test_run_max_polls_until_stop(self):
        fake = _FakeClient()

        def should_stop():
            return fake.calls >= 2

        run(runtime.run_max(
            _FakeService(), env=ENABLED_ENV, client=fake,
            should_stop=should_stop, sleep=_nosleep,
        ))
        self.assertGreaterEqual(fake.calls, 2)

    def test_run_max_closes_client_on_stop(self):
        fake = _ClosingClient()
        run(runtime.run_max(
            _FakeService(), env=ENABLED_ENV, client=fake,
            should_stop=lambda: fake.calls >= 1, sleep=_nosleep,
        ))
        self.assertTrue(fake.closed)


class PaymentNotificationTests(unittest.TestCase):
    def _bootstrap(self, identity):
        return MaxBootstrap(MaxBootstrapDeps(
            backend_generation_deps=lambda: None,
            topup_options=lambda user_id: (),
            identity_for_internal_id=lambda internal_id: identity,
            log=__import__("logging").getLogger(__name__),
        ))

    def test_max_payment_is_sent_to_platform_user_id(self):
        bootstrap = self._bootstrap({"platform": "max", "platform_user_id": "u7"})
        client = _NotifyClient()
        bootstrap._active_client = client

        handled = run(bootstrap.notify_payment(-7, 45, 75))

        self.assertTrue(handled)
        self.assertEqual(client.messages[0][0], "u7")
        self.assertIn("45", client.messages[0][1])
        self.assertIn("75", client.messages[0][1])

    def test_max_payment_without_runtime_is_handled_without_telegram_fallback(self):
        bootstrap = self._bootstrap({"platform": "max", "platform_user_id": "u7"})
        self.assertTrue(run(bootstrap.notify_payment(-7, 45, 75)))

    def test_telegram_payment_is_not_claimed(self):
        bootstrap = self._bootstrap({"platform": "telegram", "platform_user_id": "7"})
        self.assertFalse(run(bootstrap.notify_payment(7, 45, 75)))


class FlowBotWiringTests(unittest.TestCase):
    """flow_bot composition root must wire MAX startup (source-level, no import)."""

    def setUp(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        self.src = (root / "flow_bot.py").read_text(encoding="utf-8")
        # MAX bootstrap helpers moved to channels.telegram.max_bootstrap (Phase 11).
        self.max_src = (
            root / "channels" / "telegram" / "max_bootstrap.py"
        ).read_text(encoding="utf-8")

    def test_defines_and_calls_maybe_start_max_bot(self):
        self.assertIn("def _maybe_start_max_bot", self.src)
        self.assertIn("_maybe_start_max_bot()", self.src)

    def test_builds_backend_generation_service_and_runs_max(self):
        # build_runtime + run_max moved to channels.telegram.max_bootstrap.
        self.assertIn("from channels.max.runtime import run_max", self.max_src)
        self.assertIn("BackendGenerationService(", self.max_src)
        self.assertIn("generate_images=backend_service.generate_images", self.max_src)
        self.assertIn("run_max(", self.max_src)
        self.assertIn("client=client", self.max_src)

    def test_wires_photo_bridge_backend_fns(self):
        # edit/animate need i2i + video + a downloader threaded from the client.
        self.assertIn("generate_i2i=backend_service.generate_i2i", self.max_src)
        self.assertIn("generate_video_text=backend_service.generate_video_text", self.max_src)
        self.assertIn("generate_video_ingredients=backend_service.generate_video_ingredients", self.max_src)
        self.assertIn("generate_video_frames=backend_service.generate_video_frames", self.max_src)
        self.assertIn("download_bytes=_download", self.max_src)
        self.assertIn("client.get_file_bytes(PlatformFile(", self.max_src)

    def test_registers_webhook_route_in_webhook_mode(self):
        self.assertIn("def _maybe_register_max_webhook", self.src)
        self.assertIn("_maybe_register_max_webhook(app)", self.src)
        self.assertIn("register_max_webhook,", self.max_src)
        self.assertIn("register_max_subscription_lifecycle,", self.max_src)
        self.assertIn("register_max_health,", self.max_src)
        self.assertIn("webhook_url=config.webhook_url", self.max_src)
        self.assertIn("MaxWebhookInbox(config.inbox_db)", self.max_src)
        self.assertIn("MaxUserStateStore(config.inbox_db)", self.max_src)
        self.assertIn("state_store=state_store", self.max_src)
        self.assertIn("topup_options=self._d.topup_options", self.max_src)
        self.assertIn("inbox=inbox", self.max_src)
        self.assertIn("dispatch=bot.handle", self.max_src)
        self.assertIn("worker_count=config.inbox_workers", self.max_src)
        self.assertIn('config.mode == "webhook"', self.max_src)
        # polling is skipped in webhook mode (return right after the check)
        idx = self.max_src.index('config.mode == "webhook":')
        self.assertIn("return", self.max_src[idx:idx + 120])


if __name__ == "__main__":
    unittest.main()
