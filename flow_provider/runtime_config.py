"""Flow provider runtime configuration and browser/proxy helpers.

Extracted verbatim from ``flow_bot.py`` (PR-2a) with no behavior change. Owns the
env-derived constants and helper functions the Flow provider client needs. This
module is imported before ``flow_bot`` finishes loading, so it loads dotenv
itself to keep constant values identical to the monolith.
"""
from __future__ import annotations

import logging
import os
import re
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv

ENV_FILE = os.getenv("ENV_FILE", ".env") or ".env"
load_dotenv(ENV_FILE)

# Logger name kept as "flow_bot" so records are indistinguishable from before
# (the log format does not print the logger name anyway).
log = logging.getLogger("flow_bot")


USER_DATA_DIR = os.getenv("USER_DATA_DIR", "./google_profile")
PROXY_URL = os.getenv("PROXY_URL", "")          # общий прокси по умолчанию (http/socks5)
BROWSER_PROXY_URL = os.getenv("BROWSER_PROXY_URL")
API_PROXY_URL = os.getenv("API_PROXY_URL")
CAPMONSTER_PROXY_URL = os.getenv("CAPMONSTER_PROXY_URL")
TWOCAPTCHA_KEY = os.getenv("TWOCAPTCHA_KEY", "")
CAPMONSTER_KEY = os.getenv("CAPMONSTER_KEY", "")
CAPTCHA_PROVIDER = os.getenv("CAPTCHA_PROVIDER", "auto").strip().lower()
TWOCAPTCHA_SCORE = os.getenv("TWOCAPTCHA_SCORE", "0.3").strip()
try:
    TWOCAPTCHA_SOLVE_TIMEOUT = int(os.getenv("TWOCAPTCHA_SOLVE_TIMEOUT", "150"))
except ValueError:
    TWOCAPTCHA_SOLVE_TIMEOUT = 150
try:
    CAPMONSTER_SOLVE_TIMEOUT = int(os.getenv("CAPMONSTER_SOLVE_TIMEOUT", "120"))
except ValueError:
    CAPMONSTER_SOLVE_TIMEOUT = 120
FLOW_URL = "https://labs.google/fx/tools/flow"
try:
    IDLE_PARK_SEC = float(os.getenv("IDLE_PARK_SEC", "90"))
except (TypeError, ValueError):
    IDLE_PARK_SEC = 90.0
try:
    PARK_CHECK_SEC = float(os.getenv("PARK_CHECK_SEC", "20"))
except (TypeError, ValueError):
    PARK_CHECK_SEC = 20.0
FLOW_PROJECT_CTA_RE = re.compile(r"^\s*(new project|create project|new flow)\s*$", re.I)
FLOW_NEW_PROJECT_RE = re.compile(r"^\s*new project\s*$", re.I)
def _flow_project_cta_candidates(page):
    # Prefer exact Flow project CTAs. Plain "Create" is too broad and can match
    # Google account UI such as "Create account" after redirects.
    return [
        ("new_project_button", lambda: page.get_by_role("button", name=FLOW_NEW_PROJECT_RE)),
        ("new_project_link", lambda: page.get_by_role("link", name=FLOW_NEW_PROJECT_RE)),
        ("new_project_text", lambda: page.get_by_text(FLOW_NEW_PROJECT_RE)),
        ("project_cta_button", lambda: page.get_by_role("button", name=FLOW_PROJECT_CTA_RE)),
        ("project_cta_link", lambda: page.get_by_role("link", name=FLOW_PROJECT_CTA_RE)),
        ("project_cta_text", lambda: page.get_by_text(FLOW_PROJECT_CTA_RE)),
        ("new_project_aria", lambda: page.locator('[aria-label*="new project" i]')),
        ("create_project_aria", lambda: page.locator('[aria-label*="create project" i]')),
        ("new_project_button_text", lambda: page.locator('button:has-text("New project")')),
        ("create_project_button_text", lambda: page.locator('button:has-text("Create project")')),
        ("new_flow_button_text", lambda: page.locator('button:has-text("New flow")')),
        ("new_icon_button", lambda: page.locator('button[aria-label*="new" i]')),
        ("plus_button", lambda: page.locator('button:has-text("+")')),
    ]
