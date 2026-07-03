"""Unit tests for the MAX runtime startup — no network, no real sleep."""

from __future__ import annotations

import asyncio
import unittest

from channels.max import runtime
from channels.max.client import MaxBotClient


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


if __name__ == "__main__":
    unittest.main()
