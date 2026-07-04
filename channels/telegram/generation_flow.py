"""Image generation orchestration for the Telegram adapter (Phase 11 core split).

The text→image generate-and-send flow with silent per-account failover, status
animation, health marking and result delivery. Extracted out of the flow_bot
composition root; runtime singletons and sibling flows are injected via
:class:`GenerationFlowDeps` so this module never imports flow_bot. Telegram
adapter code (uses aiogram), not a platform-neutral core module.
"""

from __future__ import annotations

import asyncio
import html
import itertools
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import aiohttp
from aiogram import types
from aiogram.types import BufferedInputFile

import flow_copy
from flow_core import (
    DEFAULT_IMAGE_MODEL,
    ImageRef,
    action_price,
    build_image_inputs,
    download_url,
    id_from_media_url,
    image_model_extra,
    load_edit_capture,
    result_pairs,
)
from mediautil import image_ext_from_bytes
from product.marketplace import marketplace_export_caption, marketplace_export_filename
from textutil import _short_prompt

_GEN_ATTEMPT_TIMEOUT = 70  # seconds per attempt


@dataclass(frozen=True)
class GenerationFlowDeps:
    workspace: Callable[[int], dict]
    is_seller: Callable[[], bool]
    seller_generate_and_send: Callable[..., Awaitable[Any]]
    user_slot: Callable[..., Any]
    credit_gate: Callable[..., Any]
    rate_limited_error: type[BaseException]
    not_enough_credits_error: type[BaseException]
    image_request_event: dict[str, str]
    username: Callable[[Any], str]
    account_for_image: Callable[..., str | None]
    ensure_user_project: Callable[..., Awaitable[str | None]]
    metrics: Any
    account_pool: Any
    client_for_acc: Callable[[str | None], Any]
    log: Any
    fire_owner_alert: Callable[[str], None]
    img_retry_kb: Callable[[], Any]
    menu_button: Callable[..., Any]
    mark_image_account_failure: Callable[..., None]
    send_result_pairs: Callable[..., Awaitable[None]]
    after_result: Callable[..., Awaitable[None]]
    streak_note: Callable[[int], str | None]
    log_image_job: Callable[..., None]
    edit_capture_file: Any
    reupload_ref_for_edit_failover: Callable[..., Awaitable[ImageRef | None]]
    is_rate_limit_error: Callable[[dict], bool]
    post_generation_referral_hooks: Callable[..., Awaitable[None]]
    flow_account_id: str


