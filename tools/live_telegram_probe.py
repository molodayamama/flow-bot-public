from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tg_e2e.config import build_config
from tg_e2e.env_file import load_env_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Approved live Telegram probe for short text/photo bot checks.",
        allow_abbrev=False,
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--bot-username", default=None)
    parser.add_argument("--session-file", default=None)
    parser.add_argument("--timeout-sec", type=int, default=90)
    parser.add_argument("--text")
    parser.add_argument("--photo")
    parser.add_argument("--caption", default="")
    parser.add_argument("--recent", type=int, default=0)
    parser.add_argument("--text-limit", type=int, default=2400)
    parser.add_argument(
        "--idle-sec",
        type=int,
        default=4,
        help="Seconds with no new bot messages before returning collected output.",
    )
    parser.add_argument("--click-text")
    parser.add_argument("--click-data")
    parser.add_argument("--click-message-id", type=int)
    return parser


async def main_async(args: argparse.Namespace) -> int:
    if not args.text and not args.photo and args.recent <= 0 and not args.click_text and not args.click_data:
        raise SystemExit("provide --text, --photo, --click-text, --click-data, or --recent")

    env = load_env_file(args.env_file)
    config = build_config(
        mode="telegram-smoke",
        env=env,
        approve_external_action=True,
        bot_username=args.bot_username,
        session_file=args.session_file,
        timeout_sec=max(10, min(int(args.timeout_sec), 600)),
    )

    from telethon import TelegramClient

    assert config.session_file is not None
    assert config.tg_api_id is not None
    assert config.tg_api_hash is not None
    assert config.bot_username is not None

    client = TelegramClient(str(config.session_file), config.tg_api_id, config.tg_api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise SystemExit("telegram session is not authorized")
        entity = await client.get_entity(config.bot_username)

        before = 0
        sent_kind = None
        click_result = None
        clicked_message_after = None
        if args.text:
            sent = await client.send_message(entity, args.text)
            before = int(sent.id)
            sent_kind = "text"
        elif args.photo:
            photo_path = Path(args.photo).resolve()
            if not photo_path.exists():
                raise SystemExit(f"photo not found: {photo_path}")
            sent = await client.send_file(entity, str(photo_path), caption=args.caption or None)
            before = int(sent.id)
            sent_kind = "photo"
        elif args.click_text or args.click_data:
            latest_before_click = await _latest_message_id(client, entity)
            clicked_message, click_result = await _click_button(
                client,
                entity,
                click_text=args.click_text,
                click_data=args.click_data,
                message_id=args.click_message_id,
            )
            before = max(latest_before_click, int(getattr(clicked_message, "id", 0) or 0))
            sent_kind = "click"

        if args.recent > 0 and not sent_kind:
            messages = await _recent(client, entity, args.recent, args.text_limit)
        else:
            messages = await _collect_after(
                client,
                entity,
                before,
                args.timeout_sec,
                args.text_limit,
                idle_sec=args.idle_sec,
            )
            if sent_kind == "click":
                clicked_id = int((click_result or {}).get("message_id") or 0)
                if clicked_id:
                    refreshed = await client.get_messages(entity, ids=clicked_id)
                    if refreshed is not None and not getattr(refreshed, "out", False):
                        clicked_message_after = _summarize_message(refreshed, args.text_limit)

        print(json.dumps({
            "sent_kind": sent_kind or "recent",
            "bot_username": config.bot_username,
            "click_result": click_result,
            "clicked_message_after": clicked_message_after,
            "message_count": len(messages),
            "messages": messages,
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        await client.disconnect()


async def _recent(client: Any, entity: Any, limit: int, text_limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    async for msg in client.iter_messages(entity, limit=max(1, min(limit, 50))):
        rows.append(_summarize_message(msg, text_limit))
    return rows


async def _latest_message_id(client: Any, entity: Any) -> int:
    latest = await client.get_messages(entity, limit=1)
    if not latest:
        return 0
    return int(getattr(latest[0], "id", 0) or 0)


async def _collect_after(
    client: Any,
    entity: Any,
    min_id: int,
    timeout_sec: int,
    text_limit: int,
    *,
    idle_sec: int,
) -> list[dict[str, Any]]:
    deadline = asyncio.get_running_loop().time() + timeout_sec
    seen: set[int] = set()
    rows: list[dict[str, Any]] = []
    idle_deadline: float | None = None
    idle_window = max(1, min(int(idle_sec), int(timeout_sec)))
    while asyncio.get_running_loop().time() < deadline:
        changed = False
        async for msg in client.iter_messages(entity, min_id=min_id, reverse=True, limit=40):
            msg_id = int(getattr(msg, "id", 0) or 0)
            if msg_id in seen or getattr(msg, "out", False):
                continue
            seen.add(msg_id)
            changed = True
            rows.append(_summarize_message(msg, text_limit))
        if rows and changed:
            idle_deadline = asyncio.get_running_loop().time() + idle_window
        elif rows and idle_deadline is not None and asyncio.get_running_loop().time() >= idle_deadline:
            break
        await asyncio.sleep(1)
    return rows


async def _click_button(
    client: Any,
    entity: Any,
    *,
    click_text: str | None,
    click_data: str | None,
    message_id: int | None,
) -> tuple[Any, dict[str, Any]]:
    target = None
    if message_id:
        target = await client.get_messages(entity, ids=message_id)
    else:
        async for msg in client.iter_messages(entity, limit=30):
            if getattr(msg, "out", False):
                continue
            if _message_has_button(msg, click_text=click_text, click_data=click_data):
                target = msg
                break
    if target is None:
        raise SystemExit("button not found")

    rows = getattr(getattr(target, "reply_markup", None), "rows", []) or []
    for row_index, row in enumerate(rows):
        buttons = getattr(row, "buttons", []) or []
        for button_index, btn in enumerate(buttons):
            label = str(getattr(btn, "text", "") or "")
            data = _button_data(btn)
            if _button_matches(label, data, click_text=click_text, click_data=click_data):
                result = await target.click(row_index, button_index)
                return target, {
                    "message_id": int(getattr(target, "id", 0) or 0),
                    "button_text": label[:120],
                    "button_data": data[:160] if data else None,
                    "result_type": result.__class__.__name__ if result is not None else None,
                }
    raise SystemExit("button not found")


def _message_has_button(msg: Any, *, click_text: str | None, click_data: str | None) -> bool:
    rows = getattr(getattr(msg, "reply_markup", None), "rows", []) or []
    for row in rows:
        for btn in getattr(row, "buttons", []) or []:
            if _button_matches(
                str(getattr(btn, "text", "") or ""),
                _button_data(btn),
                click_text=click_text,
                click_data=click_data,
            ):
                return True
    return False


def _button_matches(label: str, data: str | None, *, click_text: str | None, click_data: str | None) -> bool:
    if click_data and data == click_data:
        return True
    if click_text and click_text.casefold() in label.casefold():
        return True
    return False


def _summarize_message(msg: Any, text_limit: int) -> dict[str, Any]:
    text = str(getattr(msg, "message", "") or "")
    limit = max(120, min(int(text_limit), 6000))
    return {
        "id": int(getattr(msg, "id", 0) or 0),
        "out": bool(getattr(msg, "out", False)),
        "text": text[:limit],
        "text_length": len(text),
        "has_photo": getattr(msg, "photo", None) is not None,
        "has_document": getattr(msg, "document", None) is not None,
        "has_video": _has_video(msg),
        "buttons": _buttons(msg),
    }


def _has_video(msg: Any) -> bool:
    document = getattr(msg, "document", None)
    mime = getattr(document, "mime_type", "") if document is not None else ""
    return str(mime).startswith("video/")


def _buttons(msg: Any) -> list[dict[str, Any]]:
    markup = getattr(msg, "reply_markup", None)
    rows = getattr(markup, "rows", []) if markup is not None else []
    buttons: list[dict[str, Any]] = []
    for row in rows:
        for btn in getattr(row, "buttons", []):
            label = getattr(btn, "text", "")
            if label:
                buttons.append({
                    "text": str(label)[:120],
                    "data": (_button_data(btn) or "")[:160] or None,
                })
    return buttons


def _button_data(btn: Any) -> str | None:
    raw = getattr(btn, "data", None)
    if raw is None:
        return None
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return None
    return str(raw)


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
