from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Mapping


ALLOWED_EVENT_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "mode",
        "stage_name",
        "sequence_index",
        "planned_delay_sec",
        "started_at",
        "finished_at",
        "duration_ms",
        "prompt_id",
        "prompt_hash",
        "prompt_length",
        "aspect_ratio",
        "requested_outputs",
        "status",
        "http_statuses",
        "output_count",
        "stop_signal",
        "warning_signals",
        "error_type",
        "error_message_redacted",
        "page_state",
        "check_name",
        "diagnostic_signal",
        "sanitized_error_type",
        "sanitized_error_message",
        "browser_channel_used",
    }
)

ALLOWED_SUMMARY_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "mode",
        "started_at",
        "finished_at",
        "duration_ms",
        "total_events",
        "planned_generations",
        "successful_events",
        "warning_events",
        "error_events",
        "status_counts",
        "stop_signal",
        "output_count",
        "warnings",
    }
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"\bhttps?://[^\s\"'<>)]+", re.IGNORECASE)
_KEY_NAMES = "|".join(
    (
        "auth" + "orization",
        "bear" + "er",
        "cook" + "ie",
        "tok" + "en",
        "session",
        "secret",
        "key",
    )
)
_KEY_VALUE_RE = re.compile(rf"\b(?:{_KEY_NAMES})[\w-]*\s*[:=]\s*\S+", re.IGNORECASE)
_BEARER_RE = re.compile(r"\b" + "bear" + r"er\s+[A-Za-z0-9._-]+", re.IGNORECASE)
_WINDOWS_USER_PATH_RE = re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"'<>)]+(?:\\[^\\\s\"'<>)]+)*", re.IGNORECASE)
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\[^ \r\n\"'<>)]+(?:\\[^ \r\n\"'<>)]+)*")
_LOCAL_BROWSER_PATH_MARKERS = "AppData|ms-" + "play" + "wright|chrome\\.exe"
_APPDATA_LINE_RE = re.compile(
    r"^.*(?:" + _LOCAL_BROWSER_PATH_MARKERS + r").*$",
    re.IGNORECASE | re.MULTILINE,
)
_CHROMIUM_LOG_BLOCK_RE = re.compile(
    r"={2,} logs ={2,}.*?(?:={2,} end logs ={2,}|$)",
    re.IGNORECASE | re.DOTALL,
)
_GOOGLE_PROFILE_RE = re.compile(r"\bgoogle_profile\b", re.IGNORECASE)


def write_jsonl(
    path: Path,
    records: Iterable[Mapping[str, object]],
    *,
    extra_secrets: Iterable[str] = (),
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            safe_record = sanitize_event(record, extra_secrets=extra_secrets)
            handle.write(json.dumps(safe_record, sort_keys=True, ensure_ascii=True))
            handle.write("\n")


def write_summary(
    path: Path,
    summary: Mapping[str, object],
    *,
    extra_secrets: Iterable[str] = (),
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_summary = sanitize_summary(summary, extra_secrets=extra_secrets)
    path.write_text(
        json.dumps(safe_summary, sort_keys=True, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def sanitize_event(
    record: Mapping[str, object],
    *,
    extra_secrets: Iterable[str] = (),
) -> dict[str, object]:
    return _sanitize_mapping(record, ALLOWED_EVENT_FIELDS, extra_secrets)


def sanitize_summary(
    summary: Mapping[str, object],
    *,
    extra_secrets: Iterable[str] = (),
) -> dict[str, object]:
    return _sanitize_mapping(summary, ALLOWED_SUMMARY_FIELDS, extra_secrets)


def redact_text(value: str, *, extra_secrets: Iterable[str] = ()) -> str:
    redacted = value
    for secret in extra_secrets:
        if secret and len(secret) >= 3:
            redacted = redacted.replace(secret, "[redacted]")
    redacted = _CHROMIUM_LOG_BLOCK_RE.sub("[redacted]", redacted)
    redacted = _APPDATA_LINE_RE.sub("[redacted]", redacted)
    redacted = _WINDOWS_USER_PATH_RE.sub("[redacted]", redacted)
    redacted = _WINDOWS_PATH_RE.sub("[redacted]", redacted)
    redacted = _GOOGLE_PROFILE_RE.sub("[redacted]", redacted)
    redacted = _BEARER_RE.sub("[redacted]", redacted)
    redacted = _KEY_VALUE_RE.sub("[redacted]", redacted)
    redacted = _EMAIL_RE.sub("[redacted]", redacted)
    redacted = _URL_RE.sub("[redacted]", redacted)
    return redacted


def _sanitize_mapping(
    mapping: Mapping[str, object],
    allowed_fields: frozenset[str],
    extra_secrets: Iterable[str],
) -> dict[str, object]:
    return {
        key: _sanitize_value(value, extra_secrets)
        for key, value in mapping.items()
        if key in allowed_fields
    }


def _sanitize_value(value: object, extra_secrets: Iterable[str]) -> object:
    if isinstance(value, str):
        return redact_text(value, extra_secrets=extra_secrets)
    if isinstance(value, list):
        return [_sanitize_value(item, extra_secrets) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_value(item, extra_secrets) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _sanitize_value(item, extra_secrets)
            for key, item in value.items()
            if isinstance(key, str)
        }
    return value
