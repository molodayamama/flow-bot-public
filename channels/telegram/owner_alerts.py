"""Owner alert sender for Telegram adapters."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class OwnerAlertsDeps:
    owner_ids: Iterable[int]
    bot: Any
    get_running_loop: Callable[[], Any] = asyncio.get_running_loop


class OwnerAlerts:
    def __init__(self, deps: OwnerAlertsDeps) -> None:
        self._d = deps

    async def send(self, text: str) -> None:
        """Send a plain-text Telegram message to every owner. Never raises."""
        for owner_id in tuple(self._d.owner_ids):
            try:
                await self._d.bot.send_message(owner_id, text, parse_mode="HTML")
            except Exception:
                pass

    def fire(self, text: str) -> None:
        """Schedule owner alert from a synchronous call-site."""
        try:
            loop = self._d.get_running_loop()
            loop.create_task(self.send(text))
        except RuntimeError:
            pass
