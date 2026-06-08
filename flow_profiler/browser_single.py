from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from . import SCHEMA_VERSION
from .config import FlowProfilerConfig
from .planner import prompt_hash
from .safety import SafetyClassifier
from .writers import redact_text


FLOW_URL = "https://labs.google/fx/tools/flow"
_HARD_STATUS_CODES = {401, 403, 429}


async def run_single_browser_smoke(config: FlowProfilerConfig, run_id: str) -> dict[str, object]:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import async_playwright

    prompt_item = config.prompts[0]
    started_at = _utc_now()
    start_tick = time.perf_counter()
    http_statuses: list[int] = []
    context: Any | None = None
    page_state = "not-started"
    stop_signal: str | None = None
    output_count = 0
    error_type: str | None = None
    error_message: str | None = None
    phase = "launching"

    try:
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                str(config.user_data_dir),
                headless=False,
            )
            phase = "browser-open"
            page = context.pages[0] if context.pages else await context.new_page()
            page.on("response", lambda response: _record_status(response, http_statuses))

            phase = "navigation"
            page_state = "navigating"
            response = await page.goto(
                FLOW_URL,
                wait_until="domcontentloaded",
                timeout=config.timeout_sec * 1000,
            )
            if response is not None:
                _record_status(response, http_statuses)

            phase = "flow"
            stop_signal = await _detect_stop_signal(page, http_statuses)
            if stop_signal is None:
                page_state = "flow-open"
                await _open_project_or_new_flow(page)
                stop_signal = await _detect_stop_signal(page, http_statuses)

            if stop_signal is None:
                prompt_input = await _find_prompt_input(page)
                output_baseline = await _count_media(page)
                page_state = "prompt-entry"
                await _enter_prompt(prompt_input, prompt_item.text)
                await _submit_once(page, prompt_input)
                page_state = "submitted"
                stop_signal, output_count = await _wait_for_result_or_stop(
                    page,
                    http_statuses,
                    output_baseline,
                    config.timeout_sec,
                )

            if output_count > 0 and stop_signal is None:
                page_state = "media-detected"
            elif stop_signal is not None:
                page_state = stop_signal
    except PlaywrightTimeoutError as exc:
        if phase == "launching":
            stop_signal = "browser_launch_failed"
            page_state = "browser-launch-failed"
            error_type = "browser_launch_failed"
            error_message = _browser_launch_error_message()
        else:
            stop_signal = "timeout"
            page_state = "timeout"
            error_type = exc.__class__.__name__
            error_message = _safe_error_message(exc, config)
    except Exception as exc:
        if phase == "launching":
            stop_signal = "browser_launch_failed"
            page_state = "browser-launch-failed"
            error_type = "browser_launch_failed"
            error_message = _browser_launch_error_message()
        else:
            page_state = "browser-error"
            error_type = exc.__class__.__name__
            error_message = _safe_error_message(exc, config)
    finally:
        if context is not None:
            await context.close()

    finished_at = _utc_now()
    duration_ms = max(0, round((time.perf_counter() - start_tick) * 1000))
    status = _event_status(stop_signal, output_count, error_type)
    warning_signals = [stop_signal] if stop_signal else []

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "mode": "single",
        "stage_name": "browser-smoke",
        "sequence_index": 1,
        "planned_delay_sec": 0.0,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
        "prompt_id": prompt_item.prompt_id,
        "prompt_hash": prompt_hash(prompt_item.text),
        "prompt_length": len(prompt_item.text),
        "aspect_ratio": config.aspect_ratio,
        "requested_outputs": 1,
        "status": status,
        "http_statuses": _safe_statuses(http_statuses),
        "output_count": output_count,
        "stop_signal": stop_signal,
        "warning_signals": warning_signals,
        "error_type": error_type,
        "error_message_redacted": error_message,
        "page_state": page_state,
    }


def _record_status(response: Any, statuses: list[int]) -> None:
    try:
        status = int(response.status)
    except (TypeError, ValueError):
        return
    if status in _HARD_STATUS_CODES or status >= 500:
        statuses.append(status)


async def _detect_stop_signal(page: Any, http_statuses: list[int]) -> str | None:
    classifier = SafetyClassifier()
    for status in http_statuses:
        signal = classifier.classify(status)
        if signal.severity == "hard_stop":
            return signal.kind

    url = str(getattr(page, "url", "") or "")
    if url.startswith("https://accounts.google."):
        return "login_required"
    if url and not url.startswith("https://labs.google/") and url != "about:blank":
        return "unexpected_redirect"

    for kind, pattern in _visible_stop_patterns():
        if await _has_visible_text(page, pattern):
            return kind
    return None


