"""Telegram photo ingestion for the flow adapter (Phase 11 core split).

Downloads user-sent photos, uploads them to the pooled provider account, and
routes them into the active state branch: image edit/generation, single
reference-to-video, or an album (media group) for frames/ingredients video.
Album delivery is debounced by ``media_group_id`` (globally unique, so user
sessions never mix).

Extracted out of the flow_bot composition root; runtime singletons and screen
callbacks are injected via :class:`PhotoIntakeDeps` so this module never imports
flow_bot. Telegram adapter code (uses aiogram), not a platform-neutral core
module.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aiogram import types

import flow_copy
from flow_core import ImageRef

_ALBUM_FLUSH_DELAY = 0.8


@dataclass(frozen=True)
class PhotoIntakeDeps:
    bot_download: Callable[[str], Awaitable[Any]]
    log: Any
    account_for_video: Callable[..., str | None]
    account_for_image: Callable[..., str | None]
    ensure_user_project: Callable[..., Awaitable[str | None]]
    keeper_for_acc: Callable[[str | None], Any]
    workspace: Callable[[int], dict]
    image_registry: Any
    pending_edits: dict
    fmt_to_aspect: Callable[[str], str]
    show_edit_confirm: Callable[..., Awaitable[Any]]
    edit_settings_kb: Callable[..., Any]
    vid_clear: Callable[[int], None]
    clear_image_flow_keys: Callable[[dict], None]
    show_new_video_wizard: Callable[..., Awaitable[Any]]
    show_video_frames: Callable[..., Awaitable[Any]]
    show_video_ingredients: Callable[..., Awaitable[Any]]
    nwiz_model: Callable[[dict], str]
    default_fmt: str
    default_image_model: str
    vid_default_fmt: str


class PhotoIntake:
    def __init__(self, deps: PhotoIntakeDeps) -> None:
        self._d = deps
        # Album (media group) buffering for video modes. Telegram delivers an
        # album as separate photo messages sharing media_group_id. We debounce by
        # group id: each photo (re)schedules a short flush; when the group goes
        # quiet we process all of its photos at once. Keyed by the globally-unique
        # media_group_id, so user sessions can't mix.
        self._album_buf: dict[str, list[types.Message]] = {}
        self._album_tasks: dict[str, "asyncio.Task"] = {}

    async def _download_bytes(self, file_id: str) -> bytes:
        buf = await self._d.bot_download(file_id)
        return buf.read() if hasattr(buf, "read") else bytes(buf)

    # ── raw upload → media source dict (video reference) ─────────────────

    async def upload_photo_source_from_message(
        self, message: types.Message, *, user_id: int, status_msg: types.Message
    ) -> dict | None:
        photo = message.photo[-1]
        return await self.upload_photo_source_from_file_id(
            message, user_id=user_id, status_msg=status_msg, file_id=photo.file_id
        )

    async def upload_photo_source_from_file_id(
        self, message: types.Message, *, user_id: int, status_msg: types.Message, file_id: str
    ) -> dict | None:
        d = self._d
        try:
            data = await self._download_bytes(file_id)
        except Exception:
            d.log.exception("download user photo failed")
            await status_msg.edit_text("❌ Не удалось получить ваше фото.")
            return None

        acc_id = d.account_for_video(user_id)
        if acc_id is None:
            await status_msg.edit_text(flow_copy.msg("accounts_unavailable"))
            return None
        project_id = await d.ensure_user_project(user_id, account_id=acc_id)
        try:
            source = await d.keeper_for_acc(acc_id).upload_image(data, filename=f"tg_{user_id}.png", project_id=project_id)
        except Exception:
            d.log.exception("upload_image failed")
            source = None

        if not source or not source.get("mediaId"):
            await status_msg.edit_text(flow_copy.msg("upload_failed"))
            return None

        d.log.info(
            "📤 video ref photo uploaded account=%s",
            acc_id,
        )
        source.setdefault("_project_id", project_id)
        source.setdefault("_account_id", acc_id)
        source.setdefault("_tg_file_id", file_id)  # для бесшовного ре-аплоада
        return source

    # ── album buffering (frames / ingredients video) ────────────────────

    def vid_caption(self, message: types.Message) -> str:
        return (message.caption or "").strip()

    async def flush_album(self, media_group_id: str, user_id: int) -> None:
        try:
            await asyncio.sleep(_ALBUM_FLUSH_DELAY)
        except asyncio.CancelledError:
            return
        messages = self._album_buf.pop(media_group_id, [])
        self._album_tasks.pop(media_group_id, None)
        if messages:
            await self.handle_album_photos(messages, user_id=user_id)

    async def handle_album_photos(self, messages: list, *, user_id: int) -> None:
        """Обработать альбом для frames/ingredients: загрузить фото пачкой."""
        d = self._d
        st = d.workspace(user_id)
        vmode = st.get("vmode")
        first = messages[0]
        status_msg = await first.answer(flow_copy.msg("uploading_photo"))
        sources = []
        for m in messages:
            src = await self.upload_photo_source_from_message(m, user_id=user_id, status_msg=status_msg)
            if src:
                sources.append(src)
        try:
            await status_msg.delete()
        except Exception:
            pass
        if not sources:
            return
        caption = self.vid_caption(first)
        if caption:
            st["vcaption_prompt"] = caption
        if vmode == "frames":
            # Первое фото → начало, второе → конец.
            st["vfrm_start"] = sources[0]
            if len(sources) > 1:
                st["vfrm_end"] = sources[1]
            await d.show_video_frames(first, user_id=user_id, edit=False)
        else:
            photos: list = st.setdefault("ving_photos", [])
            for s in sources:
                if len(photos) >= 4:
                    break
                photos.append(s)
            await d.show_video_ingredients(first, user_id=user_id, edit=False)

    # ── upload → ImageRef (image edit/generation) ───────────────────────

    async def upload_image_ref_from_photo_message(
        self, message: types.Message, *, user_id: int, status_msg: types.Message,
        prompt: str, aspect_ratio: str,
    ) -> ImageRef | None:
        photo = message.photo[-1]
        return await self.upload_image_ref_from_file_id(
            message, user_id=user_id, status_msg=status_msg, file_id=photo.file_id,
            prompt=prompt, aspect_ratio=aspect_ratio,
        )

    async def upload_image_ref_from_file_id(
        self, message: types.Message, *, user_id: int, status_msg: types.Message,
        file_id: str, prompt: str, aspect_ratio: str,
    ) -> ImageRef | None:
        d = self._d
        try:
            data = await self._download_bytes(file_id)
        except Exception:
            d.log.exception("download user photo failed")
            await status_msg.edit_text("❌ Не удалось получить ваше фото.")
            return None

        acc_id = d.account_for_image(user_id, prefer_image_only=True)
        if acc_id is None:
            await status_msg.edit_text(flow_copy.msg("accounts_unavailable"))
            return None
        project_id = await d.ensure_user_project(user_id, account_id=acc_id)
        try:
            source = await d.keeper_for_acc(acc_id).upload_image(data, filename=f"tg_{user_id}.png", project_id=project_id)
        except Exception:
            d.log.exception("upload_image failed")
            source = None

        if not source or not source.get("mediaId"):
            await status_msg.edit_text(flow_copy.msg("upload_failed"))
            return None

        source.setdefault("_tg_file_id", file_id)
        upload_project = source.pop("_project_id", None) or project_id
        return ImageRef(
            user_id=user_id,
            project_id=upload_project,
            source=source,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            account_id=acc_id,
        )

    # ── prepare-and-route entrypoints (called from routers) ──────────────

    async def prepare_photo_edit_from_file_id(
        self, message: types.Message, *, user_id: int, file_id: str, caption: str,
        aspect_fmt: str | None = None, image_model: str | None = None,
        as_generation: bool = False,
    ) -> bool:
        d = self._d
        aspect_fmt = aspect_fmt if aspect_fmt is not None else d.default_fmt
        status_msg = await message.answer(flow_copy.msg("uploading_photo"))
        ref = await self.upload_image_ref_from_file_id(
            message, user_id=user_id, status_msg=status_msg, file_id=file_id,
            prompt=caption or "uploaded image", aspect_ratio=d.fmt_to_aspect(aspect_fmt),
        )
        try:
            await status_msg.delete()
        except Exception:
            pass
        if not ref:
            return False

        token = d.image_registry.add(ref)
        d.pending_edits[user_id] = token
        st = d.workspace(user_id)
        # Фото-референс из «Создать картинку» тарифицируется как генерация (10/15),
        # а не как правка (15/20). Флаг читают edit_confirm_kb/show_edit_confirm/es:apply.
        st["edit_as_gen"] = bool(as_generation)
        st["await"] = "edit_confirm" if caption else "edit"
        st["step"] = None
        st.pop("pending_prompt", None)
        st["edit_fmt"] = aspect_fmt
        st["edit_imodel"] = image_model or st.get("edit_imodel", d.default_image_model)
        st.pop("ag_variants", None)
        st.pop("edit_instruction", None)
        if caption:
            st["edit_instruction"] = caption
            await d.show_edit_confirm(message, user_id=user_id, edit=False)
        else:
            await message.answer(
                flow_copy.msg("photo_uploaded_ask_prompt"),
                reply_markup=d.edit_settings_kb(st["edit_fmt"], st["edit_imodel"]),
            )
        return True

    async def prepare_photo_video_from_file_id(
        self, message: types.Message, *, user_id: int, file_id: str, caption: str,
        vfmt: str | None = None, vstyle: str | None = None,
    ) -> bool:
        d = self._d
        d.vid_clear(user_id)
        st = d.workspace(user_id)
        d.clear_image_flow_keys(st)
        status_msg = await message.answer(flow_copy.msg("uploading_photo"))
        source = await self.upload_photo_source_from_file_id(
            message, user_id=user_id, status_msg=status_msg, file_id=file_id
        )
        try:
            await status_msg.delete()
        except Exception:
            pass
        if not source:
            return False
        st["vphoto"] = source
        st["vprompt"] = caption
        st["vstep"] = "vnewwiz"
        st["vmode"] = "ingredients"
        st["vfmt"] = vfmt or st.get("vfmt") or d.vid_default_fmt
        st.setdefault("vdur", 4)
        st.setdefault("vquality", "lite")
        st["vstyle"] = vstyle if vstyle is not None else st.get("vstyle", "")
        st["vmodel"] = d.nwiz_model(st)
        await d.show_new_video_wizard(message, user_id=user_id, edit=False)
        return True
