"""Tail fallback for unmatched callback queries (Phase 6).

Historically the monolith registered a bare ``@dp.callback_query()`` catch-all
whose ``parse_action_callback(...) is None`` branch silently acknowledged any
unknown callback (expired buttons, messages from older releases), so Telegram
clients never showed an endless spinner. That catch-all now carries a precise
image-action filter, and the silent-ack duty lives here instead.

This router must be included LAST on the dispatcher: aiogram matches included
routers in inclusion order, so every extracted callback router has to be
registered before this one.
"""

from __future__ import annotations

from aiogram import Router, types


def create_router() -> Router:
    """Build the lowest-priority router that acks anything still unmatched."""

    router = Router(name="tg-callback-fallback")

    @router.callback_query()
    async def on_unknown_callback(callback: types.CallbackQuery) -> None:
        # Same user-visible behaviour as the old catch-all's unknown branch:
        # a silent answer() so the button press never hangs.
        await callback.answer()

    return router
