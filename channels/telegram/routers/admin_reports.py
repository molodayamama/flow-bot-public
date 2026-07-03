"""Read-only admin metrics report commands router (Phase 6)."""

from __future__ import annotations

import asyncio
import html
from dataclasses import dataclass
from typing import Any, Callable

from aiogram import Router, types
from aiogram.filters import Command

import flow_copy
from flow_core import CHANNEL_PARAM_PREFIX, parse_channel_seed


@dataclass(frozen=True)
class AdminReportsDeps:
    """Injected admin gate, metrics reports, pool, and keeper accessors."""

    admin_only: Callable[[types.Message], bool]
    metrics: Any
    account_pool: Any
    keeper_for_acc: Callable[[Any], Any]
    bot_username: Callable[[], str]


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def create_router(deps: AdminReportsDeps) -> Router:
    """Build the /admin_today .. /admin_cohort read-only reports router."""

    router = Router(name="tg-admin-reports")

    @router.message(Command("admin_today"))
    async def cmd_admin_today(message: types.Message):
        if not deps.admin_only(message):
            await message.answer(flow_copy.msg("admin_denied"))
            return
        r = deps.metrics.report_today()
        top = "\n".join(f"  • {a['event_name']} — {a['count']}" for a in r["top_actions"]) or "  —"
        await message.answer(
            "📊 <b>Сегодня</b>\n"
            f"Новые: <b>{r['new_users']}</b> · активные: <b>{r['active_users']}</b> · "
            f"платящие: <b>{r['paying_users']}</b>\n"
            f"Картинки: <b>{r['image_generations']}</b> · видео: <b>{r['video_generations']}</b>\n"
            f"Success rate: <b>{_fmt_pct(r['success_rate'])}</b>\n"
            f"Выручка: <b>{r['revenue_rub']:.0f}₽</b> ({r['revenue_stars']} Stars)\n"
            f"Кредиты: списано <b>{r['credits_charged']}</b> · возвращено <b>{r['credits_refunded']}</b>\n"
            f"Топ действий:\n{top}",
            parse_mode="HTML",
        )


    @router.message(Command("admin_revenue"))
    async def cmd_admin_revenue(message: types.Message):
        if not deps.admin_only(message):
            await message.answer(flow_copy.msg("admin_denied"))
            return
        r = deps.metrics.report_revenue(30)
        by_pack = "\n".join(
            f"  • {p['package_id']}: {p['count']}× · {p['rub']:.0f}₽ ({p['stars']} Stars)"
            for p in r["by_package"]
        ) or "  —"
        by_day = "\n".join(
            f"  • {d['day']}: {d['rub']:.0f}₽ ({d['count']}×)" for d in r["by_day"][:7]
        ) or "  —"
        await message.answer(
            "💰 <b>Выручка (30 дней)</b>\n"
            f"Всего: <b>{r['revenue_rub']:.0f}₽</b> ({r['revenue_stars']} Stars) · "
            f"платежей: <b>{r['transactions_count']}</b> · плательщиков: <b>{r['paying_users']}</b>\n"
            f"По пакетам:\n{by_pack}\n"
            f"По дням:\n{by_day}",
            parse_mode="HTML",
        )


    @router.message(Command("admin_flow"))
    async def cmd_admin_flow(message: types.Message):
        if not deps.admin_only(message):
            await message.answer(flow_copy.msg("admin_denied"))
            return
        r = deps.metrics.report_flow()
        ops = "\n".join(
            f"  • {o['operation_type']}: {o['count']}× · ✅{o['success']}/❌{o['fail']}"
            f"{' · ~%dмс' % o['avg_duration_ms'] if o['avg_duration_ms'] else ''}"
            for o in r["by_operation"]
        ) or "  —"
        models = "\n".join(
            f"  • {m['model']}: {m['jobs']}× · ΔG {m['flow_credits_delta_sum']}"
            for m in r["by_model_credits"]
        ) or "  —"
        errs = "\n".join(f"  • {e['error_type']}: {e['count']}" for e in r["errors_by_type"]) or "  —"
        await message.answer(
            "🛠 <b>Flow-движок</b>\n"
            f"Success rate: <b>{_fmt_pct(r['success_rate'])}</b>\n"
            f"По операциям:\n{ops}\n"
            f"G-кредиты по моделям:\n{models}\n"
            f"Ошибки:\n{errs}",
            parse_mode="HTML",
        )


    @router.message(Command("admin_accounts"))
    async def cmd_admin_accounts(message: types.Message):
        if not deps.admin_only(message):
            await message.answer(flow_copy.msg("admin_denied"))
            return
        # Живое состояние пула (роутинг/health) + статистика jobs за сегодня.
        acc_ids = deps.account_pool.account_ids()
        gcredit_results = await asyncio.gather(
            *[deps.keeper_for_acc(aid).get_g_credits() for aid in acc_ids],
            return_exceptions=True,
        )
        gcredits_map = {
            aid: (res if isinstance(res, dict) else None)
            for aid, res in zip(acc_ids, gcredit_results)
        }
        pool_lines = []
        for s in deps.account_pool.status():
            state = "⛔ выключен" if s["disabled"] else (
                f"🧊 кулдаун {s['cooldown_left']}с" if s["cooldown_left"] else "✅ активен"
            )
            media_cap = "🎬+🖼" if s.get("video_allowed", True) else "🖼 only"
            gc = gcredits_map.get(s["id"])
            if gc and gc.get("error"):
                gc_str = f" · G {html.escape(str(gc.get('error')))}"
            elif gc:
                paid = "💳" if gc.get("is_paid") else "🆓"
                gc_str = f" · G {gc['credits']}{paid}"
            else:
                gc_str = " · G ?"
            pool_lines.append(
                f"  • <b>{html.escape(s['id'])}</b>: {state} · {media_cap} · 👥{s['users']} · сбоев {s['fails']}{gc_str}"
            )
        text = "🧮 <b>Пул аккаунтов</b>\n" + "\n".join(pool_lines)
        accounts = deps.metrics.report_accounts().get("accounts", [])
        if accounts:
            lines = []
            for a in accounts:
                rem = f" · остаток G {a['credits_remaining']}" if a["credits_remaining"] is not None else ""
                err = f" · ⚠️ {html.escape(str(a['last_error']))}" if a["last_error"] else ""
                lines.append(
                    f"  • <b>{html.escape(str(a['account_id']))}</b>: "
                    f"{a['jobs']}× · ✅{a['success']}/❌{a['fail']}{rem}{err}"
                )
            text += "\n\n🧮 <b>Задания (сегодня)</b>\n" + "\n".join(lines)
        else:
            text += "\n\nСегодня заданий не было."
        await message.answer(text, parse_mode="HTML")


    @router.message(Command("admin_refs"))
    async def cmd_admin_refs(message: types.Message):
        if not deps.admin_only(message):
            await message.answer(flow_copy.msg("admin_denied"))
            return
        r = deps.metrics.report_refs()
        top = "\n".join(
            f"  • {t['referrer_user_id']}: {t['count']}" for t in r["top_referrers"]
        ) or "  —"
        await message.answer(
            "🤝 <b>Рефералы</b>\n"
            f"Всего: <b>{r['total_referrals']}</b> · пришли: <b>{r['joined']}</b> · "
            f"вознаграждены: <b>{r['rewarded']}</b>\n"
            f"Выдано кредитов: <b>{r['total_reward_credits']}</b>\n"
            f"Топ пригласивших:\n{top}",
            parse_mode="HTML",
        )


    @router.message(Command("admin_channels"))
    async def cmd_admin_channels(message: types.Message):
        """Рекламные каналы: привлечено / платящие / выручка по каждой deep-link.

        Без аргумента — сводка по всем каналам. С аргументом
        (`/admin_channels <ярлык>`) — готовая ссылка для этого канала.
        """
        if not deps.admin_only(message):
            await message.answer(flow_copy.msg("admin_denied"))
            return
        parts = (message.text or "").split()
        if len(parts) > 1:
            slug = parse_channel_seed(CHANNEL_PARAM_PREFIX + parts[1])
            if not slug:
                await message.answer(
                    "Ярлык канала: латиница/цифры/_/-, до 32 символов. "
                    "Пример: <code>/admin_channels my_channel</code>",
                    parse_mode="HTML",
                )
                return
            username = deps.bot_username() or "&lt;bot&gt;"
            link = f"https://t.me/{username}?start={CHANNEL_PARAM_PREFIX}{slug}"
            await message.answer(
                f"🔗 Ссылка для канала <b>{html.escape(slug)}</b>:\n<code>{html.escape(link)}</code>",
                parse_mode="HTML",
            )
            return
        r = deps.metrics.report_channels()
        if not r["channels"]:
            await message.answer(
                "📡 <b>Каналы</b>\nПока никто не пришёл по рекламным ссылкам.\n"
                "Ссылка для канала: <code>/admin_channels &lt;ярлык&gt;</code>",
                parse_mode="HTML",
            )
            return
        lines = []
        for c in r["channels"]:
            rub = f" · {c['revenue_rub']:.0f}₽" if c["revenue_rub"] else ""
            lines.append(
                f"  • <b>{html.escape(str(c['channel']))}</b>: 👥{c['users']} · "
                f"💳{c['paid_users']} · Stars {c['revenue_stars']}{rub}"
            )
        await message.answer(
            f"📡 <b>Каналы</b> (привлечено всего: <b>{r['total_acquired']}</b>)\n"
            + "\n".join(lines)
            + "\n\nСсылка для канала: <code>/admin_channels &lt;ярлык&gt;</code>",
            parse_mode="HTML",
        )


    @router.message(Command("admin_errors"))
    async def cmd_admin_errors(message: types.Message):
        if not deps.admin_only(message):
            await message.answer(flow_copy.msg("admin_denied"))
            return
        r = deps.metrics.report_errors(7)
        by_type = "\n".join(f"  • {e['error_type']}: {e['count']}" for e in r["errors_by_type"]) or "  —"
        recent = "\n".join(
            f"  • {x['created_at']} · {x['operation_type']}/{x['model']} · {x['error_type']}"
            for x in r["recent"]
        ) or "  —"
        await message.answer(
            "🚨 <b>Ошибки (7 дней)</b>\n"
            f"По типам:\n{by_type}\n"
            f"Последние:\n{recent}",
            parse_mode="HTML",
        )


    @router.message(Command("admin_cohort"))
    async def cmd_admin_cohort(message: types.Message):
        """D1/D7/D30 retention cohorts (последние 7 когорт на каждый период)."""
        if not deps.admin_only(message):
            await message.answer(flow_copy.msg("admin_denied"))
            return
        rows = deps.metrics.report_cohort_retention()
        if not rows:
            await message.answer("📊 Недостаточно данных для когортного анализа.")
            return

        # Group by period, average across cohorts.
        from collections import defaultdict
        by_period: dict[int, list[dict]] = defaultdict(list)
        for r in rows:
            by_period[r["period"]].append(r)

        lines = ["📊 <b>Retention когорты</b>"]
        for p in sorted(by_period):
            cohorts = by_period[p]
            total_size = sum(c["cohort_size"] for c in cohorts)
            total_ret = sum(c["retained"] for c in cohorts)
            avg_rate = (total_ret / total_size * 100) if total_size else 0.0
            lines.append(
                f"\n<b>D{p}</b>  (когорт: {len(cohorts)}, "
                f"всего юзеров: {total_size}, удержано: {total_ret}, "
                f"avg {avg_rate:.1f}%)"
            )
            for c in cohorts:
                pct = f"{c['rate']*100:.1f}%" if c["cohort_size"] else "—"
                lines.append(
                    f"  {c['cohort_date']}  {c['retained']}/{c['cohort_size']}  {pct}"
                )

        await message.answer("\n".join(lines), parse_mode="HTML")

    return router
