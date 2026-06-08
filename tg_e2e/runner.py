from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import time
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from flow_profiler.planner import prompt_hash
from flow_profiler.report import build_summary
from flow_profiler.safety import SafetyClassifier
from flow_profiler.writers import redact_text, write_jsonl, write_summary

from . import SCHEMA_VERSION
from .config import TelegramE2EConfig


@dataclass(frozen=True)
class TelegramBatch:
    text: str = ""
    photo_count: int = 0
    error_type: str | None = None
    error_message: str | None = None
    flood_wait_sec: int | None = None


class TelegramClientProtocol(Protocol):
    async def ensure_authorized(self) -> str:
        ...

    async def send_and_collect(
        self,
        *,
        bot_username: str,
        text: str,
        timeout_sec: int,
        expected_min_photos: int = 0,
    ) -> TelegramBatch:
        ...

    async def close(self) -> None:
        ...


ClientFactory = Callable[[TelegramE2EConfig], TelegramClientProtocol]
SleepFunc = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class Scenario:
    stage_name: str
    text: str
    planned_delay_sec: float
    expected_min_photos: int = 0
    uses_prompt: bool = False


def run(
    config: TelegramE2EConfig,
    *,
    run_id: str | None = None,
    client_factory: ClientFactory | None = None,
    sleep_func: SleepFunc | None = None,
) -> dict[str, Path | str | int]:
    actual_run_id = run_id or _new_run_id(config.mode)
    run_dir = config.output_dir / actual_run_id
    started = _utc_now()
    start_tick = time.perf_counter()
    events = (
        _dry_run_events(config, actual_run_id, started)
        if config.mode == "dry-run"
        else _run_external(config, actual_run_id, client_factory, sleep_func)
    )
    finished = _utc_now()
    duration_ms = max(0, round((time.perf_counter() - start_tick) * 1000))
    stamped = [
        {
            **event,
            "started_at": event.get("started_at") or started,
            "finished_at": event.get("finished_at") or finished,
            "duration_ms": event.get("duration_ms", duration_ms),
        }
        for event in events
    ]
    summary = build_summary(
        run_id=actual_run_id,
        mode=config.mode,
        started_at=str(stamped[0]["started_at"]) if stamped else started,
        finished_at=str(stamped[-1]["finished_at"]) if stamped else finished,
        duration_ms=sum(int(event.get("duration_ms") or 0) for event in stamped),
        events=stamped,
    )
    secrets = _extra_secrets(config)
    write_jsonl(run_dir / "events.jsonl", stamped, extra_secrets=secrets)
    write_summary(run_dir / "summary.json", summary, extra_secrets=secrets)
    return {
        "run_id": actual_run_id,
        "run_dir": run_dir,
        "events_path": run_dir / "events.jsonl",
        "summary_path": run_dir / "summary.json",
        "events_written": len(stamped),
    }


def _dry_run_events(config: TelegramE2EConfig, run_id: str, started: str) -> list[dict[str, object]]:
    return [
        _base_event(
            config=config,
            run_id=run_id,
            scenario=scenario,
            sequence_index=index,
            status="planned",
            started_at=started,
            finished_at=started,
            duration_ms=0,
        )
        for index, scenario in enumerate(_scenarios(config), start=1)
    ]


def _run_external(
    config: TelegramE2EConfig,
    run_id: str,
    client_factory: ClientFactory | None,
    sleep_func: SleepFunc | None,
) -> list[dict[str, object]]:
    if client_factory is None:
        from .telethon_client import make_telethon_client as client_factory
    return asyncio.run(_run_external_async(config, run_id, client_factory, sleep_func or asyncio.sleep))


async def _run_external_async(
    config: TelegramE2EConfig,
    run_id: str,
    client_factory: ClientFactory,
    sleep_func: SleepFunc,
) -> list[dict[str, object]]:
    client = client_factory(config)
    events: list[dict[str, object]] = []
    try:
        if config.mode == "telegram-login":
            started = _utc_now()
            start_tick = time.perf_counter()
            try:
                page_state = await client.ensure_authorized()
                batch = TelegramBatch(text=page_state)
            except Exception as exc:
                batch = TelegramBatch(
                    error_type=exc.__class__.__name__,
                    error_message=str(exc),
                )
            finished = _utc_now()
            scenario = Scenario("telegram-login", "login", 0)
            events.append(
                _classify_batch(
                    config=config,
                    run_id=run_id,
                    scenario=scenario,
                    sequence_index=1,
                    batch=batch,
                    started_at=started,
                    finished_at=finished,
                    duration_ms=max(0, round((time.perf_counter() - start_tick) * 1000)),
                )
            )
            return events

        assert config.bot_username is not None
        for index, scenario in enumerate(_scenarios(config), start=1):
            if scenario.planned_delay_sec > 0:
                await sleep_func(scenario.planned_delay_sec)
            started = _utc_now()
            start_tick = time.perf_counter()
            try:
                batch = await client.send_and_collect(
                    bot_username=config.bot_username,
                    text=scenario.text,
                    timeout_sec=config.timeout_sec,
                    expected_min_photos=scenario.expected_min_photos,
                )
            except Exception as exc:
                batch = TelegramBatch(
                    error_type=exc.__class__.__name__,
                    error_message=str(exc),
                )
            finished = _utc_now()
            duration_ms = max(0, round((time.perf_counter() - start_tick) * 1000))
            event = _classify_batch(
                config=config,
                run_id=run_id,
                scenario=scenario,
                sequence_index=index,
                batch=batch,
                started_at=started,
                finished_at=finished,
                duration_ms=duration_ms,
            )
            events.append(event)
            if event.get("stop_signal") or event.get("error_type"):
                break
    finally:
        await client.close()
    return events


