"""Unit tests for the MAX Bot API client media/edit methods.

Exercises payload SHAPES and request routing against a fake aiohttp session —
no network. Live-endpoint validation (upload PUT, video-by-token) is deliberately
left for an approved live smoke; these tests pin the documented request bodies.
"""

from __future__ import annotations

import asyncio
import unittest

from channels.base import BotPlatform, Button, Keyboard, PlatformFile, PlatformMedia
from channels.max.client import MaxBotClient


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeResp:
    def __init__(self, *, json_body=None, raw=b"", content_type="application/json"):
        self._json = json_body if json_body is not None else {}
        self._raw = raw
        self.content_type = content_type

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    async def json(self):
        return self._json

    async def text(self):
        return ""

    async def read(self):
        return self._raw


class _FakeSession:
    """Records requests; returns queued responses by call order."""

    def __init__(self, responses=None):
        self.calls = []
        self._responses = list(responses or [])

    def _next(self):
        return self._responses.pop(0) if self._responses else _FakeResp()

    def request(self, method, url, **kwargs):
        self.calls.append(("request", method, url, kwargs))
        return self._next()

    def get(self, url, **kwargs):
        self.calls.append(("get", "GET", url, kwargs))
        return self._next()

    def post(self, url, **kwargs):
        self.calls.append(("post", "POST", url, kwargs))
        return self._next()


def _client(responses=None):
    sess = _FakeSession(responses)
    return MaxBotClient(token="TESTTOKEN", session=sess), sess


class ProtocolTests(unittest.TestCase):
    def test_satisfies_bot_platform(self):
        self.assertIsInstance(MaxBotClient(token="x"), BotPlatform)

    def test_name_is_max(self):
        self.assertEqual(MaxBotClient(token="x").name, "max")

    def test_auth_header_uses_token(self):
        self.assertEqual(MaxBotClient(token="ABC")._headers()["Authorization"], "ABC")

    def test_ssl_arg_none_without_ca_bundle(self):
        self.assertIsNone(MaxBotClient(token="x")._ssl_arg())

    def test_ssl_arg_builds_context_from_ca_bundle(self):
        import ssl as _ssl
        import certifi  # any real PEM works to prove a context is built
        ctx = MaxBotClient(token="x", ca_bundle=certifi.where())._ssl_arg()
        self.assertIsInstance(ctx, _ssl.SSLContext)

    def test_requests_pass_ssl_argument(self):
        c, sess = _client([_FakeResp(json_body={"ok": True})])
        run(c.send_message("7", "hi"))
        self.assertIn("ssl", sess.calls[0][3])


class MediaPayloadTests(unittest.TestCase):
    def test_image_url_payload(self):
        c, _ = _client()
        body = c.build_media_message_payload(
            chat_id="7", media=PlatformMedia(kind="photo", url="http://u/i.png", caption="hi")
        )
        self.assertNotIn("chat_id", body)
        self.assertEqual(body["text"], "hi")
        att = body["attachments"][0]
        self.assertEqual(att["type"], "image")
        self.assertEqual(att["payload"], {"url": "http://u/i.png"})

    def test_video_token_payload(self):
        c, _ = _client()
        body = c.build_media_message_payload(
            chat_id="7", media=PlatformMedia(kind="video", url="http://u/v.mp4"), token="TOK"
        )
        att = body["attachments"][0]
        self.assertEqual(att["type"], "video")
        self.assertEqual(att["payload"], {"token": "TOK"})  # token wins over url
        self.assertNotIn("text", body)

    def test_document_type_maps_to_file(self):
        c, _ = _client()
        body = c.build_media_message_payload(
            chat_id="7", media=PlatformMedia(kind="document", url="http://u/f.pdf")
        )
        self.assertEqual(body["attachments"][0]["type"], "file")

    def test_keyboard_appended_as_extra_attachment(self):
        c, _ = _client()
        kb = Keyboard.single(Button.callback("Menu", "m:menu"))
        body = c.build_media_message_payload(
            chat_id="7", media=PlatformMedia(kind="photo", url="http://u/i.png"), keyboard=kb
        )
        self.assertEqual(len(body["attachments"]), 2)


