"""Behaviour tests for channels.telegram.monitors (fakes, no network/sleep loops)."""

from __future__ import annotations

import asyncio
import unittest

from channels.telegram.monitors import Monitors, MonitorsDeps


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Log:
    def __getattr__(self, _n):
        return lambda *a, **k: None


class _Pool:
    def __init__(self, ids, video_capable):
        self._ids = ids
        self._vc = video_capable

    def account_ids(self):
        return self._ids

    def is_video_capable(self, a):
        return a in self._vc


class _Metrics:
    def __init__(self):
        self.events = []

    def log_event(self, name, **kw):
        self.events.append((name, kw))


def _deps(*, scores, ids, video_capable, alerts, metrics=None):
    async def _alert(text):
        alerts.append(text)

    async def _send(*a, **k):
        pass

    return MonitorsDeps(
        metrics=metrics or _Metrics(),
        credit_store=None,
        log=_Log(),
        send_message=_send,
        send_owner_alert=_alert,
        account_pool=_Pool(ids, video_capable),
        video_scores_for_model=lambda *a, **k: scores,
        quick_ideas=("cat",),
        digest_hour=12, digest_batch=10, digest_delay_s=0, digest_interval_h=6,
        video_pool_min_score=10, video_pool_check_interval_s=300,
    )


class _StopLoop(Exception):
    pass


def _run_health_once(monitors: Monitors):
    """Drive the real video_pool_health_loop for one iteration by patching
    asyncio.sleep to no-op the startup delay and break after the first cycle."""
    import channels.telegram.monitors as mod
    calls = {"n": 0}
    orig = mod.asyncio.sleep

    async def _fast_sleep(_secs):
        calls["n"] += 1
        if calls["n"] >= 2:  # startup sleep + one interval sleep => stop
            raise _StopLoop
    mod.asyncio.sleep = _fast_sleep
    try:
        try:
            run(monitors.video_pool_health_loop())
        except _StopLoop:
            pass
    finally:
        mod.asyncio.sleep = orig


class MonitorsTests(unittest.TestCase):
    def test_degraded_pool_alerts_once(self):
        alerts = []
        metrics = _Metrics()
        deps = _deps(
            scores={"a": {"score": 1}, "b": {"score": 2}},
            ids=["a", "b"], video_capable={"a", "b"}, alerts=alerts, metrics=metrics,
        )
        m = Monitors(deps)
        _run_health_once(m)
        self.assertEqual(len(alerts), 1)  # degraded alert emitted once
        self.assertTrue(m._pool_degraded_alerted)
        self.assertIn("video_pool_degraded", [e[0] for e in metrics.events])

    def test_healthy_pool_emits_no_alert(self):
        alerts = []
        deps = _deps(
            scores={"a": {"score": 50}}, ids=["a", "b"], video_capable={"a", "b"}, alerts=alerts,
        )
        m = Monitors(deps)
        _run_health_once(m)
        self.assertEqual(alerts, [])
        self.assertFalse(m._pool_degraded_alerted)

    def test_deps_are_frozen(self):
        deps = _deps(scores={}, ids=[], video_capable=set(), alerts=[])
        with self.assertRaises(Exception):
            deps.digest_hour = 9  # frozen dataclass


if __name__ == "__main__":
    unittest.main()
