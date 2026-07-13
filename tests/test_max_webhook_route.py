"""Unit tests for the MAX webhook route core — no aiohttp, no network."""

from __future__ import annotations

import asyncio
import json
import unittest

from channels.max import webhook_route
from channels.max.webhook import MAX_SECRET_HEADER


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Sink:
    def __init__(self):
        self.events = []

    async def dispatch(self, event):
        self.events.append(event)


SECRET = "s3cr3t"
GOOD_HEADERS = {MAX_SECRET_HEADER: SECRET}


def _msg_body():
    return json.dumps({
        "update_type": "message_created",
        "message": {
            "sender": {"user_id": "1"},
            "recipient": {"chat_id": "1"},
            "body": {"mid": "m1", "text": "hi"},
        },
    })


class ProcessWebhookTests(unittest.TestCase):
    def test_bad_secret_is_403_no_dispatch(self):
        sink = _Sink()
        status, _ = run(webhook_route.process_webhook(
            headers={MAX_SECRET_HEADER: "wrong"}, body=_msg_body(),
            secret=SECRET, dispatch=sink.dispatch,
        ))
        self.assertEqual(status, 403)
        self.assertEqual(sink.events, [])

    def test_missing_secret_is_403(self):
        sink = _Sink()
        status, _ = run(webhook_route.process_webhook(
            headers={}, body=_msg_body(), secret=SECRET, dispatch=sink.dispatch,
        ))
        self.assertEqual(status, 403)

    def test_bad_json_is_400(self):
        sink = _Sink()
        status, _ = run(webhook_route.process_webhook(
            headers=GOOD_HEADERS, body="{not json", secret=SECRET, dispatch=sink.dispatch,
        ))
        self.assertEqual(status, 400)
        self.assertEqual(sink.events, [])

    def test_oversized_body_is_413(self):
        sink = _Sink()
        status, _ = run(webhook_route.process_webhook(
            headers=GOOD_HEADERS, body="x" * 11, secret=SECRET,
            dispatch=sink.dispatch, max_body_bytes=10,
        ))
        self.assertEqual(status, 413)
        self.assertEqual(sink.events, [])

    def test_valid_message_dispatches_and_200(self):
        sink = _Sink()
        status, text = run(webhook_route.process_webhook(
            headers=GOOD_HEADERS, body=_msg_body(), secret=SECRET, dispatch=sink.dispatch,
        ))
        self.assertEqual(status, 200)
        self.assertEqual(text, "ok")
        self.assertEqual(len(sink.events), 1)

    def test_unknown_payload_is_200_no_dispatch(self):
        sink = _Sink()
        status, _ = run(webhook_route.process_webhook(
            headers=GOOD_HEADERS, body=json.dumps({"noise": 1}),
            secret=SECRET, dispatch=sink.dispatch,
        ))
        self.assertEqual(status, 200)
        self.assertEqual(sink.events, [])

    def test_dispatch_error_still_200(self):
        async def boom(event):
            raise RuntimeError("handler blew up")

        status, _ = run(webhook_route.process_webhook(
            headers=GOOD_HEADERS, body=_msg_body(), secret=SECRET, dispatch=boom,
        ))
        self.assertEqual(status, 200)

    def test_accepts_prevalidated_mapping_body(self):
        sink = _Sink()
        payload = {
            "update_type": "message_created",
            "message": {
                "sender": {"user_id": "1"},
                "recipient": {"chat_id": "1"},
                "body": {"mid": "m1", "text": "hi"},
            },
        }
        status, _ = run(webhook_route.process_webhook(
            headers=GOOD_HEADERS, body=payload, secret=SECRET, dispatch=sink.dispatch,
        ))
        self.assertEqual(status, 200)
        self.assertEqual(len(sink.events), 1)

    def test_inbox_acknowledges_after_enqueue_and_deduplicates(self):
        class _Inbox:
            def __init__(self):
                self.payloads = []

            def enqueue(self, payload):
                inserted = not self.payloads
                self.payloads.append(dict(payload))
                return "event-id", inserted

        sink = _Sink()
        inbox = _Inbox()
        first = run(webhook_route.process_webhook(
            headers=GOOD_HEADERS, body=_msg_body(), secret=SECRET,
            dispatch=sink.dispatch, inbox=inbox,
        ))
        second = run(webhook_route.process_webhook(
            headers=GOOD_HEADERS, body=_msg_body(), secret=SECRET,
            dispatch=sink.dispatch, inbox=inbox,
        ))
        self.assertEqual(first, (200, "queued"))
        self.assertEqual(second, (200, "duplicate"))
        self.assertEqual(sink.events, [])


