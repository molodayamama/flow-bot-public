from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.check_tracked_secrets import audit


class TrackedSecretAuditTests(unittest.TestCase):
    def test_forbidden_runtime_path_is_reported_without_content(self) -> None:
        failures = audit([Path("api_config.json")])
        self.assertEqual(failures, ["forbidden tracked path: api_config.json"])

    def test_high_confidence_token_reports_only_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "leak.py"
            token = "123456789:" + "A" * 35
            path.write_text(f'TOKEN = "{token}"', encoding="utf-8")
            failures = audit([path])
        self.assertEqual(len(failures), 1)
        self.assertNotIn(token, failures[0])

    def test_normal_source_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clean.py"
            path.write_text('TOKEN = os.getenv("TOKEN", "")', encoding="utf-8")
            self.assertEqual(audit([path]), [])

    def test_named_hardcoded_secret_reports_only_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "leak.py"
            value = "private-example-value"
            path.write_text(f'MAX_BOT_TOKEN = "{value}"', encoding="utf-8")
            failures = audit([path])
        self.assertEqual(len(failures), 1)
        self.assertNotIn(value, failures[0])

    def test_private_key_header_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "leak.txt"
            path.write_text("-----BEGIN PRIVATE KEY-----\n", encoding="utf-8")
            self.assertEqual(len(audit([path])), 1)


if __name__ == "__main__":
    unittest.main()
