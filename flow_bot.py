"""
Google Flow Bot — гибридный подход
Playwright держит живую сессию и перехватывает Bearer + капчу,
HTTP-клиент делает запросы с этими свежими данными.

Установка зависимостей:
    pip install aiogram playwright aiohttp aiohttp-socks python-dotenv
    playwright install chromium

.env файл:
    TELEGRAM_TOKEN=...
    USER_DATA_DIR=./google_profile   # папка с профилем Chrome (уже залогиненным)
    PROXY_URL=                        # optional shared proxy URL
    BROWSER_PROXY_URL=                # optional: proxy for Chrome only; off/none/direct disables it
    API_PROXY_URL=                    # optional: proxy for Flow HTTP only; off/none/direct disables it
    CAPMONSTER_PROXY_URL=             # optional: proxy for CapMonster only; off/none/direct disables it
    TG_PROXY_URL=                     # optional Telegram SOCKS5 proxy URL
    OWNER_ID=                         # опционально: один или несколько Telegram ID через запятую
    ADMIN_IDS=                        # ID админов через запятую — могут /grant <id> <кредиты>
    USER_CREDITS_FILE=user_credits.json   # опц.: путь к файлу балансов
    TWOCAPTCHA_KEY=                   # ключ 2captcha (включает платный фолбэк решения капчи)
    CAPMONSTER_KEY=                   # ключ CapMonster Cloud (альтернатива 2captcha)
    CAPTCHA_PROVIDER=auto             # auto | browser | capmonster | 2captcha — порядок решения капчи
    TWOCAPTCHA_SCORE=0.3              # запрашиваемый минимальный скор v3 (выше ~0.3 почти недостижимо)
    TWOCAPTCHA_SOLVE_TIMEOUT=150      # сек ожидания решения от 2captcha (у либы дефолт 600)
    CAPMONSTER_SOLVE_TIMEOUT=120      # сек ожидания решения от CapMonster
"""

import asyncio
import html
import json
import logging
import os
import random
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from urllib.parse import unquote, urlparse

import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command
from dotenv import load_dotenv
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
    loads_xssi,
    load_edit_capture,
    media_source_from_response,
    parse_action_callback,
    result_pairs,
    save_edit_capture,
)
from flow_core import (
    CreditStore,
    PaymentStore,
    STARS_PACKS,
    action_price,
    pack as credit_pack,
    pack_label,
    public_pack_ids,
    price_gen,
)
from flow_core import (
    VIDEO_MODELS,
    VIDEO_UI_ASPECTS,
    video_model_meta,
    video_price,
    video_extend_price,
    VIDEO_EXTEND_STEP,
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
    parse_video_gen_response,
    parse_video_scene_id,
    parse_scene_segments,
    build_concat_payload,
    parse_concat_operation_name,
    build_concat_status_payload,
    parse_concat_status,
    check_video_poll_status,
    video_media_redirect_url,
)
import flow_copy
import metrics