def _visible_stop_patterns() -> list[tuple[str, re.Pattern[str]]]:
    return [
        ("login_required", re.compile(r"\b(sign in|sign into|login|log in)\b", re.I)),
        ("account_chooser", re.compile(r"choose an account|account chooser", re.I)),
        ("auth_challenge", re.compile(r"auth challenge|session expired|before you continue", re.I)),
        ("captcha_required", re.compile(r"captcha|recaptcha", re.I)),
        ("human_verification", re.compile(r"human verification|verify it's you|verify it is you", re.I)),
        ("account_risk", re.compile(r"unusual activity|suspicious activity", re.I)),
        ("account_warning", re.compile(r"account warning|ban warning|account disabled|account suspended", re.I)),
        ("quota_or_credits", re.compile(r"quota|credits? exhausted|credits? required|out of credits", re.I)),
        ("rate_limited", re.compile(r"rate limit|rate-limit|too many requests", re.I)),
        ("generation_unavailable", re.compile(r"generation unavailable|unavailable generation|try again later", re.I)),
    ]


async def _has_visible_text(page: Any, pattern: re.Pattern[str]) -> bool:
    locator = page.get_by_text(pattern).first()
    try:
        return await locator.is_visible(timeout=250)
    except Exception:
        return False


async def _open_project_or_new_flow(page: Any) -> None:
    if await _prompt_input_is_available(page):
        return
    controls = [
        page.get_by_role("button", name=re.compile(r"new flow", re.I)).first(),
        page.get_by_text(re.compile(r"new flow", re.I)).first(),
        page.get_by_role("link", name=re.compile(r"new flow", re.I)).first(),
    ]
    for control in controls:
        try:
            if await control.is_visible(timeout=1000):
                await control.click(timeout=1000)
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
                return
        except Exception:
            continue


async def _prompt_input_is_available(page: Any) -> bool:
    for locator in _prompt_input_candidates(page):
        try:
            if await locator.first().is_visible(timeout=250):
                return True
        except Exception:
            continue
    return False


async def _find_prompt_input(page: Any) -> Any:
    deadline = time.perf_counter() + 15
    while time.perf_counter() < deadline:
        for locator in _prompt_input_candidates(page):
            candidate = locator.first()
            try:
                if await candidate.is_visible(timeout=500):
                    return candidate
            except Exception:
                continue
        await page.wait_for_timeout(500)
    raise RuntimeError("prompt input unavailable")


def _prompt_input_candidates(page: Any) -> list[Any]:
    return [
        page.locator("textarea"),
        page.get_by_role("textbox", name=re.compile(r"prompt|describe|create", re.I)),
        page.get_by_placeholder(re.compile(r"prompt|describe|create", re.I)),
        page.locator("[contenteditable='true']"),
    ]


async def _enter_prompt(locator: Any, prompt: str) -> None:
    try:
        await locator.fill(prompt, timeout=5000)
        return
    except Exception:
        await locator.click(timeout=5000)
        await locator.press("Control+A")
        await locator.type(prompt, delay=0)


async def _submit_once(page: Any, prompt_input: Any) -> None:
    controls = [
        page.get_by_role("button", name=re.compile(r"generate|create|submit", re.I)).first(),
        page.get_by_text(re.compile(r"generate|create", re.I)).first(),
    ]
    for control in controls:
        try:
            if await control.is_visible(timeout=1000) and await control.is_enabled(timeout=1000):
                await control.click(timeout=3000)
                return
        except Exception:
            continue
    await prompt_input.press("Enter", timeout=3000)


async def _wait_for_result_or_stop(
    page: Any,
    http_statuses: list[int],
    output_baseline: int,
    timeout_sec: int,
) -> tuple[str | None, int]:
    deadline = time.perf_counter() + timeout_sec
    while time.perf_counter() < deadline:
        stop_signal = await _detect_stop_signal(page, http_statuses)
        if stop_signal is not None:
            return stop_signal, max(0, await _count_media(page) - output_baseline)
        output_count = max(0, await _count_media(page) - output_baseline)
        if output_count > 0:
            return None, output_count
        await page.wait_for_timeout(1000)
    return "timeout", max(0, await _count_media(page) - output_baseline)


async def _count_media(page: Any) -> int:
    try:
        locators = page.locator("img, video")
        count = await locators.count()
    except Exception:
        return 0
    visible = 0
    for index in range(count):
        try:
            if await locators.nth(index).is_visible(timeout=100):
                visible += 1
        except Exception:
            continue
    return visible


def _safe_statuses(statuses: list[int]) -> list[int]:
    return sorted({int(status) for status in statuses})


def _event_status(stop_signal: str | None, output_count: int, error_type: str | None) -> str:
    if error_type is not None:
        return "error"
    if stop_signal is not None:
        return "stopped"
    if output_count > 0:
        return "success"
    return "unknown"


def _safe_error_message(exc: BaseException, config: FlowProfilerConfig) -> str:
    secrets = [config.prompts[0].text]
    if config.user_data_dir is not None:
        secrets.append(str(config.user_data_dir))
    return redact_text(str(exc), extra_secrets=secrets)[:240]


def _browser_launch_error_message() -> str:
    return "Browser launch failed before navigation. See local console for details."


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
