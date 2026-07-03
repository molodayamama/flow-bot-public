"""Animate generated image callback router (Phase 6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from aiogram import F, Router, types

import flow_copy


@dataclass(frozen=True)
class AnimateDeps:
    """Injected registry and scenario factory for ``an:`` callbacks."""

    image_registry: Any
    animate_photo_scenario: Callable[[], Any]
    context_factory: Callable[[types.Message, int], Any]


def create_router(deps: AnimateDeps) -> Router:
    """Build the ``an:`` generated-image animation callback router."""

    router = Router(name="tg-animate")

    @router.callback_query(F.data.startswith("an:"))
    async def on_animate_action(callback: types.CallbackQuery):
        data = callback.data or ""
        user_id = callback.from_user.id
        msg = callback.message
        if data.startswith("an:img:"):
            token = data.split(":", 2)[2]
            ref = deps.image_registry.get(token)
            if ref is None or ref.user_id != user_id:
                await callback.answer(flow_copy.msg("expired"), show_alert=True)
                return
            await callback.answer()
            await deps.animate_photo_scenario().start_from_generated_image(
                deps.context_factory(msg, user_id),
                source=ref.source if isinstance(ref.source, dict) else {},
                account_id=ref.account_id,
                project_id=ref.project_id,
            )
            return
        await callback.answer()

    return router