class RegisterRouteTests(unittest.TestCase):
    def test_registers_post_route(self):
        class _Router:
            def __init__(self):
                self.routes = []

            def add_post(self, path, handler):
                self.routes.append((path, handler))

        class _App:
            def __init__(self):
                self.router = _Router()

        app = _App()
        webhook_route.register_max_webhook(
            app, dispatch=_Sink().dispatch, secret=SECRET, path="/max/webhook"
        )
        self.assertEqual(len(app.router.routes), 1)
        self.assertEqual(app.router.routes[0][0], "/max/webhook")

    def test_health_is_not_ready_without_subscription_or_workers(self):
        class _Inbox:
            def stats(self):
                return {"pending": 2, "processed": 3, "dead": 0}

        status, payload = webhook_route.max_health_snapshot(
            {"max_webhook_ready": False}, _Inbox()
        )
        self.assertEqual(status, 503)
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["inbox"]["pending"], 2)

    def test_health_is_degraded_but_ready_with_dead_letters(self):
        class _Task:
            def done(self):
                return False

        class _Inbox:
            def stats(self):
                return {"pending": 0, "processed": 3, "dead": 1}

        status, payload = webhook_route.max_health_snapshot({
            "max_webhook_ready": True,
            "max_inbox_tasks": (_Task(), _Task()),
        }, _Inbox())
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "degraded")

    def test_registers_health_get_route(self):
        class _Router:
            def __init__(self):
                self.routes = []

            def add_get(self, path, handler):
                self.routes.append((path, handler))

        class _App(dict):
            def __init__(self):
                super().__init__()
                self.router = _Router()

        app = _App()
        webhook_route.register_max_health(app, inbox=object())
        self.assertEqual(app.router.routes[0][0], "/max/health")

    def test_subscription_lifecycle_fails_closed_and_sets_readiness(self):
        class _Client:
            def __init__(self):
                self.calls = []
                self.closed = False

            async def subscribe_webhook(self, **kwargs):
                self.calls.append(kwargs)
                return {"success": True}

            async def close(self):
                self.closed = True

        class _App(dict):
            def __init__(self):
                super().__init__()
                self.on_startup = []
                self.on_cleanup = []

        app = _App()
        client = _Client()
        webhook_route.register_max_subscription_lifecycle(
            app,
            client=client,
            webhook_url="https://bot.example/max/webhook",
            secret=SECRET,
        )
        self.assertFalse(app["max_webhook_ready"])
        run(app.on_startup[0](app))
        self.assertTrue(app["max_webhook_ready"])
        self.assertEqual(client.calls[0]["url"], "https://bot.example/max/webhook")
        self.assertEqual(len(app.on_cleanup), 1)
        run(app.on_cleanup[0](app))
        self.assertFalse(app["max_webhook_ready"])
        self.assertTrue(client.closed)

    def test_subscription_lifecycle_rejects_provider_failure(self):
        class _Client:
            def __init__(self):
                self.closed = False

            async def subscribe_webhook(self, **kwargs):
                return {"success": False}

            async def close(self):
                self.closed = True

        class _App(dict):
            def __init__(self):
                super().__init__()
                self.on_startup = []
                self.on_cleanup = []

        app = _App()
        client = _Client()
        webhook_route.register_max_subscription_lifecycle(
            app,
            client=client,
            webhook_url="https://bot.example/max/webhook",
            secret=SECRET,
        )
        with self.assertRaises(RuntimeError):
            run(app.on_startup[0](app))
        self.assertFalse(app["max_webhook_ready"])
        self.assertTrue(client.closed)


if __name__ == "__main__":
    unittest.main()
