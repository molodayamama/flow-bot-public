"""Admin REST API — aiohttp routes registered into the existing web.Application.

All routes are under /api/admin/ and are already protected by nginx HTTP Basic
Auth (lo / password). The Python side requires no additional auth because the
server binds only to 127.0.0.1 and is unreachable from the internet directly.

Usage in flow_bot.py:
    import admin_api
    admin_api.register_admin_routes(app, account_pool)
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

from aiohttp import web

import config_store
import metrics

if TYPE_CHECKING:
    from flow_core import AccountPool

log = logging.getLogger("flow.admin_api")

_pool: "AccountPool | None" = None


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


# ── accounts ───────────────────────────────────────────────────────────

async def handle_accounts_get(request: web.Request) -> web.Response:
    if _pool is None:
        return _json({"error": "pool not initialized"}, 503)
    accounts = _pool.status()
    stats = metrics.report_account_stats()
    for acc in accounts:
        s = stats.get(acc["id"], {})
        acc["jobs_total"]   = s.get("total",   0)
        acc["jobs_success"] = s.get("success", 0)
        acc["jobs_fail"]    = s.get("fail",    0)
    return _json(accounts)


async def handle_account_enable(request: web.Request) -> web.Response:
    acc_id = request.match_info["id"]
    if _pool is None:
        return _json({"error": "pool not initialized"}, 503)
    ok = _pool.set_disabled(acc_id, False)
    if not ok:
        return _json({"error": f"account {acc_id!r} not found"}, 404)
    return _json({"ok": True, "id": acc_id, "disabled": False})


async def handle_account_disable(request: web.Request) -> web.Response:
    acc_id = request.match_info["id"]
    if _pool is None:
        return _json({"error": "pool not initialized"}, 503)
    ok = _pool.set_disabled(acc_id, True)
    if not ok:
        return _json({"error": f"account {acc_id!r} not found"}, 404)
    return _json({"ok": True, "id": acc_id, "disabled": True})


async def handle_account_video(request: web.Request) -> web.Response:
    acc_id = request.match_info["id"]
    if _pool is None:
        return _json({"error": "pool not initialized"}, 503)
    body = await _body(request)
    if body is None:
        return _json({"error": "invalid JSON body"}, 400)
    allowed = bool(body.get("allowed", True))
    ok = _pool.set_video_allowed(acc_id, allowed)
    if not ok:
        return _json({"error": f"account {acc_id!r} not found"}, 404)
    return _json({"ok": True, "id": acc_id, "video_allowed": allowed})


async def handle_account_reset(request: web.Request) -> web.Response:
    acc_id = request.match_info["id"]
    if _pool is None:
        return _json({"error": "pool not initialized"}, 503)
    ok = _pool.reset_failures(acc_id)
    if not ok:
        return _json({"error": f"account {acc_id!r} not found"}, 404)
    return _json({"ok": True, "id": acc_id, "fails": 0, "cooldown_left": 0})


# ── config: messages ───────────────────────────────────────────────────

async def handle_messages_get(request: web.Request) -> web.Response:
    try:
        import flow_copy
        data = dict(flow_copy.MESSAGES)
    except Exception:
        data = {}
    data.update(config_store.get_section("messages"))
    return _json(data)


async def handle_messages_post(request: web.Request) -> web.Response:
    body = await _body(request)
    if body is None:
        return _json({"error": "expected JSON object"}, 400)
    config_store.set_section("messages", {k: str(v) for k, v in body.items()})
    return _json({"ok": True, "saved": len(body)})


# ── config: labels ─────────────────────────────────────────────────────

async def handle_labels_get(request: web.Request) -> web.Response:
    try:
        import flow_copy
        data = dict(flow_copy.LABELS)
    except Exception:
        data = {}
    data.update(config_store.get_section("labels"))
    return _json(data)


async def handle_labels_post(request: web.Request) -> web.Response:
    body = await _body(request)
    if body is None:
        return _json({"error": "expected JSON object"}, 400)
    config_store.set_section("labels", {k: str(v) for k, v in body.items()})
    return _json({"ok": True, "saved": len(body)})


# ── config: prices ─────────────────────────────────────────────────────

def _build_price_defaults() -> dict:
    try:
        import flow_core
        vid = flow_core.VIDEO_MODELS
        return {
            "image_nano":    getattr(flow_core, "PRICE_PER_IMAGE", 10),
            "image_pro":     getattr(flow_core, "PRICE_PER_IMAGE", 10),
            "edit_photo":    getattr(flow_core, "IMAGE_EDIT_PRICE", 15),
            "upscale":       getattr(flow_core, "UPSCALE_PRICE", 5),
            "omni_4s":       vid.get("omni-flash-4s",  {}).get("price", 50),
            "omni_6s":       vid.get("omni-flash-6s",  {}).get("price", 70),
            "omni_8s":       vid.get("omni-flash-8s",  {}).get("price", 85),
            "omni_10s":      vid.get("omni-flash-10s", {}).get("price", 100),
            "veo_lite":      vid.get("veo-lite",       {}).get("price", 60),
            "veo_fast":      vid.get("veo-fast",       {}).get("price", 120),
            "veo_quality":   vid.get("veo-quality",    {}).get("price", 450),
            "animate":       getattr(flow_core, "ANIMATE_PRICE", 75),
            "extend_video":  getattr(flow_core, "VIDEO_EXTEND_PRICE", 60),
            "edit_video":    getattr(flow_core, "VIDEO_PROMPT_EDIT_PRICE", 150),
            "frames_extra":  getattr(flow_core, "VIDEO_FRAMES_SURCHARGE", 25),
        }
    except Exception:
        return {}


async def handle_prices_get(request: web.Request) -> web.Response:
    defaults = _build_price_defaults()
    defaults.update(config_store.get_section("prices"))
    return _json(defaults)


async def handle_prices_post(request: web.Request) -> web.Response:
    body = await _body(request)
    if body is None:
        return _json({"error": "expected JSON object"}, 400)
    prices: dict = {}
    for k, v in body.items():
        try:
            prices[str(k)] = int(v)
        except (TypeError, ValueError):
            pass
    config_store.set_section("prices", prices)
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
    applied: dict = {}
    if "starter_credits" in body:
        try:
            v = int(body["starter_credits"])
            applied["starter_credits"] = v
            try:
                import flow_core
                flow_core.STARTER_CREDITS = v
            except Exception:
                pass
        except (TypeError, ValueError):
            pass
    if "cooldown_sec" in body and _pool is not None:
        try:
            v = float(body["cooldown_sec"])
            _pool._cooldown_sec = v
            applied["cooldown_sec"] = v
        except (TypeError, ValueError):
            pass
    if "max_failures" in body and _pool is not None:
        try:
            v = max(1, int(body["max_failures"]))
            _pool._max_failures = v
            applied["max_failures"] = v
        except (TypeError, ValueError):
            pass
    config_store.set_section("settings", {k: v for k, v in applied.items()})
    return _json({"ok": True, "applied": applied})


# ── config: flags ──────────────────────────────────────────────────────

_FLAG_META = {
    "upload_video_edit": ("Правка загруженного видео", "UPLOAD_VIDEO_EDIT_ENABLED", False),
    "ideas_hub":         ("Идеи и шаблоны",            "IDEAS_HUB_ENABLED",         True),
    "referrals":         ("Реферальная система",        "REFERRAL_ENABLED",          True),
    "stars_pay":         ("Оплата Telegram Stars",      "STARS_PAYMENT_ENABLED",     True),
    "sbp_pay":           ("Оплата СБП/Карта",           "SBP_PAYMENT_ENABLED",       True),
}


async def handle_flags_get(request: web.Request) -> web.Response:
    stored = config_store.get_section("flags")
    result = {}
    for key, (label, desc, code_default) in _FLAG_META.items():
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
        result[key] = {"label": label, "desc": desc, "value": bool(value)}
    return _json(result)


async def handle_flags_post(request: web.Request) -> web.Response:
    body = await _body(request)
    if body is None:
        return _json({"error": "expected JSON object"}, 400)
    flags = {k: bool(v) for k, v in body.items() if k in _FLAG_META}
    config_store.set_section("flags", flags)
    # Hot-apply UPLOAD_VIDEO_EDIT_ENABLED
    if "upload_video_edit" in flags:
        try:
            import flow_bot
            flow_bot.UPLOAD_VIDEO_EDIT_ENABLED = flags["upload_video_edit"]
            log.info("admin: UPLOAD_VIDEO_EDIT_ENABLED set to %s", flags["upload_video_edit"])
        except Exception:
            log.warning("admin: failed to hot-apply UPLOAD_VIDEO_EDIT_ENABLED", exc_info=True)
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
            f"       COALESCE(c.granted, 0) AS granted "
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

def register_admin_routes(app: web.Application, pool: "AccountPool") -> None:
    """Register all /api/admin/* routes into an existing aiohttp Application."""
    global _pool
    _pool = pool
    r = app.router
    r.add_get ("/api/admin/ping",                      handle_ping)
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
    # Referrals
    r.add_get ("/api/admin/referrals",                 handle_referrals_get)
    # Analytics
    r.add_get ("/api/admin/analytics/today",           handle_analytics_today)
    r.add_get ("/api/admin/analytics/revenue",         handle_analytics_revenue)
    r.add_get ("/api/admin/analytics/flow",            handle_analytics_flow)
    r.add_get ("/api/admin/analytics/channels",        handle_analytics_channels)
    r.add_get ("/api/admin/analytics/errors",          handle_analytics_errors)
    r.add_get ("/api/admin/analytics/active",          handle_analytics_active)
    log.info("Admin API registered on /api/admin/* (%d routes)", 27)
