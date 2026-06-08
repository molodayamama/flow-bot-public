from __future__ import annotations

import importlib
import re
import tempfile
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from . import SCHEMA_VERSION
from .config import FlowProfilerConfig
from .writers import redact_text


SAFE_DIAGNOSTIC_URL = "about:blank"
_CHECK_NAMES = (
    "playwright_import",
    "playwright_manager_start",
    "chromium_launch_temp_clean",
    "persistent_context_temp_clean",
    "persistent_context_existing_profile",
)


async def run_browser_check_diagnostics(
    config: FlowProfilerConfig,
    run_id: str,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    async_api: Any | None = None
    manager_ready = False
    launch_ready = False
    persistent_ready = False

    async def import_check() -> tuple[str | None, str | None]:
        nonlocal async_api
        async_api = importlib.import_module("playwright.async_api")
        return None, None

    event, ok = await _run_check(
        config,
        run_id,
        sequence_index=1,
        check_name="playwright_import",
        action=import_check,
    )
    events.append(event)
    if not ok:
        events.extend(_skipped_after_failure(run_id, start_index=2))
        return events

    async def manager_check() -> tuple[str | None, str | None]:
        assert async_api is not None
        async with async_api.async_playwright():
            return None, None

    event, manager_ready = await _run_check(
        config,
        run_id,
        sequence_index=2,
        check_name="playwright_manager_start",
        action=manager_check,
    )
    events.append(event)
    if not manager_ready:
        events.extend(_skipped_after_failure(run_id, start_index=3))
        return events

    async def launch_check() -> tuple[str | None, str | None]:
        assert async_api is not None
        async with async_api.async_playwright() as playwright:
            with tempfile.TemporaryDirectory(prefix="flow-profiler-browser-check-") as temp_dir:
                browser = await playwright.chromium.launch(
                    headless=True,
                    downloads_path=temp_dir,
                )
                try:
                    page = await browser.new_page()
                    await _open_safe_page(page, config.timeout_sec)
                finally:
                    await browser.close()
        return None, "bundled-chromium"

    event, launch_ready = await _run_check(
        config,
        run_id,
        sequence_index=3,
        check_name="chromium_launch_temp_clean",
        action=launch_check,
    )
    events.append(event)

    async def persistent_temp_check() -> tuple[str | None, str | None]:
        assert async_api is not None
        async with async_api.async_playwright() as playwright:
            with tempfile.TemporaryDirectory(prefix="flow-profiler-browser-check-profile-") as temp_dir:
                context = await playwright.chromium.launch_persistent_context(
                    temp_dir,
                    headless=True,
                )
                try:
                    page = context.pages[0] if context.pages else await context.new_page()
                    await _open_safe_page(page, config.timeout_sec)
                finally:
                    await context.close()
        return None, "bundled-chromium"

    if launch_ready:
        event, persistent_ready = await _run_check(
            config,
            run_id,
            sequence_index=4,
            check_name="persistent_context_temp_clean",
            action=persistent_temp_check,
        )
    else:
        event = _skipped_event(
            run_id,
            sequence_index=4,
            check_name="persistent_context_temp_clean",
            diagnostic_signal="prerequisite_failed",
        )
    events.append(event)

    async def persistent_existing_check() -> tuple[str | None, str | None]:
        assert async_api is not None
        async with async_api.async_playwright() as playwright:
            browser_context = await playwright.chromium.launch_persistent_context(
                str(config.user_data_dir),
                headless=True,
            )
            try:
                page = browser_context.pages[0] if browser_context.pages else await browser_context.new_page()
                await _open_safe_page(page, config.timeout_sec)
            finally:
                await browser_context.close()
        return None, "bundled-chromium"

    if config.user_data_dir is None:
        events.append(
            _skipped_event(
                run_id,
                sequence_index=5,
                check_name="persistent_context_existing_profile",
                diagnostic_signal="no_user_data_dir",
            )
        )
    elif persistent_ready:
        event, _ = await _run_check(
            config,
            run_id,
            sequence_index=5,
            check_name="persistent_context_existing_profile",
            action=persistent_existing_check,
        )
        events.append(event)
    else:
        events.append(
            _skipped_event(
                run_id,
                sequence_index=5,
                check_name="persistent_context_existing_profile",
                diagnostic_signal="prerequisite_failed",
            )
        )

    return events


async def _run_check(
    config: FlowProfilerConfig,
    run_id: str,
    *,
    sequence_index: int,
    check_name: str,
    action: Callable[[], Awaitable[tuple[str | None, str | None]]],
) -> tuple[dict[str, object], bool]:
    started_at = _utc_now()
    start_tick = time.perf_counter()
    status = "success"
    signal: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    channel: str | None = None
    try:
        signal, channel = await action()
    except Exception as exc:
        status = "error"
        error_type = exc.__class__.__name__
        error_message = _safe_error_message(exc, config)
        signal = _diagnostic_signal(check_name, error_message)

    finished_at = _utc_now()
    duration_ms = max(0, round((time.perf_counter() - start_tick) * 1000))
    event = _base_event(
        run_id=run_id,
        check_name=check_name,
        sequence_index=sequence_index,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        status=status,
        diagnostic_signal=signal,
        browser_channel_used=channel,
    )
    if status == "error":
        event.update(
            {
                "warning_signals": [signal] if signal else [],
                "error_type": signal or "unknown_local_launch_failure",
                "sanitized_error_type": error_type,
                "sanitized_error_message": error_message,
            }
        )
    return event, status == "success"


async def _open_safe_page(page: Any, timeout_sec: int) -> None:
    await page.goto(
        SAFE_DIAGNOSTIC_URL,
        wait_until="load",
        timeout=max(1, timeout_sec) * 1000,
    )


def _skipped_after_failure(
    run_id: str,
    *,
    start_index: int,
) -> list[dict[str, object]]:
    skipped: list[dict[str, object]] = []
    for sequence_index, check_name in enumerate(_CHECK_NAMES[start_index - 1 :], start=start_index):
        skipped.append(
            _skipped_event(
                run_id,
                sequence_index=sequence_index,
                check_name=check_name,
                diagnostic_signal="prerequisite_failed",
            )
        )
    return skipped


def _skipped_event(
    run_id: str,
    *,
    sequence_index: int,
    check_name: str,
    diagnostic_signal: str,
) -> dict[str, object]:
    now = _utc_now()
    return _base_event(
        run_id=run_id,
        check_name=check_name,
        sequence_index=sequence_index,
        started_at=now,
        finished_at=now,
        duration_ms=0,
        status="skipped",
        diagnostic_signal=diagnostic_signal,
        browser_channel_used=None,
    )


def _base_event(
    *,
    run_id: str,
    check_name: str,
    sequence_index: int,
    started_at: str,
    finished_at: str,
    duration_ms: int,
    status: str,
    diagnostic_signal: str | None,
    browser_channel_used: str | None,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "mode": "browser-check",
        "stage_name": "browser-diagnostic",
        "sequence_index": sequence_index,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
        "status": status,
        "warning_signals": [],
        "error_type": None,
        "check_name": check_name,
        "diagnostic_signal": diagnostic_signal,
        "sanitized_error_type": None,
        "sanitized_error_message": None,
        "browser_channel_used": browser_channel_used,
    }


def _diagnostic_signal(check_name: str, sanitized_message: str) -> str:
    text = re.sub(r"\s+", " ", sanitized_message.lower()).strip()
    if check_name == "playwright_import":
        return "playwright_import_unavailable"
    if check_name == "playwright_manager_start":
        return "playwright_runtime_unavailable"
    if "executable doesn't exist" in text or "playwright install" in text:
        return "missing_playwright_browser_install"
    if "permission denied" in text or "access is denied" in text or "operation not permitted" in text:
        return "os_browser_permission_issue"
    if "no usable sandbox" in text or "eacces" in text:
        return "os_browser_permission_issue"
    if check_name == "persistent_context_existing_profile":
        if "process singleton" in text or "user data directory is already in use" in text:
            return "profile_already_locked"
        if "profile appears to be in use" in text or "another chrome" in text:
            return "profile_already_locked"
        if "already running" in text or "chrome process" in text:
            return "existing_chrome_process_conflict"
        if "corrupt" in text or "incompatible" in text or "profile cannot be used" in text:
            return "profile_incompatible_or_corrupted"
    if check_name == "persistent_context_temp_clean":
        return "persistent_context_launch_failed"
    return "unknown_local_launch_failure"


def _safe_error_message(exc: BaseException, config: FlowProfilerConfig) -> str:
    extra_secrets: list[str] = []
    if config.user_data_dir is not None:
        extra_secrets.append(str(config.user_data_dir))
    raw_message = str(exc)
    message = redact_text(raw_message, extra_secrets=extra_secrets)
    raw_lower = raw_message.lower()
    generic_prefixes: list[str] = []
    if "executable doesn't exist" in raw_lower and "executable doesn't exist" not in message.lower():
        generic_prefixes.append("Executable doesn't exist.")
    if "playwright install" in raw_lower and "playwright install" not in message.lower():
        generic_prefixes.append("Run playwright install.")
    if generic_prefixes:
        message = " ".join(generic_prefixes + [message])
    message = re.sub(r"\s+", " ", message).strip()
    return message[:240] if message else "Local browser diagnostic failed."


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
