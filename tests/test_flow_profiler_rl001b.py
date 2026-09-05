from __future__ import annotations

import builtins
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

from flow_profiler.config import ConfigError, build_config, validate_no_dangerous_flags
from flow_profiler.runner import run_single
from flow_profiler.safety import SafetyClassifier
from flow_profiler.writers import sanitize_event


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _single_config(root: Path, user_dir: Path):
    return build_config(
        mode="single",
        prompt="simple safe landscape test",
        prompt_id="smoke-001",
        output_dir="runs",
        project_root=root,
        user_data_dir=str(user_dir),
        approve_external_action=True,
    )


class SingleConfigTests(unittest.TestCase):
    def test_single_mode_requires_approval_prompt_and_existing_single_user_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_dir = root / "profile"
            user_dir.mkdir()
            prompt_file = root / "prompts.txt"
            prompt_file.write_text("x\n", encoding="utf-8")
            regular_file = root / "not-a-dir"
            regular_file.write_text("x", encoding="utf-8")

            config = _single_config(root, user_dir)
            self.assertEqual(config.mode, "single")
            self.assertEqual(config.max_generations, 1)
            self.assertEqual(config.requested_outputs, 1)
            self.assertEqual(config.delay_sec, 0)
            self.assertTrue(config.stop_on_first_warning)
            self.assertEqual(config.timeout_sec, 120)
            self.assertEqual(config.user_data_dir, user_dir.resolve())

            bad_cases = [
                {"approve_external_action": False},
                {"prompt": ""},
                {"prompt_file": prompt_file, "prompt": None},
                {"user_data_dir": None},
                {"user_data_dir": [user_dir, user_dir]},
                {"user_data_dir": f"{user_dir}{os.pathsep}{user_dir}"},
                {"user_data_dir": "../profile"},
                {"user_data_dir": regular_file},
                {"user_data_dir": root / "missing"},
                {"requested_outputs": 2},
                {"max_generations": 2},
                {"delay_sec": 1},
                {"timeout_sec": 29},
                {"timeout_sec": 301},
            ]
            for kwargs in bad_cases:
                with self.subTest(kwargs=kwargs):
                    params = {
                        "mode": "single",
                        "prompt": "safe prompt",
                        "output_dir": "runs",
                        "project_root": root,
                        "user_data_dir": str(user_dir),
                        "approve_external_action": True,
                    }
                    params.update(kwargs)
                    with self.assertRaises(ConfigError):
                        build_config(**params)

    def test_dry_run_rejects_single_only_external_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_dir = root / "profile"
            user_dir.mkdir()
            with self.assertRaises(ConfigError):
                build_config(
                    mode="dry-run",
                    prompt="safe prompt",
                    output_dir="runs",
                    project_root=root,
                    user_data_dir=str(user_dir),
                )
            with self.assertRaises(ConfigError):
                build_config(
                    mode="dry-run",
                    prompt="safe prompt",
                    output_dir="runs",
                    project_root=root,
                    approve_external_action=True,
                )

    def test_forbidden_flags_and_modes_remain_rejected(self) -> None:
        for mode in ("batch", "ramp"):
            with self.subTest(mode=mode):
                with self.assertRaises(ConfigError):
                    build_config(mode=mode, prompt="x", output_dir="runs")
        for flag in ("--proxy", "--captcha", "--telegram", "--retry", "--batch", "--ramp"):
            with self.subTest(flag=flag):
                with self.assertRaises(ConfigError):
                    validate_no_dangerous_flags([flag])