# ───────────────────────────────────────────
load_dotenv(".env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "PASTE_YOUR_TOKEN_HERE")
USER_DATA_DIR = os.getenv("USER_DATA_DIR", "./google_profile")
# Метрики: ярлык Flow-аккаунта (для flow_jobs) и курс Stars→₽ для выручки.
FLOW_ACCOUNT_ID = os.getenv("FLOW_ACCOUNT_ID", "default")
try:
    STARS_TO_RUB = float(os.getenv("STARS_TO_RUB", "1.3"))  # ~₽ за 1 Star, best-effort
except (TypeError, ValueError):
    STARS_TO_RUB = 1.3
PROXY_URL = os.getenv("PROXY_URL", "")          # общий прокси по умолчанию (http/socks5)
BROWSER_PROXY_URL = os.getenv("BROWSER_PROXY_URL")
API_PROXY_URL = os.getenv("API_PROXY_URL")
CAPMONSTER_PROXY_URL = os.getenv("CAPMONSTER_PROXY_URL")
TG_PROXY_URL = os.getenv("TG_PROXY_URL", "")    # SOCKS5 прокси для Telegram API
TWOCAPTCHA_KEY = os.getenv("TWOCAPTCHA_KEY", "")
CAPMONSTER_KEY = os.getenv("CAPMONSTER_KEY", "")
# auto: browser → capmonster → 2captcha; или явно: browser | capmonster | 2captcha
CAPTCHA_PROVIDER = os.getenv("CAPTCHA_PROVIDER", "auto").strip().lower()
TWOCAPTCHA_SCORE = os.getenv("TWOCAPTCHA_SCORE", "0.3").strip()
try:
    TWOCAPTCHA_SOLVE_TIMEOUT = int(os.getenv("TWOCAPTCHA_SOLVE_TIMEOUT", "150"))
except ValueError:
    TWOCAPTCHA_SOLVE_TIMEOUT = 150
try:
    CAPMONSTER_SOLVE_TIMEOUT = int(os.getenv("CAPMONSTER_SOLVE_TIMEOUT", "120"))
except ValueError:
    CAPMONSTER_SOLVE_TIMEOUT = 120
def _parse_ids(raw: str) -> set[int]:
    """Разобрать список Telegram ID из строки (разделители — запятая/точка с запятой)."""
    return {
        int(x) for x in (raw or "").replace(";", ",").split(",")
        if x.strip().isdigit()
    }


# OWNER_ID и ADMIN_IDS оба поддерживают НЕСКОЛЬКО ID через запятую/точку с запятой.
OWNER_IDS = _parse_ids(os.getenv("OWNER_ID", ""))
# Админы бота — могут начислять кредиты командой /grant и видят тест-пакет.
# Объединяем с владельцами: каждый owner — администратор.
ADMIN_IDS = _parse_ids(os.getenv("ADMIN_IDS", "")) | OWNER_IDS
FLOW_URL = "https://labs.google/fx/tools/flow"
# Файл с картой telegram_user_id -> flow_project_id (каждый юзер = свой проект).
USER_PROJECTS_FILE = os.getenv("USER_PROJECTS_FILE", "user_projects.json")
# Файл с захваченным форматом запроса редактирования (imageInputs). Бот учится
# ему один раз, перехватив реальную правку в открытом окне Flow.
EDIT_CAPTURE_FILE = os.getenv("EDIT_CAPTURE_FILE", "edit_capture.json")
# Сырой дамп перехваченной правки (реальные значения imageInputs + id недавних
# картинок) для ручной достройки шаблона. gitignored, без bearer/recaptcha.
EDIT_CAPTURE_RAW_FILE = os.getenv("EDIT_CAPTURE_RAW_FILE", "edit_capture_raw.json")
# Недавно выданные картинки (id), сохраняются на диск чтобы пережить рестарт.
RECENT_IMAGES_FILE = os.getenv("RECENT_IMAGES_FILE", "recent_images.json")
# Схемы ответов на загрузку фото (values-free) если mediaId не нашёлся — для доводки.
UPLOAD_CAPTURE_FILE = os.getenv("UPLOAD_CAPTURE_FILE", "upload_capture.json")
# Изученный формат запроса НАСТОЯЩЕГО апскейла (перехватывается из браузера один раз).
UPSCALE_CAPTURE_FILE = os.getenv("UPSCALE_CAPTURE_FILE", "upscale_capture.json")
# Выдавать каждому юзеру отдельный проект (можно выключить: PER_USER_PROJECTS=0).
PER_USER_PROJECTS = os.getenv("PER_USER_PROJECTS", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
# После стольких подряд неудач создания проектов фича отключается на сессию,
# чтобы не висеть на каждом сообщении (откат на общий проект сессии).
PROJECT_CREATION_MAX_FAILURES = 3

COOLDOWN_SEC = 10  # минимум между запросами одного юзера (анти-абуз)
# Если кулдаун не вышел, но осталось не больше этого — подождём и выполним,
# а не отклоняем запрос. Дольше — просим повторить позже.
MAX_AUTO_WAIT_SEC = 10
TOKEN_TTL_SEC = 50 * 60  # обновлять Bearer каждые 50 минут

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


def _normalize_proxy_url(proxy_url: str) -> str:
    proxy_url = (proxy_url or "").strip()
    if proxy_url and "://" not in proxy_url:
        return "http://" + proxy_url
    return proxy_url


def _proxy_is_disabled(proxy_url: str | None) -> bool:
    return (proxy_url or "").strip().lower() in ("0", "false", "no", "off", "none", "direct")


def _effective_proxy_url(proxy_url: str | None, fallback: str = PROXY_URL) -> str:
    if proxy_url is None:
        proxy_url = fallback
    if _proxy_is_disabled(proxy_url):
        return ""
    return _normalize_proxy_url(proxy_url)


def _playwright_proxy_config(proxy_url: str) -> dict | None:
    """Convert PROXY_URL into Playwright's split proxy auth shape."""
    proxy_url = _effective_proxy_url(proxy_url, fallback="")
    if not proxy_url:
        return None
    try:
        p = urlparse(proxy_url)
        scheme = p.scheme.lower()
        if scheme not in ("http", "https", "socks4", "socks5"):
            return None
        if not p.hostname:
            return None
        server = f"{scheme}://{p.hostname}"
        if p.port:
            server += f":{p.port}"
        config: dict = {"server": server}
        if p.username:
            config["username"] = unquote(p.username)
        if p.password:
            config["password"] = unquote(p.password)
        return config
    except Exception:
        return None


def _host_path(url: str) -> str:
    """host + path of a URL (no query/secrets) — safe to log for diagnostics."""
    try:
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        return f"{parts.netloc}{parts.path}"
    except Exception:
        return "?"


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

    def __init__(self):
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

    async def _start_locked(self):
        """Запускает браузер и открывает Flow. Вызывать один раз при старте."""
        try:
            self._ready.clear()
            self._clear_session_cache()
            await self._close_browser_locked()
            self._pw = await async_playwright().start()

            launch_kwargs = dict(
                user_data_dir=USER_DATA_DIR,
                channel="chrome",
                headless=False,  # False — обязательно, иначе Google блокирует
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            browser_proxy_url = _effective_proxy_url(BROWSER_PROXY_URL)
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
            log.info("✅ Браузер готов!")

        except Exception as e:
            self._ready.clear()
            log.error(f"💥 Ошибка запуска браузера: {e}")
            raise

    async def _close_browser_locked(self):
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

                clicked = False
                candidates = [
                    lambda: tab.get_by_role("button", name=re.compile(r"new flow", re.I)),
                    lambda: tab.get_by_role("link", name=re.compile(r"new flow", re.I)),
                    lambda: tab.get_by_text(re.compile(r"^\s*new flow\s*$", re.I)),
                    lambda: tab.get_by_role("button", name=re.compile(r"new project|create", re.I)),
                ]
                for getter in candidates:
                    try:
                        loc = getter().first
                        if await loc.count() > 0 and await loc.is_visible(timeout=1_000):
                            await loc.click(timeout=5_000)
                            clicked = True
                            break
                    except Exception:
                        continue

                if not clicked:
                    log.warning("⚠️ Кнопка 'New flow' не найдена — новый проект не создан")
                    return None

                # Ждём, пока вкладка перейдёт на новый /project/ и сделает запрос.
                try:
                    await tab.wait_for_url("**/project/**", timeout=15_000)
                except Exception:
                    pass

                deadline = time.time() + 8
                while time.time() < deadline:
                    pid = self._project_id_from_url(tab.url) or next(
                        (p for p in seen_pids if p != baseline_pid), None
                    )
                    if pid and pid != baseline_pid:
                        log.info(f"🆕 Новый проект создан: {pid}")
                        return pid
                    await asyncio.sleep(0.5)

                log.warning("⚠️ Новый project_id не появился после клика 'New flow'")
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
    RECAPTCHA_ACTIONS = ["IMAGE_GENERATION", "PINHOLE", "batchGenerateImages"]
    # Видео-эндпоинт проверяет reCAPTCHA-action отдельно от картинок: токен,
    # выданный под action картинок, сервер отклоняет (403). Точный video-action
    # фронта Flow не зафиксирован, поэтому перебираем кандидатов (см.
    # generate_video) — первый, который сервер примет, и есть рабочий.
    # Current live result: VIDEO_GENERATION is verified. Later values are only
    # defensive fallbacks if Google changes validation.
    VIDEO_RECAPTCHA_ACTIONS = [
        # Live smoke 2026-06-08: accepted by video:batchAsyncGenerateVideoText.
        "VIDEO_GENERATION",
        "PINHOLE",
        "batchAsyncGenerateVideoText",
        "GENERATE_VIDEO",
        "IMAGE_GENERATION",
    ]

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

    @staticmethod
    def _provider_order() -> list[str]:
        """Очередь провайдеров решения капчи по CAPTCHA_PROVIDER (см. .env)."""
        if CAPTCHA_PROVIDER == "2captcha":
            return ["2captcha", "browser"]
        if CAPTCHA_PROVIDER == "capmonster":
            return ["capmonster", "browser"]
        if CAPTCHA_PROVIDER == "browser":
            return ["browser"]
        # auto: browser (бесплатно, лучший скор) → capmonster → 2captcha
        return ["browser", "capmonster", "2captcha"]

    async def solve_captcha(self, action: str = "IMAGE_GENERATION") -> str:
        """Решает reCAPTCHA для конкретного ``action`` и возвращает токен.

        Провайдеры пробуются в порядке ``_provider_order()`` (управляется
        ``CAPTCHA_PROVIDER``):
        - ``browser`` — ``grecaptcha.enterprise.execute()`` прямо в живом Chrome;
        - ``capmonster`` — CapMonster Cloud с куками браузера и опциональным прокси;
        - ``2captcha`` — 2captcha (без кук → обычно 403 от Google Flow).
        Финальный фолбэк: перехват токена из реального запроса браузера.
        """
        await self.ensure_browser()

        for provider in self._provider_order():
            if provider == "browser":
                browser_token = await self._solve_via_browser_js(action)
                if browser_token:
                    return browser_token
            elif provider == "capmonster":
                if not CAPMONSTER_KEY:
                    continue
                token = await self._solve_via_capmonster(action)
                if token:
                    return token
                log.warning("⚠️ CapMonster не дала токен, пробую следующий способ...")
            elif provider == "2captcha":
                if not TWOCAPTCHA_KEY:
                    continue
                token = await self._solve_via_2captcha(action)
                if token:
                    return token
                log.warning("⚠️ 2captcha не дала токен, пробую следующий способ...")

        # Финальный фолбэк: перехват из реального запроса браузера.
        try:
            return await self._get_fresh_captcha() or ""
        except Exception as e:
            if not self._is_target_closed_error(e):
                raise
            log.warning("Browser context closed while refreshing captcha, restarting...")
            await self.ensure_browser()
            return ""

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

    async def _solve_via_2captcha(self, action: str) -> str:
        """Решает reCAPTCHA v3 Enterprise через 2captcha и возвращает токен.

        Тонкости обёртки ``2captcha-python`` (учтены ниже, иначе сервис работает
        «вхолостую»):
        - порог скора задаётся параметром ``score`` (НЕ ``min_score``); неизвестные
          kwargs молча уходят в API и игнорируются — из-за чего прежняя версия
          фактически не задавала порог вообще;
        - скор выше ~0.3 на v3 получить почти невозможно (по доке 2captcha),
          поэтому дефолт ``TWOCAPTCHA_SCORE`` умеренный;
        - дефолтный ``recaptchaTimeout`` либы — 600 c: неудачный солв подвешивал
          запрос на 10 минут, поэтому укорачиваем до ``TWOCAPTCHA_SOLVE_TIMEOUT``.
        Блокирующий вызов вынесен в executor, чтобы не стопорить event loop.
        """
        try:
            from twocaptcha import TwoCaptcha
        except ImportError:
            log.error("❌ pip install 2captcha-python")
            return ""

        sitekey = self._recaptcha_sitekey or self.RECAPTCHA_SITEKEY
        solver = TwoCaptcha(
            TWOCAPTCHA_KEY,
            recaptchaTimeout=TWOCAPTCHA_SOLVE_TIMEOUT,
            pollingInterval=5,
        )
        captcha_url = "https://labs.google/fx/tools/flow"

        try:
            log.info(
                f"🔐 2captcha: action={action} score≥{TWOCAPTCHA_SCORE} "
                f"timeout={TWOCAPTCHA_SOLVE_TIMEOUT}s url={captcha_url}..."
            )
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: solver.recaptcha(
                    sitekey,
                    captcha_url,
                    version="v3",
                    enterprise=1,
                    action=action,
                    score=TWOCAPTCHA_SCORE,
                ),
            )
            token = result.get("code", "") if isinstance(result, dict) else ""
            if token:
                log.info(f"✅ 2captcha решена action={action} (len={len(token)})")
                return token
            log.warning(f"⚠️ 2captcha вернула пустой ответ action={action}: {result!r}")
        except Exception as e:
            log.error(f"❌ 2captcha action={action}: {e}")

        return ""

    async def _solve_via_capmonster(self, action: str) -> str:
        """Решает reCAPTCHA v3 Enterprise через CapMonster Cloud.

        Передаёт куки живого браузера и user-agent — сервис решает капчу
        от имени залогиненного Google-аккаунта. Если PROXY_URL задан,
        передаёт и прокси: тогда IP совпадает с IP API-запроса → 403 маловероятен.
        """
        try:
            sitekey = self._recaptcha_sitekey or self.RECAPTCHA_SITEKEY

            # Куки из живого Chrome (NID, SID, HSID… — ключ к высокому скору)
            cookies_str = ""
            try:
                raw = await self._context.cookies("https://labs.google")
                cookies_str = "; ".join(f"{c['name']}={c['value']}" for c in raw)
            except Exception:
                pass

            ua = ""
            try:
                ua = await self._page.evaluate("navigator.userAgent")
            except Exception:
                pass

            task: dict = {
                "type": "RecaptchaV3EnterpriseTask",
                "websiteURL": "https://labs.google/fx/tools/flow",
                "websiteKey": sitekey,
                "minScore": float(TWOCAPTCHA_SCORE),
                "pageAction": action,
            }
            if cookies_str:
                task["cookies"] = cookies_str
            if ua:
                task["userAgent"] = ua

            proxy_fields = self._proxy_fields_for_capmonster()
            if proxy_fields:
                task.update(proxy_fields)

            log.info(
                f"🔐 CapMonster: action={action} score≥{TWOCAPTCHA_SCORE} "
                f"cookies={'yes' if cookies_str else 'no'} "
                f"proxy={'yes' if proxy_fields else 'no'}..."
            )

            async with aiohttp.ClientSession() as http:
                async with http.post(
                    "https://api.capmonster.cloud/createTask",
                    json={"clientKey": CAPMONSTER_KEY, "task": task},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    data = await resp.json(content_type=None)

            if data.get("errorId") or not data.get("taskId"):
                log.warning(f"⚠️ CapMonster createTask error: {data}")
                return ""

            task_id = data["taskId"]
            deadline = asyncio.get_event_loop().time() + CAPMONSTER_SOLVE_TIMEOUT

            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(4)
                async with aiohttp.ClientSession() as http:
                    async with http.post(
                        "https://api.capmonster.cloud/getTaskResult",
                        json={"clientKey": CAPMONSTER_KEY, "taskId": task_id},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        result = await resp.json(content_type=None)

                status = result.get("status")
                if status == "ready":
                    token = (result.get("solution") or {}).get("gRecaptchaResponse", "")
                    if token:
                        log.info(f"✅ CapMonster решена action={action} (len={len(token)})")
                    else:
                        log.warning(f"⚠️ CapMonster пустой токен: {result}")
                    return token
                if status != "processing":
                    log.warning(f"⚠️ CapMonster ошибка: {result}")
                    return ""

            log.warning(f"⚠️ CapMonster timeout action={action}")
            return ""

        except Exception as e:
            log.error(f"❌ CapMonster action={action}: {e}")
            return ""

    @staticmethod
    def _proxy_fields_for_capmonster() -> dict:
        """Парсит прокси в поля CapMonster task (proxyType/Address/Port/…)."""
        proxy_url = _effective_proxy_url(CAPMONSTER_PROXY_URL)
        if not proxy_url:
            return {}
        try:
            p = urlparse(proxy_url)
            scheme = p.scheme.lower()
            if scheme not in ("http", "https", "socks4", "socks5"):
                return {}
            fields: dict = {
                "proxyType": scheme,
                "proxyAddress": p.hostname or "",
                "proxyPort": p.port or 80,
            }
            if p.username:
                fields["proxyLogin"] = p.username
            if p.password:
                fields["proxyPassword"] = p.password
            return fields
        except Exception:
            return {}

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
            # Просто перезагружаем страницу проекта — браузер сам пойдёт к API
            await self._page.reload(timeout=30_000)
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

    # ── получение свежей капчи ─────────────

    async def _get_fresh_captcha(self) -> str | None:
        """
        Запускаем ОДНУ генерацию в браузере с пустым промптом,
        перехватываем тело запроса и достаём оттуда reCAPTCHA токен.
        Это настоящий токен из реального Chrome — Google его принимает.
        """
        captcha_token = None

        async def intercept(request):
            nonlocal captcha_token
            if (
                "batchGenerateImages" in request.url
                or "batchGenerateVideos" in request.url
            ):
                try:
                    body = request.post_data
                    if body:
                        import json as _json

                        data = _json.loads(body)
                        token = (
                            data.get("clientContext", {})
                            .get("recaptchaContext", {})
                            .get("token")
                        )
                        if token:
                            captcha_token = token
                            log.info(f"🎯 Капча перехвачена (len={len(token)})")
                except Exception:
                    pass

        self._page.on("request", intercept)

        try:
            # Вводим короткий нейтральный промпт, нажимаем Enter
            ta = self._page.locator("#PINHOLE_TEXT_AREA_ELEMENT_ID")
            await ta.wait_for(state="visible", timeout=10_000)
            await ta.click()
            await self._page.keyboard.press("Control+A")
            await self._page.keyboard.press("Backspace")
            await ta.type("test", delay=50)

            # Ждём запрос (капча генерируется при нажатии Enter)
            try:
                async with self._page.expect_request(
                    lambda r: "batchGenerate" in r.url, timeout=15_000
                ):
                    await self._page.keyboard.press("Enter")
            except Exception:
                pass

            await asyncio.sleep(2)

        finally:
            try:
                self._page.remove_listener("request", intercept)
            except Exception:
                pass

        return captcha_token

    # ── публичный метод ────────────────────

    async def get_session(self) -> dict:
        """
        Возвращает актуальные данные сессии для HTTP-запроса.
        Обновляет Bearer если он старше TOKEN_TTL_SEC.
        """
        await self._ready.wait()
        async with self._lock:
            await self._ensure_browser_locked()
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
                    return {"error": f"HTTP {resp.status}: {text[:200]}"}

                return await resp.json()

            except Exception as e:
                log.error(f"❌ generate_via_browser: {e}")
                return {"error": str(e)}

    async def upload_image(self, data: bytes, filename: str = "upload.png") -> dict | None:
        """Загрузить присланное фото в Flow через файловый input браузера.

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

    def _build_headers(self, session: dict) -> dict:
        bearer = session["bearer"]
        extra = session["headers"]

        headers = {
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "text/plain;charset=UTF-8",
            "Accept": "*/*",
            "Accept-Language": "ru,ru-RU;q=0.9,en-US;q=0.8,en;q=0.7",
            "Origin": "https://labs.google",
            "Referer": "https://labs.google/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
        }

        # Добавляем заголовки перехваченные из реального браузера
        mapping = {
            "user-agent": "User-Agent",
            "sec-ch-ua": "Sec-Ch-Ua",
            "sec-ch-ua-platform": "Sec-Ch-Ua-Platform",
            "x-browser-channel": "X-Browser-Channel",
            "x-browser-copyright": "X-Browser-Copyright",
            "x-browser-year": "X-Browser-Year",
            "x-browser-validation": "X-Browser-Validation",
            "x-client-data": "X-Client-Data",
        }
        for src, dst in mapping.items():
            if src in extra:
                headers[dst] = extra[src]

        return headers

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
            if allow_browser_fallback:
                return await self.keeper.generate_via_browser(prompt)
            return {"error": "Не удалось определить проект. Попробуйте позже."}

        url = f"{self.API_BASE}/projects/{project_id}/flowMedia:batchGenerateImages"
        seed = random.randint(100_000, 999_999)
        sess_id = f";{int(time.time() * 1000)}"

        # Ротация actions: пробуем каждый action пока Google не примет
        actions = list(SessionKeeper.RECAPTCHA_ACTIONS)

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
                    proxy = _effective_proxy_url(API_PROXY_URL) or None
                    async with http.post(
                        url,
                        headers=self._build_headers(session),
                        json=payload,
                        proxy=proxy,
                        timeout=aiohttp.ClientTimeout(total=120),
                    ) as resp:
                        status = resp.status
                        text = await resp.text()
                        log.info(f"📡 HTTP ответ: {status} (action={action})")

            except Exception as e:
                log.error(f"❌ HTTP ошибка: {e}")
                return {"error": f"Сетевая ошибка: {e}"}

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
                log.warning(f"⚠️ HTTP 403 action={action} ({idx+1}/{len(actions)}). Тело: {text[:300]}")
                continue

            if status == 429:
                return {"error": flow_copy.msg("rate_limited")}

            log.error(f"❌ Неизвестный статус {status}: {text[:300]}")
            return {"error": flow_copy.msg("service_error", status=status)}

        # Все actions провалились
        if allow_browser_fallback:
            log.warning("Все actions провалились, фолбек в браузер")
            return await self.keeper.generate_via_browser(prompt)
        log.warning("Все actions провалились (без браузерного фолбэка)")
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

        for idx, action in enumerate(SessionKeeper.RECAPTCHA_ACTIONS):
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
                    proxy = _effective_proxy_url(API_PROXY_URL) or None
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

        for idx, action in enumerate(SessionKeeper.RECAPTCHA_ACTIONS):
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
                    proxy = _effective_proxy_url(API_PROXY_URL) or None
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
        proxy = _effective_proxy_url(API_PROXY_URL) or None

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
        proxy = _effective_proxy_url(API_PROXY_URL) or None
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
        proxy    = _effective_proxy_url(API_PROXY_URL) or None
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

        # TEMP (capture-driven): trace r2v/frames request shape to diagnose the
        # "ingredients video never generates" bug. No secrets — endpoint/model/aspect only.
        if is_reference or is_frames:
            log.info(
                "🎬 r2v req endpoint=%s model_key=%s aspect=%s ref_images=%d frames=%s",
                endpoint_name, model_key, aspect, len(reference_images),
                bool(start_image or end_image),
            )

        # ── Шаг 1: капча + отправка с авто-перебором video-action ───────
        # Видео-эндпоинт отклоняет (403) reCAPTCHA-токен, выданный под action
        # картинок. Точный video-action не зафиксирован, поэтому перебираем
        # кандидатов: на каждый — свежий токен и свежий batchId; первый
        # не-403 ответ принимаем. Рабочий action логируется (зафиксировать).
        if progress_cb:
            await progress_cb("⏳ Отправляю запрос на генерацию видео…")

        gen_status: int | None = None
        gen_text = ""
        solved_any = False
        for action in SessionKeeper.VIDEO_RECAPTCHA_ACTIONS:
            captcha_token = await self.keeper.solve_captcha(action)
            if not captcha_token:
                continue
            solved_any = True
            batch_id = str(_uuid.uuid4())
            if is_edit:
                payload = build_video_edit_payload(
                    prompt=prompt,
                    project_id=project_id,
                    captcha_token=captcha_token,
                    aspect=aspect,
                    session_id=sess_id,
                    batch_id=batch_id,
                    source_media_id=source_media_id or "",
                    source_workflow_id=source_workflow_id or "",
                )
            elif is_extend:
                payload = build_video_extend_payload(
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
            else:
                payload = build_video_payload(
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
                return {"error": f"Сетевая ошибка при запуске видео: {exc}"}

            if gen_status == 403:
                log.warning(f"🎬 video → 403 (action={action}), пробую следующий action")
                continue
            log.info(f"🎬 video {endpoint_name} → {gen_status} (action={action})")
            break

        if not solved_any:
            return {"error": "Не удалось решить капчу для видео"}
        if gen_status == 401:
            await self.keeper._refresh_bearer()
            return {"error": "Bearer устарел, попробуйте ещё раз"}
        if gen_status == 429:
            return {"error": flow_copy.msg("rate_limited")}
        if gen_status == 403:
            return {"error": "Сервис отклонил запрос видео (403) на всех action."}
        if gen_status != 200:
            # TEMP (capture-driven): log the real API error body (no auth headers).
            log.warning("🎬 video %s non-200 status=%s body=%s",
                        endpoint_name, gen_status, gen_text[:300])
            return {"error": f"HTTP {gen_status}: {gen_text[:200]}"}

        try:
            import json as _json
            gen_data = _json.loads(gen_text)
        except Exception:
            return {"error": "Не удалось разобрать ответ генерации видео"}

        media_info = parse_video_gen_response(gen_data)
        if not media_info:
            # TEMP (capture-driven): 200 OK but no media id — log a snippet of the body.
            log.warning("🎬 video %s 200 but no media_id; body=%s",
                        endpoint_name, gen_text[:300])
            return {"error": "media_id не найден в ответе"}

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

            if progress_cb and poll_num % 3 == 0:
                elapsed = int(poll_num * VIDEO_POLL_INTERVAL)
                await progress_cb(f"⏳ Генерация видео… {elapsed}с")

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
                return {
                    "media_id":   media_id,
                    "project_id": project_id,
                    "model_key":  model_key,
                    "workflow_id": workflow_id,
                    "scene_id":    scene_id,
                    "status":     "ok",
                }
            if status == VIDEO_STATUS_FAILED:
                return {"error": "Генерация видео завершилась с ошибкой на стороне Google"}

        return {"error": f"Таймаут ({VIDEO_POLL_TIMEOUT}с): видео не готово"}

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
        proxy = _effective_proxy_url(API_PROXY_URL) or None

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


# ───────────────────────────────────────────
# БЛОК 3 — Telegram бот
# ───────────────────────────────────────────


def _make_bot() -> Bot:
    """Создаёт Bot с SOCKS5 прокси если TG_PROXY_URL задан."""
    if TG_PROXY_URL:
        try:
            from aiohttp_socks import ProxyConnector

            _proxy_url = TG_PROXY_URL.replace("socks5h://", "socks5://", 1)

            class _SocksSession(AiohttpSession):
                # Один коннектор и одна сессия на весь процесс.
                _connector = None
                _shared: aiohttp.ClientSession = None

                async def create_session(self) -> aiohttp.ClientSession:
                    if (
                        _SocksSession._shared is not None
                        and not _SocksSession._shared.closed
                    ):
                        return _SocksSession._shared
                    if _SocksSession._connector is None or _SocksSession._connector.closed:
                        _SocksSession._connector = ProxyConnector.from_url(
                            _proxy_url, rdns=True
                        )
                    _SocksSession._shared = aiohttp.ClientSession(
                        connector=_SocksSession._connector,
                        connector_owner=False,
                    )
                    return _SocksSession._shared

            log.info(f"🧦 Telegram через SOCKS5: {TG_PROXY_URL}")
            return Bot(token=TELEGRAM_TOKEN, session=_SocksSession())
        except ImportError:
            log.error("❌ aiohttp-socks не установлен! pip install aiohttp-socks")

    return Bot(token=TELEGRAM_TOKEN)


keeper = SessionKeeper()
client = FlowHttpClient(keeper)
bot = _make_bot()
dp = Dispatcher()

user_last_request: dict[int, float] = defaultdict(float)
# Юзеры с запросом «в работе» — чтобы параллельные запросы не абузили.
user_busy: set[int] = set()

# Каждый Telegram-пользователь -> свой Flow-проект (переживает рестарт).
project_store = UserProjectStore(USER_PROJECTS_FILE)
# token -> ImageRef для инлайн-кнопок (в памяти, ограниченный размер).
image_registry = ImageRegistry()
# token -> VideoRef для кнопки «скачать видео» (тот же класс реестра).
video_registry = ImageRegistry(max_entries=2000)
# user_id -> token: пользователь нажал «Редактировать» и мы ждём его текст-правку.
pending_edits: dict[int, str] = {}
# user_id -> список картинок-«ингредиентов», выбранных кнопкой «➕ В микс».
mix_baskets: dict[int, list[dict]] = defaultdict(list)
MIX_MAX = 4

# Баланс кредитов на пользователя (монетизация).
credit_store = CreditStore(os.getenv("USER_CREDITS_FILE", "user_credits.json"))
payment_store = PaymentStore(os.getenv("PAYMENTS_FILE", "payments.json"))

# Метрики (SQLite). init_db не бросает; log_* безопасны при сбое БД.
try:
    metrics.init_db(os.getenv("METRICS_DB", "metrics.db"))
except Exception:
    log.warning("metrics.init_db failed; metrics disabled", exc_info=True)


def _username(message_or_user) -> str | None:
    """Best-effort @username/имя для метрик (никогда не бросает)."""
    try:
        u = getattr(message_or_user, "from_user", message_or_user)
        return u.username or u.full_name
    except Exception:
        return None

# ── состояние кнопочного визарда генерации (в памяти) ──────────────────
# user_id -> {"step", "count", "fmt", "msg_id", "await": "prompt|edit|revary|photo",
#             "ref_token": <для edit/revary>, "last": {...настройки повтора...}}
wizard_state: dict[int, dict] = defaultdict(dict)


def _ws(user_id: int) -> dict:
    return wizard_state[user_id]


def _reset_image_flow(user_id: int, *, keep_last: bool = True) -> None:
    """Сбросить незавершённый image-флоу: состояние визарда И ожидание правки.

    ``pending_edits`` живёт отдельно от ``_ws``, поэтому ``_ws.clear()`` его не
    трогает — без этого сброса загруженное для правки фото «залипает» и
    следующий промпт уходит на правку старой картинки. ``keep_last`` сохраняет
    настройки прошлой генерации как дефолты визарда.
    """
    st = _ws(user_id)
    last = st.get("last") if keep_last else None
    st.clear()
    pending_edits.pop(user_id, None)
    if last:
        st["last"] = last
        st["count"] = last.get("count", DEFAULT_COUNT)
        st["fmt"] = _aspect_to_fmt(last.get("aspect", "landscape"))
        st["imodel"] = last.get("imodel", DEFAULT_IMAGE_MODEL)


def _fmt_to_aspect(fmt: str) -> str:
    return {
        "land": "landscape",
        "port": "portrait",
        "sq": "square",
        "f43": "landscape_43",
        "f34": "portrait_34",
    }.get(fmt, "landscape")

# Авто-отключение per-user проектов после серии неудач (откат на общий проект).
_project_creation_failures = 0
_per_user_projects_enabled = PER_USER_PROJECTS

# parse_result / result_pairs импортированы из flow_core (общая логика разбора).


class RateLimited(Exception):
    """Запрос отклонён: у пользователя уже выполняется другой запрос."""


@asynccontextmanager
async def user_slot(user_id: int, message: types.Message):
    """Удержать «слот» пользователя на время запроса.

    Поведение:
    - Если уже идёт запрос этого пользователя — отклоняем (анти-абуз).
    - Если кулдаун не вышел, но осталось ≤ ``MAX_AUTO_WAIT_SEC`` — ждём остаток
      и выполняем (не отклоняем за раннее нажатие).
    - Слот гарантированно освобождается в ``finally``.
    """
    if user_id in user_busy:
        await message.answer("⏳ Ваш предыдущий запрос ещё выполняется — дождитесь его.")
        raise RateLimited

    elapsed = time.time() - user_last_request[user_id]
    remaining = COOLDOWN_SEC - elapsed
    if remaining > 0:
        if remaining > MAX_AUTO_WAIT_SEC:
            await message.answer(f"⏱️ Слишком часто. Подождите ещё {int(remaining)} сек.")
            raise RateLimited
        await message.answer(f"⏱️ Подождите {int(remaining) + 1} сек, выполняю...")
        await asyncio.sleep(remaining)

    user_busy.add(user_id)
    try:
        yield
    finally:
        user_busy.discard(user_id)
        user_last_request[user_id] = time.time()


class NotEnoughCredits(Exception):
    """У пользователя недостаточно кредитов для действия."""


class _Charge:
    """Контейнер результата платного действия: ставим .ok=True при успехе."""

    __slots__ = ("ok",)

    def __init__(self) -> None:
        self.ok = False


@asynccontextmanager
async def credit_gate(
    user_id: int, action: str, message: types.Message, num_images: int = 1, *, surcharge: int = 0
):
    """Списать кредиты за действие; вернуть при неуспехе (charge-on-success).

    Резервируем стоимость на входе; если тело не выставило ``charge.ok``, делаем
    рефанд. Бесплатные действия (цена 0) проходят без списания. При нехватке
    средств показываем экран пополнения и поднимаем ``NotEnoughCredits``.
    ``surcharge`` — доплата сверх базовой цены (например, премиум-модель картинки).
    """
    price = action_price(action, num_images) + max(0, int(surcharge))
    if price <= 0:
        yield _Charge()  # бесплатно — без списания
        return

    have = credit_store.balance(user_id)
    if have < price:
        kb = types.InlineKeyboardMarkup(
            inline_keyboard=[[_menu_button("topup", "m:topup")], [_menu_button("menu", "m:menu")]]
        )
        await message.answer(
            flow_copy.msg("low_balance", needed=price, have=have), reply_markup=kb,
            parse_mode="HTML",
        )
        raise NotEnoughCredits

    credit_store.charge(user_id, price)
    charge = _Charge()
    try:
        yield charge
    finally:
        if not charge.ok:
            credit_store.refund(user_id, price)


async def ensure_user_project(user_id: int) -> str | None:
    """Вернуть Flow-проект пользователя, создав его при первом обращении.

    При неудаче создания (или при выключенной фиче) возвращает ``None`` —
    вызывающий код тогда использует общий проект сессии (старое поведение),
    чтобы генерация всё равно работала. После ``PROJECT_CREATION_MAX_FAILURES``
    неудач подряд фича отключается на сессию, чтобы не висеть на каждом запросе.
    """
    global _project_creation_failures, _per_user_projects_enabled

    existing = project_store.get(user_id)
    if existing:
        return existing
    if not _per_user_projects_enabled:
        return None

    try:
        pid = await keeper.create_new_project()
    except Exception:
        log.exception("create_new_project failed")
        pid = None

    if pid:
        _project_creation_failures = 0
        project_store.set(user_id, pid)
        log.info(f"📋 Пользователю {user_id} выдан проект {pid}")
        return pid

    _project_creation_failures += 1
    if _project_creation_failures >= PROJECT_CREATION_MAX_FAILURES:
        _per_user_projects_enabled = False
        log.warning(
            "⚠️ Per-user проекты отключены после %d неудач — работаю на общем проекте сессии.",
            _project_creation_failures,
        )
    return None


def _image_keyboard(token: str) -> types.InlineKeyboardMarkup:
    """Инлайн-кнопки действий для конкретной выданной картинки."""
    B = types.InlineKeyboardButton

    def b(action: str, copy_key: str) -> types.InlineKeyboardButton:
        # Show the credit cost on paid actions; free ones (download) stay clean.
        label = flow_copy.label(copy_key)
        price = action_price(copy_key)
        if price > 0:
            label = f"{label} · {price}⭐"
        return B(text=label, callback_data=action_callback_data(action, token))

    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [b("edit", "edit"), b("vary", "revary")],
            [b("regen", "regen"), b("realup", "realup")],
            [b("download", "dl_raw")],
        ]
    )


# ── меню и визард (кнопочный UX) ──────────────────────────────────────

L = flow_copy.label


def _menu_button(copy_key: str, data: str) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text=L(copy_key), callback_data=data)


def main_menu_kb(show_repeat: bool = False) -> types.InlineKeyboardMarkup:
    rows = [
        [_menu_button("gen", "m:gen")],
        [_menu_button("vid_gen", "m:vid")],
        [_menu_button("myphoto", "m:myphoto"), _menu_button("balance", "m:balance")],
        [_menu_button("help", "m:help")],
    ]
    if show_repeat:
        rows.insert(0, [_menu_button("repeat_last", "m:repeat")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


DEFAULT_COUNT = 1
DEFAULT_FMT = "land"
_FMT_NAMES = {"land": "16:9", "port": "9:16", "sq": "1:1", "f43": "4:3", "f34": "3:4"}


SELECT_STYLE = "success"  # green — Bot API 9.4 colour for the chosen wizard option


def _sel_btn(label: str, chosen: bool, callback_data: str) -> types.InlineKeyboardButton:
    """Wizard-option button; the chosen one turns green via Bot API 9.4 ``style``.

    ``style="success"`` (Bot API 9.4, Feb 2026) colours the button green on the
    client. aiogram 3.24 doesn't type the field, but pydantic forwards it in the
    outgoing JSON, so we pass it as an extra kwarg only when selected. Clients
    older than 9.4 simply ignore the unknown field — the label stays readable, just
    without the colour (graceful degradation), so no checkmark prefix is needed.
    """
    kwargs = {"text": label, "callback_data": callback_data}
    if chosen:
        kwargs["style"] = SELECT_STYLE
    return types.InlineKeyboardButton(**kwargs)


def _imodel_row(selected: str, prefix: str = "w:imodel") -> list:
    """Ряд выбора модели картинки (Nano Banana 2 / Pro) с наценкой в подписи."""
    B = types.InlineKeyboardButton
    row = []
    for mid, meta in IMAGE_MODELS.items():
        label = meta["label"]
        if meta["extra"]:
            label = f"{label} +{meta['extra']}"
        row.append(_sel_btn(label, mid == selected, f"{prefix}:{mid}"))
    return row


def _fmt_rows(fmt: str, prefix: str = "w:fmt") -> list:
    """Два ряда выбора формата картинки (16:9 / 4:3 / 1:1 / 3:4 / 9:16)."""
    B = types.InlineKeyboardButton

    def fb(code: str, key: str):
        return _sel_btn(L(key), fmt == code, f"{prefix}:{code}")

    return [
        [fb("land", "fmt:land"), fb("f43", "fmt:f43"), fb("sq", "fmt:sq")],
        [fb("f34", "fmt:f34"), fb("port", "fmt:port")],
    ]


def wizard_kb(count: int, fmt: str, imodel: str = DEFAULT_IMAGE_MODEL) -> types.InlineKeyboardMarkup:
    """Один экран: количество + формат + модель + «Сгенерировать» (выбор — зелёная кнопка)."""
    B = types.InlineKeyboardButton
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _sel_btn(L("cnt:1"), count == 1, "w:cnt:1"),
                _sel_btn(L("cnt:2"), count == 2, "w:cnt:2"),
                _sel_btn(L("cnt:4"), count == 4, "w:cnt:4"),
            ],
            *_fmt_rows(fmt, "w:fmt"),
            _imodel_row(imodel, "w:imodel"),
            [_menu_button("go", "w:go")],
            [_menu_button("cancel", "w:cancel")],
        ]
    )


def edit_settings_kb(fmt: str, imodel: str) -> types.InlineKeyboardMarkup:
    """Формат + модель для редактирования фото (правку пользователь вводит текстом).

    Callback-префикс ``es:`` намеренно не пересекается с ``edit:`` (кнопка
    «Изменить» под картинкой), иначе хендлер перехватил бы её.
    """
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            *_fmt_rows(fmt, "es:fmt"),
            _imodel_row(imodel, "es:imodel"),
            [_menu_button("cancel", "es:cancel")],
        ]
    )


def reply_menu_kb() -> types.ReplyKeyboardMarkup:
    """Постоянная клавиатура внизу чата — всегда под рукой."""
    B = types.KeyboardButton
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [B(text=L("kb_gen")), B(text=L("kb_vid"))],
            [B(text=L("kb_menu")), B(text=L("kb_balance"))],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Опиши картинку или жми «🎨 Создать картинку»",
    )


def _wizard_text(user_id: int) -> str:
    st = _ws(user_id)
    count = st.get("count", DEFAULT_COUNT)
    fmt = st.get("fmt", DEFAULT_FMT)
    imodel = st.get("imodel", DEFAULT_IMAGE_MODEL)
    total_price = price_gen(count) + image_model_extra(imodel) * count
    text = flow_copy.msg(
        "wizard_screen",
        count=count,
        fmt=_FMT_NAMES.get(fmt, fmt),
        price=total_price,
        credits=credit_store.balance(user_id),
    )
    # Если пользователь уже прислал промпт в чат — показываем его над настройками.
    pending = st.get("pending_prompt")
    if pending:
        # HTML screen → escape the echoed user prompt (could contain < > &).
        return flow_copy.msg("wizard_prompt_note", prompt=html.escape(pending[:80])) + text
    return text


async def _edit_or_answer(
    message: types.Message, text: str, kb, *, parse_mode: str | None = None
) -> types.Message | None:
    """Edit the wizard message in place; swallow the harmless "not modified" error.

    Re-tapping an already-selected wizard button rebuilds an identical screen, and
    Telegram rejects ``edit_text`` with "message is not modified". We must NOT fall
    back to ``answer`` there — that posts a duplicate panel. Only a genuine edit
    failure (message too old / deleted) falls through to a fresh ``answer``.
    Returns the message that now carries the wizard (edited or freshly sent), or
    ``None`` when the no-op edit was swallowed.
    """
    try:
        return await message.edit_text(text, reply_markup=kb, parse_mode=parse_mode)
    except Exception as exc:
        if "not modified" in str(exc).lower():
            return None
        return await message.answer(text, reply_markup=kb, parse_mode=parse_mode)


async def show_wizard(message: types.Message, *, user_id: int, edit: bool):
    st = _ws(user_id)
    st.setdefault("count", DEFAULT_COUNT)
    st.setdefault("fmt", DEFAULT_FMT)
    st.setdefault("imodel", DEFAULT_IMAGE_MODEL)
    st["step"] = "wizard"
    kb = wizard_kb(st["count"], st["fmt"], st["imodel"])
    text = _wizard_text(user_id)
    if edit:
        await _edit_or_answer(message, text, kb, parse_mode="HTML")
    else:
        metrics.log_event("wizard_started", user_id=user_id, source="image")
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


# ── видео-визард (кнопочный UX, префикс v:) ────────────────────────────
# Состояние живёт в том же wizard_state[user_id], но ключи с префиксом v*,
# чтобы не пересекаться с визардом картинок (step/count/fmt/await/...).

VID_DEFAULT_FMT = "land"
VID_DEFAULT_COUNT = 1
VID_REF_DEFAULT_MODEL = "veo-fast"
# Frames (старт/финиш-кадр) дефолтится на veo-lite: единственный interpolation-
# ключ, подтверждённый живым захватом (veo_3_1_interpolation_lite). Остальные
# tiers — догадка по паттерну, пока не подтверждены живым прогоном.
VID_FRAMES_DEFAULT_MODEL = "veo-lite"
_VID_FMT_TO_ASPECT = {"land": "landscape", "port": "portrait"}
_VID_FMT_NAMES = {"land": "16:9", "port": "9:16"}


def _vid_clear(user_id: int) -> None:
    """Очистить только видео-ключи (сохранив vlast для повтора и vretry для ретрая)."""
    st = wizard_state[user_id]
    keep = {k: st.get(k) for k in ("vlast", "vretry") if k in st}
    for key in list(st):
        if key.startswith("v") and key not in keep:
            st.pop(key, None)
    st.update(keep)


def _vid_clear_reference_inputs(user_id: int) -> None:
    """Drop mode-specific image/caption inputs before a plain text video run."""
    st = wizard_state[user_id]
    for key in ("ving_photos", "vfrm_start", "vfrm_end", "vcaption_prompt"):
        st.pop(key, None)


def _video_plain_text_ready(st: dict) -> bool:
    """True when the text-to-video settings screen can accept a chat prompt."""
    if st.get("vawait"):
        return False
    if st.get("vstep") != "vsettings":
        return False
    if st.get("vmode", "text") != "text":
        return False
    model_id = st.get("vmodel")
    if not model_id or not video_model_meta(model_id):
        return False
    vfmt = st.get("vfmt")
    if vfmt not in _VID_FMT_TO_ASPECT:
        return False
    try:
        vcount = int(st.get("vcount"))
    except (TypeError, ValueError):
        return False
    return clamp_num_videos(vcount) == vcount


def _vid_family_min_price(code: str) -> int:
    """Минимальная цена в семействе — для подписи кнопки «· от N⭐» (без хардкода)."""
    if code == "omni":
        return min(video_price(m, 1, "text") for m, _ in video_models_in_family("omni-flash"))
    if code == "veo":
        return min(video_price(m, 1, "text") for m, _ in video_models_in_family("veo"))
    if code == "ing":
        return min(video_price(m, 1, "ingredients") for m in VID_REF_VARIANTS)
    if code == "frm":
        return min(video_price(m, 1, "frames") for m in VID_REF_VARIANTS)
    return 0


def video_family_kb() -> types.InlineKeyboardMarkup:
    B = types.InlineKeyboardButton

    def fam(code: str) -> types.InlineKeyboardButton:
        return B(
            text=f"{L('vid_fam:' + code)} · от {_vid_family_min_price(code)}⭐",
            callback_data=f"v:fam:{code}",
        )

    return types.InlineKeyboardMarkup(inline_keyboard=[
        [fam("omni")],
        [fam("veo")],
        [fam("ing")],
        [fam("frm")],
        [B(text=L("cancel"), callback_data="v:cancel")],
    ])


# family id (в каталоге) -> короткий код в callback_data и обратно
_VID_FAMILY_CODE = {"omni-flash": "omni", "veo": "veo"}
_VID_CODE_FAMILY = {v: k for k, v in _VID_FAMILY_CODE.items()}


def video_variant_kb(family: str, selected_model: str | None) -> types.InlineKeyboardMarkup:
    """Кнопки выбора конкретной модели внутри семейства."""
    B = types.InlineKeyboardButton
    rows = []
    for model_id, meta in video_models_in_family(family):
        name = L(f"vid_model_name:{model_id}")
        label = f"{name} · {meta['price']}⭐"
        rows.append([_sel_btn(
            label, model_id == selected_model, f"v:model:{model_id}",
        )])
    rows.append([B(text=L("vid_back:fam"), callback_data="v:back:fam")])
    rows.append([B(text=L("cancel"), callback_data="v:cancel")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def video_wizard_kb(vfmt: str, vcount: int) -> types.InlineKeyboardMarkup:
    """Экран настроек: формат (16:9 / 9:16) + количество (1–4) + действия."""
    B = types.InlineKeyboardButton
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            _sel_btn(L("fmt:land"), vfmt == "land", "v:fmt:land"),
            _sel_btn(L("fmt:port"), vfmt == "port", "v:fmt:port"),
        ],
        [
            _sel_btn(f"{n}", vcount == n, f"v:cnt:{n}")
            for n in (1, 2, 3, 4)
        ],
        [_menu_button("vid_go", "v:go")],
        [_menu_button("vid_back:model", "v:back:model")],
        [_menu_button("cancel", "v:cancel")],
    ])


def _video_can_edit(ref: VideoRef | None) -> bool:
    return bool(ref and ref.media_id and ref.project_id and ref.workflow_id)


# Продление всегда выполняется моделью veo-lite, но ИСХОДНИК может быть любым
# veo-видео (lite/fast/quality) — оператор подтвердил. Omni продлевать нельзя.
VIDEO_EXTEND_MODEL = "veo-lite"


def _video_can_extend(ref: VideoRef | None) -> bool:
    return bool(
        ref
        and ref.media_id
        and ref.project_id
        and ref.workflow_id
        and str(ref.model_id).startswith("veo-")
        and not ref.prompt_edited
    )


def video_result_kb(vtoken: str) -> types.InlineKeyboardMarkup:
    B = types.InlineKeyboardButton
    ref = video_registry.get(vtoken)
    rows = [
        [B(text=L("vid_dl"), callback_data=f"v:dl:{vtoken}")],
    ]
    if ref and ref.mode == "extend" and ref.media_id:
        rows.append([B(text=L("vid_dl_seg"), callback_data=f"v:dl_seg:{vtoken}")])
    if _video_can_edit(ref):
        edit_price = action_price("video_prompt_edit")
        rows.append([B(text=f"{L('vid_edit')} · {edit_price}⭐", callback_data=f"v:edit:{vtoken}")])
    if _video_can_extend(ref):
        # Next extend in the chain — price escalates by VIDEO_EXTEND_STEP each time.
        # Extension is always veo-lite, so price off veo-lite regardless of source.
        next_price = video_extend_price(VIDEO_EXTEND_MODEL, ref.extend_index + 1)
        rows.append([B(text=f"{L('vid_extend')} · {next_price}⭐", callback_data=f"v:extend:{vtoken}")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


# Варианты модели, доступные в режимах Frames/Ingredients (тиры Veo).
VID_REF_VARIANTS = ("veo-lite", "veo-fast", "veo-quality")


def _vid_model_row(mode: str, selected: str | None) -> list:
    """Ряд выбора модели (Veo Lite/Fast/Quality) с ценой под режим."""
    B = types.InlineKeyboardButton
    row = []
    for mid in VID_REF_VARIANTS:
        price = video_price(mid, 1, mode)
        label = f"{L('vid_model_name:' + mid)} {price}⭐"
        row.append(_sel_btn(label, mid == selected, f"v:vmod:{mid}"))
    return [row]


def _vid_fmt_count_rows(vfmt: str, vcount: int) -> list:
    """Общие ряды кнопок «формат + количество» для видео-экранов."""
    B = types.InlineKeyboardButton
    return [
        [
            _sel_btn(L("fmt:land"), vfmt == "land", "v:fmt:land"),
            _sel_btn(L("fmt:port"), vfmt == "port", "v:fmt:port"),
        ],
        [
            _sel_btn(f"{n}", vcount == n, f"v:cnt:{n}")
            for n in (1, 2, 3, 4)
        ],
    ]


def ingredients_kb(
    n: int, vfmt: str, vcount: int, vmodel: str | None, has_caption: bool = False
) -> types.InlineKeyboardMarkup:
    B = types.InlineKeyboardButton
    rows = _vid_model_row("ingredients", vmodel) + _vid_fmt_count_rows(vfmt, vcount)
    if n >= 1:
        # Подпись к фото уже задаёт описание → кнопка ведёт сразу на генерацию.
        done_key = "vid_ing_done_ready" if has_caption else "vid_ing_done"
        rows.append([B(text=L(done_key), callback_data="v:ing:done")])
    rows.append([B(text=L("vid_ing_clear"), callback_data="v:ing:clear")])
    rows.append([B(text=L("vid_back:fam"),  callback_data="v:back:fam")])
    rows.append([B(text=L("cancel"),        callback_data="v:cancel")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def frames_kb(has_start: bool, has_end: bool, vfmt: str, vcount: int, vmodel: str | None) -> types.InlineKeyboardMarkup:
    B = types.InlineKeyboardButton
    rows = _vid_model_row("frames", vmodel) + _vid_fmt_count_rows(vfmt, vcount)
    if has_start and has_end:
        rows.append([B(text=L("vid_frm_go"), callback_data="v:frm:go")])
    rows.append([B(text=L("vid_frm_clear"), callback_data="v:frm:clear")])
    rows.append([B(text=L("vid_back:fam"),  callback_data="v:back:fam")])
    rows.append([B(text=L("cancel"),        callback_data="v:cancel")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


async def show_video_ingredients(message: types.Message, *, user_id: int, edit: bool = False):
    st = wizard_state[user_id]
    st["vstep"] = "ving"
    st["vawait"] = "ving_photo"
    st.setdefault("vmode", "ingredients")
    st.setdefault("vmodel", VID_REF_DEFAULT_MODEL)
    st.setdefault("vfmt", VID_DEFAULT_FMT)
    st.setdefault("vcount", VID_DEFAULT_COUNT)
    vfmt = st.get("vfmt", VID_DEFAULT_FMT)
    vcount = st.get("vcount", VID_DEFAULT_COUNT)
    model_id = st.get("vmodel", VID_REF_DEFAULT_MODEL)
    n = len(st.get("ving_photos") or [])
    text = flow_copy.msg(
        "vid_ing_screen",
        n=n,
        model=L(f"vid_model_name:{model_id}"),
        fmt=_VID_FMT_NAMES.get(vfmt, vfmt),
        count=vcount,
        price=video_price(model_id, vcount, "ingredients"),
        credits=credit_store.balance(user_id),
    )
    # Подпись к фото уже задаёт описание — показываем её и меняем подпись кнопки.
    caption = st.get("vcaption_prompt")
    if caption:
        text += "\n\n" + flow_copy.msg(
            "vid_ing_ready_with_caption", prompt=html.escape(caption[:300])
        )
    kb = ingredients_kb(n, vfmt, vcount, model_id, has_caption=bool(caption))
    if edit:
        await _vid_edit(message, text, kb, user_id, parse_mode="HTML")
    else:
        sent = await message.answer(text, reply_markup=kb, parse_mode="HTML")
        st["vmsg_id"] = sent.message_id


async def show_video_frames(message: types.Message, *, user_id: int, edit: bool = False):
    st = wizard_state[user_id]
    st["vstep"] = "vfrm"
    st.setdefault("vmode", "frames")
    st.setdefault("vmodel", VID_FRAMES_DEFAULT_MODEL)
    st.setdefault("vfmt", VID_DEFAULT_FMT)
    st.setdefault("vcount", VID_DEFAULT_COUNT)
    vfmt = st.get("vfmt", VID_DEFAULT_FMT)
    vcount = st.get("vcount", VID_DEFAULT_COUNT)
    model_id = st.get("vmodel", VID_FRAMES_DEFAULT_MODEL)
    has_start = bool(st.get("vfrm_start"))
    has_end = bool(st.get("vfrm_end"))
    text = flow_copy.msg(
        "vid_frm_screen",
        model=L(f"vid_model_name:{model_id}"),
        start_mark="✅" if has_start else "⬜",
        end_mark="✅" if has_end else "⬜",
        fmt=_VID_FMT_NAMES.get(vfmt, vfmt),
        count=vcount,
        price=video_price(model_id, vcount, "frames"),
        credits=credit_store.balance(user_id),
    )
    if not has_start:
        text += "\n\n" + flow_copy.msg("vid_frm_send_photo_start")
        st["vawait"] = "vfrm_start"
    elif not has_end:
        text += "\n\n" + flow_copy.msg("vid_frm_send_photo_end")
        st["vawait"] = "vfrm_end"
    else:
        caption = st.get("vcaption_prompt")
        if caption:
            text += "\n\n" + flow_copy.msg(
                "vid_frm_ready_next_with_caption", prompt=html.escape(caption[:300])
            )
        else:
            text += "\n\n" + flow_copy.msg("vid_frm_ready_next")
        st["vawait"] = None
    kb = frames_kb(has_start, has_end, vfmt, vcount, model_id)
    if edit:
        await _vid_edit(message, text, kb, user_id, parse_mode="HTML")
    else:
        sent = await message.answer(text, reply_markup=kb, parse_mode="HTML")
        st["vmsg_id"] = sent.message_id


def _vid_settings_text(user_id: int) -> str:
    st = wizard_state[user_id]
    model_id = st.get("vmodel", "")
    vfmt = st.get("vfmt", VID_DEFAULT_FMT)
    vcount = st.get("vcount", VID_DEFAULT_COUNT)
    model_name = L(f"vid_model_name:{model_id}") if model_id else model_id
    return flow_copy.msg(
        "vid_settings_screen",
        model=model_name,
        fmt=_VID_FMT_NAMES.get(vfmt, vfmt),
        count=vcount,
        price=video_price(model_id, vcount),
        credits=credit_store.balance(user_id),
    )


async def _vid_edit(message: types.Message, text: str, kb, user_id: int, *, parse_mode: str | None = None):
    """Отрисовать видео-экран: редактируем активное сообщение визарда.

    Повторный тап по уже выбранному параметру даёт идентичный экран — Telegram
    отвечает "message is not modified". Глотаем эту ошибку, иначе в чат улетает
    дубль панели. Настоящий сбой редактирования откатываемся на новый ``answer``.
    """
    try:
        await message.edit_text(text, reply_markup=kb, parse_mode=parse_mode)
    except Exception as exc:
        if "not modified" in str(exc).lower():
            return
        sent = await message.answer(text, reply_markup=kb, parse_mode=parse_mode)
        wizard_state[user_id]["vmsg_id"] = sent.message_id


async def _vid_rerender_settings(message: types.Message, *, user_id: int):
    """Перерисовать активный экран настроек по текущему режиму видео."""
    vmode = wizard_state[user_id].get("vmode", "text")
    if vmode == "frames":
        await show_video_frames(message, user_id=user_id, edit=True)
    elif vmode == "ingredients":
        await show_video_ingredients(message, user_id=user_id, edit=True)
    else:
        await show_video_settings(message, user_id=user_id)


async def show_video_family(message: types.Message, *, user_id: int, edit: bool):
    _vid_clear(user_id)
    st = wizard_state[user_id]
    st.pop("vretry", None)  # свежий визард — забываем прошлый ретрай-снимок
    st["vstep"] = "vfam"
    st.setdefault("vfmt", VID_DEFAULT_FMT)
    st.setdefault("vcount", VID_DEFAULT_COUNT)
    # подставим прошлые настройки как дефолт, если повторяем
    vlast = st.get("vlast")
    if vlast:
        st["vfmt"] = _aspect_to_vfmt(vlast.get("aspect", "landscape"))
        st["vcount"] = vlast.get("count", VID_DEFAULT_COUNT)
    text = flow_copy.msg("vid_family_screen")
    kb = video_family_kb()
    if edit:
        await _vid_edit(message, text, kb, user_id)
    else:
        sent = await message.answer(text, reply_markup=kb)
        st["vmsg_id"] = sent.message_id


async def show_video_variant(message: types.Message, *, user_id: int):
    st = wizard_state[user_id]
    family = st.get("vfamily", "")
    st["vstep"] = "vmodel"
    text = flow_copy.msg("vid_variant_screen", family=family)
    kb = video_variant_kb(family, st.get("vmodel"))
    await _vid_edit(message, text, kb, user_id)


async def show_video_settings(message: types.Message, *, user_id: int):
    st = wizard_state[user_id]
    st["vstep"] = "vsettings"
    st.setdefault("vfmt", VID_DEFAULT_FMT)
    st.setdefault("vcount", VID_DEFAULT_COUNT)
    text = _vid_settings_text(user_id)
    kb = video_wizard_kb(st["vfmt"], st["vcount"])
    await _vid_edit(message, text, kb, user_id, parse_mode="HTML")


def _aspect_to_vfmt(aspect: str) -> str:
    return {"landscape": "land", "portrait": "port"}.get(aspect, "land")


def topup_kb(is_admin: bool = False) -> types.InlineKeyboardMarkup:
    rows = [
        [types.InlineKeyboardButton(text=pack_label(pid), callback_data=f"m:pack:{pid}")]
        for pid in public_pack_ids(include_test=is_admin)
    ]
    rows.append([_menu_button("back", "m:balance")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


async def show_main_menu(
    message: types.Message, *, user_id: int, edit: bool = False, ensure_kb: bool = False
):
    if ensure_kb:
        # Гарантируем постоянную нижнюю клавиатуру (если её сбросили).
        try:
            await message.answer("Меню открыто 👇", reply_markup=reply_menu_kb())
        except Exception:
            pass
    last = _ws(user_id).get("last")
    kb = main_menu_kb(show_repeat=bool(last))
    text = flow_copy.msg("menu_title")
    try:
        if edit:
            await message.edit_text(text, reply_markup=kb)
        else:
            await message.answer(text, reply_markup=kb)
    except Exception:
        await message.answer(text, reply_markup=kb)


async def show_balance(message: types.Message, *, user_id: int, edit: bool = True):
    credits = credit_store.balance(user_id)
    text = flow_copy.msg("balance_screen", credits=credits, price=price_gen(1))
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [_menu_button("topup", "m:topup")],
            [_menu_button("menu", "m:menu")],
        ]
    )
    try:
        if edit:
            await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        else:
            await message.answer(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


async def _send_one_image(
    message: types.Message,
    *,
    url: str,
    img: dict,
    index: int,
    total: int,
    caption: str,
    user_id: int,
    project_id: str | None,
    prompt: str,
    aspect_ratio: str,
) -> None:
    """Отправить одну картинку с кнопкой «Редактировать», привязанной к ней."""
    # Запоминаем сырой объект картинки, чтобы бот мог изучить формат правки.
    keeper.note_image(img)
    token = image_registry.add(
        ImageRef(
            user_id=user_id,
            project_id=project_id,
            source=img,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
        )
    )
    keyboard = _image_keyboard(token)
    try:
        await message.reply_photo(photo=url, caption=caption, reply_markup=keyboard)
        return
    except Exception as e:
        log.error(f"Ошибка отправки фото {index}/{total}: {e}")

    # Если прямая ссылка не работает — скачиваем и отправляем байтами.
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    data = await r.read()
                    from aiogram.types import BufferedInputFile

                    await message.reply_photo(
                        photo=BufferedInputFile(data, f"img_{index}.png"),
                        caption=caption,
                        reply_markup=keyboard,
                    )
    except Exception as e2:
        log.error(f"Повторная ошибка отправки фото {index}/{total}: {e2}")


# ── хэндлеры ──────────────────────────────


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    credit_store.balance(user_id)  # начисляем стартовые кредиты при первом старте
    metrics.log_event("user_started", user_id=user_id,
                      username=_username(message), source="command")
    # Показываем приветствие вместе с постоянной нижней клавиатурой.
    await message.answer(flow_copy.msg("welcome"), reply_markup=reply_menu_kb())
    await show_main_menu(message, user_id=user_id)


@dp.message(Command("menu"))
async def cmd_menu(message: types.Message):
    # Постоянная нижняя клавиатура держится с /start; здесь показываем меню.
    await show_main_menu(message, user_id=message.from_user.id, ensure_kb=True)


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    session = await keeper.get_session()
    bearer = "✅" if session["bearer"] else "❌"
    project = "✅" if session["project_id"] else "❌"
    cookies = "✅" if session["cookies"] else "❌"
    age_min = int((time.time() - keeper._bearer_ts) / 60)

    captcha_parts = [f"провайдер: {CAPTCHA_PROVIDER}"]
    if CAPMONSTER_KEY:
        captcha_parts.append(f"CapMonster: {await keeper.get_capmonster_balance()}")
    if TWOCAPTCHA_KEY:
        captcha_parts.append(f"2captcha: {await keeper.get_2captcha_balance()}")
    captcha_line = ", ".join(captcha_parts)

    await message.answer(
        f"🔧 *Состояние бота*\n\n"
        f"Bearer токен: {bearer} (возраст: {age_min} мин)\n"
        f"Project ID:   {project} ({session['project_id'] or '—'})\n"
        f"Cookies:      {cookies} ({len(session['cookies'])} шт)\n"
        f"Капча:        {captcha_line}\n",
        parse_mode="Markdown",
    )


@dp.message(Command("balance"))
async def cmd_balance(message: types.Message):
    await show_balance(message, user_id=message.from_user.id, edit=False)


@dp.message(Command("grant"))
async def cmd_grant(message: types.Message):
    """Админ-команда: начислить кредиты пользователю. `/grant <user_id> <кредиты>`.

    Доступна только ID из ADMIN_IDS (.env). Кредиты — реальные деньги
    (см. docs/MONETIZATION.md), поэтому команда закрыта для всех остальных.
    """
    if message.from_user.id not in ADMIN_IDS:
        await message.answer(flow_copy.msg("admin_denied"))
        return
    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[1].lstrip("-").isdigit() or not parts[2].lstrip("-").isdigit():
        await message.answer(flow_copy.msg("admin_usage"))
        return
    target = int(parts[1])
    amount = int(parts[2])
    if amount <= 0:
        await message.answer(flow_copy.msg("admin_usage"))
        return
    new_balance = credit_store.add(target, amount)
    log.info(f"🎁 Админ {message.from_user.id} начислил {amount} кр пользователю {target}")
    await message.answer(
        flow_copy.msg("admin_granted", credits=amount, target=target, balance=new_balance)
    )
    # Уведомим самого пользователя, если это не админ себе.
    if target != message.from_user.id:
        try:
            await bot.send_message(
                target, flow_copy.msg("topup_done", credits=amount, balance=new_balance)
            )
        except Exception:
            pass


@dp.message(Command("refund"))
async def cmd_refund(message: types.Message):
    """Админ: вернуть звёзды за платёж. `/refund <user_id>` (последний платёж)
    или `/refund <user_id> <charge_id>`. Возвращает Stars через Telegram и
    списывает ранее начисленные кредиты.
    """
    if message.from_user.id not in ADMIN_IDS:
        await message.answer(flow_copy.msg("admin_denied"))
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Использование: /refund <user_id> [charge_id]")
        return
    target = int(parts[1])
    if len(parts) >= 3:
        rec = payment_store.find_by_charge(parts[2])
    else:
        rec = payment_store.last_for_user(target)
    if not rec:
        await message.answer("Платёж не найден (или уже возвращён).")
        return
    if rec.get("refunded"):
        await message.answer("Этот платёж уже возвращён.")
        return

    try:
        await bot.refund_star_payment(
            user_id=rec["user_id"],
            telegram_payment_charge_id=rec["charge_id"],
        )
    except Exception:
        log.exception("refund_star_payment failed")
        await message.answer("⚠️ Telegram отклонил возврат (срок/ID). Проверьте charge_id.")
        return

    payment_store.mark_refunded(rec["charge_id"])
    # Снимаем начисленные кредиты (не уходим в минус).
    take = min(rec["credits"], credit_store.balance(rec["user_id"]))
    if take > 0:
        credit_store.charge(rec["user_id"], take)
    log.info(f"↩️ Рефанд {rec['stars']}⭐ пользователю {rec['user_id']} (charge {rec['charge_id']})")
    await message.answer(
        f"↩️ Возвращено {rec['stars']}⭐ пользователю {rec['user_id']}. "
        f"Списано {take} кр (начислялось {rec['credits']})."
    )
    if target != message.from_user.id:
        try:
            await bot.send_message(
                target, f"↩️ Возврат {rec['stars']}⭐ выполнен. Списано {take} кредитов."
            )
        except Exception:
            pass


# ── админ-метрики (только для ADMIN_IDS; read-only) ───────────────────

def _fmt_pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _admin_only(message: types.Message) -> bool:
    if message.from_user.id not in ADMIN_IDS:
        return False
    return True


@dp.message(Command("admin_today"))
async def cmd_admin_today(message: types.Message):
    if not _admin_only(message):
        await message.answer(flow_copy.msg("admin_denied"))
        return
    r = metrics.report_today()
    top = "\n".join(f"  • {a['event_name']} — {a['count']}" for a in r["top_actions"]) or "  —"
    await message.answer(
        "📊 <b>Сегодня</b>\n"
        f"Новые: <b>{r['new_users']}</b> · активные: <b>{r['active_users']}</b> · "
        f"платящие: <b>{r['paying_users']}</b>\n"
        f"Картинки: <b>{r['image_generations']}</b> · видео: <b>{r['video_generations']}</b>\n"
        f"Success rate: <b>{_fmt_pct(r['success_rate'])}</b>\n"
        f"Выручка: <b>{r['revenue_rub']:.0f}₽</b> ({r['revenue_stars']}⭐)\n"
        f"Кредиты: списано <b>{r['credits_charged']}</b> · возвращено <b>{r['credits_refunded']}</b>\n"
        f"Топ действий:\n{top}",
        parse_mode="HTML",
    )


@dp.message(Command("admin_revenue"))
async def cmd_admin_revenue(message: types.Message):
    if not _admin_only(message):
        await message.answer(flow_copy.msg("admin_denied"))
        return
    r = metrics.report_revenue(30)
    by_pack = "\n".join(
        f"  • {p['package_id']}: {p['count']}× · {p['rub']:.0f}₽ ({p['stars']}⭐)"
        for p in r["by_package"]
    ) or "  —"
    by_day = "\n".join(
        f"  • {d['day']}: {d['rub']:.0f}₽ ({d['count']}×)" for d in r["by_day"][:7]
    ) or "  —"
    await message.answer(
        "💰 <b>Выручка (30 дней)</b>\n"
        f"Всего: <b>{r['revenue_rub']:.0f}₽</b> ({r['revenue_stars']}⭐) · "
        f"платежей: <b>{r['transactions_count']}</b> · плательщиков: <b>{r['paying_users']}</b>\n"
        f"По пакетам:\n{by_pack}\n"
        f"По дням:\n{by_day}",
        parse_mode="HTML",
    )


@dp.message(Command("admin_flow"))
async def cmd_admin_flow(message: types.Message):
    if not _admin_only(message):
        await message.answer(flow_copy.msg("admin_denied"))
        return
    r = metrics.report_flow()
    ops = "\n".join(
        f"  • {o['operation_type']}: {o['count']}× · ✅{o['success']}/❌{o['fail']}"
        f"{' · ~%dмс' % o['avg_duration_ms'] if o['avg_duration_ms'] else ''}"
        for o in r["by_operation"]
    ) or "  —"
    models = "\n".join(
        f"  • {m['model']}: {m['jobs']}× · ΔG {m['flow_credits_delta_sum']}"
        for m in r["by_model_credits"]
    ) or "  —"
    errs = "\n".join(f"  • {e['error_type']}: {e['count']}" for e in r["errors_by_type"]) or "  —"
    await message.answer(
        "🛠 <b>Flow-движок</b>\n"
        f"Success rate: <b>{_fmt_pct(r['success_rate'])}</b>\n"
        f"По операциям:\n{ops}\n"
        f"G-кредиты по моделям:\n{models}\n"
        f"Ошибки:\n{errs}",
        parse_mode="HTML",
    )


@dp.message(Command("admin_accounts"))
async def cmd_admin_accounts(message: types.Message):
    if not _admin_only(message):
        await message.answer(flow_copy.msg("admin_denied"))
        return
    accounts = metrics.report_accounts().get("accounts", [])
    if not accounts:
        await message.answer("🧮 <b>Аккаунты</b>\nСегодня заданий не было.", parse_mode="HTML")
        return
    lines = []
    for a in accounts:
        rem = f" · остаток G {a['credits_remaining']}" if a["credits_remaining"] is not None else ""
        err = f" · ⚠️ {a['last_error']}" if a["last_error"] else ""
        lines.append(
            f"  • <b>{a['account_id']}</b>: {a['jobs']}× · ✅{a['success']}/❌{a['fail']}{rem}{err}"
        )
    await message.answer("🧮 <b>Аккаунты (сегодня)</b>\n" + "\n".join(lines), parse_mode="HTML")


@dp.message(Command("admin_refs"))
async def cmd_admin_refs(message: types.Message):
    if not _admin_only(message):
        await message.answer(flow_copy.msg("admin_denied"))
        return
    r = metrics.report_refs()
    top = "\n".join(
        f"  • {t['referrer_user_id']}: {t['count']}" for t in r["top_referrers"]
    ) or "  —"
    await message.answer(
        "🤝 <b>Рефералы</b>\n"
        f"Всего: <b>{r['total_referrals']}</b> · пришли: <b>{r['joined']}</b> · "
        f"вознаграждены: <b>{r['rewarded']}</b>\n"
        f"Выдано кредитов: <b>{r['total_reward_credits']}</b>\n"
        f"Топ пригласивших:\n{top}",
        parse_mode="HTML",
    )


@dp.message(Command("admin_errors"))
async def cmd_admin_errors(message: types.Message):
    if not _admin_only(message):
        await message.answer(flow_copy.msg("admin_denied"))
        return
    r = metrics.report_errors(7)
    by_type = "\n".join(f"  • {e['error_type']}: {e['count']}" for e in r["errors_by_type"]) or "  —"
    recent = "\n".join(
        f"  • {x['created_at']} · {x['operation_type']}/{x['model']} · {x['error_type']}"
        for x in r["recent"]
    ) or "  —"
    await message.answer(
        "🚨 <b>Ошибки (7 дней)</b>\n"
        f"По типам:\n{by_type}\n"
        f"Последние:\n{recent}",
        parse_mode="HTML",
    )


# Метрики: действие → имя события запроса / тип операции для flow_jobs.
_IMG_REQUEST_EVENT = {
    "gen": "image_requested", "regen": "image_requested",
    "revary": "variations_requested",
    "up2x": "upscale_requested",
    "edit": "image_edit_requested", "myphoto": "image_edit_requested",
}
_IMG_OP = {
    "gen": "image", "regen": "image", "revary": "variations",
    "up2x": "enhance", "edit": "edit", "myphoto": "edit",
}


async def _generate_and_send(
    message: types.Message,
    prompt: str,
    num_images: int = 4,
    aspect_ratio: str = "landscape",
    actor_id: int | None = None,
    action: str = "gen",
    image_model: str = DEFAULT_IMAGE_MODEL,
):
    user_id = actor_id or message.from_user.id

    if not prompt or len(prompt) < 3:
        await message.answer(flow_copy.msg("prompt_too_short"))
        return

    # Запоминаем настройки для «🔁 Повторить».
    _ws(user_id)["last"] = {
        "prompt": prompt, "count": num_images, "aspect": aspect_ratio, "imodel": image_model,
    }

    metrics.log_event(_IMG_REQUEST_EVENT.get(action, "image_requested"),
                      user_id=user_id, username=_username(message), source=action,
                      payload={"count": num_images, "model": image_model})

    # Премиум-модель (Nano Banana Pro) добавляет наценку на каждую картинку.
    surcharge = image_model_extra(image_model) * max(1, num_images)
    started = time.monotonic()
    ok = False
    try:
        async with user_slot(user_id, message):
            async with credit_gate(user_id, action, message, num_images, surcharge=surcharge) as charge:
                ok = await _do_generate_and_send(
                    message, prompt, num_images, aspect_ratio, user_id, image_model=image_model
                )
                charge.ok = ok
    except RateLimited:
        _log_image_job(user_id, action, image_model, started, ok=False, error="rate_limited")
        return
    except NotEnoughCredits:
        metrics.log_event("image_failed", user_id=user_id, source=action,
                          payload={"reason": "insufficient_credits"})
        return
    charged = (action_price(action, num_images) + surcharge) if ok else 0
    metrics.log_event("image_success" if ok else "image_failed",
                      user_id=user_id, source=action)
    if ok:
        metrics.log_event("credits_charged", user_id=user_id, source=action,
                          payload={"amount": charged, "action": action})
        if action == "gen":
            metrics.log_event("wizard_completed", user_id=user_id, source=action)
    _log_image_job(user_id, action, image_model, started, ok=ok, charged=charged)


def _ms_since(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _log_image_job(user_id, action, image_model, started, *, ok, charged=0, error=None):
    """flow_jobs-запись для картиночной операции (никогда не бросает)."""
    metrics.log_flow_job(
        user_id=user_id, account_id=FLOW_ACCOUNT_ID,
        operation_type=_IMG_OP.get(action, "image"), model=image_model,
        bot_credits_charged=charged, duration_ms=_ms_since(started),
        status="success" if ok else "fail",
        error_type=error or (None if ok else "gen_failed"),
    )


async def _do_generate_and_send(
    message: types.Message,
    prompt: str,
    num_images: int,
    aspect_ratio: str,
    user_id: int,
    image_model: str = DEFAULT_IMAGE_MODEL,
) -> bool:
    status_msg = await message.answer(flow_copy.msg("generating"))

    async def update_status(text: str):
        try:
            await status_msg.edit_text(f"{text}\n📝 {prompt[:80]}")
        except Exception:
            pass

    # Каждый Telegram-пользователь работает в своём проекте на сайте.
    await update_status("📂 Готовлю ваш проект...")
    project_id = await ensure_user_project(user_id)

    try:
        result = await client.generate_images(
            prompt,
            aspect_ratio=aspect_ratio,
            num_images=num_images,
            progress_cb=update_status,
            project_id=project_id,
            image_model=image_model,
        )
    except Exception:
        log.exception("Generation failed")
        await status_msg.edit_text(flow_copy.msg("gen_failed"))
        return False

    if "error" in result:
        await status_msg.edit_text(f"❌ {result['error']}")
        return False

    pairs = result_pairs(result)

    if not pairs:
        log.warning(f"Пустой ответ: {str(result)[:500]}")
        await status_msg.edit_text(flow_copy.msg("nothing_returned"))
        return False

    await update_status(flow_copy.msg("sending"))
    await _send_result_pairs(
        message, pairs, user_id=user_id, project_id=project_id,
        prompt=prompt, aspect_ratio=aspect_ratio, emoji="🎨",
    )
    await status_msg.delete()
    await _after_result(message, user_id)
    return True


async def _after_result(message: types.Message, user_id: int):
    """Короткое меню-продолжение под результатом: баланс + повтор/новое/видео/меню."""
    credits = credit_store.balance(user_id)
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [_menu_button("repeat_last", "m:repeat")],
            [_menu_button("gen", "m:gen"), _menu_button("vid_gen", "m:vid")],
            [_menu_button("balance", "m:balance")],
            [_menu_button("menu", "m:menu")],
        ]
    )
    try:
        await message.answer(
            flow_copy.msg("after_image_screen", credits=credits),
            reply_markup=kb,
            parse_mode="HTML",
        )
    except Exception:
        pass


async def _send_result_pairs(
    message: types.Message,
    pairs: list,
    *,
    user_id: int,
    project_id: str | None,
    prompt: str,
    aspect_ratio: str,
    emoji: str,
):
    """Отправить набор картинок с кнопками действий (общий для всех режимов)."""
    total = len(pairs)
    for i, (url, img) in enumerate(pairs, 1):
        await _send_one_image(
            message,
            url=url,
            img=img,
            index=i,
            total=total,
            caption=f"{emoji} {i}/{total} · {prompt[:80]}",
            user_id=user_id,
            project_id=project_id,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
        )
        await asyncio.sleep(0.3)


@dp.message(Command("img"))
async def cmd_img(message: types.Message):
    prompt = " ".join(message.text.split()[1:]).strip()
    await _generate_and_send(message, prompt, num_images=4)


@dp.message(Command("one"))
async def cmd_one(message: types.Message):
    prompt = " ".join(message.text.split()[1:]).strip()
    await _generate_and_send(message, prompt, num_images=1)


@dp.message(Command("portrait"))
async def cmd_portrait(message: types.Message):
    prompt = " ".join(message.text.split()[1:]).strip()
    await _generate_and_send(message, prompt, num_images=2, aspect_ratio="portrait")


@dp.message(Command("square"))
async def cmd_square(message: types.Message):
    prompt = " ".join(message.text.split()[1:]).strip()
    await _generate_and_send(message, prompt, num_images=2, aspect_ratio="square")


@dp.message(Command("imgn"))
async def cmd_imgn(message: types.Message):
    """`/imgn N <промпт>` — N изображений (1–8)."""
    parts = message.text.split()
    n = clamp_num_images(parts[1]) if len(parts) > 1 else 4
    # Если первый аргумент был числом — это счётчик, иначе он часть промпта.
    if len(parts) > 1 and parts[1].lstrip("-").isdigit():
        prompt = " ".join(parts[2:]).strip()
    else:
        prompt = " ".join(parts[1:]).strip()
    await _generate_and_send(message, prompt, num_images=n)


@dp.message(Command("mix"))
async def cmd_mix(message: types.Message):
    """`/mix <промпт>` — собрать картинку из выбранных «ингредиентов»."""
    prompt = " ".join(message.text.split()[1:]).strip()
    await _mix_and_send(message, prompt)


def _is_rate_limit_error(result: dict) -> bool:
    error = str((result or {}).get("error", "")).lower()
    return (
        error == flow_copy.msg("rate_limited").lower()
        or "429" in error
        or "too many requests" in error
        or "слишком много" in error
    )


async def _edit_and_send(
    message: types.Message,
    ref: ImageRef,
    instruction: str,
    *,
    aspect_ratio: str | None = None,
    image_model: str = DEFAULT_IMAGE_MODEL,
) -> bool:
    """Применить правку ``instruction`` к конкретной картинке ``ref``.

    Правка уходит именно этому изображению (через ``imageInputs``) в проекте
    того же пользователя. Браузерный фолбэк отключён, чтобы вместо правки не
    прислать несвязанную картинку. ``aspect_ratio`` / ``image_model`` позволяют
    сменить формат и модель прямо при редактировании (по умолчанию — как у
    исходной картинки и базовая модель).
    """
    user_id = message.from_user.id

    if not instruction or len(instruction) < 3:
        await message.answer("❌ Опишите правку (минимум 3 символа)")
        return False

    capture = load_edit_capture(EDIT_CAPTURE_FILE)
    image_inputs = build_image_inputs(ref.source, capture)
    if not image_inputs:
        await message.answer(
            "⚠️ Не удалось определить идентификатор исходной картинки. "
            "Сгенерируйте изображение заново и нажмите «Редактировать» под ним."
        )
        return False

    aspect = aspect_ratio or ref.aspect_ratio
    surcharge = image_model_extra(image_model)
    metrics.log_event("image_edit_requested", user_id=user_id, source="edit",
                      payload={"model": image_model})
    started = time.monotonic()
    ok = False
    try:
        async with user_slot(user_id, message):
            async with credit_gate(user_id, "edit", message, 1, surcharge=surcharge) as charge:
                ok = await _do_edit_and_send(
                    message, ref, instruction, image_inputs, user_id,
                    aspect_ratio=aspect, image_model=image_model,
                )
                charge.ok = ok
    except RateLimited:
        _log_image_job(user_id, "edit", image_model, started, ok=False, error="rate_limited")
        return False
    except NotEnoughCredits:
        metrics.log_event("image_failed", user_id=user_id, source="edit",
                          payload={"reason": "insufficient_credits"})
        return False
    charged = (action_price("edit", 1) + surcharge) if ok else 0
    metrics.log_event("image_success" if ok else "image_failed", user_id=user_id, source="edit")
    if ok:
        metrics.log_event("credits_charged", user_id=user_id, source="edit",
                          payload={"amount": charged, "action": "edit"})
    _log_image_job(user_id, "edit", image_model, started, ok=ok, charged=charged)
    return ok


async def _do_edit_and_send(
    message: types.Message,
    ref: ImageRef,
    instruction: str,
    image_inputs: list,
    user_id: int,
    *,
    aspect_ratio: str | None = None,
    image_model: str = DEFAULT_IMAGE_MODEL,
) -> bool:
    status_msg = await message.answer(
        f"✏️ Редактирую изображение...\n📝 {instruction[:80]}"
    )
    aspect = aspect_ratio or ref.aspect_ratio

    async def update_status(text: str):
        try:
            await status_msg.edit_text(f"{text}\n📝 {instruction[:80]}")
        except Exception:
            pass

    try:
        result = await client.generate_images(
            instruction,
            aspect_ratio=aspect,
            num_images=1,
            progress_cb=update_status,
            project_id=ref.project_id,
            image_inputs=image_inputs,
            allow_browser_fallback=False,
            image_model=image_model,
        )
    except Exception:
        log.exception("Edit failed")
        await status_msg.edit_text("❌ Ошибка редактирования. Попробуйте ещё раз.")
        return False

    if "error" in result:
        if _is_rate_limit_error(result):
            await status_msg.edit_text(flow_copy.msg("image_edit_rate_limited"))
        else:
            await status_msg.edit_text(f"❌ {result['error']}")
        return False

    pairs = result_pairs(result)
    if not pairs:
        log.warning(f"Пустой ответ редактирования: {str(result)[:500]}")
        await status_msg.edit_text(flow_copy.msg("nothing_returned"))
        return False

    await update_status(flow_copy.msg("sending"))
    await _send_result_pairs(
        message, pairs, user_id=user_id, project_id=ref.project_id,
        prompt=instruction, aspect_ratio=aspect, emoji="✏️",
    )
    await status_msg.delete()
    return True


async def _run_i2i(
    message: types.Message,
    ref: ImageRef,
    prompt: str,
    *,
    num_images: int,
    emoji: str,
    fail_text: str,
    action: str = "edit",
) -> bool:
    """Общий image-to-image: правка/вариации/улучшение картинки ``ref``.

    Браузерный фолбэк отключён, чтобы вместо результата по этой картинке не
    прислать несвязанную генерацию.
    """
    user_id = ref.user_id
    capture = load_edit_capture(EDIT_CAPTURE_FILE)
    image_inputs = build_image_inputs(ref.source, capture)
    if not image_inputs:
        await message.answer(
            "⚠️ Не удалось определить идентификатор исходной картинки. "
            "Сгенерируйте изображение заново и попробуйте снова."
        )
        return False

    metrics.log_event(_IMG_REQUEST_EVENT.get(action, "image_requested"),
                      user_id=user_id, source=action, payload={"count": num_images})
    started = time.monotonic()
    ok = False
    try:
        async with user_slot(user_id, message):
            async with credit_gate(user_id, action, message, num_images) as charge:
                ok = await _do_run_i2i(
                    message, ref, prompt, image_inputs,
                    num_images=num_images, emoji=emoji, fail_text=fail_text,
                )
                charge.ok = ok
    except RateLimited:
        _log_image_job(user_id, action, None, started, ok=False, error="rate_limited")
        return False
    except NotEnoughCredits:
        metrics.log_event("image_failed", user_id=user_id, source=action,
                          payload={"reason": "insufficient_credits"})
        return False
    charged = action_price(action, num_images) if ok else 0
    metrics.log_event("image_success" if ok else "image_failed",
                      user_id=user_id, source=action)
    if ok:
        metrics.log_event("credits_charged", user_id=user_id, source=action,
                          payload={"amount": charged, "action": action})
    _log_image_job(user_id, action, None, started, ok=ok, charged=charged)
    return ok


async def _do_run_i2i(
    message: types.Message,
    ref: ImageRef,
    prompt: str,
    image_inputs: list,
    *,
    num_images: int,
    emoji: str,
    fail_text: str,
) -> bool:
    user_id = ref.user_id
    status_msg = await message.answer(f"{emoji} Обрабатываю...")

    async def update_status(text: str):
        try:
            await status_msg.edit_text(text)
        except Exception:
            pass

    try:
        result = await client.generate_images(
            prompt,
            aspect_ratio=ref.aspect_ratio,
            num_images=num_images,
            progress_cb=update_status,
            project_id=ref.project_id,
            image_inputs=image_inputs,
            allow_browser_fallback=False,
        )
    except Exception:
        log.exception("i2i failed")
        await status_msg.edit_text("❌ Ошибка. Попробуйте ещё раз.")
        return False

    if "error" in result:
        if _is_rate_limit_error(result):
            await status_msg.edit_text(flow_copy.msg("image_edit_rate_limited"))
        else:
            await status_msg.edit_text(f"❌ {result['error']}")
        return False

    pairs = result_pairs(result)
    if not pairs:
        await status_msg.edit_text(fail_text)
        return False

    await _send_result_pairs(
        message, pairs, user_id=user_id, project_id=ref.project_id,
        prompt=prompt, aspect_ratio=ref.aspect_ratio, emoji=emoji,
    )
    await status_msg.delete()
    return True


async def _vary_and_send(message: types.Message, ref: ImageRef):
    """🎲 Вариации: image-to-image с тем же промптом, что у исходной картинки."""
    base_prompt = ref.prompt or "variation of this image"
    await _run_i2i(
        message, ref, base_prompt,
        num_images=2, emoji="🎲", action="revary",
        fail_text="⚠️ Не удалось сделать вариации. Попробуйте ещё раз.",
    )


async def _enhance_and_send(message: types.Message, ref: ImageRef):
    """✨ Улучшить ×2 (платно): image-to-image с промптом-энхансером качества."""
    enhance_prompt = (
        (ref.prompt or "this image")
        + ", upscaled, ultra high resolution, sharp details, 4k, enhanced quality"
    )
    await _run_i2i(
        message, ref, enhance_prompt,
        num_images=1, emoji="✨", action="up2x",
        fail_text="⚠️ Не удалось улучшить картинку. Попробуйте ещё раз.",
    )


async def _real_upscale_and_send(message: types.Message, ref: ImageRef):
    """🔍 Настоящий апскейл сервиса (flow/upsampleImage → картинка в 2K).

    В отличие от «Чёткости ×2» (доработка промптом) — это родной серверный
    апскейл по сверенному контракту. Возвращает готовую увеличенную картинку.
    """
    user_id = ref.user_id
    media_id = ref.source.get("mediaId") if isinstance(ref.source, dict) else None
    if not media_id:
        media_id = id_from_media_url(ref.source.get("fifeUrl") if isinstance(ref.source, dict) else None)
    if not media_id:
        await message.answer(flow_copy.msg("upscale_unavailable"))
        return

    metrics.log_event("upscale_requested", user_id=user_id, source="realup")
    started = time.monotonic()
    ok = False
    try:
        async with user_slot(user_id, message):
            async with credit_gate(user_id, "realup", message, 1) as charge:
                ok = await _do_real_upscale(message, ref, media_id)
                charge.ok = ok
    except RateLimited:
        _log_image_job(user_id, "realup", None, started, ok=False, error="rate_limited")
        return
    except NotEnoughCredits:
        metrics.log_event("image_failed", user_id=user_id, source="realup",
                          payload={"reason": "insufficient_credits"})
        return
    charged = action_price("realup", 1) if ok else 0
    metrics.log_event("image_success" if ok else "image_failed", user_id=user_id, source="realup")
    if ok:
        metrics.log_event("credits_charged", user_id=user_id, source="realup",
                          payload={"amount": charged, "action": "realup"})
    metrics.log_flow_job(
        user_id=user_id, account_id=FLOW_ACCOUNT_ID, operation_type="upscale",
        model=None, bot_credits_charged=charged, duration_ms=_ms_since(started),
        status="success" if ok else "fail", error_type=None if ok else "upscale_failed",
    )


async def _do_real_upscale(message: types.Message, ref: ImageRef, media_id: str) -> bool:
    status_msg = await message.answer(flow_copy.msg("upscaling"))

    async def update_status(text: str):
        try:
            await status_msg.edit_text(text)
        except Exception:
            pass

    try:
        result = await client.upsample_image(media_id, ref.project_id, progress_cb=update_status)
    except Exception:
        log.exception("real upscale failed")
        await status_msg.edit_text(flow_copy.msg("gen_failed"))
        return False

    if "error" in result:
        await status_msg.edit_text(f"❌ {result['error']}")
        return False

    image_bytes = result.get("image_bytes")
    if not image_bytes:
        await status_msg.edit_text(flow_copy.msg("nothing_returned"))
        return False

    from aiogram.types import BufferedInputFile

    # Отдаём документом (без сжатия Telegram), чтобы сохранить высокое разрешение.
    ext = "png" if image_bytes[:8].startswith(b"\x89PNG") else "jpg"
    try:
        await message.answer_document(
            BufferedInputFile(image_bytes, f"upscaled_{media_id[-8:]}.{ext}"),
            caption="🔍 Картинка в высоком разрешении (2K).",
        )
        await status_msg.delete()
    except Exception:
        log.exception("upscaled image send failed")
        await status_msg.edit_text("❌ Не удалось отправить файл.")
        return False
    return True


async def _regen_and_send(message: types.Message, ref: ImageRef):
    """🔄 Ещё: новая text-to-image генерация по тому же промпту (новый seed)."""
    if not ref.prompt:
        await message.answer("⚠️ Нет исходного промпта для повтора.")
        return
    await _generate_and_send(
        message, ref.prompt, num_images=4, aspect_ratio=ref.aspect_ratio,
        actor_id=ref.user_id, action="regen",
    )


async def _send_original_file(message: types.Message, ref: ImageRef):
    """⬇️ Оригинал: отдать картинку файлом в полном качестве.

    На сайте кнопка «upscale» — это клиентское скачивание файла, а не серверный
    запрос. Эквивалент в боте: скачать исходные байты и отправить ДОКУМЕНТОМ
    (Telegram не пережимает документы, в отличие от фото), сохранив полное
    разрешение сгенерированной картинки.
    """
    user_id = ref.user_id
    url = download_url(ref.source)
    if not url:
        await message.answer("⚠️ Нет ссылки на файл этой картинки.")
        return

    try:
        async with user_slot(user_id, message):
            await _do_send_original_file(message, ref, url)
    except RateLimited:
        return


async def _do_send_original_file(message: types.Message, ref: ImageRef, url: str):
    status_msg = await message.answer("⬇️ Готовлю файл в полном качестве...")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=60)) as r:
                if r.status != 200:
                    await status_msg.edit_text(f"⚠️ Не удалось скачать файл (HTTP {r.status}).")
                    return
                data = await r.read()
    except Exception:
        log.exception("download original failed")
        await status_msg.edit_text("❌ Ошибка скачивания. Попробуйте ещё раз.")
        return

    from aiogram.types import BufferedInputFile

    media_id = ref.source.get("mediaId") if isinstance(ref.source, dict) else None
    filename = f"flow_{media_id or 'image'}.png"
    try:
        await message.answer_document(
            BufferedInputFile(data, filename),
            caption="⬇️ Оригинал в полном качестве (Telegram не сжимает документы).",
        )
        await status_msg.delete()
    except Exception:
        log.exception("send document failed")
        await status_msg.edit_text("❌ Не удалось отправить файл.")


async def _mix_and_send(message: types.Message, prompt: str):
    """Собрать одну картинку из выбранных «ингредиентов» + текстовый промпт."""
    user_id = message.from_user.id
    basket = mix_baskets.get(user_id) or []
    if len(basket) < 2:
        await message.answer(
            "➕ Сначала добавьте 2–4 картинки в микс кнопкой «➕ В микс» под ними, "
            "потом пришлите `/mix ваш промпт`.",
            parse_mode="Markdown",
        )
        return
    if not prompt or len(prompt) < 3:
        await message.answer("❌ Укажите промпт к миксу (минимум 3 символа)")
        return

    capture = load_edit_capture(EDIT_CAPTURE_FILE)
    image_inputs = build_ingredients_inputs(basket, capture)
    if len(image_inputs) < 2:
        await message.answer(
            "⚠️ Не удалось собрать ингредиенты. Сгенерируйте картинки заново."
        )
        return

    try:
        async with user_slot(user_id, message):
            await _do_mix_and_send(message, prompt, image_inputs, user_id)
    except RateLimited:
        return


async def _do_mix_and_send(
    message: types.Message, prompt: str, image_inputs: list, user_id: int
):
    status_msg = await message.answer(f"🧩 Собираю микс из {len(image_inputs)} картинок...")

    async def update_status(text: str):
        try:
            await status_msg.edit_text(text)
        except Exception:
            pass

    project_id = await ensure_user_project(user_id)
    try:
        result = await client.generate_images(
            prompt,
            aspect_ratio="landscape",
            num_images=2,
            progress_cb=update_status,
            project_id=project_id,
            image_inputs=image_inputs,
            allow_browser_fallback=False,
        )
    except Exception:
        log.exception("mix failed")
        await status_msg.edit_text("❌ Ошибка микса. Попробуйте ещё раз.")
        return

    if "error" in result:
        await status_msg.edit_text(f"❌ {result['error']}")
        return

    pairs = result_pairs(result)
    if not pairs:
        await status_msg.edit_text("⚠️ Микс не дал результата.")
        return

    mix_baskets[user_id] = []  # корзина израсходована
    await _send_result_pairs(
        message, pairs, user_id=user_id, project_id=project_id,
        prompt=prompt, aspect_ratio="landscape", emoji="🧩",
    )
    await status_msg.delete()


@dp.callback_query(F.data.startswith("m:"))
async def on_menu_action(callback: types.CallbackQuery):
    """Кнопки главного меню и экранов (генерация/баланс/пополнение/помощь)."""
    user_id = callback.from_user.id
    data = callback.data or ""
    msg = callback.message

    if data == "m:gen":
        await callback.answer()
        _reset_image_flow(user_id)  # сбрасывает и pending_edits (залипшее фото)
        await show_wizard(msg, user_id=user_id, edit=True)
    elif data == "m:vid":
        await callback.answer()
        pending_edits.pop(user_id, None)  # бросаем залипшее фото-правку при переходе в видео
        await show_video_family(msg, user_id=user_id, edit=True)
    elif data == "m:repeat":
        await callback.answer("Повторяю 🔁")
        await _repeat_last(callback, user_id)
    elif data == "m:balance":
        await callback.answer()
        await show_balance(msg, user_id=user_id, edit=True)
    elif data == "m:topup":
        await callback.answer()
        metrics.log_event("topup_opened", user_id=user_id, source="menu")
        await msg.edit_text(
            flow_copy.msg("topup_screen"),
            reply_markup=topup_kb(is_admin=user_id in ADMIN_IDS),
        )
    elif data.startswith("m:pack:"):
        await _start_topup(callback, user_id, data.split(":", 2)[2])
    elif data == "m:help":
        await callback.answer()
        kb = types.InlineKeyboardMarkup(
            inline_keyboard=[[_menu_button("menu", "m:menu")]]
        )
        await msg.edit_text(flow_copy.msg("help"), reply_markup=kb)
    elif data == "m:myphoto":
        await callback.answer()
        _reset_image_flow(user_id, keep_last=False)
        _ws(user_id)["await"] = "photo"
        await msg.edit_text(flow_copy.msg("ask_photo"))
    elif data == "m:menu":
        await callback.answer()
        pending_edits.pop(user_id, None)
        _ws(user_id)["await"] = None
        await show_main_menu(msg, user_id=user_id, edit=True)
    else:
        await callback.answer()


@dp.callback_query(F.data.startswith("es:"))
async def on_edit_settings(callback: types.CallbackQuery):
    """Пикер формата/модели на экране редактирования фото."""
    user_id = callback.from_user.id
    data = callback.data or ""
    st = _ws(user_id)

    if data == "es:cancel":
        st["await"] = None
        pending_edits.pop(user_id, None)
        await callback.answer("Отменено")
        try:
            await callback.message.delete()
        except Exception:
            pass
        return

    changed = False
    if data.startswith("es:fmt:"):
        st["edit_fmt"] = data.split(":")[2]
        changed = True
    elif data.startswith("es:imodel:"):
        choice = data.split(":")[2]
        if image_model_meta(choice):
            st["edit_imodel"] = choice
            changed = True
    await callback.answer()
    if changed:
        try:
            await callback.message.edit_reply_markup(
                reply_markup=edit_settings_kb(
                    st.get("edit_fmt", DEFAULT_FMT),
                    st.get("edit_imodel", DEFAULT_IMAGE_MODEL),
                )
            )
        except Exception:
            pass


@dp.callback_query(F.data.startswith("w:"))
async def on_wizard_action(callback: types.CallbackQuery):
    """Один экран визарда: меняем количество/формат и жмём «Сгенерировать»."""
    user_id = callback.from_user.id
    data = callback.data or ""
    st = _ws(user_id)
    msg = callback.message

    if data == "w:cancel":
        await callback.answer("Отменено")
        st.clear()
        await show_main_menu(msg, user_id=user_id, edit=True)
        return
    if data.startswith("w:cnt:"):
        st["count"] = clamp_num_images(data.split(":")[2])
        await callback.answer()
        await show_wizard(msg, user_id=user_id, edit=True)
        return
    if data.startswith("w:fmt:"):
        st["fmt"] = data.split(":")[2]
        await callback.answer()
        await show_wizard(msg, user_id=user_id, edit=True)
        return
    if data.startswith("w:imodel:"):
        choice = data.split(":")[2]
        if image_model_meta(choice):
            st["imodel"] = choice
        await callback.answer()
        await show_wizard(msg, user_id=user_id, edit=True)
        return
    if data == "w:go":
        pending = st.get("pending_prompt")
        if pending:
            # Промпт уже прислан в чат — генерируем сразу с выбранными
            # количеством, форматом и моделью; второй раз текст не спрашиваем.
            st["pending_prompt"] = None
            count = st.get("count", DEFAULT_COUNT)
            fmt = st.get("fmt", DEFAULT_FMT)
            await callback.answer()
            await _generate_and_send(
                callback.message, pending, num_images=count,
                aspect_ratio=_fmt_to_aspect(fmt), actor_id=user_id,
                image_model=st.get("imodel", DEFAULT_IMAGE_MODEL),
            )
            return
        st["await"] = "prompt"
        st["step"] = "prompt"
        await callback.answer()
        await msg.edit_text(flow_copy.msg("ask_prompt"))
        return
    await callback.answer()


@dp.callback_query(F.data.startswith("v:"))
async def on_video_action(callback: types.CallbackQuery):
    """Видео-визард: выбор семейства/модели/формата/кол-ва и запуск генерации."""
    user_id = callback.from_user.id
    data = callback.data or ""
    st = _ws(user_id)
    msg = callback.message

    # Идёт генерация — блокируем любые нажатия визарда.
    if st.get("vstep") == "vgenerating" and data != "v:dl" and not data.startswith("v:dl:") and not data.startswith("v:dl_seg:"):
        await callback.answer(flow_copy.msg("vid_busy"), show_alert=True)
        return

    # Отмена → главное меню.
    if data == "v:cancel":
        await callback.answer("Отменено")
        _vid_clear(user_id)
        await show_main_menu(msg, user_id=user_id, edit=True)
        return

    # Скачать готовое видео по токену.
    if data.startswith("v:dl:"):
        await _video_download(callback, user_id, data.split(":", 2)[2])
        return
    if data.startswith("v:dl_seg:"):
        await _video_segment_download(callback, user_id, data.split(":", 2)[2])
        return
    if data.startswith("v:edit:"):
        await _video_edit_start(callback, user_id, data.split(":", 2)[2])
        return
    if data.startswith("v:extend:"):
        await _video_extend_start(callback, user_id, data.split(":", 2)[2])
        return

    # Повторить последнюю генерацию.
    if data == "v:repeat":
        await callback.answer("Повторяю 🔁")
        await _video_repeat_last(callback, user_id)
        return

    # Выбор семейства.
    if data.startswith("v:fam:"):
        code = data.split(":")[2]
        if code == "ing":
            await callback.answer()
            _vid_clear(user_id)
            st["vmode"] = "ingredients"
            st["vmodel"] = VID_REF_DEFAULT_MODEL
            st["vcount"] = 1
            await show_video_ingredients(msg, user_id=user_id, edit=True)
            return
        if code == "frm":
            await callback.answer()
            _vid_clear(user_id)
            st["vmode"] = "frames"
            st["vmodel"] = VID_FRAMES_DEFAULT_MODEL
            st["vcount"] = 1
            await show_video_frames(msg, user_id=user_id, edit=True)
            return
        family = _VID_CODE_FAMILY.get(code)
        if not family:
            await callback.answer()
            return
        st["vfamily"] = family
        st["vmodel"] = None
        await callback.answer()
        await show_video_variant(msg, user_id=user_id)
        return

    # Ingredients actions.
    if data == "v:ing:done":
        photos = st.get("ving_photos") or []
        if len(photos) < 1:
            await callback.answer(flow_copy.msg("vid_ing_need_more"), show_alert=True)
            return
        model_id = st.get("vmodel") or VID_REF_DEFAULT_MODEL
        price = video_price(model_id, st.get("vcount", 1), "ingredients")
        if credit_store.balance(user_id) < price:
            kb = types.InlineKeyboardMarkup(inline_keyboard=[
                [_menu_button("topup", "m:topup")], [_menu_button("menu", "m:menu")]
            ])
            await callback.answer()
            await msg.answer(
                flow_copy.msg("low_balance", needed=price, have=credit_store.balance(user_id)),
                reply_markup=kb,
                parse_mode="HTML",
            )
            return
        # Подпись к фото уже задаёт описание — генерируем сразу.
        caption = st.pop("vcaption_prompt", None)
        if caption:
            st["vawait"] = None
            await callback.answer()
            await _video_generate_and_send(msg, caption, user_id=user_id)
            return
        st["vawait"] = "vprompt"
        st["vstep"] = "vprompt"
        await callback.answer()
        await msg.answer(flow_copy.msg("vid_ing_ask_prompt"))
        return

    if data == "v:ing:clear":
        await callback.answer()
        st["ving_photos"] = []
        await show_video_ingredients(msg, user_id=user_id, edit=True)
        return

    # Frames actions.
    if data == "v:frm:go":
        if not (st.get("vfrm_start") and st.get("vfrm_end")):
            await callback.answer(flow_copy.msg("vid_frm_need_both"), show_alert=True)
            return
        model_id = st.get("vmodel") or VID_FRAMES_DEFAULT_MODEL
        price = video_price(model_id, st.get("vcount", 1), "frames")
        if credit_store.balance(user_id) < price:
            kb = types.InlineKeyboardMarkup(inline_keyboard=[
                [_menu_button("topup", "m:topup")], [_menu_button("menu", "m:menu")]
            ])
            await callback.answer()
            await msg.answer(
                flow_copy.msg("low_balance", needed=price, have=credit_store.balance(user_id)),
                reply_markup=kb,
                parse_mode="HTML",
            )
            return
        caption = st.pop("vcaption_prompt", None)
        if caption:
            st["vawait"] = None
            await callback.answer()
            await _video_generate_and_send(msg, caption, user_id=user_id)
            return
        st["vawait"] = "vprompt"
        st["vstep"] = "vprompt"
        await callback.answer()
        await msg.answer(flow_copy.msg("vid_frm_ask_prompt"))
        return

    if data == "v:frm:clear":
        await callback.answer()
        st.pop("vfrm_start", None)
        st.pop("vfrm_end", None)
        st.pop("vawait", None)
        await show_video_frames(msg, user_id=user_id, edit=True)
        return

    # Назад к выбору семейства.
    if data == "v:back:fam":
        st["vmodel"] = None
        await callback.answer()
        await show_video_family(msg, user_id=user_id, edit=True)
        return

    # Выбор конкретной модели → экран настроек.
    if data.startswith("v:model:"):
        model_id = data.split(":", 2)[2]
        if not video_model_meta(model_id):
            await callback.answer()
            return
        st["vmodel"] = model_id
        await callback.answer()
        await show_video_settings(msg, user_id=user_id)
        return

    # Назад к выбору модели (в том же семействе).
    if data == "v:back:model":
        await callback.answer()
        await show_video_variant(msg, user_id=user_id)
        return

    # Смена формата / количества на экране настроек (текст / кадры / ингредиенты).
    if data.startswith("v:fmt:"):
        st["vfmt"] = data.split(":")[2]
        await callback.answer()
        await _vid_rerender_settings(msg, user_id=user_id)
        return
    if data.startswith("v:cnt:"):
        st["vcount"] = clamp_num_videos(data.split(":")[2])
        await callback.answer()
        await _vid_rerender_settings(msg, user_id=user_id)
        return
    # Выбор модели (Veo Lite/Fast/Quality) в режимах Frames/Ingredients.
    if data.startswith("v:vmod:"):
        mid = data.split(":", 2)[2]
        if video_model_meta(mid):
            st["vmodel"] = mid
        await callback.answer()
        await _vid_rerender_settings(msg, user_id=user_id)
        return

    # Повтор после ошибки — снова просим промпт с теми же настройками.
    if data == "v:retry":
        snap = st.get("vretry")
        if not snap or not snap.get("vmodel"):
            await callback.answer(flow_copy.msg("vid_expired_wizard"), show_alert=True)
            await show_main_menu(msg, user_id=user_id, edit=True)
            return
        # Восстанавливаем настройки и фото из снимка и повторяем тот же запрос.
        for k, v in snap.items():
            if k != "prompt" and v is not None:
                st[k] = v
        await callback.answer()
        await _video_generate_and_send(msg, snap["prompt"], user_id=user_id)
        return

    # Подтверждение → промпт или сразу генерация (если промпт уже есть).
    if data == "v:go":
        model_id = st.get("vmodel")
        if not model_id:
            await callback.answer(flow_copy.msg("vid_expired_wizard"), show_alert=True)
            await show_main_menu(msg, user_id=user_id, edit=True)
            return
        # Проверка баланса до входа в ввод промпта.
        price = video_price(model_id, st.get("vcount", VID_DEFAULT_COUNT))
        if credit_store.balance(user_id) < price:
            kb = types.InlineKeyboardMarkup(inline_keyboard=[
                [_menu_button("topup", "m:topup")], [_menu_button("menu", "m:menu")]
            ])
            await callback.answer()
            await _vid_edit(
                msg,
                flow_copy.msg("low_balance", needed=price, have=credit_store.balance(user_id)),
                kb, user_id,
                parse_mode="HTML",
            )
            return
        pending = st.get("vpending_prompt")
        if pending:
            st["vpending_prompt"] = None
            await callback.answer()
            await _video_generate_and_send(msg, pending, user_id=user_id)
            return
        st["vawait"] = "vprompt"
        st["vstep"] = "vprompt"
        await callback.answer()
        await msg.edit_text(flow_copy.msg("vid_ask_prompt"))
        return

    await callback.answer()


def _aspect_to_fmt(aspect: str) -> str:
    return {
        "landscape": "land",
        "portrait": "port",
        "square": "sq",
        "landscape_43": "f43",
        "portrait_34": "f34",
    }.get(aspect, "land")


async def _video_delivery_bytes(
    ref: VideoRef, *, fetched_bytes: bytes | None = None
) -> tuple[bytes | None, bool]:
    """Fetch bytes for delivery.

    For an Extend result the default is the FULL stitched video (the service's
    own server-side concatenation). ``ref.media_id`` is only the newly added
    segment, so we fall back to it (is_full=False) if stitching is unavailable.
    Returns ``(bytes, is_full)``.
    """
    if ref.mode == "extend" and ref.scene_id and ref.project_id:
        full_bytes = await client.fetch_full_extended_video(ref.scene_id, ref.project_id)
        if full_bytes:
            return full_bytes, True
        log.warning("full stitched video unavailable; falling back to extension segment")

    video_bytes = fetched_bytes
    if video_bytes is None:
        video_bytes = await client.fetch_video_bytes(ref.media_id)
    return video_bytes, False


async def _repeat_last(callback: types.CallbackQuery, user_id: int):
    last = _ws(user_id).get("last")
    if not last:
        await callback.message.answer("Нет предыдущей генерации.")
        return
    await _generate_and_send(
        callback.message,
        last["prompt"],
        num_images=last["count"],
        aspect_ratio=last["aspect"],
        actor_id=user_id,
        image_model=last.get("imodel", DEFAULT_IMAGE_MODEL),
    )


async def _video_generate_and_send(
    message: types.Message,
    prompt: str,
    *,
    user_id: int,
    unit_price_override: int | None = None,
    prompt_edited: bool = False,
    status_text: str | None = None,
    source_video: VideoRef | None = None,
    video_operation: str = "generate",
    source_scene_id: str | None = None,
) -> None:
    """Запустить видеогенерацию: списать кредиты, дождаться, отправить, показать кнопки."""
    st = _ws(user_id)

    if not prompt or len(prompt.strip()) < 3:
        await message.answer(flow_copy.msg("vid_prompt_too_short"))
        return

    model_id = st.get("vmodel", "omni-flash-4s")
    vmode = st.get("vmode", "text")
    vfmt = st.get("vfmt", VID_DEFAULT_FMT)
    vcount = st.get("vcount", VID_DEFAULT_COUNT)
    aspect = _VID_FMT_TO_ASPECT.get(vfmt, "landscape")

    meta = video_model_meta(model_id)
    if not meta:
        await message.answer(flow_copy.msg("vid_expired_wizard"))
        return
    if vmode == "ingredients" and len(build_video_reference_images(st.get("ving_photos"))) < 1:
        await message.answer(flow_copy.msg("vid_ing_need_more"))
        return
    if vmode == "frames":
        start_image, end_image = build_video_frame_images(st.get("vfrm_start"), st.get("vfrm_end"))
        if not (start_image and end_image):
            await message.answer(flow_copy.msg("vid_frm_need_both"))
            return

    model_key = meta["key"]
    single_price = unit_price_override if unit_price_override is not None else video_price(model_id, 1, vmode)
    total_price = single_price * vcount

    _vid_started = time.monotonic()
    metrics.log_event("video_requested", user_id=user_id, username=_username(message),
                      source=video_operation if video_operation != "generate" else vmode,
                      payload={"model": model_id, "count": vcount, "mode": vmode})

    have = credit_store.balance(user_id)
    if have < total_price:
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [_menu_button("topup", "m:topup")], [_menu_button("menu", "m:menu")]
        ])
        await message.answer(
            flow_copy.msg("low_balance", needed=total_price, have=have), reply_markup=kb,
            parse_mode="HTML",
        )
        return

    credit_store.charge(user_id, total_price)
    refunded_units = 0

    st["vstep"] = "vgenerating"
    status_msg = await message.answer(status_text or flow_copy.msg("vid_working"))

    async def update_status(text: str):
        try:
            await status_msg.edit_text(f"{text}\n📝 {prompt[:80]}")
        except Exception:
            pass

    def _stash_retry():
        # Снимок состояния, чтобы «Попробовать снова» повторил ТОТ ЖЕ запрос.
        # Без этого finally → _vid_clear сотрёт vmodel, и ретрай решит «кнопки устарели».
        st["vretry"] = {
            "vmodel": model_id, "vmode": vmode, "vfmt": vfmt, "vcount": vcount,
            "vfamily": st.get("vfamily"),
            "ving_photos": st.get("ving_photos"),
            "vfrm_start": st.get("vfrm_start"), "vfrm_end": st.get("vfrm_end"),
            "vcaption_prompt": st.get("vcaption_prompt"),
            "prompt": prompt,
        }

    async def _fail_retry(i: int):
        nonlocal refunded_units
        refund_amt = single_price * (vcount - i)
        credit_store.refund(user_id, refund_amt)
        refunded_units += vcount - i
        _stash_retry()
        metrics.log_event("video_failed", user_id=user_id, source=vmode,
                          payload={"model": model_id})
        metrics.log_event("credits_refunded", user_id=user_id, source=vmode,
                          payload={"amount": refund_amt})
        metrics.log_flow_job(
            user_id=user_id, account_id=FLOW_ACCOUNT_ID,
            operation_type=f"video_{vmode}", model=model_id,
            bot_credits_charged=0, refund_amount=refund_amt,
            duration_ms=_ms_since(_vid_started), status="fail", error_type="video_gen_failed",
        )
        fail_kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [_menu_button("vid_retry", "v:retry")],
            [_menu_button("menu", "m:menu")],
        ])
        try:
            await status_msg.edit_text(flow_copy.msg("vid_gen_failed"), reply_markup=fail_kb)
        except Exception:
            await message.answer(flow_copy.msg("vid_gen_failed"), reply_markup=fail_kb)

    sent_count = 0
    try:
        from aiogram.types import BufferedInputFile

        for i in range(vcount):
            result = await client.generate_video(
                prompt,
                model_key=model_key,
                aspect=aspect,
                project_id=source_video.project_id if source_video else None,
                reference_sources=st.get("ving_photos") if vmode == "ingredients" else None,
                start_source=st.get("vfrm_start") if vmode == "frames" else None,
                end_source=st.get("vfrm_end") if vmode == "frames" else None,
                operation=video_operation,
                source_media_id=source_video.media_id if source_video else None,
                source_workflow_id=source_video.workflow_id if source_video else None,
                source_scene_id=source_scene_id or (source_video.scene_id if source_video else None),
                progress_cb=update_status,
            )

            if "error" in result:
                # TEMP (capture-driven): surface why r2v/ingredients gen fails.
                log.warning(
                    "🎬 gen failed: mode=%s model=%s aspect=%s err=%s",
                    vmode, model_id, aspect, str(result.get("error"))[:300],
                )
                await _fail_retry(i)
                return

            media_id = result["media_id"]
            await update_status("⬇️ Готовлю видео для отправки…")
            video_bytes = await client.fetch_video_bytes(media_id)

            if not video_bytes:
                await _fail_retry(i)
                return

            vref = VideoRef(
                user_id=user_id,
                project_id=result.get("project_id"),
                media_id=media_id,
                source_media_id=source_video.media_id if video_operation == "extend" and source_video else None,
                prompt=prompt,
                model_id=model_id,
                aspect_ratio=aspect,
                mode=video_operation if video_operation != "generate" else vmode,
                prompt_edited=prompt_edited,
                workflow_id=result.get("workflow_id"),
                # Extend needs the scene id to stitch the full timeline on download.
                scene_id=result.get("scene_id") or (source_scene_id if video_operation == "extend" else None),
                # Each extend deepens the chain; drives the progressive extend price.
                extend_index=(source_video.extend_index + 1)
                if (video_operation == "extend" and source_video) else 0,
            )
            vtoken = video_registry.add(vref)

            caption = flow_copy.msg("vid_result_caption", i=i + 1, n=vcount, prompt=prompt[:60])
            if meta.get("family") == "omni-flash":
                caption = f"{caption}\n\n{flow_copy.msg('vid_omni_no_extend_hint')}"
            elif _video_can_extend(vref):
                caption = f"{caption}\n\n{flow_copy.msg('vid_result_actions_hint', edit=action_price('video_prompt_edit'), extend=video_extend_price(VIDEO_EXTEND_MODEL, vref.extend_index + 1))}"
            delivery_bytes, merged_video = await _video_delivery_bytes(vref, fetched_bytes=video_bytes)
            if not delivery_bytes:
                await _fail_retry(i)
                return

            filename = f"video_{i + 1}{'_full' if merged_video else ''}.mp4"
            try:
                await message.answer_video(
                    BufferedInputFile(delivery_bytes, filename),
                    caption=caption,
                    reply_markup=video_result_kb(vtoken),
                )
                sent_count += 1
            except Exception:
                log.exception("answer_video failed, falling back to document")
                try:
                    await message.answer_document(
                        BufferedInputFile(delivery_bytes, filename),
                        caption=caption,
                        reply_markup=video_result_kb(vtoken),
                    )
                    sent_count += 1
                except Exception:
                    log.exception("answer_document fallback also failed")
                    credit_store.refund(user_id, single_price)
                    refunded_units += 1

        st["vlast"] = {"model": model_id, "aspect": aspect, "count": vcount, "prompt": prompt}
        st.pop("vretry", None)  # успех — снимок для ретрая больше не нужен

        if sent_count:
            charged = single_price * sent_count
            metrics.log_event("video_success", user_id=user_id, source=vmode,
                              payload={"model": model_id, "count": sent_count})
            metrics.log_event("credits_charged", user_id=user_id, source=vmode,
                              payload={"amount": charged, "action": f"video_{vmode}"})
            metrics.log_flow_job(
                user_id=user_id, account_id=FLOW_ACCOUNT_ID,
                operation_type=f"video_{vmode}", model=model_id,
                bot_credits_charged=charged,
                refund_amount=single_price * refunded_units,
                duration_ms=_ms_since(_vid_started), status="success",
            )
            try:
                await status_msg.delete()
            except Exception:
                pass
        else:
            credit_store.refund(user_id, total_price)
            await status_msg.edit_text(flow_copy.msg("vid_gen_failed"))

    except Exception:
        log.exception("_video_generate_and_send failed")
        already_refunded = single_price * refunded_units
        remaining = total_price - already_refunded
        if remaining > 0:
            credit_store.refund(user_id, remaining)
        metrics.log_event("video_failed", user_id=user_id, source=vmode,
                          payload={"model": model_id, "reason": "exception"})
        metrics.log_flow_job(
            user_id=user_id, account_id=FLOW_ACCOUNT_ID,
            operation_type=f"video_{vmode}", model=model_id, bot_credits_charged=0,
            refund_amount=total_price, duration_ms=_ms_since(_vid_started),
            status="error", error_type="exception",
        )
        try:
            await status_msg.edit_text(flow_copy.msg("vid_gen_failed"))
        except Exception:
            pass
    finally:
        st.pop("vstep", None)
        _vid_clear(user_id)


async def _video_download(callback: types.CallbackQuery, user_id: int, token: str) -> None:
    """Скачать видео по кнопке — бесплатно, без повторной генерации."""
    ref = video_registry.get(token)
    if ref is None or ref.user_id != user_id:
        await callback.answer(flow_copy.msg("expired"), show_alert=True)
        return

    await callback.answer()
    status_msg = await callback.message.answer(flow_copy.msg("preparing_file"))
    video_bytes, merged_video = await _video_delivery_bytes(ref)

    if not video_bytes:
        await status_msg.edit_text("❌ Не удалось скачать видео. Попробуйте позже.")
        return

    from aiogram.types import BufferedInputFile

    filename = f"video_{ref.media_id[-8:]}{'_full' if merged_video else ''}.mp4"
    try:
        await callback.message.answer_document(
            BufferedInputFile(video_bytes, filename),
            caption="⬇️ Видео в полном качестве.",
        )
        await status_msg.delete()
    except Exception:
        log.exception("video download send failed")
        await status_msg.edit_text("❌ Не удалось отправить файл.")


async def _video_segment_download(
    callback: types.CallbackQuery, user_id: int, token: str
) -> None:
    """Скачать только новый фрагмент Extend (сам результат, без склейки таймлайна)."""
    ref = video_registry.get(token)
    if ref is None or ref.user_id != user_id:
        await callback.answer(flow_copy.msg("expired"), show_alert=True)
        return
    if not ref.media_id:
        await callback.answer(flow_copy.msg("expired"), show_alert=True)
        return

    await callback.answer()
    status_msg = await callback.message.answer(flow_copy.msg("preparing_file"))
    video_bytes = await client.fetch_video_bytes(ref.media_id)

    if not video_bytes:
        await status_msg.edit_text("❌ Не удалось скачать фрагмент. Попробуйте позже.")
        return

    from aiogram.types import BufferedInputFile

    filename = f"video_fragment_{ref.media_id[-8:]}.mp4"
    try:
        await callback.message.answer_document(
            BufferedInputFile(video_bytes, filename),
            caption="⬇️ Только новый фрагмент.",
        )
        await status_msg.delete()
    except Exception:
        log.exception("video fragment download send failed")
        await status_msg.edit_text("❌ Не удалось отправить файл.")


async def _video_edit_start(callback: types.CallbackQuery, user_id: int, token: str) -> None:
    """Ask for a prompt edit instruction for a delivered video."""
    ref = video_registry.get(token)
    if ref is None or ref.user_id != user_id:
        await callback.answer(flow_copy.msg("expired"), show_alert=True)
        return
    if not _video_can_edit(ref):
        await callback.answer(flow_copy.msg("vid_extend_unavailable"), show_alert=True)
        return

    st = _ws(user_id)
    _vid_clear(user_id)
    st["vawait"] = "vedit_prompt"
    st["vedit_token"] = token
    await callback.answer()
    await callback.message.answer(
        flow_copy.msg("vid_edit_ask_prompt", price=action_price("video_prompt_edit"))
    )


async def _video_extend_start(callback: types.CallbackQuery, user_id: int, token: str) -> None:
    """Ask for a continuation prompt for a delivered video."""
    ref = video_registry.get(token)
    if ref is None or ref.user_id != user_id:
        await callback.answer(flow_copy.msg("expired"), show_alert=True)
        return
    if not _video_can_extend(ref):
        await callback.answer(flow_copy.msg("vid_extend_unavailable"), show_alert=True)
        return

    st = _ws(user_id)
    _vid_clear(user_id)
    st["vawait"] = "vextend_prompt"
    st["vextend_token"] = token
    await callback.answer()
    next_price = video_extend_price(VIDEO_EXTEND_MODEL, ref.extend_index + 1)
    await callback.message.answer(
        flow_copy.msg("vid_extend_ask_prompt", price=next_price)
    )


def _video_prompt_edit_prompt(ref: VideoRef, instruction: str) -> str:
    instruction = (instruction or "").strip()
    return instruction


async def _video_prompt_edit_and_send(
    message: types.Message, ref: VideoRef, instruction: str, *, user_id: int
) -> None:
    if not instruction or len(instruction.strip()) < 3:
        await message.answer(flow_copy.msg("vid_prompt_too_short"))
        return
    if not _video_can_edit(ref):
        await message.answer(flow_copy.msg("vid_extend_unavailable"))
        return

    st = _ws(user_id)
    _vid_clear_reference_inputs(user_id)
    st["vmode"] = "edit"
    st["vmodel"] = ref.model_id or "omni-flash-4s"
    st["vfmt"] = _aspect_to_vfmt(ref.aspect_ratio)
    st["vcount"] = 1
    st["vawait"] = None
    st.pop("vedit_token", None)
    await _video_generate_and_send(
        message,
        instruction.strip(),
        user_id=user_id,
        unit_price_override=action_price("video_prompt_edit"),
        prompt_edited=True,
        status_text=flow_copy.msg("vid_edit_working"),
        source_video=ref,
        video_operation="edit",
    )


async def _video_extend_and_send(
    message: types.Message, ref: VideoRef, prompt: str, *, user_id: int
) -> None:
    if not prompt or len(prompt.strip()) < 3:
        await message.answer(flow_copy.msg("vid_prompt_too_short"))
        return
    if not _video_can_extend(ref):
        await message.answer(flow_copy.msg("vid_extend_unavailable"))
        return

    status_msg = await message.answer(flow_copy.msg("vid_working"))
    scene_id = ref.scene_id or await client.prepare_video_extend_scene(
        project_id=ref.project_id,
        workflow_id=ref.workflow_id,
    )
    if not scene_id:
        try:
            await status_msg.edit_text(flow_copy.msg("vid_extend_unavailable"))
        except Exception:
            await message.answer(flow_copy.msg("vid_extend_unavailable"))
        return
    try:
        await status_msg.delete()
    except Exception:
        pass

    st = _ws(user_id)
    _vid_clear_reference_inputs(user_id)
    st["vmode"] = "extend"
    st["vmodel"] = VIDEO_EXTEND_MODEL  # продление всегда через veo-lite, независимо от исходника
    st["vfmt"] = _aspect_to_vfmt(ref.aspect_ratio)
    st["vcount"] = 1
    st["vawait"] = None
    st.pop("vextend_token", None)
    # Progressive price: each extend in the chain costs VIDEO_EXTEND_STEP more.
    extend_price = video_extend_price(VIDEO_EXTEND_MODEL, ref.extend_index + 1)
    await _video_generate_and_send(
        message,
        prompt.strip(),
        user_id=user_id,
        unit_price_override=extend_price,
        status_text=flow_copy.msg("vid_working"),
        source_video=ref,
        video_operation="extend",
        source_scene_id=scene_id,
    )


async def _video_repeat_last(callback: types.CallbackQuery, user_id: int) -> None:
    """Повторить последнюю видеогенерацию с теми же настройками и промптом."""
    st = _ws(user_id)
    vlast = st.get("vlast")
    if not vlast or not vlast.get("prompt"):
        await callback.message.answer("Нет предыдущей видеогенерации.")
        return

    st["vmodel"] = vlast["model"]
    st["vfmt"] = _aspect_to_vfmt(vlast.get("aspect", "landscape"))
    st["vcount"] = vlast.get("count", VID_DEFAULT_COUNT)
    await _video_generate_and_send(callback.message, vlast["prompt"], user_id=user_id)


async def _start_topup(callback: types.CallbackQuery, user_id: int, pack_id: str):
    """Выставить счёт в Telegram Stars за выбранный пакет кредитов."""
    p = credit_pack(pack_id)
    if not p:
        await callback.answer("Пакет не найден", show_alert=True)
        return
    if p.get("test") and user_id not in ADMIN_IDS:
        await callback.answer("Пакет не найден", show_alert=True)
        return
    await callback.answer()
    try:
        prices = [types.LabeledPrice(label=f"{p['credits']} кредитов", amount=p["stars"])]
        await bot.send_invoice(
            chat_id=callback.message.chat.id,
            title=f"{p['credits']} кредитов",
            description=f"Пополнение баланса на {p['credits']} кредитов",
            payload=f"credits:{pack_id}:{user_id}",
            currency="XTR",  # Telegram Stars
            prices=prices,
        )
    except Exception:
        log.exception("send_invoice failed")
        await callback.message.answer(
            "⚠️ Оплата временно недоступна. Попробуйте позже."
        )


@dp.pre_checkout_query()
async def on_pre_checkout(query: types.PreCheckoutQuery):
    await query.answer(ok=True)


@dp.message(F.successful_payment)
async def on_successful_payment(message: types.Message):
    """Зачислить кредиты после успешной оплаты Stars."""
    sp = message.successful_payment
    payload = sp.invoice_payload if sp else ""
    user_id = message.from_user.id
    pack_id = payload.split(":")[1] if payload.startswith("credits:") else ""
    p = credit_pack(pack_id)
    if not p:
        log.warning(f"Unknown payment payload: {payload}")
        await message.answer("Платёж получен, но пакет не распознан. Напишите в поддержку.")
        return
    new_balance = credit_store.add(user_id, p["credits"])
    # Запоминаем платёж (charge_id) — нужен для возврата звёзд через /refund.
    charge_id = getattr(sp, "telegram_payment_charge_id", "") or ""
    stars_paid = getattr(sp, "total_amount", p["stars"])
    if charge_id:
        try:
            payment_store.add(user_id, charge_id, stars_paid, p["credits"], pack_id)
        except Exception:
            log.exception("payment_store.add failed")
    # Метрики: идемпотентная запись транзакции (charge_id уникален) + событие.
    metrics.record_transaction(
        provider="telegram_stars",
        provider_payment_id=charge_id or f"nocharge:{user_id}:{pack_id}",
        user_id=user_id, package_id=pack_id,
        amount_rub=round(stars_paid * STARS_TO_RUB, 2), stars_amount=stars_paid,
        credits_issued=p["credits"], status="paid",
    )
    metrics.log_event("payment_success", user_id=user_id, username=_username(message),
                      source="stars",
                      payload={"pack": pack_id, "stars": stars_paid, "credits": p["credits"]})
    log.info(f"💳 Оплата: +{p['credits']} кр пользователю {user_id} (баланс {new_balance})")
    await message.answer(
        flow_copy.msg("topup_done", credits=p["credits"], balance=new_balance)
    )
    await show_main_menu(message, user_id=user_id)


@dp.callback_query()
async def on_image_action(callback: types.CallbackQuery):
    """Единый обработчик инлайн-кнопок под картинкой (edit/vary/regen/mix/up)."""
    parsed = parse_action_callback(callback.data or "")
    if parsed is None:
        await callback.answer()
        return
    action, token = parsed
    user_id = callback.from_user.id
    ref = image_registry.get(token)
    if ref is None or ref.user_id != user_id:
        await callback.answer(
            "Кнопка устарела. Сгенерируйте изображение заново.", show_alert=True
        )
        return

    if action == "edit":
        pending_edits[user_id] = token
        st = _ws(user_id)
        st["await"] = "edit"
        # Формат по умолчанию = формат исходной картинки; модель — последняя выбранная.
        st["edit_fmt"] = _aspect_to_fmt(ref.aspect_ratio)
        st.setdefault("edit_imodel", DEFAULT_IMAGE_MODEL)
        await callback.answer()
        await callback.message.answer(
            flow_copy.msg("ask_edit_prompt"),
            reply_markup=edit_settings_kb(st["edit_fmt"], st["edit_imodel"]),
        )
    elif action == "revary":
        pending_edits[user_id] = token
        _ws(user_id)["await"] = "revary"
        await callback.answer()
        await callback.message.answer(flow_copy.msg("ask_revary_prompt"))
    elif action == "vary":
        await callback.answer("Делаю вариации 🎲")
        await _vary_and_send(callback.message, ref)
    elif action == "regen":
        await callback.answer("Генерирую ещё 🔄")
        await _regen_and_send(callback.message, ref)
    elif action == "up2x":
        await callback.answer("Повышаю чёткость ✨")
        await _enhance_and_send(callback.message, ref)
    elif action == "realup":
        await callback.answer("Увеличиваю разрешение 🔍")
        await _real_upscale_and_send(callback.message, ref)
    elif action in ("download", "upscale"):
        await callback.answer("Готовлю файл ⬇️")
        await _send_original_file(callback.message, ref)
    elif action == "mix":
        basket = mix_baskets[user_id]
        if len(basket) >= MIX_MAX:
            await callback.answer(f"В миксе уже {MIX_MAX}", show_alert=True)
            return
        basket.append(ref.source)
        await callback.answer(f"Добавлено в микс: {len(basket)}")
        if len(basket) >= 2:
            await callback.message.answer(
                f"🧩 В миксе {len(basket)} картинок. Пришлите `/mix ваш промпт`.",
                parse_mode="Markdown",
            )


async def _upload_photo_source_from_message(
    message: types.Message,
    *,
    user_id: int,
    status_msg: types.Message,
) -> dict | None:
    try:
        photo = message.photo[-1]
        buf = await bot.download(photo.file_id)
        data = buf.read() if hasattr(buf, "read") else bytes(buf)
    except Exception:
        log.exception("download user photo failed")
        await status_msg.edit_text("❌ Не удалось получить ваше фото.")
        return None

    project_id = await ensure_user_project(user_id)
    try:
        source = await keeper.upload_image(data, filename=f"tg_{user_id}.png")
    except Exception:
        log.exception("upload_image failed")
        source = None

    if not source or not source.get("mediaId"):
        await status_msg.edit_text(flow_copy.msg("upload_failed"))
        return None

    source.setdefault("_project_id", project_id)
    return source


# ── album (media group) buffering for video modes ──────────────────────
# Telegram delivers an album as separate photo messages sharing media_group_id.
# We debounce by group id: each photo (re)schedules a short flush; when the group
# goes quiet we process all of its photos at once. Keyed by the globally-unique
# media_group_id, so user sessions can't mix.
_album_buf: dict[str, list[types.Message]] = {}
_album_tasks: dict[str, "asyncio.Task"] = {}
_ALBUM_FLUSH_DELAY = 0.8


def _vid_caption(message: types.Message) -> str:
    return (message.caption or "").strip()


async def _flush_album(media_group_id: str, user_id: int):
    try:
        await asyncio.sleep(_ALBUM_FLUSH_DELAY)
    except asyncio.CancelledError:
        return
    messages = _album_buf.pop(media_group_id, [])
    _album_tasks.pop(media_group_id, None)
    if messages:
        await _handle_album_photos(messages, user_id=user_id)


async def _handle_album_photos(messages: list, *, user_id: int):
    """Обработать альбом для frames/ingredients: загрузить фото пачкой."""
    st = _ws(user_id)
    vmode = st.get("vmode")
    first = messages[0]
    status_msg = await first.answer(flow_copy.msg("uploading_photo"))
    sources = []
    for m in messages:
        src = await _upload_photo_source_from_message(m, user_id=user_id, status_msg=status_msg)
        if src:
            sources.append(src)
    try:
        await status_msg.delete()
    except Exception:
        pass
    if not sources:
        return
    caption = _vid_caption(first)
    if caption:
        st["vcaption_prompt"] = caption
    if vmode == "frames":
        # Первое фото → начало, второе → конец.
        st["vfrm_start"] = sources[0]
        if len(sources) > 1:
            st["vfrm_end"] = sources[1]
        await show_video_frames(first, user_id=user_id, edit=False)
    else:
        photos: list = st.setdefault("ving_photos", [])
        for s in sources:
            if len(photos) >= 4:
                break
            photos.append(s)
        await show_video_ingredients(first, user_id=user_id, edit=False)


@dp.message(F.photo)
async def handle_photo(message: types.Message):
    """Пользователь прислал фото (+ опц. подпись) — загружаем его в Flow и правим.

    С подписью — сразу применяем её как правку к загруженной картинке.
    Без подписи — запоминаем и просим прислать текст правки следующим сообщением.
    """
    user_id = message.from_user.id
    st = _ws(user_id)
    vawait = st.get("vawait")

    # Альбом в видео-режимах: буферизуем и обрабатываем пачкой (см. _flush_album).
    mgid = message.media_group_id
    if mgid and vawait in ("ving_photo", "vfrm_start", "vfrm_end"):
        _album_buf.setdefault(mgid, []).append(message)
        task = _album_tasks.get(mgid)
        if task:
            task.cancel()
        _album_tasks[mgid] = asyncio.create_task(_flush_album(mgid, user_id))
        return

    # Ingredients: upload and store Flow sources; generation stays blocked until API capture.
    if vawait == "ving_photo":
        photos: list = st.setdefault("ving_photos", [])
        if len(photos) < 4:
            status_msg = await message.answer(flow_copy.msg("uploading_photo"))
            source = await _upload_photo_source_from_message(
                message, user_id=user_id, status_msg=status_msg
            )
            if not source:
                return
            photos.append(source)
            caption = _vid_caption(message)
            if caption:
                st["vcaption_prompt"] = caption
            try:
                await status_msg.delete()
            except Exception:
                pass
        await show_video_ingredients(message, user_id=user_id, edit=False)
        return

    # Frames: collect start / end frame photo.
    if vawait == "vfrm_start":
        status_msg = await message.answer(flow_copy.msg("uploading_photo"))
        source = await _upload_photo_source_from_message(
            message, user_id=user_id, status_msg=status_msg
        )
        if not source:
            return
        st["vfrm_start"] = source
        st["vawait"] = "vfrm_end"
        caption = _vid_caption(message)
        if caption:
            st["vcaption_prompt"] = caption
        try:
            await status_msg.delete()
        except Exception:
            pass
        await show_video_frames(message, user_id=user_id, edit=False)
        return
    if vawait == "vfrm_end":
        status_msg = await message.answer(flow_copy.msg("uploading_photo"))
        source = await _upload_photo_source_from_message(
            message, user_id=user_id, status_msg=status_msg
        )
        if not source:
            return
        st["vfrm_end"] = source
        st["vawait"] = None
        caption = _vid_caption(message)
        if caption:
            st["vcaption_prompt"] = caption
        try:
            await status_msg.delete()
        except Exception:
            pass
        await show_video_frames(message, user_id=user_id, edit=False)
        return

    # Video wizard expects text, not a photo.
    if vawait in ("vprompt", "vedit_prompt", "vextend_prompt"):
        await message.answer(flow_copy.msg("vid_text_only_hint"))
        return

    caption = (message.caption or "").strip()

    status_msg = await message.answer(flow_copy.msg("uploading_photo"))
    try:
        # Берём максимальное по размеру фото.
        photo = message.photo[-1]
        buf = await bot.download(photo.file_id)
        data = buf.read() if hasattr(buf, "read") else bytes(buf)
    except Exception:
        log.exception("download user photo failed")
        await status_msg.edit_text("❌ Не удалось получить ваше фото.")
        return

    project_id = await ensure_user_project(user_id)
    try:
        source = await keeper.upload_image(data, filename=f"tg_{user_id}.png")
    except Exception:
        log.exception("upload_image failed")
        source = None

    if not source or not source.get("mediaId"):
        await status_msg.edit_text(flow_copy.msg("upload_failed"))
        return

    # Редактируем в ТОМ ЖЕ проекте, куда реально легла загрузка (иначе Google
    # не найдёт картинку). Если браузер не отдал проект — используем проект юзера.
    upload_project = source.pop("_project_id", None) or project_id

    ref = ImageRef(
        user_id=user_id, project_id=upload_project, source=source,
        prompt=caption or "uploaded image", aspect_ratio="landscape",
    )
    await status_msg.delete()

    if caption:
        await _edit_and_send(message, ref, caption)
        return

    # Без подписи — запоминаем как «текущую картинку для правки».
    token = image_registry.add(ref)
    pending_edits[user_id] = token
    _ws(user_id)["await"] = "edit"
    await message.answer(flow_copy.msg("photo_uploaded_ask_prompt"))


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_plain_text(message: types.Message):
    """Текст без команды: кнопки нижнего меню, ответ визарду или прямая генерация."""
    user_id = message.from_user.id
    text = message.text.strip()
    st = _ws(user_id)

    # Постоянная нижняя клавиатура: её нажатия приходят как обычный текст.
    if text == L("kb_gen"):
        _reset_image_flow(user_id)  # чистит и pending_edits (залипшее фото)
        await show_wizard(message, user_id=user_id, edit=False)
        return
    if text == L("kb_menu"):
        pending_edits.pop(user_id, None)
        st["await"] = None
        await show_main_menu(message, user_id=user_id, ensure_kb=True)
        return
    if text == L("kb_balance"):
        await show_balance(message, user_id=user_id, edit=False)
        return
    if text == L("kb_vid"):
        vlast = st.get("vlast")
        _vid_clear(user_id)
        pending_edits.pop(user_id, None)  # бросаем залипшее фото-правку при переходе в видео
        if vlast:
            st["vlast"] = vlast
        await show_video_family(message, user_id=user_id, edit=False)
        return

    # Видео-правка ждёт инструкцию к уже готовому ролику.
    if st.get("vawait") == "vedit_prompt":
        token = st.get("vedit_token")
        ref = video_registry.get(token) if token else None
        if ref is None or ref.user_id != user_id:
            _vid_clear(user_id)
            await message.answer(flow_copy.msg("expired"))
            await show_main_menu(message, user_id=user_id)
            return
        await _video_prompt_edit_and_send(message, ref, text, user_id=user_id)
        return

    if st.get("vawait") == "vextend_prompt":
        token = st.get("vextend_token")
        ref = video_registry.get(token) if token else None
        if ref is None or ref.user_id != user_id:
            _vid_clear(user_id)
            await message.answer(flow_copy.msg("expired"))
            await show_main_menu(message, user_id=user_id)
            return
        await _video_extend_and_send(message, ref, text, user_id=user_id)
        return

    # Видео-визард ждёт промпт.
    if st.get("vawait") == "vprompt":
        if message.photo:
            await message.answer(flow_copy.msg("vid_text_only_hint"))
            return
        st["vawait"] = None
        await _video_generate_and_send(message, text, user_id=user_id)
        return

    if _video_plain_text_ready(st):
        st["vawait"] = None
        await _video_generate_and_send(message, text, user_id=user_id)
        return

    awaiting = st.get("await")

    # Ждём промпт генерации из визарда.
    if awaiting == "prompt":
        st["await"] = None
        count = st.get("count", DEFAULT_COUNT)
        fmt = st.get("fmt", DEFAULT_FMT)
        await _generate_and_send(
            message, text, num_images=count,
            aspect_ratio=_fmt_to_aspect(fmt), actor_id=user_id,
            image_model=st.get("imodel", DEFAULT_IMAGE_MODEL),
        )
        return

    # Ждём текст правки/вариаций для конкретной картинки.
    if awaiting in ("edit", "revary"):
        token = pending_edits.get(user_id)
        ref = image_registry.get(token) if token else None
        if ref is not None and ref.user_id == user_id:
            ok = False
            if awaiting == "revary":
                ok = await _run_i2i(
                    message, ref, text, num_images=2, emoji="🎲",
                    fail_text=flow_copy.msg("nothing_returned"),
                )
            else:
                ok = await _edit_and_send(
                    message, ref, text,
                    aspect_ratio=_fmt_to_aspect(st.get("edit_fmt", _aspect_to_fmt(ref.aspect_ratio))),
                    image_model=st.get("edit_imodel", DEFAULT_IMAGE_MODEL),
                )
            if ok:
                st["await"] = None
                pending_edits.pop(user_id, None)
            return
        st["await"] = None
        pending_edits.pop(user_id, None)
        await message.answer(flow_copy.msg("expired"))
        await show_main_menu(message, user_id=user_id)
        return

    # ВАЖНО: раньше тут был «старый путь», который редактировал pending_edits-фото
    # даже без awaiting=="edit". Из-за этого после «фото → меню → генерация» промпт
    # уходил на правку залипшего фото. Убрано намеренно: правка идёт ТОЛЬКО при
    # awaiting in ("edit","revary") выше; навигация по меню чистит pending_edits.

    # Визард уже открыт и пользователь выбрал настройки — промпт из чата запускает
    # генерацию сразу, с текущими количеством/форматом/моделью (без «Сгенерировать»).
    if st.get("step") == "wizard":
        count = st.get("count", DEFAULT_COUNT)
        fmt = st.get("fmt", DEFAULT_FMT)
        imodel = st.get("imodel", DEFAULT_IMAGE_MODEL)
        st["step"] = None  # экран отработал — случайный текст потом не сгенерит повторно
        st.pop("pending_prompt", None)
        await _generate_and_send(
            message, text, num_images=count,
            aspect_ratio=_fmt_to_aspect(fmt), actor_id=user_id,
            image_model=imodel,
        )
        return

    # Иначе пользователь прислал промпт «вхолодную», не открыв визард. Не генерируем
    # вслепую: показываем выбор количества/формата/модели с уже сохранённым
    # промптом — после «Сгенерировать» сразу пойдёт генерация.
    last = st.get("last")
    st.clear()
    if last:  # сохраняем прошлые настройки как дефолт визарда
        st["last"] = last
        st["count"] = last.get("count", DEFAULT_COUNT)
        st["fmt"] = _aspect_to_fmt(last.get("aspect", "landscape"))
        st["imodel"] = last.get("imodel", DEFAULT_IMAGE_MODEL)
    st["pending_prompt"] = text
    await show_wizard(message, user_id=user_id, edit=False)


# ───────────────────────────────────────────
# ТОЧКА ВХОДА
# ───────────────────────────────────────────


async def main():
    log.info("🚀 Запуск Flow Bot...")

    if TELEGRAM_TOKEN == "PASTE_YOUR_TOKEN_HERE":
        log.error("❌ Укажите TELEGRAM_TOKEN в .env или прямо в коде!")
        return

    # Нативное меню команд Telegram (синяя кнопка «Меню» у поля ввода).
    try:
        await bot.set_my_commands(
            [
                types.BotCommand(command="start", description="Запуск и главное меню"),
                types.BotCommand(command="menu", description="🏠 Главное меню"),
                types.BotCommand(command="balance", description="💳 Баланс и пополнение"),
            ]
        )
    except Exception:
        log.warning("Не удалось установить меню команд")

    # Сначала запускаем браузер, потом бота
    await keeper.start()

    log.info("🤖 Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
