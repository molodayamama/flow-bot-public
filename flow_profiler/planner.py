from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable

from . import SCHEMA_VERSION
from .config import FlowProfilerConfig, PromptInput


@dataclass(frozen=True)
class PlannedEvent:
    schema_version: str
    run_id: str
    mode: str
    stage_name: str
    sequence_index: int
    planned_delay_sec: float
    prompt_id: str
    prompt_hash: str
    prompt_length: int
    aspect_ratio: str
    requested_outputs: int
    status: str
    http_statuses: tuple[int, ...]
    output_count: int
    stop_signal: str | None
    warning_signals: tuple[str, ...]
    error_type: str | None
    error_message_redacted: str | None
    page_state: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "mode": self.mode,
            "stage_name": self.stage_name,
            "sequence_index": self.sequence_index,
            "planned_delay_sec": self.planned_delay_sec,
            "prompt_id": self.prompt_id,
            "prompt_hash": self.prompt_hash,
            "prompt_length": self.prompt_length,
            "aspect_ratio": self.aspect_ratio,
            "requested_outputs": self.requested_outputs,
            "status": self.status,
            "http_statuses": list(self.http_statuses),
            "output_count": self.output_count,
            "stop_signal": self.stop_signal,
            "warning_signals": list(self.warning_signals),
            "error_type": self.error_type,
            "error_message_redacted": self.error_message_redacted,
            "page_state": self.page_state,
        }


def plan_dry_run(config: FlowProfilerConfig, run_id: str) -> list[PlannedEvent]:
    events: list[PlannedEvent] = []
    prompts = tuple(config.prompts)
    for zero_index in range(config.max_generations):
        prompt_item = prompts[zero_index % len(prompts)]
        events.append(_planned_event(config, run_id, zero_index + 1, prompt_item))
    return events


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _planned_event(
    config: FlowProfilerConfig,
    run_id: str,
    sequence_index: int,
    prompt_item: PromptInput,
) -> PlannedEvent:
    return PlannedEvent(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        mode=config.mode,
        stage_name="offline-plan",
        sequence_index=sequence_index,
        planned_delay_sec=config.delay_sec,
        prompt_id=prompt_item.prompt_id,
        prompt_hash=prompt_hash(prompt_item.text),
        prompt_length=len(prompt_item.text),
        aspect_ratio=config.aspect_ratio,
        requested_outputs=config.requested_outputs,
        status="planned",
        http_statuses=(),
        output_count=0,
        stop_signal=None,
        warning_signals=(),
        error_type=None,
        error_message_redacted=None,
        page_state="not-started",
    )


def event_dicts(events: Iterable[PlannedEvent]) -> list[dict[str, object]]:
    return [event.to_dict() for event in events]

