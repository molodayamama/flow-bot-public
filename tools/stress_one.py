"""Stress-test одного Google Flow аккаунта без Telegram.

Открывает реальный браузер и делает настоящие запросы к Google API.
Требует явного запуска оператором — не запускать как автоматическую проверку.

Использование:
    python tools/stress_one.py --count 20 --delay 1 --prompt "a red apple"
    python tools/stress_one.py --count 50 --delay 0 --prompt "sunset"
    python tools/stress_one.py --count 30 --delay 5 --prompt "cat" --output results.jsonl

Сценарии:
    --delay 0   максимальный стресс (без пауз)
    --delay 1   лёгкий стресс
    --delay 5   умеренный темп
    --delay 30  медленный прогрев

Если бот уже запущен (profile заблокирован), скрипт автоматически делает
временную копию профиля и работает с ней.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── ранний разбор аргументов профиля (до импорта flow_bot) ─────────────────
_pre = argparse.ArgumentParser(add_help=False)
_pre.add_argument("--profile-dir", default=None)
_pre_ns, _ = _pre.parse_known_args()

from dotenv import load_dotenv  # noqa: E402
load_dotenv(PROJECT_ROOT / ".env")


def _resolve_profile(profile_dir_arg: str | None) -> Path:
    """Вернуть путь к профилю Chrome. Завершить с ошибкой если профиль заблокирован."""
    base = Path(profile_dir_arg or os.getenv("USER_DATA_DIR", "./google_profile"))
    if not base.is_absolute():
        base = PROJECT_ROOT / base

    if not base.exists():
        return base  # пусть Playwright выдаст понятную ошибку

    # Chrome на Windows создаёт <user-data-dir>/lockfile и держит его эксклюзивно
    lock_candidates = [base / "lockfile", base / "SingletonLock"]
    for lf in lock_candidates:
        if lf.exists():
            try:
                with open(lf, "r+b"):
                    pass
            except OSError:
                print(
                    f"\n❌ Профиль Chrome заблокирован: {base}\n"
                    f"   Вероятно, бот (flow_bot.py) сейчас запущен.\n\n"
                    f"   Варианты:\n"
                    f"   1. Остановите бот и повторите запуск stress_one.py\n"
                    f"   2. Используйте другой профиль:\n"
                    f"      python tools/stress_one.py --profile-dir ./google_profile2 ...\n",
                    flush=True,
                )
                sys.exit(1)

    return base


_profile_path = _resolve_profile(_pre_ns.profile_dir)
os.environ["USER_DATA_DIR"] = str(_profile_path)

# Импортируем только нужное — браузер не стартует до явного вызова .start()
from flow_bot import SessionKeeper, FlowHttpClient as FlowClient  # noqa: E402


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _fmt_result(r: dict) -> str:
    if "error" in r:
        return f"ERR {r['error'][:60]}"
    pairs = []
    for key in ("responses", "media"):
        items = r.get(key) or []
        if items:
            pairs.append(f"{key}={len(items)}")
    return f"OK {' '.join(pairs) or '(empty)'}"


async def run(
    prompt: str,
    count: int,
    delay: float,
    aspect: str,
    num_images: int,
    output_path: Path | None,
) -> None:
    keeper = SessionKeeper()
    print(f"[{_ts()}] Запускаю браузер…", flush=True)
    await keeper.start()

    client = FlowClient(keeper)

    results: list[dict] = []
    first_429 = None
    first_403 = None
    ok_count = 0
    err_count = 0

    print(
        f"[{_ts()}] Старт: count={count} delay={delay}s "
        f"prompt={prompt!r} aspect={aspect} n={num_images}",
        flush=True,
    )
    print("-" * 60, flush=True)

    for seq in range(1, count + 1):
        t0 = time.perf_counter()

        result = await client.generate_images(
            prompt,
            aspect_ratio=aspect,
            num_images=num_images,
            allow_browser_fallback=False,  # только HTTP, чтобы видеть чистые статусы
        )

        latency_ms = round((time.perf_counter() - t0) * 1000)
        is_ok = "error" not in result
        status_tag = "OK " if is_ok else "ERR"

        http_status: int | None = None
        err_text = result.get("error", "")
        for code in (200, 403, 429, 401, 500):
            if str(code) in err_text:
                http_status = code
                break

        if is_ok:
            ok_count += 1
        else:
            err_count += 1
            if http_status == 429 and first_429 is None:
                first_429 = seq
            if http_status == 403 and first_403 is None:
                first_403 = seq

        record = {
            "seq": seq,
            "ok": is_ok,
            "http_status": http_status,
            "latency_ms": latency_ms,
            "error": err_text[:120] if err_text else None,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        results.append(record)

        print(
            f"[{_ts()}] #{seq:3d}/{count} {status_tag} {latency_ms:5d}ms "
            f"| {_fmt_result(result)[:50]}",
            flush=True,
        )

        if delay > 0 and seq < count:
            await asyncio.sleep(delay)

    # ── итог ──────────────────────────────────────────────────────────────
    print("-" * 60, flush=True)
    print(f"[{_ts()}] ИТОГ:", flush=True)
    print(f"  Всего:       {count}", flush=True)
    print(f"  Успехов:     {ok_count} ({ok_count/count*100:.0f}%)", flush=True)
    print(f"  Ошибок:      {err_count}", flush=True)
    if first_429:
        print(f"  Первый 429:  запрос #{first_429}", flush=True)
    if first_403:
        print(f"  Первый 403:  запрос #{first_403}", flush=True)
    ok_latencies = [r["latency_ms"] for r in results if r["ok"]]
    if ok_latencies:
        avg = sum(ok_latencies) / len(ok_latencies)
        print(f"  Ср. время:   {avg:.0f}ms (только успехи)", flush=True)

    await keeper._close_browser_locked()

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"[{_ts()}] Результаты → {output_path}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Stress-test одного Flow-аккаунта")
    p.add_argument("--prompt", default="a red apple", help="промпт для генерации")
    p.add_argument("--count", type=int, default=20, help="кол-во генераций")
    p.add_argument("--delay", type=float, default=1.0, help="пауза между запросами (сек)")
    p.add_argument("--aspect", default="square", help="landscape | portrait | square")
    p.add_argument("--num-images", type=int, default=1, help="картинок за запрос (1-4)")
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="путь к JSONL-файлу с результатами (опционально)",
    )
    p.add_argument(
        "--profile-dir",
        default=None,
        help="путь к профилю Chrome (по умолчанию: USER_DATA_DIR из .env / ./google_profile)",
    )
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    asyncio.run(
        run(
            prompt=args.prompt,
            count=args.count,
            delay=args.delay,
            aspect=args.aspect,
            num_images=args.num_images,
            output_path=args.output,
        )
    )
