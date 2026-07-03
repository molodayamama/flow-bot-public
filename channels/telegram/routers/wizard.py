"""Image wizard callback router (Phase 6)."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, MutableMapping, Sequence

from aiogram import F, Router, types

import flow_copy


@dataclass(frozen=True)
class WizardDeps:
    """Injected image-wizard state helpers and renderers."""

    workspace: Callable[[int], MutableMapping[str, Any]]
    clamp_num_images: Callable[[str], int]
    image_model_meta: Callable[[str], Any]
    reset_image_flow: Callable[..., Any]
    show_main_menu: Callable[..., Awaitable[Any]]
    show_wizard: Callable[..., Awaitable[Any]]
    show_prompt_picker: Callable[..., Awaitable[Any]]
    boost_prompt_with_gemini: Callable[[str], Awaitable[str | None]]
    generate_and_send: Callable[..., Awaitable[Any]]
    fmt_to_aspect: Callable[[str], str]
    log_event: Callable[..., Any]
    quick_ideas: Sequence[str]
    default_count: int
    default_fmt: str
    default_image_model: str


def create_router(deps: WizardDeps) -> Router:
    """Build the ``w:`` image wizard callback router."""

    router = Router(name="tg-wizard")

    @router.callback_query(F.data.startswith("w:"))
    async def on_wizard_action(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        data = callback.data or ""
        st = deps.workspace(user_id)
        msg = callback.message

        if data == "w:cancel":
            await callback.answer("Отменено")
            st.clear()
            await deps.show_main_menu(msg, user_id=user_id, edit=True)
            return
        if data.startswith("w:cnt:"):
            st["count"] = deps.clamp_num_images(data.split(":")[2])
            await callback.answer()
            await deps.show_wizard(msg, user_id=user_id, edit=True)
            return
        if data.startswith("w:fmt:"):
            st["fmt"] = data.split(":")[2]
            await callback.answer()
            await deps.show_wizard(msg, user_id=user_id, edit=True)
            return
        if data.startswith("w:imodel:"):
            choice = data.split(":")[2]
            if deps.image_model_meta(choice):
                st["imodel"] = choice
            await callback.answer()
            await deps.show_wizard(msg, user_id=user_id, edit=True)
            return
        if data.startswith("w:hist:"):
            try:
                idx = int(data.split(":")[2])
            except (IndexError, ValueError):
                await callback.answer()
                return
            cache = st.get("_hist_cache", [])
            if 0 <= idx < len(cache):
                chosen = cache[idx]
                deps.reset_image_flow(user_id, keep_last=True)
                st["pending_prompt"] = chosen
                await callback.answer(f"📋 {chosen[:40]}", show_alert=False)
                await deps.show_wizard(msg, user_id=user_id, edit=False)
            else:
                await callback.answer()
            return
        if data == "w:idea:next":
            pool = st.get("ideas_pool", [])
            offset = st.get("ideas_offset", 0) + 3
            if offset + 3 > len(pool):
                pool = list(deps.quick_ideas)
                random.shuffle(pool)
                st["ideas_pool"] = pool
                offset = 0
            st["ideas_offset"] = offset
            await callback.answer()
            await deps.show_prompt_picker(msg, user_id=user_id, edit=True)
            return
        if data.startswith("w:idea:"):
            try:
                idx = int(data.split(":")[2])
            except (IndexError, ValueError):
                await callback.answer()
                return
            pool = st.get("ideas_pool", [])
            offset = st.get("ideas_offset", 0)
            idea = pool[offset + idx] if 0 <= offset + idx < len(pool) else None
            if idea:
                st["pending_prompt"] = idea
                await callback.answer(f"💡 {idea[:40]}", show_alert=False)
            await deps.show_wizard(msg, user_id=user_id, edit=True)
            return
        if data == "w:boost_prompt":
            pending = st.get("pending_prompt")
            if not pending:
                await callback.answer("Сначала введи запрос", show_alert=True)
                return
            await callback.answer("✨ Улучшаю промпт…")
            improved = await deps.boost_prompt_with_gemini(pending)
            if improved:
                st["pending_prompt"] = improved
                deps.log_event("prompt_boosted", user_id=user_id)
                await deps.show_wizard(msg, user_id=user_id, edit=True)
            else:
                await callback.answer("Не удалось улучшить — попробуй позже", show_alert=True)
            return
        if data == "w:change_prompt":
            st.pop("pending_prompt", None)
            await callback.answer()
            await deps.show_prompt_picker(msg, user_id=user_id, edit=True)
            return
        if data == "w:go":
            pending = st.get("pending_prompt")
            if pending:
                st["pending_prompt"] = None
                count = st.get("count", deps.default_count)
                fmt = st.get("fmt", deps.default_fmt)
                await callback.answer()
                await deps.generate_and_send(
                    callback.message,
                    pending,
                    num_images=count,
                    aspect_ratio=deps.fmt_to_aspect(fmt),
                    actor_id=user_id,
                    image_model=st.get("imodel", deps.default_image_model),
                )
                return
            st["await"] = "prompt"
            st["step"] = "prompt"
            await callback.answer()
            await msg.edit_text(flow_copy.msg("ask_prompt"))
            return
        await callback.answer()

    return router
