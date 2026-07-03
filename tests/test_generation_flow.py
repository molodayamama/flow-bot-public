"""Behaviour tests for channels.telegram.generation_flow (fakes, no network)."""

from __future__ import annotations

import asyncio
import contextlib
import unittest

from aiogram import types

from channels.telegram.generation_flow import GenerationFlow, GenerationFlowDeps


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _StatusMsg:
    def __init__(self):
        self.texts = []
        self.deleted = False

    async def edit_text(self, text, *, reply_markup=None):
        self.texts.append(text)

    async def delete(self):
        self.deleted = True


class _Message:
    def __init__(self):
        self.status = _StatusMsg()

    async def answer(self, text):
        return self.status


class _Client:
    def __init__(self, result):
        self._result = result

    async def generate_images(self, prompt, **kw):
        return self._result


class _Pool:
    def __init__(self, capacity=True):
        self._capacity = capacity
        self.successes = []
        self.failures = []

    def has_image_capacity(self, acc):
        return self._capacity

    @contextlib.asynccontextmanager
    async def image_slot(self, acc):
        yield

    def mark_failure(self, acc):
        self.failures.append(acc)
        return False

    def mark_success(self, acc):
        self.successes.append(acc)


class _NullLog:
    def __getattr__(self, _n):
        def _f(*a, **k):
            pass
        return _f


def _deps(*, clients, accounts, pool=None, sent=None):
    pool = pool or _Pool()
    sent = sent if sent is not None else []
    acc_iter = iter(accounts)

    async def _ensure(uid, *, account_id=None):
        return "proj"

    async def _send_result_pairs(message, pairs, **kw):
        sent.append(pairs)

    async def _after_result(message, uid, *, streak_note=None):
        pass

    return GenerationFlowDeps(
        username=lambda m: "u",
        account_for_image=lambda uid, exclude=None: next(acc_iter, None),
        ensure_user_project=_ensure,
        metrics=_NullLog(),
        account_pool=pool,
        client_for_acc=lambda acc: clients[acc],
        log=_NullLog(),
        fire_owner_alert=lambda t: None,
        img_retry_kb=lambda: None,
        menu_button=lambda text, callback_data: types.InlineKeyboardButton(
            text=text, callback_data=callback_data
        ),
        mark_image_account_failure=lambda acc, res: None,
        send_result_pairs=_send_result_pairs,
        after_result=_after_result,
        streak_note=lambda uid: None,
    ), pool, sent


PAIRS_RESULT = {
    "responses": [
        {"generatedImage": {"fifeUrl": "http://x/1.png", "mediaId": "m1"}}
    ]
}


class GenerationFlowTests(unittest.TestCase):
    def test_success_delivers_and_marks_success(self):
        deps, pool, sent = _deps(clients={"a": _Client(PAIRS_RESULT)}, accounts=["a"])
        flow = GenerationFlow(deps)
        msg = _Message()
        ok = run(flow.do_generate_and_send(msg, "cat", 1, "square", 7))
        self.assertTrue(ok)
        self.assertEqual(pool.successes, ["a"])
        self.assertEqual(len(sent), 1)
        self.assertTrue(msg.status.deleted)

    def test_empty_pool_returns_false(self):
        deps, pool, sent = _deps(clients={}, accounts=[])
        flow = GenerationFlow(deps)
        msg = _Message()
        ok = run(flow.do_generate_and_send(msg, "cat", 1, "square", 7))
        self.assertFalse(ok)
        self.assertEqual(sent, [])
        self.assertTrue(msg.status.texts)

    def test_account_error_fails_over_to_second_account(self):
        clients = {"a": _Client({"error": "boom"}), "b": _Client(PAIRS_RESULT)}
        deps, pool, sent = _deps(clients=clients, accounts=["a", "b"])
        flow = GenerationFlow(deps)
        msg = _Message()
        ok = run(flow.do_generate_and_send(msg, "cat", 1, "square", 7))
        self.assertTrue(ok)
        self.assertEqual(pool.successes, ["b"])  # second account delivered

    def test_prompt_rejected_no_failover(self):
        clients = {"a": _Client({"error": "bad words", "error_type": "prompt_rejected"}),
                   "b": _Client(PAIRS_RESULT)}
        deps, pool, sent = _deps(clients=clients, accounts=["a", "b"])
        flow = GenerationFlow(deps)
        msg = _Message()
        ok = run(flow.do_generate_and_send(msg, "cat", 1, "square", 7))
        self.assertFalse(ok)          # rejected → stop, no failover
        self.assertEqual(pool.successes, [])
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
