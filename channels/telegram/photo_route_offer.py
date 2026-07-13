"""Captioned-photo route prompt for the Telegram adapter."""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Callable, MutableMapping

from aiogram import types

import flow_copy
from textutil import _short_prompt


@dataclass(frozen=True)
class PhotoRouteOfferDeps:
    pending_photo_routes: MutableMapping[int, dict]
    photo_route_kb: Callable[[], types.InlineKeyboardMarkup]


class PhotoRouteOffer:
    def __init__(self, deps: PhotoRouteOfferDeps) -> None:
        self._d = deps

    def store(self, user_id: int, *, file_id: str, caption: str) -> None:
        self._d.pending_photo_routes[user_id] = {
            "file_id": file_id,
            "caption": caption.strip()[:2000],
        }

    def store_many(self, user_id: int, *, file_ids: list[str], caption: str) -> None:
        clean = [file_id for file_id in file_ids[:4] if file_id]
        self._d.pending_photo_routes[user_id] = {
            "file_id": clean[0] if clean else "",
            "file_ids": clean,
            "caption": caption.strip()[:2000],
        }

    async def offer(self, message: types.Message, *, user_id: int, caption: str) -> None:
        self.store(user_id, file_id=message.photo[-1].file_id, caption=caption)
        await message.answer(
            flow_copy.msg(
                "photo_route_choice",
                prompt=html.escape(_short_prompt(caption, 300)),
            ),
            reply_markup=self._d.photo_route_kb(),
            parse_mode="HTML",
        )

    async def offer_many(
        self, message: types.Message, *, user_id: int, file_ids: list[str], caption: str,
    ) -> None:
        self.store_many(user_id, file_ids=file_ids, caption=caption)
        await message.answer(
            flow_copy.msg(
                "photo_route_choice",
                prompt=html.escape(_short_prompt(caption, 300)),
            ),
            reply_markup=self._d.photo_route_kb(),
            parse_mode="HTML",
        )
