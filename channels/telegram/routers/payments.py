"""Telegram Stars payment routers (Phase 6)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Awaitable

from aiogram import F, Router, types

import flow_copy


@dataclass(frozen=True)
class PaymentDeps:
    """Injected money-facing dependencies for Telegram Stars payments."""

    credit_pack: Callable[[str], dict | None]
    stars_to_rub: float
    credit_store: Any
    payment_store: Any
    metrics: Any
    log: Any
    username: Callable[[types.Message], str | None]
    maybe_apply_referral_rewards: Callable[..., Any]
    show_main_menu: Callable[..., Awaitable[Any]]


def create_router(deps: PaymentDeps) -> Router:
    """Build pre-checkout and successful-payment handlers."""

    router = Router(name="tg-payments")

    @router.pre_checkout_query()
    async def on_pre_checkout(query: types.PreCheckoutQuery):
        """Last guard before Stars are charged: accept only known credit packs."""
        payload = query.invoice_payload or ""
        parts = payload.split(":")
        ok = len(parts) >= 2 and parts[0] == "credits" and deps.credit_pack(parts[1]) is not None
        if not ok:
            deps.log.warning("pre_checkout отклонён: payload=%r", payload[:64])
        await query.answer(
            ok=ok,
            error_message=None if ok else "Пакет не найден — обновите меню и попробуйте снова.",
        )

    @router.message(F.successful_payment)
    async def on_successful_payment(message: types.Message):
        """Credit user balance after a successful Telegram Stars payment."""
        sp = message.successful_payment
        payload = sp.invoice_payload if sp else ""
        user_id = message.from_user.id
        pack_id = payload.split(":")[1] if payload.startswith("credits:") else ""
        p = deps.credit_pack(pack_id)
        if not p:
            deps.log.warning("Unknown payment payload: %s", payload)
            await message.answer("Платёж получен, но пакет не распознан. Напишите в поддержку.")
            return

        charge_id = getattr(sp, "telegram_payment_charge_id", "") or ""
        stars_paid = getattr(sp, "total_amount", p["stars"])
        provider_payment_id = charge_id or f"nocharge:{user_id}:{pack_id}:{message.message_id}"
        tx_status = deps.metrics.record_transaction_status(
            provider="telegram_stars",
            provider_payment_id=provider_payment_id,
            user_id=user_id,
            package_id=pack_id,
            amount_rub=round(stars_paid * deps.stars_to_rub, 2),
            stars_amount=stars_paid,
            credits_issued=p["credits"],
            status="paid",
        )
        if tx_status == "duplicate":
            deps.log.warning("💳 Дубль доставки платежа проигнорирован: %s", provider_payment_id)
            return
        if tx_status == "error":
            deps.log.error(
                "💳 metrics недоступны, зачисляю без дедуп-гарантии: %s",
                provider_payment_id,
            )

        new_balance = deps.credit_store.add(user_id, p["credits"])
        if charge_id:
            try:
                deps.payment_store.add(user_id, charge_id, stars_paid, p["credits"], pack_id)
            except Exception:
                deps.log.exception("payment_store.add failed")

        deps.metrics.log_event(
            "payment_success",
            user_id=user_id,
            username=deps.username(message),
            source="stars",
            payload={"pack": pack_id, "stars": stars_paid, "credits": p["credits"]},
        )
        deps.maybe_apply_referral_rewards(
            user_id,
            stars_paid=stars_paid,
            credits_issued=p["credits"],
            pack_id=pack_id,
            provider_payment_id=provider_payment_id,
        )
        deps.log.info(
            "💳 Оплата: +%s кр пользователю %s (баланс %s)",
            p["credits"],
            user_id,
            new_balance,
        )
        await message.answer(flow_copy.msg("topup_done", credits=p["credits"], balance=new_balance))
        await deps.show_main_menu(message, user_id=user_id)

    return router
