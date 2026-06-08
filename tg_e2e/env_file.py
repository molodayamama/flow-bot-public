from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping

from .config import ConfigError


_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_env_file(path: str | Path, *, base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    raw_path = Path(path)
    if _has_parent_reference(raw_path) or str(path).strip() in {"", "."}:
        raise ConfigError("unsafe env_file")
    if not raw_path.exists():
        return env
    if not raw_path.is_file():
        raise ConfigError("env_file must be a file")

    try:
        lines = raw_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigError("env_file could not be read") from exc

    file_values = _parse_env_lines(lines)
    for key, value in file_values.items():
        env.setdefault(key, value)
    return env


def _parse_env_lines(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].lstrip()
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not _KEY_RE.match(key):
            continue
        values[key] = _parse_value(value)
    return values


def _parse_value(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        body = stripped[1:-1]
        if stripped[0] == '"':
            return (
                body.replace("\\n", "\n")
                .replace("\\r", "\r")
                .replace("\\t", "\t")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
            )
        return body
    return _strip_inline_comment(stripped).strip()


def _strip_inline_comment(value: str) -> str:
    for index, char in enumerate(value):
        if char == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index]
    return value


def _has_parent_reference(path: Path) -> bool:
    return any(part == ".." for part in path.parts)
