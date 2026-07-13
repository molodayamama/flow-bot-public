"""Request-driven browser keep-warm policy for Flow accounts."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class KeepWarmConfig:
    image_accounts: int = 1
    video_accounts: int = 1
    hold_seconds: float = 1800.0


class AccountKeepWarmPolicy:
    """Pin recently selected accounts so their Flow browser tab is not parked."""

    def __init__(
        self,
        *,
        account_pool: Any,
        keepers: dict[str, Any],
        config: KeepWarmConfig,
        clock: Callable[[], float] = time.time,
        log: Any = None,
    ) -> None:
        self._pool = account_pool
        self._keepers = keepers
        self._config = config
        self._clock = clock
        self._log = log
        self._targets: dict[str, dict[str, float]] = {"image": {}, "video": {}}

    def _limit(self, role: str) -> int:
        value = self._config.video_accounts if role == "video" else self._config.image_accounts
        return max(0, int(value))

    def _eligible(self, role: str, account_id: str) -> bool:
        if role == "video":
            return bool(self._pool.is_video_capable(account_id))
        return bool(self._pool.is_available(account_id))

    def prune(self, now: float | None = None) -> None:
        now = self._clock() if now is None else now
        for role, targets in self._targets.items():
            for account_id, expires_at in list(targets.items()):
                if expires_at <= now or not self._eligible(role, account_id):
                    targets.pop(account_id, None)
            limit = self._limit(role)
            if limit <= 0:
                targets.clear()
            while len(targets) > limit:
                targets.pop(min(targets, key=targets.get), None)

    def note(self, role: str, account_id: str | None) -> None:
        if role not in self._targets or not account_id or self._limit(role) <= 0:
            return
        if not self._eligible(role, account_id):
            return
        hold = max(0.0, float(self._config.hold_seconds))
        if hold <= 0:
            return
        now = self._clock()
        self._targets[role][account_id] = now + hold
        self.prune(now)
        keeper = self._keepers.get(account_id)
        if keeper is not None:
            keeper.keep_warm_for(hold, role)
        if self._log is not None:
            self._log.info(
                "keep-warm pinned after request: %s:%s for %.0f sec",
                role,
                account_id,
                hold,
            )

    def active_targets(self, now: float | None = None) -> dict[str, tuple[str, ...]]:
        self.prune(now)
        return {role: tuple(sorted(targets)) for role, targets in self._targets.items()}
