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

