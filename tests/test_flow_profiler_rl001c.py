from __future__ import annotations

import builtins
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

from flow_profiler.config import ConfigError, build_config
from flow_profiler.runner import run_browser_check, run_dry_run, run_single
from flow_profiler.writers import sanitize_event


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _browser_check_config(root: Path, **kwargs):
    params = {
        "mode": "browser-check",
        "output_dir": "runs",
        "project_root": root,
        "approve_external_action": True,
    }
    params.update(kwargs)
    return build_config(**params)


class BrowserCheckConfigTests(unittest.TestCase):
    def test_browser_check_requires_approval_and_rejects_generation_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt_file = root / "prompts.txt"
            prompt_file.write_text("x\n", encoding="utf-8")
            user_dir = root / "profile"
            user_dir.mkdir()

            config = _browser_check_config(root)
            self.assertEqual(config.mode, "browser-check")
            self.assertEqual(config.prompts, ())
            self.assertEqual(config.max_generations, 0)
            self.assertIsNone(config.user_data_dir)

            bad_cases = [
                {"approve_external_action": False},
                {"prompt": "must be rejected"},
                {"prompt_file": prompt_file},
                {"prompt_id": "unused"},
                {"requested_outputs": 2},
                {"max_generations": 2},
                {"delay_sec": 1},
                {"timeout_sec": 4},
                {"timeout_sec": 301},
                {"user_data_dir": [user_dir, user_dir]},
                {"user_data_dir": "../profile"},
            ]
            for kwargs in bad_cases:
                with self.subTest(kwargs=kwargs):
                    with self.assertRaises(ConfigError):
                        _browser_check_config(root, **kwargs)

    def test_browser_check_accepts_zero_or_one_existing_user_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_dir = root / "profile"
            user_dir.mkdir()
            regular_file = root / "not-a-dir"
            regular_file.write_text("x", encoding="utf-8")

            self.assertIsNone(_browser_check_config(root).user_data_dir)
            self.assertEqual(_browser_check_config(root, user_data_dir=str(user_dir)).user_data_dir, user_dir)
            self.assertEqual(_browser_check_config(root, user_data_dir=[str(user_dir)]).user_data_dir, user_dir)

            with self.assertRaises(ConfigError):
                _browser_check_config(root, user_data_dir=regular_file)
            with self.assertRaises(ConfigError):
                _browser_check_config(root, user_data_dir=root / "missing")

    def test_omitted_user_data_dir_does_not_create_or_resolve_google_profile(self) -> None:
        async def fake_diagnostics(config, run_id):
            self.assertIsNone(config.user_data_dir)
            return [_diagnostic_event(run_id, 1, "playwright_import", "success")]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _browser_check_config(root)
            result = run_browser_check(config, run_id="browsercheck-no-profile", diagnostic_executor=fake_diagnostics)

            self.assertTrue(Path(result["events_path"]).exists())
            self.assertFalse((root / "google_profile").exists())


