"""Runtime Flow account pool and routing helpers.

Extracted from ``flow_core.py`` for the Photozhab Core Split. The public names
are re-exported from ``flow_core`` for backward compatibility.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

_ACCOUNT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,31}$")

# ── account pool (multi-account routing; см. docs/MONETIZATION.md §12) ─


@dataclass(frozen=True)
class FlowAccount:
    """Один Google-аккаунт пула: id + путь к его Chrome-профилю.

    Опциональные поля ёмкости переопределяют глобальные дефолты пула:
    ``image_capacity`` — максимум параллельных image-джобов на аккаунт;
    ``video_capacity`` — максимум параллельных video-джобов на аккаунт.
    None = использовать дефолт AccountPool.
    """

    id: str
    profile_dir: str
    browser_proxy_url: str | None = None
    api_proxy_url: str | None = None
    image_capacity: int | None = None
    video_capacity: int | None = None


def parse_flow_accounts(
    raw: str | None,
    *,
    default_id: str = "default",
    default_dir: str = "./google_profile",
) -> list[FlowAccount]:
    """Разобрать env ``FLOW_ACCOUNTS`` в список аккаунтов.

    Формат: записи через ``;`` или ``,``, каждая — ``id=путь_к_chrome_профилю``
    (разделитель именно ``=``: в Windows-путях есть ``:``). Запись без ``=`` —
    просто путь, id генерится ``accN``. Пустая/отсутствующая переменная — один
    аккаунт ``default_id``/``default_dir`` (поведение одиночного бота).
    Дубль id — выигрывает первая запись.
    """
    accounts: list[FlowAccount] = []
    seen: set[str] = set()
    fallback_id = (
        default_id
        if _ACCOUNT_ID_RE.fullmatch(str(default_id or ""))
        else "default"
    )
    for chunk in re.split(r"[;,]", raw or ""):
        entry = chunk.strip()
        if not entry:
            continue
        base, *option_parts = [part.strip() for part in entry.split("|")]
        if "=" in base:
            acc_id, _, path = base.partition("=")
            acc_id, path = acc_id.strip(), path.strip()
        else:
            acc_id, path = "", base
        if not path:
            continue
        if not acc_id:
            acc_id = f"acc{len(accounts) + 1}"
        if not _ACCOUNT_ID_RE.fullmatch(acc_id):
            continue
        if acc_id in seen:
            continue
        browser_proxy_url: str | None = None
        api_proxy_url: str | None = None
        image_capacity: int | None = None
        video_capacity: int | None = None
        for option in option_parts:
            if not option or "=" not in option:
                continue
            key, _, value = option.partition("=")
            key = key.strip().lower().replace("-", "_")
            value = unquote(value.strip())
            if not value:
                continue
            if key == "proxy":
                browser_proxy_url = value
                api_proxy_url = value
            elif key in {"browser_proxy", "browser_proxy_url"}:
                browser_proxy_url = value
            elif key in {"api_proxy", "api_proxy_url"}:
                api_proxy_url = value
            elif key == "image_capacity":
                try:
                    image_capacity = max(1, int(value))
                except ValueError:
                    pass
            elif key == "video_capacity":
                try:
                    video_capacity = max(0, int(value))
                except ValueError:
                    pass
        seen.add(acc_id)
        accounts.append(
            FlowAccount(
                id=acc_id,
                profile_dir=path,
                browser_proxy_url=browser_proxy_url,
                api_proxy_url=api_proxy_url,
                image_capacity=image_capacity,
                video_capacity=video_capacity,
            )
        )
    if not accounts:
        accounts.append(FlowAccount(id=fallback_id, profile_dir=default_dir))
    return accounts


class AccountPool:
    """Sticky-роутинг юзеров по аккаунтам + health/cooldown/failover.

    Контракт (docs/MONETIZATION.md §12): у каждого аккаунта есть статус, роутер
    выбирает здоровый, упавший уходит в кулдаун, аккаунт можно отключить
    вручную. Привязка user→account персистится (atomic JSON, как остальные
    сторы) — проекты юзера живут на «его» аккаунте. Health — в памяти:
    после рестарта все аккаунты считаются здоровыми (cooldown заново).

    Чистый класс: часы инжектируются (``clock``), I/O — только собственный
    JSON-стор. Один аккаунт в пуле никогда не блокируется кулдауном (падения
    единственного аккаунта почти наверняка системные: лучше попытаться, чем
    молча отказывать всем).
    """

    def __init__(
        self,
        accounts: list[FlowAccount],
        store_path: str | Path | None = None,
        *,
        max_failures: int = 3,
        cooldown_sec: float = 600.0,
        default_image_capacity: int = 2,
        default_video_capacity: int = 1,
        clock=time.monotonic,
    ) -> None:
        if not accounts:
            raise ValueError("AccountPool needs at least one account")
        self._accounts: "OrderedDict[str, FlowAccount]" = OrderedDict(
            (a.id, a) for a in accounts
        )
        self._max_failures = max(1, int(max_failures))
        self._cooldown_sec = float(cooldown_sec)
        self._default_image_capacity = max(1, int(default_image_capacity))
        self._default_video_capacity = max(0, int(default_video_capacity))
        self._clock = clock
        self._path = Path(store_path) if store_path else None
        self._assign: dict[str, str] = {}
        self._health: dict[str, dict] = {
            a.id: {"fails": 0, "cooldown_until": 0.0, "disabled": False,
                   "video_allowed": True, "needs_relogin": False}
            for a in accounts
        }
        self._runtime_ready: dict[str, bool] = {a.id: True for a in accounts}
        self._runtime_status: dict[str, str] = {a.id: "ready" for a in accounts}
        # Capacity tracking (runtime-only; resets on restart).
        # Semaphores are created lazily on first use so unit-tests don't need a
        # running event loop when constructing AccountPool.
        self._image_sems: dict[str, asyncio.Semaphore] = {}
        self._video_sems: dict[str, asyncio.Semaphore] = {}
        self._active_image: dict[str, int] = {a.id: 0 for a in accounts}
        self._active_video: dict[str, int] = {a.id: 0 for a in accounts}
        self._load()

    # ── состав пула ────────────────────────────────────────────────────

    def account_ids(self) -> list[str]:
        return list(self._accounts)

    def get(self, account_id: str) -> FlowAccount | None:
        return self._accounts.get(account_id)

    def add_account(self, account: FlowAccount, *, runtime_status: str = "warming") -> bool:
        """Add a newly onboarded account to the runtime pool.

        Persistent membership still comes from FLOW_ACCOUNTS on restart; this is
        a best-effort hot-add used by the admin onboarding flow after .env is
        updated successfully.
        """
        if account.id in self._accounts:
            return False
        self._accounts[account.id] = account
        self._health[account.id] = {
            "fails": 0,
            "cooldown_until": 0.0,
            "disabled": False,
            "video_allowed": True,
            "needs_relogin": False,
        }
        self._runtime_ready[account.id] = False
        self._runtime_status[account.id] = runtime_status or "warming"
        self._active_image[account.id] = 0
        self._active_video[account.id] = 0
        return True

    def remove_account(self, account_id: str) -> bool:
        """Remove an account from the runtime pool.

        Persistent membership is still controlled by FLOW_ACCOUNTS; callers must
        update .env separately before removing from runtime.
        """
        if account_id not in self._accounts or len(self._accounts) <= 1:
            return False
        del self._accounts[account_id]
        self._health.pop(account_id, None)
        self._runtime_ready.pop(account_id, None)
        self._runtime_status.pop(account_id, None)
        self._image_sems.pop(account_id, None)
        self._video_sems.pop(account_id, None)
        self._active_image.pop(account_id, None)
        self._active_video.pop(account_id, None)
        self._assign = {k: v for k, v in self._assign.items() if v != account_id}
        self._save()
        return True

    def __len__(self) -> int:
        return len(self._accounts)

    # ── health ─────────────────────────────────────────────────────────

    def is_available(self, account_id: str) -> bool:
        h = self._health.get(account_id)
        if h is None:
            return False
        if not self._runtime_ready.get(account_id, True):
            return False
        if h["disabled"]:
            return False
        if h.get("needs_relogin"):
            return False
        return self._clock() >= h["cooldown_until"]

    def mark_success(self, account_id: str) -> None:
        h = self._health.get(account_id)
        if h is not None:
            h["fails"] = 0
            h["cooldown_until"] = 0.0
            # Успешная джоба = логин жив → снимаем флаг релогина, если был.
            h["needs_relogin"] = False

    def mark_failure(self, account_id: str) -> bool:
        """Учесть сбой; вернуть True, если аккаунт ушёл в кулдаун."""
        h = self._health.get(account_id)
        if h is None:
            return False
        h["fails"] += 1
        if h["fails"] >= self._max_failures:
            h["cooldown_until"] = self._clock() + self._cooldown_sec
            h["fails"] = 0
            return True
        return False

    def mark_cooldown(self, account_id: str) -> bool:
        """Force a bounded runtime cooldown for a strong account-health signal."""
        h = self._health.get(account_id)
        if h is None:
            return False
        h["fails"] = 0
        h["cooldown_until"] = self._clock() + self._cooldown_sec
        return True

    def reset_failures(self, account_id: str) -> bool:
        """Clear failure counter and cooldown; keep disabled state intact.

        Returns True if account id is known, False otherwise.
        """
        h = self._health.get(account_id)
        if h is None:
            return False
        h["fails"] = 0
        h["cooldown_until"] = 0.0
        return True

    def set_disabled(self, account_id: str, disabled: bool) -> bool:
        """Ручное отключение/включение аккаунта; True если id известен."""
        h = self._health.get(account_id)
        if h is None:
            return False
        h["disabled"] = bool(disabled)
        if not disabled:
            h["fails"] = 0
            h["cooldown_until"] = 0.0
            # Ручное включение подразумевает, что логин починили (релогин/онбординг).
            h["needs_relogin"] = False
        self._save()
        return True

    def mark_needs_relogin(self, account_id: str, value: bool = True) -> bool:
        """Пометить, что у аккаунта протух Google-логин (credits отдал 401 /
        нет project_id). Рантайм-флаг (не персистится): выводит аккаунт из
        ротации в :meth:`is_available`, пока успешная джоба/включение его
        не снимут, либо до перезапуска (там прогрев перепроверит заново).
        True — если id известен."""
        h = self._health.get(account_id)
        if h is None:
            return False
        h["needs_relogin"] = bool(value)
        return True

    def set_video_allowed(self, account_id: str, allowed: bool) -> bool:
        """Разрешить/запретить видео на аккаунте; True если id известен.

        Персистируется в state-файле — переживает рестарт.
        """
        h = self._health.get(account_id)
        if h is None:
            return False
        h["video_allowed"] = bool(allowed)
        self._save()
        return True

    def set_runtime_ready(self, account_id: str, ready: bool, status: str | None = None) -> bool:
        """Set non-persistent startup/runtime readiness for routing."""
        if account_id not in self._accounts:
            return False
        self._runtime_ready[account_id] = bool(ready)
        self._runtime_status[account_id] = status or ("ready" if ready else "warming")
        return True

    def is_video_capable(self, account_id: str) -> bool:
        """True если аккаунт доступен (не в кулдауне/disabled), может видео и имеет
        ненулевую video_capacity (capacity=0 = image-only)."""
        if not self.is_available(account_id):
            return False
        if self._video_cap(account_id) <= 0:
            return False
        h = self._health.get(account_id)
        return bool(h and h.get("video_allowed", True))

    def is_reference_usable(self, account_id: str) -> bool:
        """True если на аккаунте можно запускать reference-видео (r2v/frames).

        Reference-медиа (загруженное фото) привязано к конкретному аккаунту, и
        generate ОБЯЗАН идти туда же — иначе сервис вернёт 404 "entity not found".
        Поэтому, в отличие от :meth:`is_video_capable`, кулдаун здесь игнорируется
        (медиа всё равно живёт только тут); блокируем лишь hard-disabled и
        image-only аккаунты.
        """
        h = self._health.get(account_id)
        if h is None or h.get("disabled"):
            return False
        if not self._runtime_ready.get(account_id, True):
            return False
        if self._video_cap(account_id) <= 0:
            return False
        return bool(h.get("video_allowed", True))

    def is_image_only(self, account_id: str) -> bool:
        h = self._health.get(account_id)
        if h and not h.get("video_allowed", True):
            return True
        return self._video_cap(account_id) <= 0

    # ── capacity control ───────────────────────────────────────────────

    def _image_cap(self, account_id: str) -> int:
        acc = self._accounts.get(account_id)
        cap = acc.image_capacity if (acc and acc.image_capacity is not None) else self._default_image_capacity
        return max(1, cap)

    def _video_cap(self, account_id: str) -> int:
        acc = self._accounts.get(account_id)
        cap = acc.video_capacity if (acc and acc.video_capacity is not None) else self._default_video_capacity
        return max(0, cap)

    def _image_sem(self, account_id: str) -> asyncio.Semaphore:
        """Lazy-init semaphore for image slots on this account."""
        if account_id not in self._image_sems:
            self._image_sems[account_id] = asyncio.Semaphore(self._image_cap(account_id))
        return self._image_sems[account_id]

    def _video_sem(self, account_id: str) -> asyncio.Semaphore:
        """Lazy-init semaphore for video slots on this account."""
        if account_id not in self._video_sems:
            cap = self._video_cap(account_id)
            self._video_sems[account_id] = asyncio.Semaphore(max(1, cap))
        return self._video_sems[account_id]

    def has_image_capacity(self, account_id: str) -> bool:
        """Non-blocking check: True if an image slot is available right now."""
        sem = self._image_sems.get(account_id)
        if sem is None:
            return True  # not yet created → semaphore hasn't been exhausted
        return sem._value > 0  # CPython internal; stable since 3.10

    def has_video_capacity(self, account_id: str) -> bool:
        """Non-blocking check: True if a video slot is available right now."""
        sem = self._video_sems.get(account_id)
        if sem is None:
            return True
        return sem._value > 0

    @asynccontextmanager
    async def image_slot(self, account_id: str):
        """Acquire an image job slot (blocks if account is at capacity).

        Always releases in ``finally`` — safe across exceptions, timeouts, and
        failover ``continue``/``return`` paths.
        """
        sem = self._image_sem(account_id)
        async with sem:
            self._active_image[account_id] = self._active_image.get(account_id, 0) + 1
            try:
                yield
            finally:
                self._active_image[account_id] = max(
                    0, self._active_image.get(account_id, 1) - 1
                )

    @asynccontextmanager
    async def video_slot(self, account_id: str):
        """Acquire a video job slot (blocks if account is at capacity).

        Always releases in ``finally``.
        """
        sem = self._video_sem(account_id)
        async with sem:
            self._active_video[account_id] = self._active_video.get(account_id, 0) + 1
            try:
                yield
            finally:
                self._active_video[account_id] = max(
                    0, self._active_video.get(account_id, 1) - 1
                )

    # ── роутинг ────────────────────────────────────────────────────────

    def assigned_to(self, user_id: int | str) -> str | None:
        return self._assign.get(str(user_id))

    def pick_for(self, user_id: int | str) -> str | None:
        """Аккаунт для джобы юзера (sticky) или None, если весь пул недоступен.

        Один аккаунт в пуле возвращается всегда (см. docstring класса).
        Sticky-привязка переезжает на наименее загруженный живой аккаунт,
        только когда «свой» недоступен (failover; проект пересоздаётся там).
        """
        key = str(user_id)
        if len(self._accounts) == 1:
            only = next(iter(self._accounts))
            if self._assign.get(key) != only:
                self._assign[key] = only
                self._save()
            return only
        sticky = self._assign.get(key)
        if sticky and self.is_available(sticky):
            return sticky
        candidates = [aid for aid in self._accounts if self.is_available(aid)]
        if not candidates:
            return None
        loads: dict[str, int] = {aid: 0 for aid in self._accounts}
        for assigned in self._assign.values():
            if assigned in loads:
                loads[assigned] += 1
        best = min(candidates, key=lambda aid: loads[aid])
        self._assign[key] = best
        self._save()
        return best

    def pick_for_image(
        self,
        user_id: int | str,
        *,
        prefer_image_only: bool = False,
        exclude: set[str] | None = None,
    ) -> str | None:
        """Pick an account for image work.

        Normal image generation keeps the regular sticky route. Upload-based
        image editing can prefer accounts marked ``video_allowed=False`` so paid
        video-capable accounts keep more quota for video jobs.
        """
        excluded = set(exclude or set())
        if not prefer_image_only and not excluded:
            return self.pick_for(user_id)
        key = str(user_id)
        sticky = self._assign.get(key)
        if not prefer_image_only and sticky and sticky not in excluded and self.is_available(sticky):
            return sticky

        candidates = [
            aid for aid in self._accounts
            if aid not in excluded and self.is_available(aid) and self.is_image_only(aid)
        ]
        if not candidates:
            candidates = [
                aid for aid in self._accounts
                if aid not in excluded and self.is_available(aid)
            ]
        if not candidates:
            return None
        if sticky in candidates:
            return sticky
        loads: dict[str, int] = {aid: 0 for aid in self._accounts}
        for assigned in self._assign.values():
            if assigned in loads:
                loads[assigned] += 1
        best = min(candidates, key=lambda aid: loads[aid])
        self._assign[key] = best
        self._save()
        return best

    def pick_for_video(
        self,
        user_id: int | str,
        *,
        model_family: str | None = None,
        health_scores: dict | None = None,
        exclude: set[str] | None = None,
    ) -> str | None:
        """Pick the healthiest video-capable account for a fresh video job.

        Sticky assignment is only a tie-breaker; hard proxy-check failures are
        excluded from fresh video routing.
        """
        key = str(user_id)
        sticky = self._assign.get(key)
        health_scores = health_scores or {}
        excluded = set(exclude or set())
        candidates = [
            aid for aid in self._accounts
            if self.is_video_capable(aid)
            and aid not in excluded
            and not bool((health_scores.get(aid) or {}).get("proxy_failed"))
        ]
        if not candidates:
            return None
        loads: dict[str, int] = {aid: 0 for aid in self._accounts}
        for assigned in self._assign.values():
            if assigned in loads:
                loads[assigned] += 1

        def _rank(aid: str) -> tuple:
            hs = health_scores.get(aid) or {}
            try:
                score = float(hs.get("score", 50.0))
            except (TypeError, ValueError):
                score = 50.0
            family_penalty = 0
            if model_family and hs.get("model_family") and hs.get("model_family") != model_family:
                family_penalty = 1
            return (
                family_penalty,
                -score,
                self._active_video.get(aid, 0),
                0 if aid == sticky else 1,
                loads[aid],
                aid,
            )

        return min(candidates, key=_rank)

    def status(self) -> list[dict]:
        """Срез состояния пула для админ-отчёта (без секретов)."""
        now = self._clock()
        loads: dict[str, int] = {aid: 0 for aid in self._accounts}
        for assigned in self._assign.values():
            if assigned in loads:
                loads[assigned] += 1
        out = []
        for aid in self._accounts:
            h = self._health[aid]
            out.append({
                "id": aid,
                "profile_dir": self._accounts[aid].profile_dir,
                "disabled": h["disabled"],
                "needs_relogin": h.get("needs_relogin", False),
                "runtime_ready": self._runtime_ready.get(aid, True),
                "runtime_status": self._runtime_status.get(aid, "ready"),
                "video_allowed": h.get("video_allowed", True),
                "cooldown_left": max(0, int(h["cooldown_until"] - now)),
                "fails": h["fails"],
                "users": loads[aid],
                "active_image_jobs": self._active_image.get(aid, 0),
                "active_video_jobs": self._active_video.get(aid, 0),
                "image_capacity": self._image_cap(aid),
                "video_capacity": self._video_cap(aid),
            })
        return out

    # ── персистентность привязок ───────────────────────────────────────

    def _load(self) -> None:
        if self._path is None:
            return
        try:
            parsed = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(parsed, dict):
            assign = parsed.get("assignments", {})
            if isinstance(assign, dict):
                # Привязки к выбывшим из конфига аккаунтам отбрасываем — юзер
                # просто получит новый аккаунт (и новый проект) при следующей джобе.
                self._assign = {
                    str(k): str(v) for k, v in assign.items()
                    if str(v) in self._accounts
                }
            # Восстанавливаем флаги video_allowed (персистируем только False-записи)
            video_cfg = parsed.get("video_allowed", {})
            if isinstance(video_cfg, dict):
                for acc_id, allowed in video_cfg.items():
                    h = self._health.get(str(acc_id))
                    if h is not None:
                        h["video_allowed"] = bool(allowed)
            # Восстанавливаем ручные отключения (disabled=True)
            disabled_cfg = parsed.get("disabled", [])
            if isinstance(disabled_cfg, list):
                for acc_id in disabled_cfg:
                    h = self._health.get(str(acc_id))
                    if h is not None:
                        h["disabled"] = True

    def _save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Сохраняем только аккаунты у которых video_allowed=False (остальные — дефолт True)
        video_cfg = {
            aid: h["video_allowed"]
            for aid, h in self._health.items()
            if not h.get("video_allowed", True)
        }
        # Сохраняем список вручную отключённых аккаунтов (disabled=True)
        disabled_list = [
            aid for aid, h in self._health.items()
            if h.get("disabled", False)
        ]
        payload: dict = {"assignments": self._assign}
        if video_cfg:
            payload["video_allowed"] = video_cfg
        if disabled_list:
            payload["disabled"] = disabled_list
        fd, tmp_name = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self._path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
