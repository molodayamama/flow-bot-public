"""Offline production-preflight tests; no secrets or network."""
from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
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
            "FLOW_BROWSER_API_KEY": "configured-at-runtime",
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

    def test_consumer_requires_runtime_browser_api_key(self) -> None:
        env = dict(self.env)
        env.pop("FLOW_BROWSER_API_KEY")

        report = validate_environment(env, root=self.root)

        self.assertIn("FLOW_BROWSER_API_KEY is required", report.errors)

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
            MAX_API_BASE_URL="https://max.example.test",
        )

        report = validate_environment(env, root=self.root)

        self.assertTrue(report.ok, report.errors)

    def test_default_max_api_requires_pinned_russian_root(self) -> None:
        env = dict(
            self.env,
            ROBOKASSA_ENABLED="1",
            ROBOKASSA_MERCHANT_LOGIN="merchant",
            ROBOKASSA_PASSWORD1="password-one",
            ROBOKASSA_PASSWORD2="password-two",
            ROBOKASSA_PUBLIC_BASE_URL="https://pay.example.test",
            MAX_ENABLED="1",
            MAX_BOT_TOKEN="max-token",
            MAX_MODE="webhook",
            MAX_WEBHOOK_URL="https://bot.example.test/max/webhook",
            MAX_WEBHOOK_SECRET="safe-secret",
            MAX_INBOX_DB="state/max.db",
        )

        class _Context:
            def get_ca_certs(self, *, binary_form=False):
                self.assert_binary = binary_form
                return [b"unrelated-root"]

        with patch("tools.production_preflight.ssl.create_default_context", return_value=_Context()):
            report = validate_environment(env, root=self.root)

        self.assertIn(
            "MAX TLS trust is missing: configure MAX_CA_BUNDLE with the Russian Trusted Root CA or install it system-wide",
            report.errors,
        )

    def test_default_max_api_accepts_pinned_russian_root(self) -> None:
        import hashlib
        import tools.production_preflight as preflight

        trusted_der = b"trusted-root-fixture"
        env = dict(
            self.env,
            ROBOKASSA_ENABLED="1",
            ROBOKASSA_MERCHANT_LOGIN="merchant",
            ROBOKASSA_PASSWORD1="password-one",
            ROBOKASSA_PASSWORD2="password-two",
            ROBOKASSA_PUBLIC_BASE_URL="https://pay.example.test",
            MAX_ENABLED="1",
            MAX_BOT_TOKEN="max-token",
            MAX_MODE="webhook",
            MAX_WEBHOOK_URL="https://bot.example.test/max/webhook",
            MAX_WEBHOOK_SECRET="safe-secret",
            MAX_INBOX_DB="state/max.db",
        )

        class _Context:
            def get_ca_certs(self, *, binary_form=False):
                return [trusted_der] if binary_form else []

        fingerprint = hashlib.sha256(trusted_der).hexdigest()
        with (
            patch("tools.production_preflight.ssl.create_default_context", return_value=_Context()),
            patch.object(preflight, "_MAX_TRUSTED_CA_SHA256", {fingerprint}),
        ):
            report = validate_environment(env, root=self.root)

        self.assertTrue(report.ok, report.errors)

    def test_invalid_max_ca_bundle_fails_preflight_without_path_value(self) -> None:
        env = dict(
            self.env,
            ROBOKASSA_ENABLED="1",
            ROBOKASSA_MERCHANT_LOGIN="merchant",
            ROBOKASSA_PASSWORD1="password-one",
            ROBOKASSA_PASSWORD2="password-two",
            ROBOKASSA_PUBLIC_BASE_URL="https://pay.example.test",
            MAX_ENABLED="1",
            MAX_BOT_TOKEN="max-token",
            MAX_MODE="webhook",
            MAX_WEBHOOK_URL="https://bot.example.test/max/webhook",
            MAX_WEBHOOK_SECRET="safe-secret",
            MAX_INBOX_DB="state/max.db",
            MAX_CA_BUNDLE=str(self.root / "private-ca.pem"),
        )
        (self.root / "private-ca.pem").write_text("not a certificate", encoding="utf-8")

        report = validate_environment(env, root=self.root)

        self.assertIn("MAX_CA_BUNDLE is not a valid CA bundle", report.errors)
        self.assertNotIn("private-ca.pem", "\n".join(report.errors))

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

    def test_web_app_requires_payment_secret_and_https_origin(self) -> None:
        env = dict(
            self.env,
            WEB_APP_ENABLED="1",
            WEB_SESSION_SECRET="short",
            WEB_PUBLIC_ORIGIN="http://photozhab.test/path",
        )
        report = validate_environment(env, root=self.root)
        self.assertIn("WEB app production requires ROBOKASSA_ENABLED=1 for top-up", report.errors)
        self.assertIn("WEB_SESSION_SECRET must contain at least 32 characters", report.errors)
        self.assertIn("WEB_PUBLIC_ORIGIN must be an HTTPS origin without a path", report.errors)

    def test_valid_web_app_config_passes_with_zero_starter(self) -> None:
        env = dict(
            self.env,
            WEB_APP_ENABLED="1",
            WEB_SESSION_SECRET="a-random-runtime-secret-over-thirty-two-chars",
            WEB_PUBLIC_ORIGIN="https://photozhab.test",
            WEB_STARTER_CREDITS="0",
            WEB_MEDIA_DIR=str(self.root / "state" / "web-media"),
            ROBOKASSA_ENABLED="1",
            ROBOKASSA_MERCHANT_LOGIN="merchant",
            ROBOKASSA_PASSWORD1="one",
            ROBOKASSA_PASSWORD2="two",
            ROBOKASSA_PUBLIC_BASE_URL="https://pay.photozhab.test",
        )
        report = validate_environment(env, root=self.root)
        self.assertTrue(report.ok, report.errors)

    def test_web_starter_credit_is_rejected_in_production(self) -> None:
        env = dict(
            self.env,
            WEB_APP_ENABLED="1",
            WEB_SESSION_SECRET="a-random-runtime-secret-over-thirty-two-chars",
            WEB_PUBLIC_ORIGIN="https://photozhab.test",
            WEB_STARTER_CREDITS="30",
            ROBOKASSA_ENABLED="1",
            ROBOKASSA_MERCHANT_LOGIN="merchant",
            ROBOKASSA_PASSWORD1="one",
            ROBOKASSA_PASSWORD2="two",
            ROBOKASSA_PUBLIC_BASE_URL="https://pay.photozhab.test",
        )
        report = validate_environment(env, root=self.root)
        self.assertIn("WEB_STARTER_CREDITS must be 0 in production", report.errors)

    def test_web_secure_cookie_cannot_be_disabled_in_production(self) -> None:
        env = dict(
            self.env,
            WEB_APP_ENABLED="1",
            WEB_SESSION_SECRET="a-random-runtime-secret-over-thirty-two-chars",
            WEB_PUBLIC_ORIGIN="https://photozhab.test",
            WEB_COOKIE_SECURE="0",
            ROBOKASSA_ENABLED="1",
            ROBOKASSA_MERCHANT_LOGIN="merchant",
            ROBOKASSA_PASSWORD1="one",
            ROBOKASSA_PASSWORD2="two",
            ROBOKASSA_PUBLIC_BASE_URL="https://pay.photozhab.test",
        )
        report = validate_environment(env, root=self.root)
        self.assertIn("WEB_COOKIE_SECURE must be enabled in production", report.errors)

    def test_web_external_login_settings_fail_closed(self) -> None:
        env = dict(
            self.env,
            WEB_APP_ENABLED="1",
            WEB_SESSION_SECRET="a-random-runtime-secret-over-thirty-two-chars",
            WEB_PUBLIC_ORIGIN="https://photozhab.test",
            WEB_YANDEX_CLIENT_ID="configured-without-secret",
            WEB_MAX_MINI_APP_URL="https://evil.test/fake-max",
            ROBOKASSA_ENABLED="1",
            ROBOKASSA_MERCHANT_LOGIN="merchant",
            ROBOKASSA_PASSWORD1="one",
            ROBOKASSA_PASSWORD2="two",
            ROBOKASSA_PUBLIC_BASE_URL="https://pay.photozhab.test",
        )
        report = validate_environment(env, root=self.root)
        self.assertIn(
            "WEB_YANDEX_CLIENT_ID and WEB_YANDEX_CLIENT_SECRET must be set together",
            report.errors,
        )
        self.assertIn("WEB_MAX_MINI_APP_URL must use https://max.ru", report.errors)

    def test_yandex_welcome_credit_must_remain_thirty_in_production(self) -> None:
        env = dict(
            self.env,
            WEB_APP_ENABLED="1",
            WEB_SESSION_SECRET="a-random-runtime-secret-over-thirty-two-chars",
            WEB_PUBLIC_ORIGIN="https://photozhab.test",
            WEB_YANDEX_CLIENT_ID="client",
            WEB_YANDEX_CLIENT_SECRET="secret",
            WEB_YANDEX_STARTER_CREDITS="0",
            ROBOKASSA_ENABLED="1",
            ROBOKASSA_MERCHANT_LOGIN="merchant",
            ROBOKASSA_PASSWORD1="one",
            ROBOKASSA_PASSWORD2="two",
            ROBOKASSA_PUBLIC_BASE_URL="https://pay.photozhab.test",
        )
        report = validate_environment(env, root=self.root)
        self.assertIn("WEB_YANDEX_STARTER_CREDITS must be 30 in production", report.errors)

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
