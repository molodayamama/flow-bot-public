"""Credit-mutating admin/user commands router (Phase 6).

/promo, /addpromo, /grant, /refund - these move real user credit balances.
Bodies are byte-exact moves from the monolith with only deps.* substitutions;
every amount/limit/gate check is unchanged.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Collection, MutableMapping

from aiogram import Router, types
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command

import flow_copy


@dataclass(frozen=True)
class AdminCreditsDeps:
    """Injected gates, stores, bot, and alert/referral helpers."""

    workspace: Callable[[int], MutableMapping[str, Any]]
    admin_ids: Collection[int]
    owner_ids: Collection[int]
    metrics: Any
    credit_store: Any
    payment_store: Any
    bot: Any
    username: Callable[[Any], str | None]
    send_owner_alert: Callable[[str], Awaitable[Any]]
    clawback_referral_rewards: Callable[..., Any]
    log: Any


def create_router(deps: AdminCreditsDeps) -> Router:
    """Build the /promo /addpromo /grant /refund router."""

    router = Router(name="tg-admin-credits")

    @router.message(Command("promo"))
    async def cmd_promo(message: types.Message):
        """Ввести промокод: /promo КОД"""
        user_id = message.from_user.id
        parts = (message.text or "").split()
        if len(parts) < 2:
            await message.answer(flow_copy.msg("promo_ask"))
            deps.workspace(user_id)["await"] = "promo"
            return
        code = parts[1].strip()
        credits_got = deps.metrics.redeem_promo(code, user_id)
        if credits_got is None:
            await message.answer(flow_copy.msg("promo_invalid"))
            return
        balance = deps.credit_store.add(user_id, credits_got)
        deps.metrics.log_event("promo_redeemed", user_id=user_id, payload={"code": code, "credits": credits_got})
        uname = deps.username(message)
        uname_str = f"@{uname}" if uname else f"id {user_id}"
        asyncio.create_task(deps.send_owner_alert(
            f"🎟 <b>Промокод активирован</b>\n"
            f"Юзер: {uname_str}\n"
            f"Код: <code>{code.upper()}</code>  +{credits_got} кр."
        ))
        await message.answer(
            flow_copy.msg("promo_success", credits=credits_got, balance=balance),
            parse_mode="HTML",
        )


    @router.message(Command("addpromo"))
    async def cmd_addpromo(message: types.Message):
        """Владелец: создать промокод. /addpromo КОД КРЕДИТЫ [МАКС_ИСПОЛЬЗОВАНИЙ]"""
        if message.from_user.id not in deps.owner_ids:
            await message.answer(flow_copy.msg("admin_denied"))
            return
        parts = (message.text or "").split()
        if len(parts) < 3 or not parts[2].isdigit():
            await message.answer("Использование: /addpromo КОД КРЕДИТЫ [макс_использований]")
            return
        code = parts[1].upper().strip()
        credits = int(parts[2])
        max_uses = int(parts[3]) if len(parts) >= 4 and parts[3].isdigit() else 1
        if credits <= 0 or max_uses <= 0:
            await message.answer("Кредиты и количество использований должны быть > 0.")
            return
        ok = deps.metrics.create_promo_code(code, credits, max_uses, created_by=message.from_user.id)
        if ok:
            await message.answer(
                f"✅ Промокод <code>{code}</code> создан: <b>{credits} кр.</b>, "
                f"использований: {max_uses}.",
                parse_mode="HTML",
            )
        else:
            await message.answer(f"❌ Промокод <code>{code}</code> уже существует.", parse_mode="HTML")


    @router.message(Command("grant"))
    async def cmd_grant(message: types.Message):
        """Админ-команда: начислить кредиты пользователю. `/grant <user_id> <кредиты>`.

        Доступна только ID из deps.admin_ids (.env). Кредиты — реальные деньги
        (см. docs/MONETIZATION.md), поэтому команда закрыта для всех остальных.
        """
        if message.from_user.id not in deps.admin_ids:
            await message.answer(flow_copy.msg("admin_denied"))
            return
        parts = (message.text or "").split()
        if len(parts) != 3 or not parts[1].lstrip("-").isdigit() or not parts[2].lstrip("-").isdigit():
            await message.answer(flow_copy.msg("admin_usage"))
            return
        target = int(parts[1])
        amount = int(parts[2])
        if amount <= 0:
            await message.answer(flow_copy.msg("admin_usage"))
            return
        new_balance = deps.credit_store.add(target, amount)
        deps.log.info(f"🎁 Админ {message.from_user.id} начислил {amount} кр пользователю {target}")
        await message.answer(
            flow_copy.msg("admin_granted", credits=amount, target=target, balance=new_balance)
        )
        # Уведомим самого пользователя, если это не админ себе.
        if target != message.from_user.id:
            try:
                await deps.bot.send_message(
                    target, flow_copy.msg("topup_done", credits=amount, balance=new_balance)
                )
            except TelegramForbiddenError:
                deps.metrics.mark_user_blocked(target)
            except Exception:
                pass


    @router.message(Command("refund"))
    async def cmd_refund(message: types.Message):
        """Админ: вернуть звёзды за платёж. `/refund <user_id>` (последний платёж)
        или `/refund <user_id> <charge_id>`. Возвращает Stars через Telegram и
        списывает ранее начисленные кредиты.
        """
        if message.from_user.id not in deps.admin_ids:
            await message.answer(flow_copy.msg("admin_denied"))
            return
        parts = (message.text or "").split()
        if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
            await message.answer("Использование: /refund <user_id> [charge_id]")
            return
        target = int(parts[1])
        if len(parts) >= 3:
            rec = deps.payment_store.find_by_charge(parts[2])
        else:
            rec = deps.payment_store.last_for_user(target)
        if not rec:
            await message.answer("Платёж не найден (или уже возвращён).")
            return
        if rec.get("refunded"):
            await message.answer("Этот платёж уже возвращён.")
            return

        try:
            await deps.bot.refund_star_payment(
                user_id=rec["user_id"],
                telegram_payment_charge_id=rec["charge_id"],
            )
        except Exception:
            deps.log.exception("refund_star_payment failed")
            await message.answer("⚠️ Telegram отклонил возврат (срок/ID). Проверьте charge_id.")
            return

        deps.payment_store.mark_refunded(rec["charge_id"])
        # Снимаем начисленные кредиты (не уходим в минус).
        take = min(rec["credits"], deps.credit_store.balance(rec["user_id"]))
        if take > 0:
            deps.credit_store.charge(rec["user_id"], take)
        deps.metrics.log_event("credits_refunded", user_id=rec["user_id"], source="admin_refund",
                          payload={"amount": take, "charge_id": rec["charge_id"]})
        # Откатываем реферальные награды, привязанные к этому платежу.
        deps.clawback_referral_rewards(rec["user_id"], rec["charge_id"])
        deps.log.info(f"↩️ Рефанд {rec['stars']} Stars пользователю {rec['user_id']} (charge {rec['charge_id']})")
        await message.answer(
            f"↩️ Возвращено {rec['stars']} Stars пользователю {rec['user_id']}. "
            f"Списано {take} кр (начислялось {rec['credits']})."
        )
        if target != message.from_user.id:
            try:
                await deps.bot.send_message(
                    target, f"↩️ Возврат {rec['stars']} Stars выполнен. Списано {take} кредитов."
                )
            except Exception:
                pass

    return router
