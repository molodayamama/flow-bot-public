"""Fail CI on tracked runtime artifacts or high-confidence secret patterns.

Only file paths are reported; matching content is never printed.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


FORBIDDEN_PATHS = (
    re.compile(r"(^|/)\.env(?:_flow)?$"),
    re.compile(r"(^|/)api_config\.json$"),
    re.compile(r"(^|/)labs\.google\.har$"),
    re.compile(r"(^|/)google_profile(?:_|/|$)"),
    re.compile(r"(^|/)proxylist.*\.txt$"),
    re.compile(r"(^|/)tools/.*_capture\.json$"),
)
SECRET_PATTERNS = (
    re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b"),
)
SOURCE_ONLY_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:https?|socks5h?)://[^\s/:]+:[^\s/@]+@"),
)
TEXT_SUFFIXES = {".py", ".js", ".json", ".md", ".yml", ".yaml", ".toml", ".txt"}


def tracked_paths() -> list[Path]:
    output = subprocess.check_output(["git", "ls-files", "-z"])
    return [Path(raw.decode("utf-8")) for raw in output.split(b"\0") if raw]


def audit(paths: list[Path]) -> list[str]:
    failures: list[str] = []
    for path in paths:
        normalized = path.as_posix()
        if any(pattern.search(normalized) for pattern in FORBIDDEN_PATHS):
            failures.append(f"forbidden tracked path: {normalized}")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES or normalized.startswith("tests/"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        patterns = SECRET_PATTERNS
        if path.suffix.lower() != ".md":
            patterns += SOURCE_ONLY_SECRET_PATTERNS
        if any(pattern.search(text) for pattern in patterns):
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
