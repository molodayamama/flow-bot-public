"""Public command handlers extracted from the Telegram monolith (Phase 6).

Thin wrappers for /menu, /help, /referral (/ref) and /balance that delegate to
screen renderers injected by the application wiring. This module must never
import the monolith back (no import cycle); everything it needs arrives
through ``CommandsDeps``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aiogram import Router, types
from aiogram.filters import Command


@dataclass(frozen=True)
class CommandsDeps:
    """Injected screen renderers (bound to app internals at wiring time)."""

    show_main_menu: Callable[..., Awaitable[Any]]
    show_help_screen: Callable[..., Awaitable[Any]]
    show_referral_screen: Callable[..., Awaitable[Any]]
    show_balance: Callable[..., Awaitable[Any]]


def create_router(deps: CommandsDeps) -> Router:
    """Build the public-commands Router over the injected renderers."""

    router = Router(name="tg-public-commands")

    @router.message(Command("menu"))
    async def cmd_menu(message: types.Message):
        # Постоянная нижняя клавиатура держится с /start; здесь показываем меню.
        await deps.show_main_menu(message, user_id=message.from_user.id, ensure_kb=True)

    @router.message(Command("help"))
    async def cmd_help(message: types.Message):
        await deps.show_help_screen(message, edit=False)

    @router.message(Command("referral", "ref"))
    async def cmd_referral(message: types.Message):
        await deps.show_referral_screen(message, user_id=message.from_user.id, edit=False)

    @router.message(Command("balance"))
    async def cmd_balance(message: types.Message):
        await deps.show_balance(message, user_id=message.from_user.id, edit=False)

    return router
