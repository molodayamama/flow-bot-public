from __future__ import annotations

import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from channels.telegram.referral_flow import ReferralFlow, ReferralFlowDeps


class _Copy:
    def msg(self, key: str, **kwargs):
        if key == "invite_share_text":
            return "share me"
        if key == "first_referral_cta":
            return f"invite for {kwargs['referred']}"
        if key == "referral_reward_got":
            return f"got {kwargs['bonus']}"
        return key

    def label(self, key: str) -> str:
        return f"label:{key}"


class _Service:
    def __init__(self) -> None:
        self.calls = []

    def apply_payment_rewards(self, *args, **kwargs) -> None:
        self.calls.append(("apply", args, kwargs))

    def clawback(self, *args) -> None:
        self.calls.append(("clawback", args, {}))


class _Metrics:
    def __init__(self, invited: int = 0) -> None:
        self.invited = invited
        self.blocked = []

    def referral_stats(self, user_id: int) -> dict:
        return {"invited": self.invited}

    def mark_user_blocked(self, user_id: int) -> None:
        self.blocked.append(user_id)


class _Bot:
    def __init__(self) -> None:
        self.sent = []

    async def send_message(self, *args, **kwargs) -> None:
        self.sent.append((args, kwargs))


class ReferralFlowTests(unittest.IsolatedAsyncioTestCase):
    def _flow(self, **overrides):
        deps = {
            "bot_username": lambda: "FlowBot",
            "referral_param_prefix": "ref_",
            "referred_bonus": 15,
            "referral_service": _Service(),
            "metrics": _Metrics(),
            "bot": _Bot(),
            "copy": _Copy(),
            "create_task": lambda coro: self._tasks.append(coro),
        }
        deps.update(overrides)
        self._tasks = []
        return ReferralFlow(ReferralFlowDeps(**deps)), deps

    def test_link_uses_bot_username_or_raw_payload(self) -> None:
        flow, _deps = self._flow()
        self.assertEqual(flow.link(42), "https://t.me/FlowBot?start=ref_42")

        flow, _deps = self._flow(bot_username=lambda: "")
        self.assertEqual(flow.link(42), "ref_42")

    def test_invite_button_builds_telegram_share_url(self) -> None:
        flow, _deps = self._flow()

        button = flow.invite_button(42)

        self.assertEqual(button.text, "label:invite_friend")
        split = urlsplit(button.url)
        self.assertEqual(f"{split.scheme}://{split.netloc}{split.path}", "https://t.me/share/url")
        qs = parse_qs(split.query)
        self.assertEqual(qs["url"], ["https://t.me/FlowBot?start=ref_42"])
        self.assertEqual(qs["text"], ["share me"])

    def test_rewards_and_clawback_delegate_to_service(self) -> None:
        service = _Service()
        flow, _deps = self._flow(referral_service=service)

        flow.maybe_apply_rewards(
            7,
            stars_paid=100,
            credits_issued=200,
            pack_id="pro",
            provider_payment_id="chg_1",
        )
        flow.clawback_rewards(7, "chg_1")

        self.assertEqual(service.calls[0][0], "apply")
        self.assertEqual(service.calls[0][1], (7,))
        self.assertEqual(service.calls[0][2]["provider_payment_id"], "chg_1")
        self.assertEqual(service.calls[1], ("clawback", (7, "chg_1"), {}))

    def test_first_cta_text_stops_after_user_has_referrals(self) -> None:
        flow, _deps = self._flow(metrics=_Metrics(invited=0))
        self.assertEqual(flow.first_cta_text(1), "invite for 15")

        flow, _deps = self._flow(metrics=_Metrics(invited=1))
        self.assertIsNone(flow.first_cta_text(1))

    async def test_post_generation_hooks_send_cta_with_invite_button(self) -> None:
        answers = []
        message = SimpleNamespace(answer=lambda *args, **kwargs: _answer(answers, args, kwargs))
        flow, _deps = self._flow()

        await flow.post_generation_hooks(message, 9)

        self.assertEqual(answers[0][0], ("invite for 15",))
        kb = answers[0][1]["reply_markup"]
        self.assertEqual(kb.inline_keyboard[0][0].text, "label:invite_friend")

    async def test_notify_referrer_schedules_best_effort_send(self) -> None:
        bot = _Bot()
        flow, _deps = self._flow(bot=bot)

        flow.notify_referrer(5, 30)
        await self._tasks[0]

        self.assertEqual(bot.sent, [((5, "got 30"), {"parse_mode": "HTML"})])


async def _answer(answers, args, kwargs):
    answers.append((args, kwargs))
