from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from .config import FlowProfilerConfig
from .lock import FlowProfilerLock
from .planner import event_dicts, plan_dry_run, prompt_hash
from .report import build_summary
from . import SCHEMA_VERSION
from .writers import write_jsonl, write_summary


SingleBrowserExecutor = Callable[[FlowProfilerConfig, str], Awaitable[dict[str, object]]]
BrowserCheckExecutor = Callable[[FlowProfilerConfig, str], Awaitable[list[dict[str, object]]]]


def run_dry_run(config: FlowProfilerConfig, *, run_id: str | None = None) -> dict[str, Path | str | int]:
    if config.mode != "dry-run":
        raise ValueError("only dry-run mode is supported")

    actual_run_id = run_id or _new_run_id()
    started = _utc_now()
    start_tick = time.perf_counter()
    prompt_texts = [prompt.text for prompt in config.prompts]

    with FlowProfilerLock(config.output_dir):
        run_dir = config.output_dir / actual_run_id
        events = event_dicts(plan_dry_run(config, actual_run_id))
        finished = _utc_now()
        duration_ms = max(0, round((time.perf_counter() - start_tick) * 1000))

        stamped_events = [
            {
                **event,
                "started_at": started,
                "finished_at": finished,
                "duration_ms": duration_ms,
            }
            for event in events
        ]
        summary = build_summary(
            run_id=actual_run_id,
            mode=config.mode,
            started_at=started,
            finished_at=finished,
            duration_ms=duration_ms,
            events=stamped_events,
        )
        write_jsonl(run_dir / "events.jsonl", stamped_events, extra_secrets=prompt_texts)
        write_summary(run_dir / "summary.json", summary, extra_secrets=prompt_texts)

    return {
        "run_id": actual_run_id,
        "run_dir": run_dir,
        "events_path": run_dir / "events.jsonl",
        "summary_path": run_dir / "summary.json",
        "planned_generations": len(events),
    }


def run_single(
    config: FlowProfilerConfig,
    *,
    run_id: str | None = None,
    browser_executor: SingleBrowserExecutor | None = None,
) -> dict[str, Path | str | int]:
    if config.mode != "single":
        raise ValueError("only single mode is supported")

    actual_run_id = run_id or _new_run_id("single")
    run_dir = config.output_dir / actual_run_id
    prompt_texts = [prompt.text for prompt in config.prompts]
    if config.user_data_dir is not None:
        prompt_texts.append(str(config.user_data_dir))

    with FlowProfilerLock(config.output_dir):
        if browser_executor is None:
            try:
                from .browser_single import run_single_browser_smoke as browser_executor
            except Exception as exc:
                events = [_single_error_event(config, actual_run_id, exc)]
            else:
                events = [_run_browser_executor(browser_executor, config, actual_run_id)]
        else:
            events = [_run_browser_executor(browser_executor, config, actual_run_id)]

        started = str(events[0]["started_at"])
        finished = str(events[0]["finished_at"])
        duration_ms = int(events[0].get("duration_ms") or 0)
        summary = build_summary(
            run_id=actual_run_id,
            mode=config.mode,
            started_at=started,
            finished_at=finished,
            duration_ms=duration_ms,
            events=events,
        )
        write_jsonl(run_dir / "events.jsonl", events, extra_secrets=prompt_texts)
        write_summary(run_dir / "summary.json", summary, extra_secrets=prompt_texts)

    return {
        "run_id": actual_run_id,
        "run_dir": run_dir,
        "events_path": run_dir / "events.jsonl",
        "summary_path": run_dir / "summary.json",
        "events_written": len(events),
    }