class SingleRunnerTests(unittest.TestCase):
    def test_mocked_single_runner_writes_one_event_and_locks_before_adapter_call(self) -> None:
        async def fake_browser_executor(config, run_id):
            lock_path = config.output_dir / ".flow_profiler.lock"
            self.assertTrue(lock_path.exists())
            self.assertEqual(json.loads(lock_path.read_text(encoding="utf-8"))["status"], "active")
            return {
                "schema_version": "test",
                "run_id": run_id,
                "mode": "single",
                "stage_name": "browser-smoke",
                "sequence_index": 1,
                "planned_delay_sec": 0.0,
                "started_at": "2026-05-18T00:00:00Z",
                "finished_at": "2026-05-18T00:00:01Z",
                "duration_ms": 1000,
                "prompt_id": "smoke-001",
                "prompt_hash": "hash",
                "prompt_length": 26,
                "aspect_ratio": "landscape",
                "requested_outputs": 1,
                "status": "success",
                "http_statuses": [],
                "output_count": 1,
                "stop_signal": None,
                "warning_signals": [],
                "error_type": None,
                "error_message_redacted": None,
                "page_state": "media-detected",
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_dir = root / "profile"
            user_dir.mkdir()
            config = _single_config(root, user_dir)
            result = run_single(config, run_id="single-test", browser_executor=fake_browser_executor)

            events_path = Path(result["events_path"])
            summary_path = Path(result["summary_path"])
            events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            lock_payload = json.loads((config.output_dir / ".flow_profiler.lock").read_text(encoding="utf-8"))

        self.assertEqual(len(events), 1)
        self.assertEqual(result["events_written"], 1)
        self.assertEqual(summary["total_events"], 1)
        self.assertEqual(lock_payload["status"], "released")

    def test_browser_launch_failure_from_runner_is_normalized_and_redacted(self) -> None:
        async def failing_browser_executor(config, run_id):
            raise RuntimeError(
                "TargetClosedError C:\\Users\\Tema\\AppData\\Local\\ms-playwright\\chrome.exe "
                f"{config.user_data_dir} {config.prompts[0].text} google_profile"
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_dir = root / "google_profile"
            user_dir.mkdir()
            full_prompt = "single launch failure prompt must stay private"
            config = build_config(
                mode="single",
                prompt=full_prompt,
                prompt_id="smoke-001",
                output_dir="runs",
                project_root=root,
                user_data_dir=str(user_dir),
                approve_external_action=True,
            )
            result = run_single(config, run_id="single-launch-fail", browser_executor=failing_browser_executor)

            events_path = Path(result["events_path"])
            summary_path = Path(result["summary_path"])
            event = json.loads(events_path.read_text(encoding="utf-8").strip())
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            combined = events_path.read_text(encoding="utf-8") + summary_path.read_text(encoding="utf-8")

        self.assertEqual(event["status"], "error")
        self.assertEqual(event["stop_signal"], "browser_launch_failed")
        self.assertEqual(event["page_state"], "browser-launch-failed")
        self.assertEqual(event["output_count"], 0)
        self.assertEqual(event["error_type"], "browser_launch_failed")
        self.assertEqual(
            event["error_message_redacted"],
            "Browser launch failed before navigation. See local console for details.",
        )
        self.assertEqual(summary["stop_signal"], "browser_launch_failed")
        for forbidden in (
            "C:\\Users\\",
            "AppData",
            "ms-playwright",
            "chrome.exe",
            "google_profile",
            full_prompt,
            str(user_dir),
        ):
            self.assertNotIn(forbidden, combined)

    def test_dry_run_does_not_import_browser_single_or_playwright(self) -> None:
        blocked = {"playwright", "flow_profiler.browser_single"}
        original_import = builtins.__import__
        imported: list[str] = []

        def tracking_import(name, globals=None, locals=None, fromlist=(), level=0):
            imported.append(name)
            if name.split(".")[0] in blocked or name in blocked:
                raise AssertionError(f"blocked import: {name}")
            return original_import(name, globals, locals, fromlist, level)

        from flow_profiler.runner import run_dry_run

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = build_config(
                mode="dry-run",
                prompt="offline prompt",
                output_dir="runs",
                project_root=root,
            )
            builtins.__import__ = tracking_import
            try:
                run_dry_run(config, run_id="dryrun-isolated")
            finally:
                builtins.__import__ = original_import

        self.assertNotIn("flow_profiler.browser_single", imported)
        self.assertFalse({"playwright"}.intersection({name.split(".")[0] for name in imported}))


class SingleWriterAndSafetyTests(unittest.TestCase):
    def test_writer_redacts_prompt_user_dir_and_sensitive_samples(self) -> None:
        full_prompt = "single prompt must not persist"
        user_dir = r"C:\Users\tester\profile dir"
        event = {
            "schema_version": "test",
            "run_id": "single-1",
            "mode": "single",
            "stage_name": "browser-smoke",
            "sequence_index": 1,
            "planned_delay_sec": 0,
            "prompt_id": "smoke",
            "prompt_hash": "hash",
            "prompt_length": len(full_prompt),
            "aspect_ratio": "landscape",
            "requested_outputs": 1,
            "status": "error",
            "http_statuses": [403],
            "output_count": 0,
            "warning_signals": ["access_denied"],
            "error_message_redacted": (
                f"{full_prompt} {user_dir} "
                + ("session-" + "tok" + "en=abc123 ")
                + ("BEARER_" + "TOK" + "EN=abc123 ")
                + "cookie=abc person@example.test "
                + "https://user:pass@proxy.invalid/path https://images.invalid/generated.png"
            ),
            "page_state": "browser-error",
            "raw_prompt": full_prompt,
            "user_data_dir": user_dir,
        }

        safe = sanitize_event(event, extra_secrets=[full_prompt, user_dir])
        serialized = json.dumps(safe)
        self.assertNotIn("raw_prompt", safe)
        self.assertNotIn("user_data_dir", safe)
        for forbidden in (
            full_prompt,
            user_dir,
            "abc123",
            "person@example.test",
            "proxy.invalid",
            "images.invalid",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_writer_redacts_browser_log_paths(self) -> None:
        full_prompt = "launch log prompt must not persist"
        user_dir = r"C:\Users\Tema\project\google_profile"
        raw_message = (
            "TargetClosedError\n"
            "==================== Browser logs ====================\n"
            "<launching> C:\\Users\\Tema\\AppData\\Local\\ms-playwright\\chromium\\chrome.exe "
            "--user-data-dir=C:\\Users\\Tema\\project\\google_profile\n"
            "======================================================\n"
            f"{full_prompt} {user_dir} person@example.test https://images.invalid/result.png"
        )
        safe = sanitize_event(
            {
                "schema_version": "test",
                "run_id": "single-1",
                "mode": "single",
                "stage_name": "browser-smoke",
                "sequence_index": 1,
                "planned_delay_sec": 0,
                "prompt_id": "smoke",
                "prompt_hash": "hash",
                "prompt_length": len(full_prompt),
                "aspect_ratio": "landscape",
                "requested_outputs": 1,
                "status": "error",
                "http_statuses": [],
                "output_count": 0,
                "warning_signals": [],
                "error_message_redacted": raw_message,
                "page_state": "browser-launch-failed",
            },
            extra_secrets=[full_prompt, user_dir],
        )
        serialized = json.dumps(safe)
        for forbidden in (
            "C:\\Users\\",
            "AppData",
            "ms-playwright",
            "chrome.exe",
            "google_profile",
            full_prompt,
            user_dir,
            "person@example.test",
            "images.invalid",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_stop_condition_classifier_mappings(self) -> None:
        classifier = SafetyClassifier()
        cases = {
            401: "auth_required",
            403: "access_denied",
            429: "rate_limited",
            "choose an account": "account_chooser",
            "auth challenge": "auth_challenge",
            "session expired": "auth_challenge",
            "recaptcha": "captcha_required",
            "human verification": "human_verification",
            "suspicious activity": "account_risk",
            "account warning": "account_warning",
            "credits exhausted": "quota_or_credits",
            "rate limit": "rate_limited",
            "generation unavailable": "generation_unavailable",
            "unexpected redirect": "unexpected_redirect",
            "browser launch failed": "browser_launch_failed",
            "timeout": "timeout",
            "repeated timeout": "repeated_timeout",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                signal = classifier.classify(value)
                self.assertEqual(signal.kind, expected)
                self.assertEqual(signal.severity, "hard_stop")


class SingleStaticSafetyTests(unittest.TestCase):
    def test_browser_adapter_classifies_launch_failure_without_real_playwright(self) -> None:
        class FakeTargetClosedError(Exception):
            pass

        class FakeChromium:
            async def launch_persistent_context(self, *args, **kwargs):
                raise FakeTargetClosedError(
                    "TargetClosedError C:\\Users\\Tema\\AppData\\Local\\ms-playwright\\chrome.exe "
                    "google_profile simple safe landscape test"
                )

        class FakePlaywright:
            chromium = FakeChromium()

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return None

        fake_async_api = types.ModuleType("playwright.async_api")
        fake_async_api.TimeoutError = TimeoutError
        fake_async_api.async_playwright = lambda: FakePlaywright()
        fake_playwright = types.ModuleType("playwright")
        original_modules = {
            name: sys.modules.get(name)
            for name in ("playwright", "playwright.async_api")
        }
        sys.modules["playwright"] = fake_playwright
        sys.modules["playwright.async_api"] = fake_async_api

        try:
            from flow_profiler.browser_single import run_single_browser_smoke

            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                user_dir = root / "google_profile"
                user_dir.mkdir()
                config = build_config(
                    mode="single",
                    prompt="simple safe landscape test",
                    prompt_id="smoke-001",
                    output_dir="runs",
                    project_root=root,
                    user_data_dir=str(user_dir),
                    approve_external_action=True,
                )
                event = __import__("asyncio").run(run_single_browser_smoke(config, "adapter-launch-fail"))
        finally:
            for name, module in original_modules.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.assertEqual(event["status"], "error")
        self.assertEqual(event["stop_signal"], "browser_launch_failed")
        self.assertEqual(event["page_state"], "browser-launch-failed")
        self.assertEqual(event["output_count"], 0)
        self.assertEqual(event["error_type"], "browser_launch_failed")
        self.assertEqual(
            event["error_message_redacted"],
            "Browser launch failed before navigation. See local console for details.",
        )

    def test_browser_module_import_does_not_import_playwright(self) -> None:
        original_import = builtins.__import__

        def tracking_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name.split(".")[0] == "playwright":
                raise AssertionError(f"blocked import: {name}")
            return original_import(name, globals, locals, fromlist, level)

        builtins.__import__ = tracking_import
        try:
            __import__("flow_profiler.browser_single")
        finally:
            builtins.__import__ = original_import

    def test_static_safety_for_browser_only_surface(self) -> None:
        profiler_files = [PROJECT_ROOT / "flow_quota_profiler.py"]
        profiler_files.extend(sorted((PROJECT_ROOT / "flow_profiler").glob("*.py")))
        forbidden = [
            "TwoCaptcha",
            "twocaptcha",
            "grecaptcha",
            "aiohttp",
            "import requests",
            "from requests import",
            "import telegram",
            "from telegram import",
            "node-telegram-bot-api",
            "batchGenerateImages",
            "flowMedia",
            "aisandbox-pa.googleapis.com",
            "api_config",
            "proxylist",
            "from flow_bot import",
            "import flow_bot",
            "from google_labs_flow_bot import",
            "import google_labs_flow_bot",
            "from gemini_bot import",
            "import gemini_bot",
            "from bot import",
            "import bot",
            "from login import",
            "import login",
        ]
        for path in profiler_files:
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                with self.subTest(path=path.name, marker=marker):
                    self.assertNotIn(marker, text)
            if path.name == "browser_single.py":
                self.assertIn("    from playwright.async_api import", text)
            elif path.name == "browser_check.py":
                self.assertIn('import_module("playwright.async_api")', text)
            else:
                self.assertNotIn("playwright", text)

    def test_cli_rejects_repeated_user_data_dir_before_prompt_echo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_dir = root / "profile"
            output_dir = root / "runs"
            user_dir.mkdir()
            unique_prompt = "rl001b repeated flag prompt must stay private 6824"
            completed = subprocess.run(
                [
                    sys.executable,
                    "flow_quota_profiler.py",
                    "--mode",
                    "single",
                    "--prompt-id",
                    "smoke-001",
                    "--prompt",
                    unique_prompt,
                    "--output-dir",
                    str(output_dir),
                    "--user-data-dir",
                    str(user_dir),
                    "--user-data-dir",
                    str(user_dir),
                    "--approve-external-action",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 2)
        self.assertNotIn(unique_prompt, completed.stdout)
        self.assertNotIn(unique_prompt, completed.stderr)
        self.assertNotIn(str(user_dir), completed.stdout)
        self.assertNotIn(str(user_dir), completed.stderr)


if __name__ == "__main__":
    unittest.main()
