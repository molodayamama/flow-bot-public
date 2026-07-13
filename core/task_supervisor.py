"""Ownership and deterministic shutdown for long-running asyncio tasks."""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable
from typing import Any


class BackgroundTaskSupervisor:
    """Own background tasks, consume failures, and cancel them on shutdown."""

    def __init__(self, logger: logging.Logger) -> None:
        self._log = logger
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closing = False

    @property
    def active_count(self) -> int:
        return len(self._tasks)

    def start(self, awaitable: Awaitable[Any], *, name: str) -> asyncio.Task[Any]:
        if self._closing:
            close = getattr(awaitable, "close", None)
            if inspect.iscoroutine(awaitable) and callable(close):
                close()
            raise RuntimeError("background task supervisor is closing")
        task = asyncio.create_task(awaitable, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        return task

    def _on_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            self._log.error(
                "background task failed name=%s type=%s",
                task.get_name(),
                error.__class__.__name__,
                exc_info=(error.__class__, error, error.__traceback__),
            )

    async def close(self) -> None:
        self._closing = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
