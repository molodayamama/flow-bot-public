"""Uploaded-video message input router (Phase 6).

Owns the ``F.video | F.document`` MESSAGE handler (user uploads a clip for
"edit my video"). The ``vu:`` CALLBACK router lives in video_upload.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, MutableMapping

from aiogram import F, Router, types

import flow_copy
from flow_core import (
    action_price,
    video_duration_from_poll_item,
    video_edit_end_frame,
)


@dataclass(frozen=True)
class VideoUploadInputDeps:
    """Injected state, config, transport, and video-edit helpers."""

    workspace: Callable[[int], MutableMapping[str, Any]]
    upload_video_edit_enabled: Callable[[], bool]
    bot: Any
    log: Any
    metrics: Any
    account_for_video: Callable[[int], Any]
    ensure_user_project: Callable[..., Awaitable[Any]]
    keeper_for_acc: Callable[[Any], Any]
    client_for_acc: Callable[[Any], Any]
    video_edit_uploaded: Callable[..., Awaitable[Any]]


def create_router(deps: VideoUploadInputDeps) -> Router:
    """Build the video/document message-input router."""

    router = Router(name="tg-video-upload-input")

    @router.message(F.video | F.document)
    async def handle_video_upload(message: types.Message):
        """Приём пользовательского видео для режима «Изменить своё видео»."""
        user_id = message.from_user.id
        st = deps.workspace(user_id)
        if not deps.upload_video_edit_enabled() or st.get("vawait") != "vu_video":
            return  # видео ждём только в этом режиме (и пока фича включена) — иначе игнор
        file_obj = message.video or message.document
        if file_obj is None:
            return
        # Документы-картинки сюда не относим (для них есть обычный фото-флоу).
        mime = (getattr(file_obj, "mime_type", "") or "")
        if message.document and not mime.startswith("video"):
            await message.answer(flow_copy.msg("vid_upload_need_video"))
            return
        status = await message.answer(flow_copy.msg("vid_upload_working"))
        try:
            buf = await deps.bot.download(file_obj.file_id)
            data = buf.read() if hasattr(buf, "read") else bytes(buf)
        except Exception:
            deps.log.exception("download user video failed")
            await status.edit_text(flow_copy.msg("vid_upload_failed"))
            return
        acc_id = deps.account_for_video(user_id)
        if acc_id is None:
            await status.edit_text(flow_copy.msg("accounts_unavailable"))
            return
        project_id = await deps.ensure_user_project(user_id, account_id=acc_id)
        source = await deps.keeper_for_acc(acc_id).upload_video(
            data,
            filename=f"tg_{user_id}.mp4",
            project_id=project_id,
            content_type=mime or "video/mp4",
        )
        if not source or not source.get("mediaId"):
            await status.edit_text(flow_copy.msg("vid_upload_failed"))
            return
        # Транскод на стороне сервиса: без ожидания SUCCESSFUL правка падает FAILED.
        source.setdefault("_account_id", acc_id)
        ready_item = await deps.client_for_acc(acc_id).wait_video_ready(
            source["mediaId"], source.get("_project_id") or project_id or ""
        )
        if ready_item is None:
            await status.edit_text(flow_copy.msg("vid_upload_failed"))
            return
        # Реальная длительность клипа → endFrameIndex правки (кадры за концом клипа
        # роняют edit-джобу). Сервер надёжнее Telegram (документы без duration).
        source["duration_s"] = (
            video_duration_from_poll_item(ready_item)
            or float(getattr(file_obj, "duration", 0) or 0)
            or None
        )
        if source["duration_s"] is None:
            # endFrameIndex упадёт в дефолт 240: для клипа короче 8с правка уйдёт в
            # FAILED на стороне сервиса — пусть причина будет видна в логах.
            deps.log.warning("🎬 upload: длительность не определена (ни poll, ни Telegram) — "
                        "endFrameIndex возьмёт дефолт %s", video_edit_end_frame(None))
        st["vu_source"] = source
        deps.metrics.log_event("video_upload_edit_started", user_id=user_id, source="upload")
        try:
            await status.delete()
        except Exception:
            pass
        caption = (message.caption or "").strip()
        if len(caption) >= 3:
            # Видео пришло сразу с текстом правки — не переспрашиваем, генерируем.
            st["vawait"] = None
            await deps.video_edit_uploaded(message, caption, user_id=user_id)
            return
        st["vawait"] = "vu_edit_prompt"
        await message.answer(
            flow_copy.msg("vid_upload_ask_prompt", price=action_price("video_prompt_edit"))
        )

    return router
