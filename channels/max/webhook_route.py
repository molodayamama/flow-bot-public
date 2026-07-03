"""MAX webhook HTTP route (production-preferred alternative to polling).

`process_webhook` is the pure, framework-free core: verify the shared secret,
parse the JSON body into a platform-neutral event, dispatch it, and return an
``(status, text)`` pair — fully testable without aiohttp. `register_max_webhook`
is a thin aiohttp adapter that mounts it on an existing web app.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Mapping

from channels.max.webhook import parse_update, verify_webhook_secret

log = logging.getLogger(__name__)

Dispatch = Callable[[Any], Awaitable[None]]
Parser = Callable[[Mapping[str, Any]], Any]


async def process_webhook(
    *,
    headers: Mapping[str, str],
    body: str | bytes,
    secret: str,
    dispatch: Dispatch,
    parse: Parser = parse_update,
) -> tuple[int, str]:
    """Validate + parse + dispatch one webhook delivery; return (status, text).

    403 on a bad/missing secret, 400 on unparseable JSON, 200 otherwise (a body
    that parses to no known event is a benign 200 with no dispatch). A dispatch
    error is logged and still returns 200 so MAX does not hammer retries.
    """
    if not verify_webhook_secret(headers, secret):
        return 403, "forbidden"
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
) -> None:
    """Mount the MAX webhook POST route on an aiohttp application."""
    from aiohttp import web

    async def handler(request: "web.Request") -> "web.Response":
        raw = await request.read()
        status, text = await process_webhook(
            headers=request.headers, body=raw, secret=secret, dispatch=dispatch
        )
        return web.Response(status=status, text=text)

    app.router.add_post(path, handler)
