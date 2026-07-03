"""Image result delivery for the Telegram adapter (Phase 11 core split).

Sends generated images with their per-image action keyboard, and the shared
multi-image result loop with the author-referral footer. Extracted out of the
flow_bot composition root; dependencies (runtime registries/stores, keyboards,
the late-bound bot username) are injected via :class:`ImageDeliveryDeps` so this
module never imports flow_bot. It is Telegram-adapter code (uses aiogram), not a
platform-neutral core module.
"""

from __future__ import annotations

import asyncio
import html
from dataclasses import dataclass
from typing import Any, Callable

import aiohttp
from aiogram import types
from aiogram.types import BufferedInputFile

import flow_copy
from flow_core import ImageRef
from textutil import _short_prompt


@dataclass(frozen=True)
class ImageDeliveryDeps:
    keeper_for_acc: Callable[[str | None], Any]
    image_registry: Any
    workspace: Callable[[int], dict]
    is_seller: Callable[[], bool]
    seller_image_keyboard: Callable[[str], Any]
    image_keyboard: Callable[[str], Any]
    metrics: Any
    log: Any
    referral_link: Callable[[int], str]
    bot_username: Callable[[], str]
    # after-result menu deps
    credit_store: Any
    menu_button: Callable[..., Any]
    invite_button: Callable[[int], Any]
    first_referral_cta_text: Callable[[int], str | None]


class ImageDelivery:
    def __init__(self, deps: ImageDeliveryDeps) -> None:
        self._d = deps

    async def send_one_image(
        self, message, *, url: str, img: dict, index: int, total: int, caption: str,
        user_id: int, project_id: str | None, prompt: str, aspect_ratio: str,
        account_id: str | None = None,
    ) -> None:
        """Отправить одну картинку с кнопкой «Редактировать», привязанной к ней."""
        d = self._d
        # Запоминаем сырой объект картинки, чтобы бот мог изучить формат правки.
        d.keeper_for_acc(account_id).note_image(img)
        token = d.image_registry.add(
            ImageRef(
                user_id=user_id,
                project_id=project_id,
                source=img,
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                account_id=account_id,
                platform=d.workspace(user_id).get("mp_platform", "") if d.is_seller() else "",
            )
        )
        keyboard = d.seller_image_keyboard(token) if d.is_seller() else d.image_keyboard(token)
        try:
            sent = await message.reply_photo(
                photo=url, caption=caption, reply_markup=keyboard, parse_mode="HTML"
            )
            if sent and sent.photo:
                d.metrics.save_to_gallery(
                    user_id, sent.photo[-1].file_id, token=token,
                    prompt=prompt[:400] if prompt else None,
                )
            return
        except Exception as e:
            d.log.error(f"Ошибка отправки фото {index}/{total}: {e}")

        # Если прямая ссылка не работает — скачиваем и отправляем байтами.
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                    if r.status == 200:
                        data = await r.read()
                        sent2 = await message.reply_photo(
                            photo=BufferedInputFile(data, f"img_{index}.png"),
                            caption=caption,
                            reply_markup=keyboard,
                            parse_mode="HTML",
                        )
                        if sent2 and sent2.photo:
                            d.metrics.save_to_gallery(
                                user_id, sent2.photo[-1].file_id, token=token,
                                prompt=prompt[:400] if prompt else None,
                            )
        except Exception as e2:
            d.log.error(f"Повторная ошибка отправки фото {index}/{total}: {e2}")

    async def after_result(self, message, user_id: int, *, streak_note: str | None = None) -> None:
        """Короткое меню после результата: создать ещё · видео · друг · меню."""
        d = self._d
        kb = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [d.menu_button("gen", "m:gen"), d.menu_button("vid_gen", "m:vid")],
                [d.invite_button(user_id)],
                [d.menu_button("menu", "m:menu")],
            ]
        )
        after = flow_copy.msg("after_image_screen", credits=d.credit_store.balance(user_id))
        text = f"{streak_note}\n\n{after}" if streak_note else after
        cta = d.first_referral_cta_text(user_id)
        if cta:
            text = f"{text}\n\n{cta}"
        try:
            await message.answer(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass

    async def send_result_pairs(
        self, message, pairs: list, *, user_id: int, project_id: str | None,
        prompt: str, aspect_ratio: str, emoji: str, account_id: str | None = None,
    ) -> None:
        """Отправить набор картинок с кнопками действий (общий для всех режимов)."""
        d = self._d
        total = len(pairs)
        # Реферальная ссылка автора — под каждой картинкой
        ref_link = d.referral_link(user_id)
        bot_username = d.bot_username()
        if bot_username:
            ref_text = (
                f'\n\n<a href="{html.escape(ref_link)}">'
                f'Создай своё в @{html.escape(bot_username)}</a>'
            )
        else:
            ref_text = ""
        for i, (url, img) in enumerate(pairs, 1):
            counter = f" {i}/{total}" if total > 1 else ""
            caption = f"{emoji}{counter} · {html.escape(_short_prompt(prompt, 80))}{ref_text}"
            await self.send_one_image(
                message, url=url, img=img, index=i, total=total, caption=caption,
                user_id=user_id, project_id=project_id, prompt=prompt,
                aspect_ratio=aspect_ratio, account_id=account_id,
            )
            await asyncio.sleep(0.3)
