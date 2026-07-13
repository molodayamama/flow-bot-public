"""Offline, values-free production configuration validation."""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import ssl
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlparse

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from channels.max.client import max_config_from_env, validate_max_config
from accounts.pool import parse_flow_accounts


_TRUE = {"1", "true", "yes", "on"}
_TG_TOKEN = re.compile(r"^[0-9]+:[A-Za-z0-9_-]{3,}$")
_MAX_RUSSIAN_TLS_HOSTS = {"platform-api.max.ru", "platform-api2.max.ru"}
# SHA-256 of the DER-encoded Russian Trusted Root CA published by Gosuslugi.
# Keep this as a set so an announced root rotation can overlap safely.
_MAX_TRUSTED_CA_SHA256 = {
    "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31",
}


@dataclass(frozen=True)
class PreflightReport:
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


def _enabled(env: Mapping[str, str], name: str) -> bool:
    return str(env.get(name, "") or "").strip().lower() in _TRUE


def _require(env: Mapping[str, str], names: Sequence[str], errors: list[str]) -> None:
    for name in names:
        if not str(env.get(name, "") or "").strip():
            errors.append(f"{name} is required")


def _valid_id_list(raw: str) -> bool:
    parts = [part.strip() for part in str(raw or "").split(",") if part.strip()]
    return bool(parts) and all(part.isdigit() and int(part) > 0 for part in parts)


def _profile_paths(env: Mapping[str, str]) -> list[tuple[str, str]]:
    configured = str(env.get("FLOW_ACCOUNTS", "") or "").strip()
    default_dir = str(env.get("USER_DATA_DIR", "google_profile"))
    accounts = parse_flow_accounts(
        configured,
        default_id=str(env.get("FLOW_ACCOUNT_ID", "main") or "main"),
        default_dir=default_dir,
    )
    label = "FLOW_ACCOUNTS" if configured else "USER_DATA_DIR"
    return [(f"{label} account {account.id}", account.profile_dir) for account in accounts]


def _robokassa_enabled(env: Mapping[str, str]) -> bool:
    raw = str(env.get("ROBOKASSA_ENABLED", "") or "").strip()
    if raw:
        return raw.lower() in _TRUE
    return all(
        str(env.get(name, "") or "").strip()
        for name in (
            "ROBOKASSA_MERCHANT_LOGIN",
            "ROBOKASSA_PASSWORD1",
            "ROBOKASSA_PASSWORD2",
        )
    )


