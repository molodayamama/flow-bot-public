"""Approval-gated MAX transport smoke checks with values-free output."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from channels.base import PlatformFile, PlatformMedia
from channels.max.client import MaxBotClient, MaxConfig, max_config_from_env, validate_max_config


MODES = ("plan", "subscription", "message", "media")


def load_environment(path: str | Path) -> dict[str, str]:
    values = {key: str(value or "") for key, value in dotenv_values(path).items()}
    values.update({key: value for key, value in os.environ.items()})
    return values


def validate_smoke_inputs(
    config: MaxConfig,
    *,
    mode: str,
    approved: bool,
    user_id: str = "",
    chat_id: str = "",
    media_file: str | Path | None = None,
) -> tuple[str, ...]:
    errors: list[str] = []
    if mode not in MODES:
        errors.append("mode is invalid")
    if not config.enabled:
        errors.append("MAX_ENABLED=1 is required")
    try:
        validate_max_config(config)
    except ValueError as exc:
        errors.extend(part.strip() for part in str(exc).split(";") if part.strip())
    if mode != "plan" and not approved:
        errors.append("external mode requires --approve-external-action")
    if mode == "message" and not str(user_id).strip():
        errors.append("MAX_SMOKE_USER_ID or --user-id is required")
    if mode == "media":
        if not str(chat_id).strip():
            errors.append("MAX_SMOKE_CHAT_ID or --chat-id is required")
        if media_file is None or not Path(media_file).is_file():
            errors.append("--media-file must point to a readable file")
    return tuple(dict.fromkeys(errors))


async def run_smoke(
    config: MaxConfig,
    *,
    mode: str,
    user_id: str = "",
    chat_id: str = "",
    media_file: str | Path | None = None,
    client_factory: Callable[..., Any] = MaxBotClient,
) -> str:
    """Run one approved operation and return a values-free success label."""
    if mode == "plan":
        return "plan_ready"
    client = client_factory(
        token=config.bot_token,
        base_url=config.api_base_url,
        ca_bundle=config.ca_bundle,
    )
    try:
        if mode == "subscription":
            await client.get_subscriptions()
            return "subscription_ok"
        if mode == "message":
            await client.send_message_to_user(
                str(user_id),
                "Photozhab MAX transport smoke check. No action is required.",
            )
            return "message_ok"
        if mode == "media":
            path = Path(media_file or "")
            media = PlatformMedia(
                kind="photo",
                bytes_data=path.read_bytes(),
                file=PlatformFile(file_id=path.name),
                caption="Photozhab MAX media transport smoke check.",
            )
            await client.send_photo(str(chat_id), media)
            return "media_ok"
        raise ValueError("mode is invalid")
    finally:
        await client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=MODES, default="plan")
    parser.add_argument("--env-file", default=os.getenv("ENV_FILE", ".env"))
    parser.add_argument("--user-id", default="")
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--media-file")
    parser.add_argument("--approve-external-action", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = load_environment(args.env_file)
    config = max_config_from_env(env)
    user_id = args.user_id or env.get("MAX_SMOKE_USER_ID", "")
    chat_id = args.chat_id or env.get("MAX_SMOKE_CHAT_ID", "")
    errors = validate_smoke_inputs(
        config,
        mode=args.mode,
        approved=args.approve_external_action,
        user_id=user_id,
        chat_id=chat_id,
        media_file=args.media_file,
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2
    try:
        result = asyncio.run(
            run_smoke(
                config,
                mode=args.mode,
                user_id=user_id,
                chat_id=chat_id,
                media_file=args.media_file,
            )
        )
    except Exception as exc:
        print(
            f"MAX smoke failed: type={exc.__class__.__name__}",
            file=sys.stderr,
        )
        return 1
    print(f"MAX smoke passed: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