def _video_failure_reason(item) -> str:
    """Классифицировать FAILED-итем поллинга видео в причину для пользователя.

    Из live-захвата: ``PUBLIC_ERROR_AUDIO_FILTERED`` — модель не смогла сделать
    звук (видео заблокировано, помогает повтор с новым seed); ``DANGER_FILTER`` /
    ``PROHIBITED_INPUT`` — модерация промпта/картинки.
    """
    try:
        import json as _j
        blob = _j.dumps(item, ensure_ascii=False)
    except Exception:
        blob = str(item)
    if "PUBLIC_ERROR_AUDIO_FILTERED" in blob:
        return "audio_filtered"
    if "PUBLIC_ERROR_DANGER_FILTER" in blob or "PROHIBITED_INPUT" in blob:
        return "danger_filter"
    return ""
EDIT_CAPTURE_FILE = os.getenv("EDIT_CAPTURE_FILE", "edit_capture.json")
EDIT_CAPTURE_RAW_FILE = os.getenv("EDIT_CAPTURE_RAW_FILE", "edit_capture_raw.json")
RECENT_IMAGES_FILE = os.getenv("RECENT_IMAGES_FILE", "recent_images.json")
UPLOAD_CAPTURE_FILE = os.getenv("UPLOAD_CAPTURE_FILE", "upload_capture.json")
UPSCALE_CAPTURE_FILE = os.getenv("UPSCALE_CAPTURE_FILE", "upscale_capture.json")
TOKEN_TTL_SEC = 50 * 60  # обновлять Bearer каждые 50 минут
GCREDITS_CACHE_SEC = max(60, int(os.getenv("GCREDITS_CACHE_SEC", "1800")))
GCREDITS_SESSION_SNAPSHOT_TIMEOUT_SEC = 1.5
def _normalize_proxy_url(proxy_url: str) -> str:
    proxy_url = (proxy_url or "").strip()
    if proxy_url and "://" not in proxy_url:
        return "http://" + proxy_url
    return proxy_url
def _proxy_is_disabled(proxy_url: str | None) -> bool:
    return (proxy_url or "").strip().lower() in ("0", "false", "no", "off", "none", "direct")
def _effective_proxy_url(proxy_url: str | None, fallback: str = PROXY_URL) -> str:
    if proxy_url is None:
        proxy_url = fallback
    if _proxy_is_disabled(proxy_url):
        return ""
    return _normalize_proxy_url(proxy_url)
def _proxy_host_only(url: str | None) -> str:
    """host:port прокси без логина/пароля (для диагностики, безопасно отдавать)."""
    if not url:
        return "(none)"
    low = url.strip().lower()
    if low in ("off", "none", "direct", ""):
        return low or "(none)"
    try:
        from urllib.parse import urlparse
        p = urlparse(url if "://" in url else "http://" + url)
        host = p.hostname or "?"
        return f"{p.scheme or 'http'}://{host}:{p.port}" if p.port else f"{p.scheme or 'http'}://{host}"
    except Exception:
        return "(set)"
def _browser_fetch_headers(headers: dict) -> dict:
    """Headers that browser fetch may safely set from page JS."""
    out = {"Content-Type": "text/plain;charset=UTF-8", "Accept": "*/*"}
    for key, value in (headers or {}).items():
        if str(key).lower() == "authorization" and value:
            out["Authorization"] = value
    return out
def _playwright_proxy_config(proxy_url: str) -> dict | None:
    """Convert PROXY_URL into Playwright's split proxy auth shape."""
    proxy_url = _effective_proxy_url(proxy_url, fallback="")
    if not proxy_url:
        return None
    try:
        p = urlparse(proxy_url)
        scheme = p.scheme.lower()
        if scheme not in ("http", "https", "socks4", "socks5"):
            return None
        if not p.hostname:
            return None
        server = f"{scheme}://{p.hostname}"
        if p.port:
            server += f":{p.port}"
        config: dict = {"server": server}
        if p.username:
            config["username"] = unquote(p.username)
        if p.password:
            config["password"] = unquote(p.password)
        return config
    except Exception:
        return None
def _host_path(url: str) -> str:
    """host + path of a URL (no query/secrets) — safe to log for diagnostics."""
    try:
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        return f"{parts.netloc}{parts.path}"
    except Exception:
        return "?"
