"""Telegram Bot construction with optional SOCKS5 proxy (Phase 11 core split).

Builds an aiogram :class:`Bot` bound to ``TELEGRAM_TOKEN``, transparently routed
through a SOCKS5 proxy when ``TG_PROXY_URL`` is set. The SOCKS session is shared
process-wide (one connector, one ``aiohttp.ClientSession``) so reconnects reuse
the same pool. Extracted out of the flow_bot composition root; env values are
injected via :class:`BotFactoryDeps` so this module never imports flow_bot.

Telegram adapter code (uses aiogram/aiohttp), not a platform-neutral core module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiohttp
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession


@dataclass(frozen=True)
class BotFactoryDeps:
    token: str
    proxy_url: str
    log: Any


def make_bot(deps: BotFactoryDeps) -> Bot:
    """Создаёт Bot с SOCKS5 прокси если ``proxy_url`` задан."""
    if deps.proxy_url:
        try:
            from aiohttp_socks import ProxyConnector

            _proxy_url = deps.proxy_url.replace("socks5h://", "socks5://", 1)

            class _SocksSession(AiohttpSession):
                # Один коннектор и одна сессия на весь процесс.
                _connector = None
                _shared: aiohttp.ClientSession = None

                async def create_session(self) -> aiohttp.ClientSession:
                    if (
                        _SocksSession._shared is not None
                        and not _SocksSession._shared.closed
                    ):
                        return _SocksSession._shared
                    if _SocksSession._connector is None or _SocksSession._connector.closed:
                        _SocksSession._connector = ProxyConnector.from_url(
                            _proxy_url, rdns=True
                        )
                    _SocksSession._shared = aiohttp.ClientSession(
                        connector=_SocksSession._connector,
                        connector_owner=False,
                    )
                    return _SocksSession._shared

            deps.log.info("🧦 Telegram через SOCKS5: configured")
            return Bot(token=deps.token, session=_SocksSession())
        except ImportError:
            deps.log.error("❌ aiohttp-socks не установлен! pip install aiohttp-socks")

    return Bot(token=deps.token)
