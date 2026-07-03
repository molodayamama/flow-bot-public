"""Admin /status diagnostics router (Phase 6)."""

from __future__ import annotations

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


def create_router(deps: AdminStatusDeps) -> Router:
    """Build the admin-only /status router."""

    router = Router(name="tg-admin-status")

    @router.message(Command("status"))
    async def cmd_status(message: types.Message):
        if message.from_user.id not in deps.admin_ids:
            await message.answer(flow_copy.msg("admin_denied"))
            return

        session = await deps.keeper.get_session()
        bearer = "вњ…" if session["bearer"] else "вќЊ"
        project = "вњ…" if session["project_id"] else "вќЊ"
        cookies = "вњ…" if session["cookies"] else "вќЊ"
        age_min = int((deps.time() - deps.bearer_timestamp()) / 60)

        captcha_parts = [f"РїСЂРѕРІР°Р№РґРµСЂ: {deps.captcha_provider()}"]
        if deps.capmonster_key():
            captcha_parts.append(f"CapMonster: {await deps.keeper.get_capmonster_balance()}")
        if deps.twocaptcha_key():
            captcha_parts.append(f"2captcha: {await deps.keeper.get_2captcha_balance()}")
        captcha_line = ", ".join(captcha_parts)

        pool_lines = []
        for s in deps.account_pool.status():
            if s["disabled"]:
                icon = "рџ”’"
            elif s["cooldown_left"]:
                icon = f"вќ„пёЏ{s['cooldown_left']}s"
            else:
                icon = "вњ…"
            vid_icon = "рџЋ¬" if s["video_allowed"] else "рџ–ј"
            pool_lines.append(
                f"  {icon}{vid_icon} <code>{s['id']}</code>: "
                f"img {s['active_image_jobs']}/{s['image_capacity']} "
                f"vid {s['active_video_jobs']}/{s['video_capacity']} "
                f"users={s['users']}"
            )
        pool_section = "\n".join(pool_lines) or "  (РЅРµС‚ Р°РєРєР°СѓРЅС‚РѕРІ)"

        vh_lines = []
        try:
            vh = deps.metrics.report_video_health((1, 24))
            for win in ("1h", "24h"):
                rows = vh.get("windows", {}).get(win, [])
                if not rows:
                    continue
                vh_lines.append(f"  <u>{win}</u>:")
                for r in rows:
                    sr = r["success_rate"]
                    avg = r["avg_attempts_before_200"]
                    vh_lines.append(
                        f"    <code>{r['account']}</code>: "
                        f"att={r['video_attempts']} 403={r['video_403']} "
                        f"ok={r['video_success']}(в†»{r['video_success_after_retry']}) "
                        f"fail={r['video_final_fail']} "
                        f"avg={avg if avg is not None else 'вЂ”'} "
                        f"sr={int(sr * 100) if sr is not None else 'вЂ”'}%"
                    )
        except Exception:
            pass
        vh_section = "\n".join(vh_lines) or "  (РЅРµС‚ РІРёРґРµРѕ-СЃРѕР±С‹С‚РёР№)"

        await message.answer(
            f"рџ”§ <b>РЎРѕСЃС‚РѕСЏРЅРёРµ Р±РѕС‚Р°</b>\n\n"
            f"Bearer С‚РѕРєРµРЅ: {bearer} (РІРѕР·СЂР°СЃС‚: {age_min} РјРёРЅ)\n"
            f"Project ID:   {project} ({session['project_id'] or 'вЂ”'})\n"
            f"Cookies:      {cookies} ({len(session['cookies'])} С€С‚)\n"
            f"РљР°РїС‡Р°:        {captcha_line}\n\n"
            f"<b>РџСѓР» Р°РєРєР°СѓРЅС‚РѕРІ:</b>\n{pool_section}\n\n"
            f"<b>Р’РёРґРµРѕ-Р·РґРѕСЂРѕРІСЊРµ:</b>\n{vh_section}\n",
            parse_mode="HTML",
        )

    return router
