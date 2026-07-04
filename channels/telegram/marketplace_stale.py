"""Marketplace stale-screen guard for Telegram callbacks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping

from aiogram import types


STALE_SCREEN_TEXT = "Это старый экран — открой актуальное меню"


@dataclass(frozen=True)
class MarketplaceStaleDeps:
    workspace: Callable[[int], MutableMapping[str, Any]]
    stale_text: str = STALE_SCREEN_TEXT


class MarketplaceStale:
    def __init__(self, deps: MarketplaceStaleDeps) -> None:
        self._d = deps

    def message_id(self, message) -> int:
        try:
            return int(getattr(message, "message_id", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def stamp_message(self, user_id: int, message) -> None:
        msg_id = self.message_id(message)
        if msg_id:
            self._d.workspace(user_id)["mp_active_msg_id"] = msg_id

    def is_stale_callback(self, user_id: int, callback: types.CallbackQuery) -> bool:
        current = self._d.workspace(user_id).get("mp_active_msg_id")
        try:
            current_id = int(current or 0)
        except (TypeError, ValueError):
            current_id = 0
        clicked_id = self.message_id(getattr(callback, "message", None))
        return bool(current_id and clicked_id and clicked_id != current_id)

    async def reject_stale_callback(self, callback: types.CallbackQuery) -> None:
        await callback.answer(self._d.stale_text, show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
