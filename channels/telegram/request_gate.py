"""Per-user request-lifecycle primitives for the Telegram adapter (Phase 11).

Two context managers used by every generation flow:

* :meth:`RequestGate.user_slot` — anti-abuse per-user concurrency + cooldown slot
  (one in-flight request per user; short early presses wait out the remainder).
* :meth:`RequestGate.credit_gate` — reserve credits on entry, refund on failure
  (charge-on-success). The charge/refund rule itself lives in
  ``billing/credit_gate.py``; this binds the Telegram balance UI to it.

Extracted out of the flow_bot composition root; runtime singletons and session
state are injected via :class:`RequestGateDeps` so this module never imports
flow_bot. Telegram adapter code (uses aiogram), not a platform-neutral core
module.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Callable

from aiogram import types

import flow_copy
from flow_core import action_price
from billing.credit_gate import NotEnoughCredits, open_credit_gate


class RateLimited(Exception):
    """Запрос отклонён: у пользователя уже выполняется другой запрос."""


@dataclass(frozen=True)
class RequestGateDeps:
    user_busy: dict
    user_last_request: dict
    busy_max_sec: int
    cooldown_sec: int
    max_auto_wait_sec: int
    log: Any
    credit_store: Any
    zero_balance_kb: Callable[[], Any]
    menu_button: Callable[..., Any]


class RequestGate:
    def __init__(self, deps: RequestGateDeps) -> None:
        self._d = deps

    @asynccontextmanager
    async def user_slot(self, user_id: int, message: types.Message):
        """Удержать «слот» пользователя на время запроса.

        Поведение:
        - Если уже идёт запрос этого пользователя — отклоняем (анти-абуз).
        - Если кулдаун не вышел, но осталось ≤ ``max_auto_wait_sec`` — ждём остаток
          и выполняем (не отклоняем за раннее нажатие).
        - Слот гарантированно освобождается в ``finally``.
        """
        d = self._d
        busy_since = d.user_busy.get(user_id)
        if busy_since is not None and (time.time() - busy_since) < d.busy_max_sec:
            await message.answer("⏳ Ваш предыдущий запрос ещё выполняется — дождитесь его.")
            raise RateLimited
        if busy_since is not None:
            # Слот протух (операция зависла) — отпускаем и пускаем новый запрос.
            d.log.warning("user_busy слот для %s протух (%.0fс), отпускаю", user_id, time.time() - busy_since)
            d.user_busy.pop(user_id, None)

        elapsed = time.time() - d.user_last_request[user_id]
        remaining = d.cooldown_sec - elapsed
        if remaining > 0 and remaining > d.max_auto_wait_sec:
            await message.answer(f"⏱️ Слишком часто. Подождите ещё {int(remaining)} сек.")
            raise RateLimited

        # Занимаем слот ДО любого await чтобы избежать race condition:
        # два одновременных запроса иначе оба пройдут проверку user_busy
        # и уйдут в sleep параллельно.
        d.user_busy[user_id] = time.time()
        try:
            if remaining > 0:
                await message.answer(f"⏱️ Подождите {int(remaining) + 1} сек, выполняю...")
                await asyncio.sleep(remaining)
            yield
        finally:
            d.user_busy.pop(user_id, None)
            d.user_last_request[user_id] = time.time()

    @asynccontextmanager
    async def credit_gate(
        self, user_id: int, action: str, message: types.Message, num_images: int = 1, *, surcharge: int = 0
    ):
        """Списать кредиты за действие; вернуть при неуспехе (charge-on-success).

        Резервируем стоимость на входе; если тело не выставило ``charge.ok``, делаем
        рефанд. Бесплатные действия (цена 0) проходят без списания. При нехватке
        средств показываем экран пополнения и поднимаем ``NotEnoughCredits``.
        ``surcharge`` — доплата сверх базовой цены (например, премиум-модель картинки).
        """
        d = self._d
        price = action_price(action, num_images) + max(0, int(surcharge))

        async def _on_insufficient(have: int, needed: int) -> None:
            # Telegram-specific balance UI; the charge/refund rule lives in billing/.
            if have == 0:
                await message.answer(
                    flow_copy.msg("zero_balance"),
                    reply_markup=d.zero_balance_kb(),
                    parse_mode="HTML",
                )
            else:
                kb = types.InlineKeyboardMarkup(
                    inline_keyboard=[[d.menu_button("topup", "m:topup")], [d.menu_button("menu", "m:menu")]]
                )
                await message.answer(
                    flow_copy.msg("low_balance", needed=needed, have=have), reply_markup=kb,
                    parse_mode="HTML",
                )

        async with open_credit_gate(
            d.credit_store, user_id, price, on_insufficient=_on_insufficient
        ) as charge:
            yield charge
