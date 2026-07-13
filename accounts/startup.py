"""Account-pool warmup and startup-readiness orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .pool import AccountPool


@dataclass(frozen=True)
class AccountWarmupDeps:
    """Runtime dependencies owned by the application composition root."""

    account_pool: AccountPool
    keepers: Mapping[str, Any]
    startup_state: dict[str, Any]
    set_startup_account: Callable[..., None]
    set_startup_phase: Callable[..., None]
    min_ready_accounts: int
    warmup_concurrency: int
    clock: Callable[[], float]
    log: Any


@dataclass(frozen=True)
class AccountWarmupResult:
    """Warmup tasks and the decision that gates polling startup."""

    tasks: tuple[asyncio.Task[bool], ...]
    ready_count: int
    total_accounts: int
    min_ready: int
    threshold_met: bool


async def warm_account_pool(
    deps: AccountWarmupDeps,
    *,
    seller_mode: bool,
) -> AccountWarmupResult:
    """Warm account keepers until polling may start.

    In consumer mode, remaining keepers intentionally continue in background
    after the readiness threshold is met. The caller owns and later cleans up
    every returned task. Seller mode marks the local pool as skipped without
    starting browser-backed keepers.
    """
    d = deps
    total_accounts = len(d.keepers)

    if seller_mode:
        d.startup_state["min_ready"] = 0
        for acc_id in d.keepers:
            d.account_pool.set_runtime_ready(acc_id, True, "skipped")
            d.set_startup_account(acc_id, "skipped", ready=True)
        d.set_startup_phase("ready", ready_accounts=total_accounts)
        return AccountWarmupResult(
            tasks=(),
            ready_count=total_accounts,
            total_accounts=total_accounts,
            min_ready=0,
            threshold_met=True,
        )

    warmup_sem = asyncio.Semaphore(d.warmup_concurrency)
    min_ready = min(max(1, d.min_ready_accounts), total_accounts)
    d.startup_state["min_ready"] = min_ready
    d.startup_state["total_accounts"] = total_accounts
    ready_event = asyncio.Event()
    warm_started = d.clock()
    ready_count = 0
    completed_count = 0

    for acc_id in d.keepers:
        d.account_pool.set_runtime_ready(acc_id, False, "warming")
        d.set_startup_account(acc_id, "pending", ready=False)
    d.set_startup_phase("warming")

    async def _warm_account(acc_id: str, keeper: Any) -> bool:
        nonlocal ready_count, completed_count
        ok = False
        async with warmup_sem:
            try:
                d.account_pool.set_runtime_ready(acc_id, False, "warming")
                d.set_startup_account(acc_id, "running", ready=False)
                d.log.info("🌐 Запускаю аккаунт пула: %s", acc_id)
                await keeper.start()
                ok = True
                ready_count += 1
                d.account_pool.set_runtime_ready(acc_id, True, "ready")
                d.set_startup_account(acc_id, "ready", ready=True)
            except Exception as exc:
                d.log.exception("❌ Аккаунт %s не стартовал — отключаю в пуле", acc_id)
                d.account_pool.set_runtime_ready(acc_id, False, "failed")
                d.account_pool.set_disabled(acc_id, True)
                d.set_startup_account(
                    acc_id,
                    "failed",
                    ready=False,
                    error=exc.__class__.__name__,
                )
            finally:
                completed_count += 1
                if ready_count >= min_ready or completed_count >= total_accounts:
                    ready_event.set()
        return ok

    tasks = tuple(
        asyncio.create_task(_warm_account(acc_id, keeper))
        for acc_id, keeper in d.keepers.items()
    )
    await ready_event.wait()
    if ready_count < min_ready:
        await asyncio.gather(*tasks, return_exceptions=True)
        d.set_startup_phase("blocked", ready_accounts=ready_count)
        d.log.error(
            "❌ Прогрев пула не достиг threshold %d/%d — выходим.",
            ready_count,
            min_ready,
        )
        return AccountWarmupResult(
            tasks=tasks,
            ready_count=ready_count,
            total_accounts=total_accounts,
            min_ready=min_ready,
            threshold_met=False,
        )

    d.set_startup_phase("ready_threshold_met", ready_accounts=ready_count)
    d.log.info(
        "🌐 Прогрев threshold: %d/%d аккаунтов за %.1f c "
        "(минимум %d, лимит %d)",
        ready_count,
        total_accounts,
        d.clock() - warm_started,
        min_ready,
        d.warmup_concurrency,
    )
    return AccountWarmupResult(
        tasks=tasks,
        ready_count=ready_count,
        total_accounts=total_accounts,
        min_ready=min_ready,
        threshold_met=True,
    )


__all__ = [
    "AccountPool",
    "AccountWarmupDeps",
    "AccountWarmupResult",
    "warm_account_pool",
]
