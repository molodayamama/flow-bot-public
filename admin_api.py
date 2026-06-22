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
import os
import re
import secrets
import string
import time
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web

import account_onboarding
import config_store
import metrics

if TYPE_CHECKING:
    from flow_core import AccountPool

log = logging.getLogger("flow.admin_api")

_pool: "AccountPool | None" = None
# Optional: account_id -> SessionKeeper, for live G-credits lookup. Injected
# by register_admin_routes(); None means /api/admin/accounts skips g_credits.
_keepers: dict | None = None
_video_clients: dict | None = None
_startup_state: dict | None = None
GCREDITS_LOOKUP_TIMEOUT_SEC = 3.0
ONBOARD_SESSION_TTL_SEC = 10 * 60
_onboard_sessions: dict[str, dict] = {}
ACCOUNT_METADATA_FILE = Path(os.getenv("ACCOUNT_METADATA_FILE", "account_metadata.json"))


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


def _startup_snapshot() -> dict | None:
    if not isinstance(_startup_state, dict):
        return None
    try:
        return json.loads(json.dumps(_startup_state, ensure_ascii=False, default=str))
    except Exception:
        return dict(_startup_state)


def _startup_for_account(account_id: str) -> dict | None:
    snap = _startup_snapshot()
    if not isinstance(snap, dict):
        return None
    accounts = snap.get("accounts")
    if isinstance(accounts, dict):
        item = accounts.get(account_id)
        return dict(item) if isinstance(item, dict) else None
    return None


def _startup_set_account_status(account_id: str, status: str, *, ready: bool = False, error: str | None = None) -> None:
    if not isinstance(_startup_state, dict):
        return
    item = _startup_state.setdefault("accounts", {}).setdefault(account_id, {})
    item.update({"status": status, "ready": bool(ready), "updated_at": time.time()})
    if error:
        item["error"] = error
    else:
        item.pop("error", None)
    accounts = _startup_state.get("accounts")
    if isinstance(accounts, dict):
        _startup_state["ready_accounts"] = sum(1 for a in accounts.values() if isinstance(a, dict) and a.get("ready"))
        _startup_state["total_accounts"] = len(accounts)


def _safe_reason(value: object, limit: int = 240) -> str:
    text = str(value or "")
    token_prefixes = ("Bearer" + r"\s+", "ya29" + r"\.", "session-" + "token=")
    text = re.sub(r"(" + "|".join(token_prefixes) + r")[^\s\"']+", r"\1***", text)
    text = re.sub(r"(https?://[^:/\s]+:)[^@\s]+@", r"\1***@", text)
    return text[:limit]


