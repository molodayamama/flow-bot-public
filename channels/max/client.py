from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

import aiohttp

from channels.base import Keyboard
from channels.max.renderer import render_keyboard


MAX_ENABLED_ENV = "MAX_ENABLED"
MAX_BOT_TOKEN_ENV = "MAX_BOT_TOKEN"
MAX_WEBHOOK_SECRET_ENV = "MAX_WEBHOOK_SECRET"
MAX_API_BASE_URL_ENV = "MAX_API_BASE_URL"
DEFAULT_MAX_API_BASE_URL = "https://platform-api2.max.ru"
_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class MaxConfig:
    enabled: bool = False
    bot_token: str = ""
    webhook_secret: str = ""
    api_base_url: str = DEFAULT_MAX_API_BASE_URL


def max_config_from_env(env: Mapping[str, str] | None = None) -> MaxConfig:
    source = os.environ if env is None else env
    enabled = str(source.get(MAX_ENABLED_ENV, "0")).strip().lower() in _TRUE_VALUES
    return MaxConfig(
        enabled=enabled,
        bot_token=str(source.get(MAX_BOT_TOKEN_ENV, "") or ""),
        webhook_secret=str(source.get(MAX_WEBHOOK_SECRET_ENV, "") or ""),
        api_base_url=str(source.get(MAX_API_BASE_URL_ENV, DEFAULT_MAX_API_BASE_URL) or DEFAULT_MAX_API_BASE_URL).rstrip("/"),
    )


def build_client_from_env(env: Mapping[str, str] | None = None) -> "MaxBotClient | None":
    config = max_config_from_env(env)
    if not config.enabled:
        return None
    if not config.bot_token:
        raise ValueError(f"{MAX_BOT_TOKEN_ENV} is required when {MAX_ENABLED_ENV}=1")
    return MaxBotClient(token=config.bot_token, base_url=config.api_base_url)


@dataclass
class MaxBotClient:
    """Small async client skeleton for MAX Bot API calls.

    The refactor branch does not instantiate or call this client from production
    code yet. Tests use payload builders and fakes only.
    """

    token: str
    base_url: str = DEFAULT_MAX_API_BASE_URL
    session: aiohttp.ClientSession | None = None

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
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
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
        return await self._request("POST", "/messages", json=payload)

    async def answer_callback(self, callback_id: str, text: str | None = None) -> Any:
        payload: dict[str, Any] = {"callback_id": callback_id}
        if text:
            payload["notification"] = text
        return await self._request("POST", "/answers", json=payload)

    async def get_updates(self, marker: int | None = None, limit: int = 100) -> Any:
        params: dict[str, Any] = {"limit": max(1, min(int(limit), 100))}
        if marker is not None:
            params["marker"] = int(marker)
        return await self._request("GET", "/updates", params=params)

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
                **kwargs,
            ) as response:
                response.raise_for_status()
                if response.content_type == "application/json":
                    return await response.json()
                return await response.text()
        finally:
            if owns_session:
                await session.close()

