from __future__ import annotations

import builtins
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import unittest
from pathlib import Path

from flow_profiler.config import ConfigError, build_config, validate_no_dangerous_flags
from flow_profiler.lock import FlowProfilerLock, LockError
from flow_profiler.planner import plan_dry_run, prompt_hash
from flow_profiler.runner import run_dry_run
from flow_profiler.safety import SafetyClassifier
from flow_profiler.writers import sanitize_event, write_jsonl, write_summary


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def test_accepts_prompt_and_prompt_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt_file = root / "prompts.txt"
            prompt_file.write_text("first\n\nsecond\n", encoding="utf-8")

            direct = build_config(
                mode="dry-run",
                prompt="simple safe prompt",
                output_dir="runs",
                project_root=root,
            )
            from_file = build_config(
                mode="dry-run",
                prompt_file=prompt_file,
                output_dir="runs",
                project_root=root,
            )

        self.assertEqual(direct.prompts[0].prompt_id, "prompt-001")
        self.assertEqual(len(from_file.prompts), 2)
        self.assertEqual(from_file.max_generations, 2)

    def test_rejects_unsupported_modes_and_invalid_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for mode in ("batch", "ramp"):
                with self.subTest(mode=mode):
                    with self.assertRaises(ConfigError):
                        build_config(mode=mode, prompt="x", output_dir="runs", project_root=root)

            invalid = [
                {"requested_outputs": 0},
                {"max_generations": 0},
                {"delay_sec": -0.1},
            ]
            for kwargs in invalid:
                with self.subTest(kwargs=kwargs):
                    with self.assertRaises(ConfigError):
                        build_config(
                            mode="dry-run",
                            prompt="x",
                            output_dir="runs",
                            project_root=root,
                            **kwargs,
                        )

    def test_rejects_missing_prompt_source_dangerous_flags_and_unsafe_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unsafe_paths = [
                ".",
                "..",
                "../runs",
                ".git",
                "google_profile",
                ".env",
                ".env_flow",
                "api_config.json",
            ]
            with self.assertRaises(ConfigError):
                build_config(mode="dry-run", output_dir="runs", project_root=root)
            with self.assertRaises(ConfigError):
                validate_no_dangerous_flags(["--browser"])
            for value in unsafe_paths:
                with self.subTest(output_dir=value):
                    with self.assertRaises(ConfigError):
                        build_config(
                            mode="dry-run",
                            prompt="x",
                            output_dir=value,
                            project_root=root,
                        )

    def test_rejects_prompt_file_secret_or_outside_project_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("SECRET=value\n", encoding="utf-8")
            with self.assertRaises(ConfigError):
                build_config(
                    mode="dry-run",
                    prompt_file=".env",
                    output_dir="runs",
                    project_root=root,
                )
            with self.assertRaises(ConfigError):
                build_config(
                    mode="dry-run",
                    prompt_file="../outside.txt",
                    output_dir="runs",
                    project_root=root,
                )


class PlannerTests(unittest.TestCase):
    def test_planned_events_are_deterministic_and_prompt_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(
                mode="dry-run",
                prompt="unique planner prompt",
                prompt_id="test-id",
                output_dir="runs",
                max_generations=2,
                project_root=Path(tmp),
            )
            first = [event.to_dict() for event in plan_dry_run(config, "run-fixed")]
            second = [event.to_dict() for event in plan_dry_run(config, "run-fixed")]

        self.assertEqual(first, second)
        self.assertEqual([event["sequence_index"] for event in first], [1, 2])
        self.assertEqual(first[0]["prompt_id"], "test-id")
        self.assertEqual(first[0]["prompt_hash"], prompt_hash("unique planner prompt"))
        self.assertEqual(first[0]["prompt_length"], len("unique planner prompt"))
        self.assertNotIn("unique planner prompt", json.dumps(first))


