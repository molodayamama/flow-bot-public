"""Behaviour tests for channels.telegram.generation_flow (fakes, no network)."""

from __future__ import annotations

import asyncio
import contextlib
import unittest
from unittest.mock import ANY

from aiogram import types

from flow_core import ImageRef
from channels.telegram.generation_flow import GenerationFlow, GenerationFlowDeps


def _ref(account_id="a", media_id="11111111-1111-1111-1111-111111111111"):
    return ImageRef(
        user_id=42, project_id="proj", source={"mediaId": media_id},
        prompt="orig", aspect_ratio="square", account_id=account_id,
    )


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

    async def answer_document(self, *a, **k):
        self.documents = getattr(self, "documents", [])
        self.documents.append((a, k))


class _Callback:
    def __init__(self, user_id=42):
        self.message = _Message(user_id=user_id)


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

    def assigned_to(self, user_id):
        return None


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
    reupload_ref=None,
    referral_calls=None,
):
    pool = pool or _Pool()
    sent = sent if sent is not None else []
    workspace = workspace if workspace is not None else {}
    metrics = metrics or _Metrics()
    seller_calls = seller_calls if seller_calls is not None else []
    log_jobs = log_jobs if log_jobs is not None else []
    referral_calls = referral_calls if referral_calls is not None else []
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

    async def _reupload(ref, uid, *, current_account_id=None):
        return reupload_ref

    async def _post_hooks(message, uid):
        referral_calls.append(uid)

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
        edit_capture_file="no_such_edit_capture.json",
        reupload_ref_for_edit_failover=_reupload,
        is_rate_limit_error=lambda res: bool(res.get("rate_limited")),
        post_generation_referral_hooks=_post_hooks,
        flow_account_id="acc-default",
        mix_baskets={},
        account_for=lambda uid: "a",
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

    def test_edit_short_instruction_skips(self):
        deps, pool, sent = _deps(clients={}, accounts=[])
        flow = GenerationFlow(deps)
        msg = _Message()
        ok = run(flow.edit_and_send(msg, _ref(), "x"))
        self.assertFalse(ok)
        self.assertEqual(sent, [])
        self.assertTrue(msg.answers)

    def test_edit_success_stores_last_and_fires_referral(self):
        workspace = {}
        referral_calls = []
        deps, pool, sent = _deps(
            clients={"a": _Client(PAIRS_RESULT)}, accounts=[],
            workspace=workspace, referral_calls=referral_calls,
        )
        flow = GenerationFlow(deps)
        msg = _Message(user_id=42)
        ok = run(flow.edit_and_send(msg, _ref(), "add a hat"))
        self.assertTrue(ok)
        self.assertEqual(pool.successes, ["a"])
        self.assertEqual(len(sent), 1)
        self.assertEqual(workspace[42]["last"]["kind"], "edit")
        self.assertEqual(referral_calls, [42])

    def test_edit_rate_limit_fails_over(self):
        clients = {
            "a": _Client({"error": "cooldown", "rate_limited": True}),
            "b": _Client(PAIRS_RESULT),
        }
        deps, pool, sent = _deps(
            clients=clients, accounts=[], reupload_ref=_ref(account_id="b"),
        )
        flow = GenerationFlow(deps)
        msg = _Message()
        ok = run(flow.do_edit_and_send(
            msg, _ref(account_id="a"), "add a hat",
            [{"name": "x"}], 42,
        ))
        self.assertTrue(ok)
        # marked once in failover branch, once on final delivery
        self.assertEqual(pool.successes, ["b", "b"])
        self.assertEqual(len(sent), 1)

    def test_run_i2i_success_delivers_and_fires_referral(self):
        referral_calls = []
        deps, pool, sent = _deps(
            clients={"a": _Client(PAIRS_RESULT)}, accounts=[], referral_calls=referral_calls,
        )
        flow = GenerationFlow(deps)
        msg = _Message()
        ok = run(flow.run_i2i(msg, _ref(), "vary it", num_images=2, emoji="🎲",
                              fail_text="fail", action="revary"))
        self.assertTrue(ok)
        self.assertEqual(len(sent), 1)
        self.assertEqual(referral_calls, [42])

    def test_run_i2i_rate_limit_marks_account(self):
        marked = []
        deps, pool, sent = _deps(
            clients={"a": _Client({"error": "cooldown", "rate_limited": True})}, accounts=[],
        )
        # capture failure marking
        object.__setattr__(deps, "mark_image_account_failure", lambda acc, res: marked.append(acc))
        flow = GenerationFlow(deps)
        msg = _Message()
        ok = run(flow.do_run_i2i(msg, _ref(account_id="a"), "vary", [{"name": "x"}],
                                 num_images=1, emoji="🎲", fail_text="fail"))
        self.assertFalse(ok)
        self.assertEqual(marked, ["a"])

    def test_real_upscale_delivers_document_and_logs(self):
        class _UpClient:
            async def upsample_image(self, media_id, project_id, progress_cb=None):
                return {"image_bytes": b"\x89PNGxxxx"}

        deps, pool, sent = _deps(clients={"a": _UpClient()}, accounts=[])
        flow = GenerationFlow(deps)
        msg = _Message()
        ok = run(flow.do_real_upscale(msg, _ref(account_id="a"), "mediaid12345678"))
        self.assertTrue(ok)
        self.assertTrue(msg.status.deleted)

    def test_real_upscale_no_media_id_short_circuits(self):
        deps, pool, sent = _deps(clients={}, accounts=[])
        flow = GenerationFlow(deps)
        msg = _Message()
        ref = ImageRef(user_id=42, project_id="proj", source={}, account_id="a")
        run(flow.real_upscale_and_send(msg, ref))
        self.assertTrue(msg.answers)  # upscale_unavailable

    def test_repeat_last_no_history_answers_no_previous(self):
        deps, pool, sent = _deps(clients={}, accounts=[], workspace={})
        flow = GenerationFlow(deps)
        cb = _Callback()
        run(flow.repeat_last(cb, 42))
        self.assertTrue(cb.message.answers)  # "Нет предыдущей генерации."

    def test_repeat_last_edit_replays_same_instruction(self):
        workspace = {42: {"last": {
            "kind": "edit", "ref": _ref(), "instruction": "make blue",
            "aspect": "square", "imodel": "gem-pix-2", "price_action": "edit",
        }}}
        deps, pool, sent = _deps(
            clients={"a": _Client(PAIRS_RESULT)}, accounts=[], workspace=workspace,
        )
        flow = GenerationFlow(deps)
        cb = _Callback()
        run(flow.repeat_last(cb, 42))
        self.assertEqual(len(sent), 1)  # edit re-delivered

    def test_repeat_last_generate_replays_same_prompt(self):
        workspace = {42: {"last": {
            "prompt": "cat", "count": 1, "aspect": "square", "imodel": "gem-pix-2",
        }}}
        deps, pool, sent = _deps(
            clients={"a": _Client(PAIRS_RESULT)}, accounts=["a", "a"], workspace=workspace,
        )
        flow = GenerationFlow(deps)
        cb = _Callback()
        run(flow.repeat_last(cb, 42))
        self.assertEqual(len(sent), 1)  # generate re-delivered


if __name__ == "__main__":
    unittest.main()
