"""Image retry callback handler extracted from the Telegram monolith (Phase 6).

Handles the exact ``img:retry`` callback under failed image generations. App
state and generation entrypoint are injected through ``ImageRetryDeps``; this
module must not import the monolith back.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, MutableMapping

from aiogram import F, Router, types


@dataclass(frozen=True)
class ImageRetryDeps:
    """Injected retry state lookup and image generation entrypoint."""

    workspace: Callable[[int], MutableMapping[str, Any]]
    generate_and_send: Callable[..., Awaitable[Any]]
    default_image_model: str


def create_router(deps: ImageRetryDeps) -> Router:
    """Build the exact ``img:retry`` callback router."""

    router = Router(name="tg-image-retry")

    @router.callback_query(F.data == "img:retry")
    async def on_img_retry(callback: types.CallbackQuery):
        """Повторить последний провалившийся запрос на генерацию картинок."""
        user_id = callback.from_user.id
        snap = deps.workspace(user_id).get("img_retry")
        if not snap or not snap.get("prompt"):
            await callback.answer("Запрос устарел — попробуйте снова.", show_alert=True)
            return
        await callback.answer("Повторяю 🔁")
        await deps.generate_and_send(
            callback.message,
            snap["prompt"],
            num_images=snap.get("num_images", 1),
            aspect_ratio=snap.get("aspect_ratio", "landscape"),
            actor_id=user_id,
            action=snap.get("action", "gen"),
            image_model=snap.get("image_model", deps.default_image_model),
        )

    return router
