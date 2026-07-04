"""Behaviour tests for channels.telegram.video_flow result-button callbacks."""

from __future__ import annotations

import asyncio
import unittest

from channels.telegram.video_flow import VideoFlow, VideoFlowDeps


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Msg:
    def __init__(self):
        self.answers = []
        self.docs = []

    async def answer(self, text, **kw):
        self.answers.append(text)
        return _Status()

    async def answer_document(self, *a, **k):
        self.docs.append((a, k))


class _Status:
    def __init__(self):
        self.texts = []
        self.deleted = False

    async def edit_text(self, text, **kw):
        self.texts.append(text)

    async def delete(self):
        self.deleted = True


class _Callback:
    def __init__(self):
        self.message = _Msg()
        self.answered = []

    async def answer(self, text=None, **kw):
        self.answered.append((text, kw))


class _Registry:
    def __init__(self, ref=None):
        self._ref = ref

    def get(self, token):
        return self._ref


class _Log:
    def __getattr__(self, _n):
        def _f(*a, **k):
            pass
        return _f


def _null(*a, **k):
    return None


def _deps(*, registry, delivery=(b"vid", False), workspace=None):
    workspace = workspace if workspace is not None else {}

    async def _delivery(ref, **kw):
        return delivery

    fields = dict(
        workspace=lambda uid: workspace.setdefault(uid, {}),
        user_slot=_null, rate_limited_error=RuntimeError, vid_default_count=1,
        username=lambda m: "u", video_reference_sources=_null,
        video_reference_project_id=_null, ensure_reference_on_healthy_account=_null,
        account_for_video=_null, ensure_user_project=_null, credit_store=None,
        metrics=None, account_pool=None, client_for_acc=_null,
        mark_video_account_failure=_null, video_registry=registry,
        video_result_kb=_null, referral_link=lambda uid: "", bot_username=lambda: "",
        video_delivery_bytes=_delivery, post_generation_referral_hooks=_null,
        vid_clear=_null, zero_balance_kb=_null, menu_button=_null, log=_Log(),
        aspect_to_vfmt=lambda a: "16:9", vid_clear_reference_inputs=_null,
    )
    return VideoFlowDeps(**fields), workspace


class _Ref:
    def __init__(self, user_id=7, media_id="abcd1234efgh"):
        self.user_id = user_id
        self.media_id = media_id


class VideoFlowCallbackTests(unittest.TestCase):
    def test_download_owner_mismatch_expired(self):
        deps, ws = _deps(registry=_Registry(_Ref(user_id=99)))
        flow = VideoFlow(deps)
        cb = _Callback()
        run(flow.download(cb, user_id=7, token="t"))
        self.assertTrue(cb.answered)
        self.assertEqual(cb.message.docs, [])

    def test_download_success_sends_document(self):
        deps, ws = _deps(registry=_Registry(_Ref(user_id=7)))
        flow = VideoFlow(deps)
        cb = _Callback()
        run(flow.download(cb, user_id=7, token="t"))
        self.assertEqual(len(cb.message.docs), 1)

    def test_download_no_bytes_reports_error(self):
        deps, ws = _deps(registry=_Registry(_Ref(user_id=7)), delivery=(None, False))
        flow = VideoFlow(deps)
        cb = _Callback()
        run(flow.download(cb, user_id=7, token="t"))
        self.assertEqual(cb.message.docs, [])

    def test_repeat_last_without_history(self):
        deps, ws = _deps(registry=_Registry(None), workspace={7: {}})
        flow = VideoFlow(deps)
        cb = _Callback()
        run(flow.repeat_last(cb, user_id=7))
        self.assertTrue(cb.message.answers)


if __name__ == "__main__":
    unittest.main()
