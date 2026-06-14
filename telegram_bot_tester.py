from __future__ import annotations

import argparse
import sys

from tg_e2e.config import ConfigError, build_config, validate_no_dangerous_flags
from tg_e2e.env_file import load_env_file
from tg_e2e.runner import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Approval-gated Telegram login and E2E tester for the Google Flow bot.",
        allow_abbrev=False,
    )
    parser.add_argument("--mode", required=True)
    parser.add_argument("--output-dir", default="tg_e2e_runs")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--timeout-sec", type=int, default=90)
    parser.add_argument("--approve-external-action", action="store_true")
    parser.add_argument("--bot-username")
    parser.add_argument("--tg-api-id")
    parser.add_argument("--tg-api-hash")
    parser.add_argument("--tg-phone")
    parser.add_argument("--tg-proxy-url")
    parser.add_argument("--tg-code")
    parser.add_argument("--tg-password")
    parser.add_argument("--session-file")
    parser.add_argument("--prompt")
    parser.add_argument("--max-steps", type=int, default=1)
    parser.add_argument("--delay-sec", type=float, default=90.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, unknown = parser.parse_known_args(argv)
    try:
        validate_no_dangerous_flags(unknown)
        if unknown:
            raise ConfigError("unsupported flag")
        env = load_env_file(args.env_file)
        config = build_config(
            mode=args.mode,
            output_dir=args.output_dir,
            timeout_sec=args.timeout_sec,
            approve_external_action=args.approve_external_action,
            bot_username=args.bot_username,
            tg_api_id=args.tg_api_id,
            tg_api_hash=args.tg_api_hash,
            tg_phone=args.tg_phone,
            tg_proxy_url=args.tg_proxy_url,
            tg_code=args.tg_code,
            tg_password=args.tg_password,
            session_file=args.session_file,
            prompt=args.prompt,
            max_steps=args.max_steps,
            delay_sec=args.delay_sec,
            env=env,
        )
        result = run(config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("error: Telegram E2E run failed", file=sys.stderr)
        return 1

    print(f"run_id: {result['run_id']}")
    print(f"events: {result['events_path']}")
    print(f"summary: {result['summary_path']}")
    print(f"events_written: {result['events_written']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
