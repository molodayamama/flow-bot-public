"""Atomic JSON override store for runtime bot config.

Stores only the *override* values; code defaults are the fallback.
Hot-reload via mtime-check on every read — no threads, no watchers,
no restart needed. Thread-safe via a module-level lock.

Sections:
  messages  — flow_copy.MESSAGES overrides
  labels    — flow_copy.LABELS overrides
  prices    — flow_core price constant overrides (key = constant name)
  settings  — starter_credits, cooldown_sec, max_failures
  flags     — feature toggles (upload_video_edit, etc.)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading

log = logging.getLogger("flow.config_store")

_PATH = os.getenv("CONFIG_OVERRIDE_FILE", "config_override.json")
_LOCK = threading.Lock()
_cache: dict | None = None
_mtime: float = -1.0


# ── internal ───────────────────────────────────────────────────────────

def _load() -> dict:
    """Return cached config, reloading from disk if mtime changed."""
    global _cache, _mtime
    try:
        st = os.stat(_PATH)
        with _LOCK:
            if _cache is not None and st.st_mtime == _mtime:
                return _cache
        with open(_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
        with _LOCK:
            _cache = data
            _mtime = st.st_mtime
        return data
    except FileNotFoundError:
        with _LOCK:
            _cache = {}
            _mtime = -1.0
        return {}
    except Exception:
        log.warning("config_store: failed to load %s", _PATH, exc_info=True)
        with _LOCK:
            return _cache or {}


def _atomic_write(data: dict) -> None:
    """Write data to _PATH atomically via tempfile + os.replace."""
    dir_ = os.path.dirname(os.path.abspath(_PATH)) or "."
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── public readers ─────────────────────────────────────────────────────

def get_section(name: str) -> dict:
    """Return an override section dict; empty dict if absent."""
    return _load().get(name) or {}


def get_message(key: str) -> str | None:
    """Return override message text or None if not overridden."""
    return _load().get("messages", {}).get(key)


def get_label(key: str) -> str | None:
    """Return override button label or None if not overridden."""
    return _load().get("labels", {}).get(key)


def get_price(key: str, default: int) -> int:
    """Return override price (int credits) or default."""
    v = _load().get("prices", {}).get(key)
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def get_flag(key: str, default: bool = False) -> bool:
    """Return override feature flag or default."""
    v = _load().get("flags", {}).get(key)
    return bool(v) if v is not None else default


def get_settings() -> dict:
    """Return the settings section (starter_credits, cooldown_sec, etc.)."""
    return _load().get("settings", {})


# ── public writer ──────────────────────────────────────────────────────

def set_section(name: str, data: dict) -> None:
    """Atomically merge ``data`` into the named section and save to disk."""
    global _cache, _mtime
    try:
        current = dict(_load())
        current[name] = data
        current["version"] = current.get("version", 0) + 1
        _atomic_write(current)
        try:
            new_mtime = os.stat(_PATH).st_mtime
        except OSError:
            new_mtime = -1.0
        with _LOCK:
            _cache = current
            _mtime = new_mtime
        log.info("config_store: section %r saved (%d keys)", name, len(data))
    except Exception:
        log.warning("config_store: failed to set section %r", name, exc_info=True)
        raise
