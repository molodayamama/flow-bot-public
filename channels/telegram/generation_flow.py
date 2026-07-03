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

from aiogram import types

import flow_copy
from flow_core import DEFAULT_IMAGE_MODEL, action_price, image_model_extra, result_pairs
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
