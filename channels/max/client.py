from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

import aiohttp

from channels.base import Keyboard, PlatformFile, PlatformMedia
from channels.max.renderer import render_keyboard


# PlatformMedia.kind -> MAX attachment type (docs: image/video/audio/file).
_MEDIA_KIND_TO_MAX_TYPE = {
    "photo": "image",
    "video": "video",
    "document": "file",
}


MAX_ENABLED_ENV = "MAX_ENABLED"
MAX_BOT_TOKEN_ENV = "MAX_BOT_TOKEN"
MAX_WEBHOOK_SECRET_ENV = "MAX_WEBHOOK_SECRET"
MAX_WEBHOOK_URL_ENV = "MAX_WEBHOOK_URL"
MAX_API_BASE_URL_ENV = "MAX_API_BASE_URL"
# Path to a CA bundle (PEM) that trusts the MAX API root. platform-api2.max.ru
# presents a chain rooted in the Russian Trusted Root CA, which is not in the
# Mozilla/certifi bundle; the operator points this at that CA (or installs it
# system-wide). SSL verification is never disabled.
MAX_CA_BUNDLE_ENV = "MAX_CA_BUNDLE"
# Intake mode: "poll" (long-polling, default) or "webhook" (production).
MAX_MODE_ENV = "MAX_MODE"
DEFAULT_MAX_API_BASE_URL = "https://platform-api2.max.ru"
_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class MaxConfig:
    enabled: bool = False
    bot_token: str = ""
    webhook_secret: str = ""
    webhook_url: str = ""
    api_base_url: str = DEFAULT_MAX_API_BASE_URL
    ca_bundle: str = ""
    mode: str = "poll"


def max_config_from_env(env: Mapping[str, str] | None = None) -> MaxConfig:
    source = os.environ if env is None else env
    enabled = str(source.get(MAX_ENABLED_ENV, "0")).strip().lower() in _TRUE_VALUES
    mode = str(source.get(MAX_MODE_ENV, "poll") or "poll").strip().lower()
    return MaxConfig(
        enabled=enabled,
        bot_token=str(source.get(MAX_BOT_TOKEN_ENV, "") or ""),
        webhook_secret=str(source.get(MAX_WEBHOOK_SECRET_ENV, "") or ""),
        webhook_url=str(source.get(MAX_WEBHOOK_URL_ENV, "") or "").strip(),
        api_base_url=str(source.get(MAX_API_BASE_URL_ENV, DEFAULT_MAX_API_BASE_URL) or DEFAULT_MAX_API_BASE_URL).rstrip("/"),
        ca_bundle=str(source.get(MAX_CA_BUNDLE_ENV, "") or ""),
        mode="webhook" if mode == "webhook" else "poll",
    )


def build_client_from_env(env: Mapping[str, str] | None = None) -> "MaxBotClient | None":
    config = max_config_from_env(env)
    if not config.enabled:
        return None
    if not config.bot_token:
        raise ValueError(f"{MAX_BOT_TOKEN_ENV} is required when {MAX_ENABLED_ENV}=1")
    return MaxBotClient(token=config.bot_token, base_url=config.api_base_url, ca_bundle=config.ca_bundle)


