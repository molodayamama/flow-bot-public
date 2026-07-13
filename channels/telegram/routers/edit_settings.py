"""Photo edit settings callback router (Phase 6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, MutableMapping

from aiogram import F, Router, types

import flow_copy


@dataclass(frozen=True)
class EditSettingsDeps:
    """Injected state, registry, and render helpers for ``es:`` callbacks."""

    workspace: Callable[[int], MutableMapping[str, Any]]
    pending_edits: MutableMapping[int, str]
    image_registry: Any
    edit_and_send: Callable[..., Awaitable[bool]]
    fmt_to_aspect: Callable[[str], str]
    aspect_to_fmt: Callable[[str], str]
    image_model_meta: Callable[[str], Any]
    edit_confirm_kb: Callable[..., types.InlineKeyboardMarkup]
    edit_settings_kb: Callable[..., types.InlineKeyboardMarkup]
    default_fmt: str
    default_image_model: str
    pending_edit_refs: Callable[[int], list] | None = None
    clear_pending_edit: Callable[[int], None] | None = None


def create_router(deps: EditSettingsDeps) -> Router:
    """Build the ``es:`` photo edit settings callback router."""

    router = Router(name="tg-edit-settings")

    @router.callback_query(F.data.startswith("es:"))
    async def on_edit_settings(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        data = callback.data or ""
        st = deps.workspace(user_id)

        if data == "es:cancel":
            st["await"] = None
            if deps.clear_pending_edit is not None:
                deps.clear_pending_edit(user_id)
            else:
                deps.pending_edits.pop(user_id, None)
            st.pop("edit_instruction", None)
            st.pop("ag_variants", None)
            await callback.answer("Отменено")
            try:
                await callback.message.delete()
            except Exception:
                pass
            return

        if data == "es:change":
            st["await"] = "edit"
            st.pop("ag_variants", None)
            await callback.answer()
            await callback.message.answer(
                flow_copy.msg("ask_edit_prompt"),
                reply_markup=deps.edit_settings_kb(
                    st.get("edit_fmt", deps.default_fmt),
                    st.get("edit_imodel", deps.default_image_model),
                ),
            )
            return

        if data == "es:apply":
            instr = (st.get("edit_instruction") or "").strip()
            if deps.pending_edit_refs is not None:
                refs = deps.pending_edit_refs(user_id)
            else:
                token = deps.pending_edits.get(user_id)
                old_ref = deps.image_registry.get(token) if token else None
                refs = [old_ref] if old_ref is not None else []
            ref = refs[0] if refs else None
            if not instr or ref is None or ref.user_id != user_id:
                await callback.answer(flow_copy.msg("expired"), show_alert=True)
                return
            await callback.answer()
            ok = await deps.edit_and_send(
                callback.message,
                ref,
                instr,
                actor_id=user_id,
                aspect_ratio=deps.fmt_to_aspect(
                    st.get("edit_fmt", deps.aspect_to_fmt(ref.aspect_ratio))
                ),
                image_model=st.get("edit_imodel", deps.default_image_model),
                price_action="gen" if st.get("edit_as_gen") else "edit",
                refs=refs,
            )
            if ok:
                st["await"] = None
                st.pop("edit_instruction", None)
                st.pop("ag_variants", None)
                if deps.clear_pending_edit is not None:
                    deps.clear_pending_edit(user_id)
                else:
                    deps.pending_edits.pop(user_id, None)
            return

        changed = False
        if data.startswith("es:fmt:"):
            st["edit_fmt"] = data.split(":")[2]
            changed = True
        elif data.startswith("es:imodel:"):
            choice = data.split(":")[2]
            if deps.image_model_meta(choice):
                st["edit_imodel"] = choice
                changed = True
        await callback.answer()
        if changed:
            kb = (
                deps.edit_confirm_kb(
                    st.get("edit_fmt", deps.default_fmt),
                    st.get("edit_imodel", deps.default_image_model),
                    as_generation=bool(st.get("edit_as_gen")),
                )
                if st.get("await") == "edit_confirm"
                else deps.edit_settings_kb(
                    st.get("edit_fmt", deps.default_fmt),
                    st.get("edit_imodel", deps.default_image_model),
                )
            )
            try:
                await callback.message.edit_reply_markup(reply_markup=kb)
            except Exception:
                pass

    return router
