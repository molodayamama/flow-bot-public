from __future__ import annotations

import asyncio
import unittest

from accounts.startup import AccountWarmupDeps, warm_account_pool
from channels.telegram.startup_state import (
    make_startup_state,
    set_startup_account,
    set_startup_phase,
)


class FakePool:
    def __init__(self) -> None:
        self.runtime_ready: list[tuple[str, bool, str]] = []
        self.disabled: list[tuple[str, bool]] = []

    def set_runtime_ready(self, account_id: str, ready: bool, reason: str) -> None:
        self.runtime_ready.append((account_id, ready, reason))

    def set_disabled(self, account_id: str, disabled: bool) -> None:
        self.disabled.append((account_id, disabled))


class FakeLog:
    def __init__(self) -> None:
        self.records: list[tuple[str, tuple]] = []

    def info(self, message: str, *args) -> None:
        self.records.append(("info", (message, *args)))

    def error(self, message: str, *args) -> None:
        self.records.append(("error", (message, *args)))

    def exception(self, message: str, *args) -> None:
        self.records.append(("exception", (message, *args)))


class Keeper:
    def __init__(
        self,
        *,
        gate: asyncio.Event | None = None,
        error: Exception | None = None,
    ) -> None:
        self.gate = gate
        self.error = error
        self.starts = 0

    async def start(self) -> None:
        self.starts += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error


class ConcurrencyKeeper:
    def __init__(self, tracker: dict[str, int]) -> None:
        self.tracker = tracker

    async def start(self) -> None:
        self.tracker["active"] += 1
        self.tracker["maximum"] = max(
            self.tracker["maximum"],
            self.tracker["active"],
        )
        await asyncio.sleep(0)
        self.tracker["active"] -= 1


