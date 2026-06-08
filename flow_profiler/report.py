from __future__ import annotations

from collections import Counter
from typing import Iterable, Mapping

from . import SCHEMA_VERSION


def build_summary(
    *,
    run_id: str,
    mode: str,
    started_at: str,
    finished_at: str,
    duration_ms: int,
    events: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    event_list = list(events)
    status_counts = Counter(str(event.get("status", "unknown")) for event in event_list)
    warnings = sorted(
        {
            str(signal)
            for event in event_list
            for signal in event.get("warning_signals", []) or []
        }
    )
    stop_signal = next(
        (event.get("stop_signal") for event in event_list if event.get("stop_signal")),
        None,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "mode": mode,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
        "total_events": len(event_list),
        "planned_generations": 0 if mode == "browser-check" else len(event_list),
        "successful_events": status_counts.get("success", 0),
        "warning_events": sum(1 for event in event_list if event.get("warning_signals")),
        "error_events": sum(1 for event in event_list if event.get("error_type")),
        "status_counts": dict(sorted(status_counts.items())),
        "stop_signal": stop_signal,
        "output_count": sum(int(event.get("output_count") or 0) for event in event_list),
        "warnings": warnings,
    }
