from __future__ import annotations

"""Approved live Telegram probe that drives the bot's video wizard end-to-end.

The existing ``tools/live_telegram_probe.py`` only performs a single text/photo/
click action per invocation, and ``telegram_bot_tester.py`` has no inline-button
navigation, so neither can exercise the multi-step video wizard. This driver
walks the real prompt-first wizard for one video job and waits for the result.

Text-to-video (Omni) flow:

    /menu -> m:vid -> <prompt text> -> v:ndur:<dur> -> v:ngo -> wait for video

Photo-to-video (Veo, "animate") flow:

    /menu -> m:animate -> <photo> -> <prompt text> -> (v:nqual:<q> ...) -> v:ngo

The Omni model is the duration toggle (4/6/8/10s -> omni-flash-Ns). Veo is only
reachable with a photo; quality cycles lite->fast->quality, so this probe never
clicks toward ``quality`` and refuses ``--model veo-quality`` outright.

It prints a sanitized JSON summary only (status, has_video, timings, bot text
previews). It never prints Telegram ``file_id`` values, secrets, or raw config.

Safety:
- Requires ``--approve-external-action`` (contacts Telegram, makes the bot call
  Google Flow, spends real credits/quota).
- Refuses ``veo-quality``.

Approved usage (run on the host that owns the e2e session, e.g. the VPS):

    .venv/bin/python tools/live_video_probe.py --approve-external-action \
        --model omni-flash-4s \
        --prompt "a calm cinematic sunrise over a quiet lake, gentle mist"
"""

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tg_e2e.config import build_config
from tg_e2e.env_file import load_env_file

