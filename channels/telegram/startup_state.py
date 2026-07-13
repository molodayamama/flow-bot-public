"""Startup-state construction and mutation helpers for the Telegram adapter."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any


def make_startup_state(
    account_ids: Iterable[str],
    *,
    total_accounts: int,
    min_ready: int,
) -> dict[str, Any]:
    """Build the initial startup snapshot from explicit composition inputs."""
    return {
        "phase": "init",
        "polling": False,
        "ready_accounts": 0,
        "total_accounts": total_accounts,
        "min_ready": min_ready,
        "accounts": {
            acc_id: {"status": "pending", "ready": False, "updated_at": None}
            for acc_id in account_ids
        },
    }


def set_startup_phase(
    state: dict[str, Any],
    phase: str,
    clock: Callable[[], float],
    /,
    **extra: Any,
) -> None:
    """Mutate the global startup phase while preserving legacy write order."""
    state["phase"] = phase
    state["updated_at"] = clock()
    for key, value in extra.items():
        state[key] = value


def set_startup_account(
    state: dict[str, Any],
    acc_id: str,
    status: str,
    clock: Callable[[], float],
    /,
    *,
    ready: bool = False,
    error: str | None = None,
) -> None:
    """Add or update one account and recount readiness across all accounts."""
    item = state.setdefault("accounts", {}).setdefault(acc_id, {})
    item.update({"status": status, "ready": bool(ready), "updated_at": clock()})
    if error:
        item["error"] = error
    elif "error" in item:
        item.pop("error", None)
    ready_count = sum(
        1
        for account in state.get("accounts", {}).values()
        if account.get("ready")
    )
    state["ready_accounts"] = ready_count