class BrowserCheckRunnerTests(unittest.TestCase):
    def test_mocked_diagnostics_write_events_and_summary_under_run_dir(self) -> None:
        async def fake_diagnostics(config, run_id):
            self.assertTrue((config.output_dir / ".flow_profiler.lock").exists())
            return [
                _diagnostic_event(run_id, 1, "playwright_import", "success"),
                _diagnostic_event(run_id, 2, "playwright_manager_start", "success"),
                _diagnostic_event(
                    run_id,
                    3,
                    "chromium_launch_temp_clean",
                    "error",
                    diagnostic_signal="missing_playwright_browser_install",
                    message="Executable missing",
                ),
                _diagnostic_event(
                    run_id,
                    4,
                    "persistent_context_temp_clean",
                    "skipped",
                    diagnostic_signal="prerequisite_failed",
                ),
            ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _browser_check_config(root)
            result = run_browser_check(config, run_id="browsercheck-test", diagnostic_executor=fake_diagnostics)
            events_path = Path(result["events_path"])
            summary_path = Path(result["summary_path"])
            events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            files = {path.name for path in (root / "runs" / "browsercheck-test").iterdir()}

        self.assertEqual(result["events_written"], 4)
        self.assertEqual([event["check_name"] for event in events], [
            "playwright_import",
            "playwright_manager_start",
            "chromium_launch_temp_clean",
            "persistent_context_temp_clean",
        ])
        self.assertEqual(summary["planned_generations"], 0)
        self.assertEqual(summary["status_counts"], {"error": 1, "skipped": 1, "success": 2})
        self.assertEqual(summary["warnings"], ["missing_playwright_browser_install"])
        self.assertEqual(files, {"events.jsonl", "summary.json"})

    def test_diagnostic_errors_are_redacted_and_unknown_fields_are_dropped(self) -> None:
        user_dir = r"C:\Users\Tema\project\google_profile"
        raw_message = (
            "TargetClosedError\n"
            "==================== Browser logs ====================\n"
            "<launching> C:\\Users\\Tema\\AppData\\Local\\ms-playwright\\chromium\\chrome.exe "
            f"--user-data-dir={user_dir} --cookie=" + ("session-" + "token=abc123\n")
            +
            "==================== End logs ====================\n"
            "Bearer abc person@example.test https://labs.google/fx/tools/flow"
        )
        safe = sanitize_event(
            {
                "schema_version": "test",
                "run_id": "browsercheck-1",
                "mode": "browser-check",
                "stage_name": "browser-diagnostic",
                "sequence_index": 3,
                "started_at": "2026-05-18T00:00:00Z",
                "finished_at": "2026-05-18T00:00:01Z",
                "duration_ms": 1000,
                "status": "error",
                "warning_signals": ["unknown_local_launch_failure"],
                "error_type": "unknown_local_launch_failure",
                "check_name": "chromium_launch_temp_clean",
                "diagnostic_signal": "unknown_local_launch_failure",
                "sanitized_error_type": "TargetClosedError",
                "sanitized_error_message": raw_message,
                "browser_channel_used": "bundled-chromium",
                "user_data_dir": user_dir,
                "executable_path": r"C:\Users\Tema\AppData\Local\chrome.exe",
            },
            extra_secrets=[user_dir],
        )
        serialized = json.dumps(safe)
        self.assertNotIn("user_data_dir", safe)
        self.assertNotIn("executable_path", safe)
        for forbidden in (
            "C:\\Users\\",
            "AppData",
            "ms-playwright",
            "chrome.exe",
            "google_profile",
            "session-" + "token",
            "abc123",
            "person@example.test",
            "labs.google",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_dry_run_and_single_runners_still_reject_browser_check_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _browser_check_config(Path(tmp))
            with self.assertRaises(ValueError):
                run_dry_run(config)
            with self.assertRaises(ValueError):
                run_single(config)


class BrowserCheckStaticSafetyTests(unittest.TestCase):
    def test_browser_check_module_import_does_not_import_playwright(self) -> None:
        original_import = builtins.__import__

        def tracking_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name.split(".")[0] == "playwright":
                raise AssertionError(f"blocked import: {name}")
            return original_import(name, globals, locals, fromlist, level)

        sys.modules.pop("flow_profiler.browser_check", None)
        builtins.__import__ = tracking_import
        try:
            __import__("flow_profiler.browser_check")
        finally:
            builtins.__import__ = original_import

    def test_browser_check_executor_uses_only_safe_urls(self) -> None:
        text = (PROJECT_ROOT / "flow_profiler" / "browser_check.py").read_text(encoding="utf-8")
        self.assertIn('SAFE_DIAGNOSTIC_URL = "about:blank"', text)
        self.assertNotIn("labs.google", text)
        self.assertNotIn("http://", text)
        self.assertNotIn("https://", text)
        self.assertNotIn("screenshot", text)
        self.assertNotIn(".har", text)

    def test_static_safety_allows_playwright_only_in_browser_modules(self) -> None:
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
            if path.name not in {"browser_single.py", "browser_check.py"}:
                self.assertNotIn("playwright", text)

    def test_fake_playwright_failure_produces_sanitized_diagnostic_events(self) -> None:
        class FakeChromium:
            async def launch(self, *args, **kwargs):
                raise RuntimeError(
                    "Executable doesn't exist at C:\\Users\\Tema\\AppData\\Local\\ms-playwright\\chrome.exe"
                )

        class FakePlaywright:
            chromium = FakeChromium()

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return None

        fake_async_api = types.ModuleType("playwright.async_api")
        fake_async_api.async_playwright = lambda: FakePlaywright()
        fake_playwright = types.ModuleType("playwright")
        original_modules = {name: sys.modules.get(name) for name in ("playwright", "playwright.async_api")}
        sys.modules["playwright"] = fake_playwright
        sys.modules["playwright.async_api"] = fake_async_api

        try:
            from flow_profiler.browser_check import run_browser_check_diagnostics

            with tempfile.TemporaryDirectory() as tmp:
                config = _browser_check_config(Path(tmp))
                events = __import__("asyncio").run(run_browser_check_diagnostics(config, "browsercheck-fake"))
        finally:
            for name, module in original_modules.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        launch_event = events[2]
        self.assertEqual(launch_event["status"], "error")
        self.assertEqual(launch_event["diagnostic_signal"], "missing_playwright_browser_install")
        self.assertNotIn("C:\\Users\\", json.dumps(launch_event))
        self.assertNotIn("ms-playwright", json.dumps(launch_event))


def _diagnostic_event(
    run_id: str,
    sequence_index: int,
    check_name: str,
    status: str,
    *,
    diagnostic_signal: str | None = None,
    message: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "test",
        "run_id": run_id,
        "mode": "browser-check",
        "stage_name": "browser-diagnostic",
        "sequence_index": sequence_index,
        "started_at": "2026-05-18T00:00:00Z",
        "finished_at": "2026-05-18T00:00:01Z",
        "duration_ms": 1000,
        "status": status,
        "warning_signals": [diagnostic_signal] if status == "error" and diagnostic_signal else [],
        "error_type": diagnostic_signal if status == "error" else None,
        "check_name": check_name,
        "diagnostic_signal": diagnostic_signal,
        "sanitized_error_type": "RuntimeError" if status == "error" else None,
        "sanitized_error_message": message,
        "browser_channel_used": "bundled-chromium",
    }


if __name__ == "__main__":
    unittest.main()
