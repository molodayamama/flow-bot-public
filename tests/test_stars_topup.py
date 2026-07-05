"""Behaviour tests for channels.telegram.stars_topup (fakes, no network)."""

from __future__ import annotations

import asyncio
import unittest

from aiogram import types

from channels.telegram.stars_topup import StarsTopup, StarsTopupDeps


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Callback:
    def __init__(self, chat_id=7):
        self.message = _Msg(chat_id=chat_id)
        self.answers = []

    async def answer(self, text=None, **kw):
        self.answers.append((text, kw))


class _Msg:
    def __init__(self, chat_id=7):
        self.chat = type("C", (), {"id": chat_id})()
        self.sent = []

    async def answer(self, text, **kw):
        self.sent.append((text, kw))


class _Log:
    def __getattr__(self, _n):
        return lambda *a, **k: None


def _deps(*, pack=None, admin_ids=frozenset(), invoices=None, send_invoice_raises=None):
    invoices = invoices if invoices is not None else []

    async def _send_invoice(*a, **kw):
        if send_invoice_raises is not None:
            raise send_invoice_raises
        invoices.append((a, kw))

    return StarsTopupDeps(
        credit_pack=lambda pid: pack,
        admin_ids=admin_ids,
        bot_send_invoice=_send_invoice,
        log=_Log(),
    ), invoices


class StarsTopupTests(unittest.TestCase):
    def test_unknown_pack_rejected(self):
        deps, invoices = _deps(pack=None)
        st = StarsTopup(deps)
        cb = _Callback()
        run(st.start_topup(cb, 7, "missing"))
        self.assertTrue(cb.answers)  # "Пакет не найден"
        self.assertEqual(invoices, [])

    def test_test_pack_blocked_for_non_admin(self):
        deps, invoices = _deps(pack={"credits": 100, "test": True}, admin_ids=frozenset({999}))
        st = StarsTopup(deps)
        cb = _Callback()
        run(st.start_topup(cb, 7, "trial"))
        self.assertTrue(cb.answers)
        self.assertEqual(invoices, [])

    def test_success_sends_invoice(self):
        deps, invoices = _deps(pack={"credits": 100, "stars": 75})
        st = StarsTopup(deps)
        cb = _Callback()
        run(st.start_topup(cb, 7, "p1"))
        self.assertEqual(cb.answers, [(None, {})])  # callback answered without alert
        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices[0][1]["currency"], "XTR")
        self.assertEqual(invoices[0][1]["payload"], "credits:p1:7")

    def test_send_invoice_failure_shows_friendly_message(self):
        deps, invoices = _deps(pack={"credits": 100, "stars": 75}, send_invoice_raises=RuntimeError("down"))
        st = StarsTopup(deps)
        cb = _Callback()
        run(st.start_topup(cb, 7, "p1"))
        self.assertEqual(invoices, [])
        self.assertTrue(cb.message.sent)  # "Оплата временно недоступна"


if __name__ == "__main__":
    unittest.main()
