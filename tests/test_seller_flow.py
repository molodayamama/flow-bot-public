"""Behaviour tests for channels.telegram.seller_flow (fakes, no network)."""

from __future__ import annotations

import asyncio
import contextlib
import unittest

from aiogram import types

from channels.telegram.seller_flow import SellerFlow, SellerFlowDeps


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _StatusMsg:
    async def edit_text(self, *a, **k):
        pass

    async def delete(self):
        pass


class _Message:
    def __init__(self, user_id=7):
        self.answers = []
        self.videos = []
        self.docs = []
        self.from_user = type("User", (), {"id": user_id})()
        self.photo = [type("P", (), {"file_id": "fid"})()]

    async def answer(self, text, **kw):
        self.answers.append(text)
        return _StatusMsg()

    async def answer_video(self, *a, **k):
        self.videos.append((a, k))

    async def answer_document(self, *a, **k):
        self.docs.append((a, k))


class _Client:
    def __init__(self, result):
        self._result = result
        self.calls = []

    async def generate(self, **kw):
        self.calls.append(kw)
        return self._result


class _Charge:
    def __init__(self):
        self.ok = False


class _Metrics:
    def __init__(self, seen_event=False):
        self.events = []
        self.flow_jobs = []
        self._seen = seen_event

    def log_event(self, name, **kw):
        self.events.append((name, kw))

    def log_flow_job(self, **kw):
        self.flow_jobs.append(kw)

    def has_user_event(self, user_id, name):
        return self._seen


class _CreditStore:
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


class _Log:
    def __getattr__(self, _n):
        def _f(*a, **k):
            pass
        return _f


class _RateLimited(Exception):
    pass


class _NotEnoughCredits(Exception):
    pass


def _btn(text="x", cb="c"):
    return types.InlineKeyboardButton(text=text, callback_data=cb)


def _deps(
    *,
    client,
    metrics=None,
    credit_store=None,
    sent=None,
    referral=None,
    brand_kit=None,
    is_seller=True,
    seen_brandkit=False,
    raise_rate_limited=False,
    raise_not_enough=False,
):
    metrics = metrics or _Metrics(seen_event=seen_brandkit)
    credit_store = credit_store or _CreditStore()
    sent = sent if sent is not None else []
    referral = referral if referral is not None else []

    async def _send_one_image(message, **kw):
        sent.append(kw)

    async def _download(file_id):
        return b"rawbytes"

    @contextlib.asynccontextmanager
    async def _user_slot(uid, message):
        if raise_rate_limited:
            raise _RateLimited()
        yield

    @contextlib.asynccontextmanager
    async def _credit_gate(*a, **k):
        if raise_not_enough:
            raise _NotEnoughCredits()
        yield _Charge()

    async def _post_hooks(message, uid):
        referral.append(uid)

    deps = SellerFlowDeps(
        backend_client=lambda: client,
        send_one_image=_send_one_image,
        download=_download,
        log=_Log(),
        metrics=metrics,
        username=lambda m: "u",
        image_request_event={"gen": "image_requested"},
        user_slot=_user_slot,
        credit_gate=_credit_gate,
        rate_limited_error=_RateLimited,
        not_enough_credits_error=_NotEnoughCredits,
        log_image_job=lambda *a, **k: None,
        post_generation_referral_hooks=_post_hooks,
        credit_store=credit_store,
        zero_balance_kb=lambda: types.InlineKeyboardMarkup(inline_keyboard=[[_btn()]]),
        menu_button=lambda t, c: _btn(t, c),
        mp_back_kb=lambda: types.InlineKeyboardMarkup(inline_keyboard=[[_btn()]]),
        is_seller=lambda: is_seller,
        mp_brand_kit=lambda uid: brand_kit,
    )
    return deps, metrics, credit_store, sent, referral


class SellerFlowTests(unittest.TestCase):
    def test_generate_success_sends_and_nudges_brandkit(self):
        client = _Client({"images": [{"url": "http://x/1.png"}], "account_id": "acc1"})
        deps, metrics, cs, sent, referral = _deps(client=client)
        flow = SellerFlow(deps)
        msg = _Message(user_id=42)
        ok = run(flow.generate_and_send(msg, "product card", num_images=1, aspect_ratio="square", user_id=42))
        self.assertTrue(ok)
        self.assertEqual(len(sent), 1)
        self.assertEqual(referral, [42])
        # first success with no brand kit -> nudge shown once
        self.assertIn("brandkit_nudge_shown", [e[0] for e in metrics.events])

    def test_generate_no_backend_returns_false(self):
        deps, *_ = _deps(client=None)
        flow = SellerFlow(deps)
        msg = _Message()
        ok = run(flow.generate_and_send(msg, "x", num_images=1, aspect_ratio="square", user_id=7))
        self.assertFalse(ok)

    def test_brandkit_nudge_skipped_when_kit_exists(self):
        client = _Client({"images": [{"url": "http://x/1.png"}], "account_id": "acc1"})
        deps, metrics, *_ = _deps(client=client, brand_kit={"tone": "warm"})
        flow = SellerFlow(deps)
        msg = _Message()
        run(flow.generate_and_send(msg, "card", num_images=1, aspect_ratio="square", user_id=7))
        self.assertNotIn("brandkit_nudge_shown", [e[0] for e in metrics.events])

    def test_video_backend_failure_refunds(self):
        client = _Client({"error": "backend down"})
        deps, metrics, cs, *_ = _deps(client=client)
        flow = SellerFlow(deps)
        msg = _Message()
        ok = run(flow.video_generate_and_send(msg, "animate this", image_b64="Zm9v", aspect_ratio="square", user_id=7))
        self.assertFalse(ok)
        # charged then refunded on failure
        self.assertTrue(cs.charged)
        self.assertEqual(cs.charged, cs.refunded)
        self.assertIn("video_failed", [e[0] for e in metrics.events])

    def test_video_success_delivers_and_charges(self):
        client = _Client({"videos": [{"video_b64": "Zm9v"}]})
        deps, metrics, cs, sent, referral = _deps(client=client)
        flow = SellerFlow(deps)
        msg = _Message()
        ok = run(flow.video_generate_and_send(msg, "animate", image_b64="Zm9v", aspect_ratio="square", user_id=7))
        self.assertTrue(ok)
        self.assertEqual(len(msg.videos), 1)
        self.assertTrue(cs.charged)
        self.assertEqual(cs.refunded, [])
        self.assertEqual(referral, [7])

    def test_i2i_from_photo_downloads_and_generates(self):
        client = _Client({"images": [{"url": "http://x/1.png"}], "account_id": "acc1"})
        deps, metrics, cs, sent, referral = _deps(client=client)
        flow = SellerFlow(deps)
        msg = _Message()
        ok = run(flow.i2i_from_photo(msg, "make it pop", num_images=1, aspect_ratio="square", user_id=7, action="gen"))
        self.assertTrue(ok)
        self.assertEqual(client.calls[0]["kind"], "i2i")
        self.assertIsNotNone(client.calls[0]["image_b64"])


if __name__ == "__main__":
    unittest.main()
