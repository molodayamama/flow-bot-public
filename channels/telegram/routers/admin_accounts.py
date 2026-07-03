"""Account-pool admin toggle commands + /admin_help router (Phase 6)."""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any, Callable

from aiogram import Router, types
from aiogram.filters import Command

import flow_copy


@dataclass(frozen=True)
class AdminAccountsDeps:
    """Injected admin guards, account-pool, and metrics helpers."""

    admin_only: Callable[[types.Message], bool]
    owner_only: Callable[[types.Message], bool]
    account_pool: Any
    log_event: Callable[..., Any]
    render_admin_help: Callable[[], str]


def create_router(deps: AdminAccountsDeps) -> Router:
    """Build the /acc_off /acc_on /acc_vid_off /acc_vid_on /admin_help router."""

    router = Router(name="tg-admin-accounts")

    @router.message(Command("acc_off"))
    async def cmd_acc_off(message: types.Message):
        """Ручное отключение аккаунта пула: /acc_off <id> (admin)."""
        if not deps.admin_only(message):
            await message.answer(flow_copy.msg("admin_denied"))
            return
        parts = (message.text or "").split()
        acc_id = parts[1] if len(parts) > 1 else ""
        if deps.account_pool.set_disabled(acc_id, True):
            deps.log_event("account_disabled", user_id=message.from_user.id,
                              payload={"account": acc_id})
            await message.answer(f"⛔ Аккаунт {html.escape(acc_id)} отключён.")
        else:
            await message.answer(
                "Не нашёл такой аккаунт. Известные: " + ", ".join(deps.account_pool.account_ids())
            )


    @router.message(Command("acc_on"))
    async def cmd_acc_on(message: types.Message):
        """Включить аккаунт пула обратно: /acc_on <id> (admin)."""
        if not deps.admin_only(message):
            await message.answer(flow_copy.msg("admin_denied"))
            return
        parts = (message.text or "").split()
        acc_id = parts[1] if len(parts) > 1 else ""
        if deps.account_pool.set_disabled(acc_id, False):
            deps.log_event("account_enabled", user_id=message.from_user.id,
                              payload={"account": acc_id})
            await message.answer(f"✅ Аккаунт {html.escape(acc_id)} включён.")
        else:
            await message.answer(
                "Не нашёл такой аккаунт. Известные: " + ", ".join(deps.account_pool.account_ids())
            )


    @router.message(Command("acc_vid_off"))
    async def cmd_acc_vid_off(message: types.Message):
        """Запретить видео на аккаунте пула: /acc_vid_off <id> (admin)."""
        if not deps.admin_only(message):
            await message.answer(flow_copy.msg("admin_denied"))
            return
        parts = (message.text or "").split()
        acc_id = parts[1] if len(parts) > 1 else ""
        if deps.account_pool.set_video_allowed(acc_id, False):
            deps.log_event("account_video_disabled", user_id=message.from_user.id,
                              payload={"account": acc_id})
            await message.answer(
                f"🖼 Аккаунт <b>{html.escape(acc_id)}</b>: только картинки. "
                f"Видео-запросы пойдут на другой аккаунт.",
                parse_mode="HTML",
            )
        else:
            await message.answer(
                "Не нашёл такой аккаунт. Известные: " + ", ".join(deps.account_pool.account_ids())
            )


    @router.message(Command("acc_vid_on"))
    async def cmd_acc_vid_on(message: types.Message):
        """Вернуть видео на аккаунт пула: /acc_vid_on <id> (admin)."""
        if not deps.admin_only(message):
            await message.answer(flow_copy.msg("admin_denied"))
            return
        parts = (message.text or "").split()
        acc_id = parts[1] if len(parts) > 1 else ""
        if deps.account_pool.set_video_allowed(acc_id, True):
            deps.log_event("account_video_enabled", user_id=message.from_user.id,
                              payload={"account": acc_id})
            await message.answer(
                f"🎬 Аккаунт <b>{html.escape(acc_id)}</b>: видео снова разрешено.",
                parse_mode="HTML",
            )
        else:
            await message.answer(
                "Не нашёл такой аккаунт. Известные: " + ", ".join(deps.account_pool.account_ids())
            )


    @router.message(Command("admin_help"))
    async def cmd_admin_help(message: types.Message):
        """Справочник команд (синтаксис + описание). Только для OWNER_ID из .env."""
        if not deps.owner_only(message):
            await message.answer(flow_copy.msg("admin_denied"))
            return
        await message.answer(deps.render_admin_help(), parse_mode="HTML")

    return router
