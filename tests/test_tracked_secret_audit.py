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
            path.write_text("-----BEGIN " + "PRIVATE KEY-----\n", encoding="utf-8")
            self.assertEqual(len(audit([path])), 1)

    def test_private_account_in_html_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.html"
            account = "private-account@" + "gmail.com"
            path.write_text(account, encoding="utf-8")
            failures = audit([path])
            self.assertEqual(len(failures), 1)
            self.assertNotIn(account, failures[0])

    def test_proxy_credentials_in_markdown_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.md"
            value = "AbCdEf0123456789" + ":" + "GhIjKl9876543210"
            path.write_text(f"Credentials: `{value}`", encoding="utf-8")
            self.assertEqual(len(audit([path])), 1)

    def test_token_in_fixture_is_not_exempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tests" / "fixture.txt"
            path.parent.mkdir()
            path.write_text("123456789:" + "A" * 35, encoding="utf-8")
            self.assertEqual(len(audit([path])), 1)

    def test_local_env_and_database_are_forbidden(self) -> None:
        for name in (".env.production", "seller.env", "metrics.db-wal", "archive.bundle"):
            with self.subTest(name=name):
                self.assertIn("forbidden tracked path", audit([Path(name)])[0])

    def test_env_template_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env.example"
            path.write_text("ROBOKASSA_PASSWORD1=replace_me\n", encoding="utf-8")
            self.assertEqual(audit([path]), [])


if __name__ == "__main__":
    unittest.main()
