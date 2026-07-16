"""Telegram deep-link confirmation for first-party website login."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from channels.telegram.routers.start import StartDeps, create_handler


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


class TelegramWebLoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_deep_link_claims_challenge_without_showing_code(self) -> None:
        metrics = _Metrics(True)
        message = SimpleNamespace(
            text="/start web_abcdefghijklmnopqrstuvwxyz123456",
            from_user=SimpleNamespace(
                id=42, first_name="Alice", last_name="Example", username="alice", is_bot=False
            ),
            answer=AsyncMock(),
        )
        await create_handler(_deps(metrics))(message)

        self.assertEqual(metrics.claim_calls[0][0], "abcdefghijklmnopqrstuvwxyz123456")
        self.assertEqual(metrics.claim_calls[0][1:3], ("telegram", 42))
        self.assertEqual(len(metrics.claim_calls[0]), 4)
        text = message.answer.await_args.args[0]
        self.assertNotIn("012345", text)
        self.assertIn("подтвержд", text.lower())

    async def test_already_used_deep_link_returns_no_code(self) -> None:
        metrics = _Metrics(False)
        message = SimpleNamespace(
            text="/start web_abcdefghijklmnopqrstuvwxyz123456",
            from_user=SimpleNamespace(
                id=42, first_name="Alice", last_name="", username="alice", is_bot=False
            ),
            answer=AsyncMock(),
        )
        await create_handler(_deps(metrics))(message)
        text = message.answer.await_args.args[0]
        self.assertIn("недействительна", text)
        self.assertNotIn("012345", text)


if __name__ == "__main__":
    unittest.main()
