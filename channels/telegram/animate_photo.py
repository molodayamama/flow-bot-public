"""Telegram adapter for the platform-independent animate-photo scenario (Phase 11).

Wraps the per-user "Оживить фото" (animate-photo) flow for the Telegram channel:
photo upload, screen rendering, and hand-off to the video wizard/generator. The
scenario logic itself is platform-neutral and lives elsewhere; this class is the
Telegram-specific :class:`AnimatePhotoContext` handed to it.

Extracted out of the flow_bot composition root; runtime singletons and sibling
flows are injected via :class:`AnimatePhotoDeps` so this module never imports
flow_bot. Telegram adapter code (uses aiogram), not a platform-neutral core
module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aiogram import types

import flow_copy
from channels.telegram.keyboards import L


@dataclass(frozen=True)
class AnimatePhotoDeps:
    workspace: Callable[[int], dict]
    pending_edits: dict
    vid_clear: Callable[[int], None]
    clear_image_flow_keys: Callable[[dict], None]
    nwiz_model: Callable[[dict], str]
    metrics: Any
    vid_edit: Callable[..., Awaitable[Any]]
    upload_photo_source_from_file_id: Callable[..., Awaitable[dict | None]]
    show_new_video_wizard: Callable[..., Awaitable[Any]]
    video_generate_and_send: Callable[..., Awaitable[Any]]


class AnimatePhotoContext:
    """Telegram adapter for the platform-independent animate-photo scenario."""

    def __init__(self, message: types.Message, user_id: int, deps: AnimatePhotoDeps):
        self.message = message
        self.user_id = user_id
        self._d = deps

    @property
    def state(self) -> dict:
        return self._d.workspace(self.user_id)

    def clear_pending_edit(self) -> None:
        self._d.pending_edits.pop(self.user_id, None)

    def clear_video_flow(self) -> None:
        self._d.vid_clear(self.user_id)

    def clear_image_flow(self) -> None:
        self._d.clear_image_flow_keys(self.state)

    def current_video_model(self) -> str:
        return self._d.nwiz_model(self.state)

    def log_event(self, name: str, *, source: str) -> None:
        self._d.metrics.log_event(name, user_id=self.user_id, source=source)

    async def show_photo_input(self, *, edit: bool, price: int):
        d = self._d
        text = flow_copy.msg("animate_photo_screen", price=price)
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text=L("cancel"), callback_data="v:cancel")]
        ])
        if edit:
            await d.vid_edit(self.message, text, kb, self.user_id, parse_mode="HTML")
        else:
            sent = await self.message.answer(text, reply_markup=kb, parse_mode="HTML")
            self.state["vmsg_id"] = sent.message_id

    async def show_selected_photo_prompt(self):
        text = (
            "🎬 <b>Оживить фото</b>\n\n"
            "📸 <b>Фото добавлено.</b> Опиши, что должно происходить в видео."
        )
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text=L("cancel"), callback_data="v:cancel")]
        ])
        sent = await self.message.answer(text, reply_markup=kb, parse_mode="HTML")
        self.state["vmsg_id"] = sent.message_id

    async def show_need_photo(self):
        await self.message.answer(flow_copy.msg("animate_photo_need_photo"))

    async def upload_photo_source(self, file_id: str) -> dict | None:
        d = self._d
        status_msg = await self.message.answer(flow_copy.msg("uploading_photo"))
        source = await d.upload_photo_source_from_file_id(
            self.message, user_id=self.user_id, status_msg=status_msg, file_id=file_id,
        )
        try:
            await status_msg.delete()
        except Exception:
            pass
        return source

    async def show_video_wizard(self, *, edit: bool):
        await self._d.show_new_video_wizard(self.message, user_id=self.user_id, edit=edit)

    async def generate_video(self, prompt: str):
        await self._d.video_generate_and_send(self.message, prompt, user_id=self.user_id)


def make_animate_photo_context_factory(deps: AnimatePhotoDeps) -> Callable[[types.Message, int], AnimatePhotoContext]:
    """Return a 2-arg ``(message, user_id)`` factory bound to ``deps``.

    Lets routers keep the existing ``context_factory(message, user_id)`` call
    surface while the deps are injected once at composition-root time.
    """
    def _factory(message: types.Message, user_id: int) -> AnimatePhotoContext:
        return AnimatePhotoContext(message, user_id, deps)
    return _factory
