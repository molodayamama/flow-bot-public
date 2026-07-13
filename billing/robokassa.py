"""Robokassa payment URL and webhook handlers.

Extracted so the app entry point only owns runtime wiring.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, urlencode

import aiohttp
from aiohttp import web


def _private_reference(secret: str, namespace: str, value: str) -> str:
    """Stable, non-reversible event correlation id keyed by payment config."""
    payload = f"{namespace}:{value}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()[:20]


@dataclass(frozen=True)
class RobokassaConfig:
    merchant_login: str
    password1: str
    password2: str
    hash_algo: str
    inc_curr_label: str
    test: bool
    pay_url: str
    scope: str
    consumer_result_url: str
    seller_result_url: str
    consumer_bot_username: str
    seller_bot_username: str


@dataclass(frozen=True)
class RobokassaWebDeps:
    config: RobokassaConfig
    is_configured: Callable[[], bool]
    credit_pack: Callable[[str], dict[str, Any] | None]
    robokassa_pack_amount: Callable[[str], str]
    payment_signature: Callable[..., str]
    result_signature: Callable[..., str]
    clean_scope: Callable[[str], str]
    add_credits: Callable[[int, int], int]
    settle_external_payment: Callable[..., tuple[str, int | None]]
    metrics: Any
    log: Any
    maybe_apply_referral_rewards: Callable[..., Any]
    notify_success: Callable[[int, int, int], Awaitable[Any]]
    forward_result: Callable[[str, dict[str, str]], Awaitable[web.Response]]


def new_inv_id() -> int:
    return int(time.time() * 1000) * 1000 + random.randint(100, 999)


def receipt_json(pack_id: str, out_sum: str, credits: int) -> str:
    """Fiscal receipt JSON for Robocheki SMZ/NPD."""

    receipt = {
        "items": [
            {
                "name": f"Пополнение баланса ФотоЖаб — {credits} кредитов",
                "quantity": 1,
                "sum": round(float(out_sum), 2),
                "payment_method": "full_payment",
                "payment_object": "service",
                "tax": "none",
            }
        ]
    }
    return json.dumps(receipt, ensure_ascii=False, separators=(",", ":"))


def payment_url(
    user_id: int,
    pack_id: str,
    inv_id: int,
    config: RobokassaConfig,
    *,
    credit_pack: Callable[[str], dict[str, Any] | None],
    robokassa_pack_amount: Callable[[str], str],
    payment_signature: Callable[..., str],
    channel: str = "",
) -> str:
    p = credit_pack(pack_id)
    if not p:
        raise ValueError(f"unknown pack: {pack_id!r}")
    out_sum = robokassa_pack_amount(pack_id)
    shp = {"Shp_bot": config.scope, "Shp_pack": pack_id, "Shp_user": int(user_id)}
    normalized_channel = str(channel or "").strip().lower()
    if normalized_channel:
        if normalized_channel not in {"web", "max", "telegram"}:
            raise ValueError("unsupported payment channel")
        shp["Shp_channel"] = normalized_channel
    raw_receipt = receipt_json(pack_id, out_sum, int(p["credits"]))
    signature = payment_signature(
        config.merchant_login,
        out_sum,
        inv_id,
        config.password1,
        shp_params=shp,
        receipt=raw_receipt,
        algorithm=config.hash_algo,
    )
    params = {
        "MerchantLogin": config.merchant_login,
        "OutSum": out_sum,
        "InvId": str(inv_id),
        "Description": f"PhotoZhab credits: {p['credits']}",
        "SignatureValue": signature,
        "Culture": "ru",
        "Encoding": "utf-8",
        **shp,
    }
    if config.inc_curr_label:
        params["IncCurrLabel"] = config.inc_curr_label
    if config.test:
        params["IsTest"] = "1"
    return config.pay_url + "?" + urlencode(params) + "&Receipt=" + quote(raw_receipt, safe="")


async def request_data(request: web.Request) -> dict[str, str]:
    data = {k: str(v) for k, v in request.query.items()}
    if request.method == "POST":
        post = await request.post()
        data.update({k: str(v) for k, v in post.items()})
    return data


def param(data: dict[str, str], *names: str) -> str:
    lower = {k.lower(): v for k, v in data.items()}
    for name in names:
        if name in data:
            return data[name]
        value = lower.get(name.lower())
        if value is not None:
            return value
    return ""


def shp_params(data: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in data.items() if k.startswith("Shp_")}


def amount_matches(actual: str, expected: str) -> bool:
    try:
        return abs(Decimal(actual) - Decimal(expected)) <= Decimal("0.01")
    except (InvalidOperation, TypeError):
        return False


def internal_user_id(value: str) -> int | None:
    """Parse a signed SQLite user id without accepting floats or overflow."""
    raw = str(value or "").strip()
    if not re.fullmatch(r"-?[1-9][0-9]*", raw):
        return None
    parsed = int(raw)
    if not -(2**63) <= parsed <= 2**63 - 1:
        return None
    return parsed


def target_scope(shp: dict[str, str], *, clean_scope: Callable[[str], str]) -> str:
    # Old Robokassa invoices did not carry Shp_bot; keep them on consumer.
    return clean_scope(shp.get("Shp_bot", "") or "consumer")


def result_url_for_scope(scope: str, config: RobokassaConfig) -> str:
    return config.seller_result_url if scope == "seller" else config.consumer_result_url


def provider_payment_id(inv_id: str, scope: str, *, legacy: bool = False) -> str:
    return f"robokassa:{inv_id}" if legacy else f"robokassa:{scope}:{inv_id}"


async def forward_result(
    target_scope_value: str,
    data: dict[str, str],
    *,
    config: RobokassaConfig,
    log: Any,
) -> web.Response:
    target_url = result_url_for_scope(target_scope_value, config)
    if not target_url:
        return web.Response(status=503, text="route unavailable")
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(target_url, data=data) as resp:
                text = await resp.text()
                return web.Response(status=resp.status, text=text)
    except Exception:
        log.exception("robokassa route forward failed target=%s", target_scope_value)
        return web.Response(status=503, text="route unavailable")


def bot_username_for_scope(scope: str, config: RobokassaConfig) -> str:
    if scope == "seller":
        return config.seller_bot_username or "photozhab_wb_bot"
    return config.consumer_bot_username or "photozhab_bot"


async def handle_result(request: web.Request, deps: RobokassaWebDeps) -> web.Response:
    if not deps.is_configured():
        return web.Response(status=503, text="Robokassa is not configured")
    config = deps.config
    data = await request_data(request)
    out_sum = param(data, "OutSum")
    inv_id = param(data, "InvId", "InvID")
    signature = param(data, "SignatureValue")
    shp = shp_params(data)
    expected = deps.result_signature(
        out_sum,
        inv_id,
        config.password2,
        shp_params=shp,
        algorithm=config.hash_algo,
    )
    if not signature or signature.lower() != expected.lower():
        deps.log.warning("robokassa bad signature")
        return web.Response(status=400, text="bad signature")

    scope = target_scope(shp, clean_scope=deps.clean_scope)
    if scope != config.scope:
        return await deps.forward_result(scope, data)

    pack_id = shp.get("Shp_pack", "")
    user_raw = shp.get("Shp_user", "")
    user_id = internal_user_id(user_raw)
    p = deps.credit_pack(pack_id)
    if not p or user_id is None or not inv_id:
        deps.log.warning("robokassa unmatched payment")
        deps.metrics.log_event(
            "robokassa_unmatched_payment",
            user_id=user_id or 0,
            source="robokassa",
            payload={
                "inv_ref": _private_reference(config.password2, "invoice", inv_id),
                "user_ref": _private_reference(config.password2, "user", user_raw),
                "pack_known": p is not None,
                "invoice_present": bool(inv_id),
                "reason": "bad_order",
            },
        )
        return web.Response(status=400, text="bad order")
    expected_amount = deps.robokassa_pack_amount(pack_id)
    if not amount_matches(out_sum, expected_amount):
        deps.log.warning("robokassa amount mismatch")
        return web.Response(status=400, text="bad amount")

    pay_id = provider_payment_id(inv_id, scope, legacy=("Shp_bot" not in shp))
    new_balance: int | None = None
    if user_id < 0:
        tx_status, new_balance = deps.settle_external_payment(
            provider="robokassa",
            provider_payment_id=pay_id,
            user_id=user_id,
            package_id=pack_id,
            amount_rub=float(Decimal(out_sum)),
            stars_amount=0,
            credits_issued=p["credits"],
            status="paid",
        )
    else:
        tx_status = deps.metrics.record_transaction_status(
            provider="robokassa",
            provider_payment_id=pay_id,
            user_id=user_id,
            package_id=pack_id,
            amount_rub=float(Decimal(out_sum)),
            stars_amount=0,
            credits_issued=p["credits"],
            status="paid",
        )
    if tx_status == "duplicate":
        return web.Response(text=f"OK{inv_id}")
    if tx_status == "error":
        return web.Response(status=500, text="temporary error")

    if new_balance is None:
        new_balance = deps.add_credits(user_id, p["credits"])
    deps.metrics.log_event(
        "payment_success",
        user_id=user_id,
        source="robokassa",
        payload={"pack": pack_id, "amount_rub": out_sum, "credits": p["credits"]},
    )
    deps.maybe_apply_referral_rewards(
        user_id,
        stars_paid=p["stars"],
        credits_issued=p["credits"],
        pack_id=pack_id,
        provider_payment_id=pay_id,
    )
    await deps.notify_success(user_id, p["credits"], new_balance)
    return web.Response(text=f"OK{inv_id}")


async def status_page(request: web.Request, deps: RobokassaWebDeps, *, ok: bool) -> web.Response:
    data = await request_data(request)
    shp = shp_params(data)
    scope = target_scope(shp, clean_scope=deps.clean_scope)
    username = html.escape(bot_username_for_scope(scope, deps.config))
    is_external_identity = (internal_user_id(shp.get("Shp_user", "")) or 0) < 0
    channel = str(shp.get("Shp_channel", "")).strip().lower()
    if ok:
        title = "Оплата прошла"
        if channel == "web":
            body = "Баланс пополнится автоматически. Можно вернуться к генерации на сайте."
        elif is_external_identity:
            body = "Баланс пополнится автоматически. Можно вернуться в бот MAX."
        else:
            body = "Баланс пополнится автоматически. Можно вернуться в Telegram."
    else:
        title = "Оплата не завершена"
        body = "Деньги не списаны или платёж отменён. Вернитесь и попробуйте ещё раз."
    if channel == "web":
        return_link = "<p><a href='https://photozhab.ru/app.html'>Вернуться к генерации</a></p>"
    elif is_external_identity:
        return_link = ""
    else:
        return_link = f"<p><a href='https://t.me/{username}'>Открыть бота</a></p>"
    return web.Response(
        text=(
            "<!doctype html><meta charset='utf-8'>"
            f"<title>{html.escape(title)}</title>"
            "<body style='font-family:system-ui;max-width:560px;margin:48px auto;padding:0 20px'>"
            f"<h1>{html.escape(title)}</h1>"
            f"<p>{html.escape(body)}</p>"
            f"{return_link}"
            "</body>"
        ),
        content_type="text/html",
    )


async def success(request: web.Request, deps: RobokassaWebDeps) -> web.Response:
    return await status_page(request, deps, ok=True)


async def fail(request: web.Request, deps: RobokassaWebDeps) -> web.Response:
    return await status_page(request, deps, ok=False)


async def health(request: web.Request) -> web.Response:
    return web.Response(text="OK")


def register_routes(
    app: web.Application,
    *,
    is_configured: Callable[[], bool],
    result_handler: Callable[[web.Request], Awaitable[web.Response]],
    success_handler: Callable[[web.Request], Awaitable[web.Response]],
    fail_handler: Callable[[web.Request], Awaitable[web.Response]],
    health_handler: Callable[[web.Request], Awaitable[web.Response]],
) -> None:
    if not is_configured():
        return
    app.router.add_route("*", "/robokassa/result", result_handler)
    app.router.add_get("/robokassa/success", success_handler)
    app.router.add_post("/robokassa/success", success_handler)
    app.router.add_get("/robokassa/fail", fail_handler)
    app.router.add_post("/robokassa/fail", fail_handler)
    app.router.add_get("/robokassa/health", health_handler)
