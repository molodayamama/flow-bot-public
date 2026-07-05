"""Behaviour tests for channels.telegram.animate_photo (fakes, no network)."""

from __future__ import annotations

import asyncio
import unittest

from aiogram import types

from channels.telegram.animate_photo import AnimatePhotoContext, AnimatePhotoDeps, make_animate_photo_context_factory


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
        self.answers.append((text, kw))
        return _Sent()


class _Sent:
    message_id = 99


class _Metrics:
    def __init__(self):
        self.events = []

    def log_event(self, name, **kw):
        self.events.append((name, kw))


def _deps(*, workspace=None, pending=None):
    workspace = workspace if workspace is not None else {}
    pending = pending if pending is not None else {}

    async def _vid_edit(*a, **k):
        pass

    async def _upload(*a, **k):
        return {"mediaId": "m1"}

    async def _show_wizard(*a, **k):
        return "wizard"

    async def _gen(*a, **k):
        return "generated"

    return AnimatePhotoDeps(
        workspace=lambda uid: workspace.setdefault(uid, {}),
        pending_edits=pending,
        vid_clear=lambda uid: None,
        clear_image_flow_keys=lambda st: None,
        nwiz_model=lambda st: "omni-flash-4s",
        metrics=_Metrics(),
        vid_edit=_vid_edit,
        upload_photo_source_from_file_id=_upload,
        show_new_video_wizard=_show_wizard,
        video_generate_and_send=_gen,
    ), workspace, pending


class AnimatePhotoTests(unittest.TestCase):
    def test_state_returns_workspace(self):
        deps, ws, _ = _deps()
        ctx = AnimatePhotoContext(_Msg(), 7, deps)
        self.assertIs(ctx.state, ws.setdefault(7, {}))

    def test_clear_pending_edit_pops(self):
        deps, _, pending = _deps(pending={7: "tok"})
        ctx = AnimatePhotoContext(_Msg(), 7, deps)
        ctx.clear_pending_edit()
        self.assertNotIn(7, pending)

    def test_log_event_records(self):
        deps, _, _ = _deps()
        ctx = AnimatePhotoContext(_Msg(), 7, deps)
        ctx.log_event("animate_photo_started", source="test")
        self.assertEqual(deps.metrics.events[0][0], "animate_photo_started")

    def test_show_photo_input_records_msg_id(self):
        deps, ws, _ = _deps()
        ctx = AnimatePhotoContext(_Msg(), 7, deps)
        run(ctx.show_photo_input(edit=False, price=10))
        self.assertEqual(ws[7]["vmsg_id"], 99)

    def test_upload_photo_source_returns_dict(self):
        deps, _, _ = _deps()
        ctx = AnimatePhotoContext(_Msg(), 7, deps)
        out = run(ctx.upload_photo_source("fid"))
        self.assertEqual(out["mediaId"], "m1")

    def test_generate_video_delegates(self):
        called = []
        deps = AnimatePhotoDeps(
            workspace=lambda uid: {},
            pending_edits={},
            vid_clear=lambda uid: None,
            clear_image_flow_keys=lambda st: None,
            nwiz_model=lambda st: "omni-flash-4s",
            metrics=_Metrics(),
            vid_edit=lambda *a, **k: None,
            upload_photo_source_from_file_id=lambda *a, **k: None,
            show_new_video_wizard=lambda *a, **k: None,
            video_generate_and_send=lambda *a, **k: called.append((a, k)) or asyncio.sleep(0),
        )
        ctx = AnimatePhotoContext(_Msg(), 7, deps)
        run(ctx.generate_video("dance"))
        self.assertEqual(len(called), 1)  # delegated to video_generate_and_send

    def test_factory_binds_deps(self):
        deps, _, _ = _deps()
        factory = make_animate_photo_context_factory(deps)
        ctx = factory(_Msg(), 7)
        self.assertIsInstance(ctx, AnimatePhotoContext)
        self.assertIs(ctx._d, deps)


if __name__ == "__main__":
    unittest.main()
