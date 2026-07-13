from __future__ import annotations

import hmac
from typing import Any, Mapping

from channels.base import IncomingCallback, IncomingMessage, PlatformUser


MAX_SECRET_HEADER = "X-Max-Bot-Api-Secret"


def verify_webhook_secret(headers: Mapping[str, str], expected_secret: str) -> bool:
    if not expected_secret:
        return False
    supplied = headers.get(MAX_SECRET_HEADER) or headers.get(MAX_SECRET_HEADER.lower()) or ""
    return hmac.compare_digest(str(supplied), str(expected_secret))


def parse_update(payload: Mapping[str, Any]) -> IncomingMessage | IncomingCallback | None:
    update_type = str(payload.get("update_type") or payload.get("type") or "").lower()
    if "callback" in update_type or payload.get("callback"):
        return _parse_callback(payload)
    if "message" in update_type or payload.get("message"):
        return _parse_message(payload)
    return None


def _parse_message(payload: Mapping[str, Any]) -> IncomingMessage | None:
    message = _nested_mapping(payload, "message") or payload
    user_data = _nested_mapping(message, "sender") or _nested_mapping(message, "user")
    if not user_data:
        return None
    text = str(message.get("text") or "")
    photo_ids = tuple(_attachment_ids(message, "image"))
    return IncomingMessage(
        platform="max",
        user=_platform_user(user_data),
        chat_id=str(message.get("chat_id") or _chat_id(message) or ""),
        message_id=str(message.get("message_id") or message.get("id") or ""),
        text=text or None,
        photo_file_ids=photo_ids,
        raw=payload,
    )


def _parse_callback(payload: Mapping[str, Any]) -> IncomingCallback | None:
    callback = _nested_mapping(payload, "callback") or payload
    user_data = _nested_mapping(callback, "user") or _nested_mapping(callback, "sender")
    if not user_data:
        return None
    return IncomingCallback(
        platform="max",
        user=_platform_user(user_data),
        chat_id=str(callback.get("chat_id") or _chat_id(callback) or ""),
        message_id=str(callback.get("message_id") or callback.get("message", {}).get("id") or ""),
        data=str(callback.get("payload") or callback.get("data") or ""),
        raw=payload,
    )


def _platform_user(data: Mapping[str, Any]) -> PlatformUser:
    return PlatformUser(
        platform="max",
        platform_user_id=str(data.get("user_id") or data.get("id") or ""),
        username=data.get("username"),
        first_name=data.get("first_name") or data.get("name"),
        last_name=data.get("last_name"),
        language_code=data.get("language_code"),
    )


def _nested_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    value = payload.get(key)
    return value if isinstance(value, Mapping) else None


def _chat_id(payload: Mapping[str, Any]) -> str | None:
    chat = payload.get("chat")
    if isinstance(chat, Mapping):
        value = chat.get("chat_id") or chat.get("id")
        return str(value) if value is not None else None
    return None


def _attachment_ids(message: Mapping[str, Any], attachment_type: str) -> list[str]:
    result: list[str] = []
    attachments = message.get("attachments") or []
    if not isinstance(attachments, list):
        return result
    for item in attachments:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") != attachment_type:
            continue
        payload = item.get("payload")
        if isinstance(payload, Mapping):
            # Prefer a downloadable URL (needed to fetch bytes for photo edit /
            # animate); fall back to token/file id so callers still get a ref.
            ref = (
                payload.get("url")
                or payload.get("token")
                or payload.get("file_id")
                or payload.get("id")
            )
            if ref:
                result.append(str(ref))
    return result
