from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tg_e2e.config import ConfigError, build_config, validate_no_dangerous_flags  # noqa: E402
from tg_e2e.env_file import load_env_file  # noqa: E402
from tg_e2e.telethon_client import make_telethon_client  # noqa: E402


DEFAULT_CHATS = (
    "chat_infographics",
    "designers_wb_ozon",
    "dizainer_wb",
    "wbnahodkychat",
    "marketplaces_chat",
    "MP_partner",
    "sellery_ozon",
    "wildberries_service",
    "xb_prosmm_chat",
    "neyroseti_chat",
)

SEARCH_TERMS = (
    "\u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0430",
    "\u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0438 \u0442\u043e\u0432\u0430\u0440\u0430",
    "\u0438\u043d\u0444\u043e\u0433\u0440\u0430\u0444\u0438\u043a\u0430",
    "\u0444\u043e\u0442\u043e \u0442\u043e\u0432\u0430\u0440\u0430",
    "\u0434\u0438\u0437\u0430\u0439\u043d",
    "\u0432\u0438\u0434\u0435\u043e \u0442\u043e\u0432\u0430\u0440\u0430",
    "\u0432\u0438\u0434\u0435\u043e \u0434\u043b\u044f \u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0438",
    "\u0440\u0438\u043b\u0441",
    "reels",
    "ugc",
    "\u043d\u0435\u0439\u0440\u043e\u0441\u0435\u0442\u044c",
    "\u043d\u0435\u0439\u0440\u043e\u0441\u0435\u0442\u0438",
    "\u043e\u0436\u0438\u0432\u0438\u0442\u044c \u0444\u043e\u0442\u043e",
    "veo",
    "kling",
)

DEMAND_HINTS = (
    "\u043f\u043e\u0434\u0441\u043a\u0430\u0436",
    "\u043f\u043e\u0441\u043e\u0432\u0435\u0442",
    "\u043a\u0442\u043e \u0434\u0435\u043b\u0430\u0435\u0442",
    "\u043a\u0442\u043e \u043c\u043e\u0436\u0435\u0442",
    "\u0435\u0441\u0442\u044c \u043a\u0442\u043e",
    "\u0435\u0441\u0442\u044c \u0441\u043f\u0435\u0446",
    "\u0438\u0449\u0443",
    "\u0438\u0449\u0435\u043c",
    "\u0438\u0449\u0435\u0442",
    "\u043d\u0443\u0436\u0435\u043d",
    "\u043d\u0443\u0436\u043d\u0430",
    "\u043d\u0443\u0436\u043d\u043e",
    "\u043d\u0430\u0434\u043e",
    "\u0442\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044f",
    "\u0433\u0434\u0435 \u043c\u043e\u0436\u043d\u043e",
    "\u043a\u0430\u043a \u0441\u0434\u0435\u043b\u0430\u0442\u044c",
    "\u043a\u0430\u043a\u043e\u0439 \u0441\u0435\u0440\u0432\u0438\u0441",
    "\u043a\u0430\u043a\u0430\u044f \u043d\u0435\u0439\u0440\u043e",
    "\u043d\u0435 \u043c\u043e\u0433\u0443",
    "\u043d\u0435 \u043f\u043e\u043b\u0443\u0447\u0430\u0435\u0442\u0441\u044f",
    "\u043f\u043e\u043c\u043e\u0447\u044c",
    "\u043f\u043e\u043c\u043e\u0433\u0438\u0442\u0435",
    "\u0437\u0430\u043a\u0430\u0437\u0430\u0442\u044c",
    "\u0434\u043e\u0433\u043e\u0432\u043e\u0440\u0438\u043c\u0441\u044f",
)

