"""Photo-route callback handlers extracted from the Telegram monolith (Phase 6).

Handles the small ``pr:`` callback group shown when a user sends a photo with
a caption before choosing whether it should become an image edit/generation or
a video reference. The application injects state and media preparation helpers;
this module must not import the monolith back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, MutableMapping

from aiogram import F, Router, types


@dataclass(frozen=True)
class PhotoRouteDeps:
    """Injected state and media preparation callbacks."""

    pending_photo_routes: MutableMapping[int, dict]
    prepare_photo_edit_from_file_id: Callable[..., Awaitable[Any]]
    prepare_photo_video_from_file_id: Callable[..., Awaitable[Any]]
    prepare_photo_edit_from_file_ids: Callable[..., Awaitable[Any]] | None = None
    prepare_photo_video_from_file_ids: Callable[..., Awaitable[Any]] | None = None


def create_router(deps: PhotoRouteDeps) -> Router:
    """Build the ``pr:`` callback router over injected app dependencies."""

    router = Router(name="tg-photo-route")

    @router.callback_query(F.data.startswith("pr:"))
    async def on_photo_route_choice(callback: types.CallbackQuery):
        """Фото+подпись без выбранного режима: выбрать image/video before upload."""
        user_id = callback.from_user.id
        data = callback.data or ""
        snap = deps.pending_photo_routes.get(user_id)
        if data == "pr:cancel":
            deps.pending_photo_routes.pop(user_id, None)
            await callback.answer("Отменено")
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            return
        if not snap or not snap.get("file_id") or not snap.get("caption"):
            await callback.answer("Запрос устарел — пришлите фото ещё раз.", show_alert=True)
            return
        file_id = snap["file_id"]
        file_ids = [item for item in (snap.get("file_ids") or [file_id])[:4] if item]
        caption = snap["caption"]
        deps.pending_photo_routes.pop(user_id, None)
        await callback.answer()
        if data == "pr:img":
            await deps.prepare_photo_edit_from_file_ids(
                callback.message,
                user_id=user_id,
                file_ids=file_ids,
                caption=caption,
                as_generation=True,  # «Создать изображение» по фото = тариф генерации
            )
            return
        if data == "pr:vid":
            await deps.prepare_photo_video_from_file_ids(
                callback.message,
                user_id=user_id,
                file_ids=file_ids,
                caption=caption,
            )
            return

    return router
