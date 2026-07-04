"""Seller (marketplace) generation flows for the Telegram adapter (Phase 11).

Seller mode routes generation through the consumer backend instead of the local
account pool: image cards, image-to-image product photos, and marketplace
"animate" videos. Charging (seller wallet), metrics, referral hooks and the
one-time brand-kit nudge live here. Extracted out of the flow_bot composition
root; runtime singletons are injected via :class:`SellerFlowDeps` so this module
never imports flow_bot. Telegram adapter code (uses aiogram), not a
platform-neutral core module.
"""

from __future__ import annotations

import base64
import html
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aiogram import types
from aiogram.types import BufferedInputFile

import flow_copy
from flow_core import DEFAULT_IMAGE_MODEL, action_price, image_model_extra, video_price
from config.video import VID_REF_DEFAULT_MODEL
from product.job_log import ms_since as _ms_since, seller_acc_tag as _seller_acc_tag
from textutil import _short_prompt


@dataclass(frozen=True)
class SellerFlowDeps:
    backend_client: Callable[[], Any]
    send_one_image: Callable[..., Awaitable[None]]
    download: Callable[[str], Awaitable[Any]]
    log: Any
    metrics: Any
    username: Callable[[Any], str]
    image_request_event: dict[str, str]
    user_slot: Callable[..., Any]
    credit_gate: Callable[..., Any]
    rate_limited_error: type[BaseException]
    not_enough_credits_error: type[BaseException]
    log_image_job: Callable[..., None]
    post_generation_referral_hooks: Callable[..., Awaitable[None]]
    credit_store: Any
    zero_balance_kb: Callable[[], Any]
    menu_button: Callable[..., Any]
    mp_back_kb: Callable[[], Any]
    is_seller: Callable[[], bool]
    mp_brand_kit: Callable[[int], Any]


