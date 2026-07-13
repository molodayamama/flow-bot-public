"""Offline lifecycle tests for owned background asyncio tasks."""
from __future__ import annotations

import asyncio
import logging
import unittest
from pathlib import Path

from core.task_supervisor import BackgroundTaskSupervisor


class BackgroundTaskSupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_cancels_and_awaits_owned_task(self) -> None:
        cancelled = asyncio.Event()

        async def worker() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        supervisor = BackgroundTaskSupervisor(logging.getLogger(__name__))
        supervisor.start(worker(), name="worker")
        await asyncio.sleep(0)

        await supervisor.close()

        self.assertTrue(cancelled.is_set())
        self.assertEqual(supervisor.active_count, 0)

    async def test_completed_task_is_removed(self) -> None:
        supervisor = BackgroundTaskSupervisor(logging.getLogger(__name__))
        task = supervisor.start(asyncio.sleep(0), name="short")

        await task
        await asyncio.sleep(0)

        self.assertEqual(supervisor.active_count, 0)
        await supervisor.close()

    async def test_failure_is_consumed_and_reported(self) -> None:
        class CapturingLogger:
            def __init__(self) -> None:
                self.calls = []

            def error(self, *args, **kwargs) -> None:
                self.calls.append((args, kwargs))

        async def fail() -> None:
            raise ValueError("private provider detail")

        logger = CapturingLogger()
        supervisor = BackgroundTaskSupervisor(logger)  # type: ignore[arg-type]
        task = supervisor.start(fail(), name="failing")
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

        self.assertEqual(supervisor.active_count, 0)
        self.assertEqual(len(logger.calls), 1)
        self.assertIn("failing", logger.calls[0][0])
        await supervisor.close()

    async def test_start_after_close_rejects_without_coroutine_warning(self) -> None:
        supervisor = BackgroundTaskSupervisor(logging.getLogger(__name__))
        await supervisor.close()

        coroutine = asyncio.sleep(0)
        with self.assertRaises(RuntimeError):
            supervisor.start(coroutine, name="late")


class ProductionTaskOwnershipTests(unittest.TestCase):
    def test_composition_owns_and_closes_long_running_tasks(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "flow_bot.py").read_text(encoding="utf-8")

        self.assertIn("_background_tasks = BackgroundTaskSupervisor(log)", source)
        self.assertIn('name="telegram-digest"', source)
        self.assertIn('name="video-pool-health"', source)
        self.assertIn("await _background_tasks.close()", source)
        self.assertIn("start_background=_background_tasks.start", source)

    def test_bootstraps_route_background_work_through_injected_owner(self) -> None:
        root = Path(__file__).resolve().parents[1]
        max_source = (
            root / "channels" / "telegram" / "max_bootstrap.py"
        ).read_text(encoding="utf-8")
        web_source = (
            root / "channels" / "telegram" / "web_server.py"
        ).read_text(encoding="utf-8")

        self.assertIn('self._d.start_background(run, name="max-polling")', max_source)
        self.assertIn('d.start_background(supervise, name="proxy-supervisor")', web_source)