class AccountWarmupTests(unittest.IsolatedAsyncioTestCase):
    def make_deps(
        self,
        keepers: dict[str, object],
        *,
        min_ready: int = 1,
        concurrency: int = 2,
    ) -> tuple[AccountWarmupDeps, FakePool, dict]:
        pool = FakePool()
        state = make_startup_state(
            keepers,
            total_accounts=len(keepers),
            min_ready=min_ready,
        )
        ticks = iter(float(value) for value in range(1, 100))

        def clock() -> float:
            return next(ticks)

        def set_account(account_id: str, status: str, **kwargs) -> None:
            set_startup_account(state, account_id, status, clock, **kwargs)

        def set_phase(phase: str, **extra) -> None:
            set_startup_phase(state, phase, clock, **extra)

        deps = AccountWarmupDeps(
            account_pool=pool,  # type: ignore[arg-type]
            keepers=keepers,
            startup_state=state,
            set_startup_account=set_account,
            set_startup_phase=set_phase,
            min_ready_accounts=min_ready,
            warmup_concurrency=concurrency,
            clock=clock,
            log=FakeLog(),
        )
        return deps, pool, state

    async def test_seller_mode_skips_keeper_start_and_marks_pool_ready(self) -> None:
        main = Keeper()
        sub1 = Keeper()
        deps, pool, state = self.make_deps({"main": main, "sub1": sub1})

        result = await warm_account_pool(deps, seller_mode=True)

        self.assertTrue(result.threshold_met)
        self.assertEqual(result.tasks, ())
        self.assertEqual(result.ready_count, 2)
        self.assertEqual((main.starts, sub1.starts), (0, 0))
        self.assertEqual(
            pool.runtime_ready,
            [("main", True, "skipped"), ("sub1", True, "skipped")],
        )
        self.assertEqual(state["phase"], "ready")
        self.assertEqual(state["min_ready"], 0)
        self.assertEqual(state["ready_accounts"], 2)

    async def test_empty_seller_pool_is_ready_without_local_keepers(self) -> None:
        deps, _pool, state = self.make_deps({})

        result = await asyncio.wait_for(
            warm_account_pool(deps, seller_mode=True),
            timeout=0.1,
        )

        self.assertTrue(result.threshold_met)
        self.assertEqual(result.tasks, ())
        self.assertEqual(result.ready_count, 0)
        self.assertEqual(result.total_accounts, 0)
        self.assertEqual(result.min_ready, 0)
        self.assertEqual(state["phase"], "ready")

    async def test_empty_consumer_pool_blocks_without_waiting_forever(self) -> None:
        deps, _pool, state = self.make_deps({})

        result = await asyncio.wait_for(
            warm_account_pool(deps, seller_mode=False),
            timeout=0.1,
        )

        self.assertFalse(result.threshold_met)
        self.assertEqual(result.tasks, ())
        self.assertEqual(result.ready_count, 0)
        self.assertEqual(result.total_accounts, 0)
        self.assertEqual(result.min_ready, 0)
        self.assertEqual(state["phase"], "blocked")
        self.assertEqual(state["ready_accounts"], 0)
        self.assertEqual(state["total_accounts"], 0)
        self.assertEqual(state["min_ready"], 0)

    async def test_threshold_returns_while_remaining_keeper_warms_in_background(self) -> None:
        slow_gate = asyncio.Event()
        fast = Keeper()
        slow = Keeper(gate=slow_gate)
        deps, pool, state = self.make_deps({"fast": fast, "slow": slow})

        result = await warm_account_pool(deps, seller_mode=False)

        self.assertTrue(result.threshold_met)
        self.assertEqual(result.ready_count, 1)
        self.assertEqual(len(result.tasks), 2)
        self.assertEqual(state["phase"], "ready_threshold_met")
        self.assertIn(("fast", True, "ready"), pool.runtime_ready)

        slow_gate.set()
        self.assertEqual(await asyncio.gather(*result.tasks), [True, True])
        self.assertEqual(state["ready_accounts"], 2)

    async def test_all_failures_block_polling_and_disable_accounts(self) -> None:
        keepers = {
            "main": Keeper(error=RuntimeError("main failed")),
            "sub1": Keeper(error=ValueError("sub1 failed")),
        }
        deps, pool, state = self.make_deps(keepers)

        result = await warm_account_pool(deps, seller_mode=False)

        self.assertFalse(result.threshold_met)
        self.assertEqual(result.ready_count, 0)
        self.assertTrue(all(task.done() for task in result.tasks))
        self.assertEqual(pool.disabled, [("main", True), ("sub1", True)])
        self.assertEqual(state["phase"], "blocked")
        self.assertEqual(state["ready_accounts"], 0)
        self.assertEqual(state["accounts"]["main"]["error"], "RuntimeError")
        self.assertEqual(state["accounts"]["sub1"]["error"], "ValueError")

    async def test_partial_success_below_threshold_still_blocks(self) -> None:
        keepers = {
            "main": Keeper(),
            "sub1": Keeper(error=RuntimeError("failed")),
        }
        deps, pool, state = self.make_deps(keepers, min_ready=2)

        result = await warm_account_pool(deps, seller_mode=False)

        self.assertFalse(result.threshold_met)
        self.assertEqual(result.ready_count, 1)
        self.assertEqual(state["phase"], "blocked")
        self.assertIn(("main", True, "ready"), pool.runtime_ready)
        self.assertEqual(pool.disabled, [("sub1", True)])

    async def test_warmup_respects_concurrency_limit(self) -> None:
        tracker = {"active": 0, "maximum": 0}
        keepers = {
            account_id: ConcurrencyKeeper(tracker)
            for account_id in ("main", "sub1", "sub2")
        }
        deps, _pool, state = self.make_deps(
            keepers,
            min_ready=3,
            concurrency=1,
        )

        result = await warm_account_pool(deps, seller_mode=False)
        await asyncio.gather(*result.tasks)

        self.assertTrue(result.threshold_met)
        self.assertEqual(result.ready_count, 3)
        self.assertEqual(tracker["maximum"], 1)
        self.assertEqual(state["ready_accounts"], 3)


if __name__ == "__main__":
    unittest.main()
