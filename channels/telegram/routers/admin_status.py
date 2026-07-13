"""Admin /status diagnostics router (Phase 6)."""

from __future__ import annotations

import html
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

from aiogram import Router, types
from aiogram.filters import Command

import flow_copy


@dataclass(frozen=True)
class AdminStatusDeps:
    """Injected admin gate and diagnostic facades for /status."""

    admin_ids: Collection[int]
    keeper: Any
    account_pool: Any
    metrics: Any
    captcha_provider: Callable[[], str]
    capmonster_key: Callable[[], str | None]
    twocaptcha_key: Callable[[], str | None]
    time: Callable[[], float]
    bearer_timestamp: Callable[[], float]


def _safe(value: Any) -> str:
    return html.escape(str(value), quote=False)


def create_router(deps: AdminStatusDeps) -> Router:
    """Build the admin-only /status router."""

    router = Router(name="tg-admin-status")

    @router.message(Command("status"))
    async def cmd_status(message: types.Message):
        if message.from_user.id not in deps.admin_ids:
            await message.answer(flow_copy.msg("admin_denied"))
            return

        session = await deps.keeper.get_session()
        bearer = "✅" if session["bearer"] else "❌"
        project = "✅" if session["project_id"] else "❌"
        cookies = "✅" if session["cookies"] else "❌"
        age_min = int((deps.time() - deps.bearer_timestamp()) / 60)

        captcha_parts = [f"провайдер: {_safe(deps.captcha_provider())}"]
        if deps.capmonster_key():
            balance = await deps.keeper.get_capmonster_balance()
            captcha_parts.append(f"CapMonster: {_safe(balance)}")
        if deps.twocaptcha_key():
            balance = await deps.keeper.get_2captcha_balance()
            captcha_parts.append(f"2captcha: {_safe(balance)}")
        captcha_line = ", ".join(captcha_parts)

        pool_lines = []
        for status in deps.account_pool.status():
            if status["disabled"]:
                icon = "🔒"
            elif status["cooldown_left"]:
                icon = f"❄️{status['cooldown_left']}s"
            else:
                icon = "✅"
            video_icon = "🎬" if status["video_allowed"] else "🖼"
            pool_lines.append(
                f"  {icon}{video_icon} <code>{_safe(status['id'])}</code>: "
                f"img {status['active_image_jobs']}/{status['image_capacity']} "
                f"vid {status['active_video_jobs']}/{status['video_capacity']} "
                f"users={status['users']}"
            )
        pool_section = "\n".join(pool_lines) or "  (нет аккаунтов)"

        video_health_lines = []
        try:
            video_health = deps.metrics.report_video_health((1, 24))
            for window in ("1h", "24h"):
                rows = video_health.get("windows", {}).get(window, [])
                if not rows:
                    continue
                video_health_lines.append(f"  <u>{window}</u>:")
                for row in rows:
                    success_rate = row["success_rate"]
                    average = row["avg_attempts_before_200"]
                    video_health_lines.append(
                        f"    <code>{_safe(row['account'])}</code>: "
                        f"att={row['video_attempts']} 403={row['video_403']} "
                        f"ok={row['video_success']}(↻{row['video_success_after_retry']}) "
                        f"fail={row['video_final_fail']} "
                        f"avg={average if average is not None else '—'} "
                        f"sr={int(success_rate * 100) if success_rate is not None else '—'}%"
                    )
        except Exception:
            pass
        video_health_section = "\n".join(video_health_lines) or "  (нет видео-событий)"

        await message.answer(
            f"🔧 <b>Состояние бота</b>\n\n"
            f"Bearer токен: {bearer} (возраст: {age_min} мин)\n"
            f"Project ID:   {project}\n"
            f"Cookies:      {cookies} ({len(session['cookies'])} шт)\n"
            f"Капча:        {captcha_line}\n\n"
            f"<b>Пул аккаунтов:</b>\n{pool_section}\n\n"
            f"<b>Видео-здоровье:</b>\n{video_health_section}\n",
            parse_mode="HTML",
        )

    return router