class SendTests(unittest.TestCase):
    def test_send_photo_url_posts_messages(self):
        c, sess = _client([_FakeResp(json_body={"ok": True})])
        run(c.send_photo("7", PlatformMedia(kind="photo", url="http://u/i.png")))
        method, url = sess.calls[0][1], sess.calls[0][2]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/messages"))
        sent = sess.calls[0][3]["json"]
        self.assertEqual(sess.calls[0][3]["params"], {"chat_id": "7"})
        self.assertEqual(sent["attachments"][0]["payload"], {"url": "http://u/i.png"})

    def test_send_video_bytes_uploads_then_sends_with_token(self):
        # POST /uploads -> {url}; multipart POST -> {token}; POST /messages -> ok
        responses = [
            _FakeResp(json_body={"url": "http://upload/here"}),
            _FakeResp(json_body={"token": "UPLOADED"}),
            _FakeResp(json_body={"ok": True}),
        ]
        c, sess = _client(responses)
        run(c.send_video("7", PlatformMedia(kind="video", bytes_data=b"\x00\x01")))
        # last call is the message with the uploaded token
        last = sess.calls[-1]
        self.assertTrue(last[2].endswith("/messages"))
        self.assertEqual(last[3]["json"]["attachments"][0]["payload"], {"token": "UPLOADED"})
        upload = [call for call in sess.calls if call[0] == "post"][0]
        self.assertEqual(upload[2], "http://upload/here")
        self.assertEqual(upload[3]["headers"], {"Authorization": "TESTTOKEN"})
        self.assertIn("data", upload[3])

    def test_get_file_bytes_downloads_url(self):
        c, sess = _client([_FakeResp(raw=b"IMGDATA")])
        got = run(c.get_file_bytes(PlatformFile(file_id="f", url="http://cdn/f.png")))
        self.assertEqual(got, b"IMGDATA")
        self.assertEqual(sess.calls[0][0], "get")
        self.assertEqual(sess.calls[0][2], "http://cdn/f.png")

    def test_get_file_bytes_without_url_raises(self):
        c, _ = _client()
        with self.assertRaises(ValueError):
            run(c.get_file_bytes(PlatformFile(file_id="f")))

    def test_edit_message_puts_with_message_id(self):
        c, sess = _client([_FakeResp(json_body={"ok": True})])
        run(c.edit_message("7", "42", "new text"))
        method, url, kwargs = sess.calls[0][1], sess.calls[0][2], sess.calls[0][3]
        self.assertEqual(method, "PUT")
        self.assertTrue(url.endswith("/messages"))
        self.assertEqual(kwargs["params"]["message_id"], "42")
        self.assertEqual(kwargs["json"]["text"], "new text")

    def test_send_message_passes_chat_id_as_query_parameter(self):
        c, sess = _client([_FakeResp(json_body={"ok": True})])
        run(c.send_message("7", "hello"))
        kwargs = sess.calls[0][3]
        self.assertEqual(kwargs["params"], {"chat_id": "7"})
        self.assertNotIn("chat_id", kwargs["json"])

    def test_answer_callback_passes_callback_id_as_query_parameter(self):
        c, sess = _client([_FakeResp(json_body={"ok": True})])
        run(c.answer_callback("cb-1", "done"))
        kwargs = sess.calls[0][3]
        self.assertEqual(kwargs["params"], {"callback_id": "cb-1"})
        self.assertEqual(kwargs["json"], {"notification": "done"})

    def test_get_updates_supports_documented_limit_timeout_and_types(self):
        c, sess = _client([_FakeResp(json_body={"updates": []})])
        run(c.get_updates(marker=12, limit=5000, timeout=120, types=("message_created", "message_callback")))
        self.assertEqual(sess.calls[0][3]["params"], {
            "limit": 1000,
            "timeout": 90,
            "marker": 12,
            "types": "message_created,message_callback",
        })

    def test_subscription_methods_match_documented_contract(self):
        c, sess = _client([
            _FakeResp(json_body=[]),
            _FakeResp(json_body={"success": True}),
            _FakeResp(json_body={"success": True}),
        ])
        run(c.get_subscriptions())
        run(c.subscribe_webhook(
            url="https://bot.example/max/webhook",
            secret="secret-1",
            update_types=("message_created", "message_callback"),
        ))
        run(c.unsubscribe_webhook(url="https://bot.example/max/webhook"))
        self.assertEqual(sess.calls[0][1:3], ("GET", f"{c.base_url}/subscriptions"))
        self.assertEqual(sess.calls[1][3]["json"]["secret"], "secret-1")
        self.assertEqual(sess.calls[2][3]["params"], {"url": "https://bot.example/max/webhook"})


if __name__ == "__main__":
    unittest.main()
