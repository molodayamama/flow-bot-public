"""Offline production-preflight tests; no secrets or network."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.production_preflight import validate_environment


class ProductionPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "google_profile").mkdir()
        self.env = {
            "TELEGRAM_TOKEN": "123456789:TESTTOKEN",
            "OWNER_ID": "123456789",
            "USER_DATA_DIR": "google_profile",
            "METRICS_DB": "state/metrics.db",
            "USER_PROJECTS_FILE": "state/projects.json",
            "USER_CREDITS_FILE": "state/credits.json",
            "PAYMENTS_FILE": "state/payments.json",
            "CREDITS_SQLITE": "1",
        }
        (self.root / "state").mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_minimal_telegram_production_config_passes(self) -> None:
        report = validate_environment(self.env, root=self.root)
        self.assertTrue(report.ok, report.errors)
        self.assertEqual(report.warnings, ())

    def test_missing_identity_and_profile_fail_without_values(self) -> None:
        env = dict(self.env, TELEGRAM_TOKEN="private", OWNER_ID="", ADMIN_IDS="")
        env["USER_DATA_DIR"] = "missing-private-path"

        report = validate_environment(env, root=self.root)

        self.assertFalse(report.ok)
        rendered = "\n".join(report.errors)
        self.assertIn("TELEGRAM_TOKEN", rendered)
        self.assertIn("OWNER_ID", rendered)
        self.assertIn("USER_DATA_DIR", rendered)
        self.assertNotIn("private", rendered)

    def test_max_production_requires_webhook_and_robokassa(self) -> None:
        env = dict(
            self.env,
            MAX_ENABLED="1",
            MAX_BOT_TOKEN="test-max-token",
            MAX_MODE="poll",
        )

        report = validate_environment(env, root=self.root)

        self.assertFalse(report.ok)
        self.assertIn("MAX production requires ROBOKASSA_ENABLED=1 for top-up", report.errors)
        self.assertTrue(any("MAX_MODE" in error or "webhook" in error for error in report.errors))

    def test_complete_max_webhook_and_payment_config_passes(self) -> None:
        env = dict(
            self.env,
            ROBOKASSA_ENABLED="1",
            ROBOKASSA_MERCHANT_LOGIN="merchant",
            ROBOKASSA_PASSWORD1="password-one",
            ROBOKASSA_PASSWORD2="password-two",
            ROBOKASSA_PUBLIC_BASE_URL="https://pay.example.test",
            ROBOKASSA_SCOPE="consumer",
            MAX_ENABLED="1",
            MAX_BOT_TOKEN="max-token",
            MAX_MODE="webhook",
            MAX_WEBHOOK_URL="https://bot.example.test/max/webhook",
            MAX_WEBHOOK_SECRET="safe-secret",
            MAX_INBOX_DB="state/max.db",
            MAX_INBOX_WORKERS="4",
        )

        report = validate_environment(env, root=self.root)

        self.assertTrue(report.ok, report.errors)

    def test_runtime_parent_must_exist(self) -> None:
        env = dict(self.env, METRICS_DB="missing/metrics.db")
        report = validate_environment(env, root=self.root)
        self.assertIn("METRICS_DB parent directory does not exist", report.errors)

    def test_seller_does_not_require_browser_profile(self) -> None:
        env = dict(self.env, BOT_MODE="seller", USER_DATA_DIR="missing-profile")
        report = validate_environment(env, root=self.root)
        self.assertTrue(report.ok, report.errors)

    def test_seller_infers_its_robokassa_scope(self) -> None:
        env = dict(
            self.env,
            BOT_MODE="seller",
            ROBOKASSA_ENABLED="1",
            ROBOKASSA_MERCHANT_LOGIN="merchant",
            ROBOKASSA_PASSWORD1="one",
            ROBOKASSA_PASSWORD2="two",
            ROBOKASSA_PUBLIC_BASE_URL="https://pay.example.test",
        )
        report = validate_environment(env, root=self.root)
        self.assertTrue(report.ok, report.errors)

    def test_robokassa_credentials_enable_runtime_validation_implicitly(self) -> None:
        env = dict(
            self.env,
            ROBOKASSA_MERCHANT_LOGIN="merchant",
            ROBOKASSA_PASSWORD1="one",
            ROBOKASSA_PASSWORD2="two",
        )
        report = validate_environment(env, root=self.root)
        self.assertIn("ROBOKASSA_PUBLIC_BASE_URL is required", report.errors)

    def test_legacy_json_credit_store_is_rejected_in_production(self) -> None:
        env = dict(self.env, CREDITS_SQLITE="0")
        report = validate_environment(env, root=self.root)
        self.assertFalse(report.ok)
        self.assertTrue(any("atomic production credits" in error for error in report.errors))

        development = validate_environment(env, root=self.root, production=False)
        self.assertTrue(development.ok)
        self.assertTrue(
            any("atomic production credits" in warning for warning in development.warnings)
        )


if __name__ == "__main__":
    unittest.main()