HARD_REQUEST_HINTS = (
    "\u043f\u043e\u0434\u0441\u043a\u0430\u0436",
    "\u043f\u043e\u0441\u043e\u0432\u0435\u0442",
    "\u043a\u0442\u043e \u0434\u0435\u043b\u0430\u0435\u0442",
    "\u043a\u0442\u043e \u043c\u043e\u0436\u0435\u0442",
    "\u043a\u0442\u043e \u043f\u043e\u0434\u0441\u043a\u0430\u0436",
    "\u0435\u0441\u0442\u044c \u043a\u0442\u043e",
    "\u0435\u0441\u0442\u044c \u0441\u043f\u0435\u0446",
    "\u0438\u0449\u0443",
    "\u0438\u0449\u0435\u043c",
    "\u0438\u0449\u0435\u0442",
    "\u043d\u0443\u0436\u0435\u043d",
    "\u043d\u0443\u0436\u043d\u0430",
    "\u043d\u0443\u0436\u043d\u043e",
    "\u043d\u0430\u0434\u043e",
    "\u0442\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044f",
    "\u0433\u0434\u0435 \u043c\u043e\u0436\u043d\u043e",
    "\u0433\u0434\u0435 \u0437\u0430\u043a\u0430\u0437",
    "\u043a\u0430\u043a \u0441\u0434\u0435\u043b\u0430\u0442\u044c",
    "\u043a\u0430\u043a\u043e\u0439 \u0441\u0435\u0440\u0432\u0438\u0441",
    "\u043a\u0430\u043a\u0430\u044f \u043d\u0435\u0439\u0440\u043e",
    "\u043d\u0435 \u043c\u043e\u0433\u0443",
    "\u043d\u0435 \u043f\u043e\u043b\u0443\u0447\u0430\u0435\u0442\u0441\u044f",
    "\u043f\u043e\u043c\u043e\u0433\u0438\u0442\u0435",
)

TOPIC_HINTS = (
    "\u043a\u0430\u0440\u0442\u043e\u0447",
    "\u0438\u043d\u0444\u043e\u0433\u0440\u0430\u0444",
    "\u0434\u0438\u0437\u0430\u0439\u043d",
    "\u0444\u043e\u0442\u043e",
    "\u0444\u043e\u043d",
    "\u0441\u043b\u0430\u0439\u0434",
    "\u0442\u043e\u0432\u0430\u0440",
    "\u0432\u0438\u0434\u0435\u043e",
    "\u0440\u0438\u043b\u0441",
    "reels",
    "ugc",
    "\u043e\u0431\u043b\u043e\u0436",
    "\u043e\u0436\u0438\u0432",
    "\u0430\u043d\u0438\u043c\u0430\u0446",
    "\u043d\u0435\u0439\u0440\u043e",
    "\u0438\u0438",
    "ai",
    "veo",
    "kling",
)

SUPPLY_HINTS = (
    "\u0441\u043e\u0437\u0434\u0430\u044e",
    "\u0441\u043e\u0437\u0434\u0430\u0435\u043c",
    "\u0441\u0434\u0435\u043b\u0430\u044e",
    "\u043f\u043e\u043c\u043e\u0433\u0443",
    "\u043f\u0440\u0435\u0434\u043b\u0430\u0433\u0430\u044e",
    "\u0443\u0441\u043b\u0443\u0433",
    "\u043f\u043e\u0440\u0442\u0444\u043e\u043b\u0438\u043e",
    "\u043f\u0440\u0430\u0439\u0441",
    "\u043f\u0438\u0448\u0438\u0442\u0435",
    "\u0441\u043a\u0438\u043d\u0443",
    "\u0437\u0430\u043f\u0443\u0441\u0442\u0438\u043b\u0430\u0441\u044c",
    "\u0431\u043e\u0442",
    "\u0441\u0435\u0440\u0432\u0438\u0441",
)

