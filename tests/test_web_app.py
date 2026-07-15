"""Offline HTTP/security contracts for the first-party browser channel."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from channels.web.app import WebAppConfig, WebAppDeps, register_web_app


ORIGIN = "https://photozhab.test"


def _max_init_data(token: str, *, user_id: int = 55, auth_date: int | None = None) -> str:
    values = {
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
        "query_id": "query-once",
        "user": json.dumps({"id": user_id, "first_name": "Max", "last_name": "User"}, separators=(",", ":")),
    }
    launch = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, launch.encode(), hashlib.sha256).hexdigest()
    from urllib.parse import urlencode
    return urlencode(values)


class _Metrics:
    def __init__(self) -> None:
        self.identities: dict[tuple[str, str], int] = {}
        self.credits: dict[int, int] = {}
        self.events = []
        self.auth_sessions = {}
        self.challenges = {}
        self.oauth_states = {}
        self.assertions = set()
        self.welcome_grants = set()

    def ensure_user_identity(self, platform, platform_user_id):
        key = (str(platform), str(platform_user_id))
        if key not in self.identities:
            self.identities[key] = int(platform_user_id) if str(platform) == "telegram" else -(len(self.identities) + 1)
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

    def get_web_auth_session(self, sid):
        return self.auth_sessions.get(sid)

    def bind_web_auth_session(self, sid, platform, platform_user_id, display_name, **kwargs):
        internal_id = self.ensure_user_identity(platform, platform_user_id)
        value = {
            "platform": str(platform), "platform_user_id": str(platform_user_id),
            "internal_user_id": internal_id, "display_name": display_name,
        }
        self.auth_sessions[sid] = value
        return value

    def delete_web_auth_session(self, sid):
        return self.auth_sessions.pop(sid, None) is not None

    def grant_identity_welcome_credits(self, platform, platform_user_id, internal_user_id, credits):
        key = (str(platform), str(platform_user_id))
        granted = key not in self.welcome_grants
        if granted:
            self.welcome_grants.add(key)
            self.credits[int(internal_user_id)] = self.credits.get(int(internal_user_id), 0) + int(credits)
        return {"granted": granted, "balance": self.credits.get(int(internal_user_id), 0)}

    def create_web_login_challenge(self, sid, challenge, **kwargs):
        self.challenges[challenge] = {"sid": sid}
        return True

    def claim_web_login_challenge(self, challenge, platform, platform_user_id, display_name, code, **kwargs):
        item = self.challenges.get(challenge)
        if not item or item.get("identity"):
            return False
        item["identity"] = {
            "platform": platform, "platform_user_id": str(platform_user_id), "display_name": display_name,
        }
        item["code"] = code
        return True

    def complete_web_login_challenge(self, sid, code, **kwargs):
        for item in self.challenges.values():
            if item.get("sid") == sid and item.get("code") == code and not item.get("used"):
                item["used"] = True
                return item["identity"]
        return None

    def create_web_oauth_state(self, sid, state, provider, verifier, **kwargs):
        self.oauth_states[(sid, state, provider)] = verifier
        return True

    def consume_web_oauth_state(self, sid, state, provider, **kwargs):
        return self.oauth_states.pop((sid, state, provider), None)

    def consume_web_auth_assertion(self, provider, assertion, **kwargs):
        key = (provider, assertion)
        if key in self.assertions:
            return False
        self.assertions.add(key)
        return True


class WebAppHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.metrics = _Metrics()
        self.backend_calls = []
        self.prompt_calls = []
        self.prompt_result = {
            "variants": [
                {"title": "Кино", "prompt": "cinematic portrait with soft light"},
                {"title": "Документальный", "prompt": "documentary portrait in natural light"},
            ]
        }
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

        async def prompt_improve(user_id, prompt, mode):
            self.prompt_calls.append((user_id, prompt, mode))
            return self.prompt_result

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
            max_bot_token="max-test-token",
            yandex_client_id="yandex-client",
            yandex_client_secret="yandex-secret",
        )
        deps = WebAppDeps(
            config=self.config,
            backend_generate=backend,
            prompt_improve=prompt_improve,
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
        self.anonymous_payload = await response.json()
        self.assertEqual(self.metrics.identities, {})
        await self.login_telegram()
        response = await self.client.get("/web/api/session")
        self.session_payload = await response.json()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        self.temp.cleanup()

    async def post(self, path, payload, *, origin=ORIGIN):
        return await self.client.post(path, json=payload, headers={"Origin": origin})

    async def login_telegram(self, *, user_id=777, code="123456"):
        response = await self.post("/web/api/auth/telegram/start", {})
        self.assertEqual(response.status, 200)
        url = (await response.json())["url"]
        payload = parse_qs(urlparse(url).query)["start"][0]
        self.assertTrue(payload.startswith("web_"))
        self.assertTrue(self.metrics.claim_web_login_challenge(
            payload.removeprefix("web_"), "telegram", user_id, "Test User", code
        ))
        response = await self.post("/web/api/auth/telegram/complete", {"code": code})
        self.assertEqual(response.status, 200, await response.json())
        return response

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
        self.assertTrue(payload["authenticated"])
        self.assertEqual(payload["identity"]["provider"], "telegram")

    async def test_first_session_sets_hardened_cookie(self):
        self.client.session.cookie_jar.clear()
        response = await self.client.get("/web/api/session")
        cookie = response.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn("test-secret", cookie)

    async def test_landing_experiment_accepts_only_bounded_same_origin_events(self):
        self.client.session.cookie_jar.clear()
        before = len(self.metrics.events)
        response = await self.post(
            "/web/api/experiment",
            {"name": "landing_hero", "variant": "c", "event": "exposure"},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(), {"ok": True})
        args, kwargs = self.metrics.events[-1]
        self.assertEqual(args, ("landing_hero_exposure",))
        self.assertIsNone(kwargs["user_id"])
        self.assertEqual(kwargs["source"], "web")
        self.assertEqual(kwargs["payload"], {"variant": "c"})

        for payload, origin in (
            ({"name": "landing_hero", "variant": "z", "event": "exposure"}, ORIGIN),
            ({"name": "other", "variant": "a", "event": "cta"}, ORIGIN),
            ({"name": "landing_hero", "variant": "a", "event": "arbitrary"}, ORIGIN),
            ({"name": "landing_hero", "variant": "a", "event": "cta"}, "https://evil.test"),
        ):
            rejected = await self.post("/web/api/experiment", payload, origin=origin)
            self.assertIn(rejected.status, {400, 403})
        oversized = await self.post(
            "/web/api/experiment",
            {"name": "landing_hero", "variant": "a", "event": "x" * 600},
        )
        self.assertEqual(oversized.status, 413)
        self.assertEqual(len(self.metrics.events), before + 1)

    async def test_anonymous_session_cannot_generate_or_pay(self):
        self.client.session.cookie_jar.clear()
        response = await self.client.get("/web/api/session")
        payload = await response.json()
        self.assertFalse(payload["authenticated"])
        self.assertEqual(payload["packs"], [])
        generated = await self.post("/web/api/generate", {"mode": "image", "prompt": "safe prompt"})
        paid = await self.post("/web/api/payment", {"pack_id": "trial"})
        self.assertEqual(generated.status, 401)
        self.assertEqual(paid.status, 401)
        self.assertEqual(self.backend_calls, [])
        self.assertEqual(self.payment_calls, [])

    async def test_prompt_improve_uses_backend_variants_and_canonical_price(self):
        response = await self.post(
            "/web/api/prompt-improve", {"prompt": "portrait of a fox", "mode": "image"}
        )
        payload = await response.json()
        self.assertEqual(response.status, 200, payload)
        self.assertEqual(payload["variants"][0], {"tag": "Кино", "text": "cinematic portrait with soft light"})
        self.assertEqual(payload["charged"], 5)
        self.assertEqual(payload["balance"], 95)
        self.assertEqual(self.prompt_calls, [(777, "portrait of a fox", "image")])

    async def test_prompt_improve_failure_refunds_and_hides_provider_body(self):
        self.prompt_result = {"error": "provider token must not escape"}
        response = await self.post(
            "/web/api/prompt-improve", {"prompt": "portrait of a fox", "mode": "video"}
        )
        self.assertEqual(response.status, 502)
        self.assertEqual(await response.json(), {"error": "prompt_improve_failed"})
        internal_id = next(iter(self.metrics.credits))
        self.assertEqual(self.metrics.credits[internal_id], 100)

    async def test_prompt_improve_insufficient_balance_never_calls_backend(self):
        internal_id = next(iter(self.metrics.credits))
        self.metrics.credits[internal_id] = 0
        response = await self.post(
            "/web/api/prompt-improve", {"prompt": "portrait of a fox", "mode": "image"}
        )
        payload = await response.json()
        self.assertEqual(response.status, 402)
        self.assertEqual(payload["required"], 5)
        self.assertEqual(self.prompt_calls, [])

    async def test_prompt_improve_requires_auth_and_does_not_call_backend(self):
        self.client.session.cookie_jar.clear()
        await self.client.get("/web/api/session")
        response = await self.post(
            "/web/api/prompt-improve", {"prompt": "portrait of a fox", "mode": "image"}
        )
        self.assertEqual(response.status, 401)
        self.assertEqual(self.prompt_calls, [])

    async def test_cross_origin_generation_is_rejected_before_backend(self):
        response = await self.post("/web/api/generate", {"mode": "image", "prompt": "safe prompt"}, origin="https://evil.test")
        self.assertEqual(response.status, 403)
        self.assertEqual(self.backend_calls, [])

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
        self.assertEqual(self.backend_calls[0]["user_id"], 777)
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

    async def test_payment_uses_authenticated_telegram_identity_and_web_channel(self):
        response = await self.post("/web/api/payment", {"pack_id": "trial", "user_id": 99})
        payload = await response.json()
        self.assertEqual(response.status, 200, payload)
        self.assertTrue(payload["url"].startswith("https://pay.example/"))
        user_id, pack_id, inv_id, channel = self.payment_calls[0]
        self.assertEqual(user_id, 777)
        self.assertEqual((pack_id, inv_id, channel), ("trial", 12345, "web"))

    async def test_unknown_payment_pack_is_rejected(self):
        response = await self.post("/web/api/payment", {"pack_id": "admin-test"})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.payment_calls, [])

    async def test_telegram_code_is_single_use_and_logout_revokes_session(self):
        response = await self.post("/web/api/auth/logout", {})
        self.assertEqual(response.status, 200)
        response = await self.client.get("/web/api/session")
        self.assertFalse((await response.json())["authenticated"])

        started = await self.post("/web/api/auth/telegram/start", {})
        url = (await started.json())["url"]
        token = parse_qs(urlparse(url).query)["start"][0].removeprefix("web_")
        self.assertTrue(self.metrics.claim_web_login_challenge(
            token, "telegram", 888, "Other User", "654321"
        ))
        wrong = await self.post("/web/api/auth/telegram/complete", {"code": "000000"})
        self.assertEqual(wrong.status, 401)
        accepted = await self.post("/web/api/auth/telegram/complete", {"code": "654321"})
        self.assertEqual(accepted.status, 200)
        replayed = await self.post("/web/api/auth/telegram/complete", {"code": "654321"})
        self.assertEqual(replayed.status, 401)
        current = await self.client.get("/web/api/session")
        self.assertEqual((await current.json())["identity"]["display_name"], "Other User")

    async def test_signed_max_mini_app_login_is_verified_and_not_replayable(self):
        await self.post("/web/api/auth/logout", {})
        init_data = _max_init_data(self.config.max_bot_token)
        response = await self.post("/web/api/auth/max", {"init_data": init_data})
        payload = await response.json()
        self.assertEqual(response.status, 200, payload)
        self.assertEqual(payload["identity"]["provider"], "max")
        self.assertLess(self.metrics.auth_sessions[next(iter(self.metrics.auth_sessions))]["internal_user_id"], 0)

        await self.post("/web/api/auth/logout", {})
        replay = await self.post("/web/api/auth/max", {"init_data": init_data})
        self.assertEqual(replay.status, 409)
        tampered = await self.post("/web/api/auth/max", {"init_data": init_data.replace("Max", "Mallory")})
        self.assertEqual(tampered.status, 401)

    async def test_yandex_oauth_state_is_cookie_bound_and_rotates_session(self):
        await self.post("/web/api/auth/logout", {})
        started = await self.client.get("/web/api/auth/yandex/start", allow_redirects=False)
        self.assertEqual(started.status, 302)
        redirect = urlparse(started.headers["Location"])
        self.assertEqual(redirect.netloc, "oauth.yandex.ru")
        params = parse_qs(redirect.query)
        self.assertEqual(params["code_challenge_method"], ["S256"])
        state = params["state"][0]
        fake_identity = {
            "platform": "yandex", "platform_user_id": "ya-55", "display_name": "Yandex User",
        }
        with patch(
            "channels.web.app._fetch_yandex_identity",
            new=AsyncMock(return_value=fake_identity),
        ):
            callback = await self.client.get(
                f"/web/api/auth/yandex/callback?code=one-time-code&state={state}",
                allow_redirects=False,
            )
        self.assertEqual(callback.status, 302)
        self.assertEqual(callback.headers["Location"], "/app.html?auth=success")
        current = await self.client.get("/web/api/session")
        payload = await current.json()
        self.assertTrue(payload["authenticated"])
        self.assertEqual(payload["identity"]["provider"], "yandex")
        self.assertEqual(payload["balance"], 30)
        self.assertEqual(self.metrics.welcome_grants, {("yandex", "ya-55")})

        await self.post("/web/api/auth/logout", {})
        started_again = await self.client.get("/web/api/auth/yandex/start", allow_redirects=False)
        state_again = parse_qs(urlparse(started_again.headers["Location"]).query)["state"][0]
        with patch(
            "channels.web.app._fetch_yandex_identity",
            new=AsyncMock(return_value=fake_identity),
        ):
            callback_again = await self.client.get(
                f"/web/api/auth/yandex/callback?code=another-code&state={state_again}",
                allow_redirects=False,
            )
        self.assertEqual(callback_again.headers["Location"], "/app.html?auth=success")
        current_again = await self.client.get("/web/api/session")
        self.assertEqual((await current_again.json())["balance"], 30)

    async def test_yandex_callback_rejects_different_browser_cookie(self):
        await self.post("/web/api/auth/logout", {})
        started = await self.client.get("/web/api/auth/yandex/start", allow_redirects=False)
        state = parse_qs(urlparse(started.headers["Location"]).query)["state"][0]
        self.client.session.cookie_jar.clear()
        callback = await self.client.get(
            f"/web/api/auth/yandex/callback?code=code&state={state}",
            allow_redirects=False,
        )
        self.assertEqual(callback.status, 302)
        self.assertEqual(callback.headers["Location"], "/app.html?auth=failed")

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
