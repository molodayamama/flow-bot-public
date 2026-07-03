"""Ideas template and guided-picker callback handlers (Phase 6).

Handles the ``tp:`` template Q&A and ``gp:`` guided picker state machines. App
state/renderers are injected through ``IdeasFlowDeps``; this module must not
import the monolith back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, MutableMapping

from aiogram import F, Router, types

import flow_copy
import prompts_lib


@dataclass(frozen=True)
class IdeasFlowDeps:
    """Injected ideas-flow state helpers and renderers."""

    workspace: Callable[[int], MutableMapping[str, Any]]
    ideas_clear: Callable[..., Any]
    tp_store_answer: Callable[[MutableMapping[str, Any], str], Any]
    show_ideas_root: Callable[..., Awaitable[Any]]
    render_template_step: Callable[..., Awaitable[Any]]
    render_guided_step: Callable[..., Awaitable[Any]]
    log_event: Callable[..., Any]


def create_router(deps: IdeasFlowDeps) -> Router:
    """Build the ``tp:`` and ``gp:`` callback router."""

    router = Router(name="tg-ideas-flow")

    @router.callback_query(F.data.startswith("tp:"))
    async def on_template_action(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        data = callback.data or ""
        msg = callback.message
        st = deps.workspace(user_id)
        if data.startswith("tp:tpl:"):
            tid = data.split(":", 2)[2]
            if not prompts_lib.get_template(tid):
                await callback.answer(flow_copy.msg("expired"), show_alert=True)
                return
            st["tp_tpl"] = tid
            st["tp_step"] = 0
            st["tp_answers"] = {}
            st["ideas_mode"] = "templates"
            deps.log_event("template_opened", user_id=user_id, source="ideas",
                              payload={"template": tid})
            await callback.answer()
            await deps.render_template_step(msg, user_id=user_id)
            return
        if not st.get("tp_tpl"):
            await callback.answer(flow_copy.msg("expired"), show_alert=True)
            await deps.show_ideas_root(msg, user_id=user_id, edit=True)
            return
        if data.startswith("tp:ans:"):
            idx = int(data.split(":")[2])
            questions = prompts_lib.template_questions(st["tp_tpl"])
            step = st.get("tp_step", 0)
            opts = questions[step]["options"] if step < len(questions) else []
            value = opts[idx]["value"] if 0 <= idx < len(opts) else ""
            deps.tp_store_answer(st, value)
            await callback.answer()
            await deps.render_template_step(msg, user_id=user_id)
        elif data == "tp:skip":
            deps.tp_store_answer(st, "")
            await callback.answer()
            await deps.render_template_step(msg, user_id=user_id)
        elif data == "tp:back":
            st["tp_step"] = max(0, st.get("tp_step", 0) - 1)
            st["tp_await"] = None
            await callback.answer()
            await deps.render_template_step(msg, user_id=user_id)
        elif data == "tp:cancel":
            deps.ideas_clear(st, clear_photo=True)
            await callback.answer("Отменено")
            await deps.show_ideas_root(msg, user_id=user_id, edit=True)
        else:
            await callback.answer()

    @router.callback_query(F.data.startswith("gp:"))
    async def on_guided_picker_action(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        data = callback.data or ""
        msg = callback.message
        st = deps.workspace(user_id)
        if "gp_step" not in st:
            await callback.answer(flow_copy.msg("expired"), show_alert=True)
            await deps.show_ideas_root(msg, user_id=user_id, edit=True)
            return
        if data.startswith("gp:opt:"):
            idx = int(data.split(":")[2])
            step = st.get("gp_step", 0)
            steps = prompts_lib.guided_steps()
            opts = steps[step]["options"] if step < len(steps) else []
            if 0 <= idx < len(opts):
                st.setdefault("gp_answers", {})[steps[step]["key"]] = opts[idx]["value"]
            st["gp_step"] = step + 1
            await callback.answer()
            await deps.render_guided_step(msg, user_id=user_id)
        elif data == "gp:back":
            st["gp_step"] = max(0, st.get("gp_step", 0) - 1)
            await callback.answer()
            await deps.render_guided_step(msg, user_id=user_id)
        elif data == "gp:cancel":
            deps.ideas_clear(st, clear_photo=True)
            await callback.answer("Отменено")
            await deps.show_ideas_root(msg, user_id=user_id, edit=True)
        else:
            await callback.answer()

    return router
