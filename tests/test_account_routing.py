"""Unit tests for accounts.routing.VideoAccountRouter.

These exercise the video account routing/health logic in isolation — no
flow_bot import — proving the core moved out of the monolith cleanly.
"""

from __future__ import annotations

import unittest

from accounts.routing import VideoAccountRouter


class _FakePool:
    def __init__(self, *, usable=True, picked="acc-1"):
        self.usable = usable
        self.picked = picked
        self.pick_calls = []

    def is_reference_usable(self, account_id):
        return self.usable

    def pick_for_video(self, user_id, *, model_family, health_scores):
        self.pick_calls.append((user_id, model_family, health_scores))
        return self.picked


class _FakeKeeper:
    def __init__(self, cache):
        self._gcredits_cache = cache


class _FakeMetrics:
    def __init__(self, scores):
        self.scores = scores
        self.calls = []

    def report_video_account_scores(self, *, model_family, credit_hints, min_credits):
        self.calls.append((model_family, credit_hints, min_credits))
        return self.scores


def _router(*, pool=None, keepers=None, metrics=None, meta=None):
    return VideoAccountRouter(
        account_pool=pool or _FakePool(),
        keepers=keepers if keepers is not None else {},
        metrics=metrics or _FakeMetrics({}),
        video_model_meta=meta or (lambda mid: {"family": "omni"}),
    )


class VideoAccountRouterTests(unittest.TestCase):
    def test_family_for_model_reads_meta(self):
        r = _router(meta=lambda mid: {"family": "veo"})
        self.assertEqual(r.family_for_model("veo-lite"), "veo")

    def test_family_for_model_defaults_unknown(self):
        r = _router(meta=lambda mid: None)
        self.assertEqual(r.family_for_model("mystery"), "unknown")

    def test_cached_gcredits_hints_copies_dict_caches_only(self):
        keepers = {
            "a": _FakeKeeper({"g": 5}),
            "b": _FakeKeeper(None),
            "c": _FakeKeeper("not-a-dict"),
        }
        r = _router(keepers=keepers)
        hints = r.cached_gcredits_hints()
        self.assertEqual(hints, {"a": {"g": 5}})
        # returned dict is a copy, not the live cache
        hints["a"]["g"] = 99
        self.assertEqual(keepers["a"]._gcredits_cache, {"g": 5})

    def test_scores_for_model_passes_family_and_hints(self):
        m = _FakeMetrics({"acc-1": {}})
        r = _router(metrics=m, keepers={"acc-1": _FakeKeeper({"g": 1})},
                    meta=lambda mid: {"family": "omni"})
        r.scores_for_model("omni-flash-4s", 10)
        self.assertEqual(m.calls, [("omni", {"acc-1": {"g": 1}}, 10)])

    def test_health_reason_missing_account(self):
        self.assertEqual(_router().account_health_reason(None, "m"), "missing_account")

    def test_health_reason_account_unavailable(self):
        r = _router(pool=_FakePool(usable=False))
        self.assertEqual(r.account_health_reason("acc-1", "m"), "account_unavailable")

    def test_health_reason_proxy_failed(self):
        m = _FakeMetrics({"acc-1": {"proxy_failed": True}})
        r = _router(pool=_FakePool(usable=True), metrics=m)
        self.assertEqual(r.account_health_reason("acc-1", "m"), "proxy_check_failed")

    def test_health_reason_unusual_403(self):
        m = _FakeMetrics({"acc-1": {"recent_unusual_403": True}})
        r = _router(pool=_FakePool(usable=True), metrics=m)
        self.assertEqual(
            r.account_health_reason("acc-1", "m"),
            "recent_public_error_unusual_activity",
        )

    def test_health_reason_healthy_returns_none(self):
        m = _FakeMetrics({"acc-1": {}})
        r = _router(pool=_FakePool(usable=True), metrics=m)
        self.assertIsNone(r.account_health_reason("acc-1", "m"))

    def test_account_for_video_forwards_family_and_scores(self):
        pool = _FakePool(picked="acc-7")
        m = _FakeMetrics({"acc-7": {}})
        r = _router(pool=pool, metrics=m, meta=lambda mid: {"family": "omni"})
        got = r.account_for_video(42, model_id="omni-flash-4s", min_credits=3)
        self.assertEqual(got, "acc-7")
        self.assertEqual(pool.pick_calls[0][0], 42)
        self.assertEqual(pool.pick_calls[0][1], "omni")

    def test_module_does_not_import_flow_bot(self):
        import inspect
        import accounts.routing as mod
        self.assertNotIn("flow_bot", inspect.getsource(mod))


if __name__ == "__main__":
    unittest.main()
