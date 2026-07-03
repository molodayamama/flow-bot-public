"""Pure media helpers (Phase 11 core split).

Dependency-free, platform-neutral utilities for outbound media handling, shared
by any channel (Telegram now, MAX later).
"""

from __future__ import annotations


def image_ext_from_bytes(data: bytes, fallback: str = "png") -> str:
    """Sniff an image extension from magic bytes (png/jpg/webp), else fallback."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    return fallback
