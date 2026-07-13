"""Video generation orchestration for the Telegram adapter (Phase 11 core split).

The text/reference/frames→video generate-and-send flow: pre-charge account
selection, credit charge/refund, per-account failover for text-to-video, status
animation, result delivery with download buttons, and full refund/cleanup on
failure. Extracted out of the flow_bot composition root; runtime singletons and
sibling helpers are injected via :class:`VideoFlowDeps` so this module never
imports flow_bot. Telegram adapter code (uses aiogram), not a platform-neutral
core module.
"""

from __future__ import annotations

import asyncio
import html
import itertools
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aiogram import types
from aiogram.types import BufferedInputFile

import flow_copy
from flow_core import (
    VideoRef,
    action_price,
    build_video_frame_images,
    build_video_reference_images,
    video_extend_price,
    video_model_meta,
    video_price,
)
from config.video import _VID_FMT_TO_ASPECT, VID_DEFAULT_FMT, VIDEO_EXTEND_MODEL
from channels.telegram.keyboards import _video_can_edit, _video_can_extend
from product.job_log import ms_since as _ms_since
from textutil import _short_prompt


@dataclass(frozen=True)
class VideoFlowDeps:
    workspace: Callable[[int], dict]
    user_slot: Callable[..., Any]
    rate_limited_error: type[BaseException]
    vid_default_count: int
    username: Callable[[Any], str]
    video_reference_sources: Callable[..., Any]
    video_reference_project_id: Callable[..., str | None]
    ensure_reference_on_healthy_account: Callable[..., Awaitable[str | None]]
    account_for_video: Callable[..., str | None]
    ensure_user_project: Callable[..., Awaitable[str | None]]
    credit_store: Any
    metrics: Any
    account_pool: Any
    client_for_acc: Callable[[str | None], Any]
    mark_video_account_failure: Callable[..., None]
    video_registry: Any
    video_result_kb: Callable[[str], Any]
    referral_link: Callable[[int], str]
    bot_username: Callable[[], str]
    video_delivery_bytes: Callable[..., Awaitable[tuple[bytes | None, bool]]]
    post_generation_referral_hooks: Callable[..., Awaitable[None]]
    vid_clear: Callable[[int], None]
    zero_balance_kb: Callable[[], Any]
    menu_button: Callable[..., Any]
    log: Any
    aspect_to_vfmt: Callable[[str], str]
    vid_clear_reference_inputs: Callable[[int], None]


