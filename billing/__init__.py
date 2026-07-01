"""Billing primitives shared across platforms.

PR-7a extracts the platform-neutral credit gate (charge-on-success with
refund-on-failure) here so Telegram and MAX can share one billing rule set. The
credit/payment stores and pricing already live in ``flow_core``; this package is
the seam for the orchestration that used to live inside ``flow_bot``.
"""

from billing.credit_gate import Charge, NotEnoughCredits, open_credit_gate

__all__ = ["Charge", "NotEnoughCredits", "open_credit_gate"]
