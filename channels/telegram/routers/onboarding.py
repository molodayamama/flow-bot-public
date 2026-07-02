"""Onboarding callback handlers extracted from the Telegram monolith (Phase 6).

Handles the small ``ob:`` callback group for the two-step first-run choice of
image, video, or photo upload flow. App state and screen renderers are injected
through ``OnboardingDeps``; this module must not import the monolith back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, MutableMapping

from aiogram import F, Router, types

import flow_copy
from channels.telegram.keyboards import _onboarding_step2_kb


@dataclass(frozen=True)
class OnboardingDeps:
    """Injected state mutators and screen renderers."""

    balance: Callable[[int], int]
    show_main_menu: Callable[..., Awaitable[Any]]
    reset_image_flow: Callable[..., Any]
    show_prompt_picker: Callable[..., Awaitable[Any]]
    vid_clear: Callable[[int], Any]
    show_video_prompt_input: Callable[..., Awaitable[Any]]
    workspace: Callable[[int], MutableMapping[str, Any]]


def create_router(deps: OnboardingDeps) -> Router:
    """Build the ``ob:`` callback router over injected app dependencies."""

    router = Router(name="tg-onboarding")

    @router.callback_query(F.data.startswith("ob:"))
    async def on_onboarding_action(callback: types.CallbackQuery):
        """Онбординг новых пользователей: 2-шаговый выбор что создавать."""
        user_id = callback.from_user.id
        data = callback.data or ""
        msg = callback.message
        credits = deps.balance(user_id)

        if data == "ob:skip":
            await callback.answer()
            await deps.show_main_menu(msg, user_id=user_id, edit=True)
            return

        if data in ("ob:img", "ob:vid", "ob:photo"):
            kind = data.split(":")[1]
            copy_key = f"onboarding_step2_{kind}"
            text = flow_copy.msg(copy_key, credits=credits)
            await callback.answer()
            await msg.edit_text(text, reply_markup=_onboarding_step2_kb(kind), parse_mode="HTML")
            return

        if data.startswith("ob:go:"):
            kind = data.split(":")[2]
            await callback.answer()
            if kind == "img":
                deps.reset_image_flow(user_id)
                await deps.show_prompt_picker(msg, user_id=user_id, edit=True)
            elif kind == "vid":
                deps.vid_clear(user_id)
                await deps.show_video_prompt_input(msg, user_id=user_id, edit=True)
            elif kind == "photo":
                deps.reset_image_flow(user_id, keep_last=False)
                deps.workspace(user_id)["await"] = "photo"
                await msg.edit_text(flow_copy.msg("ask_photo"))
            return

        await callback.answer()

    return router
