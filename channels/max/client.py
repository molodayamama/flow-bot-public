from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlparse

import aiohttp

from channels.base import Keyboard, PlatformFile, PlatformMedia
from channels.max.renderer import render_keyboard
from channels.max.transport import (
    MaxApiError,
    SlidingWindowRateLimiter,
    error_from_response,
    retry_delay,
)


# PlatformMedia.kind -> MAX attachment type (docs: image/video/audio/file).
_MEDIA_KIND_TO_MAX_TYPE = {
    "photo": "image",
    "video": "video",
    "document": "file",
}
_MAX_UPLOAD_HOSTS = frozenset({"fu.oneme.ru", "iu.oneme.ru", "vu.okcdn.ru"})
_MAX_INCOMING_IMAGE_BYTES = 50 * 1024 * 1024
_MAX_OUTGOING_VIDEO_BYTES = 250 * 1024 * 1024


MAX_ENABLED_ENV = "MAX_ENABLED"
MAX_BOT_TOKEN_ENV = "MAX_BOT_TOKEN"
MAX_WEBHOOK_SECRET_ENV = "MAX_WEBHOOK_SECRET"
MAX_WEBHOOK_URL_ENV = "MAX_WEBHOOK_URL"
MAX_INBOX_DB_ENV = "MAX_INBOX_DB"
MAX_INBOX_WORKERS_ENV = "MAX_INBOX_WORKERS"
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
_WEBHOOK_SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{5,256}$")


@dataclass(frozen=True)
class MaxConfig:
    enabled: bool = False
    bot_token: str = ""
    webhook_secret: str = ""
    webhook_url: str = ""
    inbox_db: str = "max_webhook_inbox.db"
    inbox_workers: int = 4
    api_base_url: str = DEFAULT_MAX_API_BASE_URL
    ca_bundle: str = ""
    mode: str = "poll"


def max_config_from_env(env: Mapping[str, str] | None = None) -> MaxConfig:
    source = os.environ if env is None else env
    enabled = str(source.get(MAX_ENABLED_ENV, "0")).strip().lower() in _TRUE_VALUES
    mode = str(source.get(MAX_MODE_ENV, "poll") or "poll").strip().lower()
    try:
        inbox_workers = int(str(source.get(MAX_INBOX_WORKERS_ENV, "4") or "4"))
    except ValueError:
        inbox_workers = 0
    return MaxConfig(
        enabled=enabled,
        bot_token=str(source.get(MAX_BOT_TOKEN_ENV, "") or ""),
        webhook_secret=str(source.get(MAX_WEBHOOK_SECRET_ENV, "") or ""),
        webhook_url=str(source.get(MAX_WEBHOOK_URL_ENV, "") or "").strip(),
        inbox_db=str(source.get(MAX_INBOX_DB_ENV, "max_webhook_inbox.db") or "max_webhook_inbox.db"),
        inbox_workers=inbox_workers,
        api_base_url=str(source.get(MAX_API_BASE_URL_ENV, DEFAULT_MAX_API_BASE_URL) or DEFAULT_MAX_API_BASE_URL).rstrip("/"),
        ca_bundle=str(source.get(MAX_CA_BUNDLE_ENV, "") or ""),
        mode=mode,
    )


def validate_max_config(config: MaxConfig, *, production: bool = False) -> None:
    """Fail closed on invalid enabled MAX configuration."""
    if not config.enabled:
        return
    errors: list[str] = []
    if not config.bot_token:
        errors.append(f"{MAX_BOT_TOKEN_ENV} is required")
    if config.mode not in {"poll", "webhook"}:
        errors.append(f"{MAX_MODE_ENV} must be poll or webhook")
    api = urlparse(config.api_base_url)
    if api.scheme != "https" or not api.hostname:
        errors.append(f"{MAX_API_BASE_URL_ENV} must be an HTTPS URL")
    if config.ca_bundle and not Path(config.ca_bundle).is_file():
        errors.append(f"{MAX_CA_BUNDLE_ENV} must point to a readable file")
    if production and config.mode != "webhook":
        errors.append("production MAX requires MAX_MODE=webhook")
    if config.mode == "webhook":
        hook = urlparse(config.webhook_url)
        if hook.scheme != "https" or not hook.hostname:
            errors.append(f"{MAX_WEBHOOK_URL_ENV} must be an HTTPS URL")
        if hook.port not in {None, 443}:
            errors.append(f"{MAX_WEBHOOK_URL_ENV} must use port 443")
        if not _WEBHOOK_SECRET_RE.fullmatch(config.webhook_secret):
            errors.append(
                f"{MAX_WEBHOOK_SECRET_ENV} must be 5-256 URL-safe characters"
            )
        if config.inbox_db == ":memory:":
            errors.append(f"{MAX_INBOX_DB_ENV} must be durable in webhook mode")
        if not 1 <= config.inbox_workers <= 32:
            errors.append(f"{MAX_INBOX_WORKERS_ENV} must be between 1 and 32")
    if errors:
        raise ValueError("; ".join(errors))


