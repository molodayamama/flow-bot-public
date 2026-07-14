from __future__ import annotations

import unittest
from unittest.mock import patch

from channels.max.handler import MaxMvpBot
from channels.telegram import keyboards as telegram_keyboards


class _Wallet:
    def internal_id(self, platform, user_id):
        return -1

    def balance(self, platform, user_id):
        return 30

    def charge(self, platform, user_id, amount):
        return True

    def refund(self, platform, user_id, amount):
        return None


class ConsumerMenuParityTests(unittest.TestCase):
    def test_max_main_menu_matches_telegram_consumer_rows_and_callbacks(self) -> None:
        max_bot = MaxMvpBot(platform=object(), service=object(), wallet=_Wallet())
        max_keyboard = max_bot._menu_keyboard("user")
        with patch.object(telegram_keyboards._cfg, "IS_SELLER", False):
            telegram_keyboard = telegram_keyboards.main_menu_kb(credits=30)

        max_rows = [
            [(button.text, button.callback_data) for button in row]
            for row in max_keyboard.rows
        ]
        telegram_rows = [
            [(button.text, button.callback_data) for button in row]
            for row in telegram_keyboard.inline_keyboard
        ]

        self.assertEqual(max_rows, telegram_rows)
        self.assertEqual(
            [callback for row in max_rows for _text, callback in row],
            [
                "m:gen",
                "m:vid",
                "m:animate",
                "m:myphoto",
                "m:ideas",
                "m:balance",
                "m:profile",
                "m:invite",
            ],
        )


if __name__ == "__main__":
    unittest.main()
