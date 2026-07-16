"""Fail-closed redaction for values that may reach application logs."""
from __future__ import annotations

import logging
import re
import traceback
from typing import Iterable


REDACTED = "[redacted]"

_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_OAUTH_RE = re.compile(r"(?i)\bOAuth\s+[A-Za-z0-9._~+/=-]{8,}")
_GOOGLE_TOKEN_RE = re.compile(r"\bya29\.[A-Za-z0-9_-]{8,}")
_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{20,}\b")
_CREDENTIAL_URL_RE = re.compile(
    r"(?i)\b((?:https?|socks5h?)://)[^\s/:@]+:[^\s/@]+@"
)
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?ix)"
    r"(?P<prefix>[\"']?"
    r"(?:authorization|bearer|token|access_token|api[_-]?key|secret|password|pass|"
    r"cookie|session[_-]?token|captcha[_-]?token|webhook[_-]?secret|media[_-]?id|"
    r"project[_-]?id|payment[_-]?id|charge[_-]?id|invoice[_-]?payload|"
    r"(?:referred[_-]?)?user(?:[_-]?id)?|uid|chat(?:[_-]?id)?|"
    r"account(?:[_-]?id)?|channel(?:[_-]?id)?|ticket(?:[_-]?id)?|promo[_-]?code|"
    r"sid|hsid|ssid|__secure-[13]psid(?:ts)?)"
    r"[\"']?\s*[:=]\s*)"
    r"(?P<quote>[\"']?)"
    r"(?P<value>[^\s,;&}\"']{1,})"
    r"(?P=quote)"
)


def redact_text(value: object, *, extra_secrets: Iterable[str] = ()) -> str:
    """Return display-safe text without logging or raising on hostile values."""
    try:
        text = str(value)
        for secret in extra_secrets:
            if secret:
                text = text.replace(str(secret), REDACTED)
        text = _CREDENTIAL_URL_RE.sub(r"\1[redacted]@", text)
        text = _BEARER_RE.sub(f"Bearer {REDACTED}", text)
        text = _OAUTH_RE.sub(f"OAuth {REDACTED}", text)
        text = _GOOGLE_TOKEN_RE.sub(REDACTED, text)
        text = _TELEGRAM_TOKEN_RE.sub(REDACTED, text)
        text = _SENSITIVE_ASSIGNMENT_RE.sub(
            lambda match: (
                f"{match.group('prefix')}{match.group('quote')}"
                f"{REDACTED}{match.group('quote')}"
            ),
            text,
        )
        return _EMAIL_RE.sub(REDACTED, text)
    except BaseException:
        return REDACTED


class SecretRedactionFilter(logging.Filter):
    """Sanitize the formatted message, stack and traceback before emission."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_text(record.getMessage())
            record.args = ()
            if record.exc_info:
                exc_type, _exc_value, exc_traceback = record.exc_info
                frames = "".join(traceback.format_list(traceback.extract_tb(exc_traceback)))
                rendered = (
                    f"{frames}{exc_type.__module__}.{exc_type.__qualname__}: "
                    "[exception details redacted]"
                )
                record.exc_info = None
                record.exc_text = redact_text(rendered)
            elif record.exc_text:
                record.exc_text = redact_text(record.exc_text)
            if record.stack_info:
                record.stack_info = redact_text(record.stack_info)
        except BaseException:
            # Logging must never become a secret-exfiltration fallback.
            record.msg = "[log redaction failed]"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


def install_logging_redaction(logger: logging.Logger | None = None) -> None:
    """Install process-wide redaction and cover already-created handlers."""
    if logger is None:
        current_factory = logging.getLogRecordFactory()
        if not getattr(current_factory, "_flow_secret_redacting", False):
            record_filter = SecretRedactionFilter()

            def redacting_factory(*args, **kwargs):
                record = current_factory(*args, **kwargs)
                record_filter.filter(record)
                return record

            redacting_factory._flow_secret_redacting = True  # type: ignore[attr-defined]
            logging.setLogRecordFactory(redacting_factory)

    target = logger or logging.getLogger()
    for handler in target.handlers:
        if any(isinstance(item, SecretRedactionFilter) for item in handler.filters):
            continue
        handler.addFilter(SecretRedactionFilter())