SUPPLY_ONLY_HINTS = (
    "\u0441\u043e\u0437\u0434\u0430\u044e",
    "\u0441\u043e\u0437\u0434\u0430\u0435\u043c",
    "\u044f \u0441\u043e\u0437\u0434\u0430\u044e",
    "\u0441\u043e\u0437\u0434\u0430\u043c",
    "\u0434\u0435\u043b\u0430\u044e",
    "\u044f \u0434\u0435\u043b\u0430\u044e",
    "\u0441\u0434\u0435\u043b\u0430\u044e",
    "\u0440\u0430\u0437\u0440\u0430\u0431\u043e\u0442\u0430\u044e",
    "\u043f\u043e\u043c\u043e\u0433\u0443",
    "\u043f\u0440\u0435\u0434\u043b\u0430\u0433\u0430\u044e",
    "\u0443\u0441\u043b\u0443\u0433",
    "\u043f\u0440\u0430\u0439\u0441",
    "\u043c\u043e\u0438 \u0440\u0430\u0431\u043e\u0442\u044b",
    "\u043c\u043e\u0435 \u043f\u043e\u0440\u0442\u0444\u043e\u043b\u0438\u043e",
    "\u044f \u0434\u0438\u0437\u0430\u0439\u043d\u0435\u0440",
    "\u0440\u0430\u0431\u043e\u0442\u0430\u044e \u0441",
    "\u0441\u0442\u043e\u0438\u043c\u043e\u0441\u0442\u044c",
    "\u0441\u043a\u0438\u0434\u043a",
    "\u0441\u0440\u043e\u043a \u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u0438\u044f",
    "\u0434\u043b\u044f \u043e\u0444\u043e\u0440\u043c\u043b\u0435\u043d\u0438\u044f \u0437\u0430\u043a\u0430\u0437\u0430",
    "\u043f\u043e\u0434 \u0437\u0430\u043a\u0430\u0437",
    "\u0437\u0430\u043a\u0430\u0437\u0430\u0442\u044c",
    "\u043a\u043e\u043c\u0443 \u043d\u0443\u0436\u043d",
    "\u0432\u044b \u043f\u043e \u0430\u0434\u0440\u0435\u0441\u0443",
    "\u043f\u0440\u043e\u0444\u0435\u0441\u0441\u0438\u043e\u043d\u0430\u043b\u044c\u043d\u043e \u0437\u0430\u043d\u0438\u043c\u0430\u044e\u0441\u044c",
)

NOISE_HINTS = (
    "\u0441\u0435\u0440\u0442\u0438\u0444\u0438\u043a\u0430\u0442",
    "\u043c\u0430\u0440\u043a\u0438\u0440\u043e\u0432",
    "\u0431\u0430\u0440\u043a\u043e\u0434",
    "\u043f\u043e\u0441\u0442\u0430\u0432\u043a",
    "\u0444\u0443\u043b\u0444\u0438\u043b\u043c",
    "\u043b\u043e\u0433\u0438\u0441\u0442",
    "\u0432\u044b\u043a\u0443\u043f",
    "\u0431\u0430\u0439\u0435\u0440",
    "\u043d\u0434\u0441",
    "\u0448\u0442\u0440\u0438\u0445",
    "\u043f\u043e\u0438\u0441\u043a \u043a\u043b\u0438\u0435\u043d\u0442",
    "\u0438\u0449\u0443 \u0440\u0430\u0431\u043e\u0442",
    "\u0432\u0430\u043a\u0430\u043d\u0441\u0438",
)

CATEGORY_HINTS = {
    "product_card": (
        "\u043a\u0430\u0440\u0442\u043e\u0447",
        "\u0438\u043d\u0444\u043e\u0433\u0440\u0430\u0444",
        "\u0441\u043b\u0430\u0439\u0434",
        "\u0434\u0438\u0437\u0430\u0439\u043d",
    ),
    "product_photo": (
        "\u0444\u043e\u0442\u043e \u0442\u043e\u0432\u0430\u0440",
        "\u0431\u0435\u043b\u044b\u0439 \u0444\u043e\u043d",
        "\u0444\u043e\u043d",
        "\u043d\u0435\u0439\u0440\u043e\u0444\u043e\u0442\u043e",
    ),
    "short_video": (
        "\u0432\u0438\u0434\u0435\u043e",
        "\u0440\u0438\u043b\u0441",
        "reels",
        "ugc",
        "\u043e\u0431\u043b\u043e\u0436",
    ),
    "photo_animation": (
        "\u043e\u0436\u0438\u0432",
        "\u0430\u043d\u0438\u043c\u0430\u0446",
        "veo",
        "kling",
    ),
    "ai_tool": (
        "\u043d\u0435\u0439\u0440\u043e",
        "\u0438\u0438",
        "ai",
        "gpt",
    ),
}


