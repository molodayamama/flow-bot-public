"""Simple live bot tester — prints each bot response as it arrives.

Usage:
    python live_test.py --prompt "cute cat"
    python live_test.py --smoke-only
    python live_test.py --prompt "cute cat" --timeout 180
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# Force utf-8 stdout so Russian/emoji text doesn't crash on cp1251 terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from tg_e2e.config import TelegramE2EConfig, build_config
from tg_e2e.env_file import load_env_file


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _print(tag: str, text: str) -> None:
    print(f"[{_ts()}] {tag} {text}", flush=True)


async def _build_client(config: TelegramE2EConfig):
    try:
        from telethon import TelegramClient, connection
    except ImportError:
        print("ERROR: telethon not installed — run: pip install telethon", file=sys.stderr)
        sys.exit(1)

    assert config.session_file is not None
    config.session_file.parent.mkdir(parents=True, exist_ok=True)

    kwargs: dict = {}
    if config.mtproxy is not None:
        kwargs["connection"] = connection.ConnectionTcpMTProxyRandomizedIntermediate
        kwargs["proxy"] = (config.mtproxy.host, config.mtproxy.port, config.mtproxy.secret)
        _print("PROXY", f"MTProxy {config.mtproxy.host}:{config.mtproxy.port}")

    client = TelegramClient(
        str(config.session_file),
        config.tg_api_id,
        config.tg_api_hash,
        **kwargs,
    )
    await client.connect()
    return client


async def _send_and_watch(
    client,
    entity,
    text: str,
    *,
    timeout_sec: int,
    expect_photo: bool = False,
) -> tuple[int, int]:
    """Send *text*, then poll and print every new message until done.

    Returns (text_count, photo_count).
    """
    _print(">>>", repr(text))
    sent = await client.send_message(entity, text)
    min_id: int = int(sent.id)

    seen: set[int] = set()
    texts: list[str] = []
    photos = 0
    deadline = asyncio.get_running_loop().time() + timeout_sec
    quiet = 0

    while asyncio.get_running_loop().time() < deadline:
        new_this_round = False
        try:
            async for msg in client.iter_messages(entity, min_id=min_id, reverse=True, limit=30):
                mid = int(getattr(msg, "id", 0) or 0)
                if mid in seen:
                    continue
                seen.add(mid)
                new_this_round = True

                body: str = getattr(msg, "message", None) or ""
                has_photo = getattr(msg, "photo", None) is not None

                if has_photo:
                    photos += 1
                    _print("<<<", f"PHOTO #{photos}" + (f" | {body[:80]}" if body else ""))
                elif body:
                    texts.append(body)
                    snippet = body[:400].replace("\n", " ")
                    _print("<<<", f"TEXT  | {snippet}")
        except Exception as exc:
            _print("WARN", f"poll error: {exc!r}")

        combined = " ".join(texts).lower()

        # Terminal conditions
        if expect_photo and photos >= 1:
            break
        if not expect_photo and texts and quiet >= 2:
            break
        # Error / stop signals from bot text
        terminal_words = ("ошибк", "error", "429", "cooldown", "подожд", "подождите")
        if any(w in combined for w in terminal_words):
            break

        quiet = 0 if new_this_round else quiet + 1
        await asyncio.sleep(2)

    elapsed = timeout_sec - max(0, int(deadline - asyncio.get_running_loop().time()))
    _print("---", f"done in ~{elapsed}s | texts={len(texts)} photos={photos}")
    return len(texts), photos


async def main(args: argparse.Namespace) -> int:
    env = load_env_file(args.env_file)
    mode = "telegram-smoke" if args.smoke_only else "telegram-generation"
    prompt = None if args.smoke_only else args.prompt

    try:
        config = build_config(
            mode=mode,
            output_dir="live_test_runs",
            timeout_sec=args.timeout,
            approve_external_action=True,
            prompt=prompt,
            env=env,
        )
    except Exception as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    _print("INFO", f"mode={mode}  bot={config.bot_username}  timeout={args.timeout}s")

    client = await _build_client(config)
    try:
        if not await client.is_user_authorized():
            _print("INFO", "not authorized — run telegram-login first")
            return 3

        entity = await client.get_entity(config.bot_username)
        _print("INFO", f"entity resolved: {config.bot_username}")

        # Smoke: /start + /status
        _print("=====", "SMOKE")
        await _send_and_watch(client, entity, "/start", timeout_sec=15)
        await asyncio.sleep(1)
        await _send_and_watch(client, entity, "/status", timeout_sec=15)

        if args.smoke_only:
            _print("=====", "SMOKE DONE")
            return 0

        # Generation
        await asyncio.sleep(2)
        _print("=====", "GENERATION")
        _, photos = await _send_and_watch(
            client, entity, f"/one {args.prompt}",
            timeout_sec=args.timeout,
            expect_photo=True,
        )

        if photos >= 1:
            _print("=====", f"SUCCESS — {photos} photo(s) received")
            return 0
        else:
            _print("=====", "FAILED — no photos received within timeout")
            return 1

    finally:
        await client.disconnect()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Live bot tester — prints raw bot responses")
    p.add_argument("--prompt", default="cute kitten", help="generation prompt")
    p.add_argument("--smoke-only", action="store_true", help="only /start + /status, skip generation")
    p.add_argument("--timeout", type=int, default=180, help="generation timeout in seconds")
    p.add_argument("--env-file", default=".env", help="path to .env file")
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(main(args)))
