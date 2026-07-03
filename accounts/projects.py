"""Per-user Flow project lifecycle, extracted from the monolith (Phase 11).

A Flow project is sticky to a pool account (``account_id:user_id``). On failover
to another account the user gets a fresh project there; legacy single-account
records migrate to the default account's key on first read.

``ProjectManager`` owns the auto-disable circuit breaker as instance state
instead of module globals, honoring the "no global mutable state" rule. All
runtime dependencies (store, account picker, keeper resolver, config, logger)
are injected so the class has no import-time coupling to the Telegram bot.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable


class ProjectManager:
    """Resolves/creates a user's Flow project and self-disables after failures."""

    def __init__(
        self,
        *,
        project_store: Any,
        account_for: Callable[[int], str | None],
        keeper_for_acc: Callable[[str | None], Any],
        default_account_id: str,
        max_failures: int,
        per_user_enabled: bool,
        log: Any,
    ) -> None:
        self._store = project_store
        self._account_for = account_for
        self._keeper_for_acc = keeper_for_acc
        self._default_account_id = default_account_id
        self._max_failures = max_failures
        self._enabled = bool(per_user_enabled)
        self._failures = 0
        self._log = log

    @property
    def enabled(self) -> bool:
        return self._enabled

    @staticmethod
    def project_key(account_id: str, user_id: int) -> str:
        """Ключ проекта в сторе: проект юзера живёт на конкретном аккаунте пула."""
        return f"{account_id}:{user_id}"

    async def ensure(self, user_id: int, *, account_id: str | None = None) -> str | None:
        """Вернуть Flow-проект пользователя, создав его при первом обращении.

        Проект привязан к аккаунту пула (sticky): при failover на другой аккаунт
        юзеру создаётся новый проект там. Старые записи без префикса аккаунта
        мигрируются на ключ дефолтного аккаунта при первом чтении.

        При неудаче создания (или при выключенной фиче) возвращает ``None`` —
        вызывающий код тогда использует общий проект сессии (старое поведение),
        чтобы генерация всё равно работала. После ``max_failures`` неудач подряд
        фича отключается на сессию, чтобы не висеть на каждом запросе.
        """
        acc_id = account_id or self._account_for(user_id) or self._default_account_id
        key = self.project_key(acc_id, user_id)
        existing = self._store.get(key)
        if existing:
            return existing
        if acc_id == self._default_account_id:
            legacy = self._store.get(user_id)  # записи времён одного аккаунта
            if legacy:
                self._store.set(key, legacy)
                return legacy
        if not self._enabled:
            return None

        try:
            pid = await self._keeper_for_acc(acc_id).create_new_project()
        except Exception:
            self._log.exception("create_new_project failed")
            pid = None

        if pid:
            self._failures = 0
            self._store.set(key, pid)
            self._log.info(f"📋 Пользователю {user_id} выдан проект {pid} (аккаунт {acc_id})")
            return pid

        self._failures += 1
        if self._failures >= self._max_failures:
            self._enabled = False
            self._log.warning(
                "⚠️ Per-user проекты отключены после %d неудач — работаю на общем проекте сессии.",
                self._failures,
            )
        return None
