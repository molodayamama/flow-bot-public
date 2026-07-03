"""Routing helpers for the runtime account pool.

``AccountPool`` is re-exported for backward compatibility. The video account
routing logic (model family lookup, per-account health scoring and the video
account pick) lives here as :class:`VideoAccountRouter` so it is testable in
isolation and reusable by non-Telegram channels (Phase 11 core split).
"""

from __future__ import annotations

from typing import Any, Callable

from .pool import AccountPool

__all__ = ["AccountPool", "VideoAccountRouter"]


class VideoAccountRouter:
    """Selects and health-scores accounts for video jobs.

    Dependencies are injected so the router carries no import-time coupling to
    the Telegram bot: ``account_pool`` and ``keepers`` are the live runtime
    singletons (mutations stay visible), while ``metrics`` and
    ``video_model_meta`` are stable module-level references.
    """

    def __init__(
        self,
        *,
        account_pool: Any,
        keepers: dict,
        metrics: Any,
        video_model_meta: Callable[[str], Any],
    ) -> None:
        self._pool = account_pool
        self._keepers = keepers
        self._metrics = metrics
        self._video_model_meta = video_model_meta

    def family_for_model(self, model_id: str) -> str:
        meta = self._video_model_meta(model_id)
        return str((meta or {}).get("family") or "unknown")

    def cached_gcredits_hints(self) -> dict:
        hints = {}
        for acc_id, kp in self._keepers.items():
            cached = getattr(kp, "_gcredits_cache", None)
            if isinstance(cached, dict):
                hints[acc_id] = dict(cached)
        return hints

    def scores_for_model(self, model_id: str, min_credits: int = 0) -> dict:
        return self._metrics.report_video_account_scores(
            model_family=self.family_for_model(model_id),
            credit_hints=self.cached_gcredits_hints(),
            min_credits=int(min_credits or 0),
        )

    def account_health_reason(
        self, account_id: str | None, model_id: str, min_credits: int = 0
    ) -> str | None:
        if not account_id:
            return "missing_account"
        if not self._pool.is_reference_usable(account_id):
            return "account_unavailable"
        score = (self.scores_for_model(model_id, min_credits).get(account_id) or {})
        if score.get("proxy_failed"):
            return "proxy_check_failed"
        if score.get("recent_unusual_403"):
            return "recent_public_error_unusual_activity"
        return None

    def account_for_video(
        self, user_id: int, *, model_id: str = "omni-flash-4s", min_credits: int = 0
    ) -> str | None:
        """Аккаунт для видео-джобы — только среди video_capable, None — нет доступных."""
        return self._pool.pick_for_video(
            user_id,
            model_family=self.family_for_model(model_id),
            health_scores=self.scores_for_model(model_id, min_credits),
        )
