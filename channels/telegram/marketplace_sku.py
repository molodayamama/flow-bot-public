"""Marketplace SKU helpers for the Telegram adapter."""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any, Callable, MutableMapping

from aiogram import types


@dataclass(frozen=True)
class MarketplaceSkuDeps:
    workspace: Callable[[int], MutableMapping[str, Any]]
    metrics: Any
    menu_button: Callable[..., types.InlineKeyboardButton]


class MarketplaceSku:
    def __init__(self, deps: MarketplaceSkuDeps) -> None:
        self._d = deps

    def choice_kb(self, user_id: int) -> types.InlineKeyboardMarkup:
        d = self._d
        B = types.InlineKeyboardButton
        choices = d.metrics.recent_seller_skus(user_id, limit=5)
        d.workspace(user_id)["mp_sku_choices"] = choices
        rows = [
            [B(text=f"📦 {sku[:48]}", callback_data=f"mp:sku:{idx}")]
            for idx, sku in enumerate(choices)
        ]
        rows.append([B(text="➕ Новый SKU / артикул", callback_data="mp:sku:new")])
        rows.append([d.menu_button("menu", "m:menu")])
        return types.InlineKeyboardMarkup(inline_keyboard=rows)

    def pending_payload(self, user_id: int) -> dict | None:
        payload = self._d.workspace(user_id).get("mp_sku_pending")
        return payload if isinstance(payload, dict) else None

    def latest_payload(self, user_id: int, *, platform: str | None = None) -> dict | None:
        d = self._d
        rows = d.metrics.get_gallery(user_id, limit=1)
        if not rows:
            return None
        row = rows[0]
        file_id = str(row.get("file_id") or "")
        if not file_id:
            return None
        return {
            "file_id": file_id,
            "token": str(row.get("token") or ""),
            "prompt": str(row.get("prompt") or ""),
            "platform": platform or str(d.workspace(user_id).get("mp_platform") or ""),
        }

    async def save_payload(
        self, message: types.Message, user_id: int, sku: str, payload: dict
    ) -> bool:
        d = self._d
        if not payload:
            await message.answer(
                "Кнопка устарела. Нажми «➕ В серию SKU» под нужной картинкой ещё раз."
            )
            return False
        row_id = d.metrics.save_seller_sku_item(
            user_id,
            sku,
            file_id=str(payload.get("file_id") or ""),
            token=str(payload.get("token") or ""),
            prompt=str(payload.get("prompt") or ""),
            platform=str(payload.get("platform") or ""),
        )
        if row_id <= 0:
            await message.answer(
                "Не удалось сохранить SKU. Проверь название и попробуй ещё раз."
            )
            return False
        return True

    async def save_pending_item(
        self, message: types.Message, user_id: int, sku: str
    ) -> bool:
        d = self._d
        payload = self.pending_payload(user_id)
        if not payload:
            d.workspace(user_id).pop("mp_sku_pending", None)
            await message.answer(
                "Кнопка устарела. Нажми «➕ В серию SKU» под нужной картинкой ещё раз."
            )
            return False
        if not await self.save_payload(message, user_id, sku, payload):
            return False
        st = d.workspace(user_id)
        st.pop("mp_sku_pending", None)
        st.pop("mp_sku_choices", None)
        st["await"] = None
        d.metrics.log_event(
            "mp_sku_saved", user_id=user_id, source=str(payload.get("platform") or "seller")
        )
        await message.answer(
            f"📦 Добавлено в SKU <b>{html.escape(sku.strip())}</b>.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="📦 Мои товары", callback_data="mp:projects")],
                [d.menu_button("menu", "m:menu")],
            ]),
            parse_mode="HTML",
        )
        return True
