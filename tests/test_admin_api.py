from __future__ import annotations

import json
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import admin_api
import metrics


class WipeProfileDirTests(unittest.TestCase):
    def test_wipes_only_profile_dirs(self) -> None:
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            prof = Path(tmp) / "google_profile_zz"
            prof.mkdir()
            (prof / "Preferences").write_text("{}", encoding="utf-8")
            other = Path(tmp) / "important_data"
            other.mkdir()
            (other / "keep.txt").write_text("x", encoding="utf-8")
            # Профильный каталог — удаляется.
            admin_api._wipe_profile_dir(str(prof))
            self.assertFalse(prof.exists())
            # Непрофильный — НЕ трогаем (защита от случайного rmtree).
            admin_api._wipe_profile_dir(str(other))
            self.assertTrue(other.exists())
            # Пустой путь — без ошибок.
            admin_api._wipe_profile_dir("")


class _FakePool:
    def __init__(self, accounts):
        self._accounts = accounts

    def status(self):
        return self._accounts

    def get(self, account_id):
        for item in self._accounts:
            if item.get("id") == account_id:
                return SimpleNamespace(
                    profile_dir=item.get("profile_dir"),
                    browser_proxy_url=item.get("proxy"),
                    api_proxy_url=item.get("proxy"),
                )
        return None

    def remove_account(self, account_id):
        before = len(self._accounts)
        self._accounts = [a for a in self._accounts if a.get("id") != account_id]
        return len(self._accounts) != before

    def account_ids(self):
        return [str(a.get("id")) for a in self._accounts]

    def set_runtime_ready(self, account_id, ready, status=None):
        for item in self._accounts:
            if item.get("id") == account_id:
                item["runtime_ready"] = bool(ready)
                item["runtime_status"] = status
                return True
        return False