# Omni duration (seconds) -> friendly model id (mirrors flow_bot._VID_OMNI_DUR_MODEL).
_OMNI_DUR = {4: "omni-flash-4s", 6: "omni-flash-6s", 8: "omni-flash-8s", 10: "omni-flash-10s"}
_OMNI_MODEL_DUR = {v: k for k, v in _OMNI_DUR.items()}
# Veo quality -> friendly model id (mirrors flow_bot._VID_VEO_QUAL_MODEL).
_VEO_MODEL_QUAL = {"veo-lite": "lite", "veo-fast": "fast"}  # quality intentionally omitted
_BLOCKED_MODELS = {"veo-quality"}
_VEO_QUALITY_CYCLE = ["lite", "fast", "quality"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Approved live Telegram video-wizard driver (one job).",
        allow_abbrev=False,
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--bot-username", default=None)
    parser.add_argument("--session-file", default=None)
    parser.add_argument("--model", required=True,
                        help="omni-flash-4s|6s|8s|10s (text) or veo-lite|veo-fast (needs --photo)")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--photo", default=None, help="image path; required for veo-* models")
    parser.add_argument("--step-timeout-sec", type=int, default=45,
                        help="Max wait for a wizard message to appear/update.")
    parser.add_argument("--gen-timeout-sec", type=int, default=300,
                        help="Max wait for the video result after pressing Create.")
    parser.add_argument("--idle-sec", type=int, default=10)
    parser.add_argument("--text-limit", type=int, default=600)
    parser.add_argument("--approve-external-action", action="store_true")
    return parser


# ── message helpers ──────────────────────────────────────────────────────────

def _summarize(msg: Any, text_limit: int) -> dict[str, Any]:
    text = str(getattr(msg, "message", "") or "")
    limit = max(120, min(int(text_limit), 4000))
    return {
        "id": int(getattr(msg, "id", 0) or 0),
        "out": bool(getattr(msg, "out", False)),
        "text": text[:limit],
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
    out: list[dict[str, Any]] = []
    for row in rows:
        for btn in getattr(row, "buttons", []) or []:
            out.append({"text": str(getattr(btn, "text", "") or "")[:90], "data": _button_data(btn)})
    return out


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


def _find_button(msg: Any, data: str) -> tuple[int, int] | None:
    rows = getattr(getattr(msg, "reply_markup", None), "rows", []) or []
    for r, row in enumerate(rows):
        for c, btn in enumerate(getattr(row, "buttons", []) or []):
            if _button_data(btn) == data:
                return r, c
    return None


async def _wait_new_with_button(client: Any, entity: Any, after_id: int, data: str,
                                timeout_sec: int) -> Any:
    """Wait for a NEW bot message (id > after_id) carrying button ``data``."""
    deadline = asyncio.get_running_loop().time() + timeout_sec
    last_buttons: list[dict[str, Any]] = []
    while asyncio.get_running_loop().time() < deadline:
        async for msg in client.iter_messages(entity, min_id=after_id, reverse=True, limit=20):
            if getattr(msg, "out", False):
                continue
            last_buttons = _buttons(msg)
            if _find_button(msg, data) is not None:
                return msg
        await asyncio.sleep(1)
    raise SystemExit(f"no bot message with button {data!r} within {timeout_sec}s; "
                     f"last buttons={last_buttons}")


async def _click(client: Any, entity: Any, message_id: int, data: str, timeout_sec: int) -> dict[str, Any]:
    """Re-fetch ``message_id`` until it shows button ``data`` then click it."""
    deadline = asyncio.get_running_loop().time() + timeout_sec
    last = None
    while asyncio.get_running_loop().time() < deadline:
        last = await client.get_messages(entity, ids=message_id)
        pos = _find_button(last, data) if last is not None else None
        if pos is not None:
            await last.click(pos[0], pos[1])
            return {"clicked": data}
        await asyncio.sleep(1)
    raise SystemExit(f"button {data!r} not on message {message_id} within {timeout_sec}s; "
                     f"last buttons={_buttons(last) if last is not None else None}")


async def _collect_video(client: Any, entity: Any, min_id: int, timeout_sec: int,
                         idle_sec: int, text_limit: int) -> tuple[list[dict[str, Any]], bool]:
    deadline = asyncio.get_running_loop().time() + timeout_sec
    seen: set[int] = set()
    rows: list[dict[str, Any]] = []
    got_video = False
    while asyncio.get_running_loop().time() < deadline:
        async for msg in client.iter_messages(entity, min_id=min_id, reverse=True, limit=40):
            mid = int(getattr(msg, "id", 0) or 0)
            if mid in seen or getattr(msg, "out", False):
                continue
            seen.add(mid)
            summary = _summarize(msg, text_limit)
            rows.append(summary)
            if summary["has_video"]:
                got_video = True
        if got_video:
            break
        await asyncio.sleep(2)
    return rows, got_video


# ── main ─────────────────────────────────────────────────────────────────────

async def main_async(args: argparse.Namespace) -> int:
    if not args.approve_external_action:
        raise SystemExit("refusing to run without --approve-external-action")
    model = (args.model or "").strip().lower()
    if model in _BLOCKED_MODELS:
        raise SystemExit(f"model {model!r} is blocked by this probe (operator constraint)")

    is_veo = model in _VEO_MODEL_QUAL
    is_omni = model in _OMNI_MODEL_DUR
    if not (is_veo or is_omni):
        raise SystemExit(f"unknown/disallowed model {model!r}; "
                         f"allowed: {sorted(_OMNI_MODEL_DUR) + sorted(_VEO_MODEL_QUAL)}")
    if is_veo and not args.photo:
        raise SystemExit(f"model {model!r} needs a photo; pass --photo <path>")

    env = load_env_file(args.env_file)
    config = build_config(
        mode="telegram-smoke", env=env, approve_external_action=True,
        bot_username=args.bot_username, session_file=args.session_file,
        timeout_sec=max(10, min(int(args.gen_timeout_sec), 600)),
    )

    from telethon import TelegramClient

    assert config.session_file is not None
    assert config.tg_api_id is not None and config.tg_api_hash is not None
    assert config.bot_username is not None

    client = TelegramClient(str(config.session_file), config.tg_api_id, config.tg_api_hash)
    await client.connect()
    started = time.perf_counter()
    steps: list[dict[str, Any]] = []
    try:
        if not await client.is_user_authorized():
            raise SystemExit("telegram session is not authorized")
        me = await client.get_me()
        entity = await client.get_entity(config.bot_username)

        # Both Omni (text) and Veo (photo) go through the prompt-first m:vid entry.
        # Attaching a photo on that screen switches the wizard to Veo automatically.
        menu = await client.send_message(entity, "/menu")
        menu_msg = await _wait_new_with_button(client, entity, menu.id, "m:vid", args.step_timeout_sec)
        steps.append(await _click(client, entity, int(menu_msg.id), "m:vid", args.step_timeout_sec))

        if is_veo:
            photo_path = Path(args.photo).resolve()
            if not photo_path.exists():
                raise SystemExit(f"photo not found: {photo_path}")
            # Caption carries the prompt; the photo flips the wizard to Veo.
            sent = await client.send_file(entity, str(photo_path), caption=args.prompt)
            steps.append({"step": "photo_sent", "message_id": int(sent.id)})
        else:
            sent = await client.send_message(entity, args.prompt)
            steps.append({"step": "prompt_sent", "message_id": int(sent.id)})

        # The new-wizard settings panel arrives as a fresh bot message with v:ngo.
        wiz = await _wait_new_with_button(client, entity, int(sent.id), "v:ngo", args.step_timeout_sec)
        wiz_id = int(wiz.id)
        steps.append({"step": "wizard", "message_id": wiz_id})

        if is_omni:
            dur = _OMNI_MODEL_DUR[model]
            steps.append(await _click(client, entity, wiz_id, f"v:ndur:{dur}", args.step_timeout_sec))
        else:
            # Veo: default quality is lite. Click toward fast only if requested;
            # never click toward quality.
            target_q = _VEO_MODEL_QUAL[model]
            cur = "lite"
            guard = 0
            while cur != target_q and guard < 2:
                nxt = _VEO_QUALITY_CYCLE[(_VEO_QUALITY_CYCLE.index(cur) + 1) % len(_VEO_QUALITY_CYCLE)]
                if nxt == "quality":
                    raise SystemExit("refusing to cycle Veo quality through 'quality'")
                steps.append(await _click(client, entity, wiz_id, f"v:nqual:{nxt}", args.step_timeout_sec))
                cur = nxt
                guard += 1

        steps.append(await _click(client, entity, wiz_id, "v:ngo", args.step_timeout_sec))

        messages, got_video = await _collect_video(
            client, entity, wiz_id, args.gen_timeout_sec, args.idle_sec, args.text_limit,
        )

        print(json.dumps({
            "acting_user_id": int(getattr(me, "id", 0) or 0),
            "bot_username": config.bot_username,
            "model": model,
            "mode": "veo_photo" if is_veo else "omni_text",
            "result": "video" if got_video else "no_video",
            "elapsed_sec": round(time.perf_counter() - started, 1),
            "steps": steps,
            "messages": messages,
        }, ensure_ascii=False, indent=2))
        return 0 if got_video else 2
    finally:
        await client.disconnect()


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
