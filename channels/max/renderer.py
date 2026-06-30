from __future__ import annotations

from channels.base import Button, Keyboard


def render_button(button: Button) -> dict:
    if button.callback_data is not None:
        return {
            "type": "callback",
            "text": button.text,
            "payload": button.callback_data,
        }
    return {
        "type": "link",
        "text": button.text,
        "url": button.url,
    }


def render_keyboard(keyboard: Keyboard | None) -> dict | None:
    if keyboard is None or keyboard.is_empty:
        return None
    return {
        "type": "inline_keyboard",
        "payload": {
            "buttons": [
                [render_button(button) for button in row]
                for row in keyboard.rows
            ]
        },
    }

