"""Tests for generation.prompt_boost without network calls."""

from __future__ import annotations

import asyncio
import unittest

from generation.prompt_boost import boost_prompt_with_gemini


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Log:
    def __init__(self):
        self.warnings = []

    def warning(self, *args):
        self.warnings.append(args)


class _Timeout:
    def __init__(self, **kw):
        self.kw = kw


class _Response:
    def __init__(self, status=200, data=None, *, fail_json=False):
        self.status = status
        self.data = data or {
            "candidates": [{"content": {"parts": [{"text": " better prompt "}]} }]
        }
        self.fail_json = fail_json

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        if self.fail_json:
            raise RuntimeError("bad json")
        return self.data


class _Session:
    def __init__(self, recorder, response):
        self.recorder = recorder
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, **kw):
        self.recorder.append((url, kw))
        return self.response


class PromptBoostTests(unittest.TestCase):
    def test_no_api_key_skips_session(self):
        calls = []
        result = run(boost_prompt_with_gemini(
            "cat",
            api_key="",
            log=_Log(),
            session_factory=lambda **kw: calls.append(kw),
            timeout_factory=_Timeout,
        ))
        self.assertIsNone(result)
        self.assertEqual(calls, [])

    def test_success_returns_stripped_text_and_payload(self):
        posts = []

        def session_factory(**kw):
            self.assertEqual(kw["timeout"].kw, {"total": 20})
            return _Session(posts, _Response())

        result = run(boost_prompt_with_gemini(
            "cat",
            api_key="secret-key",
            log=_Log(),
            session_factory=session_factory,
            timeout_factory=_Timeout,
        ))
        self.assertEqual(result, "better prompt")
        self.assertIn("key=secret-key", posts[0][0])
        self.assertEqual(posts[0][1]["json"]["contents"][0]["parts"][0]["text"], "cat")

    def test_non_200_returns_none(self):
        result = run(boost_prompt_with_gemini(
            "cat",
            api_key="secret-key",
            log=_Log(),
            session_factory=lambda **kw: _Session([], _Response(status=429)),
            timeout_factory=_Timeout,
        ))
        self.assertIsNone(result)

    def test_exception_logs_warning_and_returns_none(self):
        log = _Log()
        result = run(boost_prompt_with_gemini(
            "cat",
            api_key="secret-key",
            log=log,
            session_factory=lambda **kw: _Session([], _Response(fail_json=True)),
            timeout_factory=_Timeout,
        ))
        self.assertIsNone(result)
        self.assertEqual(log.warnings[0][0], "Gemini prompt boost failed: %s")


if __name__ == "__main__":
    unittest.main()
