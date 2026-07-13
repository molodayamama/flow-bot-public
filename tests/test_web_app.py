"""Offline HTTP/security contracts for the first-party browser channel."""
from __future__ import annotations

import asyncio
import base64
import logging
import tempfile
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from channels.web.app import WebAppConfig, WebAppDeps, register_web_app


ORIGIN = "https://photozhab.test"


class _Metrics:
    def __init__(self) -> None:
        self.identities: dict[tuple[str, str], int] = {}
        self.credits: dict[int, int] = {}
        self.events = []

    def ensure_user_identity(self, platform, platform_user_id):
        key = (str(platform), str(platform_user_id))
        if key not in self.identities:
            self.identities[key] = -(len(self.identities) + 1)
        return self.identities[key]

    def get_user_identity(self, platform, platform_user_id):
        internal_id = self.identities.get((str(platform), str(platform_user_id)))
        return {"internal_user_id": internal_id} if internal_id is not None else None

    def credits_balance(self, user_id, starter):
        return self.credits.setdefault(int(user_id), int(starter))

    def credits_charge(self, user_id, amount, starter):
        balance = self.credits_balance(user_id, starter)
        if balance < amount:
            return False
        self.credits[int(user_id)] = balance - int(amount)
        return True

    def credits_refund(self, user_id, amount):
        self.credits[int(user_id)] = self.credits.get(int(user_id), 0) + int(amount)

    def log_event(self, *args, **kwargs):
        self.events.append((args, kwargs))


class WebAppHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.metrics = _Metrics()
        self.backend_calls = []
        self.backend_result = {"images": [{"url": "https://media.example/result.png"}]}
        self.backend_wait: asyncio.Event | None = None
        self.backend_started: asyncio.Event | None = None
        self.payment_calls = []

        async def backend(request):
            self.backend_calls.append(request)
            if self.backend_started is not None:
                self.backend_started.set()
            if self.backend_wait is not None:
                await self.backend_wait.wait()
            return self.backend_result

        def payment_url(user_id, pack_id, inv_id, channel):
            self.payment_calls.append((user_id, pack_id, inv_id, channel))
            return f"https://pay.example/invoice?pack={pack_id}&channel={channel}"

        self.config = WebAppConfig(
            enabled=True,
            session_secret="test-secret-that-is-longer-than-thirty-two-characters",
            public_origin=ORIGIN,
            cookie_secure=False,
            starter_credits=100,
            media_dir=Path(self.temp.name),
            rate_limit_count=6,
        )
        deps = WebAppDeps(
            config=self.config,
            backend_generate=backend,
            metrics=self.metrics,
            credit_pack=lambda pack: {"credits": 45, "stars": 35} if pack == "trial" else None,
            public_pack_ids=lambda: ["trial"],
            robokassa_pack_amount=lambda pack: "40.95",
            robokassa_configured=lambda: True,
            new_inv_id=lambda: 12345,
            payment_url=payment_url,
            log=logging.getLogger(__name__),
        )
        app = web.Application(client_max_size=12 * 1024 * 1024)
        self.assertTrue(register_web_app(app, deps))
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        response = await self.client.get("/web/api/session")
        self.assertEqual(response.status, 200)
        self.session_payload = await response.json()
        self.assertEqual(self.metrics.identities, {})

    async def asyncTearDown(self) -> None:
        await self.client.close()
        self.temp.cleanup()

    async def post(self, path, payload, *, origin=ORIGIN):
        return await self.client.post(path, json=payload, headers={"Origin": origin})

    async def test_session_cookie_is_opaque_http_only_and_catalog_has_no_provider_keys(self):
        response = await self.client.get("/web/api/session")
        payload = await response.json()
        cookie = response.headers.get("Set-Cookie", "")
        # The existing valid cookie is not needlessly reissued.
        self.assertEqual(cookie, "")
        self.assertEqual(payload["balance"], 100)
        self.assertEqual(payload["image_models"][0]["id"], "nb2")
        self.assertNotIn("key", payload["image_models"][0])
        self.assertNotIn("key", payload["video_models"][0])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.metrics.identities, {})

    async def test_first_session_sets_hardened_cookie(self):
        self.client.session.cookie_jar.clear()
        response = await self.client.get("/web/api/session")
        cookie = response.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn("test-secret", cookie)

    async def test_cross_origin_generation_is_rejected_before_backend(self):
        response = await self.post("/web/api/generate", {"mode": "image", "prompt": "safe prompt"}, origin="https://evil.test")
        self.assertEqual(response.status, 403)
        self.assertEqual(self.backend_calls, [])
        self.assertEqual(self.metrics.identities, {})

    async def test_successful_image_generation_charges_server_price_and_hides_internal_ids(self):
        response = await self.post("/web/api/generate", {
            "mode": "image", "prompt": "safe prompt", "image_model": "nbpro",
            "aspect": "square", "count": 2, "price": 1, "user_id": 777,
        })
        payload = await response.json()
        self.assertEqual(response.status, 200, payload)
        self.assertEqual(payload["charged"], 30)
        self.assertEqual(payload["balance"], 70)
        self.assertEqual(payload["media"], [{"type": "image", "url": "https://media.example/result.png"}])
        self.assertNotIn("account_id", payload)
        self.assertLess(self.backend_calls[0]["user_id"], 0)
        self.assertEqual(self.backend_calls[0]["num_images"], 2)

    async def test_backend_failure_refunds_reserved_credits(self):
        self.backend_result = {"error": "provider body must not escape"}
        response = await self.post("/web/api/generate", {"mode": "image", "prompt": "safe prompt", "count": 1})
        payload = await response.json()
        self.assertEqual(response.status, 502)
        self.assertEqual(payload, {"error": "generation_failed"})
        internal_id = next(iter(self.metrics.credits))
        self.assertEqual(self.metrics.credits[internal_id], 100)
        self.assertNotIn("provider body", str(payload))

    async def test_insufficient_balance_never_calls_backend(self):
        seeded = await self.post(
            "/web/api/generate", {"mode": "image", "prompt": "seed identity"}
        )
        self.assertEqual(seeded.status, 200)
        internal_id = next(iter(self.metrics.credits))
        self.metrics.credits[internal_id] = 0
        self.backend_calls.clear()
        response = await self.post("/web/api/generate", {"mode": "video", "prompt": "safe video", "video_model": "veo-lite"})
        payload = await response.json()
        self.assertEqual(response.status, 402)
        self.assertEqual(payload["error"], "insufficient_credits")
        self.assertEqual(payload["required"], 60)
        self.assertEqual(self.backend_calls, [])

    async def test_photo_mode_requires_strict_supported_base64_image(self):
        response = await self.post("/web/api/generate", {"mode": "edit", "prompt": "change it", "image_b64": base64.b64encode(b"not an image").decode()})
        self.assertEqual(response.status, 400)
        self.assertEqual((await response.json())["error"], "invalid_image")
        self.assertEqual(self.backend_calls, [])
        self.assertEqual(self.metrics.identities, {})

    async def test_one_generation_per_session_is_enforced(self):
        self.backend_wait = asyncio.Event()
        self.backend_started = asyncio.Event()
        first = asyncio.create_task(self.post("/web/api/generate", {"mode": "image", "prompt": "first prompt"}))
        await self.backend_started.wait()
        second = await self.post("/web/api/generate", {"mode": "image", "prompt": "second prompt"})
        self.assertEqual(second.status, 409)
        self.backend_wait.set()
        self.assertEqual((await first).status, 200)

    async def test_video_is_bounded_stored_and_session_bound(self):
        video = b"\x00\x00\x00\x18ftypmp42" + b"safe-video"
        self.backend_result = {"videos": [{"video_b64": base64.b64encode(video).decode()}]}
        response = await self.post("/web/api/generate", {"mode": "video", "prompt": "safe video", "video_model": "omni-flash-4s"})
        payload = await response.json()
        self.assertEqual(response.status, 200, payload)
        media_url = payload["media"][0]["url"]
        media = await self.client.get(media_url)
        self.assertEqual(media.status, 200)
        self.assertEqual(await media.read(), video)
        self.client.session.cookie_jar.clear()
        await self.client.get("/web/api/session")
        denied = await self.client.get(media_url)
        self.assertEqual(denied.status, 404)

    async def test_payment_uses_server_pack_negative_identity_and_web_channel(self):
        response = await self.post("/web/api/payment", {"pack_id": "trial", "user_id": 99})
        payload = await response.json()
        self.assertEqual(response.status, 200, payload)
        self.assertTrue(payload["url"].startswith("https://pay.example/"))
        user_id, pack_id, inv_id, channel = self.payment_calls[0]
        self.assertLess(user_id, 0)
        self.assertEqual((pack_id, inv_id, channel), ("trial", 12345, "web"))

    async def test_unknown_payment_pack_is_rejected(self):
        response = await self.post("/web/api/payment", {"pack_id": "admin-test"})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.payment_calls, [])
        self.assertEqual(self.metrics.identities, {})

    async def test_payment_invoice_creation_is_rate_limited(self):
        for _ in range(10):
            response = await self.post("/web/api/payment", {"pack_id": "trial"})
            self.assertEqual(response.status, 200)
        blocked = await self.post("/web/api/payment", {"pack_id": "trial"})
        self.assertEqual(blocked.status, 429)
        self.assertEqual(len(self.payment_calls), 10)


class WebAppDisabledTests(unittest.TestCase):
    def test_disabled_app_registers_no_routes(self):
        app = web.Application()
        deps = WebAppDeps(
            config=WebAppConfig(enabled=False, session_secret=""),
            backend_generate=None, metrics=None, credit_pack=None, public_pack_ids=None,
            robokassa_pack_amount=None, robokassa_configured=None, new_inv_id=None,
            payment_url=None, log=None,
        )
        self.assertFalse(register_web_app(app, deps))
        self.assertEqual(list(app.router.routes()), [])


if __name__ == "__main__":
    unittest.main()
