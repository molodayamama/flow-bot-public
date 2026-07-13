"""Health helpers for the runtime account pool.

Failure/cooldown *policy* for image and video jobs lives here as
:class:`AccountFailurePolicy` so it is testable in isolation and reusable by
non-Telegram channels (Phase 11 core split). Dependencies are injected — the
policy carries no import-time coupling to the Telegram bot: ``account_pool`` is
the live runtime singleton (mutations stay visible), ``log`` / ``metrics`` are
stable module-level references, and ``fire_owner_alert`` is a fire-and-forget
callback the channel provides.
"""

from __future__ import annotations

from typing import Any, Callable

import flow_copy

from .pool import AccountPool

__all__ = ["AccountPool", "is_rate_limit_error", "AccountFailurePolicy"]


def is_rate_limit_error(result: dict | None) -> bool:
    """True when a backend result reads as a provider rate-limit (429)."""
    error = str((result or {}).get("error", "")).lower()
    return (
        error == flow_copy.msg("rate_limited").lower()
        or "429" in error
        or "too many requests" in error
        or "слишком много" in error
    )


def _cooldown_alert(account_id: str, reason: str, op: str) -> str:
    return (
        f"⚠️ <b>Аккаунт кулдаун</b>\n"
        f"Аккаунт: <code>{account_id}</code>\n"
        f"Причина: {reason} ({op})"
    )


class AccountFailurePolicy:
    """Applies cooldown/failure marks to the pool after a backend job fails.

    A shared pool cooldown blocks *both* image and video on an account; the
    router then steers traffic to healthy accounts. Owner alerts fire only for
    signals worth a human (unusual-activity, auth, N-in-a-row) — never for bare
    429s, which can be frequent across a fleet.
    """

    def __init__(
        self,
        *,
        account_pool: Any,
        log: Any,
        metrics: Any,
        fire_owner_alert: Callable[[str], None],
    ) -> None:
        self._pool = account_pool
        self._log = log
        self._metrics = metrics
        self._fire_owner_alert = fire_owner_alert

    def mark_image_failure(self, account_id: str | None, result: dict | None = None) -> None:
        if not account_id:
            return
        if (result or {}).get("account_risk") == "unusual_activity":
            if self._pool.mark_cooldown(account_id):
                self._log.warning(
                    "Image account %s cooled down after provider unusual-activity", account_id
                )
                self._metrics.log_event(
                    "account_cooldown",
                    payload={"account": account_id, "reason": "unusual_activity", "op": "image"},
                )
                self._fire_owner_alert(_cooldown_alert(account_id, "unusual_activity", "image"))
            return
        if is_rate_limit_error(result):
            # 429 — cool down at once. No owner alert: fleet-wide 429s are noisy.
            if self._pool.mark_cooldown(account_id):
                self._log.warning(
                    "Image account %s cooled down after provider 429 (rate limit)", account_id
                )
                self._metrics.log_event(
                    "account_cooldown",
                    payload={"account": account_id, "reason": "rate_limited", "op": "image"},
                )
            return
        if self._pool.mark_failure(account_id):
            self._fire_owner_alert(_cooldown_alert(account_id, "N ошибок подряд", "image"))

    def mark_video_failure(self, account_id: str | None, result: dict | None = None) -> None:
        if not account_id:
            return
        risk = (result or {}).get("account_risk")
        if risk in {"video_auth", "unusual_activity"}:
            # Auth/bearer — cool down at once (requests won't pass until fixed).
            if self._pool.mark_cooldown(account_id):
                self._log.warning(
                    "Video account %s cooled down after provider account-risk signal", account_id
                )
                self._metrics.log_event(
                    "account_cooldown",
                    payload={"account": account_id, "reason": risk, "op": "video"},
                )
                self._fire_owner_alert(_cooldown_alert(account_id, risk, "video"))
            return
        if is_rate_limit_error(result):
            if self._pool.mark_cooldown(account_id):
                self._log.warning(
                    "Video account %s cooled down after provider 429 (rate limit)", account_id
                )
                self._metrics.log_event(
                    "account_cooldown",
                    payload={"account": account_id, "reason": "rate_limited", "op": "video"},
                )
            return
        # video_recaptcha_403 (stochastic score) and other errors — do NOT cool
        # down after a single 403 series: count it as a fail; cooldown only after
        # several in a row (mark_failure threshold), so a good account isn't burned.
        if risk == "video_recaptcha_403":
            self._metrics.log_event("video_recaptcha_403", payload={"account": account_id})
        if self._pool.mark_failure(account_id):
            self._fire_owner_alert(_cooldown_alert(account_id, "N ошибок подряд", "video"))
