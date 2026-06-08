from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import parse_qs, urlparse


class ConfigError(ValueError):
    """Raised when Telegram E2E configuration is unsafe or invalid."""


@dataclass(frozen=True)
class TelegramE2EConfig:
    mode: str
    output_dir: Path
    project_root: Path
    timeout_sec: int
    approve_external_action: bool = False
    bot_username: str | None = None
    tg_api_id: int | None = None
    tg_api_hash: str | None = None
    tg_phone: str | None = None
    session_file: Path | None = None
    mtproxy: TelegramMTProxy | None = None
    tg_proxy_url: str | None = None
    prompt: str | None = None
    max_steps: int = 1
    delay_sec: float = 90.0


@dataclass(frozen=True)
class TelegramMTProxy:
    host: str
    port: int
    secret: str


EXTERNAL_MODES = frozenset({"telegram-login", "telegram-smoke", "telegram-generation", "telegram-ramp"})
SUPPORTED_MODES = frozenset({"dry-run", *EXTERNAL_MODES})


def build_config(
    *,
    mode: str,
    output_dir: str | Path = "tg_e2e_runs",
    project_root: str | Path | None = None,
    timeout_sec: int = 90,
    approve_external_action: bool = False,
    bot_username: str | None = None,
    tg_api_id: int | str | None = None,
    tg_api_hash: str | None = None,
    tg_phone: str | None = None,
    session_file: str | Path | None = None,
    tg_proxy_url: str | None = None,
    prompt: str | None = None,
    max_steps: int = 1,
    delay_sec: float = 90.0,
    env: Mapping[str, str] | None = None,
) -> TelegramE2EConfig:
    root = Path(project_root or Path.cwd()).resolve()
    actual_env = os.environ if env is None else env
    normalized_mode = (mode or "").strip().lower()
    if normalized_mode not in SUPPORTED_MODES:
        raise ConfigError("unsupported mode")

    out_dir = _safe_output_dir(output_dir, root)
    actual_session_file = _value(session_file, actual_env, "TG_E2E_SESSION_FILE") or ".sessions/tg_e2e"
    resolved_session_file = _safe_session_file(actual_session_file, root)
    actual_bot_username = _value(bot_username, actual_env, "BOT_USERNAME")
    actual_hash = _first_value(tg_api_hash, actual_env, ("TG_API_HASH", "TELEGRAM_API_HASH"))
    actual_phone = _first_value(tg_phone, actual_env, ("TG_PHONE", "TELEGRAM_PHONE"))
    actual_api_id = _parse_api_id(_first_value(tg_api_id, actual_env, ("TG_API_ID", "TELEGRAM_API_ID")))
    actual_proxy_url = _proxy_value(tg_proxy_url, actual_env)
    actual_mtproxy = _parse_mtproxy_url(actual_proxy_url) if actual_proxy_url else None
    actual_prompt = prompt.strip() if prompt is not None else None

    if timeout_sec < 10 or timeout_sec > 600:
        raise ConfigError("timeout_sec must be between 10 and 600")
    if max_steps < 1 or max_steps > 5:
        raise ConfigError("max_steps must be between 1 and 5")
    if delay_sec < 0:
        raise ConfigError("delay_sec must not be negative")

    if normalized_mode == "dry-run":
        if approve_external_action:
            raise ConfigError("dry-run rejects approve_external_action")
        if session_file is not None:
            raise ConfigError("dry-run rejects session_file")
        return TelegramE2EConfig(
            mode=normalized_mode,
            output_dir=out_dir,
            project_root=root,
            timeout_sec=timeout_sec,
            prompt=actual_prompt,
            max_steps=max_steps,
            delay_sec=delay_sec,
        )

    if not approve_external_action:
        raise ConfigError("external Telegram modes require approve_external_action")
    if normalized_mode != "telegram-login" and not actual_bot_username:
        raise ConfigError("external Telegram modes require BOT_USERNAME")
    if actual_api_id is None:
        raise ConfigError("external Telegram modes require TG_API_ID")
    if not actual_hash:
        raise ConfigError("external Telegram modes require TG_API_HASH")
    if not actual_phone:
        raise ConfigError("external Telegram modes require TG_PHONE")

    if normalized_mode == "telegram-login":
        if prompt is not None:
            raise ConfigError("telegram-login rejects prompt")
        if max_steps != 1:
            raise ConfigError("telegram-login requires max_steps=1")
    elif normalized_mode == "telegram-smoke":
        if prompt is not None:
            raise ConfigError("telegram-smoke rejects prompt")
        if max_steps != 1:
            raise ConfigError("telegram-smoke requires max_steps=1")
    else:
        if actual_prompt is None or len(actual_prompt) < 3:
            raise ConfigError("generation modes require a prompt with at least 3 characters")
        if normalized_mode == "telegram-generation" and max_steps != 1:
            raise ConfigError("telegram-generation requires max_steps=1")
        if normalized_mode == "telegram-ramp" and delay_sec < 17:
            raise ConfigError("telegram-ramp requires delay_sec >= 17")

    return TelegramE2EConfig(
        mode=normalized_mode,
        output_dir=out_dir,
        project_root=root,
        timeout_sec=timeout_sec,
        approve_external_action=True,
        bot_username=actual_bot_username,
        tg_api_id=actual_api_id,
        tg_api_hash=actual_hash,
        tg_phone=actual_phone,
        session_file=resolved_session_file,
        mtproxy=actual_mtproxy,
        tg_proxy_url=actual_proxy_url,
        prompt=actual_prompt,
        max_steps=max_steps,
        delay_sec=delay_sec,
    )


