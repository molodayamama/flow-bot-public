"""Admin/owner help reference for the Telegram adapter (Phase 11 core split).

Two pieces:

* :data:`HELP_SECTIONS` — the static command reference table for ``/admin_help``
  (angle brackets HTML-escaped; single source so adding a command is one edit).
* :func:`render_admin_help` — renders the table into an HTML message.
* :func:`owner_only` — owner-level command gate (``OWNER_ID`` ⊆ ``ADMIN_IDS``).

Extracted out of the flow_bot composition root; ``owner_ids`` is injected so
this module never imports flow_bot. Telegram adapter code (uses aiogram), not a
platform-neutral core module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from aiogram import types


HELP_SECTIONS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("👤 Пользовательские", (
        ("/start", "запуск и главное меню"),
        ("/menu", "главное меню"),
        ("/balance", "баланс и пополнение через Stars"),
        ("/promo &lt;код&gt;", "активировать промокод"),
        ("/img &lt;промпт&gt;", "4 картинки по тексту"),
        ("/one &lt;промпт&gt;", "1 картинка"),
        ("/portrait &lt;промпт&gt;", "2 вертикальные картинки"),
        ("/square &lt;промпт&gt;", "2 квадратные картинки"),
        ("/imgn N &lt;промпт&gt;", "N картинок (1–8)"),
        ("/mix &lt;промпт&gt;", "собрать картинку из выбранных «ингредиентов»"),
    )),
    ("🛡 Админские (ADMIN_IDS)", (
        ("/grant &lt;user_id&gt; &lt;кредиты&gt;", "начислить пользователю кредиты"),
        ("/status", "состояние сессии (токен / проект / капча)"),
        ("/refund &lt;user_id&gt; [charge_id]", "вернуть Stars за платёж (по умолчанию последний)"),
        ("/admin_today", "сводка за сегодня (юзеры/выручка/успехи)"),
        ("/admin_revenue", "выручка за 30 дней (пакеты, по дням)"),
        ("/admin_flow", "нагрузка по моделям и бэкенду"),
        ("/admin_accounts", "состояние пула аккаунтов + задания за сегодня"),
        ("/acc_off &lt;id&gt;", "вручную отключить аккаунт пула"),
        ("/acc_on &lt;id&gt;", "вернуть аккаунт пула в работу"),
        ("/acc_vid_off &lt;id&gt;", "только картинки на аккаунте (видео → другой акк)"),
        ("/acc_vid_on &lt;id&gt;", "вернуть видео на аккаунт"),
        ("/admin_refs", "рефералы: приглашения/награды/топ"),
        ("/admin_channels [ярлык]", "каналы (атрибуция); с ярлыком — выдать ссылку"),
        ("/admin_errors", "ошибки бэкенда за 7 дней"),
        ("/admin_cohort", "D1/D7/D30 retention когорты"),
    )),
    ("👑 Владелец (OWNER_ID)", (
        ("/admin_help", "этот справочник команд"),
        ("/addpromo &lt;код&gt; &lt;кредиты&gt; [N]", "создать промокод (N использований, по умолч. 1)"),
    )),
)


@dataclass(frozen=True)
class AdminHelpDeps:
    owner_ids: Iterable[int]


class AdminHelp:
    def __init__(self, deps: AdminHelpDeps) -> None:
        self._d = deps

    def owner_only(self, message: types.Message) -> bool:
        """Гейт для команд уровня владельца (OWNER_ID в .env), строго ⊆ ADMIN_IDS."""
        return message.from_user.id in self._d.owner_ids

    def render_admin_help(self) -> str:
        blocks = ["🧭 <b>Команды бота</b>"]
        for title, rows in HELP_SECTIONS:
            lines = "\n".join(f"  <code>{cmd}</code> — {desc}" for cmd, desc in rows)
            blocks.append(f"<b>{title}</b>\n{lines}")
        return "\n\n".join(blocks)
