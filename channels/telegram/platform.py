"""Telegram implementation of the platform-neutral channel contract.

``TelegramPlatform`` adapts an aiogram ``Bot`` (or any compatible fake) to
``channels.base.BotPlatform`` so shared scenarios can send text and media to
Telegram without importing aiogram themselves. Keyboard conversion is reused
from :mod:`channels.telegram.renderer`; file downloads follow the same
``bot.download(file_id)`` pattern the monolith uses.
"""

from __future__ import annotations

from typing import Any

from aiogram.types import BufferedInputFile

from channels.base import Keyboard, PlatformFile, PlatformMedia
from channels.telegram.renderer import render_keyboard


_PARSE_MODE = "HTML"

# Reasonable default upload names per media kind when sending raw bytes.
_KIND_FILENAMES = {
    "photo": "photo.jpg",
    "video": "video.mp4",
    "document": "file.bin",
}

# Refine the extension from the file's mime type when it is known.
_MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "application/pdf": ".pdf",
}


def media_filename(media: PlatformMedia) -> str:
    """Pick an upload filename for bytes media (kind default + mime override)."""

    default = _KIND_FILENAMES.get(media.kind, "file.bin")
    mime_type = media.file.mime_type if media.file is not None else None
    extension = _MIME_EXTENSIONS.get(mime_type or "")
    if extension:
        return default.rsplit(".", 1)[0] + extension
    return default


def media_source(media: PlatformMedia) -> Any:
    """Resolve outbound media to what aiogram send methods accept.

    Priority: raw bytes (wrapped for upload) -> known Telegram file_id ->
    plain URL string (Bot API fetches it server-side).
    """

    if media.bytes_data is not None:
        return BufferedInputFile(media.bytes_data, filename=media_filename(media))
    if media.file is not None:
        return media.file.file_id
    return media.url


class TelegramPlatform:
    """``channels.base.BotPlatform`` adapter over an aiogram ``Bot``."""

    name = "telegram"

    def __init__(self, bot: Any) -> None:
        self.bot = bot

    async def send_message(
        self,
        chat_id: str,
        text: str,
        keyboard: Keyboard | None = None,
    ) -> Any:
        return await self.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=render_keyboard(keyboard),
            parse_mode=_PARSE_MODE,
        )

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        text: str,
        keyboard: Keyboard | None = None,
    ) -> Any:
        return await self.bot.edit_message_text(
            chat_id=chat_id,
            message_id=int(message_id),
            text=text,
            reply_markup=render_keyboard(keyboard),
            parse_mode=_PARSE_MODE,
        )

    async def answer_callback(self, callback_id: str, text: str | None = None) -> Any:
        return await self.bot.answer_callback_query(
            callback_query_id=callback_id,
            text=text,
        )

    async def send_photo(
        self,
        chat_id: str,
        media: PlatformMedia,
        keyboard: Keyboard | None = None,
    ) -> Any:
        return await self.bot.send_photo(
            chat_id=chat_id,
            photo=media_source(media),
            caption=media.caption,
            parse_mode=_PARSE_MODE if media.caption else None,
            reply_markup=render_keyboard(keyboard),
        )

    async def send_video(
        self,
        chat_id: str,
        media: PlatformMedia,
        keyboard: Keyboard | None = None,
    ) -> Any:
        return await self.bot.send_video(
            chat_id=chat_id,
            video=media_source(media),
            caption=media.caption,
            parse_mode=_PARSE_MODE if media.caption else None,
            reply_markup=render_keyboard(keyboard),
        )

    async def send_document(
        self,
        chat_id: str,
        media: PlatformMedia,
        keyboard: Keyboard | None = None,
    ) -> Any:
        return await self.bot.send_document(
            chat_id=chat_id,
            document=media_source(media),
            caption=media.caption,
            parse_mode=_PARSE_MODE if media.caption else None,
            reply_markup=render_keyboard(keyboard),
        )

    async def get_file_bytes(self, file: PlatformFile) -> bytes:
        # Same download pattern the monolith uses (aiogram 3.x Bot.download,
        # which resolves get_file + download_file internally).
        buf = await self.bot.download(file.file_id)
        return buf.read() if hasattr(buf, "read") else bytes(buf)