class VideoFlow:
    def __init__(self, deps: VideoFlowDeps) -> None:
        self._d = deps

    async def generate_and_send(
        self,
        message: types.Message,
        prompt: str,
        *,
        user_id: int,
        unit_price_override: int | None = None,
        prompt_edited: bool = False,
        status_text: str | None = None,
        source_video: VideoRef | None = None,
        video_operation: str = "generate",
        source_scene_id: str | None = None,
    ) -> None:
        """Слот-обёртка: видео идёт через тот же per-user замок, что и картинки.

        Без него параллельные видео+картинка одного юзера читали баланс до
        списания друг друга (гонка проверь-потом-спиши на кредитах).
        """
        d = self._d
        try:
            async with d.user_slot(user_id, message):
                await self.do_generate_and_send(
                    message, prompt, user_id=user_id,
                    unit_price_override=unit_price_override,
                    prompt_edited=prompt_edited, status_text=status_text,
                    source_video=source_video, video_operation=video_operation,
                    source_scene_id=source_scene_id,
                )
        except d.rate_limited_error:
            return

    async def do_generate_and_send(
        self,
        message: types.Message,
        prompt: str,
        *,
        user_id: int,
        unit_price_override: int | None = None,
        prompt_edited: bool = False,
        status_text: str | None = None,
        source_video: VideoRef | None = None,
        video_operation: str = "generate",
        source_scene_id: str | None = None,
    ) -> None:
        """Запустить видеогенерацию: списать кредиты, дождаться, отправить, показать кнопки."""
        d = self._d
        st = d.workspace(user_id)

        if not prompt or len(prompt.strip()) < 3:
            await message.answer(flow_copy.msg("vid_prompt_too_short"))
            return

        model_id = st.get("vmodel", "omni-flash-4s")
        vmode = st.get("vmode", "text")
        vfmt = st.get("vfmt", VID_DEFAULT_FMT)
        vcount = st.get("vcount", d.vid_default_count)
        aspect = _VID_FMT_TO_ASPECT.get(vfmt, "landscape")

        meta = video_model_meta(model_id)
        if not meta:
            await message.answer(flow_copy.msg("vid_expired_wizard"))
            return
        if vmode == "ingredients" and len(build_video_reference_images(st.get("ving_photos"))) < 1:
            await message.answer(flow_copy.msg("vid_ing_need_more"))
            return
        if vmode == "frames":
            start_image, end_image = build_video_frame_images(st.get("vfrm_start"), st.get("vfrm_end"))
            if not (start_image and end_image):
                await message.answer(flow_copy.msg("vid_frm_need_both"))
                return

        model_key = model_id
        single_price = unit_price_override if unit_price_override is not None else video_price(model_id, 1, vmode)
        total_price = single_price * vcount

        # Аккаунт пула: правки/продления держим на аккаунте исходного ролика
        # (media живёт только там), свежие генерации — на video-capable аккаунте.
        # Для reference-видео (ingredients/frames) загруженное фото привязано к
        # аккаунту → генерим строго на нём, без молчаливого фолбэка на другой
        # аккаунт (там медиа нет → 404). Если этот аккаунт стал недоступен (disabled)
        # — честно просим прислать фото заново, не списывая кредиты.
        has_reference = bool(d.video_reference_sources(st, vmode))
        if source_video and source_video.account_id:
            # Extend/edit of an existing generated video: media lives only on that
            # account, can't be moved. (Rare; the source video was just created.)
            acc_id = source_video.account_id
        elif has_reference:
            # Photo-video (ingredients/frames): transparently (re)place the photo on
            # a healthy account so the user never sees an "account unavailable" error.
            acc_id = await d.ensure_reference_on_healthy_account(
                st, vmode, user_id=user_id, model_id=model_id, min_credits=single_price
            )
        else:
            acc_id = d.account_for_video(user_id, model_id=model_id, min_credits=single_price)
        if acc_id is None:
            # Нет доступных video-capable аккаунтов — отказ ДО списания кредитов.
            await message.answer(flow_copy.msg("accounts_unavailable"))
            return
        video_project_id = (
            source_video.project_id if source_video
            else d.video_reference_project_id(st, vmode)
        )
        if not video_project_id:
            video_project_id = await d.ensure_user_project(user_id, account_id=acc_id)

        _vid_started = time.monotonic()
        d.metrics.log_event("video_requested", user_id=user_id, username=d.username(message),
                            source=video_operation if video_operation != "generate" else vmode,
                            payload={"model": model_id, "count": vcount, "mode": vmode})

        have = d.credit_store.balance(user_id)
        if have < total_price:
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
                    flow_copy.msg("low_balance", needed=total_price, have=have), reply_markup=kb,
                    parse_mode="HTML",
                )
            return

        d.credit_store.charge(user_id, total_price)
        refunded_units = 0

        st["vstep"] = "vgenerating"
        status_msg = await message.answer(status_text or flow_copy.msg("vid_working"))

        async def update_status(text: str):
            try:
                await status_msg.edit_text(f"{text}\n📝 {_short_prompt(prompt, 80)}")
            except Exception:
                pass

        # Фоновая анимация статусных фраз — как у генерации картинок: фразы меняются
        # каждые 2.5 сек, чтобы ожидание видео тоже было живым (раньше фраза
        # обновлялась только раз в ~15 сек на каждом 3-м polling-цикле).
        _vid_phrases = flow_copy.MESSAGES.get("vid_status_phrases") or []
        _vid_anim_stop = asyncio.Event()

        async def _vid_animate():
            await asyncio.sleep(2.5)
            for phrase in itertools.cycle(_vid_phrases):
                if _vid_anim_stop.is_set():
                    return
                try:
                    await status_msg.edit_text(f"{phrase}\n📝 {_short_prompt(prompt, 80)}")
                except Exception:
                    pass
                await asyncio.sleep(2.5)
                if _vid_anim_stop.is_set():
                    return

        _vid_anim_task = asyncio.create_task(_vid_animate()) if _vid_phrases else None

        def _stop_vid_anim():
            _vid_anim_stop.set()
            if _vid_anim_task:
                _vid_anim_task.cancel()

        def _stash_retry():
            # Снимок состояния, чтобы «Попробовать снова» повторил ТОТ ЖЕ запрос.
            # Без этого finally → _vid_clear сотрёт vmodel, и ретрай решит «кнопки устарели».
            st["vretry"] = {
                "vmodel": model_id, "vmode": vmode, "vfmt": vfmt, "vcount": vcount,
                "vfamily": st.get("vfamily"),
                "ving_photos": st.get("ving_photos"),
                "vfrm_start": st.get("vfrm_start"), "vfrm_end": st.get("vfrm_end"),
                "vcaption_prompt": st.get("vcaption_prompt"),
                "prompt": prompt,
            }

        async def _fail_retry(i: int, message_key: str = "vid_gen_failed", error_type: str = "video_gen_failed"):
            nonlocal refunded_units
            refund_amt = single_price * (vcount - i)
            d.credit_store.refund(user_id, refund_amt)
            refunded_units += vcount - i
            _stash_retry()
            _res = result if isinstance(result, dict) else {}
            # Модерация (danger_filter) = кривой промпт/картинка юзера, НЕ вина
            # аккаунта. Не пишем это в статистику аккаунта: ни в routing-score
            # (video_outcome), ни в flow_jobs как fail — иначе здоровый аккаунт
            # штрафуется за чужой промпт и проседает в выборе. Продуктовый счётчик
            # (video_failed) и возврат кредитов оставляем.
            content_moderation = error_type == "danger_filter"
            if not content_moderation:
                d.metrics.log_event(
                    "video_outcome", user_id=user_id, source=acc_id,
                    payload={"ok": False, "mode": vmode, "reason": error_type,
                             "model": model_id,
                             "model_key": _res.get("model_key") or model_key,
                             "model_family": _res.get("model_family") or meta.get("family"),
                             "endpoint": _res.get("endpoint") or vmode,
                             "transport": _res.get("transport") or "direct_http",
                             "attempts": int(_res.get("attempts") or 1),
                             "had_403": bool(_res.get("had_403")),
                             "unusual_403": bool(_res.get("unusual_403")),
                             "success_after_retry": False,
                             "browser_fallback": bool(_res.get("browser_fallback"))},
                )
            d.metrics.log_event("video_failed", user_id=user_id, source=vmode,
                                payload={"model": model_id, "reason": error_type})
            d.metrics.log_event("credits_refunded", user_id=user_id, source=vmode,
                                payload={"amount": refund_amt})
            if not content_moderation:
                d.metrics.log_flow_job(
                    user_id=user_id, account_id=acc_id,
                    operation_type=f"video_{vmode}", model=model_id,
                    bot_credits_charged=0, refund_amount=refund_amt,
                    duration_ms=_ms_since(_vid_started), status="fail", error_type=error_type,
                )
            # Модерация: повтор того же промпта бессмыслен — даём «изменить промпт».
            # Прочие сбои (в т.ч. audio_filtered, где помогает повтор) — обычный ретрай.
            retry_btn = (
                d.menu_button("vid_retry_edit", "v:retrynew")
                if error_type == "danger_filter"
                else d.menu_button("vid_retry", "v:retry")
            )
            fail_kb = types.InlineKeyboardMarkup(inline_keyboard=[
                [retry_btn],
                [d.menu_button("menu", "m:menu")],
            ])
            try:
                await status_msg.edit_text(flow_copy.msg(message_key), reply_markup=fail_kb)
            except Exception:
                await message.answer(flow_copy.msg(message_key), reply_markup=fail_kb)

        sent_count = 0
        try:
            for i in range(vcount):
                # Notify user if account is at video capacity before queuing.
                if not d.account_pool.has_video_capacity(acc_id):
                    try:
                        await status_msg.edit_text(flow_copy.msg("high_load"))
                    except Exception:
                        pass

                # danger_filter is stochastic on the provider side (the same prompt
                # often passes on a second try), so retry once on the SAME account
                # before surfacing it to the user.
                result = {}
                for _danger_attempt in range(2):
                    async with d.account_pool.video_slot(acc_id):
                        result = await d.client_for_acc(acc_id).generate_video(
                            prompt,
                            model_key=model_key,
                            aspect=aspect,
                            project_id=video_project_id,
                            reference_sources=st.get("ving_photos") if vmode == "ingredients" else None,
                            start_source=st.get("vfrm_start") if vmode == "frames" else None,
                            end_source=st.get("vfrm_end") if vmode == "frames" else None,
                            operation=video_operation,
                            source_media_id=source_video.media_id if source_video else None,
                            source_workflow_id=source_video.workflow_id if source_video else None,
                            source_scene_id=source_scene_id or (source_video.scene_id if source_video else None),
                            source_duration_s=source_video.duration_s if source_video else None,
                            progress_cb=update_status,
                        )
                    if (result or {}).get("failure") != "danger_filter" or _danger_attempt == 1:
                        break
                    d.log.info("🎬 danger_filter — ретрай 1 раз на том же аккаунте %s", acc_id)
                    await update_status(flow_copy.msg("working"))

                # Slot released. Handle errors and optional failover.
                if "error" in result:
                    # Контент-фейлы (звук/модерация) — это НЕ проблема аккаунта:
                    # не остужаем аккаунт, не фейловеримся, показываем причину юзеру.
                    _content_fail = (result or {}).get("failure")
                    if _content_fail == "audio_filtered":
                        await _fail_retry(i, "video_audio_filtered", "audio_filtered")
                        return
                    if _content_fail == "danger_filter":
                        await _fail_retry(i, "video_danger_filter", "danger_filter")
                        return
                    d.log.warning(
                        "🎬 gen failed: mode=%s model=%s aspect=%s error_type=%s",
                        vmode,
                        model_id,
                        aspect,
                        result.get("account_risk") or result.get("failure") or "provider_error",
                    )
                    d.mark_video_account_failure(acc_id, result)
                    # Прозрачный фейловер на другой аккаунт для text-to-video (403/auth риски).
                    # Ingredients/frames используют account-bound media — фейловер там невозможен.
                    _failover_risk = (result or {}).get("account_risk")
                    if (
                        _failover_risk in {"video_auth", "video_recaptcha_403"}
                        and vmode == "text"
                        and video_operation == "generate"
                        and not source_video
                    ):
                        failover_acc = d.account_for_video(user_id, model_id=model_id, min_credits=single_price)
                        if failover_acc and failover_acc != acc_id:
                            d.log.info("🔄 video failover: %s → %s", acc_id, failover_acc)
                            acc_id = failover_acc
                            video_project_id = await d.ensure_user_project(
                                user_id, account_id=acc_id
                            )
                            await update_status("⏳ Отправляю запрос на генерацию видео…")
                            # Acquire a fresh slot on the failover account.
                            async with d.account_pool.video_slot(acc_id):
                                result = await d.client_for_acc(acc_id).generate_video(
                                    prompt,
                                    model_key=model_key,
                                    aspect=aspect,
                                    project_id=video_project_id,
                                    reference_sources=None,
                                    start_source=None,
                                    end_source=None,
                                    operation=video_operation,
                                    source_media_id=None,
                                    source_workflow_id=None,
                                    source_scene_id=None,
                                    source_duration_s=None,
                                    progress_cb=update_status,
                                )
                            if "error" not in result:
                                # Фейловер успешен — продолжаем нормальный путь.
                                d.log.info("🎬 video failover succeeded on %s", acc_id)
                                # Не возвращаемся — упадём ниже в success-ветку.
                                pass
                            else:
                                d.log.warning(
                                    "🎬 video failover also failed: %s",
                                    result.get("account_risk")
                                    or result.get("failure")
                                    or "provider_error",
                                )
                                d.mark_video_account_failure(acc_id, result)
                    if "error" in result:
                        await _fail_retry(i)
                        return

                media_id = result["media_id"]
                d.metrics.log_event(
                    "video_outcome", user_id=user_id, source=acc_id,
                    payload={"ok": True, "mode": vmode,
                             "model": model_id,
                             "model_key": (result or {}).get("model_key") or model_key,
                             "model_family": (result or {}).get("model_family") or meta.get("family"),
                             "endpoint": (result or {}).get("endpoint") or vmode,
                             "transport": (result or {}).get("transport") or "direct_http",
                             "attempts": int((result or {}).get("attempts") or 1),
                             "had_403": bool((result or {}).get("had_403")),
                             "unusual_403": bool((result or {}).get("unusual_403")),
                             "success_after_retry": int((result or {}).get("attempts") or 1) > 1,
                             "browser_fallback": bool((result or {}).get("browser_fallback"))},
                )
                _stop_vid_anim()  # генерация готова — гасим анимацию фраз
                await update_status("⬇️ Готовлю видео для отправки…")
                video_bytes = await d.client_for_acc(acc_id).fetch_video_bytes(media_id)

                if not video_bytes:
                    await _fail_retry(i)
                    return

                vref = VideoRef(
                    user_id=user_id,
                    project_id=result.get("project_id"),
                    media_id=media_id,
                    source_media_id=source_video.media_id if video_operation == "extend" and source_video else None,
                    prompt=prompt,
                    model_id=model_id,
                    aspect_ratio=aspect,
                    mode=video_operation if video_operation != "generate" else vmode,
                    prompt_edited=prompt_edited,
                    workflow_id=result.get("workflow_id"),
                    # Extend needs the scene id to stitch the full timeline on download.
                    scene_id=result.get("scene_id") or (source_scene_id if video_operation == "extend" else None),
                    # Each extend deepens the chain; drives the progressive extend price.
                    extend_index=(source_video.extend_index + 1)
                    if (video_operation == "extend" and source_video) else 0,
                    # Правка не меняет длину клипа; свежая генерация = длина модели.
                    # Нужна для endFrameIndex последующих правок (кадры за концом
                    # клипа роняют edit-джобу).
                    duration_s=(
                        source_video.duration_s if video_operation == "edit" and source_video
                        else float(meta["duration"]) if video_operation == "generate"
                        else None
                    ),
                    account_id=acc_id,
                )
                vtoken = d.video_registry.add(vref)
                d.account_pool.mark_success(acc_id)

                # Подпись держим чистой: только описание + реф-ссылка автора.
                # Цены/действия (Изменить · Продлить) живут на кнопках под роликом —
                # в подписи они мешали бы, если видео переслать другому человеку.
                caption = flow_copy.msg("vid_result_caption", i=i + 1, n=vcount, prompt=html.escape(_short_prompt(prompt, 60)))
                # Реферальная ссылка автора — как под картинками (см. _send_result_pairs).
                bot_username = d.bot_username()
                if bot_username:
                    ref_link = d.referral_link(user_id)
                    caption = (
                        f"{caption}\n\n"
                        f'<a href="{html.escape(ref_link)}">Создай своё в @{html.escape(bot_username)}</a>'
                    )
                delivery_bytes, merged_video = await d.video_delivery_bytes(vref, fetched_bytes=video_bytes)
                if not delivery_bytes:
                    await _fail_retry(i)
                    return

                filename = f"video_{i + 1}{'_full' if merged_video else ''}.mp4"
                try:
                    await message.answer_video(
                        BufferedInputFile(delivery_bytes, filename),
                        caption=caption,
                        reply_markup=d.video_result_kb(vtoken),
                        parse_mode="HTML",
                    )
                    sent_count += 1
                except Exception:
                    d.log.exception("answer_video failed, falling back to document")
                    try:
                        await message.answer_document(
                            BufferedInputFile(delivery_bytes, filename),
                            caption=caption,
                            reply_markup=d.video_result_kb(vtoken),
                            parse_mode="HTML",
                        )
                        sent_count += 1
                    except Exception:
                        d.log.exception("answer_document fallback also failed")
                        d.credit_store.refund(user_id, single_price)
                        refunded_units += 1

            st["vlast"] = {"model": model_id, "aspect": aspect, "count": vcount, "prompt": prompt}
            st.pop("vretry", None)  # успех — снимок для ретрая больше не нужен

            if sent_count:
                charged = single_price * sent_count
                d.metrics.log_event("video_success", user_id=user_id, source=vmode,
                                    payload={"model": model_id, "count": sent_count})
                d.metrics.log_event("credits_charged", user_id=user_id, source=vmode,
                                    payload={"amount": charged, "action": f"video_{vmode}"})
                d.metrics.log_flow_job(
                    user_id=user_id, account_id=acc_id,
                    operation_type=f"video_{vmode}", model=model_id,
                    bot_credits_charged=charged,
                    refund_amount=single_price * refunded_units,
                    duration_ms=_ms_since(_vid_started), status="success",
                )
                try:
                    await status_msg.delete()
                except Exception:
                    pass
                await d.post_generation_referral_hooks(message, user_id)
            else:
                d.credit_store.refund(user_id, total_price)
                await status_msg.edit_text(flow_copy.msg("vid_gen_failed"))

        except Exception:
            d.log.exception("_video_generate_and_send failed")
            already_refunded = single_price * refunded_units
            remaining = total_price - already_refunded
            if remaining > 0:
                d.credit_store.refund(user_id, remaining)
            d.metrics.log_event("video_failed", user_id=user_id, source=vmode,
                                payload={"model": model_id, "reason": "exception"})
            d.mark_video_account_failure(acc_id)
            d.metrics.log_flow_job(
                user_id=user_id, account_id=acc_id,
                operation_type=f"video_{vmode}", model=model_id, bot_credits_charged=0,
                refund_amount=total_price, duration_ms=_ms_since(_vid_started),
                status="error", error_type="exception",
            )
            try:
                await status_msg.edit_text(flow_copy.msg("vid_gen_failed"))
            except Exception:
                pass
        finally:
            _stop_vid_anim()
            st.pop("vstep", None)
            d.vid_clear(user_id)

    # ── result-button callbacks (download / edit / extend / repeat) ──────

    async def download(self, callback: types.CallbackQuery, user_id: int, token: str) -> None:
        """Скачать видео по кнопке — бесплатно, без повторной генерации."""
        d = self._d
        ref = d.video_registry.get(token)
        if ref is None or ref.user_id != user_id:
            await callback.answer(flow_copy.msg("expired"), show_alert=True)
            return

        await callback.answer()
        status_msg = await callback.message.answer(flow_copy.msg("preparing_file"))
        video_bytes, merged_video = await d.video_delivery_bytes(ref)

        if not video_bytes:
            await status_msg.edit_text("❌ Не удалось скачать видео. Попробуйте позже.")
            return

        filename = f"video_{ref.media_id[-8:]}{'_full' if merged_video else ''}.mp4"
        try:
            await callback.message.answer_document(
                BufferedInputFile(video_bytes, filename),
                caption="⬇️ Видео в полном качестве.",
            )
            await status_msg.delete()
        except Exception:
            d.log.exception("video download send failed")
            await status_msg.edit_text("❌ Не удалось отправить файл.")

    async def segment_download(self, callback: types.CallbackQuery, user_id: int, token: str) -> None:
        """Скачать только новый фрагмент Extend (сам результат, без склейки таймлайна)."""
        d = self._d
        ref = d.video_registry.get(token)
        if ref is None or ref.user_id != user_id:
            await callback.answer(flow_copy.msg("expired"), show_alert=True)
            return
        if not ref.media_id:
            await callback.answer(flow_copy.msg("expired"), show_alert=True)
            return

        await callback.answer()
        status_msg = await callback.message.answer(flow_copy.msg("preparing_file"))
        video_bytes = await d.client_for_acc(ref.account_id).fetch_video_bytes(ref.media_id)

        if not video_bytes:
            await status_msg.edit_text("❌ Не удалось скачать фрагмент. Попробуйте позже.")
            return

        filename = f"video_fragment_{ref.media_id[-8:]}.mp4"
        try:
            await callback.message.answer_document(
                BufferedInputFile(video_bytes, filename),
                caption="⬇️ Только новый фрагмент.",
            )
            await status_msg.delete()
        except Exception:
            d.log.exception("video fragment download send failed")
            await status_msg.edit_text("❌ Не удалось отправить файл.")

    async def edit_start(self, callback: types.CallbackQuery, user_id: int, token: str) -> None:
        """Ask for a prompt edit instruction for a delivered video."""
        d = self._d
        ref = d.video_registry.get(token)
        if ref is None or ref.user_id != user_id:
            await callback.answer(flow_copy.msg("expired"), show_alert=True)
            return
        if not _video_can_edit(ref):
            await callback.answer(flow_copy.msg("vid_extend_unavailable"), show_alert=True)
            return

        st = d.workspace(user_id)
        d.vid_clear(user_id)
        st["vawait"] = "vedit_prompt"
        st["vedit_token"] = token
        await callback.answer()
        await callback.message.answer(
            flow_copy.msg("vid_edit_ask_prompt", price=action_price("video_prompt_edit"))
        )

    async def extend_start(self, callback: types.CallbackQuery, user_id: int, token: str) -> None:
        """Ask for a continuation prompt for a delivered video."""
        d = self._d
        ref = d.video_registry.get(token)
        if ref is None or ref.user_id != user_id:
            await callback.answer(flow_copy.msg("expired"), show_alert=True)
            return
        if not _video_can_extend(ref):
            await callback.answer(flow_copy.msg("vid_extend_unavailable"), show_alert=True)
            return

        st = d.workspace(user_id)
        d.vid_clear(user_id)
        st["vawait"] = "vextend_prompt"
        st["vextend_token"] = token
        await callback.answer()
        next_price = video_extend_price(VIDEO_EXTEND_MODEL, ref.extend_index + 1)
        await callback.message.answer(
            flow_copy.msg("vid_extend_ask_prompt", price=next_price)
        )

    async def prompt_edit_and_send(
        self, message: types.Message, ref: VideoRef, instruction: str, *, user_id: int
    ) -> None:
        d = self._d
        if not instruction or len(instruction.strip()) < 3:
            await message.answer(flow_copy.msg("vid_prompt_too_short"))
            return
        if not _video_can_edit(ref):
            await message.answer(flow_copy.msg("vid_extend_unavailable"))
            return

        st = d.workspace(user_id)
        d.vid_clear_reference_inputs(user_id)
        st["vmode"] = "edit"
        st["vmodel"] = ref.model_id or "omni-flash-4s"
        st["vfmt"] = d.aspect_to_vfmt(ref.aspect_ratio)
        st["vcount"] = 1
        st["vawait"] = None
        st.pop("vedit_token", None)
        await self.generate_and_send(
            message,
            instruction.strip(),
            user_id=user_id,
            unit_price_override=action_price("video_prompt_edit"),
            prompt_edited=True,
            status_text=flow_copy.msg("vid_edit_working"),
            source_video=ref,
            video_operation="edit",
        )

    async def extend_and_send(
        self, message: types.Message, ref: VideoRef, prompt: str, *, user_id: int
    ) -> None:
        d = self._d
        if not prompt or len(prompt.strip()) < 3:
            await message.answer(flow_copy.msg("vid_prompt_too_short"))
            return
        if not _video_can_extend(ref):
            await message.answer(flow_copy.msg("vid_extend_unavailable"))
            return

        status_msg = await message.answer(flow_copy.msg("vid_working"))
        scene_id = ref.scene_id or await d.client_for_acc(ref.account_id).prepare_video_extend_scene(
            project_id=ref.project_id,
            workflow_id=ref.workflow_id,
        )
        if not scene_id:
            try:
                await status_msg.edit_text(flow_copy.msg("vid_extend_unavailable"))
            except Exception:
                await message.answer(flow_copy.msg("vid_extend_unavailable"))
            return
        try:
            await status_msg.delete()
        except Exception:
            pass

        st = d.workspace(user_id)
        d.vid_clear_reference_inputs(user_id)
        st["vmode"] = "extend"
        st["vmodel"] = VIDEO_EXTEND_MODEL  # продление всегда через veo-lite, независимо от исходника
        st["vfmt"] = d.aspect_to_vfmt(ref.aspect_ratio)
        st["vcount"] = 1
        st["vawait"] = None
        st.pop("vextend_token", None)
        # Fixed operator-set extend price.
        extend_price = video_extend_price(VIDEO_EXTEND_MODEL, ref.extend_index + 1)
        await self.generate_and_send(
            message,
            prompt.strip(),
            user_id=user_id,
            unit_price_override=extend_price,
            status_text=flow_copy.msg("vid_working"),
            source_video=ref,
            video_operation="extend",
            source_scene_id=scene_id,
        )

    async def repeat_last(self, callback: types.CallbackQuery, user_id: int) -> None:
        """Повторить последнюю видеогенерацию с теми же настройками и промптом."""
        d = self._d
        st = d.workspace(user_id)
        vlast = st.get("vlast")
        if not vlast or not vlast.get("prompt"):
            await callback.message.answer("Нет предыдущей видеогенерации.")
            return

        st["vmodel"] = vlast["model"]
        st["vfmt"] = d.aspect_to_vfmt(vlast.get("aspect", "landscape"))
        st["vcount"] = vlast.get("count", d.vid_default_count)
        await self.generate_and_send(callback.message, vlast["prompt"], user_id=user_id)

    async def edit_uploaded(self, message: types.Message, prompt: str, *, user_id: int) -> None:
        """Правка загруженного пользователем видео промптом (Extend недоступен)."""
        d = self._d
        st = d.workspace(user_id)
        src = st.get("vu_source") or {}
        if not src.get("mediaId"):
            await message.answer(flow_copy.msg("vid_expired_wizard"))
            return
        # Ориентация — из реальных размеров загруженного видео (PUT-ответ),
        # иначе вертикальный ролик ушёл бы в правку как landscape.
        w, h = src.get("width"), src.get("height")
        fmt = "port" if isinstance(w, int) and isinstance(h, int) and h > w else "land"
        ref = VideoRef(
            user_id=user_id,
            project_id=src.get("_project_id"),
            media_id=src.get("mediaId"),
            prompt="",
            model_id="omni-flash-4s",
            aspect_ratio=_VID_FMT_TO_ASPECT[fmt],
            mode="edit",
            prompt_edited=True,  # навсегда блокирует Продлить у результата
            workflow_id=src.get("workflowId") or src.get("workflow_id"),
            duration_s=src.get("duration_s"),
            account_id=src.get("_account_id") or d.account_for_video(user_id),
        )
        st["vmode"] = "edit"
        st["vmodel"] = "omni-flash-4s"
        st["vfmt"] = fmt
        st["vcount"] = 1
        st["vawait"] = None
        st.pop("vu_source", None)
        await self.generate_and_send(
            message,
            prompt.strip(),
            user_id=user_id,
            unit_price_override=action_price("video_prompt_edit"),
            prompt_edited=True,
            status_text=flow_copy.msg("vid_edit_working"),
            source_video=ref,
            video_operation="edit",
        )