def _load_account_metadata() -> dict:
    try:
        data = json.loads(ACCOUNT_METADATA_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_account_metadata(data: dict) -> None:
    ACCOUNT_METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = ACCOUNT_METADATA_FILE.with_name(f".{ACCOUNT_METADATA_FILE.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, ACCOUNT_METADATA_FILE)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _set_account_email(account_id: str, email: str) -> None:
    cleaned = (email or "").strip()
    if not cleaned:
        return
    data = _load_account_metadata()
    item = data.get(account_id) if isinstance(data.get(account_id), dict) else {}
    item["email"] = cleaned
    item["updated_at"] = int(time.time())
    data[account_id] = item
    _save_account_metadata(data)


def _remove_account_metadata(account_id: str) -> None:
    data = _load_account_metadata()
    if account_id in data:
        data.pop(account_id, None)
        _save_account_metadata(data)


def _profile_email_guess(profile_dir: str | None) -> str:
    if not profile_dir:
        return ""
    root = Path(profile_dir)
    candidates = [
        root / "Default" / "Preferences",
        root / "Profile 1" / "Preferences",
        root / "Preferences",
    ]
    email_re = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

    def walk(obj, depth: int = 0) -> str:
        if depth > 5:
            return ""
        if isinstance(obj, dict):
            for key, val in obj.items():
                if isinstance(val, str) and key.lower() in {"email", "user_email", "username", "user_name"}:
                    if email_re.match(val):
                        return val
                found = walk(val, depth + 1)
                if found:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = walk(item, depth + 1)
                if found:
                    return found
        return ""

    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        found = walk(data)
        if found:
            return found
    return ""


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
    out = {"ok": True, "ts": time.time()}
    startup = _startup_snapshot()
    if startup is not None:
        out["startup"] = startup
    return _json(out)


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


async def handle_video_health(request: web.Request) -> web.Response:
    """Per-account video health (attempts/403/success/retry/fail) over 1h+24h."""
    return _json(metrics.report_video_health((1, 24)))


async def handle_proxy_check(request: web.Request) -> web.Response:
    """Diagnostic: per-account browser-egress IP vs API-egress IP.

    Confirms/refutes the proxy-mismatch theory for reCAPTCHA 403s (token minted
    in the browser, request sent from aiohttp). Returns only host:port of proxies,
    never credentials.
    """
    if not _keepers:
        return _json({"error": "keepers not available"}, 503)
    ids = list(_keepers.keys())
    results = await asyncio.gather(
        *[_keepers[a].public_ips() for a in ids], return_exceptions=True
    )
    out = []
    for aid, res in zip(ids, results):
        if isinstance(res, Exception):
            out.append({"account": aid, "error": res.__class__.__name__})
        else:
            row = {"account": aid}
            row.update(res)
            out.append(row)
        try:
            metrics.log_event("proxy_check", source=aid, payload=out[-1])
        except Exception:
            log.warning("proxy_check metrics log failed for %s", aid, exc_info=True)
    return _json({"accounts": out})


def _pick_video_ab_account() -> str | None:
    if not _video_clients:
        return None
    if _pool is not None:
        for acc in _pool.status():
            aid = acc.get("id")
            if (
                aid in _video_clients
                and not acc.get("disabled")
                and bool(acc.get("video_allowed", True))
                and int(acc.get("cooldown_left") or 0) <= 0
            ):
                return aid
    return next(iter(_video_clients), None)


def _video_model_labels(model: str) -> tuple[str, str]:
    try:
        from flow_core import VIDEO_MODELS
        mid = (model or "").lower().strip()
        meta = VIDEO_MODELS.get(mid)
        if meta:
            return str(meta.get("key") or model), str(meta.get("family") or "unknown")
        for item in VIDEO_MODELS.values():
            if item.get("key") == model:
                return str(item.get("key") or model), str(item.get("family") or "unknown")
    except Exception:
        pass
    key = str(model or "unknown")
    low = key.lower()
    if "veo" in low:
        return key, "veo"
    if "abra" in low or "omni" in low:
        return key, "omni-flash"
    return key, "unknown"


async def handle_video_ab_post(request: web.Request) -> web.Response:
    """Costly diagnostic: compare direct HTTP vs browser fetch video submit."""
    if not _video_clients:
        return _json({"error": "video clients not available"}, 503)
    body = await _body(request)
    if body is None:
        return _json({"error": "invalid JSON body"}, 400)
    if body.get("confirm_spend") is not True:
        return _json({"error": "confirm_spend=true required"}, 400)

    account_id = str(body.get("account") or body.get("account_id") or "").strip()
    account_id = account_id or _pick_video_ab_account()
    if not account_id or account_id not in _video_clients:
        return _json({"error": f"account {account_id!r} not found"}, 404)

    prompt = str(body.get("prompt") or "simple cinematic shot of a calm sunrise over a lake").strip()
    prompt = prompt[:500] if prompt else "simple cinematic shot of a calm sunrise over a lake"
    model_key = str(body.get("model_key") or body.get("model") or "omni-flash-4s").strip()
    aspect = str(body.get("aspect") or "landscape").strip().lower()
    order = str(body.get("order") or "direct_first").strip().lower()
    try:
        pause_sec = max(0.0, min(float(body.get("pause_sec", 4.0)), 30.0))
    except (TypeError, ValueError):
        pause_sec = 4.0

    client = _video_clients[account_id]
    try:
        result = await client.video_transport_ab_test(
            prompt=prompt,
            model_key=model_key,
            aspect=aspect,
            order=order,
            pause_sec=pause_sec,
        )
    except Exception as exc:  # noqa: BLE001 - debug endpoint must return JSON
        log.warning("video A/B failed for %s: %s", account_id, exc.__class__.__name__, exc_info=True)
        _audit(request, "video_ab", new={"account": account_id, "model": model_key}, result="error")
        return _json({"error": exc.__class__.__name__}, 500)

    statuses = {
        arm.get("transport"): arm.get("status")
        for arm in result.get("arms", [])
        if isinstance(arm, dict)
    }
    effective_model_key, model_family = _video_model_labels(model_key)
    metrics.log_event(
        "video_ab",
        source=account_id,
        payload={
            "account": account_id,
            "model": model_key,
            "model_key": effective_model_key,
            "model_family": model_family,
            "mode": "text",
            "endpoint": "video:batchAsyncGenerateVideoText",
            "aspect": aspect,
            "order": order,
            "arms": result.get("arms", []),
            "statuses": statuses,
        },
    )
    _audit(
        request,
        "video_ab",
        new={"account": account_id, "model": model_key, "aspect": aspect, "statuses": statuses},
    )
    return _json({
        "account": account_id,
        "model_key": model_key,
        "aspect": aspect,
        "result": result,
    })


async def handle_agent_probe_post(request: web.Request) -> web.Response:
    """Discover the reCAPTCHA action for flowCreationAgent (prompt improver).

    Tries one or more candidate actions against the real agent endpoint using a
    chosen account's live session/browser solver. Credits are not spent by the
    agent call, but it still contacts Google, so it is gated by confirm=true.
    Returns sanitized results only (status, action, parsed variant/single counts).
    """
    if not _video_clients:
        return _json({"error": "video clients not available"}, 503)
    body = await _body(request)
    if body is None:
        return _json({"error": "invalid JSON body"}, 400)
    if body.get("confirm") is not True:
        return _json({"error": "confirm=true required"}, 400)

    account_id = str(body.get("account") or body.get("account_id") or "").strip()
    account_id = account_id or _pick_video_ab_account()
    if not account_id or account_id not in _video_clients:
        return _json({"error": f"account {account_id!r} not found"}, 404)

    prompt = str(body.get("prompt") or "котёнок на лежанке").strip()[:500] or "котёнок на лежанке"
    actions = body.get("actions")
    if not isinstance(actions, list) or not actions:
        single_action = body.get("action")
        if single_action:
            actions = [str(single_action)]
        else:
            from flow_bot import SessionKeeper
            actions = list(SessionKeeper.AGENT_RECAPTCHA_ACTION_CANDIDATES)
    actions = [str(a)[:64] for a in actions][:8]
    try:
        pause_sec = max(0.0, min(float(body.get("pause_sec", 3.0)), 30.0))
    except (TypeError, ValueError):
        pause_sec = 3.0

    debug = body.get("debug") is True
    try:
        turn_number = int(body.get("turn_number", 1))
    except (TypeError, ValueError):
        turn_number = 1
    agent_session_id = body.get("agent_session_id")
    agent_session_id = str(agent_session_id) if agent_session_id else None
    client = _video_clients[account_id]
    results: list[dict] = []
    found: str | None = None
    for idx, action in enumerate(actions):
        if idx and pause_sec > 0:
            await asyncio.sleep(pause_sec)
        try:
            res = await client.improve_prompt(
                prompt, action=action, debug=debug, turn_number=turn_number,
                agent_session_id=agent_session_id,
            )
        except Exception as exc:  # noqa: BLE001 - debug endpoint must return JSON
            log.warning("agent probe failed for %s/%s: %s", account_id, action, exc.__class__.__name__)
            results.append({"action": action, "error": exc.__class__.__name__})
            continue
        row = {
            "action": action,
            "status": res.get("status"),
            "ok": res.get("ok"),
            "variants": len(res.get("variants") or []),
            "has_single": bool(res.get("single")),
            "error": res.get("error"),
            "preview": str(res.get("body_preview") or "")[:160],
        }
        if debug:
            row["message"] = str(res.get("message") or "")[:400]
            row["agent_text_preview"] = str(res.get("agent_text_preview") or "")[:2000]
            row["raw_len"] = res.get("raw_len")
            row["raw_preview"] = str(res.get("raw_preview") or "")[:2000]
        results.append(row)
        if res.get("ok") and found is None:
            found = action
            break

    _audit(request, "agent_probe", new={"account": account_id, "found": found})
    return _json({"account": account_id, "found_action": found, "results": results})


async def handle_agent_action_scan_post(request: web.Request) -> web.Response:
    """Scan the Flow frontend bundles for grecaptcha action literals.

    Read-only: no Google API call, no captcha, no credit spend. Helps identify
    the flowCreationAgent reCAPTCHA action without guessing."""
    if not _video_clients:
        return _json({"error": "video clients not available"}, 503)
    body = await _body(request) or {}
    account_id = str(body.get("account") or body.get("account_id") or "").strip()
    account_id = account_id or _pick_video_ab_account()
    if not account_id or account_id not in _video_clients:
        return _json({"error": f"account {account_id!r} not found"}, 404)
    keeper = getattr(_video_clients[account_id], "keeper", None)
    if keeper is None or not hasattr(keeper, "scan_recaptcha_actions"):
        return _json({"error": "scan unavailable"}, 503)
    try:
        result = await keeper.scan_recaptcha_actions()
    except Exception as exc:  # noqa: BLE001 - debug endpoint must return JSON
        log.warning("agent action scan failed for %s: %s", account_id, exc.__class__.__name__)
        return _json({"error": exc.__class__.__name__}, 500)
    _audit(request, "agent_action_scan", new={"account": account_id})
    return _json({"account": account_id, "result": result})


async def handle_agent_capture_post(request: web.Request) -> web.Response:
    """Drive the live browser: open project, click Agent, send a prompt, and
    capture the flowCreationAgent/session network calls (sanitized). confirm=true
    gated because it briefly uses the account's browser (no credit spend)."""
    if not _video_clients:
        return _json({"error": "video clients not available"}, 503)
    body = await _body(request) or {}
    if body.get("confirm") is not True:
        return _json({"error": "confirm=true required"}, 400)
    account_id = str(body.get("account") or body.get("account_id") or "").strip()
    account_id = account_id or _pick_video_ab_account()
    if not account_id or account_id not in _video_clients:
        return _json({"error": f"account {account_id!r} not found"}, 404)
    keeper = getattr(_video_clients[account_id], "keeper", None)
    if keeper is None or not hasattr(keeper, "capture_agent_flow"):
        return _json({"error": "capture unavailable"}, 503)
    prompt = str(body.get("prompt") or "улучши промпт: котёнок на лежанке").strip()[:300]
    try:
        wait_sec = max(4.0, min(float(body.get("wait_sec", 18.0)), 40.0))
    except (TypeError, ValueError):
        wait_sec = 18.0
    try:
        result = await keeper.capture_agent_flow(prompt=prompt, wait_sec=wait_sec)
    except Exception as exc:  # noqa: BLE001 - debug endpoint must return JSON
        log.warning("agent capture failed for %s: %s", account_id, exc.__class__.__name__)
        return _json({"error": exc.__class__.__name__}, 500)
    _audit(request, "agent_capture", new={"account": account_id, "clicked": result.get("clicked_agent")})
    return _json({"account": account_id, "result": result})


async def handle_agent_sessions_post(request: web.Request) -> web.Response:
    """Explore flowCreationAgent/sessions (bearer only, no captcha, no spend)."""
    if not _video_clients:
        return _json({"error": "video clients not available"}, 503)
    body = await _body(request) or {}
    account_id = str(body.get("account") or body.get("account_id") or "").strip()
    account_id = account_id or _pick_video_ab_account()
    if not account_id or account_id not in _video_clients:
        return _json({"error": f"account {account_id!r} not found"}, 404)
    client = _video_clients[account_id]
    if not hasattr(client, "agent_session_call"):
        return _json({"error": "unavailable"}, 503)
    method = str(body.get("method") or "GET").upper()
    suffix = str(body.get("suffix") or "")[:80]
    json_body = body.get("json_body") if isinstance(body.get("json_body"), dict) else None
    proj = body.get("project_id")
    proj = str(proj) if proj else None
    try:
        result = await client.agent_session_call(
            method=method, suffix=suffix, json_body=json_body, project_id=proj)
    except Exception as exc:  # noqa: BLE001
        return _json({"error": exc.__class__.__name__}, 500)
    _audit(request, "agent_sessions", new={"account": account_id, "method": method, "suffix": suffix})
    return _json({"account": account_id, "result": result})


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
    metadata = _load_account_metadata()

    gcredits_map: dict = {}
    if _keepers:
        ids = []
        for acc in accounts:
            aid = str(acc["id"])
            startup = _startup_for_account(aid)
            if startup and startup.get("status") in {"pending", "running"}:
                continue
            if aid in _keepers:
                ids.append(aid)
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
        pool_acc = _pool.get(str(acc["id"])) if _pool is not None else None
        meta = metadata.get(str(acc["id"])) if isinstance(metadata.get(str(acc["id"])), dict) else {}
        acc["email"] = str(meta.get("email") or _profile_email_guess(acc.get("profile_dir")) or "")
        if pool_acc is not None:
            proxy_raw = pool_acc.browser_proxy_url or pool_acc.api_proxy_url or ""
            acc["proxy"] = account_onboarding.proxy_public_label(proxy_raw) if proxy_raw else ""
        startup = _startup_for_account(str(acc["id"]))
        if startup is not None:
            acc["startup"] = startup
            acc["warmup_status"] = startup.get("status")
        if acc.get("disabled"):
            acc["health"] = "disabled"
        elif startup and startup.get("status") in {"pending", "running"}:
            acc["health"] = "warming"
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


async def handle_account_delete(request: web.Request) -> web.Response:
    acc_id = request.match_info["id"]
    body = await _body(request)
    if body is None:
        return _json({"error": "invalid JSON body"}, 400)
    if body.get("confirm_delete") is not True:
        return _json({"error": "confirm_delete=true required"}, 400)
    if _pool is None:
        return _json({"error": "pool not initialized"}, 503)
    before = next((a for a in _pool.status() if a.get("id") == acc_id), None)
    if before is None:
        return _json({"error": f"account {acc_id!r} not found"}, 404)
    if len(_pool.account_ids()) <= 1:
        return _json({"ok": False, "status": "last_account", "error": "last_account"}, 400)
    try:
        env_update = account_onboarding.remove_flow_account_from_env(acc_id)
    except account_onboarding.AccountOnboardingError as exc:
        status = 404 if exc.code == "account_not_found" else 400
        _audit(request, "account.delete", old=before, new={"id": acc_id}, result=exc.code)
        return _json({"ok": False, "status": exc.code, "error": exc.code}, status)
    except PermissionError:
        _audit(request, "account.delete", old=before, new={"id": acc_id}, result="env_permission_denied")
        return _json({"ok": False, "status": "env_permission_denied", "error": "env_permission_denied"}, 500)
    await _close_runtime_account(acc_id)
    runtime_removed = _pool.remove_account(acc_id)
    _remove_account_metadata(acc_id)
    _audit(request, "account.delete", old=before, new={"id": acc_id, "runtime_removed": runtime_removed})
    return _json({
        "ok": True,
        "id": acc_id,
        "runtime_removed": runtime_removed,
        "accounts_count": env_update.get("accounts_count"),
        "profile_removed": False,
    })


# ── config: messages ───────────────────────────────────────────────────

async def _warm_hot_added_account(account_id: str, keeper) -> None:
    if _pool is not None:
        _pool.set_runtime_ready(account_id, False, "warming")
    _startup_set_account_status(account_id, "running", ready=False)
    try:
        await keeper.start()
        try:
            await keeper.create_new_project()
        except Exception as exc:  # noqa: BLE001 - project init is best effort
            log.warning("hot-added account project init failed for %s: %s", account_id, exc.__class__.__name__)
    except Exception as exc:  # noqa: BLE001 - background warmup state
        log.warning("hot-added account warmup failed for %s: %s", account_id, exc.__class__.__name__, exc_info=True)
        if _pool is not None:
            _pool.set_runtime_ready(account_id, False, "error")
        _startup_set_account_status(account_id, "error", ready=False, error=exc.__class__.__name__)
        return
    if _pool is not None:
        _pool.set_runtime_ready(account_id, True, "ready")
    _startup_set_account_status(account_id, "ready", ready=True)


async def _close_runtime_account(account_id: str) -> None:
    if _pool is not None:
        _pool.set_runtime_ready(account_id, False, "stopped")
    _startup_set_account_status(account_id, "stopped", ready=False)
    if _video_clients is not None:
        _video_clients.pop(account_id, None)
    keeper = _keepers.pop(account_id, None) if _keepers is not None else None
    if keeper is not None:
        try:
            await keeper.close()
        except Exception as exc:  # noqa: BLE001
            log.warning("runtime account close failed for %s: %s", account_id, exc.__class__.__name__, exc_info=True)


def _start_runtime_account(account_id: str) -> dict:
    if _pool is None:
        return {"runtime_added": False, "runtime_reason": "pool_not_initialized"}
    account = _pool.get(account_id)
    if account is None:
        return {"runtime_added": False, "runtime_reason": "account_not_found"}
    if _keepers is None or _video_clients is None:
        _startup_set_account_status(account_id, "pending_restart", ready=False)
        return {"runtime_added": False, "runtime_reason": "restart_required_for_clients"}
    try:
        from flow_bot import FlowHttpClient, SessionKeeper

        keeper = SessionKeeper(
            account_id=account.id,
            profile_dir=account.profile_dir,
            browser_proxy_url=account.browser_proxy_url,
            api_proxy_url=account.api_proxy_url,
        )
        _keepers[account.id] = keeper
        _video_clients[account.id] = FlowHttpClient(keeper)
        _startup_set_account_status(account.id, "pending", ready=False)
        asyncio.create_task(_warm_hot_added_account(account.id, keeper))
        return {"runtime_added": True, "runtime_reason": "warming"}
    except Exception as exc:  # noqa: BLE001
        log.warning("runtime account start failed for %s: %s", account_id, exc.__class__.__name__, exc_info=True)
        if _pool is not None:
            _pool.set_runtime_ready(account_id, False, "pending_restart")
        _startup_set_account_status(account_id, "pending_restart", ready=False, error=exc.__class__.__name__)
        return {"runtime_added": False, "runtime_reason": "restart_required_for_clients"}


def _try_hot_add_account(account_id: str, profile_dir: str, proxy_url: str) -> dict:
    if _pool is None:
        return {"runtime_added": False, "runtime_reason": "pool_not_initialized"}
    if account_id in _pool.account_ids():
        return {"runtime_added": False, "runtime_reason": "already_in_runtime"}
    entry = account_onboarding.AccountEntry(
        account_id=account_id,
        profile_dir=profile_dir,
        proxy_url=proxy_url,
    )
    account = account_onboarding.account_from_entry(entry)
    if not _pool.add_account(account):
        return {"runtime_added": False, "runtime_reason": "pool_rejected"}

    if _keepers is None or _video_clients is None:
        _startup_set_account_status(account_id, "pending_restart", ready=False)
        return {"runtime_added": True, "runtime_reason": "restart_required_for_clients"}

    try:
        from flow_bot import FlowHttpClient, SessionKeeper

        keeper = SessionKeeper(
            account_id=account.id,
            profile_dir=account.profile_dir,
            browser_proxy_url=account.browser_proxy_url,
            api_proxy_url=account.api_proxy_url,
        )
        _keepers[account.id] = keeper
        _video_clients[account.id] = FlowHttpClient(keeper)
        _startup_set_account_status(account.id, "pending", ready=False)
        asyncio.create_task(_warm_hot_added_account(account.id, keeper))
        return {"runtime_added": True, "runtime_reason": "warming"}
    except Exception as exc:  # noqa: BLE001
        log.warning("hot-add client setup failed for %s: %s", account_id, exc.__class__.__name__, exc_info=True)
        if _pool is not None:
            _pool.set_runtime_ready(account_id, False, "pending_restart")
        _startup_set_account_status(account_id, "pending_restart", ready=False, error=exc.__class__.__name__)
        return {"runtime_added": True, "runtime_reason": "restart_required_for_clients"}


async def _cleanup_onboard_sessions() -> None:
    now = time.time()
    expired = [
        session_id
        for session_id, item in list(_onboard_sessions.items())
        if now - float(item.get("updated_at", item.get("created_at", now))) > ONBOARD_SESSION_TTL_SEC
    ]
    for session_id in expired:
        item = _onboard_sessions.pop(session_id, None)
        session = item.get("session") if item else None
        if session is not None:
            await session.close()


def _store_onboard_session(session, **meta) -> str:
    session_id = secrets.token_urlsafe(18)
    now = time.time()
    _onboard_sessions[session_id] = {
        "session": session,
        "created_at": now,
        "updated_at": now,
        **meta,
    }
    return session_id


def _get_onboard_session(session_id: str):
    item = _onboard_sessions.get(session_id)
    if not item:
        return None
    item["updated_at"] = time.time()
    return item.get("session")


def _get_onboard_item(session_id: str) -> dict | None:
    item = _onboard_sessions.get(session_id)
    if item:
        item["updated_at"] = time.time()
    return item


def _wipe_profile_dir(profile_dir: str) -> None:
    """Удалить каталог профиля (для пересоздания аккаунта).

    Защита: удаляем только пути, чей basename содержит ``google_profile`` —
    чтобы случайно не снести произвольный каталог."""
    import shutil
    try:
        p = (profile_dir or "").strip()
        if not p:
            return
        base = os.path.basename(os.path.normpath(p))
        if "google_profile" not in base:
            log.warning("refusing to wipe non-profile dir: %s", p)
            return
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
            log.info("wiped profile dir for replace: %s", p)
    except Exception:
        log.warning("_wipe_profile_dir failed for %s", profile_dir, exc_info=True)


async def _drop_onboard_session(session_id: str) -> None:
    item = _onboard_sessions.pop(session_id, None)
    session = item.get("session") if item else None
    if session is not None:
        await session.close()


def _safe_onboard_session_result(result: dict, session_id: str | None, session=None) -> dict:
    status = str(result.get("status") or "login_failed")
    data = {
        "ok": bool(result.get("ok")),
        "status": status,
        "reason": _safe_reason(str(result.get("reason") or status)),
        "needs_2fa": status == "needs_2fa",
        "ready_to_add": status == "active",
    }
    if session is not None:
        data.update({
            "account_id": session.account_id,
            "profile_dir": session.profile_dir,
            "proxy": account_onboarding.proxy_public_label(session.proxy_url),
        })
    if session_id:
        data["session_id"] = session_id
    return data


async def handle_account_onboard_start_post(request: web.Request) -> web.Response:
    await _cleanup_onboard_sessions()
    body = await _body(request)
    if body is None:
        return _json({"error": "invalid JSON body"}, 400)
    if body.get("confirm_login") is not True:
        return _json({"error": "confirm_login=true required"}, 400)

    account_id = str(body.get("id") or body.get("account_id") or "").strip()
    email = str(body.get("email") or "").strip()
    password = str(body.get("password") or "")
    proxy_url = str(body.get("proxy") or body.get("proxy_url") or "").strip()
    profile_dir = str(body.get("profile_dir") or "").strip() or None
    mode = str(body.get("mode") or "add").strip().lower()
    relogin = mode == "relogin"
    # «Пересоздать» существующий id/профиль: чистим старый профиль и логинимся
    # заново, вместо того чтобы просто запретить добавление.
    replace = body.get("confirm_replace") is True and not relogin
    try:
        timeout_sec = max(30, min(int(body.get("timeout_sec", 180)), 300))
    except (TypeError, ValueError):
        timeout_sec = 180

    pool_acc = _pool.get(account_id) if (_pool is not None and account_id) else None
    if relogin:
        if pool_acc is None:
            return _json({"ok": False, "status": "account_not_found", "error": "account_not_found"}, 404)
        profile_dir = pool_acc.profile_dir
        proxy_url = proxy_url or pool_acc.browser_proxy_url or pool_acc.api_proxy_url or ""

    try:
        proxy_label = account_onboarding.proxy_public_label(proxy_url)
    except account_onboarding.AccountOnboardingError:
        proxy_label = "(invalid)"
    try:
        default_profile = account_onboarding.default_profile_dir(account_id) if account_id else ""
    except account_onboarding.AccountOnboardingError:
        default_profile = ""
    safe_new = {
        "id": account_id,
        "profile_dir": profile_dir or default_profile,
        "proxy": proxy_label,
    }
    try:
        if relogin:
            await _close_runtime_account(account_id)
        if replace:
            # Чистим старый профиль и старую .env-запись → логин с нуля.
            await _close_runtime_account(account_id)
            _wipe_profile_dir(profile_dir or account_onboarding.default_profile_dir(account_id))
            try:
                account_onboarding.remove_flow_account_from_env(account_id)
            except account_onboarding.AccountOnboardingError:
                pass
        session, result = await account_onboarding.start_google_flow_login(
            account_id=account_id,
            email=email,
            password=password,
            proxy_url=proxy_url,
            profile_dir=profile_dir or account_onboarding.default_profile_dir(account_id),
            timeout_sec=timeout_sec,
            allow_existing_profile=relogin or replace,
            skip_account_exists=relogin or replace,
        )
    except account_onboarding.AccountOnboardingError as exc:
        status = 409 if exc.code in {"account_exists", "profile_exists"} else 400
        _audit(request, "account.onboard_start", new=safe_new, result=exc.code)
        payload = {"ok": False, "status": exc.code, "error": exc.code}
        if exc.code == "account_exists":
            payload["can_relogin"] = True
        # Любой конфликт id/профиля можно разрешить пересозданием.
        if exc.code in {"account_exists", "profile_exists"}:
            payload["can_replace"] = True
        return _json(payload, status)
    except Exception as exc:  # noqa: BLE001 - admin endpoint must return JSON
        log.warning("account onboard start failed for %s: %s", account_id, exc.__class__.__name__, exc_info=True)
        _audit(request, "account.onboard_start", new=safe_new, result="error")
        return _json({"ok": False, "status": "login_failed", "error": exc.__class__.__name__}, 500)

    session_id = _store_onboard_session(session, mode="relogin" if relogin else "add", email=email) if session is not None else None
    response = _safe_onboard_session_result(result, session_id, session)
    response["mode"] = "relogin" if relogin else "add"
    try:
        metrics.log_event(
            "account_onboard_start",
            source=account_id,
            payload={"status": response.get("status"), "proxy": safe_new["proxy"]},
        )
    except Exception:
        log.warning("account_onboard_start metrics log failed", exc_info=True)
    _audit(request, "account.onboard_start", new=safe_new, result=str(response.get("status") or "login_failed"))
    return _json(response)


async def handle_account_onboard_2fa_post(request: web.Request) -> web.Response:
    await _cleanup_onboard_sessions()
    body = await _body(request)
    if body is None:
        return _json({"error": "invalid JSON body"}, 400)
    session_id = str(body.get("session_id") or "").strip()
    code = str(body.get("code") or body.get("two_fa_code") or "").strip()
    session = _get_onboard_session(session_id)
    if session is None:
        return _json({"ok": False, "status": "session_expired", "error": "session_expired"}, 404)
    try:
        result = await session.submit_2fa_code(code)
    except account_onboarding.AccountOnboardingError as exc:
        _audit(request, "account.onboard_2fa", new={"id": session.account_id}, result=exc.code)
        return _json({"ok": False, "status": exc.code, "error": exc.code}, 400)
    except Exception as exc:  # noqa: BLE001
        log.warning("account onboard 2fa failed for %s: %s", session.account_id, exc.__class__.__name__, exc_info=True)
        _audit(request, "account.onboard_2fa", new={"id": session.account_id}, result="error")
        return _json({"ok": False, "status": "login_failed", "error": exc.__class__.__name__}, 500)
    response = _safe_onboard_session_result(result, session_id, session)
    _audit(request, "account.onboard_2fa", new={"id": session.account_id}, result=str(response.get("status")))
    return _json(response)


async def handle_account_onboard_complete_post(request: web.Request) -> web.Response:
    await _cleanup_onboard_sessions()
    body = await _body(request)
    if body is None:
        return _json({"error": "invalid JSON body"}, 400)
    if body.get("confirm_add") is not True:
        return _json({"error": "confirm_add=true required"}, 400)
    session_id = str(body.get("session_id") or "").strip()
    item = _get_onboard_item(session_id)
    session = item.get("session") if item else None
    if session is None:
        return _json({"ok": False, "status": "session_expired", "error": "session_expired"}, 404)
    mode = str(item.get("mode") or "add") if item else "add"
    safe_new = {
        "id": session.account_id,
        "profile_dir": session.profile_dir,
        "proxy": account_onboarding.proxy_public_label(session.proxy_url),
    }
    try:
        if mode == "relogin":
            if session.status != "active":
                raise account_onboarding.AccountOnboardingError("login_not_active")
            result = {
                "ok": True,
                "account_id": session.account_id,
                "profile_dir": session.profile_dir,
                "proxy": account_onboarding.proxy_public_label(session.proxy_url),
                "status": "active",
                "reason": "relogin_complete",
                "env_updated": False,
                "restart_required": False,
            }
        else:
            result = account_onboarding.complete_google_flow_login(session)
    except account_onboarding.AccountOnboardingError as exc:
        status = 409 if exc.code == "account_exists" else 400
        _audit(request, "account.onboard_complete", new=safe_new, result=exc.code)
        return _json({"ok": False, "status": exc.code, "error": exc.code}, status)
    except PermissionError:
        _audit(request, "account.onboard_complete", new=safe_new, result="env_permission_denied")
        return _json({"ok": False, "status": "env_permission_denied", "error": "env_permission_denied"}, 500)
    except Exception as exc:  # noqa: BLE001
        log.warning("account onboard complete failed for %s: %s", session.account_id, exc.__class__.__name__, exc_info=True)
        _audit(request, "account.onboard_complete", new=safe_new, result="error")
        return _json({"ok": False, "status": "login_failed", "error": exc.__class__.__name__}, 500)

    _set_account_email(session.account_id, str(item.get("email") or ""))
    await _drop_onboard_session(session_id)
    if mode == "relogin":
        result.update(_start_runtime_account(session.account_id))
    else:
        result.update(_try_hot_add_account(
            str(result.get("account_id") or session.account_id),
            str(result.get("profile_dir") or session.profile_dir),
            session.proxy_url,
        ))
    try:
        metrics.log_event(
            "account_onboard",
            source=session.account_id,
            payload={
                "status": result.get("status"),
                "env_updated": bool(result.get("env_updated")),
                "runtime_added": bool(result.get("runtime_added")),
                "proxy": safe_new["proxy"],
            },
        )
    except Exception:
        log.warning("account_onboard metrics log failed", exc_info=True)
    _audit(
        request,
        "account.onboard_complete",
        new={
            "id": result.get("account_id") or session.account_id,
            "profile_dir": result.get("profile_dir") or session.profile_dir,
            "proxy": safe_new["proxy"],
            "status": result.get("status"),
            "env_updated": result.get("env_updated"),
            "runtime_added": result.get("runtime_added"),
        },
        result="ok",
    )
    return _json(result)


async def handle_account_onboard_post(request: web.Request) -> web.Response:
    body = await _body(request)
    if body is None:
        return _json({"error": "invalid JSON body"}, 400)
    if body.get("confirm_login") is not True:
        return _json({"error": "confirm_login=true required"}, 400)

    account_id = str(body.get("id") or body.get("account_id") or "").strip()
    email = str(body.get("email") or "").strip()
    password = str(body.get("password") or "")
    totp_secret = str(body.get("totp_secret") or body.get("totp") or "")
    proxy_url = str(body.get("proxy") or body.get("proxy_url") or "").strip()
    profile_dir = str(body.get("profile_dir") or "").strip() or None
    try:
        timeout_sec = max(30, min(int(body.get("timeout_sec", 180)), 300))
    except (TypeError, ValueError):
        timeout_sec = 180

    try:
        proxy_label = account_onboarding.proxy_public_label(proxy_url)
    except account_onboarding.AccountOnboardingError:
        proxy_label = "(invalid)"
    try:
        default_profile = account_onboarding.default_profile_dir(account_id) if account_id else ""
    except account_onboarding.AccountOnboardingError:
        default_profile = ""
    safe_new = {
        "id": account_id,
        "profile_dir": profile_dir or default_profile,
        "proxy": proxy_label,
    }
    try:
        result = await account_onboarding.onboard_google_flow_account(
            account_id=account_id,
            email=email,
            password=password,
            totp_secret=totp_secret,
            proxy_url=proxy_url,
            profile_dir=profile_dir,
            timeout_sec=timeout_sec,
        )
    except account_onboarding.AccountOnboardingError as exc:
        status = 409 if exc.code in {"account_exists", "profile_exists"} else 400
        _audit(request, "account.onboard", new=safe_new, result=exc.code)
        return _json({"ok": False, "status": exc.code, "error": exc.code}, status)
    except PermissionError:
        _audit(request, "account.onboard", new=safe_new, result="env_permission_denied")
        return _json({"ok": False, "status": "env_permission_denied", "error": "env_permission_denied"}, 500)
    except Exception as exc:  # noqa: BLE001 - admin endpoint must return JSON
        log.warning("account onboard failed for %s: %s", account_id, exc.__class__.__name__, exc_info=True)
        _audit(request, "account.onboard", new=safe_new, result="error")
        return _json({"ok": False, "status": "login_failed", "error": exc.__class__.__name__}, 500)

    if result.get("ok"):
        result.update(_try_hot_add_account(
            str(result.get("account_id") or account_id),
            str(result.get("profile_dir") or safe_new["profile_dir"]),
            proxy_url,
        ))
    try:
        metrics.log_event(
            "account_onboard",
            source=str(result.get("account_id") or account_id),
            payload={
                "status": result.get("status"),
                "env_updated": bool(result.get("env_updated")),
                "runtime_added": bool(result.get("runtime_added")),
                "proxy": safe_new["proxy"],
            },
        )
    except Exception:
        log.warning("account_onboard metrics log failed", exc_info=True)
    _audit(
        request,
        "account.onboard",
        new={
            "id": result.get("account_id") or account_id,
            "profile_dir": result.get("profile_dir") or safe_new["profile_dir"],
            "proxy": safe_new["proxy"],
            "status": result.get("status"),
            "env_updated": result.get("env_updated"),
            "runtime_added": result.get("runtime_added"),
        },
        result="ok" if result.get("ok") else str(result.get("status") or "login_failed"),
    )
    return _json(result)


def _account_client(account_id: str):
    if not _video_clients or account_id not in _video_clients:
        return None
    return _video_clients[account_id]


async def handle_account_test_image_post(request: web.Request) -> web.Response:
    account_id = request.match_info["id"]
    client = _account_client(account_id)
    if client is None:
        return _json({"error": f"account {account_id!r} not found"}, 404)
    body = await _body(request)
    if body is None:
        return _json({"error": "invalid JSON body"}, 400)
    if body.get("confirm_spend") is not True:
        return _json({"error": "confirm_spend=true required"}, 400)
    prompt = str(body.get("prompt") or "simple studio product photo on a clean white background").strip()[:500]
    prompt = prompt or "simple studio product photo on a clean white background"
    try:
        from flow_core import result_pairs

        started = time.time()
        result = await client.generate_images(
            prompt=prompt,
            aspect_ratio="square",
            num_images=1,
            allow_browser_fallback=False,
        )
        image_count = len(result_pairs(result)) if isinstance(result, dict) else 0
        error = result.get("error") if isinstance(result, dict) else "bad_response"
        ok = image_count > 0 and not error
        reason = f"{image_count} image accepted" if ok else _safe_reason(error or "no_image_returned")
        payload = {
            "account": account_id,
            "ok": ok,
            "status": "success" if ok else "fail",
            "reason": reason,
            "image_count": image_count,
            "duration_ms": int((time.time() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("account image test failed for %s: %s", account_id, exc.__class__.__name__, exc_info=True)
        payload = {
            "account": account_id,
            "ok": False,
            "status": "fail",
            "reason": exc.__class__.__name__,
        }
    try:
        metrics.log_event("account_image_test", source=account_id, payload=payload)
    except Exception:
        log.warning("account_image_test metrics log failed", exc_info=True)
    _audit(request, "account.test_image", new={"id": account_id, "ok": payload.get("ok")})
    return _json(payload)


async def handle_account_test_video_post(request: web.Request) -> web.Response:
    account_id = request.match_info["id"]
    client = _account_client(account_id)
    if client is None:
        return _json({"error": f"account {account_id!r} not found"}, 404)
    body = await _body(request)
    if body is None:
        return _json({"error": "invalid JSON body"}, 400)
    if body.get("confirm_spend") is not True:
        return _json({"error": "confirm_spend=true required"}, 400)
    prompt = str(body.get("prompt") or "simple cinematic shot of a calm sunrise over a lake").strip()[:500]
    prompt = prompt or "simple cinematic shot of a calm sunrise over a lake"
    try:
        started = time.time()
        result = await client.video_transport_ab_test(
            prompt=prompt,
            model_key=str(body.get("model") or "omni-flash-4s"),
            aspect=str(body.get("aspect") or "landscape"),
            order="direct_first",
            pause_sec=0,
            transports=["direct_http"],
        )
        arms = result.get("arms", []) if isinstance(result, dict) else []
        first = arms[0] if arms and isinstance(arms[0], dict) else {}
        ok = bool(first.get("ok"))
        reason = "HTTP 200 accepted" if ok else _safe_reason(
            first.get("error") or first.get("body_preview") or f"status={first.get('status')}"
        )
        payload = {
            "account": account_id,
            "ok": ok,
            "status": "success" if ok else "fail",
            "reason": reason,
            "http_status": first.get("status"),
            "duration_ms": int((time.time() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("account video test failed for %s: %s", account_id, exc.__class__.__name__, exc_info=True)
        payload = {
            "account": account_id,
            "ok": False,
            "status": "fail",
            "reason": exc.__class__.__name__,
        }
    try:
        metrics.log_event("account_video_test", source=account_id, payload=payload)
    except Exception:
        log.warning("account_video_test metrics log failed", exc_info=True)
    _audit(request, "account.test_video", new={"id": account_id, "ok": payload.get("ok")})
    return _json(payload)


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
        "status": status if status in {"open", "in_work", "done", "replied", "closed", "all", "done4you"} else "open",
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
    if status not in {"open", "in_work", "done", "replied", "closed"}:
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

def register_admin_routes(
    app: web.Application,
    pool: "AccountPool",
    keepers: dict | None = None,
    video_clients: dict | None = None,
    startup_state: dict | None = None,
) -> None:
    """Register all /api/admin/* routes into an existing aiohttp Application.

    ``keepers`` (account_id -> SessionKeeper) is optional and used by live
    account diagnostics. ``video_clients`` enables costly admin-only video A/B.
    """
    global _pool, _keepers, _video_clients, _startup_state
    _pool = pool
    _keepers = keepers
    _video_clients = video_clients
    _startup_state = startup_state
    r = app.router
    r.add_get ("/api/admin/ping",                      handle_ping)
    r.add_get ("/api/admin/ops",                       handle_ops_get)
    r.add_get ("/api/admin/stats",                     handle_stats)
    r.add_get ("/api/admin/log",                       handle_log)
    r.add_get ("/api/admin/accounts",                  handle_accounts_get)
    r.add_post("/api/admin/accounts/onboard/start",    handle_account_onboard_start_post)
    r.add_post("/api/admin/accounts/onboard/2fa",      handle_account_onboard_2fa_post)
    r.add_post("/api/admin/accounts/onboard/complete", handle_account_onboard_complete_post)
    r.add_post("/api/admin/accounts/onboard",          handle_account_onboard_post)
    r.add_post("/api/admin/accounts/{id}/enable",      handle_account_enable)
    r.add_post("/api/admin/accounts/{id}/disable",     handle_account_disable)
    r.add_post("/api/admin/accounts/{id}/video",       handle_account_video)
    r.add_post("/api/admin/accounts/{id}/reset",       handle_account_reset)
    r.add_post("/api/admin/accounts/{id}/delete",      handle_account_delete)
    r.add_post("/api/admin/accounts/{id}/test-image",  handle_account_test_image_post)
    r.add_post("/api/admin/accounts/{id}/test-video",  handle_account_test_video_post)
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
    r.add_get ("/api/admin/video-health",              handle_video_health)
    r.add_post("/api/admin/video-ab",                  handle_video_ab_post)
    r.add_post("/api/admin/agent-probe",               handle_agent_probe_post)
    r.add_post("/api/admin/agent-action-scan",         handle_agent_action_scan_post)
    r.add_post("/api/admin/agent-capture",             handle_agent_capture_post)
    r.add_post("/api/admin/agent-sessions",            handle_agent_sessions_post)
    r.add_get ("/api/admin/proxy-check",               handle_proxy_check)
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
    log.info("Admin API registered on /api/admin/* (%d routes)", 43)
