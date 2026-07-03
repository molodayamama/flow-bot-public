"""Ideas-hub callback handlers extracted from the Telegram monolith (Phase 6).

Handles the small ``ih:`` hub that switches between ideas root, template Q&A,
and guided picker. App state and renderers are injected through ``IdeasHubDeps``;
this module must not import the monolith back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, MutableMapping

from aiogram import F, Router, types

import flow_copy
from channels.telegram.keyboards import _templates_picker_kb


@dataclass(frozen=True)
class IdeasHubDeps:
    """Injected state lookup and screen renderers."""

    workspace: Callable[[int], MutableMapping[str, Any]]
    show_ideas_root: Callable[..., Awaitable[Any]]
    edit_or_answer: Callable[..., Awaitable[Any]]
    render_guided_step: Callable[..., Awaitable[Any]]


def create_router(deps: IdeasHubDeps) -> Router:
    """Build the ``ih:`` callback router over injected app dependencies."""

    router = Router(name="tg-ideas-hub")

    @router.callback_query(F.data.startswith("ih:"))
    async def on_ideas_hub_action(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        data = callback.data or ""
        msg = callback.message
        await callback.answer()
        if data == "ih:root":
            await deps.show_ideas_root(msg, user_id=user_id, edit=True)
        elif data == "ih:templates":
            deps.workspace(user_id)["ideas_mode"] = "templates"
            await deps.edit_or_answer(
                msg, flow_copy.msg("ideas_templates_title"), _templates_picker_kb(),
                parse_mode="HTML",
            )
        elif data == "ih:guided":
            st = deps.workspace(user_id)
            st["ideas_mode"] = "guided"
            st["gp_step"] = 0
            st["gp_answers"] = {}
            await deps.render_guided_step(msg, user_id=user_id)

    return router