@dataclass
class Sample:
    date: str
    category: str
    keyword: str
    snippet: str
    link: str | None = None


@dataclass
class ChatStats:
    chat: str
    link: str
    title: str | None = None
    participants: int | None = None
    about: str | None = None
    scanned_messages: int = 0
    keyword_matches: int = 0
    direct_pain_messages: int = 0
    unique_pain_authors: int = 0
    supply_messages: int = 0
    noise_messages: int = 0
    category_counts: dict[str, int] = field(default_factory=dict)
    samples: list[Sample] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only Telegram pain scanner for Photozhab lead research.",
        allow_abbrev=False,
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output-dir", default="lead_scan_runs")
    parser.add_argument("--session-file")
    parser.add_argument("--tg-api-id")
    parser.add_argument("--tg-api-hash")
    parser.add_argument("--tg-phone")
    parser.add_argument("--tg-proxy-url")
    parser.add_argument("--approve-external-action", action="store_true")
    parser.add_argument("--chat", action="append", dest="chats")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--limit-per-term", type=int, default=80)
    parser.add_argument("--max-samples-per-chat", type=int, default=8)
    parser.add_argument("--json-name", default="pain_scan.json")
    parser.add_argument("--markdown-name", default="pain_scan.md")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, unknown = parser.parse_known_args(argv)
    try:
        validate_no_dangerous_flags(unknown)
        if unknown:
            raise ConfigError("unsupported flag")
        if not args.approve_external_action:
            raise ConfigError("read-only Telegram scan requires --approve-external-action")
        if args.days < 1 or args.days > 365:
            raise ConfigError("--days must be between 1 and 365")
        if args.limit_per_term < 1 or args.limit_per_term > 500:
            raise ConfigError("--limit-per-term must be between 1 and 500")
        if args.max_samples_per_chat < 0 or args.max_samples_per_chat > 50:
            raise ConfigError("--max-samples-per-chat must be between 0 and 50")

        env = load_env_file(args.env_file)
        config = build_config(
            mode="telegram-login",
            output_dir=args.output_dir,
            approve_external_action=True,
            tg_api_id=args.tg_api_id,
            tg_api_hash=args.tg_api_hash,
            tg_phone=args.tg_phone,
            tg_proxy_url=args.tg_proxy_url,
            session_file=args.session_file,
            env=env,
        )
        chats = tuple(args.chats or DEFAULT_CHATS)
        result = asyncio.run(
            scan_chats(
                config=config,
                chats=chats,
                days=args.days,
                limit_per_term=args.limit_per_term,
                max_samples_per_chat=args.max_samples_per_chat,
            ),
        )

        out_dir = Path(args.output_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / args.json_name
        markdown_path = out_dir / args.markdown_name
        json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(render_markdown(result), encoding="utf-8")
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: lead pain scan failed: {exc.__class__.__name__}", file=sys.stderr)
        return 1

    totals = result["totals"]
    print(f"json: {json_path}")
    print(f"markdown: {markdown_path}")
    print(f"direct_pain_messages: {totals['direct_pain_messages']}")
    print(f"unique_pain_authors: {totals['unique_pain_authors']}")
    print(f"supply_messages: {totals['supply_messages']}")
    return 0


async def scan_chats(
    *,
    config: Any,
    chats: tuple[str, ...],
    days: int,
    limit_per_term: int,
    max_samples_per_chat: int,
) -> dict[str, Any]:
    wrapper = make_telethon_client(config)
    client = await wrapper._get_client()
    await wrapper.ensure_authorized()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[ChatStats] = []
    global_author_keys: set[tuple[str, int]] = set()

    try:
        for chat in chats:
            stats = await scan_one_chat(
                client=client,
                chat=chat,
                since=since,
                limit_per_term=limit_per_term,
                max_samples=max_samples_per_chat,
            )
            rows.append(stats)
            for key in getattr(stats, "_author_keys", set()):
                global_author_keys.add((stats.chat, key))
            await asyncio.sleep(0.6)
    finally:
        await wrapper.close()

    serial_rows = [serialize_chat_stats(row) for row in rows]
    serial_rows.sort(
        key=lambda row: (
            row["direct_pain_messages"],
            row["unique_pain_authors"],
            row["supply_messages"],
        ),
        reverse=True,
    )
    totals = {
        "chats_scanned": len(serial_rows),
        "keyword_matches": sum(row["keyword_matches"] for row in serial_rows),
        "direct_pain_messages": sum(row["direct_pain_messages"] for row in serial_rows),
        "unique_pain_authors": len(global_author_keys),
        "supply_messages": sum(row["supply_messages"] for row in serial_rows),
        "noise_messages": sum(row["noise_messages"] for row in serial_rows),
    }
    category_totals: dict[str, int] = {}
    for row in serial_rows:
        for category, count in row["category_counts"].items():
            category_totals[category] = category_totals.get(category, 0) + int(count)
    totals["category_counts"] = dict(sorted(category_totals.items(), key=lambda item: item[1], reverse=True))

    return {
        "generated_at": generated_at,
        "scope": {
            "days": days,
            "since": since.isoformat(timespec="seconds"),
            "search_terms": list(SEARCH_TERMS),
            "privacy": (
                "Counts unique authors in memory only. Sender ids, usernames, and "
                "contact lists are not exported."
            ),
        },
        "totals": totals,
        "chats": serial_rows,
    }


async def scan_one_chat(
    *,
    client: Any,
    chat: str,
    since: datetime,
    limit_per_term: int,
    max_samples: int,
) -> ChatStats:
    stats = ChatStats(chat=chat, link=f"https://t.me/{chat}")
    seen_messages: set[int] = set()
    author_keys: set[int] = set()
    setattr(stats, "_author_keys", author_keys)
    try:
        entity = await client.get_entity(chat)
        stats.title = getattr(entity, "title", None) or chat
        stats.participants = getattr(entity, "participants_count", None)
        try:
            from telethon import functions

            full = await client(functions.channels.GetFullChannelRequest(entity))
            stats.participants = getattr(full.full_chat, "participants_count", None) or stats.participants
            stats.about = clean_text(getattr(full.full_chat, "about", None) or "", limit=280)
        except Exception as exc:
            stats.errors.append(f"full_chat:{exc.__class__.__name__}")

        for term in SEARCH_TERMS:
            try:
                async for message in client.iter_messages(entity, search=term, limit=limit_per_term):
                    date = getattr(message, "date", None)
                    if date is not None and date < since:
                        break
                    message_id = int(getattr(message, "id", 0) or 0)
                    if not message_id or message_id in seen_messages:
                        continue
                    seen_messages.add(message_id)
                    body = getattr(message, "message", "") or ""
                    if not body.strip():
                        continue
                    stats.keyword_matches += 1
                    classification = classify_message(body)
                    if classification["noise"]:
                        stats.noise_messages += 1
                    if classification["supply"]:
                        stats.supply_messages += 1
                    if classification["pain"]:
                        stats.direct_pain_messages += 1
                        sender_id = getattr(message, "sender_id", None)
                        if isinstance(sender_id, int):
                            author_keys.add(sender_id)
                        for category in classification["categories"]:
                            stats.category_counts[category] = stats.category_counts.get(category, 0) + 1
                        if len(stats.samples) < max_samples:
                            link = None
                            username = getattr(entity, "username", None)
                            if username:
                                link = f"https://t.me/{username}/{message_id}"
                            stats.samples.append(
                                Sample(
                                    date=date.date().isoformat() if date else "",
                                    category=", ".join(classification["categories"]) or "uncategorized",
                                    keyword=term,
                                    snippet=clean_text(body),
                                    link=link,
                                ),
                            )
            except Exception as exc:
                stats.errors.append(f"search:{term}:{exc.__class__.__name__}")
            await asyncio.sleep(0.15)
    except Exception as exc:
        stats.errors.append(f"chat:{exc.__class__.__name__}")
    stats.scanned_messages = len(seen_messages)
    stats.unique_pain_authors = len(author_keys)
    return stats


def classify_message(text: str) -> dict[str, Any]:
    lowered = normalize(text)
    has_demand = any(hint in lowered for hint in DEMAND_HINTS)
    has_hard_request = any(hint in lowered for hint in HARD_REQUEST_HINTS)
    has_question = "?" in lowered
    has_topic = any(hint in lowered for hint in TOPIC_HINTS)
    has_supply = any(hint in lowered for hint in SUPPLY_HINTS)
    has_supply_only = any(hint in lowered for hint in SUPPLY_ONLY_HINTS)
    has_noise = any(hint in lowered for hint in NOISE_HINTS)
    categories = [
        category
        for category, hints in CATEGORY_HINTS.items()
        if any(hint in lowered for hint in hints)
    ]
    # Count real demand conservatively: supply ads often contain rhetorical questions.
    pain = has_topic and not has_supply_only and not has_noise and (
        has_hard_request or (has_question and has_demand and not has_supply)
    )
    return {
        "pain": pain,
        "supply": (has_supply or has_supply_only) and has_topic,
        "noise": has_noise and not pain,
        "categories": categories or ["uncategorized"],
    }


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def clean_text(text: str, *, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"@\w+", "@...", text)
    text = re.sub(r"\+?\d[\d\s().-]{7,}\d", "[phone]", text)
    return text[:limit]


def serialize_chat_stats(stats: ChatStats) -> dict[str, Any]:
    return {
        "chat": stats.chat,
        "title": stats.title,
        "link": stats.link,
        "participants": stats.participants,
        "about": stats.about,
        "scanned_messages": stats.scanned_messages,
        "keyword_matches": stats.keyword_matches,
        "direct_pain_messages": stats.direct_pain_messages,
        "unique_pain_authors": stats.unique_pain_authors,
        "supply_messages": stats.supply_messages,
        "noise_messages": stats.noise_messages,
        "category_counts": dict(sorted(stats.category_counts.items(), key=lambda item: item[1], reverse=True)),
        "samples": [sample.__dict__ for sample in stats.samples],
        "errors": stats.errors,
    }


def render_markdown(result: dict[str, Any]) -> str:
    totals = result["totals"]
    lines = [
        "# Lead Pain Scan",
        "",
        f"Generated: {result['generated_at']}",
        f"Window: last {result['scope']['days']} days",
        "",
        "Privacy: sender ids, usernames, and contact lists are not exported.",
        "",
        "## Totals",
        "",
        f"- Chats scanned: {totals['chats_scanned']}",
        f"- Keyword-matched public messages: {totals['keyword_matches']}",
        f"- Direct pain messages: {totals['direct_pain_messages']}",
        f"- Unique pain authors: {totals['unique_pain_authors']}",
        f"- Supply / competitor messages: {totals['supply_messages']}",
        "",
        "## Categories",
        "",
    ]
    for category, count in totals["category_counts"].items():
        lines.append(f"- `{category}`: {count}")
    lines.extend(["", "## Chats", ""])
    lines.append("| Chat | Pain msgs | Unique authors | Supply msgs | Top categories |")
    lines.append("|---|---:|---:|---:|---|")
    for row in result["chats"]:
        categories = ", ".join(f"{name}:{count}" for name, count in row["category_counts"].items()) or "-"
        title = row["title"] or row["chat"]
        lines.append(
            f"| [{escape_md(title)}]({row['link']}) | {row['direct_pain_messages']} | "
            f"{row['unique_pain_authors']} | {row['supply_messages']} | {escape_md(categories)} |",
        )
    lines.extend(["", "## Samples", ""])
    for row in result["chats"]:
        if not row["samples"]:
            continue
        lines.append(f"### {row['title'] or row['chat']}")
        for sample in row["samples"]:
            link = f" [message]({sample['link']})" if sample.get("link") else ""
            lines.append(
                f"- {sample['date']} `{sample['category']}` via `{sample['keyword']}`:{link} "
                f"{sample['snippet']}",
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def escape_md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
