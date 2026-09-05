"""Fail CI on tracked runtime artifacts or high-confidence secret patterns.

Only file paths are reported; matching content is never printed.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


FORBIDDEN_PATHS = (
    re.compile(r"(^|/)(?:\.env[^/]*|[^/]+\.env)$"),
    re.compile(r"(^|/)api_config\.json$"),
    re.compile(r"(^|/)labs\.google\.har$"),
    re.compile(r"(^|/)google_profile(?:_|/|$)"),
    re.compile(r"(^|/)proxylist.*\.txt$"),
    re.compile(r"(^|/)tools/.*_capture\.json$"),
    re.compile(r"\.(?:db(?:-(?:wal|shm|journal))?|sqlite3?|har|session|bundle|pem|key|p12|pfx)$"),
    re.compile(r"(^|/)(?:tokens\.json|cookies\.pkl|account_metadata.*\.json|flow_accounts_state.*\.json|payments.*\.json|user_credits.*\.json|user_projects.*\.json)$"),
    re.compile(r"(^|/)(?:\.claude/agent-memory|chrome_profile|logs)(?:/|$)"),
)
SECRET_PATTERNS = (
    re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{30,}"),
    re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b[A-Za-z0-9]{16,}:[A-Za-z0-9]{16,}\b"),
    re.compile(r"(?i)\b[\w.+-]+@(?:gmail|googlemail)\.com\b"),
)
SOURCE_ONLY_SECRET_PATTERNS = (
    re.compile(r'''(?i)\b(?:https?|socks5h?)://[^\s/:'"<>\[\]]+:[^\s/@'"<>\[\]]+@'''),
    re.compile(
        r"(?ix)\b(?:TELEGRAM_TOKEN|MAX_BOT_TOKEN|TWOCAPTCHA(?:_API)?_KEY|"
        r"CAPMONSTER_KEY|ROBOKASSA_(?:TEST_)?PASSWORD[12]|INTERNAL_API_TOKEN|"
        r"TELEMETR_API_TOKEN|TGSTAT_API_TOKEN|MAX_WEBHOOK_SECRET|"
        r"COOKIE_(?:SID|HSID|SSID|SECURE_[13]PSID(?:TS)?))"
        r"\s*=\s*[\"'][^\"'\r\n]{8,}[\"']"
    ),
)
PLACEHOLDER_CREDENTIAL = re.compile(
    r"(?i)(?:replace[_-]|your[_-]|123456789:TEST|https?://user:pass@)"
)


def tracked_paths() -> list[Path]:
    output = subprocess.check_output(["git", "ls-files", "-z"])
    return [Path(raw.decode("utf-8")) for raw in output.split(b"\0") if raw]


def audit(paths: list[Path]) -> list[str]:
    failures: list[str] = []
    for path in paths:
        normalized = path.as_posix()
        is_env_template = path.name == ".env.example" or path.name.endswith(".env.example")
        if not is_env_template and any(pattern.search(normalized) for pattern in FORBIDDEN_PATHS):
            failures.append(f"forbidden tracked path: {normalized}")
            continue
        try:
            data = path.read_bytes()
        except OSError:
            failures.append(f"cannot read tracked path: {normalized}")
            continue
        # Scan ASCII secret signatures in every file, including assets and fixtures.
        text = data.decode("utf-8", errors="replace")
        high_confidence = any(pattern.search(text) for pattern in SECRET_PATTERNS)
        source_match = False
        if "tests" not in path.parts:
            source_match = any(
                not PLACEHOLDER_CREDENTIAL.search(match.group())
                for pattern in SOURCE_ONLY_SECRET_PATTERNS
                for match in pattern.finditer(text)
            )
        if high_confidence or source_match:
            failures.append(f"possible secret pattern: {normalized}")
    return failures


def main() -> int:
    failures = audit(tracked_paths())
    for failure in failures:
        print(failure)
    if failures:
        print(f"tracked secret audit failed: {len(failures)} file(s)")
        return 1
    print("tracked secret audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
