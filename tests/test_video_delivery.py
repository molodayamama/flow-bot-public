"""Unit tests for product.video_delivery.video_delivery_bytes."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _NullLog:
    def __init__(self):
        self.warnings = []

    def warning(self, *a, **k):
        self.warnings.append(a)


class _Client:
    def __init__(self, full=None, seg=b"seg"):
        self._full = full
        self._seg = seg
        self.seg_calls = 0

    async def fetch_full_extended_video(self, scene_id, project_id):
        return self._full

    async def fetch_video_bytes(self, media_id):
        self.seg_calls += 1
        return self._seg


from product.video_delivery import video_delivery_bytes


def _ref(mode="generate", **kw):
    base = {"mode": mode, "scene_id": "s", "project_id": "p", "account_id": "a", "media_id": "m"}
    base.update(kw)
    return SimpleNamespace(**base)


class DeliveryTests(unittest.TestCase):
    def test_extend_full_stitched_returned(self):
        client = _Client(full=b"FULL")
        log = _NullLog()
        out = run(video_delivery_bytes(_ref("extend"), client_for_acc=lambda _: client, log=log))
        self.assertEqual(out, (b"FULL", True))

    def test_extend_fallback_to_segment_when_no_stitch(self):
        client = _Client(full=None, seg=b"SEG")
        log = _NullLog()
        out = run(video_delivery_bytes(_ref("extend"), client_for_acc=lambda _: client, log=log))
        self.assertEqual(out, (b"SEG", False))
        self.assertTrue(log.warnings)

    def test_non_extend_fetches_segment(self):
        client = _Client(seg=b"X")
        out = run(video_delivery_bytes(_ref("generate"), client_for_acc=lambda _: client, log=_NullLog()))
        self.assertEqual(out, (b"X", False))
        self.assertEqual(client.seg_calls, 1)

    def test_prefetched_bytes_skip_fetch(self):
        client = _Client(seg=b"X")
        out = run(video_delivery_bytes(
            _ref("generate"), client_for_acc=lambda _: client, log=_NullLog(), fetched_bytes=b"PRE",
        ))
        self.assertEqual(out, (b"PRE", False))
        self.assertEqual(client.seg_calls, 0)


if __name__ == "__main__":
    unittest.main()
