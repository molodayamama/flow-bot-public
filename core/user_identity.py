from __future__ import annotations

from dataclasses import dataclass


TELEGRAM_PLATFORM = "telegram"
MAX_PLATFORM = "max"


@dataclass(frozen=True)
class PlatformIdentity:
    platform: str
    platform_user_id: str


def normalize_platform(platform: str) -> str:
    return (platform or "").strip().lower()


def normalize_platform_user_id(platform_user_id: str | int) -> str:
    return str(platform_user_id).strip()


def platform_identity(platform: str, platform_user_id: str | int) -> PlatformIdentity:
    normalized_platform = normalize_platform(platform)
    normalized_user_id = normalize_platform_user_id(platform_user_id)
    if not normalized_platform:
        raise ValueError("platform is required")
    if not normalized_user_id:
        raise ValueError("platform_user_id is required")
    return PlatformIdentity(normalized_platform, normalized_user_id)


def telegram_legacy_internal_id(platform_user_id: str | int, legacy_user_id: int | None = None) -> int:
    value = legacy_user_id if legacy_user_id is not None else platform_user_id
    internal_id = int(value)
    if internal_id <= 0:
        raise ValueError("telegram legacy internal id must be positive")
    return internal_id


def uses_telegram_legacy_id(platform: str) -> bool:
    return normalize_platform(platform) == TELEGRAM_PLATFORM