def _resolved(root: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else root / path


def _max_tls_trust_error(config) -> str | None:
    """Validate MAX TLS trust offline, without opening a provider connection."""
    try:
        context = ssl.create_default_context(cafile=config.ca_bundle or None)
    except (OSError, ssl.SSLError, ValueError):
        return "MAX_CA_BUNDLE is not a valid CA bundle"

    hostname = (urlparse(config.api_base_url).hostname or "").lower()
    if hostname not in _MAX_RUSSIAN_TLS_HOSTS:
        return None

    fingerprints = {
        hashlib.sha256(cert).hexdigest()
        for cert in context.get_ca_certs(binary_form=True)
    }
    if fingerprints.isdisjoint(_MAX_TRUSTED_CA_SHA256):
        return (
            "MAX TLS trust is missing: configure MAX_CA_BUNDLE with the "
            "Russian Trusted Root CA or install it system-wide"
        )
    return None


def validate_environment(
    env: Mapping[str, str],
    *,
    root: str | Path = ".",
    production: bool = True,
    env_file: str | Path | None = None,
) -> PreflightReport:
    """Validate configuration without printing or contacting external services."""
    root_path = Path(root).resolve()
    source = {str(key): str(value or "") for key, value in env.items()}
    errors: list[str] = []
    warnings: list[str] = []

    bot_mode = source.get("BOT_MODE", "consumer").strip().lower() or "consumer"
    if bot_mode not in {"consumer", "seller"}:
        errors.append("BOT_MODE must be consumer or seller")

    token = source.get("TELEGRAM_TOKEN", "").strip()
    if not _TG_TOKEN.fullmatch(token):
        errors.append("TELEGRAM_TOKEN is missing or malformed")
    owner = source.get("OWNER_ID", "")
    admins = source.get("ADMIN_IDS", "")
    if not (_valid_id_list(owner) or _valid_id_list(admins)):
        errors.append("OWNER_ID or ADMIN_IDS must contain a positive numeric id")

    if bot_mode != "seller":
        _require(source, ("FLOW_BROWSER_API_KEY",), errors)
        for label, raw_path in _profile_paths(source):
            if not raw_path:
                errors.append(f"{label} has no profile path")
            elif not _resolved(root_path, raw_path).is_dir():
                errors.append(f"{label} profile directory does not exist")

    robokassa_enabled = _robokassa_enabled(source)
    if robokassa_enabled:
        _require(
            source,
            (
                "ROBOKASSA_MERCHANT_LOGIN",
                "ROBOKASSA_PASSWORD1",
                "ROBOKASSA_PASSWORD2",
                "ROBOKASSA_PUBLIC_BASE_URL",
            ),
            errors,
        )
        public_raw = source.get("ROBOKASSA_PUBLIC_BASE_URL", "").strip()
        public_url = urlparse(public_raw)
        if public_raw and (public_url.scheme != "https" or not public_url.hostname):
            errors.append("ROBOKASSA_PUBLIC_BASE_URL must be an HTTPS URL")
        default_scope = "seller" if bot_mode == "seller" else "consumer"
        if source.get("ROBOKASSA_SCOPE", default_scope).strip() not in {
            "consumer",
            "seller",
        }:
            errors.append("ROBOKASSA_SCOPE must be consumer or seller")

    web_app_enabled = _enabled(source, "WEB_APP_ENABLED")
    if web_app_enabled:
        if bot_mode == "seller":
            errors.append("WEB_APP_ENABLED is supported only by the consumer process")
        if not robokassa_enabled:
            errors.append("WEB app production requires ROBOKASSA_ENABLED=1 for top-up")
        if len(source.get("WEB_SESSION_SECRET", "")) < 32:
            errors.append("WEB_SESSION_SECRET must contain at least 32 characters")
        origin_raw = source.get("WEB_PUBLIC_ORIGIN", "https://photozhab.ru").strip()
        origin = urlparse(origin_raw)
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.path not in {"", "/"}
            or origin.params
            or origin.query
            or origin.fragment
            or origin.username
            or origin.password
        ):
            errors.append("WEB_PUBLIC_ORIGIN must be an HTTPS origin without a path")
        try:
            web_starter = int(source.get("WEB_STARTER_CREDITS", "0"))
        except ValueError:
            web_starter = -1
        if production and web_starter != 0:
            errors.append("WEB_STARTER_CREDITS must be 0 in production")
        if production and source.get("WEB_COOKIE_SECURE", "1").strip().lower() in {
            "0", "false", "no", "off",
        }:
            errors.append("WEB_COOKIE_SECURE must be enabled in production")
        yandex_client_id = source.get("WEB_YANDEX_CLIENT_ID", "").strip()
        yandex_client_secret = source.get("WEB_YANDEX_CLIENT_SECRET", "").strip()
        if bool(yandex_client_id) != bool(yandex_client_secret):
            errors.append(
                "WEB_YANDEX_CLIENT_ID and WEB_YANDEX_CLIENT_SECRET must be set together"
            )
        max_mini_raw = source.get(
            "WEB_MAX_MINI_APP_URL", "https://max.ru/se13461237_bot?startapp=web"
        ).strip()
        max_mini_url = urlparse(max_mini_raw)
        if max_mini_url.scheme != "https" or max_mini_url.hostname != "max.ru":
            errors.append("WEB_MAX_MINI_APP_URL must use https://max.ru")
        media_parent = _resolved(root_path, source.get("WEB_MEDIA_DIR", "/tmp/photozhab-web-media")).parent
        if not media_parent.is_dir() or not os.access(media_parent, os.W_OK):
            errors.append("WEB_MEDIA_DIR parent directory must exist and be writable")

    max_enabled = _enabled(source, "MAX_ENABLED")
    if max_enabled:
        if not robokassa_enabled:
            errors.append("MAX production requires ROBOKASSA_ENABLED=1 for top-up")
        max_config = max_config_from_env(source)
        try:
            validate_max_config(max_config, production=production)
        except ValueError as exc:
            errors.extend(part.strip() for part in str(exc).split(";") if part.strip())
        else:
            tls_error = _max_tls_trust_error(max_config)
            if tls_error:
                errors.append(tls_error)

    for name, default in (
        ("METRICS_DB", "metrics.db"),
        ("MAX_INBOX_DB", "max_webhook_inbox.db"),
        ("USER_PROJECTS_FILE", "user_projects.json"),
        ("USER_CREDITS_FILE", "user_credits.json"),
        ("PAYMENTS_FILE", "payments.json"),
    ):
        if name == "MAX_INBOX_DB" and not max_enabled:
            continue
        parent = _resolved(root_path, source.get(name, default)).parent
        if not parent.is_dir():
            errors.append(f"{name} parent directory does not exist")
        elif not os.access(parent, os.W_OK):
            errors.append(f"{name} parent directory is not writable")

    if env_file is not None and os.name == "posix":
        path = Path(env_file)
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:
                errors.append("ENV_FILE permissions must be 0600 or stricter")
        except OSError:
            errors.append("ENV_FILE is not readable")

    if not _enabled(source, "CREDITS_SQLITE"):
        message = "CREDITS_SQLITE=1 is required for atomic production credits"
        if production:
            errors.append(message)
        else:
            warnings.append(message)
    return PreflightReport(tuple(dict.fromkeys(errors)), tuple(dict.fromkeys(warnings)))


def load_environment(path: str | Path) -> dict[str, str]:
    values = {key: str(value or "") for key, value in dotenv_values(path).items()}
    values.update({key: value for key, value in os.environ.items()})
    return values


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=os.getenv("ENV_FILE", ".env"))
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    report = validate_environment(
        load_environment(args.env_file),
        root=args.root,
        production=True,
        env_file=args.env_file,
    )
    for message in report.errors:
        print(f"ERROR: {message}")
    for message in report.warnings:
        print(f"WARNING: {message}")
    if report.ok:
        print("production preflight passed")
        return 0
    print(f"production preflight failed: {len(report.errors)} issue(s)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