def _scenarios(config: TelegramE2EConfig) -> list[Scenario]:
    if config.mode in {"dry-run", "telegram-smoke"}:
        return [
            Scenario("telegram-start", "/start", 0),
            Scenario("telegram-status", "/status", 1),
        ]
    if config.mode == "telegram-generation":
        assert config.prompt is not None
        return [
            Scenario(
                "telegram-one-generation",
                f"/one {config.prompt}",
                0,
                expected_min_photos=1,
                uses_prompt=True,
            )
        ]
    if config.mode == "telegram-ramp":
        assert config.prompt is not None
        return [
            Scenario(
                "telegram-ramp-one-generation",
                f"/one {config.prompt}",
                0 if index == 0 else config.delay_sec,
                expected_min_photos=1,
                uses_prompt=True,
            )
            for index in range(config.max_steps)
        ]
    raise ValueError("unsupported mode")


def _classify_batch(
    *,
    config: TelegramE2EConfig,
    run_id: str,
    scenario: Scenario,
    sequence_index: int,
    batch: TelegramBatch,
    started_at: str,
    finished_at: str,
    duration_ms: int,
) -> dict[str, object]:
    base = _base_event(
        config=config,
        run_id=run_id,
        scenario=scenario,
        sequence_index=sequence_index,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        status="unknown",
    )
    signal = _signal_from_batch(batch, scenario)
    if batch.error_type:
        return {
            **base,
            "status": "error",
            "stop_signal": signal or "telegram_client_error",
            "warning_signals": [signal or "telegram_client_error"],
            "error_type": batch.error_type,
            "error_message_redacted": _safe_message(batch.error_message or "", config),
            "output_count": batch.photo_count,
        }
    if signal:
        return {
            **base,
            "status": "stopped",
            "stop_signal": signal,
            "warning_signals": [signal],
            "output_count": batch.photo_count,
        }
    if scenario.expected_min_photos and batch.photo_count >= scenario.expected_min_photos:
        return {**base, "status": "success", "output_count": batch.photo_count}
    if not scenario.expected_min_photos and batch.text:
        return {**base, "status": "success", "output_count": batch.photo_count}
    return {
        **base,
        "status": "stopped",
        "stop_signal": "timeout",
        "warning_signals": ["timeout"],
        "output_count": batch.photo_count,
    }


def _signal_from_batch(batch: TelegramBatch, scenario: Scenario) -> str | None:
    if batch.flood_wait_sec is not None:
        return "telegram_flood_wait"
    text = batch.text or ""
    lower = text.lower()
    if "cooldown" in lower or "wait" in lower or "\u043f\u043e\u0434\u043e\u0436\u0434" in lower:
        return "bot_cooldown"
    if "\u0443\u043a\u0430\u0436\u0438\u0442\u0435" in lower and "\u043f\u0440\u043e\u043c\u043f\u0442" in lower:
        return "prompt_rejected"
    if not scenario.expected_min_photos:
        return None
    signal = SafetyClassifier().classify(text)
    if signal.severity == "hard_stop":
        return signal.kind
    if scenario.expected_min_photos and "error" in lower:
        return "telegram_error_response"
    return None


def _base_event(
    *,
    config: TelegramE2EConfig,
    run_id: str,
    scenario: Scenario,
    sequence_index: int,
    status: str,
    started_at: str,
    finished_at: str,
    duration_ms: int,
) -> dict[str, object]:
    prompt_text = config.prompt or ""
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "mode": config.mode,
        "stage_name": scenario.stage_name,
        "sequence_index": sequence_index,
        "planned_delay_sec": scenario.planned_delay_sec,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
        "prompt_id": "prompt-001" if scenario.uses_prompt else None,
        "prompt_hash": prompt_hash(prompt_text) if scenario.uses_prompt else None,
        "prompt_length": len(prompt_text) if scenario.uses_prompt else 0,
        "requested_outputs": scenario.expected_min_photos,
        "status": status,
        "http_statuses": [],
        "output_count": 0,
        "stop_signal": None,
        "warning_signals": [],
        "error_type": None,
        "error_message_redacted": None,
        "page_state": "telegram-only",
    }


def _extra_secrets(config: TelegramE2EConfig) -> list[str]:
    secrets = [
        config.prompt or "",
        config.tg_api_hash or "",
        config.tg_phone or "",
        config.tg_proxy_url or "",
        config.mtproxy.secret if config.mtproxy else "",
        str(config.session_file or ""),
    ]
    return [secret for secret in secrets if secret]


def _safe_message(message: str, config: TelegramE2EConfig) -> str:
    return redact_text(message, extra_secrets=_extra_secrets(config))[:240]


def _new_run_id(prefix: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{timestamp}-{uuid.uuid4().hex[:8]}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
