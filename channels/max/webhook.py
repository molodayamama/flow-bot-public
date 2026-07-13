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
    if update_type == "message_callback" or payload.get("callback"):
        return _parse_callback(payload)
    if update_type == "message_created" or payload.get("message"):
        return _parse_message(payload)
    if update_type == "bot_started":
        return _parse_bot_started(payload)
    return None


def _parse_message(payload: Mapping[str, Any]) -> IncomingMessage | None:
    message = _nested_mapping(payload, "message") or payload
    body = _nested_mapping(message, "body") or message
    recipient = _nested_mapping(message, "recipient") or {}
    user_data = (
        _nested_mapping(message, "sender")
        or _nested_mapping(message, "user")
        or _nested_mapping(payload, "user")
    )
    if not user_data:
        return None
    user = _platform_user(user_data)
    chat_id = _first_value(
        recipient.get("chat_id"),
        message.get("chat_id"),
        payload.get("chat_id"),
        _chat_id(message),
    )
    if not user.platform_user_id or not chat_id:
        return None
    text = str(body.get("text") or "")
    photo_ids = tuple(_attachment_ids(body, "image"))
    return IncomingMessage(
        platform="max",
        user=user,
        chat_id=chat_id,
        message_id=_first_value(
            body.get("mid"),
            message.get("message_id"),
            message.get("id"),
            payload.get("message_id"),
        ) or None,
        text=text or None,
        photo_file_ids=photo_ids,
        raw=payload,
    )


def _parse_callback(payload: Mapping[str, Any]) -> IncomingCallback | None:
    callback = _nested_mapping(payload, "callback") or payload
    message = _nested_mapping(payload, "message") or {}
    body = _nested_mapping(message, "body") or {}
    recipient = _nested_mapping(message, "recipient") or {}
    user_data = (
        _nested_mapping(callback, "user")
        or _nested_mapping(callback, "sender")
        or _nested_mapping(payload, "user")
        or _nested_mapping(message, "sender")
    )
    if not user_data:
        return None
    user = _platform_user(user_data)
    chat_id = _first_value(
        payload.get("chat_id"),
        callback.get("chat_id"),
        recipient.get("chat_id"),
        _chat_id(callback),
    )
    if not user.platform_user_id or not chat_id:
        return None
    return IncomingCallback(
        platform="max",
        user=user,
        chat_id=chat_id,
        message_id=_first_value(
            payload.get("message_id"),
            callback.get("message_id"),
            body.get("mid"),
        ) or None,
        data=str(callback.get("payload") or callback.get("data") or ""),
        raw=payload,
    )


def _parse_bot_started(payload: Mapping[str, Any]) -> IncomingMessage | None:
    user_data = _nested_mapping(payload, "user")
    if not user_data:
        return None
    user = _platform_user(user_data)
    chat_id = _first_value(payload.get("chat_id"), payload.get("user_id"))
    if not user.platform_user_id or not chat_id:
        return None
    return IncomingMessage(
        platform="max",
        user=user,
        chat_id=chat_id,
        text="/start",
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


def _first_value(*values: Any) -> str:
    for value in values:
        if value is not None and str(value) != "":
            return str(value)
    return ""


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
