"""Values-free tests for the process-wide logging redaction boundary."""
from __future__ import annotations

import io
import logging
import unittest
from pathlib import Path

from security.logging_redaction import (
    SecretRedactionFilter,
    install_logging_redaction,
    redact_text,
)


class LoggingRedactionTests(unittest.TestCase):
    def test_hostile_string_conversion_fails_closed(self) -> None:
        class Hostile:
            def __str__(self) -> str:
                raise RuntimeError("must not escape")

        self.assertEqual(redact_text(Hostile()), "[redacted]")

    def test_common_credentials_and_identity_values_are_redacted(self) -> None:
        secret_values = (
            "example-bearer-value",
            "123456789:REDACTED",
            "REDACTED_CREDENTIAL",
            "project-private-value",
            "person@example.test",
            "42",
        )
        raw = (
            f"Authorization: Bearer {secret_values[0]} "
            f"telegram={secret_values[1]} "
            f"proxy=http://user:{secret_values[2]}@proxy.example.test:8080 "
            f"project_id={secret_values[3]} email={secret_values[4]} "
            f"user_id={secret_values[5]}"
        )

        rendered = redact_text(raw)

        for secret in secret_values:
            self.assertNotIn(secret, rendered)
        self.assertIn("[redacted]", rendered)

    def test_filter_redacts_formatted_args_and_exception(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(SecretRedactionFilter())
        logger = logging.Logger("redaction-test")
        logger.addHandler(handler)
        try:
            raise ValueError(
                "token=private-token-value and http://REDACTED:REDACTED@proxy.example.invalid:8080"
            )
        except ValueError:
            logger.exception("request failed Authorization=Bearer %s", "private-bearer")

        rendered = stream.getvalue()
        for secret in ("private-token-value", "REDACTED_CREDENTIAL", "private-bearer"):
            self.assertNotIn(secret, rendered)
        self.assertIn("ValueError", rendered)
        self.assertIn("exception details redacted", rendered)

    def test_install_is_idempotent_and_preserves_safe_context(self) -> None:
        logger = logging.Logger("install-test")
        handler = logging.NullHandler()
        logger.addHandler(handler)

        install_logging_redaction(logger)
        install_logging_redaction(logger)

        filters = [item for item in handler.filters if isinstance(item, SecretRedactionFilter)]
        self.assertEqual(len(filters), 1)
        self.assertEqual(redact_text("status=403 action=VIDEO_GENERATION"), "status=403 action=VIDEO_GENERATION")

    def test_process_install_redacts_handlers_added_later(self) -> None:
        old_factory = logging.getLogRecordFactory()
        stream = io.StringIO()
        logger = logging.Logger("future-handler-test")
        try:
            install_logging_redaction()
            logger.addHandler(logging.StreamHandler(stream))
            logger.error("token=%s", "future-private-value")
        finally:
            logging.setLogRecordFactory(old_factory)

        rendered = stream.getvalue()
        self.assertNotIn("future-private-value", rendered)
        self.assertIn("[redacted]", rendered)

    def test_runtime_sources_do_not_log_raw_provider_values(self) -> None:
        root = Path(__file__).resolve().parents[1]
        http_source = (root / "flow_provider/http_client.py").read_text(encoding="utf-8")
        keeper_source = (root / "flow_provider/session_keeper.py").read_text(encoding="utf-8")
        photo_source = (root / "channels/telegram/photo_intake.py").read_text(encoding="utf-8")
        token_source = (root / "test_tokens.py").read_text(encoding="utf-8")
        captcha_source = (root / "test_recaptcha_params.py").read_text(encoding="utf-8")

        for forbidden in ("gen_text[:", "body=%s", "ref_media_ids", "media_id={media_id}"):
            self.assertNotIn(forbidden, http_source)
        for forbidden in ("body=%s", "mediaId=%s", "Project ID: {pid}", "sitekey[:20]"):
            self.assertNotIn(forbidden, keeper_source)
        self.assertNotIn("project=%s media_id=%s", photo_source)
        self.assertNotIn("BEARER_TOKEN[:", token_source)
        self.assertNotIn("value[:20]", token_source)
        self.assertNotIn("r['token'][:", captcha_source)

    def test_composition_root_installs_redaction_after_logging_setup(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "flow_bot.py").read_text(encoding="utf-8")
        self.assertLess(source.index("logging.basicConfig("), source.index("install_logging_redaction()"))


if __name__ == "__main__":
    unittest.main()
