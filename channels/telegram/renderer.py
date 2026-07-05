from __future__ import annotations

from aiogram import types

from channels.base import Button, Keyboard


def render_button(button: Button) -> types.InlineKeyboardButton:
    """Convert a platform-neutral button into an aiogram inline button."""

    kwargs = {"text": button.text}
    if button.callback_data is not None:
        kwargs["callback_data"] = button.callback_data
    if button.url is not None:
        kwargs["url"] = button.url
    return types.InlineKeyboardButton(**kwargs)


def render_keyboard(keyboard: Keyboard | None) -> types.InlineKeyboardMarkup | None:
    """Convert a platform-neutral keyboard into an aiogram inline markup."""

    if keyboard is None:
        return None
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [render_button(button) for button in row]
            for row in keyboard.rows
        ]
    )


to_aiogram_button = render_button
to_aiogram_keyboard = render_keyboard


async def edit_or_answer(
    message: types.Message, text: str, kb, *, parse_mode: str | None = None
) -> types.Message | None:
    """Edit the wizard message in place; swallow the harmless "not modified" error.

    Re-tapping an already-selected wizard button rebuilds an identical screen, and
    Telegram rejects ``edit_text`` with "message is not modified". We must NOT fall
    back to ``answer`` there — that posts a duplicate panel. Only a genuine edit
    failure (message too old / deleted) falls through to a fresh ``answer``.
    Returns the message that now carries the wizard (edited or freshly sent), or
    ``None`` when the no-op edit was swallowed.
    """
    try:
        return await message.edit_text(text, reply_markup=kb, parse_mode=parse_mode)
    except Exception as exc:
        if "not modified" in str(exc).lower():
            return None
        return await message.answer(text, reply_markup=kb, parse_mode=parse_mode)

