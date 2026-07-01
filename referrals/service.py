"""Referral reward orchestration (Phase 9).

Extracted verbatim from ``flow_bot``'s ``_maybe_apply_referral_rewards`` /
``_clawback_referral_rewards``. The credit crediting plus the idempotency and
daily-cap rules live here; the referrer *notification* is a chat-platform concern
and is injected as ``notify``, so this stays channel-neutral (no aiogram /
flow_bot / flow_copy import).

Neither method ever raises into the caller — referral logic must not break
payment handling.
"""

from __future__ import annotations

from typing import Any, Callable

from flow_core import (
    REFERRAL_DAILY_CAP_CREDITS,
    REFERRAL_REWARD_WINDOW_DAYS,
    referral_milestone_bonus,
    referral_ongoing_bonus,
)

Notify = Callable[[int, int], Any]


class ReferralService:
    """Apply/clawback referrer rewards over an injected credit store + metrics."""

    def __init__(self, *, store: Any, metrics: Any, notify: Notify | None = None, log: Any = None):
        self._store = store
        self._metrics = metrics
        self._notify = notify or (lambda referrer_id, bonus: None)
        self._log = log

    def apply_payment_rewards(
        self,
        referred_user_id: int,
        *,
        stars_paid: int,
        credits_issued: int,
        pack_id: str,
        provider_payment_id: str,
    ) -> None:
        """Reward the referrer for a referred user's payment (idempotent, capped).

        Never raises into the caller: referral logic must not break payment.
        """
        m = self._metrics
        try:
            referrer_id = m.get_referrer_of(referred_user_id)
            if not referrer_id or referrer_id == referred_user_id:
                return
            # Referral binding is time-boxed (~3 months). After it, neither the
            # one-off bonus nor the % is granted — otherwise "two accounts" give a
            # forever discount and eat the margin.
            if not m.referral_is_active(referred_user_id, REFERRAL_REWARD_WINDOW_DAYS):
                return
            status = m.referral_status(referred_user_id)
            cap = REFERRAL_DAILY_CAP_CREDITS

            if status == "joined":
                bonus = referral_milestone_bonus(stars_paid)
                if bonus > 0 and m.get_referral_credits_today(referrer_id) + bonus <= cap:
                    # Atomic joined->rewarded claim: of two concurrent payments by
                    # the referred user exactly one wins the bonus (no TOCTOU gap).
                    if m.grant_milestone_if_joined(
                        referred_user_id=referred_user_id, reward_credits=bonus
                    ):
                        self._store.add(referrer_id, bonus)
                        m.log_event("referral_reward_paid", user_id=referrer_id,
                                    payload={"tier": "milestone", "bonus": bonus,
                                             "referred": referred_user_id})
                        self._notify(referrer_id, bonus)
                return

            if status == "rewarded":
                ongoing = referral_ongoing_bonus(credits_issued)
                if ongoing <= 0:
                    return
                if m.get_ongoing_reward_by_payment(provider_payment_id):
                    return  # duplicate webhook
                if m.get_referral_credits_today(referrer_id) + ongoing > cap:
                    return
                if m.record_ongoing_reward(referrer_id, referred_user_id, ongoing,
                                           provider_payment_id):
                    self._store.add(referrer_id, ongoing)
                    m.log_event("referral_reward_paid", user_id=referrer_id,
                                payload={"tier": "ongoing", "bonus": ongoing,
                                         "referred": referred_user_id})
                    self._notify(referrer_id, ongoing)
        except Exception:
            if self._log is not None:
                self._log.warning("referral reward failed", exc_info=True)

    def clawback(self, referred_user_id: int, charge_id: str) -> None:
        """Reverse referral rewards for a refunded payment (best-effort)."""
        m = self._metrics
        try:
            ongoing = m.get_ongoing_reward_by_payment(charge_id)
            if ongoing:
                self._store.charge(ongoing["referrer_user_id"],
                                   min(ongoing["reward_credits"],
                                       self._store.balance(ongoing["referrer_user_id"])))
                m.log_event("referral_reward_clawback", user_id=ongoing["referrer_user_id"],
                            payload={"amount": ongoing["reward_credits"], "tier": "ongoing"})
            milestone = m.get_milestone_by_referred(referred_user_id)
            if milestone and milestone.get("status") == "rewarded":
                self._store.charge(milestone["referrer_user_id"],
                                   min(milestone["reward_credits"],
                                       self._store.balance(milestone["referrer_user_id"])))
                m.reset_referral_to_joined(referred_user_id)
                m.log_event("referral_reward_clawback", user_id=milestone["referrer_user_id"],
                            payload={"amount": milestone["reward_credits"], "tier": "milestone"})
        except Exception:
            if self._log is not None:
                self._log.warning("referral clawback failed", exc_info=True)
