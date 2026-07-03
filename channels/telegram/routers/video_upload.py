"""Uploaded-video edit callback router (Phase 6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, MutableMapping

from aiogram import F, Router, types

import flow_copy
from channels.telegram.keyboards import _menu_button


@dataclass(frozen=True)
class VideoUploadDeps:
    """Injected state/config helpers for ``vu:`` callbacks."""

    workspace: Callable[[int], MutableMapping[str, Any]]
    upload_video_edit_enabled: Callable[[], bool]
    vid_clear: Callable[[int], Any]
    edit_or_answer: Callable[..., Awaitable[Any]]


def create_router(deps: VideoUploadDeps) -> Router:
    """Build the ``vu:`` uploaded-video edit callback router."""

    router = Router(name="tg-video-upload")

    @router.callback_query(F.data.startswith("vu:"))
    async def on_video_upload_action(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        data = callback.data or ""
        msg = callback.message
        st = deps.workspace(user_id)
        if data == "vu:start":
            if not deps.upload_video_edit_enabled():
                # Feature is temporarily hidden; also suppress stale buttons.
                await callback.answer(flow_copy.msg("vid_upload_disabled"), show_alert=True)
                return
            await callback.answer()
            deps.vid_clear(user_id)
            st["vmode"] = "edit"
            st["vawait"] = "vu_video"
            kb = types.InlineKeyboardMarkup(inline_keyboard=[
                [_menu_button("vid_back:fam", "v:back:fam")],
                [_menu_button("cancel", "v:cancel")],
            ])
            await deps.edit_or_answer(msg, flow_copy.msg("vid_upload_ask"), kb)
        else:
            await callback.answer()

    return router
