"""Behaviour tests for channels.telegram.photo_intake (fakes, no network)."""

from __future__ import annotations

import asyncio
import unittest

from flow_core import ImageRef
from channels.telegram.photo_intake import PhotoIntake, PhotoIntakeDeps


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Status:
    def __init__(self):
        self.texts = []
        self.deleted = False

    async def edit_text(self, text, **kw):
        self.texts.append(text)

    async def delete(self):
        self.deleted = True


class _Msg:
    def __init__(self, caption="", user_id=7):
        self.caption = caption
        self.answers = []
        self.from_user = type("U", (), {"id": user_id})()
        self.photo = [type("P", (), {"file_id": "fid"})()]

    async def answer(self, text, **kw):
        self.answers.append((text, kw))
        return _Status()


class _Keeper:
    def __init__(self, source):
        self._source = source

    async def upload_image(self, data, *, filename, project_id):
        return self._source


class _Registry:
    def __init__(self):
        self.added = []

    def add(self, ref):
        self.added.append(ref)
        return "tok"


class _Log:
    def __getattr__(self, _n):
        return lambda *a, **k: None


def _deps(*, source, account="a", pending=None, calls=None):
    calls = calls if calls is not None else {}

    async def _download(file_id):
        return b"rawbytes"

    async def _ensure(uid, *, account_id=None):
        return "proj"

    async def _show_edit_confirm(*a, **k):
        calls["edit_confirm"] = True

    async def _show_new_video_wizard(*a, **k):
        calls["new_video"] = True

    async def _show_frames(*a, **k):
        calls["frames"] = True

    async def _show_ingredients(*a, **k):
        calls["ingredients"] = True

    async def _offer_album(*a, **k):
        calls["album_offer"] = True

    pending_map = pending if pending is not None else {}

    def _store_refs(user_id, refs):
        pending_map[user_id] = "tok"

    return PhotoIntakeDeps(
        bot_download=_download,
        log=_Log(),
        account_for_video=lambda uid, **k: account,
        account_for_image=lambda uid, **k: account,
        ensure_user_project=_ensure,
        keeper_for_acc=lambda acc: _Keeper(source),
        workspace=lambda uid: {},
        image_registry=_Registry(),
        pending_edits=pending_map,
        store_pending_edit_refs=_store_refs,
        fmt_to_aspect=lambda f: "square",
        show_edit_confirm=_show_edit_confirm,
        edit_settings_kb=lambda *a, **k: None,
        vid_clear=lambda uid: None,
        clear_image_flow_keys=lambda st: None,
        show_new_video_wizard=_show_new_video_wizard,
        show_video_frames=_show_frames,
        show_video_ingredients=_show_ingredients,
        offer_photo_album_route_choice=_offer_album,
        nwiz_model=lambda st: "omni-flash-4s",
        default_fmt="land",
        default_image_model="gem-pix-2",
        vid_default_fmt="land",
    ), calls


class PhotoIntakeTests(unittest.TestCase):
    def test_upload_image_ref_success(self):
        deps, _ = _deps(source={"mediaId": "m1"})
        pi = PhotoIntake(deps)
        msg = _Msg()
        status = _Status()
        ref = run(pi.upload_image_ref_from_file_id(
            msg, user_id=7, status_msg=status, file_id="fid", prompt="p", aspect_ratio="square"))
        self.assertIsInstance(ref, ImageRef)
        self.assertEqual(ref.account_id, "a")
        self.assertEqual(ref.source["_tg_file_id"], "fid")

    def test_upload_fails_when_no_media_id(self):
        deps, _ = _deps(source={})
        pi = PhotoIntake(deps)
        status = _Status()
        ref = run(pi.upload_image_ref_from_file_id(
            _Msg(), user_id=7, status_msg=status, file_id="fid", prompt="p", aspect_ratio="square"))
        self.assertIsNone(ref)
        self.assertTrue(status.texts)  # upload_failed shown

    def test_upload_fails_when_no_account(self):
        deps, _ = _deps(source={"mediaId": "m1"}, account=None)
        pi = PhotoIntake(deps)
        status = _Status()
        ref = run(pi.upload_image_ref_from_file_id(
            _Msg(), user_id=7, status_msg=status, file_id="fid", prompt="p", aspect_ratio="square"))
        self.assertIsNone(ref)

    def test_prepare_photo_edit_with_caption_confirms(self):
        pending = {}
        deps, calls = _deps(source={"mediaId": "m1"}, pending=pending)
        pi = PhotoIntake(deps)
        ok = run(pi.prepare_photo_edit_from_file_id(
            _Msg(caption="make blue"), user_id=7, file_id="fid", caption="make blue"))
        self.assertTrue(ok)
        self.assertEqual(pending[7], "tok")
        self.assertTrue(calls.get("edit_confirm"))

    def test_prepare_photo_video_opens_wizard(self):
        deps, calls = _deps(source={"mediaId": "m1"})
        pi = PhotoIntake(deps)
        ok = run(pi.prepare_photo_video_from_file_id(
            _Msg(), user_id=7, file_id="fid", caption="dance"))
        self.assertTrue(ok)
        self.assertTrue(calls.get("new_video"))


if __name__ == "__main__":
    unittest.main()
