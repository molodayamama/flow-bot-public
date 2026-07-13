"""MAX webhook HTTP route (production-preferred alternative to polling).

`process_webhook` is the pure, framework-free core: verify the shared secret,
parse the JSON body into a platform-neutral event, dispatch it, and return an
``(status, text)`` pair — fully testable without aiohttp. `register_max_webhook`
is a thin aiohttp adapter that mounts it on an existing web app.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Mapping

from channels.max.webhook import parse_update, verify_webhook_secret

log = logging.getLogger(__name__)
MAX_WEBHOOK_BODY_BYTES = 1024 * 1024

Dispatch = Callable[[Any], Awaitable[None]]
Parser = Callable[[Mapping[str, Any]], Any]


async def process_webhook(
    *,
    headers: Mapping[str, str],
    body: str | bytes | Mapping[str, Any],
    secret: str,
    dispatch: Dispatch,
    parse: Parser = parse_update,
    inbox: Any | None = None,
    max_body_bytes: int = MAX_WEBHOOK_BODY_BYTES,
) -> tuple[int, str]:
    """Validate + parse + dispatch one webhook delivery; return (status, text).

    403 on a bad/missing secret, 413 on an oversized raw body, 400 on
    unparseable JSON, 200 otherwise. With an inbox configured, a valid event is
    durably queued before the 200 response and dispatched by the worker.
    """
    if not verify_webhook_secret(headers, secret):
        return 403, "forbidden"
    if isinstance(body, bytes) and len(body) > max_body_bytes:
        return 413, "payload too large"
    if isinstance(body, str) and len(body.encode("utf-8")) > max_body_bytes:
        return 413, "payload too large"
    try:
        payload = body if isinstance(body, Mapping) else json.loads(body)
    except (ValueError, TypeError):
        return 400, "bad json"
    if not isinstance(payload, Mapping):
        return 400, "bad payload"
    try:
        event = parse(payload)
    except Exception:
        log.exception("MAX webhook parse failed")
        return 200, "ok"
    if event is not None:
        if inbox is not None:
            _event_id, inserted = inbox.enqueue(payload)
            return 200, "queued" if inserted else "duplicate"
        try:
            await dispatch(event)
        except Exception:
            log.exception("MAX webhook dispatch failed")
    return 200, "ok"


def register_max_webhook(
    app: Any,
    *,
    dispatch: Dispatch,
    secret: str,
    path: str = "/max/webhook",
    inbox: Any | None = None,
) -> None:
    """Mount the MAX webhook POST route on an aiohttp application."""
    from aiohttp import web

    async def handler(request: "web.Request") -> "web.Response":
        if request.content_length is not None and request.content_length > MAX_WEBHOOK_BODY_BYTES:
            return web.Response(status=413, text="payload too large")
        raw = await request.read()
        status, text = await process_webhook(
            headers=request.headers,
            body=raw,
            secret=secret,
            dispatch=dispatch,
            inbox=inbox,
        )
        return web.Response(status=status, text=text)

    app.router.add_post(path, handler)


def register_max_subscription_lifecycle(
    app: Any,
    *,
    client: Any,
    webhook_url: str,
    secret: str,
    update_types: tuple[str, ...] = (
        "message_created",
        "message_callback",
        "bot_started",
    ),
    inbox: Any | None = None,
    dispatch: Dispatch | None = None,
    worker_count: int = 4,
) -> None:
    """Subscribe on aiohttp startup and expose fail-closed readiness state."""
    app["max_webhook_ready"] = False

    async def subscribe(_app: Any) -> None:
        result = await client.subscribe_webhook(
            url=webhook_url,
            secret=secret,
            update_types=update_types,
        )
        if not isinstance(result, Mapping) or result.get("success") is not True:
            raise RuntimeError("MAX webhook subscription was not accepted")
        _app["max_webhook_ready"] = True
        if inbox is not None and dispatch is not None:
            from channels.max.inbox import run_inbox_maintenance, run_inbox_worker

            stop = asyncio.Event()
            _app["max_inbox_stop"] = stop
            workers = tuple(
                asyncio.create_task(
                    run_inbox_worker(inbox, dispatch, stop=stop),
                    name=f"max-webhook-inbox-{index + 1}",
                )
                for index in range(max(1, int(worker_count)))
            )
            maintenance = asyncio.create_task(
                run_inbox_maintenance(inbox, stop=stop),
                name="max-webhook-inbox-maintenance",
            )
            _app["max_inbox_tasks"] = (*workers, maintenance)

    async def cleanup(_app: Any) -> None:
        stop = _app.get("max_inbox_stop")
        tasks = _app.get("max_inbox_tasks") or ()
        if stop is not None:
            stop.set()
        if tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*tasks), timeout=10.0)
            except asyncio.TimeoutError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    app.on_startup.append(subscribe)
    app.on_cleanup.append(cleanup)
