"""Behaviour tests for channels.telegram.request_gate (fakes, no network)."""

from __future__ import annotations

import asyncio
import time
import unittest

from channels.telegram.request_gate import RateLimited, RequestGate, RequestGateDeps


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Msg:
    def __init__(self):
        self.answers = []

    async def answer(self, text, **kw):
        self.answers.append(text)


class _Log:
    def __getattr__(self, _n):
        return lambda *a, **k: None


class _Store:
    def __init__(self, balance=1000):
        self._bal = balance
        self.charged = []
        self.refunded = []

    def balance(self, uid):
        return self._bal

    def charge(self, uid, amt):
        self.charged.append(amt)
        self._bal -= amt

    def refund(self, uid, amt):
        self.refunded.append(amt)
        self._bal += amt


def _gate(*, user_busy=None, user_last_request=None, store=None):
    from collections import defaultdict
    deps = RequestGateDeps(
        user_busy=user_busy if user_busy is not None else {},
        user_last_request=user_last_request if user_last_request is not None else defaultdict(float),
        busy_max_sec=300,
        cooldown_sec=10,
        max_auto_wait_sec=10,
        log=_Log(),
        credit_store=store or _Store(),
        zero_balance_kb=lambda: None,
        menu_button=lambda t, c: None,
    )
    return RequestGate(deps)


class RequestGateTests(unittest.TestCase):
    def test_user_slot_rejects_when_busy(self):
        busy = {7: time.time()}
        gate = _gate(user_busy=busy)
        msg = _Msg()

        async def go():
            async with gate.user_slot(7, msg):
                pass

        with self.assertRaises(RateLimited):
            run(go())
        self.assertTrue(msg.answers)

    def test_user_slot_runs_and_releases(self):
        busy = {}
        gate = _gate(user_busy=busy)
        msg = _Msg()
        ran = []

        async def go():
            async with gate.user_slot(7, msg):
                ran.append(7 in busy)  # slot held during body

        run(go())
        self.assertEqual(ran, [True])
        self.assertNotIn(7, busy)  # released in finally

    def test_credit_gate_charges_on_success(self):
        store = _Store(balance=1000)
        gate = _gate(store=store)
        msg = _Msg()

        async def go():
            async with gate.credit_gate(7, "gen", msg, 1) as charge:
                charge.ok = True

        run(go())
        self.assertTrue(store.charged)
        self.assertEqual(store.refunded, [])

    def test_credit_gate_refunds_on_failure(self):
        store = _Store(balance=1000)
        gate = _gate(store=store)
        msg = _Msg()

        async def go():
            async with gate.credit_gate(7, "gen", msg, 1) as charge:
                charge.ok = False

        run(go())
        self.assertTrue(store.charged)
        self.assertEqual(store.charged, store.refunded)


if __name__ == "__main__":
    unittest.main()
