"""Telegram deep-link confirmation for first-party website login."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from channels.telegram.routers.start import (
    StartDeps,
    create_handler,
    create_web_login_callback,
)


class _Metrics:
    def __init__(self, claimed: bool = True) -> None:
        self.claimed = claimed
        self.claim_calls = []

    def user_exists(self, _user_id):
        return True

    def upsert_user(self, *args, **kwargs):
        return None

    def log_event(self, *args, **kwargs):
        return None

    def claim_web_login_challenge(self, *args):
        self.claim_calls.append(args)
        return self.claimed


def _deps(metrics: _Metrics) -> StartDeps:
    return StartDeps(
        metrics=metrics,
        username=lambda message: message.from_user.username,
        reset_image_flow=lambda _uid: None,
        vid_clear=lambda _uid: None,
        credit_store=SimpleNamespace(balance=lambda _uid: 30, add=lambda *_: None),
        send_owner_alert=AsyncMock(),
        referral_param_prefix="ref_",
        referral_referred_bonus=0,
        parse_channel_seed=lambda _payload: None,
        reply_menu_kb=lambda _uid: None,
        config=SimpleNamespace(IS_SELLER=False),
        workspace=lambda _uid: {},
        mp_jobs_kb=lambda _platform: None,
        mp_stamp_message=lambda *_: None,
        price_gen=lambda _count: 10,
        onboarding_step1_kb=lambda: None,
        show_main_menu=AsyncMock(),
    )


def _start_message() -> SimpleNamespace:
    return SimpleNamespace(
        text="/start web_abcdefghijklmnopqrstuvwxyz123456",
        from_user=SimpleNamespace(
            id=42, first_name="Alice", last_name="Example", username="alice", is_bot=False
        ),
        answer=AsyncMock(),
    )


def _callback(data: str) -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(
            id=42, first_name="Alice", last_name="Example", username="alice", is_bot=False
        ),
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock()),
    )


class TelegramWebLoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_deep_link_shows_confirm_prompt_without_claim(self) -> None:
        """Диплинк не должен молча привязывать аккаунт (login CSRF): сначала
        явное подтверждение кнопкой."""
        metrics = _Metrics(True)
        message = _start_message()
        await create_handler(_deps(metrics))(message)

        self.assertEqual(metrics.claim_calls, [])
        text = message.answer.await_args.args[0]
        self.assertIn("подтверд", text.lower())
        kb = message.answer.await_args.kwargs["reply_markup"]
        self.assertEqual(kb.inline_keyboard[0][0].text, "✅ Подтвердить вход")
        self.assertEqual(
            kb.inline_keyboard[0][0].callback_data,
            "wl:ok:abcdefghijklmnopqrstuvwxyz123456",
        )

    async def test_confirm_button_claims_challenge(self) -> None:
        metrics = _Metrics(True)
        call = _callback("wl:ok:abcdefghijklmnopqrstuvwxyz123456")
        await create_web_login_callback(_deps(metrics))(call)

        self.assertEqual(len(metrics.claim_calls), 1)
        self.assertEqual(metrics.claim_calls[0][0], "abcdefghijklmnopqrstuvwxyz123456")
        self.assertEqual(metrics.claim_calls[0][1:3], ("telegram", 42))
        edited = call.message.edit_text.await_args.args[0]
        self.assertIn("подтвержд", edited.lower())

    async def test_confirm_button_with_used_link_reports_invalid(self) -> None:
        metrics = _Metrics(False)
        call = _callback("wl:ok:abcdefghijklmnopqrstuvwxyz123456")
        await create_web_login_callback(_deps(metrics))(call)

        edited = call.message.edit_text.await_args.args[0]
        self.assertIn("недействительна", edited)

    async def test_cancel_button_does_not_claim(self) -> None:
        metrics = _Metrics(True)
        call = _callback("wl:no")
        await create_web_login_callback(_deps(metrics))(call)

        self.assertEqual(metrics.claim_calls, [])
        edited = call.message.edit_text.await_args.args[0]
        self.assertIn("отмен", edited.lower())


if __name__ == "__main__":
    unittest.main()