class _FakeKeeper:
    def __init__(self, credits_dict=None, raises=False, delay=0.0):
        self._credits = credits_dict
        self._raises = raises
        self._delay = delay
        self.closed = False

    async def get_g_credits(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raises:
            raise RuntimeError("boom")
        return self._credits

    async def close(self):
        self.closed = True


class _FakeVideoClient:
    def __init__(self, result=None):
        self.calls = []
        self.image_calls = []
        self._result = result or {
            "arms": [
                {"transport": "direct_http", "status": 403, "ok": False},
                {"transport": "browser_fetch", "status": 200, "ok": True},
            ]
        }

    async def video_transport_ab_test(self, **kwargs):
        self.calls.append(kwargs)
        return self._result

    async def generate_images(self, **kwargs):
        self.image_calls.append(kwargs)
        return {"responses": [{"generatedImage": {"mediaStoreUri": "media://one"}}]}


class _FakeOnboardSession:
    def __init__(self, account_id="sub7", profile_dir="./google_profile_sub7", proxy_url=""):
        self.account_id = account_id
        self.profile_dir = profile_dir
        self.proxy_url = proxy_url
        self.status = "needs_2fa"
        self.closed = False
        self.codes = []

    async def submit_2fa_code(self, code):
        self.codes.append(code)
        self.status = "active"
        return {"ok": True, "status": "active", "reason": "flow_opened"}

    async def recheck(self):
        self.status = "active"
        return {"ok": True, "status": "active", "reason": "project_opened"}

    async def close(self):
        self.closed = True


class _JsonReq:
    headers = {}
    remote = "test"

    def __init__(self, body, match_info=None):
        self._body = body
        self.match_info = match_info or {}

    async def json(self):
        return self._body


class AdminSecurityBoundaryTests(unittest.TestCase):
    def test_admin_json_responses_are_not_cacheable_or_sniffable(self) -> None:
        response = admin_api._json({"ok": True})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_admin_access_allows_loopback_and_rejects_external_without_token(self) -> None:
        local = SimpleNamespace(remote="127.0.0.1", headers={})
        external = SimpleNamespace(remote="203.0.113.10", headers={})

        self.assertTrue(admin_api._admin_request_allowed(local))
        with patch.dict(os.environ, {"ADMIN_API_TOKEN": ""}):
            self.assertFalse(admin_api._admin_request_allowed(external))

    def test_admin_access_allows_external_only_with_configured_bearer_token(self) -> None:
        token = "admin-token-" + "x" * 32
        request = SimpleNamespace(
            remote="203.0.113.10",
            headers={"Authorization": f"Bearer {token}"},
        )

        with patch.dict(os.environ, {"ADMIN_API_TOKEN": token}):
            self.assertTrue(admin_api._admin_request_allowed(request))


class AdminUserControlsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        metrics.close()
        metrics.init_db(str(Path(self.temp.name) / "metrics.db"))

    def tearDown(self):
        metrics.close()
        self.temp.cleanup()

    async def test_credit_channel_and_seed_handlers_update_existing_user(self):
        user_id = metrics.ensure_user_identity("yandex", "ya-55")

        credit_resp = await admin_api.handle_user_credits_post(
            _JsonReq({"amount": 7}, {"id": str(user_id)})
        )
        credit_body = json.loads(credit_resp.body)
        self.assertEqual(credit_resp.status, 200)
        self.assertTrue(credit_body["ok"])
        self.assertEqual(credit_body["balance"], 7)

        channel_resp = await admin_api.handle_user_channel_post(
            _JsonReq({"channel": "Seed_A"}, {"id": str(user_id)})
        )
        channel_body = json.loads(channel_resp.body)
        self.assertEqual(channel_resp.status, 200)
        self.assertEqual(channel_body["acq_channel"], "seed_a")
        self.assertEqual(metrics.get_user_profile(user_id)["acq_channel"], "seed_a")

        detach_resp = await admin_api.handle_user_seed_detach_post(
            _JsonReq({}, {"id": str(user_id)})
        )
        detach_body = json.loads(detach_resp.body)
        self.assertEqual(detach_resp.status, 200)
        self.assertIsNone(detach_body["acq_channel"])
        self.assertIsNone(metrics.get_user_profile(user_id)["acq_channel"])

    async def test_user_controls_reject_bad_input_and_missing_users(self):
        user_id = metrics.ensure_user_identity("max", "mx-1")

        bad_credit = await admin_api.handle_user_credits_post(
            _JsonReq({"amount": 0}, {"id": str(user_id)})
        )
        self.assertEqual(bad_credit.status, 400)

        missing = await admin_api.handle_user_credits_post(
            _JsonReq({"amount": 5}, {"id": "123456"})
        )
        self.assertEqual(missing.status, 404)

        bad_channel = await admin_api.handle_user_channel_post(
            _JsonReq({"channel": "bad seed"}, {"id": str(user_id)})
        )
        self.assertEqual(bad_channel.status, 400)


class AccountsEndpointGCreditsTests(unittest.IsolatedAsyncioTestCase):
    """handle_accounts_get() attaches a live g_credits field per account."""

    def setUp(self):
        self.addCleanup(self._reset_globals)

    def _reset_globals(self):
        admin_api._ctx.pool = None
        admin_api._ctx.keepers = None
        admin_api._ctx.video_clients = None
        admin_api._ctx.startup_state = None
        admin_api.GCREDITS_LOOKUP_TIMEOUT_SEC = 3.0

    async def test_g_credits_attached_when_keepers_present(self):
        pool = _FakePool([
            {"id": "a1", "disabled": False, "cooldown_left": 0, "fails": 0},
            {"id": "a2", "disabled": False, "cooldown_left": 0, "fails": 0},
        ])
        keepers = {
            "a1": _FakeKeeper({"credits": 50, "is_paid": False}),
            "a2": _FakeKeeper(raises=True),
        }
        admin_api._ctx.pool = pool
        admin_api._ctx.keepers = keepers

        resp = await admin_api.handle_accounts_get(None)
        body = json.loads(resp.body)
        by_id = {a["id"]: a for a in body}
        self.assertEqual(by_id["a1"]["g_credits"], {"credits": 50, "is_paid": False})
        self.assertIsNone(by_id["a2"]["g_credits"])  # exception -> None, not a crash

    async def test_g_credits_absent_without_keepers(self):
        pool = _FakePool([{"id": "a1", "disabled": False, "cooldown_left": 0, "fails": 0}])
        admin_api._ctx.pool = pool
        admin_api._ctx.keepers = None

        resp = await admin_api.handle_accounts_get(None)
        body = json.loads(resp.body)
        self.assertIsNone(body[0]["g_credits"])

    async def test_slow_g_credits_does_not_block_accounts_response(self):
        pool = _FakePool([
            {"id": "a1", "disabled": False, "cooldown_left": 0, "fails": 0},
        ])
        admin_api._ctx.pool = pool
        admin_api._ctx.keepers = {"a1": _FakeKeeper({"credits": 50}, delay=0.05)}
        admin_api.GCREDITS_LOOKUP_TIMEOUT_SEC = 0.001

        resp = await admin_api.handle_accounts_get(None)
        body = json.loads(resp.body)
        self.assertEqual(body[0]["id"], "a1")
        self.assertIsNone(body[0]["g_credits"])

    async def test_accounts_include_startup_warming_status(self):
        pool = _FakePool([
            {"id": "a1", "disabled": False, "cooldown_left": 0, "fails": 0},
        ])
        admin_api._ctx.pool = pool
        admin_api._ctx.startup_state = {
            "phase": "warming",
            "accounts": {"a1": {"status": "running", "ready": False}},
        }

        resp = await admin_api.handle_accounts_get(None)
        body = json.loads(resp.body)

        self.assertEqual(body[0]["health"], "warming")
        self.assertEqual(body[0]["warmup_status"], "running")
        self.assertEqual(body[0]["startup"]["ready"], False)


class _FakeProxySup:
    def __init__(self):
        self.raised = []
        self.tore_down = []

    def ports_view(self):
        return {"used_ports": [8118, 8129], "managed": [], "suggested_free": 8130,
                "range": {"lo": 8129, "hi": 8199}}

    def list_status(self):
        return [{"port": 8129, "local_url": "http://127.0.0.1:8129",
                 "label": "http://1.2.3.4:10000", "alive": True, "created_at": 1.0}]

    async def check_upstream(self, raw):
        if "bad" in raw:
            return {"ok": False, "error": "upstream_unreachable"}
        return {"ok": True, "egress_ip": "1.2.3.4", "latency_ms": 120,
                "label": "http://1.2.3.4:10000"}

    async def raise_proxy(self, raw):
        import proxy_supervisor
        if "bad" in raw:
            raise proxy_supervisor.ProxyError("upstream_unreachable")
        self.raised.append(raw)
        return {"port": 8130, "local_url": "http://127.0.0.1:8130",
                "label": "http://1.2.3.4:10000", "egress_ip": "1.2.3.4", "reused": False}

    def teardown(self, port):
        self.tore_down.append(port)
        return port == 8129


class ProxyEndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.addCleanup(self._reset)
        admin_api._ctx.proxy_sup = _FakeProxySup()

    def _reset(self):
        admin_api._ctx.proxy_sup = None

    async def test_ports_503_when_disabled(self):
        admin_api._ctx.proxy_sup = None
        resp = await admin_api.handle_proxy_ports_get(None)
        self.assertEqual(resp.status, 503)

    async def test_ports_view(self):
        resp = await admin_api.handle_proxy_ports_get(None)
        body = json.loads(resp.body)
        self.assertIn(8129, body["used_ports"])
        self.assertEqual(body["suggested_free"], 8130)

    async def test_check_ok_and_no_credentials_in_response(self):
        resp = await admin_api.handle_proxy_verify_post(
            _JsonReq({"proxy": "user:pass@1.2.3.4:10000"}))
        body = json.loads(resp.body)
        self.assertTrue(body["ok"])
        self.assertEqual(body["egress_ip"], "1.2.3.4")
        self.assertNotIn("pass", resp.body.decode())

    async def test_check_empty_proxy_400(self):
        resp = await admin_api.handle_proxy_verify_post(_JsonReq({"proxy": "  "}))
        self.assertEqual(resp.status, 400)

    async def test_raise_ok(self):
        resp = await admin_api.handle_proxy_raise_post(
            _JsonReq({"proxy": "user:pass@1.2.3.4:10000"}))
        body = json.loads(resp.body)
        self.assertEqual(body["local_url"], "http://127.0.0.1:8130")
        self.assertEqual(admin_api._ctx.proxy_sup.raised, ["user:pass@1.2.3.4:10000"])

    async def test_raise_proxy_error_400(self):
        resp = await admin_api.handle_proxy_raise_post(
            _JsonReq({"proxy": "user:pass@bad:10000"}))
        self.assertEqual(resp.status, 400)
        self.assertEqual(json.loads(resp.body)["error"], "upstream_unreachable")

    async def test_teardown_ok_and_not_found(self):
        ok = await admin_api.handle_proxy_teardown_post(_JsonReq({"port": 8129}))
        self.assertEqual(ok.status, 200)
        missing = await admin_api.handle_proxy_teardown_post(_JsonReq({"port": 9999}))
        self.assertEqual(missing.status, 404)

    async def test_teardown_invalid_port_400(self):
        resp = await admin_api.handle_proxy_teardown_post(_JsonReq({"port": "abc"}))
        self.assertEqual(resp.status, 400)


class VideoAbEndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.addCleanup(self._reset_globals)
        self._orig_log_event = admin_api.metrics.log_event
        self.logged_events = []
        admin_api.metrics.log_event = lambda *args, **kwargs: self.logged_events.append((args, kwargs))

    def _reset_globals(self):
        admin_api._ctx.pool = None
        admin_api._ctx.keepers = None
        admin_api._ctx.video_clients = None
        admin_api._ctx.startup_state = None
        admin_api.metrics.log_event = self._orig_log_event

    async def test_video_ab_requires_explicit_spend_confirmation(self):
        client = _FakeVideoClient()
        admin_api._ctx.video_clients = {"a1": client}

        resp = await admin_api.handle_video_ab_post(_JsonReq({"account": "a1"}))
        body = json.loads(resp.body)

        self.assertEqual(resp.status, 400)
        self.assertIn("confirm_spend", body["error"])
        self.assertEqual(client.calls, [])

    async def test_video_ab_selects_available_account_and_calls_client(self):
        client = _FakeVideoClient()
        admin_api._ctx.pool = _FakePool([
            {"id": "a0", "disabled": True, "video_allowed": True, "cooldown_left": 0},
            {"id": "a1", "disabled": False, "video_allowed": True, "cooldown_left": 0},
        ])
        admin_api._ctx.video_clients = {"a1": client}

        resp = await admin_api.handle_video_ab_post(_JsonReq({
            "confirm_spend": True,
            "prompt": "short safe prompt",
            "model": "veo-lite",
            "aspect": "portrait",
            "order": "browser_first",
            "pause_sec": 0,
        }))
        body = json.loads(resp.body)

        self.assertEqual(resp.status, 200)
        self.assertEqual(body["account"], "a1")
        self.assertEqual(body["model_key"], "veo-lite")
        self.assertEqual(client.calls[0]["prompt"], "short safe prompt")
        self.assertEqual(client.calls[0]["model_key"], "veo-lite")
        self.assertEqual(client.calls[0]["aspect"], "portrait")
        self.assertEqual(client.calls[0]["order"], "browser_first")
        self.assertTrue(any(args and args[0] == "video_ab" for args, _ in self.logged_events))

    async def test_video_ab_unknown_account_is_404(self):
        admin_api._ctx.video_clients = {"a1": _FakeVideoClient()}

        resp = await admin_api.handle_video_ab_post(_JsonReq({
            "confirm_spend": True,
            "account": "missing",
        }))
        body = json.loads(resp.body)

        self.assertEqual(resp.status, 404)
        self.assertIn("missing", body["error"])


class AccountOnboardingEndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.addCleanup(self._reset_globals)
        self._orig_log_event = admin_api.metrics.log_event
        self._orig_onboard = admin_api.account_onboarding.onboard_google_flow_account
        self._orig_start_login = admin_api.account_onboarding.start_google_flow_login
        self._orig_complete_login = admin_api.account_onboarding.complete_google_flow_login
        self._orig_remove_env = admin_api.account_onboarding.remove_flow_account_from_env
        self._orig_hot_add = admin_api._try_hot_add_account
        self.logged_events = []
        admin_api.metrics.log_event = lambda *args, **kwargs: self.logged_events.append((args, kwargs))

    def _reset_globals(self):
        admin_api._ctx.pool = None
        admin_api._ctx.keepers = None
        admin_api._ctx.video_clients = None
        admin_api._ctx.startup_state = None
        admin_api._onboard_sessions.clear()
        admin_api.metrics.log_event = self._orig_log_event
        admin_api.account_onboarding.onboard_google_flow_account = self._orig_onboard
        admin_api.account_onboarding.start_google_flow_login = self._orig_start_login
        admin_api.account_onboarding.complete_google_flow_login = self._orig_complete_login
        admin_api.account_onboarding.remove_flow_account_from_env = self._orig_remove_env
        admin_api._try_hot_add_account = self._orig_hot_add

    async def test_onboard_requires_explicit_login_confirmation(self):
        resp = await admin_api.handle_account_onboard_post(_JsonReq({"id": "sub7"}))
        body = json.loads(resp.body)

        self.assertEqual(resp.status, 400)
        self.assertIn("confirm_login", body["error"])

    async def test_onboard_success_sanitizes_response_and_hot_adds(self):
        calls = []

        async def fake_onboard(**kwargs):
            calls.append(kwargs)
            return {
                "ok": True,
                "account_id": kwargs["account_id"],
                "profile_dir": "./google_profile_sub7",
                "proxy": "http://10.0.0.1:8118",
                "status": "active",
                "env_updated": True,
                "restart_required": True,
            }

        admin_api.account_onboarding.onboard_google_flow_account = fake_onboard
        admin_api._try_hot_add_account = lambda account_id, profile_dir, proxy_url: {
            "runtime_added": True,
            "runtime_reason": "warming",
        }

        proxy = "http://user:" + "pass@10.0.0.1:8118"
        resp = await admin_api.handle_account_onboard_post(_JsonReq({
            "confirm_login": True,
            "id": "sub7",
            "email": "account@example.com",
            "password": "secret-password",
            "totp_secret": "SECRETSECRET",
            "proxy": proxy,
        }))
        body = json.loads(resp.body)

        self.assertEqual(resp.status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["runtime_added"])
        encoded = json.dumps(body, ensure_ascii=False)
        self.assertNotIn("secret-password", encoded)
        self.assertNotIn("SECRETSECRET", encoded)
        self.assertNotIn("user:pass", encoded)
        self.assertEqual(calls[0]["password"], "secret-password")
        self.assertTrue(any(args and args[0] == "account_onboard" for args, _ in self.logged_events))

    async def test_staged_onboard_accepts_one_time_2fa_then_completes(self):
        calls = []
        session_holder = {}

        async def fake_start_login(**kwargs):
            calls.append(kwargs)
            session = _FakeOnboardSession(
                account_id=kwargs["account_id"],
                profile_dir=kwargs["profile_dir"],
                proxy_url=kwargs["proxy_url"],
            )
            session_holder["session"] = session
            return session, {"ok": False, "status": "needs_2fa", "reason": "two_fa_code_required"}

        def fake_complete(session):
            self.assertIs(session, session_holder["session"])
            self.assertEqual(session.status, "active")
            return {
                "ok": True,
                "account_id": session.account_id,
                "profile_dir": session.profile_dir,
                "proxy": "http://10.0.0.1:8126",
                "status": "active",
                "env_updated": True,
                "restart_required": True,
            }

        admin_api.account_onboarding.start_google_flow_login = fake_start_login
        admin_api.account_onboarding.complete_google_flow_login = fake_complete
        admin_api._try_hot_add_account = lambda account_id, profile_dir, proxy_url: {
            "runtime_added": True,
            "runtime_reason": "warming",
        }

        proxy = "http://user:" + "pass@10.0.0.1:8126"
        resp = await admin_api.handle_account_onboard_start_post(_JsonReq({
            "confirm_login": True,
            "id": "sub7",
            "email": "account@example.com",
            "password": "secret-password",
            "totp_secret": "SECRETSECRET",
            "proxy": proxy,
        }))
        body = json.loads(resp.body)

        self.assertEqual(resp.status, 200)
        self.assertTrue(body["needs_2fa"])
        self.assertIn("session_id", body)
        encoded = json.dumps(body, ensure_ascii=False)
        self.assertNotIn("secret-password", encoded)
        self.assertNotIn("SECRETSECRET", encoded)
        self.assertNotIn("user:pass", encoded)
        self.assertEqual(calls[0]["password"], "secret-password")
        self.assertEqual(calls[0]["totp_secret"], "SECRETSECRET")

        resp = await admin_api.handle_account_onboard_2fa_post(_JsonReq({
            "session_id": body["session_id"],
            "code": "123456",
        }))
        twofa = json.loads(resp.body)

        self.assertEqual(resp.status, 200)
        self.assertTrue(twofa["ready_to_add"])
        self.assertEqual(session_holder["session"].codes, ["123456"])

        resp = await admin_api.handle_account_onboard_complete_post(_JsonReq({
            "confirm_add": True,
            "session_id": body["session_id"],
        }))
        done = json.loads(resp.body)

        self.assertEqual(resp.status, 200)
        self.assertTrue(done["ok"])
        self.assertTrue(done["runtime_added"])
        self.assertTrue(session_holder["session"].closed)
        self.assertNotIn(body["session_id"], admin_api._onboard_sessions)

    async def test_staged_onboard_complete_requires_confirm(self):
        session = _FakeOnboardSession()
        session.status = "active"
        session_id = admin_api._store_onboard_session(session)

        resp = await admin_api.handle_account_onboard_complete_post(_JsonReq({
            "session_id": session_id,
        }))
        body = json.loads(resp.body)

        self.assertEqual(resp.status, 400)
        self.assertIn("confirm_add", body["error"])
        self.assertFalse(session.closed)

    async def test_relogin_start_closes_runtime_and_uses_existing_profile(self):
        calls = []
        keeper = _FakeKeeper({"credits": 1})
        admin_api._ctx.pool = _FakePool([
            {"id": "sub7", "profile_dir": "./google_profile_sub7", "disabled": False,
             "cooldown_left": 0, "fails": 0, "proxy": "http://127.0.0.1:8126"},
        ])
        admin_api._ctx.keepers = {"sub7": keeper}
        admin_api._ctx.video_clients = {"sub7": object()}

        async def fake_start_login(**kwargs):
            calls.append(kwargs)
            session = _FakeOnboardSession(
                account_id=kwargs["account_id"],
                profile_dir=kwargs["profile_dir"],
                proxy_url=kwargs["proxy_url"],
            )
            session.status = "active"
            return session, {"ok": True, "status": "active", "reason": "project_opened"}

        admin_api.account_onboarding.start_google_flow_login = fake_start_login

        resp = await admin_api.handle_account_onboard_start_post(_JsonReq({
            "confirm_login": True,
            "mode": "relogin",
            "id": "sub7",
            "email": "account@example.com",
            "password": "secret-password",
        }))
        body = json.loads(resp.body)

        self.assertEqual(resp.status, 200)
        self.assertTrue(body["ready_to_add"])
        self.assertTrue(keeper.closed)
        self.assertNotIn("sub7", admin_api._ctx.keepers)
        self.assertTrue(calls[0]["allow_existing_profile"])
        self.assertTrue(calls[0]["skip_account_exists"])
        self.assertEqual(calls[0]["profile_dir"], "./google_profile_sub7")

    async def test_delete_account_requires_confirm_and_removes_runtime(self):
        calls = []
        admin_api._ctx.pool = _FakePool([
            {"id": "sub7", "profile_dir": "./google_profile_sub7", "disabled": False,
             "cooldown_left": 0, "fails": 0},
            {"id": "sub8", "profile_dir": "./google_profile_sub8", "disabled": False,
             "cooldown_left": 0, "fails": 0},
        ])

        def fake_remove_env(account_id):
            calls.append(account_id)
            return {"accounts_count": 1}

        admin_api.account_onboarding.remove_flow_account_from_env = fake_remove_env

        resp = await admin_api.handle_account_delete(_JsonReq({}, {"id": "sub7"}))
        self.assertEqual(resp.status, 400)
        self.assertEqual(calls, [])

        resp = await admin_api.handle_account_delete(_JsonReq({"confirm_delete": True}, {"id": "sub7"}))
        body = json.loads(resp.body)

        self.assertEqual(resp.status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(calls, ["sub7"])
        self.assertEqual([a["id"] for a in admin_api._ctx.pool.status()], ["sub8"])

    async def test_delete_is_idempotent_when_already_absent(self):
        # sub7 is gone from the pool, .env, and startup — a repeated delete
        # click must return ok (already_removed), not a scary 404.
        admin_api._ctx.pool = _FakePool([
            {"id": "sub8", "profile_dir": "./google_profile_sub8", "disabled": False,
             "cooldown_left": 0, "fails": 0},
        ])
        admin_api._ctx.startup_state = None

        def fake_remove_env(account_id):
            raise admin_api.account_onboarding.AccountOnboardingError("account_not_found")

        admin_api.account_onboarding.remove_flow_account_from_env = fake_remove_env

        resp = await admin_api.handle_account_delete(_JsonReq({"confirm_delete": True}, {"id": "sub7"}))
        body = json.loads(resp.body)
        self.assertEqual(resp.status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"], "already_removed")
        # sub8 untouched.
        self.assertEqual([a["id"] for a in admin_api._ctx.pool.status()], ["sub8"])

    async def test_delete_purges_startup_ghost(self):
        # A deleted account must not linger as a startup ghost card.
        admin_api._ctx.pool = _FakePool([
            {"id": "sub7", "profile_dir": "./google_profile_sub7", "disabled": False,
             "cooldown_left": 0, "fails": 0},
            {"id": "sub8", "profile_dir": "./google_profile_sub8", "disabled": False,
             "cooldown_left": 0, "fails": 0},
        ])
        admin_api._ctx.startup_state = {"accounts": {"sub7": {"status": "stopped", "ready": False}}}
        admin_api.account_onboarding.remove_flow_account_from_env = lambda account_id: {"accounts_count": 1}

        self.assertIsNotNone(admin_api._startup_for_account("sub7"))
        resp = await admin_api.handle_account_delete(_JsonReq({"confirm_delete": True}, {"id": "sub7"}))
        body = json.loads(resp.body)
        self.assertEqual(resp.status, 200)
        self.assertTrue(body["ok"])
        self.assertIsNone(admin_api._startup_for_account("sub7"))
        self.assertEqual([a["id"] for a in admin_api._ctx.pool.status()], ["sub8"])

    async def test_onboard_progress_endpoint_returns_snapshot(self):
        admin_api.account_onboarding._PROGRESS.pop("sub7", None)
        admin_api.account_onboarding._stage("sub7", "login_start")
        admin_api.account_onboarding._stage("sub7", "flow_opened")
        req = SimpleNamespace(query={"id": "sub7"}, headers={}, remote="test")
        resp = await admin_api.handle_account_onboard_progress_get(req)
        body = json.loads(resp.body)
        self.assertEqual(resp.status, 200)
        self.assertEqual(body["step"], 6)
        self.assertEqual(body["account_id"], "sub7")
        # No id -> empty object, not an error.
        resp2 = await admin_api.handle_account_onboard_progress_get(
            SimpleNamespace(query={}, headers={}, remote="test")
        )
        self.assertEqual(json.loads(resp2.body), {})
        admin_api.account_onboarding._PROGRESS.pop("sub7", None)

    async def test_recheck_finalizes_after_manual_challenge(self):
        session = _FakeOnboardSession()
        session.status = "needs_challenge"
        session_id = admin_api._store_onboard_session(session, mode="add")

        resp = await admin_api.handle_account_onboard_recheck_post(
            _JsonReq({"session_id": session_id})
        )
        body = json.loads(resp.body)
        self.assertEqual(resp.status, 200)
        self.assertTrue(body["ready_to_add"])
        self.assertEqual(body["status"], "active")

    async def test_image_test_requires_confirm_and_then_calls_client(self):
        client = _FakeVideoClient()
        admin_api._ctx.video_clients = {"sub7": client}

        resp = await admin_api.handle_account_test_image_post(_JsonReq({}, {"id": "sub7"}))
        self.assertEqual(resp.status, 400)
        self.assertEqual(client.image_calls, [])

        resp = await admin_api.handle_account_test_image_post(_JsonReq({"confirm_spend": True}, {"id": "sub7"}))
        body = json.loads(resp.body)

        self.assertEqual(resp.status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(client.image_calls[0]["num_images"], 1)
        self.assertFalse(client.image_calls[0]["allow_browser_fallback"])

    async def test_video_test_requires_confirm_and_uses_single_transport(self):
        client = _FakeVideoClient(result={
            "arms": [{"transport": "direct_http", "status": 200, "ok": True}]
        })
        admin_api._ctx.video_clients = {"sub7": client}

        resp = await admin_api.handle_account_test_video_post(_JsonReq({}, {"id": "sub7"}))
        self.assertEqual(resp.status, 400)
        self.assertEqual(client.calls, [])

        resp = await admin_api.handle_account_test_video_post(_JsonReq({"confirm_spend": True}, {"id": "sub7"}))
        body = json.loads(resp.body)

        self.assertEqual(resp.status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(client.calls[0]["transports"], ["direct_http"])
        self.assertEqual(client.calls[0]["pause_sec"], 0)


class AdminApiValidationTests(unittest.TestCase):
    def test_ping_includes_startup_snapshot(self) -> None:
        admin_api._ctx.startup_state = {"phase": "warming", "polling": False}
        self.addCleanup(setattr, admin_api, "_ctx.startup_state", None)

        resp = asyncio.run(admin_api.handle_ping(None))
        body = json.loads(resp.body)

        self.assertTrue(body["ok"])
        self.assertEqual(body["startup"]["phase"], "warming")

    def test_support_handler_accepts_done4you_filter(self) -> None:
        calls = []
        original = admin_api.metrics.list_support_tickets

        def fake_list(status="open", limit=100):
            calls.append((status, limit))
            return [{"id": 9, "kind": "done4you"}]

        admin_api.metrics.list_support_tickets = fake_list
        self.addCleanup(setattr, admin_api.metrics, "list_support_tickets", original)

        req = SimpleNamespace(rel_url=SimpleNamespace(query={"status": "done4you", "limit": "7"}))
        resp = asyncio.run(admin_api.handle_support_get(req))
        body = json.loads(resp.body)

        self.assertEqual(calls, [("done4you", 7)])
        self.assertEqual(body["status"], "done4you")
        self.assertEqual(body["tickets"][0]["kind"], "done4you")

    def test_support_status_post_accepts_in_work_and_done(self) -> None:
        calls = []
        original_get = admin_api.metrics.get_support_ticket_detail
        original_set = admin_api.metrics.set_support_ticket_status
        original_log = admin_api.metrics.log_event

        admin_api.metrics.get_support_ticket_detail = lambda ticket_id: {"ticket": {"id": ticket_id}}

        def fake_set(ticket_id, status):
            calls.append((ticket_id, status))
            return True

        admin_api.metrics.set_support_ticket_status = fake_set
        admin_api.metrics.log_event = lambda *args, **kwargs: None
        self.addCleanup(setattr, admin_api.metrics, "get_support_ticket_detail", original_get)
        self.addCleanup(setattr, admin_api.metrics, "set_support_ticket_status", original_set)
        self.addCleanup(setattr, admin_api.metrics, "log_event", original_log)

        class Req:
            match_info = {"id": "12"}
            headers = {}
            remote = "test"

            async def json(self):
                return {"status": "in_work"}

        resp = asyncio.run(admin_api.handle_support_status_post(Req()))
        body = json.loads(resp.body)

        self.assertEqual(calls, [(12, "in_work")])
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"], "in_work")

    def test_sellers_handler_caps_limit_and_returns_metrics_report(self) -> None:
        calls = []
        original = admin_api.metrics.report_sellers

        def fake_report(limit):
            calls.append(limit)
            return {"total_sellers": 1, "sellers": [{"user_id": 1}]}

        admin_api.metrics.report_sellers = fake_report
        self.addCleanup(setattr, admin_api.metrics, "report_sellers", original)

        req = SimpleNamespace(rel_url=SimpleNamespace(query={"limit": "5000"}))
        resp = asyncio.run(admin_api.handle_sellers_get(req))
        body = json.loads(resp.body)

        self.assertEqual(calls, [1000])
        self.assertEqual(body["total_sellers"], 1)

    def test_sellers_handler_uses_default_limit_for_bad_query(self) -> None:
        calls = []
        original = admin_api.metrics.report_sellers

        def fake_report(limit):
            calls.append(limit)
            return {"total_sellers": 0, "sellers": []}

        admin_api.metrics.report_sellers = fake_report
        self.addCleanup(setattr, admin_api.metrics, "report_sellers", original)

        req = SimpleNamespace(rel_url=SimpleNamespace(query={"limit": "oops"}))
        resp = asyncio.run(admin_api.handle_sellers_get(req))
        body = json.loads(resp.body)

        self.assertEqual(calls, [100])
        self.assertEqual(body["sellers"], [])

    def test_telemetr_channel_requires_server_token(self) -> None:
        for var in ("TELEMETR_API_TOKEN", "TGSTAT_API_TOKEN"):
            old = admin_api.os.environ.pop(var, None)
            self.addCleanup(
                lambda v=var, o=old: admin_api.os.environ.__setitem__(v, o) if o is not None else None
            )
        req = SimpleNamespace(rel_url=SimpleNamespace(query={"url": "@channel"}))

        resp = asyncio.run(admin_api.handle_telemetr_channel_get(req))
        body = json.loads(resp.body)

        self.assertEqual(resp.status, 503)
        self.assertEqual(body["error"], "telemetr_not_configured")

    def test_telemetr_channel_lookup_uses_cache_and_hides_token(self) -> None:
        admin_api.os.environ["TELEMETR_API_TOKEN"] = "test-token"
        self.addCleanup(admin_api.os.environ.pop, "TELEMETR_API_TOKEN", None)
        admin_api._telemetr_cache.clear()
        self.addCleanup(admin_api._telemetr_cache.clear)
        calls = []

        async def fake_fetch(channel_id, token):
            calls.append((channel_id, token))
            return {
                "status": "ok",
                "channel_id": channel_id,
                "title": "My Channel",
                "adv_post_reach_24h": 12345,
            }

        old_fetch = admin_api._fetch_telemetr_channel
        admin_api._fetch_telemetr_channel = fake_fetch
        self.addCleanup(setattr, admin_api, "_fetch_telemetr_channel", old_fetch)
        req = SimpleNamespace(rel_url=SimpleNamespace(query={"url": "https://t.me/MyChannel"}))

        first = asyncio.run(admin_api.handle_telemetr_channel_get(req))
        second = asyncio.run(admin_api.handle_telemetr_channel_get(req))
        first_body = json.loads(first.body)
        second_body = json.loads(second.body)

        self.assertEqual(calls, [("mychannel", "test-token")])
        self.assertFalse(first_body["cached"])
        self.assertTrue(second_body["cached"])
        self.assertEqual(second_body["adv_post_reach_24h"], 12345)
        self.assertNotIn("test-token", first.text)
        self.assertNotIn("test-token", second.text)

    def test_video_ab_route_is_post_only(self) -> None:
        from pathlib import Path
        source = Path(admin_api.__file__).read_text(encoding="utf-8")
        self.assertIn('r.add_post("/api/admin/video-ab"', source)
        self.assertNotIn('r.add_get ("/api/admin/video-ab"', source)

    def test_price_validation_rejects_zero_negative_and_unknown_paid_keys(self) -> None:
        prices, errors = admin_api._validate_prices({
            "image_nano": 0,
            "edit_photo": -1,
            "unknown": 10,
            "veo_lite": 80,
            "ingredients_extra": 0,
        })

        self.assertEqual(prices, {"veo_lite": 80, "ingredients_extra": 0})
        by_key = {e["key"]: e["error"] for e in errors}
        self.assertEqual(by_key["image_nano"], "zero_paid_price")
        self.assertEqual(by_key["edit_photo"], "negative_price")
        self.assertEqual(by_key["unknown"], "unknown_price_key")

    def test_copy_validation_blocks_lost_placeholders_and_non_string_defaults(self) -> None:
        clean, errors, warnings = admin_api._validate_copy_payload(
            {
                "low_balance": "Need {needed}, have {have}",
                "vid_status_phrases": ["one", "two"],
            },
            {
                "low_balance": "Need credits",
                "vid_status_phrases": "oops",
                "custom": "Цена 10 кр",
            },
            kind="message",
        )

        self.assertEqual(clean, {"custom": "Цена 10 кр"})
        by_key = {e["key"]: e["error"] for e in errors}
        self.assertEqual(by_key["low_balance"], "missing_placeholders")
        self.assertEqual(by_key["vid_status_phrases"], "non_string_default")
        self.assertEqual(warnings, [{"key": "custom", "warning": "hardcoded_price"}])

    def test_overrides_only_keeps_diffs_and_custom_keys(self) -> None:
        defaults = {"a": "code-A", "b": "code-B", "c": "code-C"}
        clean = {
            "a": "code-A",      # unchanged → dropped (falls back to code)
            "b": "edited-B",    # changed → kept
            "custom": "extra",  # not in defaults → kept
        }
        self.assertEqual(
            admin_api._overrides_only(defaults, clean),
            {"b": "edited-B", "custom": "extra"},
        )

    def test_overrides_only_empty_when_all_match_code(self) -> None:
        defaults = {"a": "x", "b": "y"}
        self.assertEqual(admin_api._overrides_only(defaults, {"a": "x", "b": "y"}), {})


if __name__ == "__main__":
    unittest.main()
