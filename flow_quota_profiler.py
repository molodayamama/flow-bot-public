from __future__ import annotations

import argparse
import sys

from flow_profiler.config import ConfigError, build_config, validate_no_dangerous_flags
from flow_profiler.runner import run_browser_check, run_dry_run, run_single


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Flow quota profiler with offline dry-run and approval-only single smoke modes.",
        allow_abbrev=False,
    )
    parser.add_argument("--mode", required=True)
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file")
    parser.add_argument("--prompt-id")
    parser.add_argument("--output-dir", default="flow_profiler_runs")
    parser.add_argument("--requested-outputs", type=int, default=1)
    parser.add_argument("--aspect-ratio", default="landscape")
    parser.add_argument("--max-generations", type=int)
    parser.add_argument("--delay-sec", type=float, default=0)
    parser.add_argument("--timeout-sec", type=int, default=120)
    parser.add_argument("--user-data-dir", action="append")
    parser.add_argument("--approve-external-action", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, unknown = parser.parse_known_args(argv)
    try:
        validate_no_dangerous_flags(unknown)
        if unknown:
            raise ConfigError("unsupported flag")
        config = build_config(
            mode=args.mode,
            prompt=args.prompt,
            prompt_file=args.prompt_file,
            prompt_id=args.prompt_id,
            output_dir=args.output_dir,
            requested_outputs=args.requested_outputs,
            aspect_ratio=args.aspect_ratio,
            max_generations=args.max_generations,
            delay_sec=args.delay_sec,
            user_data_dir=args.user_data_dir,
            approve_external_action=args.approve_external_action,
            timeout_sec=args.timeout_sec,
        )
        if config.mode == "single":
            result = run_single(config)
        elif config.mode == "browser-check":
            result = run_browser_check(config)
        else:
            result = run_dry_run(config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("error: profiler run failed", file=sys.stderr)
        return 1

    print(f"run_id: {result['run_id']}")
    print(f"events: {result['events_path']}")
    print(f"summary: {result['summary_path']}")
    if "planned_generations" in result:
        print(f"planned_generations: {result['planned_generations']}")
    if "events_written" in result:
        print(f"events_written: {result['events_written']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