def _value(value: object | None, env: Mapping[str, str], name: str) -> str | None:
    if value is not None:
        return str(value).strip()
    from_env = env.get(name)
    return from_env.strip() if from_env else None


def _first_value(value: object | None, env: Mapping[str, str], names: tuple[str, ...]) -> str | None:
    if value is not None:
        return str(value).strip()
    for name in names:
        from_env = env.get(name)
        if from_env and from_env.strip():
            return from_env.strip()
    return None


def _parse_api_id(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError("TG_API_ID must be an integer") from exc
    if parsed <= 0:
        raise ConfigError("TG_API_ID must be positive")
    return parsed


def _proxy_value(value: object | None, env: Mapping[str, str]) -> str | None:
    explicit = _value(value, env, "TG_E2E_PROXY_URL")
    if explicit:
        return explicit
    shared = _value(None, env, "TG_PROXY_URL")
    if shared and shared.lower().startswith("tg://proxy"):
        return shared
    return None


def _parse_mtproxy_url(value: str) -> TelegramMTProxy:
    parsed = urlparse(value.strip())
    if parsed.scheme.lower() != "tg" or parsed.netloc.lower() != "proxy":
        raise ConfigError("Telegram E2E proxy must use tg://proxy URL")
    query = parse_qs(parsed.query, keep_blank_values=False)
    host = _single_query_value(query, "server")
    port_raw = _single_query_value(query, "port")
    secret = _single_query_value(query, "secret")
    if not host or any(char.isspace() for char in host):
        raise ConfigError("Telegram E2E proxy server is invalid")
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ConfigError("Telegram E2E proxy port must be an integer") from exc
    if port < 1 or port > 65535:
        raise ConfigError("Telegram E2E proxy port is out of range")
    if not _is_valid_mtproxy_secret(secret):
        raise ConfigError("Telegram E2E proxy secret is invalid")
    return TelegramMTProxy(host=host, port=port, secret=secret)


def _single_query_value(query: Mapping[str, list[str]], name: str) -> str:
    values = query.get(name) or []
    if len(values) != 1 or not values[0].strip():
        raise ConfigError(f"Telegram E2E proxy requires {name}")
    return values[0].strip()


def _is_valid_mtproxy_secret(value: str) -> bool:
    if len(value) < 32 or len(value) > 256:
        return False
    return all(char in "0123456789abcdefABCDEF" for char in value)


def _safe_output_dir(value: str | Path, project_root: Path) -> Path:
    raw = Path(value)
    if _has_parent_reference(raw) or str(value).strip() in {"", "."}:
        raise ConfigError("unsafe output_dir")
    resolved = (project_root / raw if not raw.is_absolute() else raw).resolve()
    _ensure_inside_project(resolved, project_root, "unsafe output_dir")
    if resolved == project_root or _has_unsafe_part(resolved):
        raise ConfigError("unsafe output_dir")
    return resolved


def _safe_session_file(value: str | Path, project_root: Path) -> Path:
    raw = Path(value)
    if _has_parent_reference(raw) or str(value).strip() in {"", "."}:
        raise ConfigError("unsafe session_file")
    resolved = (project_root / raw if not raw.is_absolute() else raw).resolve()
    _ensure_inside_project(resolved, project_root, "unsafe session_file")
    if resolved == project_root or _has_unsafe_part(resolved) or _has_unsafe_suffix(resolved):
        raise ConfigError("unsafe session_file")
    return resolved


def _ensure_inside_project(path: Path, project_root: Path, message: str) -> None:
    try:
        path.relative_to(project_root)
    except ValueError as exc:
        raise ConfigError(message) from exc


def _has_parent_reference(path: Path) -> bool:
    return any(part == ".." for part in path.parts)


def _has_unsafe_suffix(path: Path) -> bool:
    return path.suffix.lower() in {".har", ".json", ".env"}


def _has_unsafe_part(path: Path) -> bool:
    unsafe = {
        ".git",
        "." + "env",
        "." + "env_flow",
        "google" + "_profile",
        "api" + "_config.json",
        "proxylist.txt",
    }
    return any(part.lower() in unsafe for part in path.parts)


def validate_no_dangerous_flags(flag_names: Sequence[str]) -> None:
    blocked = {
        "captcha",
        "proxy",
        "google-profile",
        "browser",
        "har",
        "admin",
        "server-command",
        "ignore-warning",
        "continue-warning",
    }
    for flag in flag_names:
        normalized = flag.lstrip("-").replace("_", "-").lower()
        if normalized in blocked:
            raise ConfigError("unsupported flag")
