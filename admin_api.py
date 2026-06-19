"""Admin REST API — aiohttp routes registered into the existing web.Application.

All routes are under /api/admin/ and are already protected by nginx HTTP Basic
Auth (lo / password). The Python side requires no additional auth because the
server binds only to 127.0.0.1 and is unreachable from the internet directly.

Usage in flow_bot.py:
    import admin_api
    admin_api.register_admin_routes(app, account_pool)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import string
import time
from typing import TYPE_CHECKING

from aiohttp import web

import config_store
import metrics

if TYPE_CHECKING:
    from flow_core import AccountPool

log = logging.getLogger("flow.admin_api")

_pool: "AccountPool | None" = None
# Optional: account_id -> SessionKeeper, for live G-credits lookup. Injected
# by register_admin_routes(); None means /api/admin/accounts skips g_credits.
_keepers: dict | None = None
GCREDITS_LOOKUP_TIMEOUT_SEC = 3.0


# ── helpers ────────────────────────────────────────────────────────────

def _json(data: object, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data, ensure_ascii=False, default=str),
        status=status,
        content_type="application/json",
    )


async def _body(request: web.Request) -> dict | None:
    try:
        b = await request.json()
        return b if isinstance(b, dict) else None
    except Exception:
        return None


def _request_source(request: web.Request) -> str:
    user = (
        request.headers.get("X-Forwarded-User")
        or request.headers.get("X-Remote-User")
        or request.headers.get("Remote-User")
        or "unknown"
    )
    remote = request.headers.get("X-Forwarded-For") or request.remote or "unknown"
    return f"{user}@{str(remote).split(',')[0].strip()}"


def _short_value(value: object, limit: int = 1200) -> object:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return value
    return {"truncated": True, "preview": text[:limit]}


def _audit(request: web.Request, action: str, *, old: object = None, new: object = None, result: str = "ok") -> None:
    try:
        metrics.log_event(
            "admin_action",
            source=_request_source(request),
            payload={
                "action": action,
                "old": _short_value(old),
                "new": _short_value(new),
                "result": result,
            },
        )
    except Exception:
        log.warning("admin audit failed for %s", action, exc_info=True)


def _format_placeholders(text: str) -> set[str]:
    placeholders: set[str] = set()
    for _, field, _, _ in string.Formatter().parse(text):
        if not field:
            continue
        root = re.split(r"[.[]", field, maxsplit=1)[0]
        if root:
            placeholders.add(root)
    return placeholders


def _copy_group(key: str) -> str:
    if key.startswith("onboarding") or key.startswith("ob_") or key in {"welcome", "menu_title"}:
        return "onboarding"
    if key.startswith("vid_"):
        return "video generation"
    if key.startswith("referral") or key.startswith("invite"):
        return "referral"
    if key.startswith("support"):
        return "support"
    if key.startswith("gallery") or key.startswith("history"):
        return "gallery"
    if key.startswith("topup") or "balance" in key or "payment" in key or "pay_" in key:
        return "payments"
    if any(part in key for part in ("fail", "error", "unavailable", "rejected", "blocked", "limited")):
        return "errors"
    return "image generation"


_PRICE_TEXT_RE = re.compile(r"(^|[^\w])\d+\s*(?:кр|кредит(?:ов|а)?)([^\w]|$)", re.IGNORECASE)


# ── ping ───────────────────────────────────────────────────────────────

async def handle_ping(request: web.Request) -> web.Response:
    return _json({"ok": True, "ts": time.time()})


# ── stats ──────────────────────────────────────────────────────────────

async def handle_stats(request: web.Request) -> web.Response:
    return _json(metrics.report_admin_stats())


# ── log ────────────────────────────────────────────────────────────────

async def handle_log(request: web.Request) -> web.Response:
    try:
        limit = min(int(request.rel_url.query.get("limit", "50")), 200)
    except (TypeError, ValueError):
        limit = 50
    return _json(metrics.report_recent_events(limit))


async def handle_sellers_get(request: web.Request) -> web.Response:
    """Seller-segment rollup for the admin «Селлеры» panel (plan §6)."""
    try:
        limit = min(int(request.rel_url.query.get("limit", "100")), 1000)
    except (TypeError, ValueError):
        limit = 100
    return _json(metrics.report_sellers(limit))


# ── ops cockpit ───────────────────────────────────────────────────────

async def handle_ops_get(request: web.Request) -> web.Response:
    accounts = []
    if _pool is not None:
        accounts = _pool.status()
    active_accounts = [
        a for a in accounts
        if not a.get("disabled") and int(a.get("cooldown_left") or 0) <= 0
    ]
    cooldown_accounts = [
        a for a in accounts
        if int(a.get("cooldown_left") or 0) > 0
    ]
    video_accounts = [
        a for a in active_accounts
        if bool(a.get("video_allowed", True))
    ]
    return _json({
        "api": {"ok": True, "ts": time.time()},
        "accounts": {
            "active": len(active_accounts),
            "total": len(accounts),
            "cooldown": len(cooldown_accounts),
            "disabled": len([a for a in accounts if a.get("disabled")]),
            "video_active": len(video_accounts),
        },
        "health": metrics.report_ops_health(),
        "stats": metrics.report_admin_stats(),
        "events": metrics.report_recent_events(20),
    })


# ── accounts ───────────────────────────────────────────────────────────

async def _get_keeper_gcredits(account_id: str) -> dict | None:
    if not _keepers or account_id not in _keepers:
        return None
    try:
        result = await asyncio.wait_for(
            _keepers[account_id].get_g_credits(),
            timeout=GCREDITS_LOOKUP_TIMEOUT_SEC,
        )
        return result if isinstance(result, dict) else None
    except (asyncio.TimeoutError, TimeoutError):
        log.warning("admin accounts g_credits lookup timed out for %s", account_id)
        return None
    except Exception:
        log.warning("admin accounts g_credits lookup failed for %s", account_id, exc_info=True)
        return None


async def handle_accounts_get(request: web.Request) -> web.Response:
    if _pool is None:
        return _json({"error": "pool not initialized"}, 503)
    accounts = _pool.status()
    stats = metrics.report_account_stats()

    gcredits_map: dict = {}
    if _keepers:
        ids = [acc["id"] for acc in accounts if acc["id"] in _keepers]
        results = await asyncio.gather(*[_get_keeper_gcredits(aid) for aid in ids])
        gcredits_map = dict(zip(ids, results))

    for acc in accounts:
        s = stats.get(acc["id"], {})
        acc["jobs_total"]   = s.get("total",   0)
        acc["jobs_success"] = s.get("success", 0)
        acc["jobs_fail"]    = s.get("fail",    0)
        acc["last_activity"] = s.get("last_activity")
        acc["last_error"] = s.get("last_error")
        acc["g_credits"] = gcredits_map.get(acc["id"])
        if acc.get("disabled"):
            acc["health"] = "disabled"
        elif int(acc.get("cooldown_left") or 0) > 0:
            acc["health"] = "cooldown"
        elif int(acc.get("fails") or 0) > 0 or int(acc.get("jobs_fail") or 0) > 0:
            acc["health"] = "warning"
        else:
            acc["health"] = "ok"
    return _json(accounts)


async def handle_account_enable(request: web.Request) -> web.Response:
    acc_id = request.match_info["id"]
    if _pool is None:
        return _json({"error": "pool not initialized"}, 503)
    before = next((a for a in _pool.status() if a.get("id") == acc_id), None)
    ok = _pool.set_disabled(acc_id, False)
    if not ok:
        _audit(request, "account.enable", old=before, new={"id": acc_id}, result="not_found")
        return _json({"error": f"account {acc_id!r} not found"}, 404)
    _audit(request, "account.enable", old=before, new={"id": acc_id, "disabled": False})
    return _json({"ok": True, "id": acc_id, "disabled": False})


async def handle_account_disable(request: web.Request) -> web.Response:
    acc_id = request.match_info["id"]
    if _pool is None:
        return _json({"error": "pool not initialized"}, 503)
    before = next((a for a in _pool.status() if a.get("id") == acc_id), None)
    ok = _pool.set_disabled(acc_id, True)
    if not ok:
        _audit(request, "account.disable", old=before, new={"id": acc_id}, result="not_found")
        return _json({"error": f"account {acc_id!r} not found"}, 404)
    _audit(request, "account.disable", old=before, new={"id": acc_id, "disabled": True})
    return _json({"ok": True, "id": acc_id, "disabled": True})


async def handle_account_video(request: web.Request) -> web.Response:
    acc_id = request.match_info["id"]
    if _pool is None:
        return _json({"error": "pool not initialized"}, 503)
    body = await _body(request)
    if body is None:
        return _json({"error": "invalid JSON body"}, 400)
    allowed = bool(body.get("allowed", True))
    before = next((a for a in _pool.status() if a.get("id") == acc_id), None)
    ok = _pool.set_video_allowed(acc_id, allowed)
    if not ok:
        _audit(request, "account.video", old=before, new={"id": acc_id, "video_allowed": allowed}, result="not_found")
        return _json({"error": f"account {acc_id!r} not found"}, 404)
    _audit(request, "account.video", old=before, new={"id": acc_id, "video_allowed": allowed})
    return _json({"ok": True, "id": acc_id, "video_allowed": allowed})


async def handle_account_reset(request: web.Request) -> web.Response:
    acc_id = request.match_info["id"]
    if _pool is None:
        return _json({"error": "pool not initialized"}, 503)
    before = next((a for a in _pool.status() if a.get("id") == acc_id), None)
    ok = _pool.reset_failures(acc_id)
    if not ok:
        _audit(request, "account.reset_failures", old=before, new={"id": acc_id}, result="not_found")
        return _json({"error": f"account {acc_id!r} not found"}, 404)
    _audit(request, "account.reset_failures", old=before, new={"id": acc_id, "fails": 0, "cooldown_left": 0})
    return _json({"ok": True, "id": acc_id, "fails": 0, "cooldown_left": 0})


# ── config: messages ───────────────────────────────────────────────────

def _message_defaults() -> dict:
    try:
        import flow_copy
        return dict(flow_copy.MESSAGES)
    except Exception:
        return {}


def _label_defaults() -> dict:
    try:
        import flow_copy
        return dict(flow_copy.LABELS)
    except Exception:
        return {}


def _copy_warnings(key: str, value: str, *, kind: str) -> list[str]:
    warnings: list[str] = []
    if _PRICE_TEXT_RE.search(value):
        warnings.append("hardcoded_price")
    if kind == "label" and len(value) > 64:
        warnings.append("button_too_long")
    return warnings


def _copy_items(defaults: dict, overrides: dict, *, kind: str) -> list[dict]:
    items: list[dict] = []
    for key in sorted(defaults):
        default = defaults[key]
        editable = isinstance(default, str)
        value = overrides.get(key, default)
        if not isinstance(value, str):
            value = default if isinstance(default, str) else json.dumps(default, ensure_ascii=False)
        placeholders = sorted(_format_placeholders(default)) if isinstance(default, str) else []
        items.append({
            "key": key,
            "kind": kind,
            "group": _copy_group(key),
            "default": default if isinstance(default, str) else None,
            "value": value,
            "override": key in overrides,
            "editable": editable,
            "placeholders": placeholders,
            "warnings": _copy_warnings(key, value, kind=kind) if editable else ["non_string_default"],
        })
    for key in sorted(k for k in overrides if k not in defaults):
        value = str(overrides[key])
        items.append({
            "key": key,
            "kind": kind,
            "group": "custom",
            "default": None,
            "value": value,
            "override": True,
            "editable": True,
            "placeholders": [],
            "warnings": _copy_warnings(key, value, kind=kind) + ["unknown_key"],
        })
    return items


def _validate_copy_payload(defaults: dict, body: dict, *, kind: str) -> tuple[dict, list[dict], list[dict]]:
    clean: dict = {}
    errors: list[dict] = []
    warnings: list[dict] = []
    for key, raw in body.items():
        key = str(key)
        default = defaults.get(key)
        if default is not None and not isinstance(default, str):
            errors.append({"key": key, "error": "non_string_default"})
            continue
        value = str(raw)
        try:
            present = _format_placeholders(value)
        except ValueError as exc:
            errors.append({"key": key, "error": "format_syntax", "detail": str(exc)})
            continue
        if isinstance(default, str):
            required = _format_placeholders(default)
            lost = sorted(required - present)
            if lost:
                errors.append({"key": key, "error": "missing_placeholders", "placeholders": lost})
                continue
        for warning in _copy_warnings(key, value, kind=kind):
            warnings.append({"key": key, "warning": warning})
        clean[key] = value
    return clean, errors, warnings


def _overrides_only(defaults: dict, clean: dict) -> dict:
    """Keep only genuine customizations: keys absent from the code defaults, or
    whose value differs from code.

    The admin UI posts the *full* copy dict on every save. Persisting all of it
    froze a snapshot that shadowed later code-side edits (a changed message in
    ``flow_copy.py`` stayed invisible because the override still held the old
    text). Storing only real diffs lets unedited keys fall back to code.
    """
    return {k: v for k, v in clean.items() if k not in defaults or v != defaults.get(k)}

async def handle_messages_get(request: web.Request) -> web.Response:
    defaults = _message_defaults()
    overrides = config_store.get_section("messages")
    if request.rel_url.query.get("meta") == "1":
        return _json({"items": _copy_items(defaults, overrides, kind="message")})
    data = dict(defaults)
    data.update(overrides)
    return _json(data)


async def handle_messages_post(request: web.Request) -> web.Response:
    body = await _body(request)
    if body is None:
        return _json({"error": "expected JSON object"}, 400)
    defaults = _message_defaults()
    old = config_store.get_section("messages")
    clean, errors, warnings = _validate_copy_payload(defaults, body, kind="message")
    if errors:
        _audit(request, "messages.save", old=old, new={"errors": errors}, result="validation_error")
        return _json({"error": "validation failed", "errors": errors, "warnings": warnings}, 400)
    clean = _overrides_only(defaults, clean)
    try:
        config_store.set_section("messages", clean)
    except Exception:
        log.warning("messages.save: write failed", exc_info=True)
        _audit(request, "messages.save", old=old, new=clean, result="write_error")
        return _json({"error": "write_failed"}, 500)
    _audit(request, "messages.save", old=old, new=clean)
    return _json({"ok": True, "saved": len(clean), "warnings": warnings})


# ── config: labels ─────────────────────────────────────────────────────

async def handle_labels_get(request: web.Request) -> web.Response:
    defaults = _label_defaults()
    overrides = config_store.get_section("labels")
    if request.rel_url.query.get("meta") == "1":
        return _json({"items": _copy_items(defaults, overrides, kind="label")})
    data = dict(defaults)
    data.update(overrides)
    return _json(data)


async def handle_labels_post(request: web.Request) -> web.Response:
    body = await _body(request)
    if body is None:
        return _json({"error": "expected JSON object"}, 400)
    defaults = _label_defaults()
    old = config_store.get_section("labels")
    clean, errors, warnings = _validate_copy_payload(defaults, body, kind="label")
    if errors:
        _audit(request, "labels.save", old=old, new={"errors": errors}, result="validation_error")
        return _json({"error": "validation failed", "errors": errors, "warnings": warnings}, 400)
    clean = _overrides_only(defaults, clean)
    try:
        config_store.set_section("labels", clean)
    except Exception:
        log.warning("labels.save: write failed", exc_info=True)
        _audit(request, "labels.save", old=old, new=clean, result="write_error")
        return _json({"error": "write_failed"}, 500)
    _audit(request, "labels.save", old=old, new=clean)
    return _json({"ok": True, "saved": len(clean), "warnings": warnings})


# ── config: prices ─────────────────────────────────────────────────────

_PRICE_META = {
    "image_nano": {"group": "Картинки", "label": "Картинка Nano Banana 2", "free_allowed": False},
    "image_pro": {"group": "Картинки", "label": "Картинка Nano Banana Pro", "free_allowed": False},
    "edit_photo": {"group": "Картинки", "label": "Редактирование фото", "free_allowed": False},
    "upscale": {"group": "Картинки", "label": "Улучшение качества", "free_allowed": False},
    "omni_4s": {"group": "Видео", "label": "Omni Flash 4s", "free_allowed": False},
    "omni_6s": {"group": "Видео", "label": "Omni Flash 6s", "free_allowed": False},
    "omni_8s": {"group": "Видео", "label": "Omni Flash 8s", "free_allowed": False},
    "omni_10s": {"group": "Видео", "label": "Omni Flash 10s", "free_allowed": False},
    "veo_lite": {"group": "Видео", "label": "Veo Lite", "free_allowed": False},
    "veo_fast": {"group": "Видео", "label": "Veo Fast", "free_allowed": False},
    "veo_quality": {"group": "Видео", "label": "Veo Quality", "free_allowed": False},
    "ingredients_extra": {"group": "Видео: reference modes", "label": "Надбавка: фото + текст", "free_allowed": False},
    "frames_extra": {"group": "Видео: reference modes", "label": "Надбавка: старт/финиш кадры", "free_allowed": False},
    "extend_video": {"group": "Постпродакшн", "label": "Продлить видео", "free_allowed": False},
    "edit_video": {"group": "Постпродакшн", "label": "Изменить видео", "free_allowed": False},
}


def _build_price_defaults() -> dict:
    try:
        import flow_core
        vid = flow_core.VIDEO_MODELS
        pro_extra = int(flow_core.IMAGE_MODELS.get("nbpro", {}).get("extra", 5))
        return {
            "image_nano":    getattr(flow_core, "PRICE_PER_IMAGE", 10),
            "image_pro":     getattr(flow_core, "PRICE_PER_IMAGE", 10) + pro_extra,
            "edit_photo":    getattr(flow_core, "IMAGE_EDIT_PRICE", 15),
            "upscale":       getattr(flow_core, "UPSCALE_PRICE", 5),
            "omni_4s":       vid.get("omni-flash-4s",  {}).get("price", 50),
            "omni_6s":       vid.get("omni-flash-6s",  {}).get("price", 70),
            "omni_8s":       vid.get("omni-flash-8s",  {}).get("price", 85),
            "omni_10s":      vid.get("omni-flash-10s", {}).get("price", 100),
            "veo_lite":      vid.get("veo-lite",       {}).get("price", 60),
            "veo_fast":      vid.get("veo-fast",       {}).get("price", 120),
            "veo_quality":   vid.get("veo-quality",    {}).get("price", 450),
            "ingredients_extra": getattr(flow_core, "VIDEO_INGREDIENTS_SURCHARGE", 15),
            "extend_video":  getattr(flow_core, "VIDEO_EXTEND_PRICE", 60),
            "edit_video":    getattr(flow_core, "VIDEO_PROMPT_EDIT_PRICE", 150),
            "frames_extra":  getattr(flow_core, "VIDEO_FRAMES_SURCHARGE", 25),
        }
    except Exception:
        return {}


def _price_items(defaults: dict, overrides: dict) -> list[dict]:
    items: list[dict] = []
    for key, meta in _PRICE_META.items():
        default = int(defaults.get(key, 0))
        has_override = key in overrides
        try:
            value = int(overrides.get(key, default))
        except (TypeError, ValueError):
            value = default
        items.append({
            "key": key,
            "group": meta["group"],
            "label": meta["label"],
            "default": default,
            "value": value,
            "override": has_override,
            "free_allowed": bool(meta.get("free_allowed")),
        })
    return items


def _validate_prices(body: dict) -> tuple[dict, list[dict]]:
    errors: list[dict] = []
    prices: dict = {}
    for k, v in body.items():
        key = str(k)
        meta = _PRICE_META.get(key)
        if not meta:
            errors.append({"key": key, "error": "unknown_price_key"})
            continue
        try:
            price = int(v)
        except (TypeError, ValueError):
            errors.append({"key": key, "error": "not_integer"})
            continue
        if price < 0:
            errors.append({"key": key, "error": "negative_price"})
            continue
        if price == 0 and not meta.get("free_allowed"):
            errors.append({"key": key, "error": "zero_paid_price"})
            continue
        prices[key] = price
    return prices, errors


async def handle_prices_get(request: web.Request) -> web.Response:
    defaults = _build_price_defaults()
    overrides = config_store.get_section("prices")
    if request.rel_url.query.get("meta") == "1":
        return _json({
            "items": _price_items(defaults, overrides),
            "warning": "Изменение цены применяется к новым операциям сразу.",
        })
    data = dict(defaults)
    data.update(overrides)
    return _json(data)


async def handle_prices_post(request: web.Request) -> web.Response:
    body = await _body(request)
    if body is None:
        return _json({"error": "expected JSON object"}, 400)
    old = config_store.get_section("prices")
    prices, errors = _validate_prices(body)
    if errors:
        _audit(request, "prices.save", old=old, new={"errors": errors}, result="validation_error")
        return _json({"error": "validation failed", "errors": errors}, 400)
    try:
        config_store.set_section("prices", prices)
    except Exception:
        log.warning("prices.save: write failed", exc_info=True)
        _audit(request, "prices.save", old=old, new=prices, result="write_error")
        return _json({"error": "write_failed"}, 500)
    _audit(request, "prices.save", old=old, new=prices)
    return _json({"ok": True, "saved": len(prices)})


# ── config: settings ───────────────────────────────────────────────────

async def handle_settings_get(request: web.Request) -> web.Response:
    try:
        import flow_core
        starter = getattr(flow_core, "STARTER_CREDITS", 30)
    except Exception:
        starter = 30
    defaults = {
        "starter_credits": starter,
        "cooldown_sec":    int(_pool._cooldown_sec) if _pool else 600,
        "max_failures":    int(_pool._max_failures) if _pool else 3,
    }
    defaults.update(config_store.get_section("settings"))
    return _json(defaults)


async def handle_settings_post(request: web.Request) -> web.Response:
    body = await _body(request)
    if body is None:
        return _json({"error": "expected JSON object"}, 400)
    old = config_store.get_section("settings")
    applied: dict = {}
    errors: list[dict] = []
    if "starter_credits" in body:
        try:
            v = int(body["starter_credits"])
            if v < 0:
                raise ValueError("starter_credits must be >= 0")
            applied["starter_credits"] = v
            try:
                import flow_core
                flow_core.STARTER_CREDITS = v
            except Exception:
                pass
        except (TypeError, ValueError):
            errors.append({"key": "starter_credits", "error": "invalid_non_negative_integer"})
    if "cooldown_sec" in body and _pool is not None:
        try:
            v = float(body["cooldown_sec"])
            if v < 30:
                raise ValueError("cooldown_sec too small")
            _pool._cooldown_sec = v
            applied["cooldown_sec"] = v
        except (TypeError, ValueError):
            errors.append({"key": "cooldown_sec", "error": "invalid_min_30"})
    if "max_failures" in body and _pool is not None:
        try:
            v = int(body["max_failures"])
            if v < 1:
                raise ValueError("max_failures must be >= 1")
            _pool._max_failures = v
            applied["max_failures"] = v
        except (TypeError, ValueError):
            errors.append({"key": "max_failures", "error": "invalid_min_1"})
    if errors:
        _audit(request, "settings.save", old=old, new={"errors": errors}, result="validation_error")
        return _json({"error": "validation failed", "errors": errors}, 400)
    try:
        config_store.set_section("settings", {k: v for k, v in applied.items()})
    except Exception:
        log.warning("settings.save: write failed", exc_info=True)
        _audit(request, "settings.save", old=old, new=applied, result="write_error")
        return _json({"error": "write_failed"}, 500)
    _audit(request, "settings.save", old=old, new=applied)
    return _json({"ok": True, "applied": applied})


# ── config: flags ──────────────────────────────────────────────────────

_FLAG_META = {
    "upload_video_edit": {
        "label": "Правка загруженного видео",
        "desc": "UPLOAD_VIDEO_EDIT_ENABLED",
        "default": False,
        "impact": "Включает нестабильный upload-video путь генерации.",
        "dangerous": True,
    },
    "ideas_hub": {
        "label": "Идеи и шаблоны",
        "desc": "IDEAS_HUB_ENABLED",
        "default": True,
        "impact": "Меняет навигацию и сценарии онбординга.",
        "dangerous": False,
    },
    "referrals": {
        "label": "Реферальная система",
        "desc": "REFERRAL_ENABLED",
        "default": True,
        "impact": "Влияет на начисление реферальных кредитов.",
        "dangerous": True,
    },
    "stars_pay": {
        "label": "Оплата Telegram Stars",
        "desc": "STARS_PAYMENT_ENABLED",
        "default": True,
        "impact": "Влияет на доступность платежей через Telegram Stars.",
        "dangerous": True,
    },
    "sbp_pay": {
        "label": "Оплата СБП/Карта",
        "desc": "SBP_PAYMENT_ENABLED",
        "default": True,
        "impact": "Влияет на доступность Robokassa/СБП платежей.",
        "dangerous": True,
    },
}


async def handle_flags_get(request: web.Request) -> web.Response:
    stored = config_store.get_section("flags")
    result = {}
    for key, meta in _FLAG_META.items():
        code_default = bool(meta["default"])
        # For upload_video_edit, read actual runtime value from flow_bot
        if key == "upload_video_edit":
            try:
                import flow_bot
                live = getattr(flow_bot, "UPLOAD_VIDEO_EDIT_ENABLED", code_default)
            except Exception:
                live = code_default
            value = stored.get(key, live)
        else:
            value = stored.get(key, code_default)
        result[key] = {
            "label": meta["label"],
            "desc": meta["desc"],
            "value": bool(value),
            "default": code_default,
            "impact": meta["impact"],
            "dangerous": bool(meta["dangerous"]),
            "override": key in stored,
        }
    return _json(result)


async def handle_flags_post(request: web.Request) -> web.Response:
    body = await _body(request)
    if body is None:
        return _json({"error": "expected JSON object"}, 400)
    old = config_store.get_section("flags")
    flags = {k: bool(v) for k, v in body.items() if k in _FLAG_META}
    try:
        config_store.set_section("flags", flags)
    except Exception:
        log.warning("flags.save: write failed", exc_info=True)
        _audit(request, "flags.save", old=old, new=flags, result="write_error")
        return _json({"error": "write_failed"}, 500)
    # Hot-apply UPLOAD_VIDEO_EDIT_ENABLED
    if "upload_video_edit" in flags:
        try:
            import flow_bot
            flow_bot.UPLOAD_VIDEO_EDIT_ENABLED = flags["upload_video_edit"]
            log.info("admin: UPLOAD_VIDEO_EDIT_ENABLED set to %s", flags["upload_video_edit"])
        except Exception:
            log.warning("admin: failed to hot-apply UPLOAD_VIDEO_EDIT_ENABLED", exc_info=True)
    _audit(request, "flags.save", old=old, new=flags)
    return _json({"ok": True, "applied": flags})


# ── users ──────────────────────────────────────────────────────────────

async def handle_users_get(request: web.Request) -> web.Response:
    """Paginated user list with credits balance.

    Query params: page (1-based, default 1), limit (default 50, max 200),
                  q (search by username / user_id prefix).
    """
    try:
        page  = max(1, int(request.rel_url.query.get("page", "1")))
        limit = min(int(request.rel_url.query.get("limit", "50")), 200)
        q     = (request.rel_url.query.get("q") or "").strip()
    except (TypeError, ValueError):
        page, limit, q = 1, 50, ""

    offset = (page - 1) * limit

    try:
        import sqlite3, os
        db_path = os.getenv("METRICS_DB", "metrics.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        where = ""
        params_count: list = []
        params_rows:  list = []
        if q:
            where = "WHERE (u.username LIKE ? OR CAST(u.user_id AS TEXT) LIKE ?)"
            like  = f"%{q}%"
            params_count = [like, like]
            params_rows  = [like, like, limit, offset]
        else:
            params_rows = [limit, offset]

        sql_count = f"SELECT COUNT(*) FROM users u {where}"
        total = conn.execute(sql_count, params_count).fetchone()[0]

        sql_rows = (
            f"SELECT u.user_id, u.username, u.first_name, u.first_seen, u.last_active, "
            f"       u.acq_channel, u.is_blocked, "
            f"       COALESCE(c.balance, 0) AS balance, "
            f"       COALESCE(c.granted, 0) AS granted, "
            f"       (SELECT COUNT(*) FROM transactions t WHERE t.user_id=u.user_id AND t.status='paid') AS payments_count, "
            f"       (SELECT COALESCE(SUM(t.amount_rub),0) FROM transactions t WHERE t.user_id=u.user_id AND t.status='paid') AS rub_total, "
            f"       (SELECT COUNT(*) FROM support_tickets st WHERE st.user_id=u.user_id AND st.status='open') AS open_tickets, "
            f"       (SELECT fj.status FROM flow_jobs fj WHERE fj.user_id=u.user_id ORDER BY fj.id DESC LIMIT 1) AS last_job_status, "
            f"       (SELECT fj.error_type FROM flow_jobs fj WHERE fj.user_id=u.user_id AND fj.status!='success' ORDER BY fj.id DESC LIMIT 1) AS last_error "
            f"FROM users u "
            f"LEFT JOIN credits c ON c.user_id = u.user_id "
            f"{where} "
            f"ORDER BY u.last_active DESC LIMIT ? OFFSET ?"
        )
        rows = conn.execute(sql_rows, params_rows).fetchall()
        conn.close()

        users_list = [
            {
                "user_id":     int(r["user_id"]),
                "username":    r["username"],
                "first_name":  r["first_name"],
                "first_seen":  (r["first_seen"] or "")[:16],
                "last_active": (r["last_active"] or "")[:16],
                "acq_channel": r["acq_channel"],
                "is_blocked":  bool(r["is_blocked"]),
                "balance":     int(r["balance"]),
                "granted":     int(r["granted"]),
                "payments_count": int(r["payments_count"] or 0),
                "rub_total": float(r["rub_total"] or 0.0),
                "open_tickets": int(r["open_tickets"] or 0),
                "last_job_status": r["last_job_status"],
                "last_error": r["last_error"],
            }
            for r in rows
        ]
        return _json({
            "total": total,
            "page":  page,
            "limit": limit,
            "pages": max(1, (total + limit - 1) // limit),
            "users": users_list,
        })
    except Exception:
        log.warning("handle_users_get failed", exc_info=True)
        return _json({"error": "db error"}, 500)


async def handle_user_detail_get(request: web.Request) -> web.Response:
    try:
        user_id = int(request.match_info["id"])
    except (TypeError, ValueError):
        return _json({"error": "invalid user id"}, 400)
    return _json(metrics.get_admin_user_detail(user_id))


# ── support cockpit ───────────────────────────────────────────────────

async def handle_support_get(request: web.Request) -> web.Response:
    status = request.rel_url.query.get("status", "open")
    try:
        limit = min(int(request.rel_url.query.get("limit", "100")), 200)
    except (TypeError, ValueError):
        limit = 100
    tickets = metrics.list_support_tickets(status=status, limit=limit)
    return _json({
        "tickets": tickets,
        "status": status if status in {"open", "replied", "closed", "all"} else "open",
        "reply_from_admin_available": False,
        "note": "Ответ пользователю из веб-админки не отправляется; текущий reply flow живёт в Telegram.",
    })


async def handle_support_detail_get(request: web.Request) -> web.Response:
    try:
        ticket_id = int(request.match_info["id"])
    except (TypeError, ValueError):
        return _json({"error": "invalid ticket id"}, 400)
    data = metrics.get_support_ticket_detail(ticket_id)
    if not data:
        return _json({"error": "ticket not found"}, 404)
    return _json(data)


async def handle_support_status_post(request: web.Request) -> web.Response:
    try:
        ticket_id = int(request.match_info["id"])
    except (TypeError, ValueError):
        return _json({"error": "invalid ticket id"}, 400)
    body = await _body(request)
    if body is None:
        return _json({"error": "expected JSON object"}, 400)
    status = str(body.get("status", "")).strip()
    if status not in {"open", "replied", "closed"}:
        return _json({"error": "invalid status"}, 400)
    before = metrics.get_support_ticket_detail(ticket_id)
    ok = metrics.set_support_ticket_status(ticket_id, status)
    if not ok:
        _audit(request, "support.status", old=before, new={"id": ticket_id, "status": status}, result="not_found")
        return _json({"error": "ticket not found"}, 404)
    _audit(request, "support.status", old=before.get("ticket") if before else None, new={"id": ticket_id, "status": status})
    return _json({"ok": True, "id": ticket_id, "status": status, "sent_to_user": False})


# ── referrals ──────────────────────────────────────────────────────────

async def handle_referrals_get(request: web.Request) -> web.Response:
    """Referral program stats + top referrers + recent rows."""
    try:
        summary  = metrics.report_refs()
        top_ref  = metrics.report_top_referrers(20)

        import sqlite3, os
        db_path = os.getenv("METRICS_DB", "metrics.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT r.id, r.referrer_user_id, ur.username AS referrer_username, "
            "       r.referred_user_id,  ui.username AS referred_username, "
            "       r.status, r.reward_credits, r.created_at, r.rewarded_at "
            "FROM referrals r "
            "LEFT JOIN users ur ON ur.user_id = r.referrer_user_id "
            "LEFT JOIN users ui ON ui.user_id = r.referred_user_id "
            "ORDER BY r.id DESC LIMIT 100"
        ).fetchall()
        conn.close()

        recent = [
            {
                "id":                  int(r["id"]),
                "referrer_user_id":    int(r["referrer_user_id"]),
                "referrer_username":   r["referrer_username"],
                "referred_user_id":    int(r["referred_user_id"]) if r["referred_user_id"] else None,
                "referred_username":   r["referred_username"],
                "status":              r["status"],
                "reward_credits":      int(r["reward_credits"] or 0),
                "created_at":          (r["created_at"] or "")[:16],
                "rewarded_at":         (r["rewarded_at"] or "")[:16] if r["rewarded_at"] else None,
            }
            for r in rows
        ]
        return _json({
            "summary":      summary,
            "top_referrers": top_ref.get("referrers", []),
            "recent":        recent,
        })
    except Exception:
        log.warning("handle_referrals_get failed", exc_info=True)
        return _json({"error": "db error"}, 500)


# ── analytics ──────────────────────────────────────────────────────────

async def handle_analytics_today(request: web.Request) -> web.Response:
    return _json(metrics.report_today())


async def handle_analytics_revenue(request: web.Request) -> web.Response:
    try:
        days = min(int(request.rel_url.query.get("days", "30")), 365)
    except (TypeError, ValueError):
        days = 30
    return _json(metrics.report_revenue(days))


async def handle_analytics_flow(request: web.Request) -> web.Response:
    return _json(metrics.report_flow())


async def handle_analytics_channels(request: web.Request) -> web.Response:
    return _json(metrics.report_channels())


async def handle_analytics_errors(request: web.Request) -> web.Response:
    try:
        days = min(int(request.rel_url.query.get("days", "7")), 90)
    except (TypeError, ValueError):
        days = 7
    return _json(metrics.report_errors(days))


async def handle_analytics_active(request: web.Request) -> web.Response:
    dau_wau_mau = metrics.report_active_users()
    top_users   = metrics.report_top_users(limit=20)
    return _json({**dau_wau_mau, "top_users": top_users.get("users", [])})


# ── registration ───────────────────────────────────────────────────────

def register_admin_routes(app: web.Application, pool: "AccountPool", keepers: dict | None = None) -> None:
    """Register all /api/admin/* routes into an existing aiohttp Application.

    ``keepers`` (account_id -> SessionKeeper) is optional and only used by
    /api/admin/accounts to attach a live G-credits balance per account.
    """
    global _pool, _keepers
    _pool = pool
    _keepers = keepers
    r = app.router
    r.add_get ("/api/admin/ping",                      handle_ping)
    r.add_get ("/api/admin/ops",                       handle_ops_get)
    r.add_get ("/api/admin/stats",                     handle_stats)
    r.add_get ("/api/admin/log",                       handle_log)
    r.add_get ("/api/admin/accounts",                  handle_accounts_get)
    r.add_post("/api/admin/accounts/{id}/enable",      handle_account_enable)
    r.add_post("/api/admin/accounts/{id}/disable",     handle_account_disable)
    r.add_post("/api/admin/accounts/{id}/video",       handle_account_video)
    r.add_post("/api/admin/accounts/{id}/reset",       handle_account_reset)
    r.add_get ("/api/admin/config/messages",           handle_messages_get)
    r.add_post("/api/admin/config/messages",           handle_messages_post)
    r.add_get ("/api/admin/config/labels",             handle_labels_get)
    r.add_post("/api/admin/config/labels",             handle_labels_post)
    r.add_get ("/api/admin/config/prices",             handle_prices_get)
    r.add_post("/api/admin/config/prices",             handle_prices_post)
    r.add_get ("/api/admin/config/settings",           handle_settings_get)
    r.add_post("/api/admin/config/settings",           handle_settings_post)
    r.add_get ("/api/admin/config/flags",              handle_flags_get)
    r.add_post("/api/admin/config/flags",              handle_flags_post)
    # Users
    r.add_get ("/api/admin/users",                     handle_users_get)
    r.add_get ("/api/admin/users/{id}",                handle_user_detail_get)
    # Sellers (seller-bot segment)
    r.add_get ("/api/admin/sellers",                   handle_sellers_get)
    # Support
    r.add_get ("/api/admin/support",                   handle_support_get)
    r.add_get ("/api/admin/support/{id}",              handle_support_detail_get)
    r.add_post("/api/admin/support/{id}/status",       handle_support_status_post)
    # Referrals
    r.add_get ("/api/admin/referrals",                 handle_referrals_get)
    # Analytics
    r.add_get ("/api/admin/analytics/today",           handle_analytics_today)
    r.add_get ("/api/admin/analytics/revenue",         handle_analytics_revenue)
    r.add_get ("/api/admin/analytics/flow",            handle_analytics_flow)
    r.add_get ("/api/admin/analytics/channels",        handle_analytics_channels)
    r.add_get ("/api/admin/analytics/errors",          handle_analytics_errors)
    r.add_get ("/api/admin/analytics/active",          handle_analytics_active)
    log.info("Admin API registered on /api/admin/* (%d routes)", 33)
