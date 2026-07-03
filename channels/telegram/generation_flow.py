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
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aiogram import types

import flow_copy
from flow_core import DEFAULT_IMAGE_MODEL, result_pairs
from textutil import _short_prompt

_GEN_ATTEMPT_TIMEOUT = 70  # seconds per attempt


@dataclass(frozen=True)
class GenerationFlowDeps:
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


class GenerationFlow:
    def __init__(self, deps: GenerationFlowDeps) -> None:
        self._d = deps

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
