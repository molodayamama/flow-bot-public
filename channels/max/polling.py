"""MAX long-polling loop (runtime glue).

Pulls updates from the MAX Bot API, parses each into a platform-neutral event
and dispatches it to a handler. Everything it needs is injected (client, handler,
parser, sleep), so the loop is exercised fully by fakes with no network.

Long polling is documented by MAX as non-production; a webhook subscription is
preferred for production. This loop is enough to run the bot and to smoke-test
against the live API under operator approval.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Mapping, Protocol

from channels.max.webhook import parse_update

log = logging.getLogger(__name__)


class _UpdatesClient(Protocol):
    async def get_updates(self, marker: int | None = None, limit: int = 100) -> Any:
        ...


class _Handler(Protocol):
    async def handle(self, event: Any) -> None:
        ...


def _updates_and_marker(response: Any) -> tuple[list[Mapping[str, Any]], int | None]:
    """Extract the updates list and next marker from a /updates response."""
    if not isinstance(response, Mapping):
        return [], None
    updates = response.get("updates")
    updates = updates if isinstance(updates, list) else []
    marker = response.get("marker")
    marker = int(marker) if isinstance(marker, int) else None
    return updates, marker


async def poll_once(
    client: _UpdatesClient,
    handler: _Handler,
    *,
    marker: int | None,
    limit: int = 100,
    parser: Callable[[Mapping[str, Any]], Any] = parse_update,
) -> int | None:
    """Fetch one batch, dispatch parseable events, return the next marker.

    A parser/handler error on one update is logged and skipped so a single bad
    update never stalls the loop.
    """
    response = await client.get_updates(marker=marker, limit=limit)
    updates, next_marker = _updates_and_marker(response)
    for raw in updates:
        if not isinstance(raw, Mapping):
            continue
        try:
            event = parser(raw)
        except Exception:
            log.exception("MAX update parse failed")
            continue
        if event is None:
            continue
        try:
            await handler.handle(event)
        except Exception:
            log.exception("MAX update handling failed")
    return next_marker if next_marker is not None else marker


async def run_polling(
    client: _UpdatesClient,
    handler: _Handler,
    *,
    marker: int | None = None,
    limit: int = 100,
    idle_delay: float = 1.0,
    should_stop: Callable[[], bool] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    parser: Callable[[Mapping[str, Any]], Any] = parse_update,
) -> int | None:
    """Poll until ``should_stop()`` is true; return the final marker.

    ``should_stop`` defaults to running forever (production). Tests pass a
    predicate that flips after N iterations. ``sleep`` is injected so tests do
    not actually wait between batches.
    """
    stop = should_stop or (lambda: False)
    while not stop():
        try:
            marker = await poll_once(
                client, handler, marker=marker, limit=limit, parser=parser
            )
        except Exception:
            log.exception("MAX get_updates failed; backing off")
            await sleep(idle_delay)
            continue
        if stop():
            break
        await sleep(idle_delay)
    return marker