class RunnerTests(unittest.TestCase):
    def test_dry_run_does_not_import_runtime_adapters_or_read_state_files(self) -> None:
        blocked_imports = {
            "playwright",
            "flow_profiler.browser_single",
            "flow_profiler.browser_check",
            "flow_bot",
            "google_labs_flow_bot",
            "gemini_bot",
            "bot",
            "login",
        }
        original_import = builtins.__import__
        imported: list[str] = []

        def tracking_import(name, globals=None, locals=None, fromlist=(), level=0):
            imported.append(name)
            if name.split(".")[0] in blocked_imports or name in blocked_imports:
                raise AssertionError(f"blocked import: {name}")
            return original_import(name, globals, locals, fromlist, level)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = build_config(
                mode="dry-run",
                prompt="offline only prompt",
                output_dir="runs",
                project_root=root,
            )
            builtins.__import__ = tracking_import
            try:
                result = run_dry_run(config, run_id="run-isolated")
            finally:
                builtins.__import__ = original_import
            self.assertTrue(Path(result["events_path"]).exists())

        self.assertFalse({"playwright"}.intersection({name.split(".")[0] for name in imported}))
        self.assertNotIn("flow_profiler.browser_single", imported)

    def test_no_csv_files_are_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = build_config(
                mode="dry-run",
                prompt="csv absence prompt",
                output_dir="runs",
                project_root=root,
            )
            run_dry_run(config, run_id="run-no-csv")
            self.assertEqual(list(root.rglob("*.csv")), [])


class WriterTests(unittest.TestCase):
    def test_writers_drop_unknown_fields_and_redact_sensitive_text(self) -> None:
        full_prompt = "never persist this exact prompt"
        event = {
            "schema_version": "test",
            "run_id": "run-1",
            "mode": "dry-run",
            "stage_name": "offline-plan",
            "sequence_index": 1,
            "planned_delay_sec": 0,
            "prompt_id": "pid",
            "prompt_hash": "abc",
            "prompt_length": len(full_prompt),
            "aspect_ratio": "landscape",
            "requested_outputs": 1,
            "status": "planned",
            "http_statuses": [],
            "output_count": 0,
            "warning_signals": [],
            "error_message_redacted": (
                f"{full_prompt} Bearer abc123 cookie=abc "
                "person@example.test http://REDACTED:REDACTED@proxy.example.invalid:8080/path "
                "https://images.invalid/result.png"
            ),
            "full_prompt": full_prompt,
            "generated_image_url": "https://images.invalid/secret.png",
        }

        safe = sanitize_event(event, extra_secrets=[full_prompt])
        serialized = json.dumps(safe)
        self.assertNotIn("full_prompt", safe)
        self.assertNotIn("generated_image_url", safe)
        self.assertNotIn(full_prompt, serialized)
        self.assertNotIn("abc123", serialized)
        self.assertNotIn("person@example.test", serialized)
        self.assertNotIn("proxy.invalid", serialized)
        self.assertNotIn("images.invalid", serialized)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_jsonl(root / "events.jsonl", [event], extra_secrets=[full_prompt])
            write_summary(
                root / "summary.json",
                {
                    "schema_version": "test",
                    "run_id": "run-1",
                    "mode": "dry-run",
                    "total_events": 1,
                    "warnings": [full_prompt],
                    "unknown": full_prompt,
                },
                extra_secrets=[full_prompt],
            )
            output = (root / "events.jsonl").read_text(encoding="utf-8")
            output += (root / "summary.json").read_text(encoding="utf-8")
        self.assertNotIn(full_prompt, output)
        self.assertNotIn("unknown", output)