def run_browser_check(
    config: FlowProfilerConfig,
    *,
    run_id: str | None = None,
    diagnostic_executor: BrowserCheckExecutor | None = None,
) -> dict[str, Path | str | int]:
    if config.mode != "browser-check":
        raise ValueError("only browser-check mode is supported")

    actual_run_id = run_id or _new_run_id("browsercheck")
    run_dir = config.output_dir / actual_run_id
    extra_secrets: list[str] = []
    if config.user_data_dir is not None:
        extra_secrets.append(str(config.user_data_dir))

    with FlowProfilerLock(config.output_dir):
        if diagnostic_executor is None:
            try:
                from .browser_check import run_browser_check_diagnostics as diagnostic_executor
            except Exception as exc:
                events = [_browser_check_error_event(actual_run_id, exc)]
            else:
                events = _run_browser_check_executor(diagnostic_executor, config, actual_run_id)
        else:
            events = _run_browser_check_executor(diagnostic_executor, config, actual_run_id)

        if not events:
            events = [_browser_check_error_event(actual_run_id, RuntimeError("no diagnostic events"))]
        started = str(events[0]["started_at"])
        finished = str(events[-1]["finished_at"])
        duration_ms = sum(int(event.get("duration_ms") or 0) for event in events)
        summary = build_summary(
            run_id=actual_run_id,
            mode=config.mode,
            started_at=started,
            finished_at=finished,
            duration_ms=duration_ms,
            events=events,
        )
        write_jsonl(run_dir / "events.jsonl", events, extra_secrets=extra_secrets)
        write_summary(run_dir / "summary.json", summary, extra_secrets=extra_secrets)

    return {
        "run_id": actual_run_id,
        "run_dir": run_dir,
        "events_path": run_dir / "events.jsonl",
        "summary_path": run_dir / "summary.json",
        "events_written": len(events),
    }


def _run_browser_executor(
    browser_executor: SingleBrowserExecutor,
    config: FlowProfilerConfig,
    run_id: str,
) -> dict[str, object]:
    try:
        return asyncio.run(browser_executor(config, run_id))
    except Exception as exc:
        return _single_error_event(config, run_id, exc)


def _run_browser_check_executor(
    diagnostic_executor: BrowserCheckExecutor,
    config: FlowProfilerConfig,
    run_id: str,
) -> list[dict[str, object]]:
    try:
        return asyncio.run(diagnostic_executor(config, run_id))
    except Exception as exc:
        return [_browser_check_error_event(run_id, exc)]


def _single_error_event(
    config: FlowProfilerConfig,
    run_id: str,
    exc: BaseException,
) -> dict[str, object]:
    now = _utc_now()
    prompt_item = config.prompts[0]
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "mode": "single",
        "stage_name": "browser-smoke",
        "sequence_index": 1,
        "planned_delay_sec": 0.0,
        "started_at": now,
        "finished_at": now,
        "duration_ms": 0,
        "prompt_id": prompt_item.prompt_id,
        "prompt_hash": prompt_hash(prompt_item.text),
        "prompt_length": len(prompt_item.text),
        "aspect_ratio": config.aspect_ratio,
        "requested_outputs": 1,
        "status": "error",
        "http_statuses": [],
        "output_count": 0,
        "stop_signal": "browser_launch_failed",
        "warning_signals": ["browser_launch_failed"],
        "error_type": "browser_launch_failed",
        "error_message_redacted": _browser_launch_error_message(),
        "page_state": "browser-launch-failed",
    }


def _browser_check_error_event(run_id: str, exc: BaseException) -> dict[str, object]:
    now = _utc_now()
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "mode": "browser-check",
        "stage_name": "browser-diagnostic",
        "sequence_index": 1,
        "started_at": now,
        "finished_at": now,
        "duration_ms": 0,
        "status": "error",
        "warning_signals": ["browser_check_failed"],
        "error_type": "browser_check_failed",
        "check_name": "browser_check_runner",
        "diagnostic_signal": "browser_check_failed",
        "sanitized_error_type": exc.__class__.__name__,
        "sanitized_error_message": "Browser diagnostic failed before checks completed.",
        "browser_channel_used": None,
    }


def _browser_launch_error_message() -> str:
    return "Browser launch failed before navigation. See local console for details."


def _new_run_id(prefix: str = "dryrun") -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{timestamp}-{uuid.uuid4().hex[:8]}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
