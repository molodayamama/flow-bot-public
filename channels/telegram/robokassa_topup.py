"""Robokassa (СБП) top-up flow for the Telegram adapter (Phase 11 core split).

Two Telegram-facing pieces of the Robokassa payment flow:

* :meth:`RobokassaTopup.start_topup` — Stars-style callback that builds the
  payment URL and shows the inline "Оплатить через СБП/карту" button.
* :meth:`RobokassaTopup.notify_success` — best-effort "баланс пополнен" message
  with the main menu, tolerating forbidden/chat-not-found Telegram errors.

The charge/refund rule, signature checks, and result-url forwarding live in
:mod:`robokassa_billing`; this is the Telegram glue bound to that service.

Extracted out of the flow_bot composition root; runtime singletons are injected
via :class:`RobokassaTopupDeps` so this module never imports flow_bot. Telegram
adapter code (uses aiogram), not a platform-neutral core module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aiogram import types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

import flow_copy


@dataclass(frozen=True)
class RobokassaTopupDeps:
    credit_pack: Callable[[str], dict | None]
    admin_ids: frozenset[int]
    is_configured: Callable[[], bool]
    new_inv_id: Callable[[], int]
    payment_url: Callable[[int, str, int], str]
    pack_amount: Callable[[str], int]
    rub_display: Callable[[int], str]
    menu_button: Callable[..., Any]
    bot_send_message: Callable[..., Awaitable[Any]]
    workspace: Callable[[int], dict]
    main_menu_kb: Callable[..., Any]
    metrics: Any
    log: Any


class RobokassaTopup:
    def __init__(self, deps: RobokassaTopupDeps) -> None:
        self._d = deps

    async def start_topup(self, callback: types.CallbackQuery, user_id: int, pack_id: str) -> None:
        d = self._d
        p = d.credit_pack(pack_id)
        if not p or (p.get("test") and user_id not in d.admin_ids):
            await callback.answer("Пакет не найден", show_alert=True)
            return
        if not d.is_configured():
            await callback.answer("Оплата СБП пока не настроена", show_alert=True)
            return
        inv_id = d.new_inv_id()
        try:
            pay_url = d.payment_url(user_id, pack_id, inv_id)
        except Exception:
            d.log.exception("robokassa payment url failed")
            await callback.answer("Оплата временно недоступна", show_alert=True)
            return
        await callback.answer()
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="Оплатить через СБП/карту", url=pay_url)],
            [d.menu_button("back", "m:pay:robo")],
        ])
        await callback.message.answer(
            f"Счёт на {p['credits']} кр. Сумма: {d.rub_display(d.pack_amount(pack_id))} ₽.\n"
            "После оплаты баланс пополнится автоматически.",
            reply_markup=kb,
        )

    async def notify_success(self, user_id: int, credits: int, balance: int) -> None:
        d = self._d
        try:
            await d.bot_send_message(
                user_id,
                flow_copy.msg("topup_done", credits=credits, balance=balance),
                parse_mode="HTML",
                reply_markup=d.main_menu_kb(show_repeat=bool(d.workspace(user_id).get("last"))),
            )
        except TelegramForbiddenError:
            d.metrics.mark_user_blocked(user_id)
        except TelegramBadRequest as exc:
            if "chat not found" in str(exc).lower():
                d.log.warning("robokassa success notify skipped: chat not found user_id=%s", user_id)
            else:
                d.log.exception("robokassa success notify bad request")
        except Exception:
            d.log.exception("robokassa success notify failed")
