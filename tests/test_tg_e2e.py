from __future__ import annotations

import builtins
import json
import tempfile
import unittest
from pathlib import Path

from tg_e2e.config import ConfigError, build_config, validate_no_dangerous_flags
from tg_e2e.env_file import load_env_file
from tg_e2e.runner import TelegramBatch, run


class TelegramE2EConfigTests(unittest.TestCase):
    def test_dry_run_rejects_external_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = build_config(mode="dry-run", output_dir="runs", project_root=root)
            self.assertEqual(config.mode, "dry-run")
            self.assertIsNone(config.session_file)

            with self.assertRaises(ConfigError):
                build_config(
                    mode="dry-run",
                    output_dir="runs",
                    project_root=root,
                    approve_external_action=True,
                )
            with self.assertRaises(ConfigError):
                build_config(
                    mode="dry-run",
                    output_dir="runs",
                    project_root=root,
                    session_file=".sessions/test",
                )

    def test_external_modes_require_approval_credentials_and_safe_session_path(self) -> None:
        env = {
            "BOT_USERNAME": "@flow_test_bot",
            "TG_API_ID": "12345",
            "TG_API_HASH": "hash-value",
            "TG_PHONE": "+10000000000",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ConfigError):
                build_config(
                    mode="telegram-smoke",
                    output_dir="runs",
                    project_root=root,
                    env=env,
                )

            config = build_config(
                mode="telegram-smoke",
                output_dir="runs",
                project_root=root,
                approve_external_action=True,
                env=env,
            )
            self.assertEqual(config.session_file, root / ".sessions" / "tg_e2e")
            self.assertEqual(config.tg_api_id, 12345)

            login_config = build_config(
                mode="telegram-login",
                output_dir="runs",
                project_root=root,
                approve_external_action=True,
                env={key: value for key, value in env.items() if key != "BOT_USERNAME"},
            )
            self.assertIsNone(login_config.bot_username)

            for unsafe in ("../session", ".env", "api_config.json", "google_profile/session"):
                with self.subTest(unsafe=unsafe):
                    with self.assertRaises(ConfigError):
                        build_config(
                            mode="telegram-smoke",
                            output_dir="runs",
                            project_root=root,
                            approve_external_action=True,
                            session_file=unsafe,
                            env=env,
                        )

    def test_generation_and_ramp_limits_are_conservative(self) -> None:
        env = {
            "BOT_USERNAME": "@flow_test_bot",
            "TG_API_ID": "12345",
            "TG_API_HASH": "hash-value",
            "TG_PHONE": "+10000000000",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ConfigError):
                build_config(
                    mode="telegram-generation",
                    output_dir="runs",
                    project_root=root,
                    approve_external_action=True,
                    prompt="ok",
                    env=env,
                )
            with self.assertRaises(ConfigError):
                build_config(
                    mode="telegram-ramp",
                    output_dir="runs",
                    project_root=root,
                    approve_external_action=True,
                    prompt="safe prompt",
                    delay_sec=16,
                    env=env,
                )
            self.assertEqual(
                build_config(
                    mode="telegram-ramp",
                    output_dir="runs",
                    project_root=root,
                    approve_external_action=True,
                    prompt="safe prompt",
                    max_steps=5,
                    delay_sec=17,
                    env=env,
                ).max_steps,
                5,
            )

    def test_dangerous_flags_are_rejected(self) -> None:
        for flag in ("--captcha", "--proxy", "--google-profile", "--browser", "--ignore-warning"):
            with self.subTest(flag=flag):
                with self.assertRaises(ConfigError):
                    validate_no_dangerous_flags([flag])

    def test_env_file_supplies_telegram_credentials_without_overriding_process_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BOT_USERNAME=@from_file_bot",
                        "TG_API_ID=12345",
                        "TG_API_" + "HASH=file-hash",
                        "TG_" + "PHONE=+10000000000",
                    ]
                ),
                encoding="utf-8",
            )
            env = load_env_file(env_file, base_env={"BOT_USERNAME": "@from_process_bot"})
            config = build_config(
                mode="telegram-smoke",
                output_dir="runs",
                project_root=root,
                approve_external_action=True,
                env=env,
            )

        self.assertEqual(config.bot_username, "@from_process_bot")
        self.assertEqual(config.tg_api_id, 12345)
        self.assertEqual(config.tg_api_hash, "file-hash")
        self.assertEqual(config.tg_phone, "+10000000000")

    def test_telegram_alias_env_names_are_supported(self) -> None:
        env = {
            "TELEGRAM_API_ID": "12345",
            "TELEGRAM_API_HASH": "alias-hash",
            "TELEGRAM_PHONE": "+10000000000",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = build_config(
                mode="telegram-login",
                output_dir="runs",
                project_root=root,
                approve_external_action=True,
                env=env,
            )

        self.assertEqual(config.tg_api_id, 12345)
        self.assertEqual(config.tg_api_hash, "alias-hash")
        self.assertEqual(config.tg_phone, "+10000000000")

    def test_mtproxy_url_can_come_from_env_file_or_tg_proxy_fallback(self) -> None:
        secret = "dd" + "0" * 32
        proxy_url = _mtproxy_url(secret)
        env = {
            "BOT_USERNAME": "@flow_test_bot",
            "TG_API_ID": "12345",
            "TG_API_HASH": "hash-value",
            "TG_PHONE": "+10000000000",
            "TG_E2E_PROXY_URL": proxy_url,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = build_config(
                mode="telegram-smoke",
                output_dir="runs",
                project_root=root,
                approve_external_action=True,
                env=env,
            )
            fallback = build_config(
                mode="telegram-smoke",
                output_dir="runs",
                project_root=root,
                approve_external_action=True,
                env={**env, "TG_E2E_PROXY_URL": "", "TG_PROXY_URL": proxy_url},
            )

        self.assertIsNotNone(config.mtproxy)
        self.assertEqual(config.mtproxy.host, "127.0.0.1")
        self.assertEqual(config.mtproxy.port, 1080)
        self.assertEqual(config.mtproxy.secret, secret)
        self.assertEqual(fallback.mtproxy, config.mtproxy)

    def test_invalid_mtproxy_urls_are_rejected(self) -> None:
        env = {
            "BOT_USERNAME": "@flow_test_bot",
            "TG_API_ID": "12345",
            "TG_API_HASH": "hash-value",
            "TG_PHONE": "+10000000000",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, proxy_url in enumerate((
                "socks" + "5://127.0.0.1:1080",
                _mtproxy_url("d" * 34, port=99999),
                _mtproxy_url("not-hex"),
            )):
                with self.subTest(index=index):
                    with self.assertRaises(ConfigError):
                        build_config(
                            mode="telegram-smoke",
                            output_dir="runs",
                            project_root=root,
                            approve_external_action=True,
                            tg_proxy_url=proxy_url,
                            env=env,
                        )


class TelegramE2ERunnerTests(unittest.TestCase):
    def test_dry_run_writes_planned_events_without_importing_telethon(self) -> None:
        original_import = builtins.__import__

        def tracking_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name.split(".")[0] == "telethon":
                raise AssertionError(f"blocked import: {name}")
            return original_import(name, globals, locals, fromlist, level)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = build_config(mode="dry-run", output_dir="runs", project_root=root)
            builtins.__import__ = tracking_import
            try:
                result = run(config, run_id="dryrun-test")
            finally:
                builtins.__import__ = original_import

            events = [
                json.loads(line)
                for line in Path(result["events_path"]).read_text(encoding="utf-8").splitlines()
            ]
            summary = json.loads(Path(result["summary_path"]).read_text(encoding="utf-8"))

        self.assertEqual([event["stage_name"] for event in events], ["telegram-start", "telegram-status"])
        self.assertEqual(summary["status_counts"], {"planned": 2})

    def test_mocked_login_mode_authorizes_without_messaging_bot(self) -> None:
        class FakeClient:
            def __init__(self):
                self.sent = False

            async def ensure_authorized(self):
                return "authorized"

            async def send_and_collect(self, *, bot_username, text, timeout_sec, expected_min_photos=0):
                self.sent = True
                return TelegramBatch(text="unexpected")

            async def close(self):
                return None

        fake = FakeClient()
        env = {
            "TG_API_ID": "12345",
            "TG_API_HASH": "hash-value",
            "TG_PHONE": "+10000000000",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = build_config(
                mode="telegram-login",
                output_dir="runs",
                project_root=root,
                approve_external_action=True,
                env=env,
            )
            result = run(config, run_id="login-test", client_factory=lambda _: fake)
            event = json.loads(Path(result["events_path"]).read_text(encoding="utf-8").strip())

        self.assertFalse(fake.sent)
        self.assertEqual(event["stage_name"], "telegram-login")
        self.assertEqual(event["status"], "success")

    def test_smoke_text_can_mention_2captcha_without_stop_signal(self) -> None:
        class FakeClient:
            async def ensure_authorized(self):
                return "authorized"

            async def send_and_collect(self, *, bot_username, text, timeout_sec, expected_min_photos=0):
                return TelegramBatch(text="commands: /balance shows 2captcha balance", photo_count=0)

            async def close(self):
                return None

        env = {
            "BOT_USERNAME": "@flow_test_bot",
            "TG_API_ID": "12345",
            "TG_API_HASH": "hash-value",
            "TG_PHONE": "+10000000000",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = build_config(
                mode="telegram-smoke",
                output_dir="runs",
                project_root=root,
                approve_external_action=True,
                env=env,
            )
            result = run(config, run_id="smoke-captcha-word-test", client_factory=lambda _: FakeClient())
            events = [
                json.loads(line)
                for line in Path(result["events_path"]).read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(events[0]["status"], "success")
        self.assertIsNone(events[0]["stop_signal"])

    def test_mocked_generation_success_writes_photo_count_and_redacts_prompt(self) -> None:
        class FakeClient:
            async def send_and_collect(self, *, bot_username, text, timeout_sec, expected_min_photos=0):
                self.text = text
                self.expected_min_photos = expected_min_photos
                return TelegramBatch(text="done", photo_count=1)

            async def close(self):
                return None

        fake = FakeClient()

        def factory(config):
            return fake

        env = {
            "BOT_USERNAME": "@flow_test_bot",
            "TG_API_ID": "12345",
            "TG_API_HASH": "hash-value",
            "TG_PHONE": "+10000000000",
        }
        prompt = "private prompt text"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = build_config(
                mode="telegram-generation",
                output_dir="runs",
                project_root=root,
                approve_external_action=True,
                prompt=prompt,
                env=env,
            )
            result = run(config, run_id="generation-test", client_factory=factory)
            combined = Path(result["events_path"]).read_text(encoding="utf-8")
            event = json.loads(combined.strip())

        self.assertEqual(event["status"], "success")
        self.assertEqual(event["output_count"], 1)
        self.assertEqual(fake.expected_min_photos, 1)
        self.assertNotIn(prompt, combined)
        self.assertNotIn("hash-value", combined)
        self.assertNotIn("+10000000000", combined)

    def test_proxy_url_and_secret_are_redacted_from_outputs(self) -> None:
        class FakeClient:
            async def send_and_collect(self, *, bot_username, text, timeout_sec, expected_min_photos=0):
                raise RuntimeError(f"failed through {proxy_url}")

            async def close(self):
                return None

        secret = "dd" + "1" * 32
        proxy_url = _mtproxy_url(secret)
        env = {
            "BOT_USERNAME": "@flow_test_bot",
            "TG_API_ID": "12345",
            "TG_API_HASH": "hash-value",
            "TG_PHONE": "+10000000000",
            "TG_E2E_PROXY_URL": proxy_url,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = build_config(
                mode="telegram-generation",
                output_dir="runs",
                project_root=root,
                approve_external_action=True,
                prompt="safe prompt",
                env=env,
            )
            result = run(config, run_id="proxy-redaction-test", client_factory=lambda _: FakeClient())
            combined = Path(result["events_path"]).read_text(encoding="utf-8")

        self.assertNotIn(proxy_url, combined)
        self.assertNotIn(secret, combined)

    def test_mocked_ramp_stops_on_bot_cooldown(self) -> None:
        class FakeClient:
            def __init__(self):
                self.calls = 0

            async def send_and_collect(self, *, bot_username, text, timeout_sec, expected_min_photos=0):
                self.calls += 1
                if self.calls == 1:
                    return TelegramBatch(text="done", photo_count=1)
                return TelegramBatch(text="please wait 14 sec", photo_count=0)

            async def close(self):
                return None

        fake = FakeClient()

        env = {
            "BOT_USERNAME": "@flow_test_bot",
            "TG_API_ID": "12345",
            "TG_API_HASH": "hash-value",
            "TG_PHONE": "+10000000000",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = build_config(
                mode="telegram-ramp",
                output_dir="runs",
                project_root=root,
                approve_external_action=True,
                prompt="safe prompt",
                max_steps=2,
                delay_sec=17,
                env=env,
            )
            async def no_sleep(seconds):
                return None

            result = run(
                config,
                run_id="ramp-test",
                client_factory=lambda _: fake,
                sleep_func=no_sleep,
            )
            events = [
                json.loads(line)
                for line in Path(result["events_path"]).read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["status"], "success")
        self.assertEqual(events[1]["status"], "stopped")
        self.assertEqual(events[1]["stop_signal"], "bot_cooldown")


def _mtproxy_url(secret: str, *, port: int = 1080) -> str:
    base = "tg://proxy?server=127.0.0.1"
    return f"{base}&port={port}&" + f"secret={secret}"