def build_client_from_env(env: Mapping[str, str] | None = None) -> "MaxBotClient | None":
    config = max_config_from_env(env)
    if not config.enabled:
        return None
    validate_max_config(config)
    return MaxBotClient(token=config.bot_token, base_url=config.api_base_url, ca_bundle=config.ca_bundle)


@dataclass
class MaxBotClient:
    """Async client for the MAX Bot API transport used by poll/webhook runtimes."""

    token: str
    base_url: str = DEFAULT_MAX_API_BASE_URL
    session: aiohttp.ClientSession | None = None
    name: str = "max"
    ca_bundle: str = ""
    request_timeout: float = 30.0
    max_retries: int = 2
    requests_per_second: int = 28
    max_download_bytes: int = _MAX_INCOMING_IMAGE_BYTES
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep, repr=False)
    _owns_session: bool = field(default=False, init=False, repr=False)
    _limiter: SlidingWindowRateLimiter = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._limiter = SlidingWindowRateLimiter(
            self.requests_per_second,
            sleep=self.sleep,
        )

    async def _get_session(self) -> Any:
        if self.session is None or getattr(self.session, "closed", False):
            self.session = aiohttp.ClientSession()
            self._owns_session = True
        return self.session

    async def close(self) -> None:
        if self._owns_session and self.session is not None:
            await self.session.close()
            self.session = None
            self._owns_session = False

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

    async def send_message_to_user(
        self,
        user_id: str,
        text: str,
        keyboard: Keyboard | None = None,
    ) -> Any:
        """Send a direct message using the official ``user_id`` target."""
        payload = self.build_send_message_payload(
            chat_id="", text=text, keyboard=keyboard
        )
        return await self._request(
            "POST", "/messages", params={"user_id": user_id}, json=payload
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
        then multipart POST; the upload response yields the attachment token).
        URL-only or already-tokenised media needs no upload.
        """
        media_bytes = media.bytes_data
        if media_bytes is None and media.url and max_type != "image":
            # MAX accepts direct URLs for images only. Generated video/document
            # URLs must be downloaded and uploaded to MAX before /messages.
            limit = (
                _MAX_OUTGOING_VIDEO_BYTES
                if max_type == "video"
                else self.max_download_bytes
            )
            media_bytes = await self._raw_get_bytes(media.url, max_bytes=limit)
        if media_bytes is not None:
            limit = (
                _MAX_OUTGOING_VIDEO_BYTES
                if max_type == "video"
                else self.max_download_bytes
            )
            if len(media_bytes) > limit:
                raise MaxApiError(
                    status=None, code="media_too_large", retryable=False
                )
        if media_bytes is None:
            if media.file is not None and media.file.file_id:
                return media.file.file_id
            return None
        start = await self._request(
            "POST", "/uploads", params={"type": max_type}, retryable=True
        )
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
            media_bytes,
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
            "POST",
            "/messages",
            params={"chat_id": chat_id},
            json=payload,
            retry_error_codes=frozenset({"attachment.not.ready"}),
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
        return await self._request(
            "GET",
            "/updates",
            params=params,
            request_timeout=float(params["timeout"]) + 10.0,
        )

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
        return await self._request(
            "POST", "/subscriptions", json=payload, retryable=True
        )

    async def unsubscribe_webhook(self, *, url: str) -> Any:
        return await self._request("DELETE", "/subscriptions", params={"url": url})

    async def _raw_get_bytes(self, url: str, *, max_bytes: int | None = None) -> bytes:
        """GET raw bytes from an absolute URL (file download, no JSON parse)."""
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise MaxApiError(
                status=None, code="media_url_invalid", retryable=False
            )
        session = await self._get_session()
        byte_limit = self.max_download_bytes if max_bytes is None else max(1, int(max_bytes))
        try:
            async with session.get(
                url,
                ssl=self._ssl_arg(),
                timeout=aiohttp.ClientTimeout(total=120.0),
            ) as response:
                status = int(getattr(response, "status", 200))
                final_url = urlparse(str(getattr(response, "url", None) or url))
                if final_url.scheme != "https" or not final_url.hostname:
                    raise MaxApiError(
                        status=status, code="media_url_invalid", retryable=False
                    )
                if status >= 400:
                    raise MaxApiError(
                        status=status,
                        code="media_download_failed",
                        retryable=False,
                    )
                raw_length = getattr(response, "content_length", None)
                try:
                    content_length = int(raw_length) if raw_length is not None else None
                except (TypeError, ValueError):
                    content_length = None
                if content_length is not None and content_length > byte_limit:
                    raise MaxApiError(
                        status=status, code="media_too_large", retryable=False
                    )

                content = getattr(response, "content", None)
                if content is not None and callable(getattr(content, "iter_chunked", None)):
                    data = bytearray()
                    async for chunk in content.iter_chunked(64 * 1024):
                        data.extend(chunk)
                        if len(data) > byte_limit:
                            raise MaxApiError(
                                status=status, code="media_too_large", retryable=False
                            )
                    return bytes(data)

                data = await response.read()
                if len(data) > byte_limit:
                    raise MaxApiError(
                        status=status, code="media_too_large", retryable=False
                    )
                return data
        except MaxApiError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise MaxApiError(
                status=None, code="media_download_failed", retryable=True
            ) from None

    async def _raw_post_multipart(
        self, url: str, data: bytes, *, filename: str = "upload.bin"
    ) -> Any:
        """POST one multipart ``data`` file to a MAX-provided upload URL."""
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.hostname.lower() not in _MAX_UPLOAD_HOSTS
        ):
            raise MaxApiError(
                status=None, code="upload_url_invalid", retryable=False
            )
        session = await self._get_session()
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
                timeout=aiohttp.ClientTimeout(total=120.0),
            ) as response:
                payload = await self._decode_response(response)
                status = int(getattr(response, "status", 200))
                if status >= 400:
                    raise error_from_response(
                        status, payload, getattr(response, "headers", {})
                    )
                return payload
        except MaxApiError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise MaxApiError(
                status=None, code="media_upload_failed", retryable=True
            ) from None

    @staticmethod
    async def _decode_response(response: Any) -> Any:
        if response.content_type == "application/json":
            try:
                return await response.json()
            except (TypeError, ValueError):
                status = int(getattr(response, "status", 200))
                raise MaxApiError(
                    status=status,
                    code="invalid_response",
                    retryable=status >= 500,
                ) from None
        return await response.text()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        retryable: bool | None = None,
        retry_error_codes: frozenset[str] = frozenset(),
        request_timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        session = await self._get_session()
        may_retry = (
            method.upper() in {"GET", "PUT", "DELETE"}
            if retryable is None
            else bool(retryable)
        )
        timeout = aiohttp.ClientTimeout(total=request_timeout or self.request_timeout)
        for attempt in range(max(0, int(self.max_retries)) + 1):
            await self._limiter.acquire()
            try:
                async with session.request(
                    method,
                    f"{self.base_url.rstrip('/')}{path}",
                    headers=self._headers(),
                    ssl=self._ssl_arg(),
                    timeout=timeout,
                    **kwargs,
                ) as response:
                    payload = await self._decode_response(response)
                    status = int(getattr(response, "status", 200))
                    if status >= 400:
                        raise error_from_response(
                            status, payload, getattr(response, "headers", {})
                        )
                    return payload
            except MaxApiError as exc:
                error = exc
            except (aiohttp.ClientError, asyncio.TimeoutError):
                error = MaxApiError(
                    status=None,
                    code="transport_error",
                    retryable=True,
                )
            retry_this_error = (
                (may_retry and error.retryable) or error.code in retry_error_codes
            )
            if not retry_this_error or attempt >= self.max_retries:
                raise error from None
            await self.sleep(retry_delay(error, attempt))
        raise AssertionError("unreachable")
