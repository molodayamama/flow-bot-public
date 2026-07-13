"""Source guards for the mandatory offline GitHub Actions gate."""
from __future__ import annotations

import unittest
from pathlib import Path


class CiWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
        ).read_text(encoding="utf-8")

    def test_offline_suite_has_import_only_telegram_token(self) -> None:
        self.assertIn('TELEGRAM_TOKEN: "123456789:TEST"', self.source)

    def test_current_node24_actions_are_used(self) -> None:
        self.assertIn("actions/checkout@v6", self.source)
        self.assertIn("actions/setup-python@v6", self.source)

    def test_secret_audit_compile_and_full_tests_are_mandatory(self) -> None:
        self.assertIn("python tools/check_tracked_secrets.py", self.source)
        self.assertIn("bash -n deploy.sh", self.source)
        self.assertIn("python -m compileall", self.source)
        self.assertIn('python -m unittest discover -s tests -p "test_*.py"', self.source)


if __name__ == "__main__":
    unittest.main()
