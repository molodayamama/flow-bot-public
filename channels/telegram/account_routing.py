"""Account-pool routing delegates for the Telegram adapter (Phase 11 core split).

Thin delegates over :class:`AccountPool` and :class:`VideoAccountRouter` plus
the per-account keeper/client maps. Extracted out of the flow_bot composition
root; runtime singletons are injected via :class:`AccountRoutingDeps` so this
module never imports flow_bot. Telegram adapter code, not a platform-neutral
core module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AccountRoutingDeps:
    account_pool: Any
    video_router: Any
    keepers: dict
    clients: dict
    default_keeper: Any
    default_client: Any
    keep_warm: Any = None


class AccountRouting:
    def __init__(self, deps: AccountRoutingDeps) -> None:
        self._d = deps

    def account_for(self, user_id: int) -> str | None:
        """Аккаунт пула для джобы юзера (sticky), None — весь пул недоступен."""
        account_id = self._d.account_pool.pick_for(user_id)
        self._note_keep_warm("image", account_id)
        return account_id

    def _note_keep_warm(self, role: str, account_id: str | None) -> None:
        if self._d.keep_warm is not None:
            self._d.keep_warm.note(role, account_id)

    def account_for_image(
        self, user_id: int, *, prefer_image_only: bool = False,
        exclude: set[str] | None = None,
    ) -> str | None:
        account_id = self._d.account_pool.pick_for_image(
            user_id, prefer_image_only=prefer_image_only, exclude=exclude,
        )
        self._note_keep_warm("image", account_id)
        return account_id

    def account_for_video(
        self, user_id: int, *, model_id: str = "omni-flash-4s", min_credits: int = 0,
        exclude: set[str] | None = None,
    ) -> str | None:
        """Аккаунт для видео-джобы — только среди video_capable, None — нет доступных."""
        account_id = self._d.video_router.account_for_video(
            user_id, model_id=model_id, min_credits=min_credits, exclude=exclude,
        )
        self._note_keep_warm("video", account_id)
        return account_id

    def cached_gcredits_hints(self) -> dict:
        return self._d.video_router.cached_gcredits_hints()

    def video_family_for_model(self, model_id: str) -> str:
        return self._d.video_router.family_for_model(model_id)

    def video_scores_for_model(self, model_id: str, min_credits: int = 0) -> dict:
        return self._d.video_router.scores_for_model(model_id, min_credits)

    def video_account_health_reason(
        self, account_id: str | None, model_id: str, min_credits: int = 0,
    ) -> str | None:
        return self._d.video_router.account_health_reason(account_id, model_id, min_credits)

    def keeper_for_acc(self, account_id: str | None) -> Any:
        return self._d.keepers.get(account_id or "", self._d.default_keeper)

    def client_for_acc(self, account_id: str | None) -> Any:
        return self._d.clients.get(account_id or "", self._d.default_client)