class CliSafetyTests(unittest.TestCase):
    def test_unique_prompt_absent_from_files_stdout_and_stderr(self) -> None:
        unique_prompt = "rl001a unique prompt must stay private 5819"
        output_dir = PROJECT_ROOT / "flow_profiler_runs" / f"test-{uuid.uuid4().hex}"
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "flow_quota_profiler.py",
                    "--mode",
                    "dry-run",
                    "--prompt-id",
                    "test-001",
                    "--prompt",
                    unique_prompt,
                    "--output-dir",
                    str(output_dir),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            file_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in output_dir.rglob("*")
                if path.is_file()
            )
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn(unique_prompt, completed.stdout)
        self.assertNotIn(unique_prompt, completed.stderr)
        self.assertNotIn(unique_prompt, file_text)

    def test_practical_errors_do_not_echo_prompt(self) -> None:
        unique_prompt = "rl001a rejected prompt must stay private 9140"
        completed = subprocess.run(
            [
                sys.executable,
                "flow_quota_profiler.py",
                "--mode",
                "single",
                "--prompt",
                unique_prompt,
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn(unique_prompt, completed.stdout)
        self.assertNotIn(unique_prompt, completed.stderr)


class SafetyClassifierTests(unittest.TestCase):
    def test_classifies_expected_warning_signals(self) -> None:
        classifier = SafetyClassifier()
        cases = {
            401: "auth_required",
            403: "access_denied",
            429: "rate_limited",
            "captcha challenge": "captcha_required",
            "please login again": "login_required",
            "unusual activity detected": "account_risk",
            "quota exhausted": "quota_or_credits",
            "credits required": "quota_or_credits",
            "too many requests": "rate_limited",
            "unmapped text": "unknown",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(classifier.classify(value).kind, expected)


class LockTests(unittest.TestCase):
    def test_lock_rejects_concurrent_run_and_releases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            first = FlowProfilerLock(root).acquire()
            self.assertEqual(first.lock_path, root / ".flow_profiler.lock")
            try:
                self.assertTrue((root / ".flow_profiler.lock").exists())
                with self.assertRaises(LockError):
                    FlowProfilerLock(root).acquire()
            finally:
                first.release()
            self.assertTrue((root / ".flow_profiler.lock").exists())
            FlowProfilerLock(root).acquire().release()

    def test_atomic_acquire_allows_only_one_initial_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "flow_profiler_runs"
            contender_count = 16
            start = threading.Barrier(contender_count)
            successes: list[FlowProfilerLock] = []
            failures: list[BaseException] = []
            guard = threading.Lock()

            def contend() -> None:
                lock = FlowProfilerLock(root)
                try:
                    start.wait(timeout=5)
                    acquired = lock.acquire()
                except BaseException as exc:
                    with guard:
                        failures.append(exc)
                    return
                with guard:
                    successes.append(acquired)

            threads = [threading.Thread(target=contend) for _ in range(contender_count)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(len(successes), 1)
            self.assertEqual(len(failures), contender_count - 1)
            self.assertTrue(all(isinstance(exc, LockError) for exc in failures))
            self.assertEqual(successes[0].lock_path, root / ".flow_profiler.lock")

            successes[0].release()
            FlowProfilerLock(root).acquire().release()

    def test_stale_lock_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            root.mkdir()
            lock_path = root / ".flow_profiler.lock"
            lock_path.write_text("stale", encoding="utf-8")
            old_time = time.time() - 100
            os.utime(lock_path, (old_time, old_time))
            lock = FlowProfilerLock(root, stale_after_sec=1).acquire()
            try:
                self.assertTrue(lock_path.exists())
            finally:
                lock.release()
            self.assertTrue(lock_path.exists())


class StaticSafetyTests(unittest.TestCase):
    def test_runtime_files_do_not_contain_forbidden_surfaces(self) -> None:
        runtime_files = [PROJECT_ROOT / "flow_quota_profiler.py"]
        runtime_files.extend(sorted((PROJECT_ROOT / "flow_profiler").glob("*.py")))
        forbidden = [
            "TwoCaptcha",
            "twocaptcha",
            "grecaptcha",
            "batchGenerateImages",
            "flowMedia",
            "aisandbox-pa.googleapis.com",
            "api_config",
            "PROXY_URL",
            "proxylist",
            ".har",
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
        for path in runtime_files:
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                with self.subTest(path=path.name, marker=marker):
                    self.assertNotIn(marker, text)
            if path.name not in {"browser_single.py", "browser_check.py"}:
                self.assertNotIn("playwright", text)


if __name__ == "__main__":
    unittest.main()
