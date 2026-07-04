"""Background monitor loops for the Telegram adapter (Phase 11 core split).

Two long-running asyncio tasks launched at startup:

* :meth:`Monitors.daily_digest_loop` — re-engagement nudge to users idle 3-7 days.
* :meth:`Monitors.video_pool_health_loop` — owner alert on video-pool degradation
  (edge-triggered: one alert per degraded↔recovered transition).

Extracted out of the flow_bot composition root; runtime singletons are injected
via :class:`MonitorsDeps` so this module never imports flow_bot. Telegram adapter
code (uses aiogram), not a platform-neutral core module.
"""

from __future__ import annotations

import asyncio
import datetime
import random
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Sequence

from aiogram import types

import flow_copy


@dataclass(frozen=True)
class MonitorsDeps:
    metrics: Any
    credit_store: Any
    log: Any
    send_message: Callable[..., Awaitable[Any]]
    send_owner_alert: Callable[[str], Awaitable[None]]
    account_pool: Any
    video_scores_for_model: Callable[..., dict]
    quick_ideas: Sequence[str]
    digest_hour: int
    digest_batch: int
    digest_delay_s: float
    digest_interval_h: int
    video_pool_min_score: float
    video_pool_check_interval_s: int


class Monitors:
    def __init__(self, deps: MonitorsDeps) -> None:
        self._d = deps
        self._pool_degraded_alerted = False  # шлём алерт один раз на переход состояния

    async def daily_digest_loop(self) -> None:
        """Фоновый луп: раз в 6 часов шлём дайджест пользователям, неактивным 3-7 дней."""
        d = self._d
        while True:
            try:
                now_h = datetime.datetime.utcnow().hour
                # Отправляем только в окно 11:00–13:00 UTC (гибко).
                if abs(now_h - d.digest_hour) <= 1:
                    users = d.metrics.get_users_for_digest(min_days=3, max_days=7, limit=d.digest_batch)
                    d.log.info("📨 Дайджест: найдено %d кандидатов", len(users))
                    ideas = list(d.quick_ideas)
                    for entry in users:
                        uid = entry["user_id"]
                        try:
                            bal = d.credit_store.balance(uid)
                            if bal <= 0:
                                text = flow_copy.msg("digest_nudge_empty")
                            else:
                                idea = random.choice(ideas)
                                text = flow_copy.msg("digest_nudge", idea=idea)
                            kb = types.InlineKeyboardMarkup(inline_keyboard=[
                                [types.InlineKeyboardButton(text="🎨 Создать", callback_data="m:gen")],
                                [types.InlineKeyboardButton(text="🏠 Меню", callback_data="m:menu")],
                            ])
                            await d.send_message(uid, text, reply_markup=kb, parse_mode="HTML")
                            d.metrics.log_event("digest_sent", user_id=uid)
                            await asyncio.sleep(d.digest_delay_s)
                        except Exception as exc:
                            d.log.debug("Дайджест не доставлен uid=%s: %s", uid, exc)
            except Exception:
                d.log.warning("_daily_digest_loop iteration failed", exc_info=True)
            # Спим 6 часов до следующей проверки
            await asyncio.sleep(d.digest_interval_h * 3600)

    async def video_pool_health_loop(self) -> None:
        """Фоновый монитор: алерт владельцу, когда не осталось ни одного здорового
        video-аккаунта — РАНЬШЕ, чем юзеры начнут ловить отказы. Алерт шлём один раз
        на переход (degraded ↔ recovered), чтобы не спамить."""
        d = self._d
        await asyncio.sleep(120)  # дать прогреву устаканиться
        while True:
            try:
                scores = d.video_scores_for_model("omni-flash-4s")
                usable = [a for a in d.account_pool.account_ids() if d.account_pool.is_video_capable(a)]
                # «Нездоров» = есть скор и он ниже порога; без данных = нейтрально (ок).
                healthy = [
                    a for a in usable
                    if not (a in scores and (scores[a].get("score") or 0) < d.video_pool_min_score)
                ]
                if not healthy:
                    if not self._pool_degraded_alerted:
                        self._pool_degraded_alerted = True
                        detail = ", ".join(
                            f"{a}:{round((scores.get(a, {}).get('score') or 0), 1)}" for a in usable
                        ) or "нет video-capable аккаунтов"
                        await d.send_owner_alert(
                            "⚠️ <b>Видео-пул деградировал</b>\n"
                            f"Здоровых video-аккаунтов: 0 из {len(usable)} доступных.\n"
                            f"Score: {detail}\n"
                            "Видео-запросы юзеров начнут падать — проверь аккаунты/прокси."
                        )
                        d.metrics.log_event("video_pool_degraded", source="monitor",
                                            payload={"usable": len(usable)})
                elif self._pool_degraded_alerted:
                    self._pool_degraded_alerted = False
                    await d.send_owner_alert(
                        f"✅ Видео-пул восстановлен: здоровых аккаунтов {len(healthy)}."
                    )
            except Exception:
                d.log.warning("_video_pool_health_loop iteration failed", exc_info=True)
            await asyncio.sleep(d.video_pool_check_interval_s)
