"""Platform-neutral credit gate (PR-7a).

Extracted from ``flow_bot``'s ``credit_gate`` so the charge/refund rule is shared
across chat platforms and free of any Telegram/aiogram coupling. The rule is
"charge-on-success": reserve the price on enter, and refund it on exit unless the
body marked the charge as successful.

The insufficient-balance *UI* is a platform concern, so it is injected as an
``on_insufficient`` callback — this module never touches chat messages, keyboards
or copy.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable, Protocol


class NotEnoughCredits(Exception):
    """Raised when a user lacks the credits for a paid action."""


class Charge:
    """Result holder for a paid action: set ``.ok = True`` on success."""

    __slots__ = ("ok",)

    def __init__(self) -> None:
        self.ok = False


class CreditStoreLike(Protocol):
    def balance(self, user_id: int) -> int:
        ...

    def charge(self, user_id: int, amount: int) -> Any:
        ...

    def refund(self, user_id: int, amount: int) -> Any:
        ...


OnInsufficient = Callable[[int, int], Awaitable[Any]]


@asynccontextmanager
async def open_credit_gate(
    store: CreditStoreLike,
    user_id: int,
    price: int,
    *,
    on_insufficient: OnInsufficient | None = None,
):
    """Reserve ``price`` on enter; refund on exit unless ``charge.ok`` was set.

    Free actions (``price <= 0``) pass without charging. On insufficient balance,
    awaits ``on_insufficient(have, price)`` (the caller shows any UI) and then
    raises :class:`NotEnoughCredits`. Platform-neutral: no chat/UI here.
    """
    if price <= 0:
        yield Charge()  # free — no charge
        return

    have = store.balance(user_id)
    if have < price:
        if on_insufficient is not None:
            await on_insufficient(have, price)
        raise NotEnoughCredits

    store.charge(user_id, price)
    charge = Charge()
    try:
        yield charge
    finally:
        if not charge.ok:
            store.refund(user_id, price)
