"""Behaviour tests for channels.telegram.ideas_screens (fakes, no network)."""

from __future__ import annotations

import asyncio
import unittest

from aiogram import types

from channels.telegram.ideas_screens import IdeasScreens, IdeasScreensDeps


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Msg:
    def __init__(self, caption="", user_id=7):
        self.caption = caption
        self.answers = []
        self.from_user = type("U", (), {"id": user_id})()
        self.photo = [type("P", (), {"file_id": "fid"})()]

    async def answer(self, text, **kw):
        self.answers.append(text)


class _Metrics:
    def __init__(self):
        self.events = []

    def log_event(self, name, **kw):
        self.events.append((name, kw))


def _deps(*, workspace, metrics=None, calls=None):
    calls = calls if calls is not None else {}

    async def _edit_or_answer(*a, **k):
        calls["edit_or_answer"] = True

    async def _prep_video(*a, **k):
        calls["prep_video"] = k

    async def _prep_edit(*a, **k):
        calls["prep_edit"] = k

    async def _show_new_video(*a, **k):
        calls["new_video"] = True

    async def _show_wizard(*a, **k):
        calls["wizard"] = True

    async def _show_vpi(*a, **k):
        calls["vpi"] = k

    return IdeasScreensDeps(
        workspace=lambda uid: workspace.setdefault(uid, {}),
        menu_button=lambda t, c: types.InlineKeyboardButton(text=t, callback_data=c),
        edit_or_answer=_edit_or_answer,
        metrics=metrics or _Metrics(),
        prepare_photo_video_from_file_id=_prep_video,
        prepare_photo_edit_from_file_id=_prep_edit,
        vid_clear=lambda uid: None,
        clear_image_flow_keys=lambda st: None,
        vid_default_fmt="land",
        show_new_video_wizard=_show_new_video,
        show_wizard=_show_wizard,
        show_video_prompt_input=_show_vpi,
        templates_picker_kb=lambda: None,
        guided_step_kb=lambda step: None,
        guided_to_vid_style={"cinematic": "cine"},
    ), calls


class IdeasScreensTests(unittest.TestCase):
    def test_show_ideas_root_sets_mode(self):
        ws = {}
        deps, calls = _deps(workspace=ws)
        run(IdeasScreens(deps).show_ideas_root(_Msg(), user_id=7, edit=False))
        self.assertEqual(ws[7]["ideas_mode"], "root")

    def test_template_photo_saves_file_id(self):
        ws = {7: {}}
        deps, calls = _deps(workspace=ws)
        run(IdeasScreens(deps).template_photo_received(_Msg(), user_id=7))
        self.assertEqual(ws[7]["ideas_photo_file_id"], "fid")

    def test_render_template_no_template_goes_to_wizard(self):
        # No tp_tpl and no photo → composes empty prompt, opens image wizard.
        ws = {7: {}}
        deps, calls = _deps(workspace=ws)
        run(IdeasScreens(deps).render_template_step(_Msg(), user_id=7))
        self.assertTrue(calls.get("wizard"))
        self.assertIn("pending_prompt", ws[7])

    def test_render_guided_video_with_photo_uses_photo_video_pipeline(self):
        ws = {7: {
            "gp_step": 999,
            "gp_answers": {"what": "video", "format": "story", "style": "cinematic"},
            "ideas_photo_file_id": "photo-file",
        }}
        deps, calls = _deps(workspace=ws)
        run(IdeasScreens(deps).render_guided_step(_Msg(), user_id=7))
        self.assertEqual(calls["prep_video"]["file_id"], "photo-file")
        self.assertEqual(calls["prep_video"]["vfmt"], "port")
        self.assertEqual(calls["prep_video"]["vstyle"], "cine")
        self.assertNotIn("gp_step", ws[7])
        self.assertNotIn("ideas_photo_file_id", ws[7])

    def test_render_guided_image_with_photo_uses_photo_edit_pipeline(self):
        metrics = _Metrics()
        ws = {7: {
            "gp_step": 999,
            "gp_answers": {"what": "image", "format": "story"},
            "ideas_photo_file_id": "photo-file",
        }}
        deps, calls = _deps(workspace=ws, metrics=metrics)
        run(IdeasScreens(deps).render_guided_step(_Msg(), user_id=7))
        self.assertEqual(calls["prep_edit"]["file_id"], "photo-file")
        self.assertEqual(calls["prep_edit"]["aspect_fmt"], "port")
        self.assertEqual(metrics.events[0][0], "guided_completed")
        self.assertNotIn("gp_step", ws[7])
        self.assertNotIn("ideas_photo_file_id", ws[7])


if __name__ == "__main__":
    unittest.main()
