"""Unit tests for the MAX polling loop — no network, no real sleep."""

from __future__ import annotations

import asyncio
import unittest

from channels.max import polling


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeClient:
    """Returns queued /updates responses; records the markers requested."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.markers_seen = []

    async def get_updates(self, marker=None, limit=100):
        self.markers_seen.append(marker)
        if self._responses:
            return self._responses.pop(0)
        return {"updates": [], "marker": marker}


class _FakeHandler:
    def __init__(self):
        self.events = []

    async def handle(self, event):
        self.events.append(event)


async def _nosleep(_):
    return None


def _msg(uid, text, marker_hint=None):
    return {
        "update_type": "message_created",
        "message": {"sender": {"user_id": uid}, "chat_id": uid, "text": text},
    }


class PollOnceTests(unittest.TestCase):
    def test_dispatches_parsed_events_and_returns_marker(self):
        client = _FakeClient([{"updates": [_msg("1", "hi"), _msg("2", "yo")], "marker": 55}])
        handler = _FakeHandler()
        parsed = []

        def fake_parser(raw):
            parsed.append(raw)
            return raw  # dispatch the raw dict as the "event"

        nxt = run(polling.poll_once(client, handler, marker=None, parser=fake_parser))
        self.assertEqual(nxt, 55)
        self.assertEqual(len(handler.events), 2)
        self.assertEqual(len(parsed), 2)

    def test_none_events_are_skipped(self):
        client = _FakeClient([{"updates": [{"noise": 1}], "marker": 7}])
        handler = _FakeHandler()
        nxt = run(polling.poll_once(client, handler, marker=None, parser=lambda r: None))
        self.assertEqual(nxt, 7)
        self.assertEqual(handler.events, [])

    def test_parser_error_on_one_update_does_not_stall(self):
        client = _FakeClient([{"updates": [_msg("1", "a"), _msg("2", "b")], "marker": 9}])
        handler = _FakeHandler()
        calls = {"n": 0}

        def boom_then_ok(raw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("bad update")
            return raw

        nxt = run(polling.poll_once(client, handler, marker=None, parser=boom_then_ok))
        self.assertEqual(nxt, 9)
        self.assertEqual(len(handler.events), 1)  # first skipped, second dispatched

    def test_marker_preserved_when_response_has_none(self):
        client = _FakeClient([{"updates": []}])
        handler = _FakeHandler()
        nxt = run(polling.poll_once(client, handler, marker=42, parser=lambda r: r))
        self.assertEqual(nxt, 42)


class RunPollingTests(unittest.TestCase):
    def test_stops_via_predicate_and_advances_marker(self):
        client = _FakeClient([
            {"updates": [_msg("1", "a")], "marker": 10},
            {"updates": [_msg("2", "b")], "marker": 20},
        ])
        handler = _FakeHandler()

        # stop once both batches have been fetched (marker-count based, robust to
        # how many times the loop probes should_stop per iteration)
        def should_stop():
            return len(client.markers_seen) >= 2

        final = run(polling.run_polling(
            client, handler, should_stop=should_stop, sleep=_nosleep, parser=lambda r: r,
        ))
        self.assertEqual(final, 20)
        self.assertEqual(len(handler.events), 2)
        # second batch requested with the marker from the first
        self.assertIn(10, client.markers_seen)

    def test_get_updates_error_backs_off_and_continues(self):
        class _FlakyClient:
            def __init__(self):
                self.n = 0

            async def get_updates(self, marker=None, limit=100):
                self.n += 1
                if self.n == 1:
                    raise RuntimeError("network blip")
                return {"updates": [_msg("1", "ok")], "marker": 3}

        client = _FlakyClient()
        handler = _FakeHandler()

        # stop after the client has been hit twice (1 error + 1 success)
        def should_stop():
            return client.n >= 2

        final = run(polling.run_polling(
            client, handler, should_stop=should_stop, sleep=_nosleep, parser=lambda r: r,
        ))
        self.assertEqual(final, 3)
        self.assertEqual(len(handler.events), 1)


if __name__ == "__main__":
    unittest.main()
