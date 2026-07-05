"""Behaviour tests for channels.telegram.robokassa_topup (fakes, no network)."""

from __future__ import annotations

import asyncio
import unittest

from aiogram import types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from channels.telegram.robokassa_topup import RobokassaTopup, RobokassaTopupDeps


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Callback:
    def __init__(self):
        self.message = _Msg()
        self.answers = []

    async def answer(self, text=None, **kw):
        self.answers.append((text, kw))


class _Msg:
    def __init__(self):
        self.sent = []

    async def answer(self, text, **kw):
        self.sent.append((text, kw))


class _Metrics:
    def __init__(self):
        self.blocked = []

    def mark_user_blocked(self, uid):
        self.blocked.append(uid)


class _Log:
    def __getattr__(self, _n):
        return lambda *a, **k: None


def _deps(*, pack=None, configured=True, admin_ids=frozenset(), send_raises=None):
    async def _send(uid, text, **kw):
        if send_raises is not None:
            raise send_raises
        return ("sent", uid, text, kw)

    return RobokassaTopupDeps(
        credit_pack=lambda pid: pack,
        admin_ids=admin_ids,
        is_configured=lambda: configured,
        new_inv_id=lambda: 42,
        payment_url=lambda uid, pid, inv: f"http://pay/{pid}",
        pack_amount=lambda pid: 990,
        rub_display=lambda amt: f"{amt}",
        menu_button=lambda t, c: types.InlineKeyboardButton(text=t, callback_data=c),
        bot_send_message=_send,
        workspace=lambda uid: {},
        main_menu_kb=lambda **kw: None,
        metrics=_Metrics(),
        log=_Log(),
    )


class RobokassaTopupTests(unittest.TestCase):
    def test_start_topup_unknown_pack_rejected(self):
        deps = _deps(pack=None)
        rt = RobokassaTopup(deps)
        cb = _Callback()
        run(rt.start_topup(cb, 7, "missing"))
        self.assertTrue(cb.answers)
        self.assertEqual(cb.message.sent, [])

    def test_start_topup_not_configured_rejected(self):
        deps = _deps(pack={"credits": 100}, configured=False)
        rt = RobokassaTopup(deps)
        cb = _Callback()
        run(rt.start_topup(cb, 7, "p1"))
        self.assertTrue(cb.answers)
        self.assertEqual(cb.message.sent, [])

    def test_start_topup_success_shows_pay_button(self):
        deps = _deps(pack={"credits": 100})
        rt = RobokassaTopup(deps)
        cb = _Callback()
        run(rt.start_topup(cb, 7, "p1"))
        # callback answered without alert
        self.assertEqual(cb.answers[0][0], None)
        # invoice message sent with the pay-url keyboard
        self.assertTrue(cb.message.sent)
        self.assertIn("reply_markup", cb.message.sent[0][1])

    def test_start_topup_test_pack_blocked_for_non_admin(self):
        deps = _deps(pack={"credits": 100, "test": True}, admin_ids=frozenset({999}))
        rt = RobokassaTopup(deps)
        cb = _Callback()
        run(rt.start_topup(cb, 7, "trial"))
        self.assertTrue(cb.answers)  # rejected with "Пакет не найден"

    def test_notify_success_forbidden_marks_blocked(self):
        exc = TelegramForbiddenError(method="send_message", message="bot was blocked by the user")
        deps = _deps(send_raises=exc)
        rt = RobokassaTopup(deps)
        run(rt.notify_success(7, 100, 200))
        self.assertEqual(deps.metrics.blocked, [7])

    def test_notify_success_chat_not_found_swallows(self):
        exc = TelegramBadRequest(method="send_message", message="chat not found")
        deps = _deps(send_raises=exc)
        rt = RobokassaTopup(deps)
        # should not raise
        run(rt.notify_success(7, 100, 200))
        self.assertEqual(deps.metrics.blocked, [])

    def test_notify_success_sends_message(self):
        sent = []
        deps = RobokassaTopupDeps(
            credit_pack=lambda pid: None,
            admin_ids=frozenset(),
            is_configured=lambda: False,
            new_inv_id=lambda: 0,
            payment_url=lambda *a: "",
            pack_amount=lambda pid: 0,
            rub_display=lambda amt: str(amt),
            menu_button=lambda t, c: None,
            bot_send_message=lambda uid, text, **kw: sent.append((uid, text, kw)) or asyncio.sleep(0),
            workspace=lambda uid: {},
            main_menu_kb=lambda **kw: "kb",
            metrics=_Metrics(),
            log=_Log(),
        )
        rt = RobokassaTopup(deps)
        run(rt.notify_success(7, 100, 200))
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], 7)


if __name__ == "__main__":
    unittest.main()
