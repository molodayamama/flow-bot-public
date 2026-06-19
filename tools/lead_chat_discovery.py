"""Read-only Telegram chat discovery for Photozhab lead research.

Runs global public-search queries and collects candidate seller / marketplace /
design *megagroups* (public groups with a username). Does NOT join, does NOT
export user ids, usernames of members, or message content. Output is a ranked
list of public group usernames the pain scanner can read.

Usage:
    python tools/lead_chat_discovery.py --approve-external-action
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tg_e2e.config import ConfigError, build_config  # noqa: E402
from tg_e2e.env_file import load_env_file  # noqa: E402
from tg_e2e.telethon_client import make_telethon_client  # noqa: E402

# Broad seed queries covering WB/Ozon sellers, marketplaces, infographics,
# product cards, product photo, designers, and "need a designer / done for me".
QUERIES = (
    "маркетплейсы чат",
    "маркетплейсы чат селлеров",
    "маркетплейс чат поставщиков",
    "селлеры wildberries чат",
    "селлеры ozon чат",
    "wildberries поставщики чат",
    "ozon поставщики чат",
    "wb ozon чат",
    "вайлдберриз чат",
    "озон чат продавцов",
    "инфографика маркетплейсы",
    "инфографика wildberries",
    "инфографика чат",
    "дизайнеры wildberries",
    "дизайнеры маркетплейсов",
    "дизайн карточек wb ozon",
    "карточки товара чат",
    "карточки wildberries чат",
    "оформление карточек маркетплейс",
    "ищу дизайнера карточек",
    "нужен дизайнер инфографики",
    "фотоконтент маркетплейсы чат",
    "предметная съемка чат",
    "фото товара чат",
    "товарная фотография",
    "продавцы вайлдберриз чат",
    "поставщики ozon wildberries",
    "селлеры маркетплейсов чат",
    "wb seller чат",
    "ozon seller чат",
    "нейросети для маркетплейсов",
    "ai карточки товаров",
    "контент для маркетплейсов чат",
    "менеджер маркетплейсов чат",
    "товарка чат",
    # Tool-seeker rich: neural-net / AI image / SMM communities.
    "нейросети чат",
    "нейросети для бизнеса чат",
    "нейросети новички чат",
    "ai chat нейросети",
    "midjourney чат",
    "генерация изображений нейросети",
    "нейрофото чат",
    "обработка фото нейросеть",
    "удаление фона чат",
    "smm чат нейросети",
    "контент маркетинг нейросети",
    "нейросети для селлеров",
    "chatgpt чат рус",
    "ai видео нейросети чат",
)

# Title / username substrings that mark a chat as off-topic noise for us.
BAD = (
    "official", "travel", "тревел", "bank", "банк", "путеше",
    "находки халява", "обзор", "акул", "каталог",
    "погод", "крипт", "crypto", "форекс", "trading", "ставк", "casino",
    "знакомств", "18+", "интим", "новост", "news", "юмор", "анекдот",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output-dir", default="lead_scan_runs")
    parser.add_argument("--approve-external-action", action="store_true")
    parser.add_argument("--limit-per-query", type=int, default=50)
    parser.add_argument("--min-participants", type=int, default=300)
    parser.add_argument("--out-name", default="discovered_chats.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.approve_external_action:
        print("error: discovery requires --approve-external-action", file=sys.stderr)
        return 2
    try:
        env = load_env_file(args.env_file)
        config = build_config(
            mode="telegram-login",
            output_dir=args.output_dir,
            approve_external_action=True,
            env=env,
        )
        result = asyncio.run(
            discover(
                config=config,
                limit_per_query=args.limit_per_query,
                min_participants=args.min_participants,
            ),
        )
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"error: discovery failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / args.out_name
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"discovered: {len(result['chats'])} candidate megagroups")
    print(f"json: {out_path}")
    for row in result["chats"][:40]:
        print(f"{row['username']}\t{row['participants'] or ''}\t{row['title']}")
    return 0


async def discover(*, config: Any, limit_per_query: int, min_participants: int) -> dict[str, Any]:
    from telethon import functions, types

    wrapper = make_telethon_client(config)
    client = await wrapper._get_client()
    await wrapper.ensure_authorized()
    seen: dict[str, dict[str, Any]] = {}
    try:
        for query in QUERIES:
            try:
                res = await client(functions.contacts.SearchRequest(q=query, limit=limit_per_query))
            except Exception as exc:  # noqa: BLE001
                if exc.__class__.__name__ == "FloodWaitError":
                    await asyncio.sleep(min(getattr(exc, "seconds", 30), 120))
                    continue
                continue
            for chat in getattr(res, "chats", []):
                username = getattr(chat, "username", None)
                title = getattr(chat, "title", "") or ""
                if not username or not isinstance(chat, types.Channel):
                    continue
                blob = f"{username} {title}".lower()
                if any(bad in blob for bad in BAD):
                    continue
                megagroup = bool(getattr(chat, "megagroup", False))
                broadcast = bool(getattr(chat, "broadcast", False))
                participants = getattr(chat, "participants_count", None)
                entry = seen.get(username)
                if entry is None:
                    seen[username] = {
                        "username": username,
                        "title": title,
                        "participants": participants,
                        "megagroup": megagroup,
                        "broadcast": broadcast,
                        "via": query,
                    }
                elif participants and (entry["participants"] or 0) < participants:
                    entry["participants"] = participants
            await asyncio.sleep(1.2)
    finally:
        await wrapper.close()

    chats = [
        row
        for row in seen.values()
        if row["megagroup"] and (row["participants"] or 0) >= min_participants
    ]
    chats.sort(key=lambda row: row["participants"] or 0, reverse=True)
    other = [row for row in seen.values() if row not in chats]
    other.sort(key=lambda row: row["participants"] or 0, reverse=True)
    return {
        "queries": list(QUERIES),
        "min_participants": min_participants,
        "chats": chats,
        "other_candidates": other,
    }


if __name__ == "__main__":
    raise SystemExit(main())
