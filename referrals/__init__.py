"""Referral reward orchestration shared across platforms (Phase 9).

The crediting + idempotency/cap rules live in :class:`referrals.service.ReferralService`,
decoupled from any chat platform: the referrer notification is injected. flow_bot
keeps the referral join/CTA UI and wires this service with its credit store and
Telegram notifier.
"""

from referrals.service import ReferralService

__all__ = ["ReferralService"]
