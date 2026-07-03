"""Prompt-agent callback router (Phase 6).

Handles ``ag:`` callback routing for image/video/edit prompt improvement. The
actual improve/pick implementation stays injected from the app layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, MutableMapping

from aiogram import F, Router, types


@dataclass(frozen=True)
class AgentDeps:
    """Injected prompt-agent state helpers and renderers."""

    workspace: Callable[[int], MutableMapping[str, Any]]
    agent_pick: Callable[..., Awaitable[Any]]
    agent_improve_flow: Callable[..., Awaitable[Any]]
    show_new_video_wizard: Callable[..., Awaitable[Any]]
    show_wizard: Callable[..., Awaitable[Any]]
    show_edit_confirm: Callable[..., Awaitable[Any]]
    video_edit: Callable[..., Awaitable[Any]]
    edit_or_answer: Callable[..., Awaitable[Any]]
    agent_edit_instruction: Callable[[str], str]


def create_router(deps: AgentDeps) -> Router:
    """Build the ``ag:`` prompt-agent callback router."""

    router = Router(name="tg-agent")

    @router.callback_query(F.data.startswith("ag:"))
    async def on_agent_action(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        data = callback.data or ""
        msg = callback.message
        st = deps.workspace(user_id)

        # Video wizard (vprompt)
        if data.startswith("ag:vpick:"):
            await deps.agent_pick(
                callback,
                user_id=user_id,
                idx_str=data.split(":")[2],
                prompt_key="vprompt",
                rerender=deps.show_new_video_wizard,
            )
            return
        if data == "ag:vkeep":
            st.pop("ag_variants", None)
            await callback.answer()
            await deps.show_new_video_wizard(msg, user_id=user_id, edit=True)
            return
        if data == "ag:vimprove":
            await deps.agent_improve_flow(
                callback,
                user_id=user_id,
                prompt_key="vprompt",
                source="video",
                pick_prefix="ag:vpick:",
                keep_data="ag:vkeep",
                rerender=deps.show_new_video_wizard,
                edit_fn=lambda m, t, kb, **kw: deps.video_edit(m, t, kb, user_id, **kw),
                empty_prompt_msg="Сначала опишите видео",
            )
            return

        # Image wizard (pending_prompt)
        if data.startswith("ag:pick:"):
            await deps.agent_pick(
                callback,
                user_id=user_id,
                idx_str=data.split(":")[2],
                prompt_key="pending_prompt",
                rerender=deps.show_wizard,
            )
            return
        if data == "ag:keep":
            st.pop("ag_variants", None)
            await callback.answer()
            await deps.show_wizard(msg, user_id=user_id, edit=True)
            return
        if data == "ag:improve":
            await deps.agent_improve_flow(
                callback,
                user_id=user_id,
                prompt_key="pending_prompt",
                source="image",
                pick_prefix="ag:pick:",
                keep_data="ag:keep",
                rerender=deps.show_wizard,
                edit_fn=deps.edit_or_answer,
                empty_prompt_msg="Сначала опиши картинку",
            )
            return

        # Edit-my-photo confirm (edit_instruction)
        if data.startswith("ag:epick:"):
            await deps.agent_pick(
                callback,
                user_id=user_id,
                idx_str=data.split(":")[2],
                prompt_key="edit_instruction",
                rerender=deps.show_edit_confirm,
            )
            return
        if data == "ag:ekeep":
            st.pop("ag_variants", None)
            await callback.answer()
            await deps.show_edit_confirm(msg, user_id=user_id, edit=True)
            return
        if data == "ag:eimprove":
            await deps.agent_improve_flow(
                callback,
                user_id=user_id,
                prompt_key="edit_instruction",
                source="edit",
                pick_prefix="ag:epick:",
                keep_data="ag:ekeep",
                rerender=deps.show_edit_confirm,
                edit_fn=deps.edit_or_answer,
                empty_prompt_msg="Сначала напиши, что изменить",
                instruction_fn=deps.agent_edit_instruction,
            )
            return

        await callback.answer()

    return router