class SellerFlow:
    def __init__(self, deps: SellerFlowDeps) -> None:
        self._d = deps

    async def backend_call_and_send(
        self, message: types.Message, prompt: str, *, num_images: int, aspect_ratio: str,
        user_id: int, image_model: str, kind: str = "image", image_b64: str | None = None,
    ) -> tuple[bool, str | None]:
        """Seller-side: ask the consumer backend to generate, then send the URLs.

        Returns ``(sent_any, backend_account_id)`` — the account_id is the REAL
        consumer-backend account that did the work (for ``<acc>-sell`` event tagging)."""
        d = self._d
        client = d.backend_client()
        if client is None:
            await message.answer(flow_copy.msg("accounts_unavailable"))
            return False, None
        status_msg = await message.answer(flow_copy.msg("generating"))
        data = await client.generate(
            prompt=prompt, num_images=num_images, aspect_ratio=aspect_ratio,
            image_model=image_model, user_id=user_id, kind=kind, image_b64=image_b64,
        )
        images = data.get("images") or []
        if data.get("error") or not images:
            # Понятный финальный статус вместо технической ошибки. Кредиты вернёт
            # credit_gate (charge.ok остаётся False) — поэтому прямо говорим об этом.
            raw = str(data.get("error") or "").lower()
            if any(k in raw for k in ("rate", "limit", "429", "quota", "перегруж",
                                       "busy", "unavailable", "account", "капч", "captcha", "403")):
                friendly = "⏳ Сервис сейчас перегружен. Кредиты возвращены — попробуй ещё раз через пару минут 🙏"
            elif raw:
                friendly = "❌ Не получилось создать карточку. Кредиты возвращены — попробуй ещё раз или измени фото/описание."
            else:
                friendly = "❌ Карточка не получилась. Кредиты возвращены — попробуй ещё раз 🙏"
            try:
                await status_msg.edit_text(friendly)
            except Exception:
                pass
            return False, data.get("account_id")
        try:
            await status_msg.delete()
        except Exception:
            pass
        sent_any = False
        for index, im in enumerate(images, 1):
            url = im.get("url")
            if not url:
                continue
            try:
                await d.send_one_image(
                    message, url=url, img=im.get("img") or {"url": url},
                    index=index, total=len(images), caption="", user_id=user_id,
                    project_id=data.get("project_id"), prompt=prompt,
                    aspect_ratio=aspect_ratio, account_id=data.get("account_id"),
                )
                sent_any = True
            except Exception:
                d.log.exception("seller backend send image failed")
        return sent_any, data.get("account_id")

    async def generate_and_send(
        self, message: types.Message, prompt: str, *, num_images: int, aspect_ratio: str,
        user_id: int, action: str = "gen", image_model: str = DEFAULT_IMAGE_MODEL,
        kind: str = "image", image_b64: str | None = None,
    ) -> bool:
        """Seller generation: charge the seller wallet, generate via backend, send.

        ``kind="i2i"`` runs image-to-image on ``image_b64`` (the user's photo).
        """
        d = self._d
        if d.backend_client() is None:
            await message.answer(flow_copy.msg("accounts_unavailable"))
            return False
        d.metrics.log_event(
            d.image_request_event.get(action, "image_requested"), user_id=user_id,
            username=d.username(message), source=action,
            payload={"count": num_images, "model": image_model, "surface": "seller", "kind": kind},
        )
        surcharge = image_model_extra(image_model) * max(1, num_images)
        started = time.monotonic()
        ok = False
        backend_acc = None
        try:
            async with d.user_slot(user_id, message):
                async with d.credit_gate(user_id, action, message, num_images, surcharge=surcharge) as charge:
                    ok, backend_acc = await self.backend_call_and_send(
                        message, prompt, num_images=num_images, aspect_ratio=aspect_ratio,
                        user_id=user_id, image_model=image_model, kind=kind, image_b64=image_b64,
                    )
                    charge.ok = ok
        except d.rate_limited_error:
            d.log_image_job(user_id, action, image_model, started, ok=False, error="user_busy",
                            account_id=_seller_acc_tag(backend_acc))
            return False
        except d.not_enough_credits_error:
            d.metrics.log_event("image_failed", user_id=user_id, source=action,
                                payload={"reason": "insufficient_credits"})
            return False
        charged = (action_price(action, num_images) + surcharge) if ok else 0
        d.metrics.log_event("image_success" if ok else "image_failed", user_id=user_id, source=action)
        if ok:
            d.metrics.log_event("credits_charged", user_id=user_id, source=action,
                                payload={"amount": charged, "action": action})
        d.log_image_job(user_id, action, image_model, started, ok=ok, charged=charged,
                        account_id=_seller_acc_tag(backend_acc))
        if ok:
            await d.post_generation_referral_hooks(message, user_id)
            await self.maybe_brandkit_nudge(message, user_id)
        return ok

    async def maybe_brandkit_nudge(self, message: types.Message, user_id: int) -> None:
        """После первой удачной seller-генерации (один раз) предлагаем заполнить
        бренд-кит — чтобы карточки были в едином стиле магазина."""
        d = self._d
        if not d.is_seller():
            return
        try:
            if d.mp_brand_kit(user_id):
                return  # бренд-кит уже задан
            if d.metrics.has_user_event(user_id, "brandkit_nudge_shown"):
                return  # нудж уже показывали
            d.metrics.log_event("brandkit_nudge_shown", user_id=user_id, source="seller")
            kb = types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🎨 Заполнить бренд-кит", callback_data="mp:brandkit")],
                [d.menu_button("menu", "m:menu")],
            ])
            await message.answer(
                "🎉 <b>Поздравляем с первой карточкой!</b>\n\n"
                "Чтобы усилить качество и держать единый стиль магазина (цвета, тон, "
                "что показывать), заполни <b>бренд-кит</b> — я буду учитывать его в "
                "каждой карточке и серии.",
                reply_markup=kb, parse_mode="HTML",
            )
        except Exception:
            d.log.warning("brandkit nudge failed", exc_info=True)

    async def _download_bytes(self, file_id: str) -> bytes:
        buf = await self._d.download(file_id)
        return buf.read() if hasattr(buf, "read") else bytes(buf)

    async def i2i_from_file_id(
        self, message: types.Message, file_id: str, instruction: str, *, num_images: int,
        aspect_ratio: str, user_id: int, action: str,
    ) -> bool:
        """Seller marketplace photo job from a stored Telegram file_id: download, i2i."""
        try:
            data = await self._download_bytes(file_id)
        except Exception:
            self._d.log.exception("seller photo download failed")
            await message.answer(flow_copy.msg("upload_failed"))
            return False
        image_b64 = base64.b64encode(data).decode("ascii")
        return await self.generate_and_send(
            message, instruction, num_images=num_images, aspect_ratio=aspect_ratio,
            user_id=user_id, action=action, kind="i2i", image_b64=image_b64,
        )

    async def i2i_from_photo(
        self, message: types.Message, instruction: str, *, num_images: int, aspect_ratio: str,
        user_id: int, action: str,
    ) -> bool:
        """Seller marketplace photo job: download the product photo, run i2i via backend."""
        return await self.i2i_from_file_id(
            message, message.photo[-1].file_id, instruction,
            num_images=num_images, aspect_ratio=aspect_ratio, user_id=user_id, action=action,
        )

    async def video_backend_call_and_send(
        self, message: types.Message, prompt: str, *, image_b64: str, aspect_ratio: str,
        user_id: int, video_model: str = VID_REF_DEFAULT_MODEL,
    ) -> bool:
        """Seller-side marketplace video: ask consumer backend, send returned mp4."""
        d = self._d
        client = d.backend_client()
        if client is None:
            await message.answer(flow_copy.msg("accounts_unavailable"))
            return False
        status_msg = await message.answer(flow_copy.msg("vid_working"))
        data = await client.generate(
            prompt=prompt,
            num_images=1,
            aspect_ratio=aspect_ratio,
            image_model=DEFAULT_IMAGE_MODEL,
            video_model=video_model,
            user_id=user_id,
            kind="video_ingredients",
            image_b64=image_b64,
        )
        videos = data.get("videos") or []
        if data.get("error") or not videos:
            err = str(data.get("error") or flow_copy.msg("vid_gen_failed"))[:300]
            try:
                await status_msg.edit_text(f"{flow_copy.msg('vid_gen_failed')}\n{html.escape(err)}")
            except Exception:
                pass
            return False

        sent_any = False
        for index, item in enumerate(videos, 1):
            raw = item.get("video_b64")
            if not raw:
                continue
            try:
                video_bytes = base64.b64decode(raw)
            except Exception:
                d.log.exception("seller backend video base64 decode failed")
                continue
            filename = f"marketplace_video_{index}.mp4"
            caption = flow_copy.msg(
                "vid_result_caption", i=index, n=len(videos),
                prompt=html.escape(_short_prompt(prompt, 60)),
            )
            try:
                await message.answer_video(
                    BufferedInputFile(video_bytes, filename),
                    caption=caption,
                    reply_markup=d.mp_back_kb(),
                    parse_mode="HTML",
                )
                sent_any = True
            except Exception:
                d.log.exception("seller answer_video failed, falling back to document")
                try:
                    await message.answer_document(
                        BufferedInputFile(video_bytes, filename),
                        caption=caption,
                        reply_markup=d.mp_back_kb(),
                        parse_mode="HTML",
                    )
                    sent_any = True
                except Exception:
                    d.log.exception("seller answer_document video fallback failed")

        try:
            if sent_any:
                await status_msg.delete()
            else:
                await status_msg.edit_text(flow_copy.msg("vid_gen_failed"))
        except Exception:
            pass
        return sent_any

    async def video_generate_and_send(
        self, message: types.Message, prompt: str, *, image_b64: str, aspect_ratio: str,
        user_id: int, video_model: str = VID_REF_DEFAULT_MODEL,
    ) -> bool:
        """Charge seller credits, generate marketplace video via consumer backend."""
        d = self._d
        if d.backend_client() is None:
            await message.answer(flow_copy.msg("accounts_unavailable"))
            return False
        if not prompt or len(prompt.strip()) < 3:
            await message.answer(flow_copy.msg("vid_prompt_too_short"))
            return False
        price = video_price(video_model, 1, "ingredients")
        started = time.monotonic()
        charged = False
        ok = False
        try:
            async with d.user_slot(user_id, message):
                have = d.credit_store.balance(user_id)
                if have < price:
                    if have == 0:
                        await message.answer(
                            flow_copy.msg("zero_balance"),
                            reply_markup=d.zero_balance_kb(),
                            parse_mode="HTML",
                        )
                    else:
                        kb = types.InlineKeyboardMarkup(inline_keyboard=[
                            [d.menu_button("topup", "m:topup")], [d.menu_button("menu", "m:menu")]
                        ])
                        await message.answer(
                            flow_copy.msg("low_balance", needed=price, have=have),
                            reply_markup=kb,
                            parse_mode="HTML",
                        )
                    return False
                d.credit_store.charge(user_id, price)
                charged = True
                d.metrics.log_event(
                    "video_requested", user_id=user_id, username=d.username(message),
                    source="mp_animate",
                    payload={"model": video_model, "count": 1, "mode": "ingredients", "surface": "seller"},
                )
                ok = await self.video_backend_call_and_send(
                    message, prompt, image_b64=image_b64, aspect_ratio=aspect_ratio,
                    user_id=user_id, video_model=video_model,
                )
        except d.rate_limited_error:
            return False
        except Exception:
            d.log.exception("seller video backend generation failed")
            ok = False
        finally:
            if charged and not ok:
                d.credit_store.refund(user_id, price)

        d.metrics.log_event("video_success" if ok else "video_failed", user_id=user_id, source="mp_animate")
        if ok:
            d.metrics.log_event(
                "credits_charged", user_id=user_id, source="mp_animate",
                payload={"amount": price, "action": "video_mp_animate"},
            )
        elif charged:
            d.metrics.log_event(
                "credits_refunded", user_id=user_id, source="mp_animate",
                payload={"amount": price},
            )
        d.metrics.log_flow_job(
            user_id=user_id,
            account_id="consumer-backend",
            operation_type="video_mp_animate",
            model=video_model,
            bot_credits_charged=price if ok else 0,
            refund_amount=0 if ok else price if charged else 0,
            duration_ms=_ms_since(started),
            status="success" if ok else "fail",
            error_type=None if ok else "backend_failed",
        )
        if ok:
            await d.post_generation_referral_hooks(message, user_id)
        return ok

    async def video_from_photo(
        self, message: types.Message, prompt: str, *, aspect_ratio: str, user_id: int,
        video_model: str = VID_REF_DEFAULT_MODEL,
    ) -> bool:
        """Seller marketplace animate job: download product photo, run video backend."""
        try:
            data = await self._download_bytes(message.photo[-1].file_id)
        except Exception:
            self._d.log.exception("seller video photo download failed")
            await message.answer(flow_copy.msg("upload_failed"))
            return False
        image_b64 = base64.b64encode(data).decode("ascii")
        return await self.video_generate_and_send(
            message, prompt, image_b64=image_b64, aspect_ratio=aspect_ratio,
            user_id=user_id, video_model=video_model,
        )
