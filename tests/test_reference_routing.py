"""Behaviour tests for channels.telegram.reference_routing (fakes, no network)."""

from __future__ import annotations

import asyncio
import unittest

from flow_core import ImageRef
from channels.telegram.reference_routing import ReferenceRouting, ReferenceRoutingDeps


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Log:
    def __getattr__(self, _n):
        return lambda *a, **k: None


class _Keeper:
    def __init__(self, source):
        self._source = source
        self.calls = []

    async def upload_image(self, data, *, filename, project_id=None):
        self.calls.append((filename, project_id))
        return self._source


class _Pool:
    def __init__(self, *, usable=True):
        self.is_reference_usable = lambda acc_id: usable


def _deps(*, keeper_source=None, account_image="b", account_video="b", usable=True):
    async def _download(file_id):
        return b"rawbytes"

    async def _ensure(uid, *, account_id=None):
        return "proj-target"

    return ReferenceRoutingDeps(
        log=_Log(),
        bot_download=_download,
        account_pool=_Pool(usable=usable),
        account_for_image=lambda uid, **k: account_image,
        account_for_video=lambda uid, **k: account_video,
        ensure_user_project=_ensure,
        keeper_for_acc=lambda acc: _Keeper(keeper_source if keeper_source is not None else {"mediaId": "m1"}),
        video_account_health_reason=lambda *a, **k: None,  # healthy
    )


class ReferenceRoutingTests(unittest.TestCase):
    def test_edit_failover_no_current_account_returns_none(self):
        rr = ReferenceRouting(_deps())
        out = run(rr.reupload_ref_for_edit_failover(
            ImageRef(user_id=7, project_id="p", source={}, account_id="a"),
            user_id=7, current_account_id=None,
        ))
        self.assertIsNone(out)

    def test_edit_failover_same_account_returns_none(self):
        deps = _deps(account_image="a")
        rr = ReferenceRouting(deps)
        out = run(rr.reupload_ref_for_edit_failover(
            ImageRef(user_id=7, project_id="p", source={"_tg_file_id": "fid"}, account_id="a"),
            user_id=7, current_account_id="a",
        ))
        self.assertIsNone(out)  # exclude={current} filtered it out

    def test_edit_failover_reuploads_to_new_account(self):
        rr = ReferenceRouting(_deps(keeper_source={"mediaId": "m1"}))
        ref = ImageRef(user_id=7, project_id="p", source={"_tg_file_id": "fid"}, account_id="a")
        out = run(rr.reupload_ref_for_edit_failover(ref, user_id=7, current_account_id="a"))
        self.assertIsNotNone(out)
        self.assertEqual(out.account_id, "b")
        self.assertEqual(out.source["_tg_file_id"], "fid")

    def test_reupload_reference_source_no_file_id_returns_none(self):
        rr = ReferenceRouting(_deps())
        out = run(rr.reupload_reference_source({}, user_id=7, acc_id="b", project_id="p"))
        self.assertIsNone(out)

    def test_reupload_reference_source_success(self):
        rr = ReferenceRouting(_deps(keeper_source={"mediaId": "m2"}))
        out = run(rr.reupload_reference_source(
            {"_tg_file_id": "fid"}, user_id=7, acc_id="b", project_id="p"))
        self.assertIsNotNone(out)
        self.assertEqual(out["_account_id"], "b")
        self.assertEqual(out["_tg_file_id"], "fid")

    def test_ensure_no_sources_picks_video_account(self):
        rr = ReferenceRouting(_deps(account_video="c"))
        st = {}  # no ving_photos / vfrm_start
        out = run(rr.ensure_reference_on_healthy_account(st, "ingredients", user_id=7, model_id="m", min_credits=1))
        self.assertEqual(out, "c")

    def test_ensure_bound_healthy_returns_bound(self):
        # bound account healthy → reuse existing upload, no re-upload
        rr = ReferenceRouting(_deps())
        st = {"ving_photos": [{"_tg_file_id": "fid", "_account_id": "a"}]}
        out = run(rr.ensure_reference_on_healthy_account(st, "ingredients", user_id=7, model_id="m", min_credits=1))
        self.assertEqual(out, "a")


if __name__ == "__main__":
    unittest.main()
