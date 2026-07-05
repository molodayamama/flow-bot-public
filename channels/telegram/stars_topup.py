"""Telegram Stars top-up callback for the Telegram adapter (Phase 11 core split).

Sends a Telegram ``send_invoice`` for the chosen credit pack (currency ``XTR`` =
Telegram Stars). Best-effort: on Telegram-side failure shows a friendly
"оплата временно недоступна" message. The charge/refund and pack-amount logic
lives in :mod:`billing.pricing`; this is the Telegram glue.

Extracted out of the flow_bot composition root; runtime singletons are injected
via :class:`StarsTopupDeps` so this module never imports flow_bot. Telegram
adapter code (uses aiogram), not a platform-neutral core module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aiogram import types


@dataclass(frozen=True)
class StarsTopupDeps:
    credit_pack: Callable[[str], dict | None]
    admin_ids: frozenset[int]
    bot_send_invoice: Callable[..., Awaitable[Any]]
    log: Any


class StarsTopup:
    def __init__(self, deps: StarsTopupDeps) -> None:
        self._d = deps

    async def start_topup(self, callback: types.CallbackQuery, user_id: int, pack_id: str) -> None:
        """Выставить счёт в Telegram Stars за выбранный пакет кредитов."""
        d = self._d
        p = d.credit_pack(pack_id)
        if not p:
            await callback.answer("Пакет не найден", show_alert=True)
            return
        if p.get("test") and user_id not in d.admin_ids:
            await callback.answer("Пакет не найден", show_alert=True)
            return
        await callback.answer()
        try:
            prices = [types.LabeledPrice(label=f"{p['credits']} кредитов", amount=p["stars"])]
            await d.bot_send_invoice(
                chat_id=callback.message.chat.id,
                title=f"{p['credits']} кредитов",
                description=f"Пополнение баланса на {p['credits']} кредитов",
                payload=f"credits:{pack_id}:{user_id}",
                currency="XTR",  # Telegram Stars
                prices=prices,
            )
        except Exception:
            d.log.exception("send_invoice failed")
            await callback.message.answer(
                "⚠️ Оплата временно недоступна. Попробуйте позже."
            )