class GenerationFlow:
    def __init__(self, deps: GenerationFlowDeps) -> None:
        self._d = deps

    async def generate_and_send(
        self,
        message,
        prompt: str,
        num_images: int = 4,
        aspect_ratio: str = "landscape",
        actor_id: int | None = None,
        action: str = "gen",
        image_model: str = DEFAULT_IMAGE_MODEL,
    ) -> None:
        d = self._d
        user_id = actor_id or message.from_user.id

        if not prompt or len(prompt) < 3:
            await message.answer(flow_copy.msg("prompt_too_short"))
            return

        ws = d.workspace(user_id)
        ws["last"] = {
            "prompt": prompt, "count": num_images, "aspect": aspect_ratio, "imodel": image_model,
        }
        ws["img_retry"] = {
            "prompt": prompt, "num_images": num_images,
            "aspect_ratio": aspect_ratio, "image_model": image_model, "action": action,
        }

        if d.is_seller():
            await d.seller_generate_and_send(
                message, prompt, num_images=num_images, aspect_ratio=aspect_ratio,
                user_id=user_id, action=action, image_model=image_model,
            )
            return

        if d.account_for_image(user_id) is None:
            await message.answer(flow_copy.msg("accounts_unavailable"))
            return

        d.metrics.log_event(
            d.image_request_event.get(action, "image_requested"),
            user_id=user_id, username=d.username(message), source=action,
            payload={"count": num_images, "model": image_model},
        )

        surcharge = image_model_extra(image_model) * max(1, num_images)
        started = time.monotonic()
        ok = False
        try:
            async with d.user_slot(user_id, message):
                async with d.credit_gate(
                    user_id, action, message, num_images, surcharge=surcharge
                ) as charge:
                    ok = await self.do_generate_and_send(
                        message, prompt, num_images, aspect_ratio, user_id,
                        image_model=image_model,
                    )
                    charge.ok = ok
        except d.rate_limited_error:
            d.log_image_job(user_id, action, image_model, started, ok=False, error="user_busy")
            return
        except d.not_enough_credits_error:
            d.metrics.log_event(
                "image_failed", user_id=user_id, source=action,
                payload={"reason": "insufficient_credits"},
            )
            return

        charged = (action_price(action, num_images) + surcharge) if ok else 0
        d.metrics.log_event("image_success" if ok else "image_failed",
                            user_id=user_id, source=action)
        if ok:
            d.metrics.log_event("credits_charged", user_id=user_id, source=action,
                                payload={"amount": charged, "action": action})
            if action == "gen":
                d.metrics.log_event("wizard_completed", user_id=user_id, source=action)
                d.metrics.save_prompt_history(user_id, prompt)
        d.log_image_job(user_id, action, image_model, started, ok=ok, charged=charged)

    async def edit_and_send(
        self,
        message: types.Message,
        ref: ImageRef,
        instruction: str,
        *,
        actor_id: int | None = None,
        aspect_ratio: str | None = None,
        image_model: str = DEFAULT_IMAGE_MODEL,
        price_action: str = "edit",
    ) -> bool:
        """Apply ``instruction`` to a specific generated image ref."""
        d = self._d
        user_id = actor_id if actor_id is not None else message.from_user.id

        if not instruction or len(instruction) < 3:
            await message.answer("❌ Опишите правку (минимум 3 символа)")
            return False

        capture = load_edit_capture(d.edit_capture_file)
        image_inputs = build_image_inputs(ref.source, capture)
        if not image_inputs:
            await message.answer(
                "⚠️ Не удалось определить идентификатор исходной картинки. "
                "Сгенерируйте изображение заново и нажмите «Редактировать» под ним."
            )
            return False

        aspect = aspect_ratio or ref.aspect_ratio
        surcharge = image_model_extra(image_model)
        d.metrics.log_event(
            "image_edit_requested", user_id=user_id, source="edit",
            payload={"model": image_model},
        )
        started = time.monotonic()
        ok = False
        try:
            async with d.user_slot(user_id, message):
                async with d.credit_gate(
                    user_id, price_action, message, 1, surcharge=surcharge
                ) as charge:
                    ok = await self.do_edit_and_send(
                        message, ref, instruction, image_inputs, user_id,
                        aspect_ratio=aspect, image_model=image_model,
                    )
                    charge.ok = ok
        except d.rate_limited_error:
            d.log_image_job(user_id, "edit", image_model, started, ok=False, error="user_busy")
            return False
        except d.not_enough_credits_error:
            d.metrics.log_event(
                "image_failed", user_id=user_id, source="edit",
                payload={"reason": "insufficient_credits"},
            )
            return False

        charged = (action_price(price_action, 1) + surcharge) if ok else 0
        d.metrics.log_event("image_success" if ok else "image_failed", user_id=user_id, source="edit")
        if ok:
            d.metrics.log_event(
                "credits_charged", user_id=user_id, source="edit",
                payload={"amount": charged, "action": price_action},
            )
        d.log_image_job(user_id, "edit", image_model, started, ok=ok, charged=charged)
        if ok:
            d.workspace(user_id)["last"] = {
                "kind": "edit",
                "ref": ref,
                "instruction": instruction,
                "aspect": aspect,
                "imodel": image_model,
                "price_action": price_action,
            }
            await d.post_generation_referral_hooks(message, user_id)
        return ok

    async def do_edit_and_send(
        self,
        message: types.Message,
        ref: ImageRef,
        instruction: str,
        image_inputs: list,
        user_id: int,
        *,
        aspect_ratio: str | None = None,
        image_model: str = DEFAULT_IMAGE_MODEL,
    ) -> bool:
        d = self._d
        status_msg = await message.answer(
            f"✏️ Редактирую изображение...\n📝 {instruction[:80]}"
        )
        aspect = aspect_ratio or ref.aspect_ratio

        async def update_status(text: str):
            try:
                await status_msg.edit_text(f"{text}\n📝 {instruction[:80]}")
            except Exception:
                pass

        async def _generate_for(edit_ref: ImageRef, inputs: list) -> dict:
            return await d.client_for_acc(edit_ref.account_id).generate_images(
                instruction,
                aspect_ratio=aspect,
                num_images=1,
                progress_cb=update_status,
                project_id=edit_ref.project_id,
                image_inputs=inputs,
                allow_browser_fallback=False,
                image_model=image_model,
            )

        active_ref = ref
        try:
            result = await _generate_for(active_ref, image_inputs)
        except Exception:
            d.log.exception("Edit failed")
            await status_msg.edit_text("❌ Ошибка редактирования. Попробуйте ещё раз.")
            return False

        if "error" in result:
            if d.is_rate_limit_error(result):
                d.mark_image_account_failure(ref.account_id, result)
                d.log.info(
                    "image_edit failover: аккаунт %s ушёл в кулдаун, пробую другой",
                    ref.account_id,
                )
                failover_ref = await d.reupload_ref_for_edit_failover(
                    ref, user_id, current_account_id=ref.account_id,
                )
                failover_inputs = (
                    build_image_inputs(failover_ref.source, load_edit_capture(d.edit_capture_file))
                    if failover_ref else []
                )
                if failover_ref and failover_inputs:
                    try:
                        result = await _generate_for(failover_ref, failover_inputs)
                        if "error" not in result:
                            active_ref = failover_ref
                            d.account_pool.mark_success(failover_ref.account_id)
                    except Exception:
                        d.log.exception("Edit failover retry failed")
                        result = {"error": flow_copy.msg("image_edit_rate_limited")}
                if "error" in result:
                    if failover_ref and d.is_rate_limit_error(result):
                        d.mark_image_account_failure(failover_ref.account_id, result)
                    await status_msg.edit_text(flow_copy.msg("image_edit_rate_limited"))
                    return False
            else:
                await status_msg.edit_text(f"❌ {html.escape(str(result['error'])[:300])}")
                return False

        pairs = result_pairs(result)
        if not pairs:
            d.log.warning("Пустой ответ редактирования: %s", str(result)[:500])
            await status_msg.edit_text(flow_copy.msg("nothing_returned"))
            return False

        await update_status(flow_copy.msg("sending"))
        d.account_pool.mark_success(active_ref.account_id)
        await d.send_result_pairs(
            message, pairs, user_id=user_id, project_id=active_ref.project_id,
            prompt=instruction, aspect_ratio=aspect, emoji="✏️",
            account_id=active_ref.account_id,
        )
        await status_msg.delete()
        return True

    async def run_i2i(
        self,
        message: types.Message,
        ref: ImageRef,
        prompt: str,
        *,
        num_images: int,
        emoji: str,
        fail_text: str,
        action: str = "edit",
    ) -> bool:
        """Общий image-to-image: правка/вариации/улучшение картинки ``ref``.

        Браузерный фолбэк отключён, чтобы вместо результата по этой картинке не
        прислать несвязанную генерацию.
        """
        d = self._d
        user_id = ref.user_id
        capture = load_edit_capture(d.edit_capture_file)
        image_inputs = build_image_inputs(ref.source, capture)
        if not image_inputs:
            await message.answer(
                "⚠️ Не удалось определить идентификатор исходной картинки. "
                "Сгенерируйте изображение заново и попробуйте снова."
            )
            return False

        d.metrics.log_event(d.image_request_event.get(action, "image_requested"),
                            user_id=user_id, source=action, payload={"count": num_images})
        started = time.monotonic()
        ok = False
        try:
            async with d.user_slot(user_id, message):
                async with d.credit_gate(user_id, action, message, num_images) as charge:
                    ok = await self.do_run_i2i(
                        message, ref, prompt, image_inputs,
                        num_images=num_images, emoji=emoji, fail_text=fail_text,
                    )
                    charge.ok = ok
        except d.rate_limited_error:
            d.log_image_job(user_id, action, None, started, ok=False, error="user_busy")
            return False
        except d.not_enough_credits_error:
            d.metrics.log_event("image_failed", user_id=user_id, source=action,
                                payload={"reason": "insufficient_credits"})
            return False
        charged = action_price(action, num_images) if ok else 0
        d.metrics.log_event("image_success" if ok else "image_failed",
                            user_id=user_id, source=action)
        if ok:
            d.metrics.log_event("credits_charged", user_id=user_id, source=action,
                                payload={"amount": charged, "action": action})
        d.log_image_job(user_id, action, None, started, ok=ok, charged=charged)
        if ok:
            await d.post_generation_referral_hooks(message, user_id)
        return ok

    async def do_run_i2i(
        self,
        message: types.Message,
        ref: ImageRef,
        prompt: str,
        image_inputs: list,
        *,
        num_images: int,
        emoji: str,
        fail_text: str,
    ) -> bool:
        d = self._d
        user_id = ref.user_id
        status_msg = await message.answer(f"{emoji} Обрабатываю...")

        async def update_status(text: str):
            try:
                await status_msg.edit_text(text)
            except Exception:
                pass

        try:
            result = await d.client_for_acc(ref.account_id).generate_images(
                prompt,
                aspect_ratio=ref.aspect_ratio,
                num_images=num_images,
                progress_cb=update_status,
                project_id=ref.project_id,
                image_inputs=image_inputs,
                allow_browser_fallback=False,
            )
        except Exception:
            d.log.exception("i2i failed")
            await status_msg.edit_text("❌ Ошибка. Попробуйте ещё раз.")
            return False

        if "error" in result:
            if d.is_rate_limit_error(result):
                d.mark_image_account_failure(ref.account_id, result)
                await status_msg.edit_text(flow_copy.msg("image_edit_rate_limited"))
            else:
                await status_msg.edit_text(f"❌ {html.escape(str(result['error'])[:300])}")
            return False

        pairs = result_pairs(result)
        if not pairs:
            await status_msg.edit_text(fail_text)
            return False

        await d.send_result_pairs(
            message, pairs, user_id=user_id, project_id=ref.project_id,
            prompt=prompt, aspect_ratio=ref.aspect_ratio, emoji=emoji,
            account_id=ref.account_id,
        )
        await status_msg.delete()
        return True

    async def real_upscale_and_send(self, message: types.Message, ref: ImageRef) -> None:
        """🔍 Настоящий апскейл сервиса (flow/upsampleImage → картинка в 2K).

        В отличие от «Чёткости ×2» (доработка промптом) — это родной серверный
        апскейл по сверенному контракту. Возвращает готовую увеличенную картинку.
        """
        d = self._d
        user_id = ref.user_id
        media_id = ref.source.get("mediaId") if isinstance(ref.source, dict) else None
        if not media_id:
            media_id = id_from_media_url(ref.source.get("fifeUrl") if isinstance(ref.source, dict) else None)
        if not media_id:
            await message.answer(flow_copy.msg("upscale_unavailable"))
            return

        d.metrics.log_event("upscale_requested", user_id=user_id, source="realup")
        started = time.monotonic()
        ok = False
        try:
            async with d.user_slot(user_id, message):
                async with d.credit_gate(user_id, "realup", message, 1) as charge:
                    ok = await self.do_real_upscale(message, ref, media_id)
                    charge.ok = ok
        except d.rate_limited_error:
            d.log_image_job(user_id, "realup", None, started, ok=False, error="user_busy")
            return
        except d.not_enough_credits_error:
            d.metrics.log_event("image_failed", user_id=user_id, source="realup",
                                payload={"reason": "insufficient_credits"})
            return
        charged = action_price("realup", 1) if ok else 0
        d.metrics.log_event("image_success" if ok else "image_failed", user_id=user_id, source="realup")
        if ok:
            d.metrics.log_event("credits_charged", user_id=user_id, source="realup",
                                payload={"amount": charged, "action": "realup"})
        d.metrics.log_flow_job(
            user_id=user_id,
            account_id=(ref.account_id or d.account_pool.assigned_to(user_id) or d.flow_account_id),
            operation_type="upscale",
            model=None, bot_credits_charged=charged, duration_ms=_ms_since(started),
            status="success" if ok else "fail", error_type=None if ok else "upscale_failed",
        )
        if ok:
            await d.post_generation_referral_hooks(message, user_id)

    async def do_real_upscale(self, message: types.Message, ref: ImageRef, media_id: str) -> bool:
        d = self._d
        status_msg = await message.answer(flow_copy.msg("upscaling"))

        async def update_status(text: str):
            try:
                await status_msg.edit_text(text)
            except Exception:
                pass

        try:
            result = await d.client_for_acc(ref.account_id).upsample_image(
                media_id, ref.project_id, progress_cb=update_status
            )
        except Exception:
            d.log.exception("real upscale failed")
            await status_msg.edit_text(flow_copy.msg("gen_failed"))
            return False

        if "error" in result:
            # Тексты ошибок здесь — наша копия из flow_copy, но эскейпим на случай
            # сырого текста от бэкенда (защита разметки от инъекции).
            await status_msg.edit_text(f"❌ {html.escape(str(result['error'])[:300])}")
            return False

        image_bytes = result.get("image_bytes")
        if not image_bytes:
            await status_msg.edit_text(flow_copy.msg("nothing_returned"))
            return False

        # Отдаём документом (без сжатия Telegram), чтобы сохранить высокое разрешение.
        ext = "png" if image_bytes[:8].startswith(b"\x89PNG") else "jpg"
        try:
            await message.answer_document(
                BufferedInputFile(image_bytes, f"upscaled_{media_id[-8:]}.{ext}"),
                caption="🔍 Картинка в высоком разрешении (2K).",
            )
            await status_msg.delete()
        except Exception:
            d.log.exception("upscaled image send failed")
            await status_msg.edit_text("❌ Не удалось отправить файл.")
            return False
        return True

    async def send_original_file(
        self, message: types.Message, ref: ImageRef, *, marketplace_export: bool = False
    ) -> None:
        """⬇️ Оригинал: отдать картинку файлом в полном качестве.

        На сайте кнопка «upscale» — это клиентское скачивание файла, а не серверный
        запрос. Эквивалент в боте: скачать исходные байты и отправить ДОКУМЕНТОМ
        (Telegram не пережимает документы, в отличие от фото), сохранив полное
        разрешение сгенерированной картинки.
        """
        url = download_url(ref.source)
        if not url:
            await message.answer("⚠️ Нет ссылки на файл этой картинки.")
            return

        # Скачивание — бесплатная offline-операция: НЕ держим busy-слот, иначе
        # зависшая/идущая генерация мешает забрать уже готовый файл (audit Major 11).
        await self.do_send_original_file(message, ref, url, marketplace_export=marketplace_export)

    async def do_send_original_file(
        self,
        message: types.Message,
        ref: ImageRef,
        url: str,
        *,
        marketplace_export: bool = False,
    ) -> None:
        d = self._d
        status_msg = await message.answer("⬇️ Готовлю файл в полном качестве...")
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=60)) as r:
                    if r.status != 200:
                        await status_msg.edit_text(f"⚠️ Не удалось скачать файл (HTTP {r.status}).")
                        return
                    data = await r.read()
        except Exception:
            d.log.exception("download original failed")
            await status_msg.edit_text("❌ Ошибка скачивания. Попробуйте ещё раз.")
            return

        media_id = ref.source.get("mediaId") if isinstance(ref.source, dict) else None
        if marketplace_export:
            filename = marketplace_export_filename(ref, image_ext_from_bytes(data))
            caption = marketplace_export_caption(ref)
        else:
            filename = f"flow_{media_id or 'image'}.{image_ext_from_bytes(data)}"
            caption = "⬇️ Оригинал в полном качестве (Telegram не сжимает документы)."
        try:
            await message.answer_document(
                BufferedInputFile(data, filename),
                caption=caption,
            )
            await status_msg.delete()
        except Exception:
            d.log.exception("send document failed")
            await status_msg.edit_text("❌ Не удалось отправить файл.")

    async def do_generate_and_send(
        self, message, prompt: str, num_images: int, aspect_ratio: str, user_id: int,
        image_model: str = DEFAULT_IMAGE_MODEL,
    ) -> bool:
        d = self._d
        status_msg = await message.answer(flow_copy.msg("generating"))

        async def update_status(text: str):
            try:
                await status_msg.edit_text(f"{text}\n📝 {_short_prompt(prompt, 80)}")
            except Exception:
                pass

        # Фоновая анимация статусных фраз: меняется каждые 2.5 сек пока идёт генерация.
        _img_phrases = flow_copy.MESSAGES.get("img_status_phrases") or []
        _img_anim_stop = asyncio.Event()

        async def _img_animate():
            await asyncio.sleep(2.5)
            for phrase in itertools.cycle(_img_phrases):
                if _img_anim_stop.is_set():
                    return
                try:
                    await status_msg.edit_text(f"{phrase}\n📝 {_short_prompt(prompt, 80)}")
                except Exception:
                    pass
                await asyncio.sleep(2.5)
                if _img_anim_stop.is_set():
                    return

        _img_anim_task = asyncio.create_task(_img_animate()) if _img_phrases else None

        # Генерация с тихим фейловером: попытка 0 — основной аккаунт, попытка 1 — другой.
        # 400 (prompt_rejected) = проблема юзера, не аккаунта — фейловер и кулдаун не нужны.
        # asyncio.wait_for(timeout=70) на каждую попытку: не ждём 120с зависший аккаунт —
        # фейловер стартует сразу, суммарное ожидание ≤ 140с вместо ≤ 240с.
        tried: set[str] = set()
        acc_id: str | None = None
        project_id: str | None = None
        result: dict = {}
        _uname = d.username(message)

        try:
            for attempt in range(2):
                acc_id = d.account_for_image(user_id, exclude=tried if tried else None)
                if acc_id is None:
                    await status_msg.edit_text(flow_copy.msg("accounts_unavailable"))
                    return False
                tried.add(acc_id)
                project_id = await d.ensure_user_project(user_id, account_id=acc_id)

                def _log_failover(from_acc: str, reason: str) -> None:
                    d.metrics.log_event(
                        "gen_failover", user_id=user_id, username=_uname,
                        payload={"from_account": from_acc, "reason": reason[:120]},
                    )

                # Notify user if this account is already at capacity — they'll wait in queue.
                if not d.account_pool.has_image_capacity(acc_id):
                    try:
                        await status_msg.edit_text(flow_copy.msg("high_load"))
                    except Exception:
                        pass

                async with d.account_pool.image_slot(acc_id):
                    # Slot acquired — credits already reserved by credit_gate above.
                    try:
                        result = await asyncio.wait_for(
                            d.client_for_acc(acc_id).generate_images(
                                prompt,
                                aspect_ratio=aspect_ratio,
                                num_images=num_images,
                                progress_cb=update_status,
                                project_id=project_id,
                                image_model=image_model,
                            ),
                            timeout=_GEN_ATTEMPT_TIMEOUT,
                        )
                    except asyncio.TimeoutError:
                        d.log.warning("Generation timed out after %ds (account %s, attempt %d)",
                                      _GEN_ATTEMPT_TIMEOUT, acc_id, attempt)
                        if d.account_pool.mark_failure(acc_id):
                            d.fire_owner_alert(
                                f"⚠️ <b>Аккаунт кулдаун</b>\n"
                                f"Аккаунт: <code>{acc_id}</code>\n"
                                f"Причина: timeout ({_GEN_ATTEMPT_TIMEOUT}s, image)"
                            )
                        if attempt == 0:
                            _log_failover(acc_id, "timeout")
                            continue  # releases image_slot, then picks next account
                        await status_msg.edit_text(flow_copy.msg("gen_failed"), reply_markup=d.img_retry_kb())
                        return False
                    except Exception:
                        d.log.exception("Generation failed (account %s, attempt %d)", acc_id, attempt)
                        if d.account_pool.mark_failure(acc_id):
                            d.fire_owner_alert(
                                f"⚠️ <b>Аккаунт кулдаун</b>\n"
                                f"Аккаунт: <code>{acc_id}</code>\n"
                                f"Причина: exception (image)"
                            )
                        if attempt == 0:
                            _log_failover(acc_id, "exception")
                            continue  # releases image_slot, then picks next account
                        await status_msg.edit_text(flow_copy.msg("gen_failed"), reply_markup=d.img_retry_kb())
                        return False

                    if "error" in result:
                        if result.get("error_type") == "prompt_rejected":
                            # 400 — проблема запроса, не аккаунта: не трогаем health, не фейловеримся
                            await status_msg.edit_text(
                                f"❌ {html.escape(str(result['error'])[:300])}",
                                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                                    [d.menu_button("menu", "m:menu")],
                                ]),
                            )
                            return False
                        # Ошибка аккаунта — помечаем и пробуем другой (один раз)
                        d.mark_image_account_failure(acc_id, result)
                        if attempt == 0:
                            reason = str(result.get("error", ""))[:80]
                            d.log.info("Тихий фейловер после ошибки аккаунта %s: %s", acc_id, reason)
                            _log_failover(acc_id, reason)
                            continue  # releases image_slot, then picks next account
                        # Оба аккаунта не справились
                        await status_msg.edit_text(
                            f"❌ {html.escape(str(result['error'])[:300])}",
                            reply_markup=d.img_retry_kb(),
                        )
                        return False

                break  # успех (image_slot released by exiting async with)
            else:
                await status_msg.edit_text(flow_copy.msg("gen_failed"), reply_markup=d.img_retry_kb())
                return False

            pairs = result_pairs(result)

            if not pairs:
                d.log.warning(f"Пустой ответ: {str(result)[:500]}")
                await status_msg.edit_text(
                    flow_copy.msg("nothing_returned"),
                    reply_markup=d.img_retry_kb(),
                )
                return False

            d.account_pool.mark_success(acc_id)
            await update_status(flow_copy.msg("sending"))
            await d.send_result_pairs(
                message, pairs, user_id=user_id, project_id=project_id,
                prompt=prompt, aspect_ratio=aspect_ratio, emoji="🎨", account_id=acc_id,
            )
            await status_msg.delete()
            streak = d.streak_note(user_id)
            await d.after_result(message, user_id, streak_note=streak)
            return True
        finally:
            _img_anim_stop.set()
            if _img_anim_task:
                _img_anim_task.cancel()
