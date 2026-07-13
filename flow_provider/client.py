"""Flow provider client: live browser session + HTTP client to Google Flow.

Extracted verbatim from ``flow_bot.py`` (PR-2a). Encapsulates everything about
talking to Google Flow (Bearer capture, captcha solving, project ids, endpoints)
so chat channels never touch provider internals. No aiogram, no flow_bot import.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import re
import time
from collections import deque
from pathlib import Path

import aiohttp
from playwright.async_api import BrowserContext, async_playwright

from flow_core import (
    ImageRef,
    ImageRegistry,
    VideoRef,
    UserProjectStore,
    action_callback_data,
    apply_request_capture,
    build_capture_from_inputs,
    build_generation_payload,
    build_image_inputs,
    build_ingredients_inputs,
    IMAGE_UPSAMPLE_ENDPOINT,
    build_upsample_payload,
    parse_upsample_response,
    IMAGE_MODELS,
    DEFAULT_IMAGE_MODEL,
    image_model_meta,
    image_model_extra,
    build_request_capture,
    clamp_num_images,
    describe_schema,
    download_url,
    id_from_media_url,
    IMAGE_UPLOAD_ENDPOINT,
    loads_xssi,
    load_edit_capture,
    media_source_from_response,
    build_upload_image_payload,
    parse_upload_image_response,
    parse_action_callback,
    result_pairs,
    save_edit_capture,
    upload_video_ids_from_response,
    VIDEO_UPLOAD_START_URL,
    VIDEO_UPLOAD_PUT_URL,
)

from flow_core import (
    VIDEO_MODELS,
    VIDEO_UI_ASPECTS,
    video_model_meta,
    video_model_key,
    video_price,
    video_animate_min_price,
    video_extend_price,
    clamp_num_videos,
    video_models_in_family,
    video_families,
)

from flow_core import (
    VIDEO_ENDPOINT      as VIDEO_GEN_ENDPOINT,
    VIDEO_FRAMES_ENDPOINT,
    VIDEO_REFERENCE_ENDPOINT,
    VIDEO_EDIT_ENDPOINT,
    VIDEO_EXTEND_ENDPOINT,
    VIDEO_CONCAT_ENDPOINT,
    VIDEO_CONCAT_STATUS_ENDPOINT,
    VIDEO_CONCAT_POLL_INTERVAL,
    VIDEO_CONCAT_POLL_MAX,
    VIDEO_POLL_ENDPOINT,
    VIDEO_POLL_INTERVAL,
    VIDEO_POLL_TIMEOUT,
    VIDEO_STATUS_SUCCESSFUL,
    VIDEO_STATUS_FAILED,
    build_video_edit_payload,
    build_video_extend_payload,
    build_video_frame_images,
    build_video_payload,
    build_video_poll_payload,
    build_video_reference_images,
    flow_scene_create_url,
    flow_scene_workflows_url,
    extract_agent_text,
    parse_agent_response,
    parse_video_gen_response,
    parse_video_scene_id,
    parse_scene_segments,
    build_concat_payload,
    parse_concat_operation_name,
    build_concat_status_payload,
    parse_concat_status,
    check_video_poll_status,
    video_media_redirect_url,
    video_edit_end_frame,
    video_duration_from_poll_item,
    video_frames_model_key,
    video_reference_model_key,
    CREDITS_ENDPOINT,
    FLOW_BROWSER_API_KEY,
    parse_credits_response,
)

import flow_copy
from flow_provider.request_policy import (
    AGENT_RECAPTCHA_ACTION,
    AGENT_RECAPTCHA_ACTION_CANDIDATES,
    RECAPTCHA_ACTIONS,
    VIDEO_GEN_403_BACKOFF_SEC,
    VIDEO_GEN_MAX_ATTEMPTS,
    VIDEO_RECAPTCHA_ACTION,
    build_flow_headers,
)
from flow_provider.runtime_config import (
    USER_DATA_DIR,
    PROXY_URL,
    BROWSER_PROXY_URL,
    API_PROXY_URL,
    TWOCAPTCHA_KEY,
    CAPMONSTER_KEY,
    FLOW_URL,
    FLOW_PROJECT_CTA_RE,
    FLOW_NEW_PROJECT_RE,
    EDIT_CAPTURE_FILE,
    EDIT_CAPTURE_RAW_FILE,
    RECENT_IMAGES_FILE,
    UPLOAD_CAPTURE_FILE,
    UPSCALE_CAPTURE_FILE,
    IDLE_PARK_SEC,
    PARK_CHECK_SEC,
    TOKEN_TTL_SEC,
    GCREDITS_CACHE_SEC,
    GCREDITS_SESSION_SNAPSHOT_TIMEOUT_SEC,
    log,
    _flow_project_cta_candidates,
    _video_failure_reason,
    _normalize_proxy_url,
    _proxy_is_disabled,
    _effective_proxy_url,
    _proxy_host_only,
    _browser_fetch_headers,
    _playwright_proxy_config,
    _host_path,
)




# ───────────────────────────────────────────
# БЛОК 1 — "живой" браузер, который перехватывает токены
# ───────────────────────────────────────────


class SessionKeeper:
    """
    Держит Playwright-браузер открытым.
    При каждом generate() перехватывает из реального запроса браузера:
      - свежий Bearer токен
      - reCAPTCHA токен (настоящий, из реального Chrome)
      - актуальный project_id и прочие заголовки
    Затем передаёт всё это в HTTP-клиент для повторного использования.
    """

    def __init__(
        self,
        account_id: str = "",
        profile_dir: str | None = None,
        browser_proxy_url: str | None = None,
        api_proxy_url: str | None = None,
    ):
        # Пул аккаунтов: у каждого keeper'а свой Chrome-профиль (и свой Google-
        # аккаунт). Без аргументов — одиночный режим на USER_DATA_DIR.
        self.account_id = account_id
        self._profile_dir = profile_dir
        self.browser_proxy_url = browser_proxy_url
        self.api_proxy_url = api_proxy_url
        self._pw = None
        self._context: BrowserContext = None
        self._page = None
        self._bearer = None
        self._cookies_dict = {}
        self._project_id = None
        self._last_headers = {}
        self._bearer_ts = 0.0
        self._recaptcha_sitekey = ""
        self._lock = asyncio.Lock()
        self._ready = asyncio.Event()
        # Idle-tab parking state (see IDLE_PARK_SEC). All page access is serialized
        # through self._lock, so the parker can never navigate mid-operation.
        self._parked = False
        self._last_use = 0.0
        self._park_task = None
        # Кэш баланса G-кредитов (см. get_g_credits) — не дёргаем Google на
        # каждый /admin_accounts, обновляем не чаще GCREDITS_CACHE_SEC.
        self._gcredits_cache: dict | None = None
        self._gcredits_cache_ts = 0.0
        self._gcredits_lock = asyncio.Lock()
        # Недавно выданные картинки (сырые dict от Google) — чтобы при перехвате
        # реальной правки найти, какое поле imageInputs ссылается на картинку.
        self._recent_sources: deque = deque(maxlen=50)
        existing_capture = load_edit_capture(EDIT_CAPTURE_FILE)
        self._edit_capture_resolved = bool(existing_capture and existing_capture.get("resolved"))
        existing_upscale = load_edit_capture(UPSCALE_CAPTURE_FILE)
        self._upscale_capture_resolved = bool(existing_upscale and existing_upscale.get("resolved"))

    # ── инициализация ──────────────────────

    async def start(self):
        async with self._lock:
            await self._start_locked()

    async def close(self):
        async with self._lock:
            await self._close_browser_locked()

    async def _start_locked(self):
        """Запускает браузер и открывает Flow. Вызывать один раз при старте."""
        try:
            self._ready.clear()
            self._clear_session_cache()
            await self._close_browser_locked()
            self._pw = await async_playwright().start()

            launch_kwargs = dict(
                user_data_dir=self._profile_dir or USER_DATA_DIR,
                channel="chrome",
                headless=False,  # False — обязательно, иначе Google блокирует
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            browser_proxy_raw = (
                self.browser_proxy_url
                if self.browser_proxy_url is not None
                else BROWSER_PROXY_URL
            )
            browser_proxy_url = _effective_proxy_url(browser_proxy_raw)
            if browser_proxy_url:
                proxy_config = _playwright_proxy_config(browser_proxy_url)
                if proxy_config:
                    launch_kwargs["proxy"] = proxy_config
                else:
                    log.warning("Browser proxy has unsupported format; Chrome proxy is disabled")

            self._context = await self._pw.chromium.launch_persistent_context(
                **launch_kwargs
            )

            # Перехватываем ВСЕ запросы к Google API, чтобы вытащить заголовки
            self._context.on("request", self._on_request)

            self._page = await self._context.new_page()
            log.info("🌐 Открываю Google Flow...")
            await self._page.goto(FLOW_URL, timeout=90_000)
            await asyncio.sleep(4)

            # Заходим в проект (или создаём новый)
            await self._open_project()

            # Извлекаем reCAPTCHA sitekey для 2captcha
            self._recaptcha_sitekey = await self._extract_sitekey()

            self._ready.set()
            self._mark_use()
            self._start_park_loop()
            log.info("✅ Браузер готов!")

        except Exception as e:
            self._ready.clear()
            log.error(f"💥 Ошибка запуска браузера: {e}")
            raise

    async def _close_browser_locked(self):
        if self._park_task is not None:
            self._park_task.cancel()
            self._park_task = None
        self._parked = False
        for obj in (self._context, self._pw):
            if obj is None:
                continue
            try:
                close = getattr(obj, "close", None) or getattr(obj, "stop", None)
                if close:
                    await close()
            except Exception:
                pass
        self._context = None
        self._page = None
        self._pw = None

    def _clear_session_cache(self):
        self._bearer = None
        self._cookies_dict = {}
        self._project_id = None
        self._last_headers = {}
        self._bearer_ts = 0.0
        self._recaptcha_sitekey = ""

    def _browser_alive(self) -> bool:
        if self._context is None or self._page is None:
            return False
        try:
            return not self._page.is_closed()
        except Exception:
            return False

    def _is_target_closed_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        return (
            exc.__class__.__name__ == "TargetClosedError"
            or "target page, context or browser has been closed" in text
            or "browser has been closed" in text
        )

    async def _ensure_browser_locked(self):
        if self._browser_alive():
            return
        log.warning("Browser context closed, restarting Google Flow session...")
        await self._start_locked()

    async def ensure_browser(self):
        async with self._lock:
            await self._ensure_browser_locked()

    # ── idle-tab parking ───────────────────

    def _mark_use(self) -> None:
        self._last_use = time.time()

    def _on_flow_page(self) -> bool:
        try:
            return "labs.google" in (self._page.url or "")
        except Exception:
            return False

    async def _navigate_to_flow_locked(self) -> None:
        """Open this account's Flow project page and wait until the SPA issues an
        API call (so ``_on_request`` captures a fresh Bearer). Call under _lock."""
        url = (
            f"https://labs.google/fx/tools/flow/project/{self._project_id}"
            if self._project_id else FLOW_URL
        )
        await self._page.goto(url, timeout=60_000, wait_until="domcontentloaded")
        for _ in range(20):  # ~10s: wait for a fresh Bearer from the loaded SPA
            await asyncio.sleep(0.5)
            if self._bearer and (time.time() - self._bearer_ts) < 30:
                break

    async def _wake_locked(self) -> None:
        """Navigate back to Flow if the tab is parked. Call under _lock."""
        self._mark_use()
        if not self._parked:
            return
        try:
            await self._navigate_to_flow_locked()
            self._parked = False
            log.info("▶️ %s: вкладка разбужена", self.account_id or "acc")
        except Exception as e:
            log.warning("⚠️ wake failed for %s: %s", self.account_id or "acc", e)

    def _start_park_loop(self) -> None:
        if IDLE_PARK_SEC <= 0:
            return
        if self._park_task is not None and not self._park_task.done():
            return
        try:
            self._park_task = asyncio.create_task(self._park_idle_loop())
        except RuntimeError:
            self._park_task = None

    def _should_park(self) -> bool:
        """True if the tab is on Flow, idle past IDLE_PARK_SEC, and can be parked."""
        if IDLE_PARK_SEC <= 0 or self._parked or not self._browser_alive():
            return False
        return (time.time() - self._last_use) >= IDLE_PARK_SEC

    async def _park_locked(self) -> None:
        """Navigate the tab to about:blank to stop the SPA. Call under _lock."""
        await self._page.goto("about:blank", timeout=15_000)
        self._parked = True
        log.info("⏸️ %s: вкладка запаркована (экономия CPU)", self.account_id or "acc")

    async def _park_idle_loop(self) -> None:
        """Park the tab on about:blank after IDLE_PARK_SEC of no page op.

        Takes self._lock to park, so it can never collide with a captcha solve /
        bearer refresh (all of which also hold the lock)."""
        while True:
            await asyncio.sleep(PARK_CHECK_SEC)
            try:
                if not self._should_park():
                    continue
                async with self._lock:
                    if not self._should_park():
                        continue
                    await self._park_locked()
            except Exception as e:
                log.warning("⚠️ park failed for %s: %s", self.account_id or "acc", e)

    async def _open_project(self):
        """Открывает существующий проект или создаёт новый."""
        try:
            card = self._page.locator('a[href*="/project/"]').first
            if await card.count() > 0:
                await card.click()
                log.info("📂 Открыт существующий проект")
            else:
                btn = self._page.get_by_text("New flow")
                if await btn.count() > 0:
                    await btn.click()
                    log.info("📂 Создан новый проект")

            await self._page.wait_for_url("**/fx/tools/flow/project/**", timeout=30_000)

            # Вытаскиваем project_id прямо из URL страницы
            page_url = self._page.url
            if "/project/" in page_url:
                pid = page_url.split("/project/")[-1].split("?")[0].split("/")[0]
                if pid:
                    self._project_id = pid
                    log.info(f"📋 Project ID из URL: {pid}")

        except Exception as e:
            log.warning(f"⚠️ Не удалось войти в проект: {e}")

    @staticmethod
    def _project_id_from_url(url: str) -> str | None:
        """Достаёт project_id из URL страницы или API-запроса Flow."""
        for marker in ("/project/", "/projects/"):
            if marker in url:
                pid = url.split(marker)[-1].split("?")[0].split("/")[0]
                if pid:
                    return pid
        return None

    async def create_new_project(self) -> str | None:
        """Создаёт НОВЫЙ Flow-проект и возвращает его project_id.

        Чтобы выдать каждому Telegram-пользователю отдельный проект на сайте.
        Работает в ОТДЕЛЬНОЙ вкладке: главная страница ``self._page`` (с живой
        сессией и полем ввода) никогда не трогается, поэтому обычная генерация
        не ломается, даже если создание проекта не удалось.

        project_id перехватывается из сетевых запросов вкладки (надёжнее, чем
        ждать смену URL). Все таймауты короткие, при любой неудаче возвращается
        ``None`` — вызывающий код откатывается на общий проект сессии.
        """
        await self._ready.wait()
        async with self._lock:
            await self._ensure_browser_locked()
            baseline_pid = self._project_id
            tab = None
            seen_pids: list[str] = []

            def _on_tab_request(request) -> None:
                if "aisandbox-pa.googleapis.com" not in request.url:
                    return
                pid = self._project_id_from_url(request.url)
                if pid and pid not in seen_pids:
                    seen_pids.append(pid)

            try:
                tab = await self._context.new_page()
                tab.on("request", _on_tab_request)
                await tab.goto(FLOW_URL, timeout=45_000, wait_until="domcontentloaded")
                try:
                    await tab.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass

                async def _wait_for_new_project(wait_sec: float) -> str | None:
                    deadline = time.time() + wait_sec
                    while time.time() < deadline:
                        pid = self._project_id_from_url(tab.url) or next(
                            (p for p in seen_pids if p != baseline_pid), None
                        )
                        if pid and pid != baseline_pid:
                            return pid
                        await asyncio.sleep(0.5)
                    return None

                clicked = False
                candidates = _flow_project_cta_candidates(tab)
                for label, getter in candidates:
                    try:
                        loc = getter().first
                        if await loc.count() > 0 and await loc.is_visible(timeout=1_000):
                            try:
                                await loc.scroll_into_view_if_needed(timeout=2_000)
                            except Exception:
                                pass
                            await loc.click(timeout=5_000)
                            clicked = True
                            log.info("create_new_project: clicked project cta %s", label)
                            pid = await _wait_for_new_project(12)
                            if pid:
                                log.info("create_new_project: created %s", pid)
                                return pid
                            if "accounts.google." in tab.url:
                                log.warning(
                                    "create_new_project: Google redirected during project creation "
                                    "(account %s, url=%s)",
                                    self.account_id, tab.url,
                                )
                                return None
                            if "labs.google" not in tab.url:
                                log.warning(
                                    "create_new_project: unexpected redirect during project creation "
                                    "(account %s, url=%s)",
                                    self.account_id, tab.url,
                                )
                                return None
                    except Exception:
                        continue

                if not clicked:
                    log.warning(
                        "⚠️ Кнопка 'New project' не найдена (аккаунт %s, url=%s) — новый проект не создан",
                        self.account_id, tab.url,
                    )
                    # Скрин для пост-мортема — следующий сбой сам себя задокументирует,
                    # без необходимости лезть в живую сессию вручную через VNC.
                    try:
                        debug_dir = Path("debug_screens")
                        debug_dir.mkdir(exist_ok=True)
                        ts = int(time.time())
                        await tab.screenshot(
                            path=str(debug_dir / f"new_flow_missing_{self.account_id}_{ts}.png")
                        )
                    except Exception:
                        pass
                    return None

                pid = await _wait_for_new_project(8)
                if pid:
                    log.info(f"🆕 Новый проект создан: {pid}")
                    return pid

                log.warning("⚠️ Новый project_id не появился после клика 'New project'")
                return None
            except Exception as e:
                if self._is_target_closed_error(e):
                    log.warning("Browser closed while creating project, restarting...")
                    await self._start_locked()
                else:
                    log.warning(f"⚠️ create_new_project: {e}")
                return None
            finally:
                if tab is not None:
                    try:
                        await tab.close()
                    except Exception:
                        pass

    # ── reCAPTCHA sitekey ─────────────────

    # Известный sitekey для labs.google (enterprise reCAPTCHA v3)
    RECAPTCHA_SITEKEY = "REDACTED_CREDENTIAL"
    # Compatibility aliases: callers historically read request policy from the
    # keeper class.  The canonical definitions now live in request_policy.py.
    RECAPTCHA_ACTIONS = RECAPTCHA_ACTIONS
    VIDEO_RECAPTCHA_ACTION = VIDEO_RECAPTCHA_ACTION
    AGENT_RECAPTCHA_ACTION = AGENT_RECAPTCHA_ACTION
    AGENT_RECAPTCHA_ACTION_CANDIDATES = AGENT_RECAPTCHA_ACTION_CANDIDATES
    VIDEO_GEN_MAX_ATTEMPTS = VIDEO_GEN_MAX_ATTEMPTS
    VIDEO_GEN_403_BACKOFF_SEC = VIDEO_GEN_403_BACKOFF_SEC

    async def _extract_sitekey(self) -> str:
        """Вытаскивает reCAPTCHA sitekey из DOM. Если не нашёл — берём известный."""
        try:
            sitekey = await self._page.evaluate("""
                () => {
                    const el = document.querySelector('[data-sitekey]');
                    if (el) return el.getAttribute('data-sitekey');
                    for (const s of document.querySelectorAll('script')) {
                        const m = s.textContent.match(/sitekey['"\\s]*:['"\\s]*['"]([^'"]+)['"]/);
                        if (m) return m[1];
                    }
                    return '';
                }
            """)
            if sitekey:
                log.info(f"🔑 reCAPTCHA sitekey из DOM: {sitekey[:20]}...")
                return sitekey
        except Exception as e:
            log.warning(f"⚠️ _extract_sitekey: {e}")

        log.info(f"🔑 Использую известный sitekey: {self.RECAPTCHA_SITEKEY[:20]}...")
        return self.RECAPTCHA_SITEKEY

    async def solve_captcha(self, action: str = "IMAGE_GENERATION") -> str:
        """Решает reCAPTCHA через browser JS (grecaptcha.enterprise.execute).

        Без фолбэка на capmonster/2captcha — они добавляют десятки секунд задержки
        и всё равно не работают с Google Flow (нет кук + score слишком низкий).
        Если JS-капча не дала токен → возвращаем '', поколение падает на фейловер
        аккаунта, что быстрее любого платного решателя.
        """
        async with self._lock:
            await self._ensure_browser_locked()
            await self._wake_locked()  # un-park tab if idle-parked
            await self._ensure_flow_page_loaded()
            return await self._solve_via_browser_js(action)

    async def _ensure_flow_page_loaded(self) -> None:
        """Ждём готовности grecaptcha на странице перед решением капчи.

        Если вкладка пустая/битая (видели чёрный экран у sub5), grecaptcha
        исполняется без контекста → токен почти нулевого score → 403. Здесь мы
        НЕ навигируем self._page (это ломает живую сессию/поле ввода — есть
        регрессионный тест), а только ждём появления grecaptcha.enterprise; если
        так и не появился — solve вернёт '' и сработает фейловер аккаунта.
        Активное восстановление битой вкладки — задача health-логики кипера.
        """
        try:
            url = self._page.url or ""
            if "labs.google" not in url:
                log.warning("⚠️ Капча: вкладка не на Flow (%s) — возможен низкий score",
                            url[:60] or "blank")
            for _ in range(16):  # до ~8с на подгрузку grecaptcha
                ready = await self._page.evaluate(
                    "() => typeof grecaptcha !== 'undefined' && !!(grecaptcha.enterprise)"
                )
                if ready:
                    return
                await asyncio.sleep(0.5)
        except Exception as exc:
            log.warning("⚠️ _ensure_flow_page_loaded: %s", exc)

    async def _solve_via_browser_js(self, action: str) -> str:
        """Вызывает grecaptcha.enterprise.execute() прямо в живом Chrome."""
        try:
            sitekey = self._recaptcha_sitekey or self.RECAPTCHA_SITEKEY
            log.info(f"🌐 Решаю капчу через браузер JS (action={action})...")
            token = await self._page.evaluate(
                """async ([sitekey, action]) => {
                    // Ждём загрузки grecaptcha
                    if (typeof grecaptcha === 'undefined' || !grecaptcha.enterprise) {
                        return '';
                    }
                    try {
                        const token = await grecaptcha.enterprise.execute(sitekey, {action: action});
                        return token || '';
                    } catch(e) {
                        return '';
                    }
                }""",
                [sitekey, action],
            )
            if token:
                log.info(f"✅ Капча из браузера JS (action={action}, len={len(token)})")
                return token
            else:
                log.warning("⚠️ grecaptcha.enterprise не доступен в браузере")
        except Exception as e:
            log.warning(f"⚠️ _solve_via_browser_js: {e}")
        return ""

    async def scan_recaptcha_actions(self) -> dict:
        """Diagnostic: scan loaded Flow scripts for grecaptcha action literals.

        The frontend calls ``grecaptcha.enterprise.execute(key,{action:"..."})``
        with literal action strings; scanning the bundles reveals every action
        the app uses (incl. the flowCreationAgent one), so we can stop guessing.
        Reads already-loaded JS only — no API calls, no captcha, no secrets."""
        try:
            await self._ensure_flow_page_loaded()
        except Exception:
            pass
        try:
            result = await self._page.evaluate(r"""async () => {
                const urls = new Set();
                for (const e of performance.getEntriesByType('resource')) {
                    if (/\.js(\?|$)/.test(e.name)) urls.add(e.name);
                }
                for (const s of document.scripts) { if (s.src) urls.add(s.src); }
                // Collect every UPPER_SNAKE string literal; the reCAPTCHA actions
                // (e.g. VIDEO_GENERATION) may be defined as named constants, not
                // inline at the execute() call.
                const reLit = /["']([A-Z][A-Z0-9_]{4,48})["']/g;
                const KW = /(GENERATION|AGENT|CHAT|REWRITE|PROMPT|CREATION|FLOW|ENHANCE|SUGGEST|IMPROVE)/;
                const interesting = {};
                let scanned = 0, failed = 0, sawVideoGen = false;
                for (const u of urls) {
                    try {
                        const r = await fetch(u);
                        if (!r.ok) { failed++; continue; }
                        const t = await r.text();
                        scanned++;
                        if (t.indexOf('VIDEO_GENERATION') !== -1) sawVideoGen = true;
                        let m;
                        while ((m = reLit.exec(t)) !== null) {
                            const s = m[1];
                            if (KW.test(s)) interesting[s] = (interesting[s]||0)+1;
                        }
                    } catch (e) { failed++; }
                }
                return {scanned, failed, urls: urls.size, saw_video_generation: sawVideoGen, actions: interesting};
            }""")
            return {"ok": True, **(result or {})}
        except Exception as exc:  # noqa: BLE001 - diagnostic, return JSON
            return {"ok": False, "error": exc.__class__.__name__}

    async def capture_agent_flow(
        self, prompt: str = "улучши промпт: котёнок на лежанке", wait_sec: float = 18.0
    ) -> dict:
        """Diagnostic: open the project chat, enable Agent mode, send a prompt,
        and capture the flowCreationAgent/session network calls (sanitized).

        Runs in a throwaway tab of the live context (shares login) so the main
        page is undisturbed. No credit spend; reveals the real session lifecycle
        (e.g. a createSession before streamChat) and the request/response shape."""
        captured: list[dict] = []
        responses: list[dict] = []

        def _redact(s) -> str:
            if not s:
                return ""
            s = re.sub(r"(ya29\.|Bearer\s+|session-token|\"token\"\s*:\s*\")[^\s\"']+", r"\1***", str(s))
            return s[:1800]

        def _is_agent_url(u: str) -> bool:
            lu = u.lower()
            return ("flowcreationagent" in lu) or ("creationagent" in lu) or (
                ":streamchat" in lu) or ("aisandbox-pa" in lu and "agent" in lu) or (
                "aisandbox-pa" in lu and "session" in lu)

        def _on_req(request) -> None:
            try:
                if _is_agent_url(request.url):
                    body = None
                    try:
                        body = request.post_data
                    except Exception:
                        body = None
                    captured.append({
                        "method": request.method,
                        "url": _redact(request.url)[:320],
                        "body": _redact(body),
                    })
            except Exception:
                pass

        def _on_resp(response) -> None:
            try:
                if _is_agent_url(response.url):
                    responses.append({"url": _redact(response.url)[:320], "status": response.status})
            except Exception:
                pass

        await self._ready.wait()
        async with self._lock:
            await self._ensure_browser_locked()
            tab = None
            clicked_agent = False
            sent = False
            try:
                tab = await self._context.new_page()
                tab.on("request", _on_req)
                tab.on("response", _on_resp)
                pid = self._project_id
                url = f"https://labs.google/fx/tools/flow/project/{pid}" if pid else FLOW_URL
                await tab.goto(url, timeout=60_000, wait_until="domcontentloaded")
                try:
                    await tab.wait_for_load_state("networkidle", timeout=12_000)
                except Exception:
                    pass

                for getter in (
                    lambda: tab.get_by_role("button", name=re.compile(r"agent", re.I)),
                    lambda: tab.get_by_text(re.compile(r"^\s*agent\s*$", re.I)),
                    lambda: tab.locator('[aria-label*="agent" i]'),
                ):
                    try:
                        loc = getter().first
                        if await loc.count() > 0 and await loc.is_visible(timeout=1_500):
                            await loc.click(timeout=5_000)
                            clicked_agent = True
                            break
                    except Exception:
                        continue
                await asyncio.sleep(2)

                for inp in (
                    lambda: tab.get_by_role("textbox"),
                    lambda: tab.locator("textarea"),
                    lambda: tab.locator('[contenteditable="true"]'),
                ):
                    try:
                        box = inp().first
                        if await box.count() > 0 and await box.is_visible(timeout=1_500):
                            await box.click(timeout=3_000)
                            await box.type(prompt, delay=15)
                            await tab.keyboard.press("Enter")
                            sent = True
                            break
                    except Exception:
                        continue
                await asyncio.sleep(wait_sec)
            except Exception as exc:  # noqa: BLE001 - diagnostic, return JSON
                return {
                    "ok": False, "error": exc.__class__.__name__,
                    "clicked_agent": clicked_agent, "sent": sent,
                    "requests": captured, "responses": responses,
                }
            finally:
                try:
                    if tab:
                        await tab.close()
                except Exception:
                    pass
        return {
            "ok": True, "clicked_agent": clicked_agent, "sent": sent,
            "had_project": bool(self._project_id),
            "requests": captured, "responses": responses,
        }

    async def public_ips(self) -> dict:
        """Diagnostic: public IP seen by the browser vs by the API HTTP client.

        Reference: reCAPTCHA-токен куётся в браузере, а запрос летит из aiohttp.
        Если их egress-IP различаются (browser vs api proxy) — это даёт Google
        повод для 403/«suspicious». Эндпоинт /api/admin/proxy-check это меряет.
        Креды прокси не возвращаются — только host:port.
        """
        out: dict = {
            "browser_proxy": _proxy_host_only(
                self.browser_proxy_url if self.browser_proxy_url is not None else BROWSER_PROXY_URL
            ),
            "api_proxy": _proxy_host_only(
                self.api_proxy_url if self.api_proxy_url is not None else API_PROXY_URL
            ),
            "browser_ip": None,
            "api_ip": None,
        }
        # Browser egress IP — через одноразовую вкладку в существующем контексте.
        try:
            await self._ready.wait()
            async with self._lock:
                await self._ensure_browser_locked()
                page = await self._context.new_page()
                try:
                    await page.goto("https://api.ipify.org?format=json",
                                    timeout=20_000, wait_until="domcontentloaded")
                    txt = await page.evaluate("() => document.body.innerText")
                    import json as _j
                    out["browser_ip"] = (_j.loads(txt) or {}).get("ip")
                finally:
                    await page.close()
        except Exception as exc:
            out["browser_error"] = exc.__class__.__name__
        # API egress IP — через тот же прокси, что использует FlowHttpClient.
        try:
            proxy_raw = self.api_proxy_url if self.api_proxy_url is not None else API_PROXY_URL
            proxy = _effective_proxy_url(proxy_raw) or None
            async with aiohttp.ClientSession() as http:
                async with http.get("https://api.ipify.org?format=json", proxy=proxy,
                                    timeout=aiohttp.ClientTimeout(total=20)) as r:
                    d = await r.json(content_type=None)
                    out["api_ip"] = d.get("ip")
        except Exception as exc:
            out["api_error"] = exc.__class__.__name__
        out["match"] = bool(out["browser_ip"] and out["browser_ip"] == out["api_ip"])
        return out

    async def post_json_via_browser(
        self,
        url: str,
        headers: dict,
        payload: dict,
        timeout_ms: int = 60_000,
    ) -> dict | None:
        """POST JSON from the live Flow page using Chrome's own network stack."""
        await self._ready.wait()
        safe_headers = _browser_fetch_headers(headers)
        try:
            async with self._lock:
                await self._ensure_browser_locked()
                page_url = getattr(self._page, "url", "") or ""
                if not page_url.startswith("https://labs.google/"):
                    nav_url = (
                        f"https://labs.google/fx/tools/flow/project/{self._project_id}"
                        if self._project_id
                        else "https://labs.google/fx/tools/flow"
                    )
                    await self._page.goto(nav_url, timeout=30_000, wait_until="domcontentloaded")
                return await self._page.evaluate(
                    """async ({url, headers, payload, timeoutMs}) => {
                        const controller = new AbortController();
                        const timer = setTimeout(() => controller.abort(), timeoutMs);
                        try {
                            const response = await fetch(url, {
                                method: "POST",
                                headers,
                                body: JSON.stringify(payload),
                                credentials: "include",
                                mode: "cors",
                                signal: controller.signal,
                            });
                            return {status: response.status, text: await response.text()};
                        } finally {
                            clearTimeout(timer);
                        }
                    }""",
                    {
                        "url": url,
                        "headers": safe_headers,
                        "payload": payload,
                        "timeoutMs": timeout_ms,
                    },
                )
        except Exception as exc:
            log.warning("🎬 browser video POST failed: %s", exc.__class__.__name__)
            return None

    async def get_2captcha_balance(self) -> str:
        """Возвращает баланс 2captcha в виде строки."""
        if not TWOCAPTCHA_KEY:
            return "TWOCAPTCHA_KEY не задан"
        try:
            from twocaptcha import TwoCaptcha
            solver = TwoCaptcha(TWOCAPTCHA_KEY)
            balance = await asyncio.get_event_loop().run_in_executor(
                None, solver.balance
            )
            return f"${float(balance):.4f}"
        except Exception as e:
            return f"Ошибка: {e}"

    async def _gcredits_session_snapshot(self) -> dict | None:
        """Read current auth state without forcing browser navigation."""
        bearer = self._bearer
        if not bearer:
            return None
        try:
            await asyncio.wait_for(
                self._ready.wait(),
                timeout=GCREDITS_SESSION_SNAPSHOT_TIMEOUT_SEC,
            )
        except (asyncio.TimeoutError, TimeoutError):
            return None

        acquired = False
        try:
            await asyncio.wait_for(
                self._lock.acquire(),
                timeout=GCREDITS_SESSION_SNAPSHOT_TIMEOUT_SEC,
            )
            acquired = True
            if not self._browser_alive():
                return None
            try:
                raw_cookies = await self._context.cookies("https://labs.google")
            except Exception as e:
                if not self._is_target_closed_error(e):
                    log.warning("get_g_credits cookie snapshot failed for %s: %s", self.account_id, e)
                return None
            cookies = {c["name"]: c["value"] for c in raw_cookies}
            return {
                "bearer": self._bearer or bearer,
                "cookies": cookies,
            }
        except (asyncio.TimeoutError, TimeoutError):
            return None
        finally:
            if acquired:
                self._lock.release()

    async def get_g_credits(self, *, force: bool = False) -> dict | None:
        """Текущий остаток провайдерских G-credits для этого аккаунта.

        Дёргает ``CREDITS_ENDPOINT`` с уже имеющимся bearer/cookies.
        Кэшируется на GCREDITS_CACHE_SEC секунд — не бьём Google на каждый
        /admin_accounts. Любая ошибка (нет bearer, сеть, неожиданный формат)
        → None, чтобы админ-команда не падала из-за недоступности баланса.
        """
        now = time.time()
        if not force and self._gcredits_cache is not None and (now - self._gcredits_cache_ts) < GCREDITS_CACHE_SEC:
            return self._gcredits_cache
        try:
            await asyncio.wait_for(
                self._gcredits_lock.acquire(),
                timeout=GCREDITS_SESSION_SNAPSHOT_TIMEOUT_SEC,
            )
        except (asyncio.TimeoutError, TimeoutError):
            return self._gcredits_cache
        try:
            now = time.time()
            if not force and self._gcredits_cache is not None and (now - self._gcredits_cache_ts) < GCREDITS_CACHE_SEC:
                return self._gcredits_cache
            session = await self._gcredits_session_snapshot()
            if not session:
                return self._gcredits_cache
            if not session["bearer"]:
                return self._gcredits_cache
            proxy_raw = self.api_proxy_url if self.api_proxy_url is not None else API_PROXY_URL
            proxy = _effective_proxy_url(proxy_raw) or None
            async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                async with http.get(
                    f"{CREDITS_ENDPOINT}?key={FLOW_BROWSER_API_KEY}",
                    headers={
                        "authorization": f"Bearer {session['bearer']}",
                        # Ключ FLOW_BROWSER_API_KEY ограничен по HTTP-referer на
                        # labs.google. Без этих заголовков Google отвечает 403
                        # «Requests from referer <empty> are blocked». Те же
                        # значения шлют рабочие generate-запросы.
                        "Origin": "https://labs.google",
                        "Referer": "https://labs.google/",
                    },
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        body = (await resp.text())[:200]
                        log.warning(
                            "get_g_credits non-200 for %s: status=%s body=%s",
                            self.account_id, resp.status, body,
                        )
                        # NB: с idle-парковкой вкладок Bearer штатно протухает
                        # между задачами, поэтому 401 здесь НЕ значит «мёртвый
                        # логин» — не флагаем needs_relogin из админ-опроса (иначе
                        # ложно выкидывали бы живые аккаунты). Реальный мёртвый
                        # логин ловится на пути генерации (нет project_id).
                        return self._gcredits_cache or {
                            "error": "auth_401" if resp.status == 401 else f"http_{resp.status}",
                            "status": resp.status,
                        }
                    data = await resp.json(content_type=None)
        except Exception as e:
            log.warning("get_g_credits failed for %s: %s", self.account_id, e)
            return self._gcredits_cache
        finally:
            self._gcredits_lock.release()
        parsed = parse_credits_response(data)
        if parsed:
            self._gcredits_cache = parsed
            self._gcredits_cache_ts = time.time()
            # Баланс получен с валидным логином → снимаем флаг релогина, если был.
            self._flag_needs_relogin(False)
        else:
            log.warning(
                "get_g_credits unparseable response for %s: %s",
                self.account_id, str(data)[:200],
            )
        return parsed or self._gcredits_cache or {"error": "unparseable"}

    def _flag_needs_relogin(self, value: bool) -> None:
        """Пометить аккаунт как требующий релогина (или снять пометку) в пуле.

        Безопасно вызывается из методов кейпера: ссылается на модульный
        ``account_pool`` лениво и молча игнорирует, если пул ещё не создан
        (импорт-время) или аккаунт не из пула (одиночный/тестовый кейпер).
        """
        if not self.account_id:
            return
        pool = globals().get("account_pool")
        if pool is None:
            return
        try:
            pool.mark_needs_relogin(self.account_id, bool(value))
        except Exception:
            log.debug("mark_needs_relogin failed for %s", self.account_id, exc_info=True)

    async def get_capmonster_balance(self) -> str:
        """Возвращает баланс CapMonster в виде строки."""
        if not CAPMONSTER_KEY:
            return "CAPMONSTER_KEY не задан"
        try:
            async with aiohttp.ClientSession() as http:
                async with http.post(
                    "https://api.capmonster.cloud/getBalance",
                    json={"clientKey": CAPMONSTER_KEY},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json(content_type=None)
            if data.get("errorId"):
                return f"Ошибка: {data}"
            return f"${float(data['balance']):.4f}"
        except Exception as e:
            return f"Ошибка: {e}"

    # ── перехват заголовков ────────────────

    def _on_request(self, request):
        """Вызывается для каждого запроса браузера. Ловим заголовки от Google API."""
        url = request.url
        if "aisandbox-pa.googleapis.com" not in url:
            return

        headers = request.headers
        bearer = headers.get("authorization", "")
        if bearer.startswith("Bearer "):
            token = bearer[7:]
            if token != self._bearer:
                self._bearer = token
                self._bearer_ts = time.time()
                log.info(f"🔑 Bearer перехвачен (len={len(token)})")

        # Сохраняем все полезные заголовки как есть
        for key in [
            "x-browser-channel",
            "x-browser-copyright",
            "x-browser-year",
            "x-browser-validation",
            "x-client-data",
            "user-agent",
            "sec-ch-ua",
            "sec-ch-ua-platform",
        ]:
            if key in headers:
                self._last_headers[key] = headers[key]

        # Вытаскиваем project_id из URL
        if "/projects/" in url:
            parts = url.split("/projects/")
            if len(parts) > 1:
                pid = parts[1].split("/")[0].split("?")[0]
                if pid and pid != self._project_id:
                    self._project_id = pid
                    log.info(f"📋 Project ID: {pid}")

        # Учимся формату редактирования и апскейла из реальных запросов браузера.
        self._maybe_capture_edit(request, url)
        self._maybe_capture_upscale(request, url)

    # ── обучение формату редактирования ───

    def note_image(self, img: dict) -> None:
        """Запомнить сырой объект выданной картинки (для распознавания правки).

        Сохраняем и на диск, чтобы идентификаторы пережили рестарт бота: правку
        пользователь часто делает уже в новой сессии.
        """
        if not (isinstance(img, dict) and img):
            return
        self._recent_sources.append(img)
        try:
            save_edit_capture(
                RECENT_IMAGES_FILE, {"sources": list(self._recent_sources)}
            )
        except Exception:
            pass

    def _maybe_capture_edit(self, request, url: str) -> None:
        """Если браузер шлёт настоящую правку (непустой imageInputs) — изучить формат.

        Сопоставляет поле-ссылку с идентификаторами недавно выданных картинок
        (включая сохранённые на диск). Дополнительно пишет СЫРОЙ дамп правки
        (значения imageInputs + id недавних картинок, без bearer/recaptcha) в
        ``EDIT_CAPTURE_RAW_FILE`` для ручной достройки шаблона. Полностью
        обёрнуто в try/except, чтобы никогда не мешать генерации.
        """
        try:
            if "batchGenerate" not in url:
                return
            body = request.post_data
            if not body or "imageInputs" not in body:
                return
            data = json.loads(body)
            inputs = None
            for req in data.get("requests", []) or []:
                candidate = req.get("imageInputs") if isinstance(req, dict) else None
                if isinstance(candidate, list) and candidate:
                    inputs = candidate
                    break
            if not inputs:
                return

            recent = self._load_recent_sources()
            log.info("🔬 EDIT imageInputs schema: %s", describe_schema(inputs))

            # Сырой дамп для ручного анализа (только imageInputs + id картинок).
            try:
                save_edit_capture(
                    EDIT_CAPTURE_RAW_FILE,
                    {"image_inputs": inputs, "recent_sources": recent},
                )
            except Exception:
                pass

            capture = build_capture_from_inputs(inputs, recent)
            if capture.get("resolved"):
                save_edit_capture(EDIT_CAPTURE_FILE, capture)
                self._edit_capture_resolved = True
                log.info("✅ Формат редактирования изучен и сохранён в %s", EDIT_CAPTURE_FILE)
            elif not self._edit_capture_resolved:
                # Сохраняем хотя бы схему для ручного анализа, не затирая рабочий формат.
                save_edit_capture(EDIT_CAPTURE_FILE, capture)
                log.warning(
                    "⚠️ Перехвачена правка, но поле-ссылку не удалось сопоставить "
                    "с недавней картинкой (%d шт). Сырой дамп: %s",
                    len(recent),
                    EDIT_CAPTURE_RAW_FILE,
                )
        except Exception:
            # Никогда не роняем обработку запросов из-за диагностики.
            pass

    def _load_recent_sources(self) -> list:
        """Недавние картинки из памяти + с диска (переживают рестарт)."""
        combined = list(self._recent_sources)
        if combined:
            return combined
        data = load_edit_capture(RECENT_IMAGES_FILE)
        if isinstance(data, dict):
            sources = data.get("sources")
            if isinstance(sources, list):
                return sources
        return []

    def _recent_media_ids(self) -> set:
        """ID недавно выданных картинок (mediaId + id из fifeUrl)."""
        ids: set[str] = set()
        for src in self._load_recent_sources():
            if not isinstance(src, dict):
                continue
            mid = src.get("mediaId")
            if isinstance(mid, str) and mid:
                ids.add(mid)
            from_url = id_from_media_url(src.get("fifeUrl"))
            if from_url:
                ids.add(from_url)
        return ids

    def _maybe_capture_upscale(self, request, url: str) -> None:
        """Изучить формат НАСТОЯЩЕГО апскейла, перехватив реальный запрос браузера.

        Признак: POST к API, не batchGenerate, тело ссылается на id недавно
        выданной картинки (значит, действие именно над ней). Полностью в
        try/except, чтобы никогда не мешать генерации.
        """
        try:
            if "batchGenerate" in url:
                return
            if getattr(request, "method", "GET") != "POST":
                return
            body = request.post_data
            if not body:
                return
            ids = self._recent_media_ids()
            if not ids or not any(i in body for i in ids):
                return
            data = json.loads(body)
            capture = build_request_capture(url, data, ids)
            log.info("🔬 UPSCALE candidate schema: %s", describe_schema(data))
            if capture.get("resolved"):
                save_edit_capture(UPSCALE_CAPTURE_FILE, capture)
                self._upscale_capture_resolved = True
                log.info("✅ Формат апскейла изучен и сохранён в %s", UPSCALE_CAPTURE_FILE)
        except Exception:
            pass

    # ── обновление Bearer через браузер ───

    async def _refresh_bearer(self):
        """
        Принудительно триггерим запрос в браузере чтобы перехватить свежий Bearer.
        Открываем Flow и ждём запроса к API.
        """
        log.info("🔄 Обновляю Bearer токен через браузер...")
        try:
            # Если вкладка запаркована (about:blank) или ушла с Flow — reload не
            # попадёт в API и токен не перехватится. Возвращаемся на Flow.
            if self._parked or not self._on_flow_page():
                await self._navigate_to_flow_locked()
                self._parked = False
            else:
                # Просто перезагружаем страницу проекта — браузер сам пойдёт к API
                await self._page.reload(timeout=30_000)
            self._mark_use()
            await asyncio.sleep(3)

            # Если токен всё ещё не появился — кликаем в textarea
            if not self._bearer:
                ta = self._page.locator("#PINHOLE_TEXT_AREA_ELEMENT_ID")
                if await ta.count() > 0:
                    await ta.click()

            await asyncio.sleep(2)
            log.info(
                f"🔑 Bearer после обновления: {'OK' if self._bearer else 'не получен'}"
            )
        except Exception as e:
            log.warning(f"⚠️ Ошибка при обновлении Bearer: {e}")

    # ── публичный метод ────────────────────

    async def get_session(self) -> dict:
        """
        Возвращает актуальные данные сессии для HTTP-запроса.
        Обновляет Bearer если он старше TOKEN_TTL_SEC.
        """
        await self._ready.wait()
        async with self._lock:
            await self._ensure_browser_locked()
            await self._wake_locked()  # un-park tab if idle-parked (also refreshes Bearer)
            age = time.time() - self._bearer_ts
            if not self._bearer or age > TOKEN_TTL_SEC:
                await self._refresh_bearer()

            # Собираем куки из браузера (всегда свежие)
            try:
                raw_cookies = await self._context.cookies("https://labs.google")
            except Exception as e:
                if not self._is_target_closed_error(e):
                    raise
                log.warning("Browser context closed while reading cookies, restarting...")
                await self._start_locked()
                if not self._bearer:
                    await self._refresh_bearer()
                raw_cookies = await self._context.cookies("https://labs.google")
            cookies = {c["name"]: c["value"] for c in raw_cookies}

            return {
                "bearer": self._bearer,
                "cookies": cookies,
                "project_id": self._project_id,
                "headers": dict(self._last_headers),
            }

    async def generate_via_browser(self, prompt: str) -> dict:
        """
        Генерация ЧЕРЕЗ браузер (fallback если HTTP не работает).
        Перехватывает ответ напрямую из сетевого ответа браузера.
        """
        await self._ready.wait()
        async with self._lock:
            await self._ensure_browser_locked()
            await self._wake_locked()  # un-park tab if idle-parked
            try:
                ta = self._page.locator("#PINHOLE_TEXT_AREA_ELEMENT_ID")
                try:
                    await ta.wait_for(state="visible", timeout=10_000)
                except Exception:
                    # Браузер не на странице проекта — пробуем вернуться.
                    log.warning("⚠️ textarea не видна, навигация к проекту...")
                    pid = self._project_id
                    nav_url = (
                        f"https://labs.google/fx/tools/flow/project/{pid}"
                        if pid
                        else "https://labs.google/fx/tools/flow"
                    )
                    await self._page.goto(nav_url, timeout=30_000, wait_until="domcontentloaded")
                    # Ждём networkidle — страница Flow грузит React-компоненты лениво
                    try:
                        await self._page.wait_for_load_state("networkidle", timeout=15_000)
                    except Exception:
                        pass
                    if not pid:
                        await self._open_project()
                    await ta.wait_for(state="visible", timeout=25_000)
                await ta.click()
                await self._page.keyboard.press("Control+A")
                await self._page.keyboard.press("Backspace")
                await ta.type(prompt, delay=random.randint(40, 90))

                async with self._page.expect_response(
                    lambda r: "batchGenerateImages" in r.url
                    or "batchGenerateVideos" in r.url,
                    timeout=90_000,
                ) as resp_info:
                    await self._page.keyboard.press("Enter")

                resp = await resp_info.value
                log.info(f"📡 Браузер получил ответ: {resp.status}")

                if resp.status != 200:
                    text = await resp.text()
                    log.warning("📡 Браузерная генерация не-200: %s тело=%s", resp.status, text[:300])
                    return {"error": flow_copy.msg("service_error", status=resp.status)}

                return await resp.json()

            except Exception as e:
                log.error(f"❌ generate_via_browser: {e}")
                return {"error": flow_copy.msg("gen_failed")}

    async def _upload_image_api_locked(
        self, data: bytes, filename: str, project_id: str | None = None,
    ) -> dict | None:
        # Предпочитаем явный project_id (его уже выдал ensure_user_project), и
        # только как фолбэк — сессионный self._project_id. Иначе при пустом
        # self._project_id аплоад молча возвращал None → ломал видео/i2i.
        project_id = project_id or self._project_id
        if not self._bearer:
            await self._refresh_bearer()
        project_id = project_id or self._project_id
        if not (self._bearer and project_id):
            log.warning("⚠️ upload_image API: нет bearer/project_id (bearer=%s project=%s)",
                        bool(self._bearer), bool(project_id))
            return None
        proxy_raw = self.api_proxy_url if self.api_proxy_url is not None else API_PROXY_URL
        proxy = _effective_proxy_url(proxy_raw) or None
        mime_type = "image/png"
        lower_name = (filename or "").lower()
        if lower_name.endswith((".jpg", ".jpeg")):
            mime_type = "image/jpeg"
        elif lower_name.endswith(".webp"):
            mime_type = "image/webp"
        status: int | None = None
        text = ""
        for attempt in range(2):
            try:
                raw_cookies = await self._context.cookies("https://labs.google")
            except Exception as e:
                if not self._is_target_closed_error(e):
                    raise
                log.warning("Browser context closed while uploading image, restarting...")
                await self._start_locked()
                if not self._bearer:
                    await self._refresh_bearer()
                raw_cookies = await self._context.cookies("https://labs.google")

            cookies = {c["name"]: c["value"] for c in raw_cookies}
            project_id = project_id or self._project_id
            if not (self._bearer and project_id):
                return None
            session = {
                "bearer": self._bearer,
                "cookies": cookies,
                "project_id": project_id,
                "headers": dict(self._last_headers),
            }
            payload = build_upload_image_payload(
                project_id=project_id,
                image_bytes=base64.b64encode(data).decode("ascii"),
                mime_type=mime_type,
                file_name=filename or "upload.png",
            )
            try:
                async with aiohttp.ClientSession(cookies=cookies) as http:
                    async with http.post(
                        IMAGE_UPLOAD_ENDPOINT,
                        headers=build_flow_headers(session),
                        json=payload,
                        proxy=proxy,
                        timeout=aiohttp.ClientTimeout(total=60),
                    ) as resp:
                        status = resp.status
                        text = await resp.text()
            except Exception as e:
                log.warning("⚠️ upload_image API request failed: %s", e)
                return None

            if status == 401 and attempt == 0:
                log.warning("🔑 upload_image API bearer expired, refreshing...")
                await self._refresh_bearer()
                project_id = self._project_id
                continue
            break

        if status == 401:
            log.warning("🔑 upload_image API bearer expired after refresh")
            return None
        if status != 200:
            body = loads_xssi(text)
            log.warning(
                "⚠️ upload_image API status=%s schema=%s body=%s",
                status,
                describe_schema(body) if body is not None else "non-json",
                (text or "")[:300],
            )
            return None
        body = loads_xssi(text)
        source = parse_upload_image_response(body) if body is not None else None
        if source and source.get("mediaId"):
            source.setdefault("_project_id", project_id)
            log.info("⬆️ Фото загружено через Flow API: mediaId=%s…", source["mediaId"][:8])
            return source
        log.warning(
            "⚠️ upload_image API 200 but mediaId not parsed (schema=%s)",
            describe_schema(body) if body is not None else "non-json",
        )
        return None

    async def upload_image(
        self, data: bytes, filename: str = "upload.png", project_id: str | None = None,
    ) -> dict | None:
        """Загрузить присланное фото в Flow (API uploadImage; project_id явный).

        Возвращает «источник» картинки (dict с ``mediaId``/``fifeUrl``), который
        можно редактировать как сгенерированный, или ``None`` при неудаче.

        Контракт загрузки у Flow заранее неизвестен, поэтому id картинки ищется
        НЕ по конкретному имени поля/хосту, а структурно: UUID рядом с media-URL
        в любом JSON-ответе (см. ``flow_core.media_source_from_response``), с
        учётом анти-XSSI префикса ``)]}'``. Берём id, которого НЕ было на странице
        до загрузки (чтобы не схватить уже существующую картинку).
        """
        import tempfile

        await self._ready.wait()
        async with self._lock:
            await self._ensure_browser_locked()
            await self._wake_locked()  # un-park: upload needs the live Flow page (file input + Bearer)
            tmp_path = None
            captured: dict = {}
            seen_schemas: list = []

            async def _on_upload_response(response) -> None:
                if captured:
                    return
                try:
                    ctype = (response.headers or {}).get("content-type", "")
                    if "json" not in ctype and "text" not in ctype:
                        return
                    text = await response.text()
                except Exception:
                    return
                body = loads_xssi(text)
                if body is None:
                    return
                try:
                    seen_schemas.append(
                        {"url": _host_path(response.url), "schema": describe_schema(body)}
                    )
                except Exception:
                    pass
                src = media_source_from_response(body)
                if src and src.get("mediaId") and src["mediaId"] not in baseline_ids:
                    captured.update(src)

            try:
                api_source = await self._upload_image_api_locked(data, filename, project_id)
                if api_source and api_source.get("mediaId"):
                    return api_source

                # Базовые id картинок, уже присутствующих на странице ДО загрузки.
                baseline_ids = await self._page_media_ids()

                fd, tmp_path = tempfile.mkstemp(suffix="_" + filename)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)

                self._page.on("response", _on_upload_response)

                file_input = self._page.locator("input[type='file']").first
                try:
                    await file_input.wait_for(state="attached", timeout=10_000)
                except Exception:
                    log.warning("⚠️ upload_image: файловый input не найден")
                    return None
                await file_input.set_input_files(tmp_path)

                deadline = time.time() + 45
                while time.time() < deadline and not captured:
                    await asyncio.sleep(0.5)

                if captured.get("mediaId"):
                    log.info(f"⬆️ Фото загружено в Flow: mediaId={captured['mediaId'][:8]}…")
                    captured.setdefault("_project_id", self._project_id)
                    return dict(captured)

                # Не нашли id — фолбэк: новый id из DOM (картинка уже на странице).
                after_ids = await self._page_media_ids()
                fresh = [i for i in after_ids if i not in baseline_ids]
                if fresh:
                    log.info(f"⬆️ Фото загружено (id из DOM): {fresh[0][:8]}…")
                    return {"mediaId": fresh[0], "_project_id": self._project_id}

                # Совсем не нашли — сохраняем схемы ответов для ручной доводки.
                try:
                    save_edit_capture(
                        UPLOAD_CAPTURE_FILE,
                        {"note": "upload mediaId not found", "responses": seen_schemas},
                    )
                except Exception:
                    pass
                log.warning(
                    "⚠️ upload_image: mediaId не получен (ответов: %d). Схемы: %s",
                    len(seen_schemas),
                    UPLOAD_CAPTURE_FILE,
                )
                return None
            except Exception as e:
                if self._is_target_closed_error(e):
                    await self._start_locked()
                log.error(f"❌ upload_image: {e}")
                return None
            finally:
                try:
                    self._page.remove_listener("response", _on_upload_response)
                except Exception:
                    pass
                if tmp_path:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

    async def upload_video(
        self,
        data: bytes,
        filename: str = "upload.mp4",
        project_id: str | None = None,
        content_type: str = "video/mp4",
    ) -> dict | None:
        """Загрузить пользовательское видео в Flow (для редактирования промптом).

        Контракт сверен захватом (``tools/capture_video.py --upload-edit``) и
        кодом веб-приложения Flow (чанк ``_app``): видео грузится НЕ через
        ``input[type=file]``, а через same-origin proxy Flow с обязательными
        ``X-Upload-*`` заголовками:
            POST /fx/api/upload-video?action=start
                 X-Upload-Project-Id / -Content-Length / -Content-Type / -File-Name
                 → {sessionUrl, status:"active"}
            PUT  /fx/api/upload-video?action=upload   (чанки по 2 МБ)
                 X-Upload-Session-Url: <sessionUrl из start>  ← без него PUT → 400
                 X-Upload-Offset, X-Upload-Command: "upload" | "upload, finalize",
                 X-Upload-Project-Id, X-Upload-File-Name,
                 Content-Type: application/octet-stream
                 → последний чанк: {status:"final", mediaServerId,
                    workflowServerId, videoWidth, videoHeight}
        ``mediaServerId``/``workflowServerId`` и есть нужные id для video-edit.
        Запрос выполняем из контекста страницы (``page.evaluate``+``fetch``),
        чтобы cookies/origin/авторизация совпадали с веб-приложением. Фолбэка
        через ``upload_image`` нет: файловый input принимает только картинки
        («Unsupported image format» для .mp4).

        Возвращает ``{mediaId, workflowId?, _project_id, width?, height?}`` или
        ``None``.
        """
        await self._ready.wait()
        async with self._lock:
            await self._ensure_browser_locked()
            pid = project_id or self._project_id
            try:
                # Видео в браузер передаём как base64 (JSON-safe), внутри страницы
                # декодируем в bytes и заливаем чанками, как делает само веб-приложение.
                b64 = base64.b64encode(data).decode("ascii")
                result = await self._page.evaluate(
                    """async ({b64, startUrl, putUrl, projectId, fileName, contentType, chunkSize}) => {
                        const bin = atob(b64);
                        const bytes = new Uint8Array(bin.length);
                        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
                        const s = await fetch(startUrl, {
                            method: 'POST',
                            credentials: 'include',
                            headers: {
                                'X-Upload-Project-Id': projectId || '',
                                'X-Upload-Content-Length': String(bytes.length),
                                'X-Upload-Content-Type': contentType,
                                'X-Upload-File-Name': encodeURIComponent(fileName),
                            },
                        });
                        const startText = await s.text();
                        if (!s.ok) return { startStatus: s.status, status: 0, text: startText };
                        let sessionUrl = null;
                        try { sessionUrl = JSON.parse(startText).sessionUrl; } catch (e) {}
                        if (!sessionUrl) return { startStatus: s.status, status: 0, text: startText };
                        let out = null;
                        for (let offset = 0; offset < bytes.length; offset += chunkSize) {
                            const isFinal = offset + chunkSize >= bytes.length;
                            const chunk = bytes.slice(offset, Math.min(offset + chunkSize, bytes.length));
                            const r = await fetch(putUrl, {
                                method: 'PUT',
                                credentials: 'include',
                                headers: {
                                    'X-Upload-Session-Url': sessionUrl,
                                    'X-Upload-Offset': String(offset),
                                    'X-Upload-Command': isFinal ? 'upload, finalize' : 'upload',
                                    'X-Upload-Project-Id': projectId || '',
                                    'X-Upload-File-Name': encodeURIComponent(fileName),
                                    'Content-Type': 'application/octet-stream',
                                },
                                body: chunk,
                            });
                            const text = await r.text();
                            out = { startStatus: s.status, status: r.status, text };
                            if (!r.ok) return out;
                        }
                        return out;
                    }""",
                    {
                        "b64": b64,
                        "startUrl": VIDEO_UPLOAD_START_URL,
                        "putUrl": VIDEO_UPLOAD_PUT_URL,
                        "projectId": pid,
                        "fileName": filename,
                        "contentType": content_type,
                        "chunkSize": 2 * 1024 * 1024,
                    },
                )
            except Exception as e:
                if self._is_target_closed_error(e):
                    await self._start_locked()
                log.error(f"❌ upload_video (page fetch): {e}")
                return None

            if not isinstance(result, dict):
                return None
            if result.get("status") != 200:
                log.warning(
                    "⚠️ upload_video: start → %s, PUT → %s",
                    result.get("startStatus"),
                    result.get("status"),
                )
                return None
            body = loads_xssi(result.get("text") or "")
            ids = upload_video_ids_from_response(body) if body is not None else None
            if ids and ids.get("mediaId"):
                ids.setdefault("_project_id", pid)
                log.info(
                    "⬆️ Видео загружено в Flow: mediaId=%s… workflowId=%s",
                    str(ids["mediaId"])[:8],
                    "есть" if ids.get("workflowId") else "нет",
                )
                return ids
            log.warning(
                "⚠️ upload_video: ответ 200, но id не распознаны (schema=%s)",
                describe_schema(body) if body is not None else "не-JSON",
            )
            return None

    async def _page_media_ids(self) -> set[str]:
        """Множество media-UUID, присутствующих в DOM страницы (src/href/srcset)."""
        try:
            urls = await self._page.evaluate(
                """() => {
                    const out = [];
                    for (const el of document.querySelectorAll('img,source,video,a,[style]')) {
                        if (el.src) out.push(el.src);
                        if (el.href) out.push(el.href);
                        if (el.srcset) out.push(el.srcset);
                        const bg = el.style && el.style.backgroundImage;
                        if (bg) out.push(bg);
                    }
                    return out;
                }"""
            )
        except Exception:
            return set()
        ids: set[str] = set()
        for url in urls or []:
            mid = id_from_media_url(url) if isinstance(url, str) else None
            if mid:
                ids.add(mid)
        return ids


# ───────────────────────────────────────────
# БЛОК 2 — HTTP-клиент, использует перехваченные токены
# ───────────────────────────────────────────


class FlowHttpClient:
    """
    Делает прямые HTTP запросы к Google API с токенами из живого браузера.
    Быстрее чем ждать пока браузер сам нарисует результат.
    """

    API_BASE = "https://aisandbox-pa.googleapis.com/v1"

    def __init__(self, keeper: SessionKeeper):
        self.keeper = keeper

    def _api_proxy(self) -> str | None:
        raw = (
            self.keeper.api_proxy_url
            if self.keeper.api_proxy_url is not None
            else API_PROXY_URL
        )
        return _effective_proxy_url(raw) or None

    def _build_headers(self, session: dict) -> dict:
        return build_flow_headers(session)

    @staticmethod
    def _video_ab_preview(text: str, limit: int = 240) -> str:
        if not text:
            return ""
        cleaned = re.sub(r"[\r\n\t]+", " ", str(text))
        cleaned = re.sub(r"(ya29\.|Bearer\s+|session-token)[^\s\"']+", r"\1***", cleaned)
        return cleaned[:limit]

    async def video_transport_ab_test(
        self,
        *,
        prompt: str,
        model_key: str = "omni-flash-4s",
        aspect: str = "landscape",
        order: str = "direct_first",
        pause_sec: float = 4.0,
        project_id: str | None = None,
        transports: list[str] | None = None,
    ) -> dict:
        """Costly admin diagnostic: compare direct HTTP and browser fetch video submit.

        This intentionally submits real text-to-video jobs. It does not poll or
        download outputs; the diagnostic target is initial submit acceptance
        (not final generation quality).
        """
        import uuid as _uuid
        import json as _json

        session = await self.keeper.get_session()
        if not session["bearer"]:
            return {"error": "missing_bearer", "arms": []}
        project_id = project_id or session.get("project_id")
        if not project_id:
            return {"error": "missing_project_id", "arms": []}

        headers = self._build_headers(session)
        proxy = self._api_proxy()
        action = VIDEO_RECAPTCHA_ACTION
        sess_id = f";{int(time.time() * 1000)}"
        transports = [str(t) for t in (transports or ["direct_http", "browser_fetch"])]
        transports = [t for t in transports if t in {"direct_http", "browser_fetch"}]
        if not transports:
            transports = ["direct_http"]
        if order == "browser_first" and transports == ["direct_http", "browser_fetch"]:
            transports.reverse()

        arms: list[dict] = []
        for idx, transport in enumerate(transports):
            if idx and pause_sec > 0:
                await asyncio.sleep(pause_sec)
            arm_started = time.time()
            captcha_token = await self.keeper.solve_captcha(action)
            if not captcha_token:
                arms.append({
                    "transport": transport,
                    "ok": False,
                    "status": None,
                    "error": "captcha_unavailable",
                    "duration_ms": int((time.time() - arm_started) * 1000),
                })
                continue

            payload = build_video_payload(
                prompt=prompt,
                project_id=project_id,
                captcha_token=captcha_token,
                aspect=aspect,
                model_key=model_key,
                session_id=sess_id,
                batch_id=str(_uuid.uuid4()),
            )
            status: int | None = None
            text = ""
            error = ""
            try:
                if transport == "direct_http":
                    async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                        async with http.post(
                            VIDEO_GEN_ENDPOINT,
                            headers=headers,
                            json=payload,
                            proxy=proxy,
                            timeout=aiohttp.ClientTimeout(total=75),
                        ) as resp:
                            status = resp.status
                            text = await resp.text()
                else:
                    resp = await self.keeper.post_json_via_browser(
                        VIDEO_GEN_ENDPOINT,
                        headers,
                        payload,
                        timeout_ms=75_000,
                    )
                    if isinstance(resp, dict):
                        status = int(resp.get("status") or 0)
                        text = str(resp.get("text") or "")
                    else:
                        error = "browser_post_failed"
            except Exception as exc:  # noqa: BLE001 - diagnostic result, no secrets
                error = exc.__class__.__name__

            parsed = {}
            if status == 200 and text:
                try:
                    info = parse_video_gen_response(_json.loads(text)) or {}
                    parsed = {
                        "accepted": bool(info),
                        "media_id_prefix": str(info.get("media_id") or "")[:8],
                        "project_id_present": bool(info.get("project_id")),
                    }
                except Exception:
                    parsed = {"accepted": False, "parse_error": True}

            arms.append({
                "transport": transport,
                "ok": status == 200 and not error,
                "status": status,
                "error": error or None,
                "duration_ms": int((time.time() - arm_started) * 1000),
                "response": parsed,
                "body_preview": "" if status == 200 else self._video_ab_preview(text),
            })

        return {
            "account": self.keeper.account_id,
            "model_key": model_key,
            "aspect": aspect,
            "order": transports,
            "prompt_chars": len(prompt or ""),
            "arms": arms,
            "summary": {
                arm["transport"]: {"status": arm.get("status"), "ok": arm.get("ok")}
                for arm in arms
            },
        }

    async def create_agent_session(self, project_id: str | None = None) -> str | None:
        """Create a fresh flowCreationAgent session for the given project.

        ``POST /flowCreationAgent/sessions?projectId=<raw>`` with an empty body
        returns ``sessionInfo.agentSessionId``. A fresh session per request keeps
        users' agent conversations isolated (no shared/global session state).
        ``project_id`` must be the user's per-user project (the account-level
        session project is often empty when PER_USER_PROJECTS is on)."""
        for attempt in range(2):
            session = await self.keeper.get_session()
            if not session["bearer"]:
                return None
            proj_raw = str(project_id or session.get("project_id") or "")
            if not proj_raw:
                return None
            headers = dict(self._build_headers(session))
            headers["Content-Type"] = "application/json"
            headers["Accept"] = "*/*"
            url = f"{self.API_BASE}/flowCreationAgent/sessions?projectId={proj_raw}"
            try:
                async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                    async with http.post(
                        url, headers=headers, data=b"{}", proxy=self._api_proxy(),
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        status = resp.status
                        if status == 401 and attempt == 0:
                            await self.keeper._refresh_bearer()
                            continue
                        if status != 200:
                            return None
                        data = await resp.json(content_type=None)
                sid = ((data or {}).get("sessionInfo") or {}).get("agentSessionId")
                return str(sid) if sid else None
            except Exception:  # noqa: BLE001 - best effort, caller handles None
                return None
        return None

    async def improve_prompt(
        self,
        text: str,
        *,
        action: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        agent_session_id: str | None = None,
        turn_number: int = 1,
        timeout_total: float = 90.0,
        debug: bool = False,
    ) -> dict:
        """Call Flow's ``flowCreationAgent:streamChat`` to improve a prompt.

        Reuses the live bearer/project/cookies and the browser-JS reCAPTCHA
        solver. Returns sanitized fields only (status, parsed variants/single,
        message) — never bearer/cookie/token values. The reСАPTCHA ``action`` is
        a parameter so the admin discovery probe can try candidates.
        """
        session = await self.keeper.get_session()
        if not session["bearer"]:
            return {"error": "missing_bearer"}
        project_id = project_id or session.get("project_id")
        if not project_id:
            return {"error": "missing_project_id"}

        # streamChat requires a real session id (a random UUID gets an empty
        # errorEvent). Create a fresh session per request unless one is supplied.
        if not agent_session_id:
            agent_session_id = await self.create_agent_session(project_id=project_id)
        if not agent_session_id:
            return {"error": "session_create_failed"}

        action = action or AGENT_RECAPTCHA_ACTION
        captcha_token = await self.keeper.solve_captcha(action)
        if not captcha_token:
            return {"error": "captcha_unavailable", "action": action}

        headers = dict(self._build_headers(session))
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "text/event-stream, text/event-stream"

        proj = str(project_id)
        if not proj.startswith("projects/"):
            proj = f"projects/{proj}"
        body = {
            "agentSessionId": agent_session_id,
            "agentClientContext": {
                "projectId": proj,
                "clientSessionId": session_id or f";{int(time.time() * 1000)}",
                "recaptchaContext": {
                    "token": captcha_token,
                    "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB",
                },
                "turnNumber": int(turn_number),
            },
            "userMessage": {"userPrompt": {"parts": [{"text": str(text or "")}]}},
        }
        url = f"{self.API_BASE}/flowCreationAgent:streamChat?alt=sse"

        status: int | None = None
        raw = ""
        error = ""
        try:
            async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                async with http.post(
                    url,
                    headers=headers,
                    data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                    proxy=self._api_proxy(),
                    timeout=aiohttp.ClientTimeout(total=timeout_total),
                ) as resp:
                    status = resp.status
                    raw = await resp.text()
        except Exception as exc:  # noqa: BLE001 - return JSON, never raw secrets
            error = exc.__class__.__name__

        parsed = (
            parse_agent_response(raw)
            if status == 200 and raw
            else {"variants": [], "single": None, "message": ""}
        )
        out = {
            "ok": status == 200 and not error and bool(parsed["variants"] or parsed["single"]),
            "status": status,
            "action": action,
            "error": error or None,
            "variants": parsed["variants"],
            "single": parsed["single"],
            "message": parsed["message"][:600],
            "agent_session_id": agent_session_id,
            "turn_number": int(turn_number),
            "body_preview": "" if status == 200 else self._video_ab_preview(raw),
        }
        if debug:
            out["agent_text_preview"] = self._video_ab_preview(extract_agent_text(raw), limit=2000)
            out["raw_len"] = len(raw or "")
            out["raw_preview"] = self._video_ab_preview(raw, limit=2000)
        return out

    async def agent_session_call(
        self, *, method: str = "GET", suffix: str = "", json_body: dict | None = None,
        project_id: str | None = None, timeout_total: float = 45.0,
    ) -> dict:
        """Explore/operate the flowCreationAgent/sessions endpoints (bearer only,
        no captcha). ``suffix`` is appended after ``/sessions`` (e.g. ``/<id>``).
        Returns sanitized status + raw preview."""
        session = await self.keeper.get_session()
        if not session["bearer"]:
            return {"error": "missing_bearer"}
        headers = dict(self._build_headers(session))
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "*/*"
        # The collection needs ?projectId=<raw uuid> (confirmed by browser
        # capture); a specific /<id> path does not.
        if "projectId=" not in suffix and not suffix.startswith("/"):
            proj_raw = str(project_id or session.get("project_id") or "")
            if proj_raw:
                sep = "&" if "?" in suffix else "?"
                suffix = f"{suffix}{sep}projectId={proj_raw}"
        url = f"{self.API_BASE}/flowCreationAgent/sessions{suffix}"
        status: int | None = None
        raw = ""
        error = ""
        try:
            async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                req = http.request(
                    method.upper(), url, headers=headers,
                    data=(json.dumps(json_body, ensure_ascii=False).encode("utf-8")
                          if json_body is not None else None),
                    proxy=self._api_proxy(),
                    timeout=aiohttp.ClientTimeout(total=timeout_total),
                )
                async with req as resp:
                    status = resp.status
                    raw = await resp.text()
        except Exception as exc:  # noqa: BLE001 - diagnostic, return JSON
            error = exc.__class__.__name__
        return {
            "status": status,
            "error": error or None,
            "raw_len": len(raw or ""),
            "raw_preview": self._video_ab_preview(raw, limit=2000),
        }

    async def generate_images(
        self,
        prompt: str,
        aspect_ratio: str = "landscape",
        num_images: int = 4,
        progress_cb=None,
        project_id: str | None = None,
        image_inputs: list | None = None,
        allow_browser_fallback: bool = True,
        image_model: str = DEFAULT_IMAGE_MODEL,
    ) -> dict:
        """Сгенерировать (или, при ``image_inputs``, отредактировать) изображения.

        ``project_id`` — проект конкретного Telegram-пользователя; если не задан,
        берётся проект текущей сессии (старое поведение). ``image_inputs``
        ссылается на исходное изображение для редактирования (см.
        ``flow_core.build_image_inputs``). При редактировании браузерный фолбэк
        отключают, чтобы не выдать вместо правки несвязанную картинку.
        """
        session = await self.keeper.get_session()

        if not session["bearer"]:
            log.warning("Bearer не получен — использую браузерный режим")
            if allow_browser_fallback:
                return await self.keeper.generate_via_browser(prompt)
            return {"error": "Нет Bearer-токена. Попробуйте позже."}

        project_id = project_id or session["project_id"]
        if not project_id:
            log.warning("project_id не получен — использую браузерный режим")
            # Нет проекта у сессии аккаунта = логин протух → пометить на релогин
            # и вывести из ротации (см. AccountPool.is_available).
            self.keeper._flag_needs_relogin(True)
            if allow_browser_fallback:
                return await self.keeper.generate_via_browser(prompt)
            return {"error": "Не удалось определить проект. Попробуйте позже."}

        url = f"{self.API_BASE}/projects/{project_id}/flowMedia:batchGenerateImages"
        seed = random.randint(100_000, 999_999)
        sess_id = f";{int(time.time() * 1000)}"

        # Ротация actions: пробуем каждый action пока Google не примет
        actions = list(RECAPTCHA_ACTIONS)
        saw_403 = False
        saw_unusual_activity = False

        for idx, action in enumerate(actions):
            if progress_cb:
                await progress_cb(flow_copy.msg("working"))
            captcha_token = await self.keeper.solve_captcha(action)

            if not captcha_token:
                continue

            payload = build_generation_payload(
                prompt=prompt,
                project_id=project_id,
                captcha_token=captcha_token,
                aspect=aspect_ratio,
                num_images=num_images,
                seed=seed,
                session_id=sess_id,
                image_inputs=image_inputs,
                image_model=image_model,
            )

            if image_inputs:
                if progress_cb:
                    await progress_cb(flow_copy.msg("applying_edit"))
            elif progress_cb:
                await progress_cb(flow_copy.msg("generating_n", n=num_images))

            try:
                async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                    proxy = self._api_proxy()
                    async with http.post(
                        url,
                        headers=self._build_headers(session),
                        json=payload,
                        proxy=proxy,
                        timeout=aiohttp.ClientTimeout(total=75),
                    ) as resp:
                        status = resp.status
                        text = await resp.text()
                        log.info(f"📡 HTTP ответ: {status} (action={action})")

            except Exception as e:
                log.error(f"❌ HTTP ошибка: {e}")
                # Текст исключения может содержать хосты бэкенда — юзеру нейтрально.
                return {"error": flow_copy.msg("gen_failed")}

            # 200 — успех
            if status == 200:
                import json as _json

                try:
                    return _json.loads(text)
                except Exception:
                    return {"error": flow_copy.msg("parse_failed")}

            # 401 — Bearer протух, обновляем и повторяем
            if status == 401:
                log.warning("🔑 Bearer устарел, обновляю...")
                await self.keeper._refresh_bearer()
                session = await self.keeper.get_session()
                continue

            # 403 — капча не прошла, пробуем следующий action
            if status == 403:
                saw_403 = True
                if "PUBLIC_ERROR_UNUSUAL_ACTIVITY" in text or "unusual activity" in text.lower():
                    saw_unusual_activity = True
                log.warning(f"⚠️ HTTP 403 action={action} ({idx+1}/{len(actions)}). Тело: {text[:300]}")
                continue

            if status == 429:
                return {"error": flow_copy.msg("rate_limited")}

            if status == 400:
                log.warning(f"⚠️ Prompt rejected (400): {text[:300]}")
                return {"error": flow_copy.msg("prompt_rejected"), "error_type": "prompt_rejected"}

            log.error(f"❌ Неизвестный статус {status}: {text[:300]}")
            return {"error": flow_copy.msg("service_error", status=status)}

        # Все actions провалились
        if saw_unusual_activity:
            # Флаг уровня аккаунта/сессии (см. комментарий у RECAPTCHA_ACTIONS) —
            # браузерный фолбэк упрётся в то же ограничение и просто потратит
            # ~25-50с на ожидание textarea, которая не появится. Сигналим
            # account_risk сразу, чтобы вызывающий код ушёл в кулдаун и
            # фейловернулся на другой аккаунт без лишнего ожидания.
            log.warning("Все actions провалились (unusual_activity) — без браузерного фолбэка")
            return {
                "error": flow_copy.msg("rate_limited"),
                "account_risk": "unusual_activity",
            }
        if allow_browser_fallback:
            log.warning("Все actions провалились, фолбек в браузер")
            return await self.keeper.generate_via_browser(prompt)
        log.warning("Все actions провалились (без браузерного фолбэка)")
        if saw_403:
            return {"error": flow_copy.msg("rate_limited")}
        return {"error": flow_copy.msg("gen_failed")}

    async def run_captured_request(
        self, capture: dict, media_id: str, project_id: str | None, progress_cb=None
    ) -> dict:
        """Воспроизвести изученный запрос НАСТОЯЩЕГО апскейла для картинки.

        Подставляет свежие captcha/project/session/media в захваченный шаблон и
        шлёт его. Возвращает разобранный JSON ответа сервиса или ``{"error": ...}``.
        """
        session = await self.keeper.get_session()
        if not session["bearer"]:
            return {"error": flow_copy.msg("upscale_unavailable")}
        project_id = project_id or session["project_id"]
        sess_id = f";{int(time.time() * 1000)}"

        for idx, action in enumerate(RECAPTCHA_ACTIONS):
            if progress_cb:
                await progress_cb(flow_copy.msg("upscaling"))
            captcha_token = await self.keeper.solve_captcha(action)
            if not captcha_token:
                continue
            built = apply_request_capture(
                capture,
                media_id=media_id,
                captcha=captcha_token,
                project_id=project_id or "",
                session_id=sess_id,
            )
            if not built:
                return {"error": flow_copy.msg("upscale_unavailable")}
            url, body = built
            try:
                async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                    proxy = self._api_proxy()
                    async with http.post(
                        url,
                        headers=self._build_headers(session),
                        json=body,
                        proxy=proxy,
                        timeout=aiohttp.ClientTimeout(total=180),
                    ) as resp:
                        status = resp.status
                        text = await resp.text()
                        log.info(f"📡 UPSCALE ответ: {status} (action={action})")
            except Exception as e:
                log.error(f"❌ UPSCALE сеть: {e}")
                return {"error": flow_copy.msg("gen_failed")}

            if status == 200:
                parsed = loads_xssi(text)
                return parsed if isinstance(parsed, dict) else {"error": flow_copy.msg("parse_failed")}
            if status == 401:
                await self.keeper._refresh_bearer()
                session = await self.keeper.get_session()
                continue
            if status == 403:
                log.warning(f"⚠️ UPSCALE 403 ({idx+1}). Тело: {text[:200]}")
                continue
            if status == 429:
                return {"error": flow_copy.msg("rate_limited")}
            log.error(f"❌ UPSCALE статус {status}: {text[:200]}")
            return {"error": flow_copy.msg("service_error", status=status)}

        return {"error": flow_copy.msg("upscale_unavailable")}

    async def upsample_image(
        self, media_id: str, project_id: str | None, *, progress_cb=None
    ) -> dict:
        """Родной серверный апскейл картинки (UI «Upscaled x2»).

        Синхронный POST ``flow/upsampleImage`` — ответ содержит готовую увеличенную
        картинку base64 (``encodedImage``), поллинг не нужен. Возвращает
        ``{"image_bytes": bytes}`` или ``{"error": ...}``. Контракт сверен из
        реального захвата (а не промпт-доработка, как «Чёткость ×2»).
        """
        session = await self.keeper.get_session()
        if not session["bearer"]:
            return {"error": flow_copy.msg("upscale_unavailable")}
        project_id = project_id or session["project_id"]
        sess_id = f";{int(time.time() * 1000)}"

        for idx, action in enumerate(RECAPTCHA_ACTIONS):
            if progress_cb:
                await progress_cb(flow_copy.msg("upscaling"))
            captcha_token = await self.keeper.solve_captcha(action)
            if not captcha_token:
                continue
            payload = build_upsample_payload(
                media_id=media_id,
                project_id=project_id or "",
                captcha_token=captcha_token,
                session_id=sess_id,
            )
            try:
                async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                    proxy = self._api_proxy()
                    async with http.post(
                        IMAGE_UPSAMPLE_ENDPOINT,
                        headers=self._build_headers(session),
                        json=payload,
                        proxy=proxy,
                        timeout=aiohttp.ClientTimeout(total=180),
                    ) as resp:
                        status = resp.status
                        text = await resp.text()
                        log.info(f"📡 UPSAMPLE ответ: {status} (action={action})")
            except Exception as e:
                log.error(f"❌ UPSAMPLE сеть: {e}")
                return {"error": flow_copy.msg("gen_failed")}

            if status == 200:
                import json as _json
                import base64
                try:
                    data = _json.loads(text)
                except Exception:
                    return {"error": flow_copy.msg("parse_failed")}
                enc = parse_upsample_response(data)
                if not enc:
                    return {"error": flow_copy.msg("upscale_unavailable")}
                try:
                    return {"image_bytes": base64.b64decode(enc)}
                except Exception:
                    return {"error": flow_copy.msg("parse_failed")}
            if status == 401:
                await self.keeper._refresh_bearer()
                session = await self.keeper.get_session()
                continue
            if status == 403:
                log.warning(f"⚠️ UPSAMPLE 403 ({idx+1}). Тело: {text[:200]}")
                continue
            if status == 429:
                return {"error": flow_copy.msg("rate_limited")}
            log.error(f"❌ UPSAMPLE статус {status}: {text[:200]}")
            return {"error": flow_copy.msg("service_error", status=status)}

        return {"error": flow_copy.msg("upscale_unavailable")}

    async def prepare_video_extend_scene(
        self,
        *,
        project_id: str | None,
        workflow_id: str | None,
    ) -> str | None:
        """Create/read the Flow scene required by native video Extend."""
        if not (project_id and workflow_id):
            return None

        session = await self.keeper.get_session()
        if not session["bearer"]:
            return None

        headers = self._build_headers(session)
        proxy = self._api_proxy()

        try:
            async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                async with http.post(
                    flow_scene_create_url(project_id),
                    headers=headers,
                    json={"workflowIds": [workflow_id]},
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        log.warning(f"video scene create -> {resp.status}: {text[:200]}")
                        return None
                    create_data = await resp.json(content_type=None)

                scene_id = parse_video_scene_id(create_data)
                if not scene_id:
                    return None

                async with http.get(
                    flow_scene_workflows_url(scene_id, project_id),
                    headers=headers,
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        workflows_data = await resp.json(content_type=None)
                        scene_id = parse_video_scene_id(workflows_data) or scene_id
                    else:
                        log.warning(f"video scene workflows -> {resp.status}")
                return scene_id
        except Exception as exc:
            log.warning(f"video scene prep failed: {exc}")
            return None

    async def fetch_full_extended_video(
        self, scene_id: str, project_id: str
    ) -> bytes | None:
        """Return the full stitched video for an extended scene, or None.

        Replays the service's own "download full" path: GET scene workflows to
        learn the ordered segment timeline, POST runVideoFxConcatenation, poll
        runVideoFxCheckConcatenationStatus, and base64-decode the resulting
        ``encodedVideo``. No local ffmpeg involved.
        """
        session = await self.keeper.get_session()
        if not session["bearer"]:
            return None
        headers = self._build_headers(session)
        proxy = self._api_proxy()
        try:
            async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                async with http.get(
                    flow_scene_workflows_url(scene_id, project_id),
                    headers=headers,
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json(content_type=None)

                segments = parse_scene_segments(data)
                if len(segments) < 2:
                    return None  # nothing to stitch; caller falls back to the segment

                async with http.post(
                    VIDEO_CONCAT_ENDPOINT,
                    headers=headers,
                    json=build_concat_payload(segments),
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status != 200:
                        log.warning(f"concat start -> {resp.status}")
                        return None
                    op_data = await resp.json(content_type=None)

                op_name = parse_concat_operation_name(op_data)
                if not op_name:
                    return None

                status_payload = build_concat_status_payload(op_name)
                for _ in range(VIDEO_CONCAT_POLL_MAX):
                    await asyncio.sleep(VIDEO_CONCAT_POLL_INTERVAL)
                    async with http.post(
                        VIDEO_CONCAT_STATUS_ENDPOINT,
                        headers=headers,
                        json=status_payload,
                        proxy=proxy,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        if resp.status != 200:
                            continue
                        st_data = await resp.json(content_type=None)
                    status, encoded = parse_concat_status(st_data)
                    if status == VIDEO_STATUS_SUCCESSFUL:
                        if not encoded:
                            return None
                        import base64
                        try:
                            return base64.b64decode(encoded)
                        except Exception:
                            log.exception("concat encodedVideo decode failed")
                            return None
                    if status == VIDEO_STATUS_FAILED:
                        log.warning("concat job failed")
                        return None
                log.warning("concat polling timed out")
                return None
        except Exception as exc:
            log.warning(f"fetch_full_extended_video failed: {exc}")
            return None

    async def generate_video(
        self,
        prompt: str,
        model_key: str = "omni-flash-4s",
        aspect: str = "landscape",
        project_id: str | None = None,
        reference_sources: list[dict] | None = None,
        start_source: dict | None = None,
        end_source: dict | None = None,
        operation: str = "generate",
        source_media_id: str | None = None,
        source_workflow_id: str | None = None,
        source_scene_id: str | None = None,
        source_duration_s: float | None = None,
        progress_cb=None,
    ) -> dict:
        """Сгенерировать видео через асинхронный Flow video API.

        Шаги:
          1. POST ``video:batchAsyncGenerateVideoText`` — получаем media_id.
          2. Раз в ``VIDEO_POLL_INTERVAL`` секунд POST
             ``video:batchCheckAsyncVideoGenerationStatus`` — ждём
             ``MEDIA_GENERATION_STATUS_SUCCESSFUL``.
          3. Возвращаем ``{"media_id": ..., "project_id": ...}`` (URL видео
             нужно получить отдельно — см. ``fetch_video_url``).

        Возвращает ``{"error": ...}`` при неудаче.
        """
        # Все три режима захвачены и включены: text / Frames (старт-финиш) /
        # Ingredients (reference-to-video). Эндпоинт и payload выбираются по входу.
        import uuid as _uuid

        session = await self.keeper.get_session()
        if not session["bearer"]:
            return {"error": "Нет Bearer-токена для видео"}

        project_id = project_id or session.get("project_id")
        if not project_id:
            return {"error": "Нет project_id для видео"}

        sess_id  = f";{int(time.time() * 1000)}"
        headers  = self._build_headers(session)
        proxy    = self._api_proxy()
        reference_images = build_video_reference_images(reference_sources)
        start_image, end_image = build_video_frame_images(start_source, end_source)
        operation = (operation or "generate").strip().lower()
        is_edit = operation == "edit"
        is_extend = operation == "extend"
        is_frames = bool(start_image or end_image) and not (is_edit or is_extend)
        is_reference = bool(reference_images) and not is_frames and not (is_edit or is_extend)
        if is_edit:
            if not (source_media_id and source_workflow_id):
                return {"error": flow_copy.msg("vid_extend_unavailable")}
            gen_endpoint = VIDEO_EDIT_ENDPOINT
            endpoint_name = "Edit"
        elif is_extend:
            if not (source_media_id and source_scene_id):
                return {"error": flow_copy.msg("vid_extend_unavailable")}
            gen_endpoint = VIDEO_EXTEND_ENDPOINT
            endpoint_name = "Extend"
        elif is_frames:
            gen_endpoint = VIDEO_FRAMES_ENDPOINT
            endpoint_name = "Frames"
        elif is_reference:
            gen_endpoint = VIDEO_REFERENCE_ENDPOINT
            endpoint_name = "Reference"
        else:
            gen_endpoint = VIDEO_GEN_ENDPOINT
            endpoint_name = "Text"
        if is_frames:
            effective_model_key = video_frames_model_key(model_key)
        elif is_reference:
            effective_model_key = video_reference_model_key(model_key, aspect)
        elif is_edit:
            effective_model_key = "abra_edit"
        else:
            effective_model_key = video_model_key(model_key)
        model_family = str((video_model_meta(model_key) or {}).get("family") or "unknown")

        def _submit_meta(data: dict) -> dict:
            data.update({
                "model_key": effective_model_key,
                "model_family": model_family,
                "endpoint": endpoint_name.lower(),
                "mode": endpoint_name.lower(),
                "transport": "browser_fetch" if browser_fallback_used else "direct_http",
                "attempts": attempts_made,
                "had_403": had_403,
                "unusual_403": unusual_403,
                "browser_fallback": browser_fallback_used,
            })
            return data

        # TEMP (capture-driven): trace r2v/frames request shape to diagnose the
        # "ingredients video never generates" bug. No secrets — endpoint/model/aspect only.
        if is_reference or is_frames:
            log.info(
                "🎬 r2v req account=%s project=%s endpoint=%s effective_model_key=%s "
                "aspect=%s ref_media_ids=%s frames=%s",
                self.keeper.account_id, project_id, endpoint_name, effective_model_key,
                aspect, [r.get("mediaId") for r in reference_images],
                bool(start_image or end_image),
            )

        # ── Шаг 1: капча + отправка (единственный верный action + ретраи) ──
        # action = VIDEO_GENERATION (подтверждён захватом). 403 = низкий score /
        # антифрод, поэтому при 403 ретраим тот же action со свежим токеном и
        # коротким бэкоффом; перебор неверных action'ов убран (он лишь усиливал
        # флаг аккаунта). Каждая попытка — свежий токен и свежий batchId.
        if progress_cb:
            await progress_cb("⏳ Отправляю запрос на генерацию видео…")

        gen_status: int | None = None
        gen_text = ""
        solved_any = False
        refreshed_after_403 = False
        refreshed_after_401 = False
        had_403 = False
        unusual_403 = False
        attempts_made = 0
        action = VIDEO_RECAPTCHA_ACTION
        browser_fallback_used = False

        def _build_submit_payload(captcha_token: str) -> dict:
            batch_id = str(_uuid.uuid4())
            if is_edit:
                return build_video_edit_payload(
                    prompt=prompt,
                    project_id=project_id,
                    captcha_token=captcha_token,
                    aspect=aspect,
                    session_id=sess_id,
                    batch_id=batch_id,
                    source_media_id=source_media_id or "",
                    source_workflow_id=source_workflow_id or "",
                    end_frame_index=video_edit_end_frame(source_duration_s),
                )
            if is_extend:
                return build_video_extend_payload(
                    prompt=prompt,
                    project_id=project_id,
                    captcha_token=captcha_token,
                    aspect=aspect,
                    model_key=model_key,
                    session_id=sess_id,
                    batch_id=batch_id,
                    source_media_id=source_media_id or "",
                    scene_id=source_scene_id or "",
                )
            return build_video_payload(
                prompt=prompt,
                project_id=project_id,
                captcha_token=captcha_token,
                aspect=aspect,
                model_key=model_key,
                session_id=sess_id,
                batch_id=batch_id,
                reference_images=reference_images,
                start_image=start_image,
                end_image=end_image,
            )

        for _attempt in range(VIDEO_GEN_MAX_ATTEMPTS):
            posted = False
            for _auth_attempt in range(2):
                captcha_token = await self.keeper.solve_captcha(action)
                if not captcha_token:
                    break
                solved_any = True
                posted = True
                payload = _build_submit_payload(captcha_token)
                try:
                    async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                        async with http.post(
                            gen_endpoint,
                            headers=headers,
                            json=payload,
                            proxy=proxy,
                            timeout=aiohttp.ClientTimeout(total=60),
                        ) as resp:
                            gen_status = resp.status
                            gen_text   = await resp.text()
                except Exception as exc:
                    log.error("🎬 video network error: %s", exc)
                    return {"error": flow_copy.msg("vid_gen_failed")}

                if gen_status == 401 and not refreshed_after_401:
                    log.warning("🎬 video → 401 (action=%s), refreshing bearer and retrying", action)
                    refreshed_after_401 = True
                    await self.keeper._refresh_bearer()
                    session = await self.keeper.get_session()
                    headers = self._build_headers(session)
                    continue
                break

            if not posted:
                continue
            attempts_made = _attempt + 1

            if gen_status == 403:
                had_403 = True
                if "PUBLIC_ERROR_UNUSUAL_ACTIVITY" in gen_text or "unusual activity" in gen_text.lower():
                    unusual_403 = True
                log.warning(
                    "🎬 video → 403 (попытка %d/%d, score/антифрод), свежий токен",
                    _attempt + 1, VIDEO_GEN_MAX_ATTEMPTS,
                )
                if not refreshed_after_403:
                    refreshed_after_403 = True
                    await self.keeper._refresh_bearer()
                    session = await self.keeper.get_session()
                    headers = self._build_headers(session)
                # Нарастающий бэкофф + jitter перед СЛЕДУЮЩЕЙ попыткой; после
                # последней 403 не спим зря (всё равно выходим из цикла).
                if _attempt < VIDEO_GEN_MAX_ATTEMPTS - 1:
                    backoff = VIDEO_GEN_403_BACKOFF_SEC * (_attempt + 1) + random.uniform(1.0, 4.0)
                    await asyncio.sleep(backoff)
                continue
            log.info(f"🎬 video {endpoint_name} → {gen_status} (action={action})")
            break

        if not solved_any:
            return _submit_meta({"error": "Не удалось решить капчу для видео"})
        if gen_status == 401:
            return _submit_meta({
                "error": "Bearer устарел, попробуйте ещё раз",
                "account_risk": "video_auth",
            })
        if gen_status == 429:
            return _submit_meta({
                "error": flow_copy.msg("rate_limited"),
            })
        if gen_status == 403:
            return _submit_meta({
                "error": "Сервис отклонил запрос видео (403): низкий score/антифрод reCAPTCHA.",
                "account_risk": "video_recaptcha_403",
                "had_403": True,
                "unusual_403": unusual_403,
            })
        if gen_status != 200:
            # TEMP (capture-driven): log the real API error body (no auth headers).
            log.warning("🎬 video %s non-200 status=%s body=%s",
                        endpoint_name, gen_status, gen_text[:300])
            return _submit_meta({"error": flow_copy.msg("service_error", status=gen_status)})

        try:
            import json as _json
            gen_data = _json.loads(gen_text)
        except Exception:
            return _submit_meta({"error": "Не удалось разобрать ответ генерации видео"})

        media_info = parse_video_gen_response(gen_data)
        if not media_info:
            # TEMP (capture-driven): 200 OK but no media id — log a snippet of the body.
            log.warning("🎬 video %s 200 but no media_id; body=%s",
                        endpoint_name, gen_text[:300])
            return _submit_meta({"error": "media_id не найден в ответе"})

        media_id   = media_info["media_id"]
        project_id = media_info["project_id"]
        workflow_id = media_info.get("workflow_id")
        scene_id = media_info.get("scene_id") or (source_scene_id if is_extend else None)
        log.info(f"🎬 media_id={media_id}, ожидаю готовности…")

        # ── Шаг 2: polling ─────────────────────────────────────────────
        poll_payload = build_video_poll_payload(media_id, project_id)
        deadline     = time.time() + VIDEO_POLL_TIMEOUT
        poll_num     = 0

        while time.time() < deadline:
            await asyncio.sleep(VIDEO_POLL_INTERVAL)
            poll_num += 1

            # Анимация статусных фраз во время ожидания живёт в _video_generate_and_send
            # (фоновая задача, обновление каждые 2.5 сек) — здесь её больше не дублируем,
            # чтобы две правки одного сообщения не конфликтовали.

            try:
                async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                    async with http.post(
                        VIDEO_POLL_ENDPOINT,
                        headers=headers,
                        json=poll_payload,
                        proxy=proxy,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        if resp.status != 200:
                            log.warning(f"⚠️ poll {poll_num} → {resp.status}")
                            continue
                        poll_data = await resp.json(content_type=None)
            except Exception as exc:
                log.warning(f"⚠️ poll {poll_num} ошибка: {exc}")
                continue

            status, poll_item = check_video_poll_status(poll_data)
            log.info(f"🎬 poll {poll_num}: {status}")

            if status == VIDEO_STATUS_SUCCESSFUL:
                if isinstance(poll_item, dict):
                    item_workflow_id = poll_item.get("workflowId")
                    if isinstance(item_workflow_id, str) and item_workflow_id:
                        workflow_id = item_workflow_id
                    item_scene_id = poll_item.get("sceneId")
                    if isinstance(item_scene_id, str) and item_scene_id:
                        scene_id = item_scene_id
                return _submit_meta({
                    "media_id":   media_id,
                    "project_id": project_id,
                    "workflow_id": workflow_id,
                    "scene_id":    scene_id,
                    "status":     "ok",
                })
            if status == VIDEO_STATUS_FAILED:
                # Причина из тела FAILED-итема: звук не сгенерился / модерация —
                # это контент-фейлы (не вина аккаунта), их показываем юзеру.
                reason = _video_failure_reason(poll_item)
                try:
                    log.warning("🎬 FAILED item (reason=%s): %s",
                                reason or "?", _json.dumps(poll_item, ensure_ascii=False)[:600])
                except Exception:
                    pass
                if reason == "audio_filtered":
                    return _submit_meta({"error": "audio filter", "failure": "audio_filtered"})
                if reason == "danger_filter":
                    return _submit_meta({"error": "danger filter", "failure": "danger_filter"})
                return _submit_meta({"error": "Генерация видео завершилась с ошибкой на стороне Google"})

        return _submit_meta({"error": f"Таймаут ({VIDEO_POLL_TIMEOUT}с): видео не готово"})

    async def wait_video_ready(
        self,
        media_id: str,
        project_id: str,
        timeout: float = 120,
        interval: float = 3,
    ) -> dict | None:
        """Дождаться готовности ЗАГРУЖЕННОГО видео (серверный транскод).

        Веб-приложение после upload поллит ``batchCheckAsyncVideoGenerationStatus``
        (PENDING → SUCCESSFUL, см. захват --upload-edit, seq 19/21) и лишь потом
        разрешает Edit. Правка до готовности завершается
        ``MEDIA_GENERATION_STATUS_FAILED`` на стороне сервиса.

        Возвращает poll-итем готового видео (в нём ``video.dimensions.length`` —
        реальная длительность клипа для endFrameIndex) или ``None``.
        """
        session = await self.keeper.get_session()
        if not session["bearer"] or not project_id:
            return None
        headers = self._build_headers(session)
        proxy = self._api_proxy()
        payload = build_video_poll_payload(media_id, project_id)
        deadline = time.time() + timeout
        poll_num = 0
        while time.time() < deadline:
            poll_num += 1
            try:
                async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
                    async with http.post(
                        VIDEO_POLL_ENDPOINT,
                        headers=headers,
                        json=payload,
                        proxy=proxy,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        if resp.status != 200:
                            log.warning(f"⚠️ upload-ready poll {poll_num} → {resp.status}")
                        else:
                            data = await resp.json(content_type=None)
                            status, item = check_video_poll_status(data)
                            log.info(f"⬆️ upload-ready poll {poll_num}: {status}")
                            if status == VIDEO_STATUS_SUCCESSFUL:
                                if not isinstance(item, dict):
                                    # Молчаливый {} маскировал бы битый ответ: без
                                    # poll-итема не узнать длительность клипа.
                                    log.warning(
                                        "⚠️ upload-ready: SUCCESSFUL, но poll-итем не dict (%s)",
                                        type(item).__name__,
                                    )
                                    return {}
                                return item
                            if status == VIDEO_STATUS_FAILED:
                                return None
            except Exception as exc:
                log.warning(f"⚠️ upload-ready poll {poll_num} ошибка: {exc}")
            await asyncio.sleep(interval)
        log.warning("⚠️ upload-ready: таймаут %sс (media_id=%s…)", timeout, str(media_id)[:8])
        return None

    async def fetch_video_bytes(self, media_id: str) -> bytes | None:
        """Скачать готовое видео по ``media_id``.

        Фронт Flow резолвит media_id в файл через tRPC-редирект на labs.google
        (подтверждено по ``<video>.src`` страницы):
            GET https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=<id>
        Эндпоинт на хосте labs.google (НЕ API), авторизация — cookies браузерной
        сессии, не Bearer. Запрос 302-редиректит на реальные байты видео;
        aiohttp по умолчанию следует за редиректом.

        Возвращает байты видео или ``None`` при неудаче.
        """
        session = await self.keeper.get_session()
        cookies = session.get("cookies") or {}
        url = video_media_redirect_url(media_id)
        proxy = self._api_proxy()

        # Заголовки лёгкие: это запрос к фронту labs.google, не к API.
        headers = {
            "Accept": "*/*",
            "Referer": "https://labs.google/fx/tools/flow",
        }
        ua = session.get("headers", {}).get("user-agent")
        if ua:
            headers["User-Agent"] = ua

        try:
            async with aiohttp.ClientSession(cookies=cookies) as http:
                async with http.get(
                    url,
                    headers=headers,
                    proxy=proxy,
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status != 200:
                        log.warning(f"⚠️ fetch_video_bytes → {resp.status} ({url})")
                        return None
                    data = await resp.read()
                    log.info(f"🎬 видео скачано: {len(data)} байт (media_id={media_id})")
                    return data
        except Exception as exc:
            log.error(f"❌ fetch_video_bytes: {exc}")
            return None