@dataclass
class MaxBotClient:
    """Async client for the MAX Bot API transport used by poll/webhook runtimes."""

    token: str
    base_url: str = DEFAULT_MAX_API_BASE_URL
    session: aiohttp.ClientSession | None = None
    name: str = "max"
    ca_bundle: str = ""

    def _ssl_arg(self) -> Any:
        """SSL argument for aiohttp: a context trusting ``ca_bundle`` when set.

        Returns None (aiohttp default verification) when no bundle is configured.
        Verification is never disabled — MAX's Russian Trusted Root CA must be
        supplied via MAX_CA_BUNDLE or installed system-wide.
        """
        if not self.ca_bundle:
            return None
        import ssl

        return ssl.create_default_context(cafile=self.ca_bundle)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self.token,
            "Content-Type": "application/json",
        }

    def build_send_message_payload(
        self,
        *,
        chat_id: str,
        text: str,
        keyboard: Keyboard | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"text": text}
        attachment = render_keyboard(keyboard)
        if attachment:
            payload["attachments"] = [attachment]
        return payload

    async def send_message(
        self,
        chat_id: str,
        text: str,
        keyboard: Keyboard | None = None,
    ) -> Any:
        payload = self.build_send_message_payload(chat_id=chat_id, text=text, keyboard=keyboard)
        return await self._request(
            "POST", "/messages", params={"chat_id": chat_id}, json=payload
        )

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        text: str,
        keyboard: Keyboard | None = None,
    ) -> Any:
        """Edit a previously sent message's text/keyboard (best-effort contract)."""
        payload: dict[str, Any] = {"text": text}
        kb = render_keyboard(keyboard)
        if kb:
            payload["attachments"] = [kb]
        return await self._request(
            "PUT", "/messages", params={"message_id": message_id}, json=payload
        )

    async def answer_callback(self, callback_id: str, text: str | None = None) -> Any:
        payload: dict[str, Any] = {}
        if text:
            payload["notification"] = text
        return await self._request(
            "POST", "/answers", params={"callback_id": callback_id}, json=payload
        )

    # ── media ────────────────────────────────────────────────────────────
    def build_media_message_payload(
        self,
        *,
        chat_id: str,
        media: PlatformMedia,
        token: str | None = None,
        keyboard: Keyboard | None = None,
    ) -> dict[str, Any]:
        """Build a POST /messages body carrying one media attachment.

        MAX attachments reference either an uploaded ``token`` or (images only,
        per the API) a direct ``url``. A caption maps to the message ``text``.
        """
        max_type = _MEDIA_KIND_TO_MAX_TYPE.get(media.kind, "file")
        attach_payload: dict[str, Any] = {}
        if token:
            attach_payload["token"] = token
        elif media.url:
            attach_payload["url"] = media.url
        body: dict[str, Any] = {
            "attachments": [{"type": max_type, "payload": attach_payload}],
        }
        if media.caption:
            body["text"] = media.caption
        kb = render_keyboard(keyboard)
        if kb:
            body["attachments"].append(kb)
        return body

    async def _resolve_media_token(self, media: PlatformMedia, max_type: str) -> str | None:
        """Obtain an upload token for byte media; None lets url/token pass through.

        Uploads the raw bytes when present (POST /uploads to get an upload URL,
        then PUT the bytes; the upload response yields the attachment token).
        URL-only or already-tokenised media needs no upload.
        """
        if media.bytes_data is None:
            if media.file is not None and media.file.file_id:
                return media.file.file_id
            return None
        start = await self._request("POST", "/uploads", params={"type": max_type})
        if not isinstance(start, Mapping):
            return None
        upload_url = start.get("url")
        if not upload_url:
            # Some responses hand back the token directly.
            token = start.get("token")
            return str(token) if token else None
        initial_token = start.get("token")
        done = await self._raw_post_multipart(
            str(upload_url),
            media.bytes_data,
            filename=(media.file.file_id if media.file and media.file.file_id else "upload.bin"),
        )
        if isinstance(done, Mapping):
            token = done.get("token") or (done.get("image") or {}).get("token")
            if token:
                return str(token)
        return str(initial_token) if initial_token else None

    async def _send_media(
        self, chat_id: str, media: PlatformMedia, keyboard: Keyboard | None
    ) -> Any:
        max_type = _MEDIA_KIND_TO_MAX_TYPE.get(media.kind, "file")
        token = await self._resolve_media_token(media, max_type)
        payload = self.build_media_message_payload(
            chat_id=chat_id, media=media, token=token, keyboard=keyboard
        )
        return await self._request(
            "POST", "/messages", params={"chat_id": chat_id}, json=payload
        )

    async def send_photo(
        self, chat_id: str, media: PlatformMedia, keyboard: Keyboard | None = None
    ) -> Any:
        return await self._send_media(chat_id, media, keyboard)

    async def send_video(
        self, chat_id: str, media: PlatformMedia, keyboard: Keyboard | None = None
    ) -> Any:
        return await self._send_media(chat_id, media, keyboard)

    async def send_document(
        self, chat_id: str, media: PlatformMedia, keyboard: Keyboard | None = None
    ) -> Any:
        return await self._send_media(chat_id, media, keyboard)

    async def get_file_bytes(self, file: PlatformFile) -> bytes:
        """Download a file's bytes from its URL (MAX attachments carry a url)."""
        if not file.url:
            raise ValueError("PlatformFile has no url to download from")
        return await self._raw_get_bytes(file.url)

    async def get_updates(
        self,
        marker: int | None = None,
        limit: int = 100,
        *,
        timeout: int = 30,
        types: tuple[str, ...] | list[str] | None = None,
    ) -> Any:
        params: dict[str, Any] = {
            "limit": max(1, min(int(limit), 1000)),
            "timeout": max(0, min(int(timeout), 90)),
        }
        if marker is not None:
            params["marker"] = int(marker)
        if types:
            params["types"] = ",".join(str(item) for item in types)
        return await self._request("GET", "/updates", params=params)

    async def get_subscriptions(self) -> Any:
        return await self._request("GET", "/subscriptions")

    async def subscribe_webhook(
        self,
        *,
        url: str,
        secret: str,
        update_types: tuple[str, ...] | list[str] | None = None,
    ) -> Any:
        payload: dict[str, Any] = {"url": url, "secret": secret}
        if update_types:
            payload["update_types"] = [str(item) for item in update_types]
        return await self._request("POST", "/subscriptions", json=payload)

    async def unsubscribe_webhook(self, *, url: str) -> Any:
        return await self._request("DELETE", "/subscriptions", params={"url": url})

    async def _raw_get_bytes(self, url: str) -> bytes:
        """GET raw bytes from an absolute URL (file download, no JSON parse)."""
        session = self.session
        owns_session = session is None
        if session is None:
            session = aiohttp.ClientSession()
        try:
            async with session.get(url, ssl=self._ssl_arg()) as response:
                response.raise_for_status()
                return await response.read()
        finally:
            if owns_session:
                await session.close()

    async def _raw_post_multipart(
        self, url: str, data: bytes, *, filename: str = "upload.bin"
    ) -> Any:
        """POST one multipart ``data`` file to a MAX-provided upload URL."""
        session = self.session
        owns_session = session is None
        if session is None:
            session = aiohttp.ClientSession()
        try:
            form = aiohttp.FormData()
            form.add_field(
                "data",
                data,
                filename=filename,
                content_type="application/octet-stream",
            )
            async with session.post(
                url,
                data=form,
                headers={"Authorization": self.token},
                ssl=self._ssl_arg(),
            ) as response:
                response.raise_for_status()
                if response.content_type == "application/json":
                    return await response.json()
                return await response.text()
        finally:
            if owns_session:
                await session.close()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        session = self.session
        owns_session = session is None
        if session is None:
            session = aiohttp.ClientSession()
        try:
            async with session.request(
                method,
                f"{self.base_url.rstrip('/')}{path}",
                headers=self._headers(),
                ssl=self._ssl_arg(),
                **kwargs,
            ) as response:
                response.raise_for_status()
                if response.content_type == "application/json":
                    return await response.json()
                return await response.text()
        finally:
            if owns_session:
                await session.close()
