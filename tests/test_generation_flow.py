"""Behaviour tests for channels.telegram.generation_flow (fakes, no network)."""

from __future__ import annotations

import asyncio
import contextlib
import unittest
from unittest.mock import ANY

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
    def __init__(self, user_id=7):
        self.status = _StatusMsg()
        self.answers = []
        self.from_user = type("User", (), {"id": user_id})()

    async def answer(self, text):
        self.answers.append(text)
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


class _RateLimited(Exception):
    pass


class _NotEnoughCredits(Exception):
    pass


class _Charge:
    def __init__(self):
        self.ok = False


class _Metrics:
    def __init__(self):
        self.events = []
        self.history = []

    def log_event(self, name, **kwargs):
        self.events.append((name, kwargs))

    def save_prompt_history(self, user_id, prompt):
        self.history.append((user_id, prompt))


def _deps(
    *,
    clients,
    accounts,
    pool=None,
    sent=None,
    workspace=None,
    metrics=None,
    is_seller=False,
    seller_calls=None,
    log_jobs=None,
    raise_rate_limited=False,
    raise_not_enough=False,
):
    pool = pool or _Pool()
    sent = sent if sent is not None else []
    workspace = workspace if workspace is not None else {}
    metrics = metrics or _Metrics()
    seller_calls = seller_calls if seller_calls is not None else []
    log_jobs = log_jobs if log_jobs is not None else []
    acc_iter = iter(accounts)

    async def _ensure(uid, *, account_id=None):
        return "proj"

    async def _send_result_pairs(message, pairs, **kw):
        sent.append(pairs)

    async def _after_result(message, uid, *, streak_note=None):
        pass

    async def _seller_generate_and_send(message, prompt, **kw):
        seller_calls.append((prompt, kw))

    @contextlib.asynccontextmanager
    async def _user_slot(uid, message):
        if raise_rate_limited:
            raise _RateLimited()
        yield

    @contextlib.asynccontextmanager
    async def _credit_gate(*args, **kwargs):
        if raise_not_enough:
            raise _NotEnoughCredits()
        yield _Charge()

    return GenerationFlowDeps(
        workspace=lambda uid: workspace.setdefault(uid, {}),
        is_seller=lambda: is_seller,
        seller_generate_and_send=_seller_generate_and_send,
        user_slot=_user_slot,
        credit_gate=_credit_gate,
        rate_limited_error=_RateLimited,
        not_enough_credits_error=_NotEnoughCredits,
        image_request_event={"gen": "image_requested"},
        username=lambda m: "u",
        account_for_image=lambda uid, exclude=None: next(acc_iter, None),
        ensure_user_project=_ensure,
        metrics=metrics,
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
        log_image_job=lambda *a, **k: log_jobs.append((a, k)),
    ), pool, sent


PAIRS_RESULT = {
    "responses": [
        {"generatedImage": {"fifeUrl": "http://x/1.png", "mediaId": "m1"}}
    ]
}


class GenerationFlowTests(unittest.TestCase):
    def test_generate_and_send_stores_retry_and_logs_success(self):
        workspace = {}
        metrics = _Metrics()
        log_jobs = []
        deps, pool, sent = _deps(
            clients={"a": _Client(PAIRS_RESULT)}, accounts=["a", "a"],
            workspace=workspace, metrics=metrics, log_jobs=log_jobs,
        )
        flow = GenerationFlow(deps)
        msg = _Message(user_id=42)
        run(flow.generate_and_send(msg, "cat prompt", 1, "square"))
        self.assertEqual(workspace[42]["last"]["prompt"], "cat prompt")
        self.assertEqual(workspace[42]["img_retry"]["action"], "gen")
        self.assertEqual(pool.successes, ["a"])
        self.assertEqual(len(sent), 1)
        self.assertIn(("image_requested", ANY), metrics.events)
        self.assertIn(("image_success", ANY), metrics.events)
        self.assertIn((42, "cat prompt"), metrics.history)
        self.assertTrue(log_jobs)

    def test_generate_and_send_short_prompt_skips_generation(self):
        deps, pool, sent = _deps(clients={}, accounts=[])
        flow = GenerationFlow(deps)
        msg = _Message()
        run(flow.generate_and_send(msg, "x"))
        self.assertEqual(pool.successes, [])
        self.assertEqual(sent, [])
        self.assertTrue(msg.answers)

    def test_generate_and_send_seller_delegates(self):
        seller_calls = []
        deps, pool, sent = _deps(
            clients={}, accounts=[], is_seller=True, seller_calls=seller_calls,
        )
        flow = GenerationFlow(deps)
        msg = _Message()
        run(flow.generate_and_send(msg, "seller prompt", num_images=2))
        self.assertEqual(seller_calls[0][0], "seller prompt")
        self.assertEqual(seller_calls[0][1]["num_images"], 2)
        self.assertEqual(sent, [])

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
