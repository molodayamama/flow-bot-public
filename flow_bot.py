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
import base64
import html
import itertools
import json
import logging
import os
import random
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, unquote, urlencode, urlparse

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
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
    CreditStore,
    CreditStoreSQLite,
    make_credit_store,
    PaymentStore,
    STARS_PACKS,
    action_price,
    pack as credit_pack,
    public_pack_ids,
    price_gen,
    robokassa_pack_amount,
    robokassa_payment_signature,
    robokassa_result_signature,
    REFERRAL_PARAM_PREFIX,
    REFERRAL_DAILY_CAP_CREDITS,
    REFERRAL_REWARD_WINDOW_DAYS,
    REFERRAL_TIER1_BONUS,
    REFERRAL_TIER2_BONUS,
    REFERRAL_TIER3_BONUS,
    REFERRAL_REFERRED_BONUS,
    REFERRAL_ONGOING_PCT,
    referral_milestone_bonus,
    referral_ongoing_bonus,
    CHANNEL_PARAM_PREFIX,
    parse_channel_seed,
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
from flow_core import (
    FlowAccount,
    parse_flow_accounts,
    AccountPool,
)
import flow_copy
from generation import backend_service
import metrics
from product.scenarios.animate_photo import AnimatePhotoConfig, AnimatePhotoScenario
import prompts_lib
import config.settings as _cfg

from config.video import (
    VID_DEFAULT_FMT,
    VID_FRAMES_VARIANTS,
    VID_REF_VARIANTS,
    _VID_FMT_NAMES,
    _VID_OMNI_DURATIONS,
    _VID_OMNI_DUR_MODEL,
    _VID_QUICKSTART_MODEL,
    _VID_STYLES,
    _VID_VEO_QUALITY_CYCLE,
    _VID_VEO_QUAL_MODEL,
    _VID_VEO_QUAL_NAMES,
    VID_REF_DEFAULT_MODEL,
    VIDEO_EXTEND_MODEL,
    SELECT_STYLE,
)


# Topup/pack pricing helpers extracted to billing/pricing.py (Phase 5 wave 3).
from billing.pricing import (
    _robokassa_pack_amount,
    _rub_display,
    _topup_image_price,
    _topup_video_price,
    _pack_usage_hint,
    _stars_pack_label,
    _robokassa_pack_label,
)


# App/payment config extracted to config/settings.py (Phase 5 wave 2);
# re-exported so flow_bot keeps its references unchanged.
from config.settings import (
    STARS_TO_RUB,
    ROBOKASSA_CARD_DISCOUNT_PCT,
    _env_any,
    ROBOKASSA_MERCHANT_LOGIN,
    ROBOKASSA_TEST,
    ROBOKASSA_PASSWORD1,
    ROBOKASSA_PASSWORD2,
    ROBOKASSA_ENABLED,
    ROBOKASSA_PAY_URL,
    ROBOKASSA_PUBLIC_BASE_URL,
    ROBOKASSA_INC_CURR_LABEL,
    ROBOKASSA_WEB_HOST,
    ROBOKASSA_WEB_PORT,
    _robokassa_clean_scope,
    BOT_USERNAME,
    BOT_MODE,
    ROBOKASSA_SCOPE,
    STARS_PAYMENT_ENABLED,
    SBP_PAYMENT_ENABLED,
    TOPUP_TEST_PACKS_ENABLED,
)


# Telegram keyboard builders extracted to channels/telegram/keyboards.py
# (Phase 5); re-exported so flow_bot handlers keep working.
from channels.telegram.keyboards import (
    main_menu_kb,
    topup_kb,
    topup_method_kb,
    topup_stars_kb,
    topup_robo_kb,
    _include_test_packs,
    L,
    _menu_button,
    _guided_step_kb,
    _img_retry_kb,
    _mp_back_kb,
    _mp_sku_open_kb,
    _onboarding_step1_kb,
    _onboarding_step2_kb,
    _photo_route_kb,
    _prompt_picker_kb,
    _templates_picker_kb,
    mp_jobs_kb,
    mp_more_kb,
    mp_root_kb,
    _MP_PLAT_NAMES,
    _MP_PLATFORM_FMT,
    _MP_PLATFORM_SIZE,
    _MP_PLATFORM_GUIDANCE,
    _MP_NICHES,
    _MP_JOB_LABELS,
    _mp_platform_fmt,
    _mp_platform_format_label,
    _mp_platform_guidance,
    _mp_jobs_text,
    _mp_more_text,
    _mp_photo_settings_kb,
    mp_series_kb,
    _mp_niche_guidance,
    _mp_niche_kb,
    _imodel_row,
    _nwiz_styles_kb,
    _image_keyboard,
    _seller_image_keyboard,
    wizard_kb,
    video_family_kb,
    video_variant_kb,
    video_wizard_kb,
    video_result_kb,
    ingredients_kb,
    frames_kb,
    edit_settings_kb,
    edit_confirm_kb,
    _sel_btn,
    _fmt_rows,
    _vid_fmt_count_rows,
    _imodel_toggle_btn,
    _vid_model_row,
    _vid_family_min_price,
    _video_can_edit,
    _video_can_extend,
    _slides_word,
)
from channels.telegram.texts import (
    _MP_JOB_OUTCOMES,
    _MP_SERIES_COUNTS,
    _MP_SERIES_LABELS,
    _mp_brand_kit,
    _mp_niche,
    _mp_niche_label,
    _mp_brandkit_text,
    _mp_niche_text,
    _mp_sku_open_text,
    _mp_photo_request_text,
    _mp_video_request_text,
    _mp_series_request_text,
    _seller_history_job_platform,
    _seller_history_status,
    _seller_history_cost,
    _seller_history_text as _telegram_seller_history_text,
)
from channels.telegram.routers import commands as tg_commands_router
from channels.telegram.routers import menu as tg_menu_router
from channels.telegram.routers import marketplace as tg_marketplace_router
from channels.telegram.routers import onboarding as tg_onboarding_router
from channels.telegram.routers import photo_route as tg_photo_route_router
from channels.telegram.routers import image_retry as tg_image_retry_router
from channels.telegram.routers import image_action as tg_image_action_router
from channels.telegram.routers import ideas_hub as tg_ideas_hub_router
from channels.telegram.routers import ideas_flow as tg_ideas_flow_router
from channels.telegram.routers import agent as tg_agent_router
from channels.telegram.routers import video_upload as tg_video_upload_router
from channels.telegram.routers import edit_settings as tg_edit_settings_router
from channels.telegram.routers import animate as tg_animate_router
from channels.telegram.routers import wizard as tg_wizard_router
from channels.telegram.routers import fallback as tg_fallback_router

from referrals.service import ReferralService


def _seller_history_text(user_id: int) -> str:
    return _telegram_seller_history_text(user_id, metrics_module=metrics)

# Flow provider extracted into flow_provider/ (PR-2a). Re-exported so the
# rest of flow_bot.py keeps its existing references unchanged.
from flow_provider.runtime_config import (
    USER_DATA_DIR,
    PROXY_URL,
    BROWSER_PROXY_URL,
    API_PROXY_URL,
    CAPMONSTER_PROXY_URL,
    TWOCAPTCHA_KEY,
    CAPMONSTER_KEY,
    CAPTCHA_PROVIDER,
    TWOCAPTCHA_SCORE,
    TWOCAPTCHA_SOLVE_TIMEOUT,
    CAPMONSTER_SOLVE_TIMEOUT,
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
from flow_provider.client import FlowHttpClient, SessionKeeper


# ───────────────────────────────────────────
ENV_FILE = os.getenv("ENV_FILE", ".env") or ".env"
load_dotenv(ENV_FILE)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "PASTE_YOUR_TOKEN_HERE")
# Метрики: ярлык Flow-аккаунта (для flow_jobs) и курс Stars→₽ для выручки.
FLOW_ACCOUNT_ID = os.getenv("FLOW_ACCOUNT_ID", "default")
# Пул Flow-аккаунтов: записи "id=путь_к_chrome_профилю" через ';' (или ',').
# Пусто — один аккаунт FLOW_ACCOUNT_ID на USER_DATA_DIR (одиночный режим, как
# раньше). Второй аккаунт = вторая запись в env, код менять не нужно.
FLOW_ACCOUNTS_RAW = os.getenv("FLOW_ACCOUNTS", "")
FLOW_ACCOUNTS_STATE_FILE = os.getenv("FLOW_ACCOUNTS_STATE_FILE", "flow_accounts_state.json")


ROBOKASSA_HASH_ALGO = _env_any("ROBOKASSA_HASH_ALGO", "ROBOKASSA_HASH_ALGORITHM", default="md5")


# Имя бота для реферальных ссылок (берётся из get_me() на старте; env — фолбэк).
# Режим бота: "consumer" (как сейчас) или "seller" (@photozhab_wb_bot, меню
# «Маркетплейсы», селлерские пакеты). Один и тот же код, флаг на процесс;
# у каждого процесса свой TELEGRAM_TOKEN/BOT_USERNAME/USER_CREDITS_FILE.
# См. docs/SELLER_BOT_PLAN.md.
ROBOKASSA_CONSUMER_RESULT_URL = _env_any(
    "ROBOKASSA_CONSUMER_RESULT_URL",
    default="http://127.0.0.1:8081/robokassa/result",
)
ROBOKASSA_SELLER_RESULT_URL = _env_any(
    "ROBOKASSA_SELLER_RESULT_URL",
    default="http://127.0.0.1:8082/robokassa/result",
)
ROBOKASSA_CONSUMER_BOT_USERNAME = _env_any("ROBOKASSA_CONSUMER_BOT_USERNAME", default="photozhab_bot").lstrip("@")
ROBOKASSA_SELLER_BOT_USERNAME = _env_any("ROBOKASSA_SELLER_BOT_USERNAME", default="photozhab_wb_bot").lstrip("@")
TG_PROXY_URL = os.getenv("TG_PROXY_URL", "")    # SOCKS5 прокси для Telegram API
# auto: browser → capmonster → 2captcha; или явно: browser | capmonster | 2captcha
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
# Gemini API key for prompt enhancement (optional). When absent, «Улучшить промпт»
# button is hidden from the image wizard settings screen.
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
# Idle tab parking: the Flow SPA burns ~0.3 CPU core per open tab even when idle.
# After IDLE_PARK_SEC with no page op, the keeper navigates its tab to about:blank
# (SPA stops → CPU drops); the next page op wakes it (navigates back to Flow).
# Set IDLE_PARK_SEC<=0 to disable (legacy always-on-Flow behaviour).


# Файл с картой telegram_user_id -> flow_project_id (каждый юзер = свой проект).
USER_PROJECTS_FILE = os.getenv("USER_PROJECTS_FILE", "user_projects.json")
# Файл с захваченным форматом запроса редактирования (imageInputs). Бот учится
# ему один раз, перехватив реальную правку в открытом окне Flow.
# Сырой дамп перехваченной правки (реальные значения imageInputs + id недавних
# картинок) для ручной достройки шаблона. gitignored, без bearer/recaptcha.
# Недавно выданные картинки (id), сохраняются на диск чтобы пережить рестарт.
# Схемы ответов на загрузку фото (values-free) если mediaId не нашёлся — для доводки.
# Изученный формат запроса НАСТОЯЩЕГО апскейла (перехватывается из браузера один раз).
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
# Максимум параллельных image-джобов на один аккаунт (per-account semaphore).
# video-capable аккаунты могут получить меньше через ACC_VIDEO_CAPACITY.
ACC_IMAGE_CAPACITY = max(1, int(os.getenv("ACC_IMAGE_CAPACITY", "2")))
# Максимум параллельных video-джобов на video-capable аккаунт.
ACC_VIDEO_CAPACITY = max(1, int(os.getenv("ACC_VIDEO_CAPACITY", "1")))
try:
    MIN_READY_ACCOUNTS = max(1, int(os.getenv("MIN_READY_ACCOUNTS", "1")))
except (TypeError, ValueError):
    MIN_READY_ACCOUNTS = 1

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


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

            log.info("🧦 Telegram через SOCKS5: configured")
            return Bot(token=TELEGRAM_TOKEN, session=_SocksSession())
        except ImportError:
            log.error("❌ aiohttp-socks не установлен! pip install aiohttp-socks")

    return Bot(token=TELEGRAM_TOKEN)


# Пул аккаунтов: каждый со своим Chrome-профилем, keeper'ом и HTTP-клиентом.
# Без FLOW_ACCOUNTS — ровно один аккаунт (FLOW_ACCOUNT_ID/USER_DATA_DIR), и всё
# работает как раньше. `keeper`/`client` остаются алиасами первого аккаунта
# (диагностика, захваты, одиночные пути).
FLOW_ACCOUNTS = parse_flow_accounts(
    FLOW_ACCOUNTS_RAW, default_id=FLOW_ACCOUNT_ID, default_dir=USER_DATA_DIR
)
account_pool = AccountPool(
    FLOW_ACCOUNTS, FLOW_ACCOUNTS_STATE_FILE,
    default_image_capacity=ACC_IMAGE_CAPACITY,
    default_video_capacity=ACC_VIDEO_CAPACITY,
)
keepers: dict[str, SessionKeeper] = {
    acc.id: SessionKeeper(
        account_id=acc.id,
        profile_dir=acc.profile_dir,
        browser_proxy_url=acc.browser_proxy_url,
        api_proxy_url=acc.api_proxy_url,
    )
    for acc in FLOW_ACCOUNTS
}
clients: dict[str, FlowHttpClient] = {
    acc_id: FlowHttpClient(kp) for acc_id, kp in keepers.items()
}
DEFAULT_ACCOUNT_ID = FLOW_ACCOUNTS[0].id
keeper = keepers[DEFAULT_ACCOUNT_ID]
client = clients[DEFAULT_ACCOUNT_ID]

# Operator-raised local gost proxies (admin ISP-proxy onboarding). Consumer-only:
# the seller bot has no account pool / admin panel. None disables the routes.
local_proxy_sup = None
if not _cfg.IS_SELLER:
    try:
        from proxy_supervisor import LocalProxySupervisor
        local_proxy_sup = LocalProxySupervisor()
    except Exception:
        log.warning("LocalProxySupervisor init failed; ISP-proxy routes disabled",
                    exc_info=True)

startup_state: dict = {
    "phase": "init",
    "polling": False,
    "ready_accounts": 0,
    "total_accounts": len(keepers),
    "min_ready": 0 if _cfg.IS_SELLER else min(MIN_READY_ACCOUNTS, len(keepers)),
    "accounts": {
        acc_id: {"status": "pending", "ready": False, "updated_at": None}
        for acc_id in keepers
    },
}


def _startup_set_phase(phase: str, **extra) -> None:
    startup_state["phase"] = phase
    startup_state["updated_at"] = time.time()
    for key, value in extra.items():
        startup_state[key] = value


def _startup_set_account(acc_id: str, status: str, *, ready: bool = False, error: str | None = None) -> None:
    item = startup_state.setdefault("accounts", {}).setdefault(acc_id, {})
    item.update({"status": status, "ready": bool(ready), "updated_at": time.time()})
    if error:
        item["error"] = error
    elif "error" in item:
        item.pop("error", None)
    ready_count = sum(1 for a in startup_state.get("accounts", {}).values() if a.get("ready"))
    startup_state["ready_accounts"] = ready_count


def _account_for(user_id: int) -> str | None:
    """Аккаунт пула для джобы юзера (sticky), None — весь пул недоступен."""
    return account_pool.pick_for(user_id)


def _account_for_image(
    user_id: int,
    *,
    prefer_image_only: bool = False,
    exclude: set[str] | None = None,
) -> str | None:
    return account_pool.pick_for_image(
        user_id, prefer_image_only=prefer_image_only, exclude=exclude,
    )


def _cached_gcredits_hints() -> dict:
    hints = {}
    for acc_id, kp in keepers.items():
        cached = getattr(kp, "_gcredits_cache", None)
        if isinstance(cached, dict):
            hints[acc_id] = dict(cached)
    return hints


def _video_family_for_model(model_id: str) -> str:
    meta = video_model_meta(model_id)
    return str((meta or {}).get("family") or "unknown")


def _video_scores_for_model(model_id: str, min_credits: int = 0) -> dict:
    return metrics.report_video_account_scores(
        model_family=_video_family_for_model(model_id),
        credit_hints=_cached_gcredits_hints(),
        min_credits=int(min_credits or 0),
    )


def _video_account_health_reason(account_id: str | None, model_id: str, min_credits: int = 0) -> str | None:
    if not account_id:
        return "missing_account"
    if not account_pool.is_reference_usable(account_id):
        return "account_unavailable"
    score = (_video_scores_for_model(model_id, min_credits).get(account_id) or {})
    if score.get("proxy_failed"):
        return "proxy_check_failed"
    if score.get("recent_unusual_403"):
        return "recent_public_error_unusual_activity"
    return None


def _account_for_video(user_id: int, *, model_id: str = "omni-flash-4s", min_credits: int = 0) -> str | None:
    """Аккаунт для видео-джобы — только среди video_capable, None — нет доступных."""
    return account_pool.pick_for_video(
        user_id,
        model_family=_video_family_for_model(model_id),
        health_scores=_video_scores_for_model(model_id, min_credits),
    )


def _keeper_for_acc(account_id: str | None) -> SessionKeeper:
    return keepers.get(account_id or "", keeper)


def _client_for_acc(account_id: str | None) -> FlowHttpClient:
    return clients.get(account_id or "", client)


def _keeper_for(user_id: int) -> SessionKeeper:
    return _keeper_for_acc(_account_for(user_id))


def _client_for(user_id: int) -> FlowHttpClient:
    return _client_for_acc(_account_for(user_id))


bot = _make_bot()
dp = Dispatcher()

# Per-user session state + media registries now live in storage/ (Phase 10);
# re-imported so existing references keep working unchanged.
from storage.session_state import (
    user_last_request,
    user_busy,
    pending_edits,
    pending_photo_routes,
    mix_baskets,
    wizard_state,
    _ws,
)
from storage.media_registry import image_registry, video_registry

BUSY_MAX_SEC = 300  # старше — считаем зависшим и отпускаем

# Каждый Telegram-пользователь -> свой Flow-проект (переживает рестарт).
project_store = UserProjectStore(USER_PROJECTS_FILE)
MIX_MAX = 4

# Баланс кредитов на пользователя (монетизация).
_USER_CREDITS_FILE = os.getenv("USER_CREDITS_FILE", "user_credits.json")
credit_store = make_credit_store(_USER_CREDITS_FILE)
payment_store = PaymentStore(os.getenv("PAYMENTS_FILE", "payments.json"))

# Метрики (SQLite). init_db не бросает; log_* безопасны при сбое БД.
try:
    metrics.init_db(os.getenv("METRICS_DB", "metrics.db"))
except Exception:
    log.warning("metrics.init_db failed; metrics disabled", exc_info=True)

# One-time migration: if SQLite credits store is active, import existing JSON
# balances. credits_migrate_from_json uses INSERT OR IGNORE → fully idempotent.
if isinstance(credit_store, CreditStoreSQLite):
    try:
        migrated = metrics.credits_migrate_from_json(_USER_CREDITS_FILE)
        if migrated:
            log.info("credits: migrated %d balances from JSON to SQLite", migrated)
    except Exception:
        log.warning("credits: JSON→SQLite migration failed", exc_info=True)


def _username(message_or_user) -> str | None:
    """Best-effort @username/имя для метрик (никогда не бросает).

    Возвращает None если резолвится в самого бота — частый источник этого:
    ``callback.message.from_user`` это бот (сообщение с кнопками отправил он),
    а не нажавший юзер. Вызывающий код должен передавать ``callback.from_user``,
    но эта защита не даёт "@botname" протечь в события/метрики, даже если
    где-то по ошибке передали callback.message. report_recent_events() в
    metrics.py берёт последний НЕ-null username по user_id, так что None
    здесь просто откатывается на последнее известное настоящее имя.
    """
    try:
        u = getattr(message_or_user, "from_user", message_or_user)
        if BOT_USERNAME and u.username == BOT_USERNAME:
            return None
        return u.username or u.full_name
    except Exception:
        return None


# ── реферальная программа (экономика в docs/REFERRAL.md) ───────────────

def _referral_link(user_id: int) -> str:
    """Личная реферальная ссылка пользователя (deep-link /start ref_<id>)."""
    if BOT_USERNAME:
        return f"https://t.me/{BOT_USERNAME}?start={REFERRAL_PARAM_PREFIX}{user_id}"
    return f"{REFERRAL_PARAM_PREFIX}{user_id}"


def _invite_button(user_id: int) -> types.InlineKeyboardButton:
    """Кнопка «поделиться» под результатом — открывает диалог пересылки."""
    from urllib.parse import quote
    link = _referral_link(user_id)
    share = (
        "https://t.me/share/url?url=" + quote(link, safe="")
        + "&text=" + quote(flow_copy.msg("invite_share_text"), safe="")
    )
    return types.InlineKeyboardButton(text=flow_copy.label("invite_friend"), url=share)


async def _show_referral_screen(message: types.Message, *, user_id: int, edit: bool) -> None:
    stats = metrics.referral_stats(user_id)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [_invite_button(user_id)],
        [_menu_button("menu", "m:menu")],
    ])
    text = flow_copy.msg(
        "referral_screen",
        link=html.escape(_referral_link(user_id)),
        invited=stats["invited"], earned=stats["earned"],
        referred=REFERRAL_REFERRED_BONUS,
        t1=REFERRAL_TIER1_BONUS, t2=REFERRAL_TIER2_BONUS, t3=REFERRAL_TIER3_BONUS,
        pct=int(round(REFERRAL_ONGOING_PCT * 100)),
    )
    if edit:
        await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


def _maybe_apply_referral_rewards(
    referred_user_id: int, *, stars_paid: int, credits_issued: int,
    pack_id: str, provider_payment_id: str,
) -> None:
    """Reward the referrer for a referred user's payment (Phase 9 service)."""
    _referral_service.apply_payment_rewards(
        referred_user_id, stars_paid=stars_paid, credits_issued=credits_issued,
        pack_id=pack_id, provider_payment_id=provider_payment_id,
    )


def _first_referral_cta_text(user_id: int) -> str | None:
    """Invite CTA shown after every successful generation until user has referrals.

    Реферальные награды пригласившему начисляются ТОЛЬКО в on_successful_payment
    (anti-farm, REFERRAL.md §3) — здесь лишь зовём пригласить друга.
    """
    try:
        if int(metrics.referral_stats(user_id).get("invited") or 0) > 0:
            return None
    except Exception:
        return None
    return flow_copy.msg("first_referral_cta", referred=REFERRAL_REFERRED_BONUS)


async def _post_generation_referral_hooks(
    message: types.Message,
    user_id: int,
    *,
    send_cta: bool = True,
) -> None:
    if not send_cta:
        return
    text = _first_referral_cta_text(user_id)
    if not text:
        return
    try:
        await message.answer(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [_invite_button(user_id)],
        ]))
    except Exception:
        pass


def _clawback_referral_rewards(referred_user_id: int, charge_id: str) -> None:
    """Reverse referral rewards for a refunded payment (Phase 9 service)."""
    _referral_service.clawback(referred_user_id, charge_id)


def _notify_referrer(referrer_id: int, bonus: int, *, message_key: str = "referral_reward_got") -> None:
    """Best-effort уведомление реферера о начислении (не блокирует оплату)."""
    async def _send():
        try:
            await bot.send_message(
                referrer_id, flow_copy.msg(message_key, bonus=bonus),
                parse_mode="HTML",
            )
        except TelegramForbiddenError:
            metrics.mark_user_blocked(referrer_id)
        except Exception:
            pass
    try:
        asyncio.create_task(_send())
    except Exception:
        pass


# Referral reward orchestration moved to referrals/ (Phase 9); wired with this
# process's credit store and Telegram notifier.
_referral_service = ReferralService(
    store=credit_store, metrics=metrics, notify=_notify_referrer, log=log
)

# wizard_state (button-wizard per-user state) now lives in storage/session_state (Phase 10).


# _ws moved to storage/session_state.py (Phase 5); re-imported below.


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
    pending_photo_routes.pop(user_id, None)
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
    busy_since = user_busy.get(user_id)
    if busy_since is not None and (time.time() - busy_since) < BUSY_MAX_SEC:
        await message.answer("⏳ Ваш предыдущий запрос ещё выполняется — дождитесь его.")
        raise RateLimited
    if busy_since is not None:
        # Слот протух (операция зависла) — отпускаем и пускаем новый запрос.
        log.warning("user_busy слот для %s протух (%.0fс), отпускаю", user_id, time.time() - busy_since)
        user_busy.pop(user_id, None)

    elapsed = time.time() - user_last_request[user_id]
    remaining = COOLDOWN_SEC - elapsed
    if remaining > 0 and remaining > MAX_AUTO_WAIT_SEC:
        await message.answer(f"⏱️ Слишком часто. Подождите ещё {int(remaining)} сек.")
        raise RateLimited

    # Занимаем слот ДО любого await чтобы избежать race condition:
    # два одновременных запроса иначе оба пройдут проверку user_busy
    # и уйдут в sleep параллельно.
    user_busy[user_id] = time.time()
    try:
        if remaining > 0:
            await message.answer(f"⏱️ Подождите {int(remaining) + 1} сек, выполняю...")
            await asyncio.sleep(remaining)
        yield
    finally:
        user_busy.pop(user_id, None)
        user_last_request[user_id] = time.time()


# Credit-gate primitives now live in billing/credit_gate.py (PR-7a). Re-exported
# here so the rest of flow_bot and its callers keep working unchanged.
from billing.credit_gate import (  # noqa: E402
    Charge as _Charge,
    NotEnoughCredits,
    open_credit_gate,
)


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

    async def _on_insufficient(have: int, needed: int) -> None:
        # Telegram-specific balance UI; the charge/refund rule lives in billing/.
        if have == 0:
            await message.answer(
                flow_copy.msg("zero_balance"),
                reply_markup=_zero_balance_kb(),
                parse_mode="HTML",
            )
        else:
            kb = types.InlineKeyboardMarkup(
                inline_keyboard=[[_menu_button("topup", "m:topup")], [_menu_button("menu", "m:menu")]]
            )
            await message.answer(
                flow_copy.msg("low_balance", needed=needed, have=have), reply_markup=kb,
                parse_mode="HTML",
            )

    async with open_credit_gate(
        credit_store, user_id, price, on_insufficient=_on_insufficient
    ) as charge:
        yield charge


def _project_key(account_id: str, user_id: int) -> str:
    """Ключ проекта в сторе: проект юзера живёт на конкретном аккаунте пула."""
    return f"{account_id}:{user_id}"


async def ensure_user_project(user_id: int, *, account_id: str | None = None) -> str | None:
    """Вернуть Flow-проект пользователя, создав его при первом обращении.

    Проект привязан к аккаунту пула (sticky): при failover на другой аккаунт
    юзеру создаётся новый проект там. Старые записи без префикса аккаунта
    мигрируются на ключ дефолтного аккаунта при первом чтении.

    При неудаче создания (или при выключенной фиче) возвращает ``None`` —
    вызывающий код тогда использует общий проект сессии (старое поведение),
    чтобы генерация всё равно работала. После ``PROJECT_CREATION_MAX_FAILURES``
    неудач подряд фича отключается на сессию, чтобы не висеть на каждом запросе.
    """
    global _project_creation_failures, _per_user_projects_enabled

    acc_id = account_id or _account_for(user_id) or DEFAULT_ACCOUNT_ID
    key = _project_key(acc_id, user_id)
    existing = project_store.get(key)
    if existing:
        return existing
    if acc_id == DEFAULT_ACCOUNT_ID:
        legacy = project_store.get(user_id)  # записи времён одного аккаунта
        if legacy:
            project_store.set(key, legacy)
            return legacy
    if not _per_user_projects_enabled:
        return None

    try:
        pid = await _keeper_for_acc(acc_id).create_new_project()
    except Exception:
        log.exception("create_new_project failed")
        pid = None

    if pid:
        _project_creation_failures = 0
        project_store.set(key, pid)
        log.info(f"📋 Пользователю {user_id} выдан проект {pid} (аккаунт {acc_id})")
        return pid

    _project_creation_failures += 1
    if _project_creation_failures >= PROJECT_CREATION_MAX_FAILURES:
        _per_user_projects_enabled = False
        log.warning(
            "⚠️ Per-user проекты отключены после %d неудач — работаю на общем проекте сессии.",
            _project_creation_failures,
        )
    return None


# ── меню и визард (кнопочный UX) ──────────────────────────────────────


# ── Маркетплейс-меню селлер-бота (docs/SELLER_BOT_PLAN.md §4) ─────────────


def _mp_platform_aspect(platform: str) -> str:
    return _fmt_to_aspect(_mp_platform_fmt(platform))


_MP_STALE_SCREEN_TEXT = "Это старый экран — открой актуальное меню"


def _mp_message_id(message) -> int:
    try:
        return int(getattr(message, "message_id", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _mp_stamp_message(user_id: int, message) -> None:
    msg_id = _mp_message_id(message)
    if msg_id:
        _ws(user_id)["mp_active_msg_id"] = msg_id


def _mp_is_stale_callback(user_id: int, callback: types.CallbackQuery) -> bool:
    current = _ws(user_id).get("mp_active_msg_id")
    try:
        current_id = int(current or 0)
    except (TypeError, ValueError):
        current_id = 0
    clicked_id = _mp_message_id(getattr(callback, "message", None))
    return bool(current_id and clicked_id and clicked_id != current_id)


async def _mp_reject_stale_callback(callback: types.CallbackQuery) -> None:
    await callback.answer(_MP_STALE_SCREEN_TEXT, show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


# Подсказка-сид к промпту под каждую задачу (формат подставляется отдельно).
_MP_JOB_SEED = {
    "whitebg": "товар на чистом белом фоне для карточки маркетплейса, студийный свет",
    "info": "инфографика-карточка товара: крупный товар, место под заголовок и буллеты",
    "model": "товар на модели / в интерьере, реалистичная сцена для карточки",
    "cover": "обложка/главный слайд карточки товара, цепляющий ракурс",
    "bg": "заменить фон у фото товара на чистый и продающий",
}
_MP_PRODUCT_PHOTO_JOBS = frozenset(_MP_JOB_SEED)


def _mp_confirm_screen(user_id: int):
    """Экран подтверждения перед генерацией: задача, площадка, бренд-кит, ниша,
    ЦЕНА, баланс — чтобы селлер видел стоимость и контекст до списания."""
    st = _ws(user_id)
    plat = st.get("mp_platform", "wb")
    plat_name = _MP_PLAT_NAMES.get(plat, plat)
    format_label = _mp_platform_format_label(plat)
    kind = st.get("mp_pending_kind", "photo")
    try:
        brand = _mp_brand_kit(user_id)
    except Exception:
        brand = ""
    try:
        niche = _mp_niche_label(user_id)
    except Exception:
        niche = ""
    caption = (st.get("mp_pending_caption") or "").strip()
    try:
        credits = credit_store.balance(user_id)
    except Exception:
        credits = 0
    if kind == "series":
        count = st.get("mp_series_count", 3)
        if count not in _MP_SERIES_COUNTS:
            count = 3
        price = action_price("mp_series", count)
        job_label = f"серия · {count} {_slides_word(count)}"
    else:
        job = st.get("mp_preset", "whitebg")
        price = action_price("edit")
        job_label = _MP_JOB_LABELS.get(job, job)
    lines = [
        f"🛒 <b>{html.escape(plat_name)}</b> · {html.escape(job_label)}",
        "📎 Фото товара принято.",
        "",
        f"🎨 Бренд-кит: {html.escape(brand) if brand else '— (не задан)'}",
        f"🏷️ Ниша: {html.escape(niche) if niche else '— (не задана)'}",
    ]
    if caption:
        lines.append(f"📝 Пожелание: {html.escape(caption[:150])}")
    lines.append(f"📐 Формат: {html.escape(format_label)}")
    lines.append(f"💰 Стоимость: <b>{price} кр</b> · Баланс: {credits} кр")
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=f"✅ Создать · {price} кр", callback_data="mp:create")],
        [types.InlineKeyboardButton(text="✏️ Сменить задачу", callback_data="m:mp")],
        [_menu_button("cancel", "m:menu")],
    ])
    return "\n".join(lines), kb


def _mp_job_instruction(
    job: str,
    platform: str,
    seller_note: str | None = None,
    brand_kit: str | None = None,
    niche: str | None = None,
) -> str:
    seed = _MP_JOB_SEED.get(job, "сделать продающую карточку товара для маркетплейса")
    platform_name = _MP_PLAT_NAMES.get(platform, platform)
    format_label = _mp_platform_format_label(platform)
    platform_guidance = _mp_platform_guidance(platform)
    prompt = (
        f"{seed}. Используй загруженное фото как исходный товар, сохрани товар узнаваемым. "
        f"Формат карточки {format_label}, площадка: {platform_name}. {platform_guidance}"
    )
    guidance = _mp_niche_guidance(niche)
    if guidance:
        prompt += f" Ниша товара: {guidance}"
    note = (seller_note or "").strip()
    if note:
        prompt += f" Уточнение продавца: {note}"
    brand = (brand_kit or "").strip()
    if brand:
        prompt += f" Бренд-кит продавца: {brand}"
    return prompt


def _mp_video_prompt(
    platform: str,
    seller_note: str | None = None,
    *,
    brand_kit: str | None = None,
    niche: str | None = None,
) -> str:
    platform_name = _MP_PLAT_NAMES.get(platform, platform)
    parts = [
        "Create a short marketplace product video from the provided product photo.",
        f"Marketplace: {platform_name}.",
        "Use the exact product from the photo; preserve its shape, color, material, logo/text, and packaging.",
        "Make clean premium product-card motion: slow camera push-in, subtle parallax, soft studio light, tidy commercial background.",
        "No extra hands, no fake labels, no distorted text, no unrelated objects, no aggressive zoom.",
        "The result should feel ready for a product card or short marketplace listing video.",
    ]
    guidance = _mp_niche_guidance(niche)
    if guidance:
        parts.append(guidance)
    note = (seller_note or "").strip()
    if note:
        parts.append(f"Seller's video direction: {note}.")
    brand = (brand_kit or "").strip()
    if brand:
        parts.append(f"Brand kit / visual rules: {brand}.")
    return " ".join(parts)


def _mp_series_prompt(
    platform: str,
    count: int,
    seller_note: str | None = None,
    brand_kit: str | None = None,
    niche: str | None = None,
) -> str:
    platform_name = _MP_PLAT_NAMES.get(platform, platform)
    count = count if count in _MP_SERIES_COUNTS else 3
    format_label = _mp_platform_format_label(platform)
    platform_guidance = _mp_platform_guidance(platform)
    prompt = (
        f"Создай {count} разных слайдов формата {format_label} для карточки товара на {platform_name}. "
        "Используй загруженное фото как исходный товар, сохрани товар узнаваемым. "
        "Каждый результат должен быть отдельным слайдом одной серии: главный слайд, "
        "выгоды, характеристики, детали применения и доверие/гарантия. "
        "Единый аккуратный стиль, крупный товар, чистая композиция, место под короткий читаемый текст. "
        f"{platform_guidance}"
    )
    guidance = _mp_niche_guidance(niche)
    if guidance:
        prompt += f" Ниша товара: {guidance}"
    note = (seller_note or "").strip()
    if note:
        prompt += f" Уточнение продавца: {note}"
    brand = (brand_kit or "").strip()
    if brand:
        prompt += f" Бренд-кит продавца: {brand}"
    return prompt


def _mp_sku_projects(user_id: int, limit: int = 12) -> list[dict]:
    projects = metrics.list_seller_sku_projects(user_id, limit=limit)
    _ws(user_id)["mp_sku_project_choices"] = [str(p.get("sku") or "") for p in projects]
    return projects


def _mp_sku_projects_text(user_id: int, projects: list[dict] | None = None) -> str:
    projects = _mp_sku_projects(user_id) if projects is None else projects
    if not projects:
        return (
            "📦 <b>Мои товары (SKU)</b>\n\n"
            "Пока здесь пусто. Создай SKU сейчас или добавь результат кнопкой "
            "«➕ В серию SKU» под готовой карточкой."
        )
    lines = ["📦 <b>Мои товары (SKU)</b>\n\nНажми на SKU ниже, чтобы открыть рабочее пространство."]
    for item in projects:
        sku = html.escape(str(item.get("sku") or "SKU"))
        count = int(item.get("items") or 0)
        platform = item.get("platform") or ""
        platform_line = f" · {html.escape(platform)}" if platform else ""
        updated = (item.get("updated_at") or "")[:16]
        update_line = f", обновлено {updated}" if updated else ""
        lines.append(f"• <b>{sku}</b>{platform_line}: {count} {_slides_word(count)}{update_line}")
    lines.append("\nДобавляй текущую или последнюю карточку внутри нужного SKU.")
    return "\n".join(lines)


def _mp_sku_projects_kb(user_id: int, projects: list[dict] | None = None) -> types.InlineKeyboardMarkup:
    B = types.InlineKeyboardButton
    projects = _mp_sku_projects(user_id) if projects is None else projects
    _ws(user_id)["mp_sku_project_choices"] = [str(p.get("sku") or "") for p in projects]
    rows: list[list[types.InlineKeyboardButton]] = []
    for idx, item in enumerate(projects):
        sku = str(item.get("sku") or "SKU")
        count = int(item.get("items") or 0)
        rows.append([B(text=f"📦 {sku[:42]} · {count} {_slides_word(count)}", callback_data=f"mp:sku:open:{idx}")])
    rows.append([B(text="➕ Новый SKU", callback_data="mp:sku:new")])
    rows.append([B(text="◀️ Маркетплейсы", callback_data="m:mp")])
    rows.append([_menu_button("menu", "m:menu")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_sku_projects(message: types.Message, *, user_id: int, edit: bool) -> None:
    projects = _mp_sku_projects(user_id)
    text = _mp_sku_projects_text(user_id, projects)
    kb = _mp_sku_projects_kb(user_id, projects)
    if edit:
        await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        _mp_stamp_message(user_id, message)
    else:
        sent = await message.answer(text, reply_markup=kb, parse_mode="HTML")
        _mp_stamp_message(user_id, sent)


def _mp_sku_choice_kb(user_id: int) -> types.InlineKeyboardMarkup:
    B = types.InlineKeyboardButton
    choices = metrics.recent_seller_skus(user_id, limit=5)
    _ws(user_id)["mp_sku_choices"] = choices
    rows = [
        [B(text=f"📦 {sku[:48]}", callback_data=f"mp:sku:{idx}")]
        for idx, sku in enumerate(choices)
    ]
    rows.append([B(text="➕ Новый SKU / артикул", callback_data="mp:sku:new")])
    rows.append([_menu_button("menu", "m:menu")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _pending_sku_payload(user_id: int) -> dict | None:
    st = _ws(user_id)
    payload = st.get("mp_sku_pending")
    return payload if isinstance(payload, dict) else None


def _latest_sku_payload(user_id: int, *, platform: str | None = None) -> dict | None:
    rows = metrics.get_gallery(user_id, limit=1)
    if not rows:
        return None
    row = rows[0]
    file_id = str(row.get("file_id") or "")
    if not file_id:
        return None
    return {
        "file_id": file_id,
        "token": str(row.get("token") or ""),
        "prompt": str(row.get("prompt") or ""),
        "platform": platform or str(_ws(user_id).get("mp_platform") or ""),
    }


async def _save_sku_payload(message: types.Message, user_id: int, sku: str, payload: dict) -> bool:
    if not payload:
        await message.answer("Кнопка устарела. Нажми «➕ В серию SKU» под нужной картинкой ещё раз.")
        return False
    row_id = metrics.save_seller_sku_item(
        user_id,
        sku,
        file_id=str(payload.get("file_id") or ""),
        token=str(payload.get("token") or ""),
        prompt=str(payload.get("prompt") or ""),
        platform=str(payload.get("platform") or ""),
    )
    if row_id <= 0:
        await message.answer("Не удалось сохранить SKU. Проверь название и попробуй ещё раз.")
        return False
    return True


async def _save_pending_sku_item(message: types.Message, user_id: int, sku: str) -> bool:
    payload = _pending_sku_payload(user_id)
    if not payload:
        _ws(user_id).pop("mp_sku_pending", None)
        await message.answer("Кнопка устарела. Нажми «➕ В серию SKU» под нужной картинкой ещё раз.")
        return False
    if not await _save_sku_payload(message, user_id, sku, payload):
        return False
    st = _ws(user_id)
    st.pop("mp_sku_pending", None)
    st.pop("mp_sku_choices", None)
    st["await"] = None
    metrics.log_event("mp_sku_saved", user_id=user_id, source=str(payload.get("platform") or "seller"))
    await message.answer(
        f"📦 Добавлено в SKU <b>{html.escape(sku.strip())}</b>.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="📦 Мои товары", callback_data="mp:projects")],
            [_menu_button("menu", "m:menu")],
        ]),
        parse_mode="HTML",
    )
    return True


DEFAULT_COUNT = 1
DEFAULT_FMT = "land"
_FMT_NAMES = {"land": "16:9", "port": "9:16", "sq": "1:1", "f43": "4:3", "f34": "3:4"}


def _short_prompt(text: str, limit: int = 80) -> str:
    """Trim a prompt for captions/status lines, adding an ellipsis if cut.

    Avoids the abrupt «…рыгает мотая головой влево вп» cut — instead the user
    sees «…влево вп…» so it's clear the description continues.
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


_QUICK_IDEAS: list[str] = [
    "котик в стиле студии Гибли, мягкий свет",
    "киберпанк Москва ночью, неоновые вывески",
    "акварельный портрет девушки с рыжими кудрями",
    "уютная кофейня осенью, дождь за окном, тёплый свет",
    "астронавт на Марсе, алый закат, одиночество",
    "дракон из кристаллов льда, горы на фоне",
    "магический лес с грибами-фонарями ночью",
    "ретро-автомобиль 60-х, пастельные тона, поп-арт",
    "детёныш лисы в снегу, крупный план, профессиональное фото",
    "японский сад сакуры на рассвете, туман",
    "пиратский корабль в шторм, масло, кино-кадр",
    "город-пузырь под водой, биолюминесценция",
    "девушка читает книгу в библиотеке с высокими потолками",
    "волк воет на луну, силуэт, минимализм",
    "тёплая кухня бабушки с пирогами, солнечный полдень",
    "неоновый самурай в пустом метро",
    "зачарованный замок в облаках, золотой час",
    "фотореализм: капля воды на лепестке розы, макро",
    "медведь-художник рисует пейзаж в берёзовом лесу",
    "будущее: летающие сады над мегаполисом",
    "лиса-шаман у костра в зимнем лесу, северное сияние",
    "стимпанк-дирижабль над облаками, тёплый закатный свет",
    "минималистичный логотип-горы, плоский дизайн, два цвета",
    "котёнок-астронавт в шлеме, смотрит на Землю, мультяшно",
    "уличная еда в Токио ночью, неон, отражения в лужах",
    "девушка-эльф в доспехах из листьев, фэнтези, кинопостер",
    "тёплый плед, какао и книга у окна, за окном снегопад",
    "робот поливает цветы на заброшенной станции, мягкий свет",
    "винтажный мотоцикл на фоне пустыни, золотой час, плёнка",
    "подводный город с медузами-фонарями, бирюзовая дымка",
    "пушистый корги в свитере, студийный портрет, боке",
    "горный замок на рассвете, туман в долине, эпично",
    "капкейк-галактика со звёздной глазурью, макро-съёмка",
    "лес из гигантских грибов, светлячки, сказочная атмосфера",
    "ретро-постер путешествия на Марс, плакат 50-х",
    "кот в деловом костюме пьёт кофе в офисе, юмор, фотореализм",
    "балерина из дыма и света на тёмной сцене, длинная выдержка",
    "домик на дереве с гирляндами в осеннем лесу, уют",
    "феникс из золотых искр взлетает над вулканом, динамично",
]


async def show_edit_confirm(message: types.Message, *, user_id: int, edit: bool):
    """Экран подтверждения правки фото с кнопками «Улучшить запрос» / «Применить»."""
    st = _ws(user_id)
    instr = (st.get("edit_instruction") or "").strip()
    as_gen = bool(st.get("edit_as_gen"))
    if as_gen:
        header = "🎨 <b>Создать изображение</b>"
        tail = "Сгенерировать по фото и запросу — или улучшить запрос (3 варианта)?"
    else:
        header = "✏️ <b>Правка фото</b>"
        tail = "Применить как есть — или улучшить запрос (3 варианта)?"
    text = (
        f"{header}\n\n"
        f"<blockquote>{html.escape(instr[:300])}</blockquote>\n"
        f"{tail}"
    )
    kb = edit_confirm_kb(
        st.get("edit_fmt", DEFAULT_FMT),
        st.get("edit_imodel", DEFAULT_IMAGE_MODEL),
        as_generation=as_gen,
    )
    if edit:
        await _edit_or_answer(message, text, kb, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


def _balance_reply_label(user_id: int | None = None) -> str:
    label = L("kb_balance")
    if user_id is None:
        return label
    try:
        return f"{label} · {credit_store.balance(user_id)}кр"
    except Exception:
        return label


def _is_balance_reply_text(text: str) -> bool:
    label = L("kb_balance")
    return text == label or text.startswith(f"{label} ·")


def reply_menu_kb(user_id: int | None = None) -> types.ReplyKeyboardMarkup:
    """Постоянная клавиатура внизу чата — всегда под рукой."""
    B = types.KeyboardButton
    if _cfg.IS_SELLER:
        # Селлер-бот: минимальная нижняя клавиатура (меню = карточки, баланс).
        return types.ReplyKeyboardMarkup(
            keyboard=[[B(text=L("kb_menu")), B(text=_balance_reply_label(user_id))]],
            resize_keyboard=True,
            is_persistent=True,
        )
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [B(text=L("kb_gen")), B(text=L("kb_vid"))],
            [B(text=L("kb_menu")), B(text=_balance_reply_label(user_id))],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Опиши картинку или жми «🎨 Создать картинку»",
    )


async def _show_help_screen(message: types.Message, *, edit: bool) -> None:
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[_menu_button("menu", "m:menu")]])
    help_text = flow_copy.msg("seller_help" if _cfg.IS_SELLER else "help")
    if edit:
        await message.edit_text(help_text, reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer(help_text, reply_markup=kb, parse_mode="HTML")


def _wizard_text(user_id: int) -> str:
    st = _ws(user_id)
    count = st.get("count", DEFAULT_COUNT)
    fmt = st.get("fmt", DEFAULT_FMT)
    imodel = st.get("imodel", DEFAULT_IMAGE_MODEL)
    total_price = price_gen(count) + image_model_extra(imodel) * count
    settings = flow_copy.msg(
        "wizard_screen",
        count=count,
        fmt=_FMT_NAMES.get(fmt, fmt),
        price=total_price,
        credits=credit_store.balance(user_id),
    )
    # Промпт — в blockquote вверху, чтобы сразу бросался в глаза.
    pending = st.get("pending_prompt")
    if pending:
        prompt_block = flow_copy.msg("wizard_prompt_note", prompt=html.escape(pending))
        return prompt_block + settings
    return settings


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
    """Шаг 2 визарда: настройки (количество/формат/модель) + промпт уже задан."""
    st = _ws(user_id)
    st.setdefault("count", DEFAULT_COUNT)
    st.setdefault("fmt", DEFAULT_FMT)
    st.setdefault("imodel", DEFAULT_IMAGE_MODEL)
    st["step"] = "wizard"
    kb = wizard_kb(st["count"], st["fmt"], st["imodel"], show_improve=True)
    text = _wizard_text(user_id)
    if edit:
        await _edit_or_answer(message, text, kb, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


async def _boost_prompt_with_gemini(prompt: str) -> str | None:
    """Расширить короткий промпт через Gemini Flash. Возвращает улучшенный текст или None."""
    if not GEMINI_API_KEY:
        return None
    system = (
        "You improve image generation prompts. The user gave a short prompt; you expand it "
        "with lighting, composition, style, mood, and artistic detail. "
        "Reply ONLY with the improved prompt. No quotes, no explanation, no intro. "
        "Keep the same language as the input. Length: 1-3 sentences max."
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": system}]},
        "generationConfig": {"maxOutputTokens": 200, "temperature": 0.7},
    }
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    )
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as sess:
            async with sess.post(url, json=payload) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return (
                    data["candidates"][0]["content"]["parts"][0]["text"].strip()
                ) or None
    except Exception as exc:
        log.warning("Gemini prompt boost failed: %s", exc)
        return None


_IDEA_EMOJIS = ["🌺", "🌙", "🎭", "🦋", "🌊", "🎪", "⚡", "🌿", "🔮", "🎯"]


def _prompt_picker_text(ideas: list[str]) -> str:
    """Текст экрана с идеями (шаг 1 визарда).

    Каждая идея обёрнута в <code> (tap-to-copy) и предваряется эмодзи.
    """
    lines = [
        "✨ <b>Что рисуем?</b>\n",
        "Опиши идею текстом или выбери готовый сюжет ниже — "
        "нажми на него, скопируй и отправь 👇\n",
    ]
    # Идеи внутри одной цитаты (blockquote) — выглядит аккуратнее; каждая
    # строка остаётся <code>, то есть копируется по клику.
    quoted = []
    for i, idea in enumerate(ideas[:3]):
        emoji = _IDEA_EMOJIS[i % len(_IDEA_EMOJIS)]
        quoted.append(f"<code>{emoji} {html.escape(idea)}</code>")
    lines.append("<blockquote>" + "\n".join(quoted) + "</blockquote>")
    return "\n".join(lines)


async def show_prompt_picker(message: types.Message, *, user_id: int, edit: bool):
    """Шаг 1 визарда: список идей в тексте + ввод своего промпта."""
    import random as _random
    st = _ws(user_id)
    st.setdefault("count", DEFAULT_COUNT)
    st.setdefault("fmt", DEFAULT_FMT)
    st.setdefault("imodel", DEFAULT_IMAGE_MODEL)
    st["step"] = "prompt_picker"
    # Инициализируем или переиспользуем перемешанный пул идей
    if "ideas_pool" not in st:
        pool = list(_QUICK_IDEAS)
        _random.shuffle(pool)
        st["ideas_pool"] = pool
        st["ideas_offset"] = 0
    offset = st.get("ideas_offset", 0)
    ideas = st["ideas_pool"][offset:offset + 3]
    text = _prompt_picker_text(ideas)
    kb = _prompt_picker_kb(ideas)
    if edit:
        await _edit_or_answer(message, text, kb, parse_mode="HTML")
    else:
        metrics.log_event("wizard_started", user_id=user_id, source="image")
        sent = await message.answer(text, reply_markup=kb, parse_mode="HTML")
        st["picker_msg_id"] = sent.message_id


# ── видео-визард (кнопочный UX, префикс v:) ────────────────────────────
# Состояние живёт в том же wizard_state[user_id], но ключи с префиксом v*,
# чтобы не пересекаться с визардом картинок (step/count/fmt/await/...).

VID_DEFAULT_COUNT = 1
# Frames (старт/финиш-кадр) дефолтится на veo-lite: единственный interpolation-
# ключ, подтверждённый живым захватом (veo_3_1_interpolation_lite). Остальные
# tiers — догадка по паттерну, пока не подтверждены живым прогоном.
VID_FRAMES_DEFAULT_MODEL = "veo-lite"
_VID_FMT_TO_ASPECT = {"land": "landscape", "port": "portrait"}

# Правка ЗАГРУЖЕННОГО пользователем видео временно отключена: сервис нестабильно
# отдаёт результат («Oops, something went wrong!» / видео недогружается) — судя по
# всему, проблема на стороне сервиса. Видео, СГЕНЕРИРОВАННЫЕ в самом сервисе,
# редактируются штатно (кнопка ✏️ под роликом). Весь upload-код сохранён:
# вернуть фичу = поставить True (и обратно проверить через capture). См. HANDOFF.
# Flag now lives in config.settings (Phase 5) and is hot-patched there; readers use
# live _cfg.UPLOAD_VIDEO_EDIT_ENABLED. Re-exported for backward-compatible access.
UPLOAD_VIDEO_EDIT_ENABLED = _cfg.UPLOAD_VIDEO_EDIT_ENABLED

# Payment method toggles — can be hot-patched via admin panel (config_store flags).
# topup_method_kb() reads config_store at call-time so changes survive restarts.
# TOPUP_TEST_PACKS_ENABLED now lives in config.settings; topup readers use _cfg
# live reads. Re-exported for backward-compatible access.
TOPUP_TEST_PACKS_ENABLED = _cfg.TOPUP_TEST_PACKS_ENABLED


def _vid_clear(user_id: int) -> None:
    """Очистить только видео-ключи (сохранив vlast для повтора и vretry для ретрая)."""
    st = wizard_state[user_id]
    keep = {k: st.get(k) for k in ("vlast", "vretry") if k in st}
    for key in list(st):
        if key.startswith("v") and key not in keep:
            st.pop(key, None)
    st.update(keep)


def _clear_image_flow_keys(st: dict) -> None:
    """Снять image-визард (await/step/pending_prompt), не трогая видео-ключи.

    Нужно при входе в видео-из-фото («Оживить фото»): иначе залипший
    ``step=="wizard"`` после прошлой генерации картинок перехватывал промпт из
    чата и генерил картинки вместо видео.
    """
    st["await"] = None
    st["step"] = None
    st.pop("pending_prompt", None)


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


_VID_QUICKSTART_FAMILY = "omni-flash"


# family id (в каталоге) -> короткий код в callback_data и обратно
_VID_FAMILY_CODE = {"omni-flash": "omni", "veo": "veo"}
_VID_CODE_FAMILY = {v: k for k, v in _VID_FAMILY_CODE.items()}


# Продление всегда выполняется моделью veo-lite, но ИСХОДНИК может быть любым
# veo-видео (lite/fast/quality) — оператор подтвердил. Omni продлевать нельзя.


# Варианты модели, доступные в reference-to-video. Ingredients умеет Omni и Veo;
# Frames/interpolation остаётся Veo-only.


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


# ── Новый видео wizard (prompt-first) ───────────────────────────────────
#
# Новый flow: юзер пишет промпт (+ опционально фото) → бот показывает
# настройки. Без фото → Omni Flash; с фото → Veo (качество тоглом).
#

# Соответствие стилей «Подбора по шагам» (guided picker) → стили video wizard.
# Ключи слева — значения из _GUIDED_STEPS["style"]; справа — ключи _VID_STYLES.
_GUIDED_TO_VID_STYLE: dict[str, str] = {
    "anime":     "anime",
    "3d":        "3d",
    "realism":   "photo",
    "cinematic": "cine",
}


def _nwiz_engine(st: dict) -> str:
    """Движок видео: ``omni`` (⚡ Быстро) или ``veo`` (💎 Качество).

    Раньше движок жёстко зависел от наличия фото (фото → Veo, текст → Omni).
    Теперь оба движка работают и с фото, и без — выбор делает сам пользователь
    одной кнопкой, а дефолт — «Быстро» (Omni: дешевле и проще)."""
    eng = (st.get("vengine") or "").lower()
    return eng if eng in ("omni", "veo") else "omni"


def _nwiz_model(st: dict) -> str:
    """Модель для нового wizard на основе выбранного движка (не от наличия фото)."""
    if _nwiz_engine(st) == "veo":
        return _VID_VEO_QUAL_MODEL.get(st.get("vquality", "lite"), "veo-lite")
    return _VID_OMNI_DUR_MODEL.get(st.get("vdur", 4), "omni-flash-4s")


def _nwiz_price(st: dict) -> int:
    mid = _nwiz_model(st)
    vmode = "ingredients" if st.get("vphoto") else "text"
    return video_price(mid, 1, vmode)


def _nwiz_text(user_id: int) -> str:
    st = wizard_state[user_id]
    prompt = st.get("vprompt", "")
    has_photo = bool(st.get("vphoto"))
    vfmt = st.get("vfmt", VID_DEFAULT_FMT)
    dur = st.get("vdur", 4)
    style_key = st.get("vstyle", "")
    style_name = _VID_STYLES.get(style_key, ("Никакой", ""))[0]
    price = _nwiz_price(st)
    credits = credit_store.balance(user_id)

    lines: list[str] = []
    if prompt:
        lines.append(f"<blockquote>{html.escape(prompt[:300])}</blockquote>")
    if has_photo:
        lines.append("📎 <b>Фото (1 шт.) добавлено</b>")
    lines.append("")
    fmt_name = _VID_FMT_NAMES.get(vfmt, vfmt)
    if _nwiz_engine(st) == "veo":
        q_name = _VID_VEO_QUAL_NAMES.get(st.get("vquality", "lite"), "Lite")
        details = f"💎 Качество: {q_name} · Формат: {fmt_name}"
    else:
        details = f"⚡ Быстро · {dur}с · Формат: {fmt_name}"
    if style_key:
        details += f" · Стиль: {style_name}"
    lines.append(details)
    lines.append(f"💰 Стоимость: <b>{price} кр.</b> · Баланс: {credits} кр.")
    return "\n".join(lines)


def _nwiz_kb(user_id: int) -> types.InlineKeyboardMarkup:
    B = types.InlineKeyboardButton
    st = wizard_state[user_id]
    has_photo = bool(st.get("vphoto"))
    vfmt = st.get("vfmt", VID_DEFAULT_FMT)
    dur = st.get("vdur", 4)
    style_key = st.get("vstyle", "")
    style_label = _VID_STYLES.get(style_key, ("Никакой", ""))[0]
    price = _nwiz_price(st)

    engine = _nwiz_engine(st)
    rows: list[list] = []

    # Движок — ⚡ Быстро (Omni) / 💎 Качество (Veo). Жаргон omni/veo скрыт.
    rows.append([
        _sel_btn("⚡ Быстро", engine == "omni", "v:neng:omni"),
        _sel_btn("💎 Качество", engine == "veo", "v:neng:veo"),
    ])

    # Формат тоггл — 🖥 горизонталь / 📱 вертикаль
    next_fmt = "port" if vfmt == "land" else "land"
    fmt_name = _VID_FMT_NAMES.get(vfmt, vfmt)
    fmt_emoji = "📱" if vfmt == "port" else "🖥"
    rows.append([B(text=f"{fmt_emoji} {fmt_name}", callback_data=f"v:nfmt:{next_fmt}")])

    # Вторая ось: «Быстро» (Omni) → длительность; «Качество» (Veo) → уровень.
    if engine == "omni":
        rows.append([_sel_btn(f"{d}с", dur == d, f"v:ndur:{d}") for d in _VID_OMNI_DURATIONS])
    else:
        quality = st.get("vquality", "lite")
        rows.append([
            _sel_btn(_VID_VEO_QUAL_NAMES.get(t, t), quality == t, f"v:nqual:{t}")
            for t in _VID_VEO_QUALITY_CYCLE
        ])

    # Стили
    style_btn = f"🎨 Стиль: {style_label}" if style_key else "🎨 Стили"
    rows.append([B(text=style_btn, callback_data="v:nstyle:screen")])

    # Убрать фото (если есть)
    if has_photo:
        rows.append([B(text="🗑 Убрать фотографию", callback_data="v:nremove_photo")])

    # AI-агент: улучшить промпт (если уже есть описание)
    if (st.get("vprompt") or "").strip():
        rows.append([B(
            text=f"✨ Улучшить промпт · {action_price('prompt_improve')} кр",
            callback_data="ag:vimprove",
        )])

    # Изменить / Создать
    rows.append([
        B(text="✏️ Изменить", callback_data="v:nchange"),
        B(text=f"🎬 Создать · {price} кр", callback_data="v:ngo"),
    ])
    rows.append([B(text=L("cancel"), callback_data="v:cancel")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


async def show_video_prompt_input(
    message: types.Message, *, user_id: int, edit: bool,
    vfmt: str | None = None, vstyle: str | None = None,
):
    """Экран 1 нового video wizard: просим описание видео.

    ``vfmt`` / ``vstyle`` позволяют предзадать формат и стиль (например из
    «Подбора по шагам»). Применяем их ПОСЛЕ _vid_clear, иначе очистка видео-
    ключей сбросила бы их обратно на дефолт (это и был баг: 9:16 из guided
    превращался в 16:9 на экране создания).
    """
    st = wizard_state[user_id]
    _vid_clear(user_id)
    st["vstep"] = "vprompt_input"
    st["vmode"] = "text"
    st.setdefault("vfmt", VID_DEFAULT_FMT)
    st.setdefault("vdur", 4)
    st.setdefault("vquality", "lite")
    st.setdefault("vstyle", "")
    if vfmt:
        st["vfmt"] = vfmt
    if vstyle:
        st["vstyle"] = vstyle
    text = (
        "🎬 <b>Создать видео</b>\n\n"
        "Опишите, что должно происходить в видео. "
        "Можно приложить фото — тогда оживим его в движение 📎"
    )
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=L("cancel"), callback_data="v:cancel")]
    ])
    if edit:
        await _vid_edit(message, text, kb, user_id, parse_mode="HTML")
    else:
        sent = await message.answer(text, reply_markup=kb, parse_mode="HTML")
        st["vmsg_id"] = sent.message_id


def _animate_photo_scenario() -> AnimatePhotoScenario:
    return AnimatePhotoScenario(
        AnimatePhotoConfig(
            default_fmt=VID_DEFAULT_FMT,
            min_price=video_animate_min_price(),
        )
    )


class _TelegramAnimatePhotoContext:
    """Telegram adapter for the platform-independent animate-photo scenario."""

    def __init__(self, message: types.Message, user_id: int):
        self.message = message
        self.user_id = user_id

    @property
    def state(self) -> dict:
        return _ws(self.user_id)

    def clear_pending_edit(self) -> None:
        pending_edits.pop(self.user_id, None)

    def clear_video_flow(self) -> None:
        _vid_clear(self.user_id)

    def clear_image_flow(self) -> None:
        _clear_image_flow_keys(self.state)

    def current_video_model(self) -> str:
        return _nwiz_model(self.state)

    def log_event(self, name: str, *, source: str) -> None:
        metrics.log_event(name, user_id=self.user_id, source=source)

    async def show_photo_input(self, *, edit: bool, price: int):
        text = flow_copy.msg("animate_photo_screen", price=price)
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text=L("cancel"), callback_data="v:cancel")]
        ])
        if edit:
            await _vid_edit(self.message, text, kb, self.user_id, parse_mode="HTML")
        else:
            sent = await self.message.answer(text, reply_markup=kb, parse_mode="HTML")
            self.state["vmsg_id"] = sent.message_id

    async def show_selected_photo_prompt(self):
        text = (
            "рџЋ¬ <b>РћР¶РёРІРёС‚СЊ С„РѕС‚Рѕ</b>\n\n"
            "рџ“Ћ <b>Р¤РѕС‚Рѕ РґРѕР±Р°РІР»РµРЅРѕ.</b> РћРїРёС€РёС‚Рµ, С‡С‚Рѕ РґРѕР»Р¶РЅРѕ РїСЂРѕРёСЃС…РѕРґРёС‚СЊ РІ РІРёРґРµРѕ."
        )
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text=L("cancel"), callback_data="v:cancel")]
        ])
        sent = await self.message.answer(text, reply_markup=kb, parse_mode="HTML")
        self.state["vmsg_id"] = sent.message_id

    async def show_need_photo(self):
        await self.message.answer(flow_copy.msg("animate_photo_need_photo"))

    async def upload_photo_source(self, file_id: str) -> dict | None:
        status_msg = await self.message.answer(flow_copy.msg("uploading_photo"))
        source = await _upload_photo_source_from_file_id(
            self.message,
            user_id=self.user_id,
            status_msg=status_msg,
            file_id=file_id,
        )
        try:
            await status_msg.delete()
        except Exception:
            pass
        return source

    async def show_video_wizard(self, *, edit: bool):
        await show_new_video_wizard(self.message, user_id=self.user_id, edit=edit)

    async def generate_video(self, prompt: str):
        await _video_generate_and_send(self.message, prompt, user_id=self.user_id)


async def show_animate_photo_input(message: types.Message, *, user_id: int, edit: bool):
    """Entry point for "Оживить фото": require a photo, then use the new wizard."""
    await _animate_photo_scenario().start_from_menu(
        _TelegramAnimatePhotoContext(message, user_id),
        edit=edit,
    )


async def show_new_video_wizard(message: types.Message, *, user_id: int, edit: bool):
    """Экран 2 нового video wizard: настройки."""
    st = wizard_state[user_id]
    st["vstep"] = "vnewwiz"
    st["vawait"] = None
    # Синхронизируем vmodel со state
    st["vmodel"] = _nwiz_model(st)
    st["vmode"] = "ingredients" if st.get("vphoto") else "text"
    text = _nwiz_text(user_id)
    kb = _nwiz_kb(user_id)
    if edit:
        await _vid_edit(message, text, kb, user_id, parse_mode="HTML")
    else:
        sent = await message.answer(text, reply_markup=kb, parse_mode="HTML")
        st["vmsg_id"] = sent.message_id


# ── конец нового wizard ──────────────────────────────────────────────────


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


def _robokassa_configured() -> bool:
    return bool(
        ROBOKASSA_ENABLED
        and ROBOKASSA_MERCHANT_LOGIN
        and ROBOKASSA_PASSWORD1
        and ROBOKASSA_PASSWORD2
    )


def _topup_copy(key: str) -> str:
    return flow_copy.msg(key, image_price=_topup_image_price(), video_price=_topup_video_price())


def _zero_balance_kb() -> types.InlineKeyboardMarkup:
    """Клавиатура экрана «кончились кредиты»: прямые кнопки trial-пака + все пакеты."""
    B = types.InlineKeyboardButton
    rows: list[list[types.InlineKeyboardButton]] = []
    try:
        import config_store as _cs
        _flags = _cs.get_section("flags")
        stars_on = bool(_flags.get("stars_pay", STARS_PAYMENT_ENABLED))
        sbp_on   = bool(_flags.get("sbp_pay",   SBP_PAYMENT_ENABLED))
    except Exception:
        stars_on, sbp_on = STARS_PAYMENT_ENABLED, SBP_PAYMENT_ENABLED

    # Предпочтительный способ: сначала СБП (выгоднее), потом Stars
    if sbp_on and _robokassa_configured():
        rows.append([B(text=_robokassa_pack_label("trial"), callback_data="m:robo:trial")])
    if stars_on:
        rows.append([B(text=_stars_pack_label("trial"), callback_data="m:pack:trial")])
    rows.append([_menu_button("topup", "m:topup")])  # Все пакеты
    rows.append([_menu_button("menu", "m:menu")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


async def show_main_menu(
    message: types.Message, *, user_id: int, edit: bool = False, ensure_kb: bool = False
):
    if ensure_kb:
        # Гарантируем постоянную нижнюю клавиатуру (если её сбросили).
        try:
            await message.answer("Меню открыто 👇", reply_markup=reply_menu_kb(user_id))
        except Exception:
            pass
    last = _ws(user_id).get("last")
    credits = credit_store.balance(user_id)
    kb = main_menu_kb(show_repeat=bool(last), credits=credits)
    if _cfg.IS_SELLER:
        text = flow_copy.msg("seller_menu_title")
    else:
        import random as _random
        _variants = flow_copy.MESSAGES.get("menu_title_variants") or [flow_copy.msg("menu_title")]
        text = _random.choice(_variants)
    try:
        if edit:
            await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        else:
            await message.answer(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


async def show_balance(message: types.Message, *, user_id: int, edit: bool = True):
    credits = credit_store.balance(user_id)
    text = flow_copy.msg("balance_screen", credits=credits, price=price_gen(1),
                         vprice=video_price("omni-flash-4s", 1))
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
    account_id: str | None = None,
) -> None:
    """Отправить одну картинку с кнопкой «Редактировать», привязанной к ней."""
    # Запоминаем сырой объект картинки, чтобы бот мог изучить формат правки.
    _keeper_for_acc(account_id).note_image(img)
    token = image_registry.add(
        ImageRef(
            user_id=user_id,
            project_id=project_id,
            source=img,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            account_id=account_id,
            platform=_ws(user_id).get("mp_platform", "") if _cfg.IS_SELLER else "",
        )
    )
    keyboard = _seller_image_keyboard(token) if _cfg.IS_SELLER else _image_keyboard(token)
    try:
        sent = await message.reply_photo(
            photo=url, caption=caption, reply_markup=keyboard, parse_mode="HTML"
        )
        if sent and sent.photo:
            metrics.save_to_gallery(
                user_id, sent.photo[-1].file_id, token=token, prompt=prompt[:400] if prompt else None
            )
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

                    sent2 = await message.reply_photo(
                        photo=BufferedInputFile(data, f"img_{index}.png"),
                        caption=caption,
                        reply_markup=keyboard,
                        parse_mode="HTML",
                    )
                    if sent2 and sent2.photo:
                        metrics.save_to_gallery(
                            user_id, sent2.photo[-1].file_id, token=token,
                            prompt=prompt[:400] if prompt else None,
                        )
    except Exception as e2:
        log.error(f"Повторная ошибка отправки фото {index}/{total}: {e2}")


# ── хэндлеры ──────────────────────────────


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    metrics.upsert_user(user_id, username=_username(message),
                        first_name=getattr(message.from_user, "first_name", None))
    _reset_image_flow(user_id)
    _vid_clear(user_id)
    is_new = not metrics.user_exists(user_id)  # DB надёжнее in-memory _granted после рестарта
    credit_store.balance(user_id)  # начисляем стартовые кредиты при первом старте
    metrics.log_event("user_started", user_id=user_id,
                      username=_username(message), source="command",
                      payload={"is_new": is_new})
    if is_new:
        uname = _username(message)
        uname_str = f"@{uname}" if uname else f"id {user_id}"
        asyncio.create_task(_send_owner_alert(
            f"👤 <b>Новый пользователь</b>\n{uname_str}"
        ))
    # Deep-link приглашение: /start ref_<id> — фиксируем рефералку (один раз).
    parts = (message.text or "").split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""
    _referral_welcome_bonus: int = 0
    if payload.startswith(REFERRAL_PARAM_PREFIX) and not getattr(message.from_user, "is_bot", False):
        raw = payload[len(REFERRAL_PARAM_PREFIX):]
        if raw.isdigit():
            referrer_id = int(raw)
            if referrer_id != user_id and is_new and metrics.record_referral_join(
                referrer_user_id=referrer_id, referred_user_id=user_id
            ):
                # Подарок приглашённому другу — разово, ровно при создании строки
                # реферала (record_referral_join вернул True). Идёт мимо
                # payments-pipeline, поэтому НЕ триггерит награду пригласившему.
                if REFERRAL_REFERRED_BONUS > 0:
                    credit_store.add(user_id, REFERRAL_REFERRED_BONUS)
                    metrics.log_event("referral_referred_bonus", user_id=user_id,
                                      payload={"referrer": referrer_id,
                                               "bonus": REFERRAL_REFERRED_BONUS})
                metrics.log_event("referral_joined", user_id=user_id,
                                  payload={"referrer": referrer_id})
                _referral_welcome_bonus = credit_store.balance(user_id)
    # Рекламный deep-link: /start seed_<канал> — first-touch атрибуция канала.
    channel = parse_channel_seed(payload)
    if channel and not getattr(message.from_user, "is_bot", False):
        if metrics.record_acquisition(user_id=user_id, channel=channel):
            metrics.log_event("acquired_from_channel", user_id=user_id,
                              username=_username(message), payload={"channel": channel})
    # Постоянная нижняя клавиатура всегда показывается при /start
    await message.answer("👇", reply_markup=reply_menu_kb(user_id))

    if _cfg.IS_SELLER:
        # Селлер-бот: сразу показываем выбор задачи, без промежуточного hub-экрана.
        st = _ws(user_id)
        st.setdefault("mp_platform", "wb")
        sent = await message.answer(
            flow_copy.msg("welcome_seller"),
            reply_markup=mp_jobs_kb(st.get("mp_platform", "wb")),
            parse_mode="HTML",
        )
        _mp_stamp_message(user_id, sent)
        return

    if _referral_welcome_bonus > 0:
        # Пришёл по реф-ссылке → эмоциональный бонус + онбординг
        gens = _referral_welcome_bonus // price_gen(1)
        await message.answer(
            flow_copy.msg("referral_welcome", bonus=_referral_welcome_bonus, gens=gens),
            parse_mode="HTML",
        )
        # После реф-приветствия → онбординг
        if is_new:
            await message.answer(
                flow_copy.msg("onboarding_step1"),
                reply_markup=_onboarding_step1_kb(),
            )
            return
    elif is_new:
        # Новый пользователь без реф-ссылки → онбординг
        await message.answer(
            flow_copy.msg("onboarding_step1"),
            reply_markup=_onboarding_step1_kb(),
        )
        return

    # Старый пользователь → обычный welcome + меню
    await message.answer(flow_copy.msg("welcome"), parse_mode="HTML")
    await show_main_menu(message, user_id=user_id)


# /menu, /help, /referral and /balance live in channels/telegram/routers/
# commands.py (Phase 6), wired via dependency injection so the router module
# never imports flow_bot back. Router handlers are matched after dp-level
# ones; commands cannot be shadowed because every dp-level catch-all filter
# (plain text without "/", photo, video/document, successful_payment)
# excludes bot commands.
dp.include_router(
    tg_commands_router.create_router(
        tg_commands_router.CommandsDeps(
            show_main_menu=show_main_menu,
            show_help_screen=_show_help_screen,
            show_referral_screen=_show_referral_screen,
            show_balance=show_balance,
        )
    )
)


@dp.message(Command("ideas"))
async def cmd_ideas(message: types.Message):
    user_id = message.from_user.id
    _reset_image_flow(user_id)
    _vid_clear(user_id)
    await _show_ideas_root(message, user_id=user_id, edit=False)


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer(flow_copy.msg("admin_denied"))
        return

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

    pool_lines = []
    for s in account_pool.status():
        if s["disabled"]:
            icon = "🔒"
        elif s["cooldown_left"]:
            icon = f"❄️{s['cooldown_left']}s"
        else:
            icon = "✅"
        vid_icon = "🎬" if s["video_allowed"] else "🖼"
        pool_lines.append(
            f"  {icon}{vid_icon} <code>{s['id']}</code>: "
            f"img {s['active_image_jobs']}/{s['image_capacity']} "
            f"vid {s['active_video_jobs']}/{s['video_capacity']} "
            f"users={s['users']}"
        )
    pool_section = "\n".join(pool_lines) or "  (нет аккаунтов)"

    # Видео-здоровье по аккаунтам (из video_outcome событий).
    vh_lines = []
    try:
        vh = metrics.report_video_health((1, 24))
        for win in ("1h", "24h"):
            rows = vh.get("windows", {}).get(win, [])
            if not rows:
                continue
            vh_lines.append(f"  <u>{win}</u>:")
            for r in rows:
                sr = r["success_rate"]
                avg = r["avg_attempts_before_200"]
                vh_lines.append(
                    f"    <code>{r['account']}</code>: "
                    f"att={r['video_attempts']} 403={r['video_403']} "
                    f"ok={r['video_success']}(↻{r['video_success_after_retry']}) "
                    f"fail={r['video_final_fail']} "
                    f"avg={avg if avg is not None else '—'} "
                    f"sr={int(sr * 100) if sr is not None else '—'}%"
                )
    except Exception:
        pass
    vh_section = "\n".join(vh_lines) or "  (нет видео-событий)"

    await message.answer(
        f"🔧 <b>Состояние бота</b>\n\n"
        f"Bearer токен: {bearer} (возраст: {age_min} мин)\n"
        f"Project ID:   {project} ({session['project_id'] or '—'})\n"
        f"Cookies:      {cookies} ({len(session['cookies'])} шт)\n"
        f"Капча:        {captcha_line}\n\n"
        f"<b>Пул аккаунтов:</b>\n{pool_section}\n\n"
        f"<b>Видео-здоровье:</b>\n{vh_section}\n",
        parse_mode="HTML",
    )


@dp.message(Command("promo"))
async def cmd_promo(message: types.Message):
    """Ввести промокод: /promo КОД"""
    user_id = message.from_user.id
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(flow_copy.msg("promo_ask"))
        _ws(user_id)["await"] = "promo"
        return
    code = parts[1].strip()
    credits_got = metrics.redeem_promo(code, user_id)
    if credits_got is None:
        await message.answer(flow_copy.msg("promo_invalid"))
        return
    balance = credit_store.add(user_id, credits_got)
    metrics.log_event("promo_redeemed", user_id=user_id, payload={"code": code, "credits": credits_got})
    uname = _username(message)
    uname_str = f"@{uname}" if uname else f"id {user_id}"
    asyncio.create_task(_send_owner_alert(
        f"🎟 <b>Промокод активирован</b>\n"
        f"Юзер: {uname_str}\n"
        f"Код: <code>{code.upper()}</code>  +{credits_got} кр."
    ))
    await message.answer(
        flow_copy.msg("promo_success", credits=credits_got, balance=balance),
        parse_mode="HTML",
    )


@dp.message(Command("addpromo"))
async def cmd_addpromo(message: types.Message):
    """Владелец: создать промокод. /addpromo КОД КРЕДИТЫ [МАКС_ИСПОЛЬЗОВАНИЙ]"""
    if message.from_user.id not in OWNER_IDS:
        await message.answer(flow_copy.msg("admin_denied"))
        return
    parts = (message.text or "").split()
    if len(parts) < 3 or not parts[2].isdigit():
        await message.answer("Использование: /addpromo КОД КРЕДИТЫ [макс_использований]")
        return
    code = parts[1].upper().strip()
    credits = int(parts[2])
    max_uses = int(parts[3]) if len(parts) >= 4 and parts[3].isdigit() else 1
    if credits <= 0 or max_uses <= 0:
        await message.answer("Кредиты и количество использований должны быть > 0.")
        return
    ok = metrics.create_promo_code(code, credits, max_uses, created_by=message.from_user.id)
    if ok:
        await message.answer(
            f"✅ Промокод <code>{code}</code> создан: <b>{credits} кр.</b>, "
            f"использований: {max_uses}.",
            parse_mode="HTML",
        )
    else:
        await message.answer(f"❌ Промокод <code>{code}</code> уже существует.", parse_mode="HTML")


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
        except TelegramForbiddenError:
            metrics.mark_user_blocked(target)
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
    metrics.log_event("credits_refunded", user_id=rec["user_id"], source="admin_refund",
                      payload={"amount": take, "charge_id": rec["charge_id"]})
    # Откатываем реферальные награды, привязанные к этому платежу.
    _clawback_referral_rewards(rec["user_id"], rec["charge_id"])
    log.info(f"↩️ Рефанд {rec['stars']} Stars пользователю {rec['user_id']} (charge {rec['charge_id']})")
    await message.answer(
        f"↩️ Возвращено {rec['stars']} Stars пользователю {rec['user_id']}. "
        f"Списано {take} кр (начислялось {rec['credits']})."
    )
    if target != message.from_user.id:
        try:
            await bot.send_message(
                target, f"↩️ Возврат {rec['stars']} Stars выполнен. Списано {take} кредитов."
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


def _owner_only(message: types.Message) -> bool:
    """Гейт для команд уровня владельца (OWNER_ID в .env), строго ⊆ ADMIN_IDS."""
    return message.from_user.id in OWNER_IDS


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
        f"Выручка: <b>{r['revenue_rub']:.0f}₽</b> ({r['revenue_stars']} Stars)\n"
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
        f"  • {p['package_id']}: {p['count']}× · {p['rub']:.0f}₽ ({p['stars']} Stars)"
        for p in r["by_package"]
    ) or "  —"
    by_day = "\n".join(
        f"  • {d['day']}: {d['rub']:.0f}₽ ({d['count']}×)" for d in r["by_day"][:7]
    ) or "  —"
    await message.answer(
        "💰 <b>Выручка (30 дней)</b>\n"
        f"Всего: <b>{r['revenue_rub']:.0f}₽</b> ({r['revenue_stars']} Stars) · "
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
    # Живое состояние пула (роутинг/health) + статистика jobs за сегодня.
    acc_ids = account_pool.account_ids()
    gcredit_results = await asyncio.gather(
        *[_keeper_for_acc(aid).get_g_credits() for aid in acc_ids],
        return_exceptions=True,
    )
    gcredits_map = {
        aid: (res if isinstance(res, dict) else None)
        for aid, res in zip(acc_ids, gcredit_results)
    }
    pool_lines = []
    for s in account_pool.status():
        state = "⛔ выключен" if s["disabled"] else (
            f"🧊 кулдаун {s['cooldown_left']}с" if s["cooldown_left"] else "✅ активен"
        )
        media_cap = "🎬+🖼" if s.get("video_allowed", True) else "🖼 only"
        gc = gcredits_map.get(s["id"])
        if gc and gc.get("error"):
            gc_str = f" · G {html.escape(str(gc.get('error')))}"
        elif gc:
            paid = "💳" if gc.get("is_paid") else "🆓"
            gc_str = f" · G {gc['credits']}{paid}"
        else:
            gc_str = " · G ?"
        pool_lines.append(
            f"  • <b>{html.escape(s['id'])}</b>: {state} · {media_cap} · 👥{s['users']} · сбоев {s['fails']}{gc_str}"
        )
    text = "🧮 <b>Пул аккаунтов</b>\n" + "\n".join(pool_lines)
    accounts = metrics.report_accounts().get("accounts", [])
    if accounts:
        lines = []
        for a in accounts:
            rem = f" · остаток G {a['credits_remaining']}" if a["credits_remaining"] is not None else ""
            err = f" · ⚠️ {html.escape(str(a['last_error']))}" if a["last_error"] else ""
            lines.append(
                f"  • <b>{html.escape(str(a['account_id']))}</b>: "
                f"{a['jobs']}× · ✅{a['success']}/❌{a['fail']}{rem}{err}"
            )
        text += "\n\n🧮 <b>Задания (сегодня)</b>\n" + "\n".join(lines)
    else:
        text += "\n\nСегодня заданий не было."
    await message.answer(text, parse_mode="HTML")


@dp.message(Command("acc_off"))
async def cmd_acc_off(message: types.Message):
    """Ручное отключение аккаунта пула: /acc_off <id> (admin)."""
    if not _admin_only(message):
        await message.answer(flow_copy.msg("admin_denied"))
        return
    parts = (message.text or "").split()
    acc_id = parts[1] if len(parts) > 1 else ""
    if account_pool.set_disabled(acc_id, True):
        metrics.log_event("account_disabled", user_id=message.from_user.id,
                          payload={"account": acc_id})
        await message.answer(f"⛔ Аккаунт {html.escape(acc_id)} отключён.")
    else:
        await message.answer(
            "Не нашёл такой аккаунт. Известные: " + ", ".join(account_pool.account_ids())
        )


@dp.message(Command("acc_on"))
async def cmd_acc_on(message: types.Message):
    """Включить аккаунт пула обратно: /acc_on <id> (admin)."""
    if not _admin_only(message):
        await message.answer(flow_copy.msg("admin_denied"))
        return
    parts = (message.text or "").split()
    acc_id = parts[1] if len(parts) > 1 else ""
    if account_pool.set_disabled(acc_id, False):
        metrics.log_event("account_enabled", user_id=message.from_user.id,
                          payload={"account": acc_id})
        await message.answer(f"✅ Аккаунт {html.escape(acc_id)} включён.")
    else:
        await message.answer(
            "Не нашёл такой аккаунт. Известные: " + ", ".join(account_pool.account_ids())
        )


@dp.message(Command("acc_vid_off"))
async def cmd_acc_vid_off(message: types.Message):
    """Запретить видео на аккаунте пула: /acc_vid_off <id> (admin)."""
    if not _admin_only(message):
        await message.answer(flow_copy.msg("admin_denied"))
        return
    parts = (message.text or "").split()
    acc_id = parts[1] if len(parts) > 1 else ""
    if account_pool.set_video_allowed(acc_id, False):
        metrics.log_event("account_video_disabled", user_id=message.from_user.id,
                          payload={"account": acc_id})
        await message.answer(
            f"🖼 Аккаунт <b>{html.escape(acc_id)}</b>: только картинки. "
            f"Видео-запросы пойдут на другой аккаунт.",
            parse_mode="HTML",
        )
    else:
        await message.answer(
            "Не нашёл такой аккаунт. Известные: " + ", ".join(account_pool.account_ids())
        )


@dp.message(Command("acc_vid_on"))
async def cmd_acc_vid_on(message: types.Message):
    """Вернуть видео на аккаунт пула: /acc_vid_on <id> (admin)."""
    if not _admin_only(message):
        await message.answer(flow_copy.msg("admin_denied"))
        return
    parts = (message.text or "").split()
    acc_id = parts[1] if len(parts) > 1 else ""
    if account_pool.set_video_allowed(acc_id, True):
        metrics.log_event("account_video_enabled", user_id=message.from_user.id,
                          payload={"account": acc_id})
        await message.answer(
            f"🎬 Аккаунт <b>{html.escape(acc_id)}</b>: видео снова разрешено.",
            parse_mode="HTML",
        )
    else:
        await message.answer(
            "Не нашёл такой аккаунт. Известные: " + ", ".join(account_pool.account_ids())
        )


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


@dp.message(Command("admin_channels"))
async def cmd_admin_channels(message: types.Message):
    """Рекламные каналы: привлечено / платящие / выручка по каждой deep-link.

    Без аргумента — сводка по всем каналам. С аргументом
    (`/admin_channels <ярлык>`) — готовая ссылка для этого канала.
    """
    if not _admin_only(message):
        await message.answer(flow_copy.msg("admin_denied"))
        return
    parts = (message.text or "").split()
    if len(parts) > 1:
        slug = parse_channel_seed(CHANNEL_PARAM_PREFIX + parts[1])
        if not slug:
            await message.answer(
                "Ярлык канала: латиница/цифры/_/-, до 32 символов. "
                "Пример: <code>/admin_channels my_channel</code>",
                parse_mode="HTML",
            )
            return
        username = BOT_USERNAME or "&lt;bot&gt;"
        link = f"https://t.me/{username}?start={CHANNEL_PARAM_PREFIX}{slug}"
        await message.answer(
            f"🔗 Ссылка для канала <b>{html.escape(slug)}</b>:\n<code>{html.escape(link)}</code>",
            parse_mode="HTML",
        )
        return
    r = metrics.report_channels()
    if not r["channels"]:
        await message.answer(
            "📡 <b>Каналы</b>\nПока никто не пришёл по рекламным ссылкам.\n"
            "Ссылка для канала: <code>/admin_channels &lt;ярлык&gt;</code>",
            parse_mode="HTML",
        )
        return
    lines = []
    for c in r["channels"]:
        rub = f" · {c['revenue_rub']:.0f}₽" if c["revenue_rub"] else ""
        lines.append(
            f"  • <b>{html.escape(str(c['channel']))}</b>: 👥{c['users']} · "
            f"💳{c['paid_users']} · Stars {c['revenue_stars']}{rub}"
        )
    await message.answer(
        f"📡 <b>Каналы</b> (привлечено всего: <b>{r['total_acquired']}</b>)\n"
        + "\n".join(lines)
        + "\n\nСсылка для канала: <code>/admin_channels &lt;ярлык&gt;</code>",
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


@dp.message(Command("admin_cohort"))
async def cmd_admin_cohort(message: types.Message):
    """D1/D7/D30 retention cohorts (последние 7 когорт на каждый период)."""
    if not _admin_only(message):
        await message.answer(flow_copy.msg("admin_denied"))
        return
    rows = metrics.report_cohort_retention()
    if not rows:
        await message.answer("📊 Недостаточно данных для когортного анализа.")
        return

    # Group by period, average across cohorts.
    from collections import defaultdict
    by_period: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_period[r["period"]].append(r)

    lines = ["📊 <b>Retention когорты</b>"]
    for p in sorted(by_period):
        cohorts = by_period[p]
        total_size = sum(c["cohort_size"] for c in cohorts)
        total_ret = sum(c["retained"] for c in cohorts)
        avg_rate = (total_ret / total_size * 100) if total_size else 0.0
        lines.append(
            f"\n<b>D{p}</b>  (когорт: {len(cohorts)}, "
            f"всего юзеров: {total_size}, удержано: {total_ret}, "
            f"avg {avg_rate:.1f}%)"
        )
        for c in cohorts:
            pct = f"{c['rate']*100:.1f}%" if c["cohort_size"] else "—"
            lines.append(
                f"  {c['cohort_date']}  {c['retained']}/{c['cohort_size']}  {pct}"
            )

    await message.answer("\n".join(lines), parse_mode="HTML")


# Справочник команд для /admin_help. Угловые скобки экранированы (HTML parse_mode).
# Держим единым местом, чтобы при добавлении команды правка была одна.
_HELP_SECTIONS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("👤 Пользовательские", (
        ("/start", "запуск и главное меню"),
        ("/menu", "главное меню"),
        ("/balance", "баланс и пополнение через Stars"),
        ("/promo &lt;код&gt;", "активировать промокод"),
        ("/img &lt;промпт&gt;", "4 картинки по тексту"),
        ("/one &lt;промпт&gt;", "1 картинка"),
        ("/portrait &lt;промпт&gt;", "2 вертикальные картинки"),
        ("/square &lt;промпт&gt;", "2 квадратные картинки"),
        ("/imgn N &lt;промпт&gt;", "N картинок (1–8)"),
        ("/mix &lt;промпт&gt;", "собрать картинку из выбранных «ингредиентов»"),
    )),
    ("🛡 Админские (ADMIN_IDS)", (
        ("/grant &lt;user_id&gt; &lt;кредиты&gt;", "начислить пользователю кредиты"),
        ("/status", "состояние сессии (токен / проект / капча)"),
        ("/refund &lt;user_id&gt; [charge_id]", "вернуть Stars за платёж (по умолчанию последний)"),
        ("/admin_today", "сводка за сегодня (юзеры/выручка/успехи)"),
        ("/admin_revenue", "выручка за 30 дней (пакеты, по дням)"),
        ("/admin_flow", "нагрузка по моделям и бэкенду"),
        ("/admin_accounts", "состояние пула аккаунтов + задания за сегодня"),
        ("/acc_off &lt;id&gt;", "вручную отключить аккаунт пула"),
        ("/acc_on &lt;id&gt;", "вернуть аккаунт пула в работу"),
        ("/acc_vid_off &lt;id&gt;", "только картинки на аккаунте (видео → другой акк)"),
        ("/acc_vid_on &lt;id&gt;", "вернуть видео на аккаунт"),
        ("/admin_refs", "рефералы: приглашения/награды/топ"),
        ("/admin_channels [ярлык]", "каналы (атрибуция); с ярлыком — выдать ссылку"),
        ("/admin_errors", "ошибки бэкенда за 7 дней"),
        ("/admin_cohort", "D1/D7/D30 retention когорты"),
    )),
    ("👑 Владелец (OWNER_ID)", (
        ("/admin_help", "этот справочник команд"),
        ("/addpromo &lt;код&gt; &lt;кредиты&gt; [N]", "создать промокод (N использований, по умолч. 1)"),
    )),
)


def _render_admin_help() -> str:
    blocks = ["🧭 <b>Команды бота</b>"]
    for title, rows in _HELP_SECTIONS:
        lines = "\n".join(f"  <code>{cmd}</code> — {desc}" for cmd, desc in rows)
        blocks.append(f"<b>{title}</b>\n{lines}")
    return "\n\n".join(blocks)


@dp.message(Command("admin_help"))
async def cmd_admin_help(message: types.Message):
    """Справочник команд (синтаксис + описание). Только для OWNER_ID из .env."""
    if not _owner_only(message):
        await message.answer(flow_copy.msg("admin_denied"))
        return
    await message.answer(_render_admin_help(), parse_mode="HTML")


# Метрики: действие → имя события запроса / тип операции для flow_jobs.
_IMG_REQUEST_EVENT = {
    "gen": "image_requested", "regen": "image_requested",
    "revary": "variations_requested",
    "up2x": "upscale_requested",
    "edit": "image_edit_requested", "myphoto": "image_edit_requested",
    "mp_series": "image_edit_requested",
}
_IMG_OP = {
    "gen": "image", "regen": "image", "revary": "variations",
    "up2x": "enhance", "edit": "edit", "myphoto": "edit",
    "mp_series": "mp_series",
}


# ── Shared generation backend (SELLER_BOT_PLAN.md §A) ────────────────────
_backend_client_cache: "object | None" = None


def _backend_client():
    """Seller-side client to the consumer's /internal/generate (or None)."""
    global _backend_client_cache
    if _backend_client_cache is None:
        try:
            import seller_backend
            _backend_client_cache = seller_backend.BackendClient.from_env()
        except Exception:
            log.exception("backend client init failed")
            _backend_client_cache = None
    return _backend_client_cache


def _backend_generation_deps():
    """Runtime dependencies for generation.backend_service.

    Kept here because the consumer process still owns the Flow account pool,
    keepers, clients, and capture files. The service itself stays Telegram-free.
    """
    return SimpleNamespace(
        default_image_model=DEFAULT_IMAGE_MODEL,
        edit_capture_file=EDIT_CAPTURE_FILE,
        vid_ref_default_model=VID_REF_DEFAULT_MODEL,
        account_pool=account_pool,
        account_for_image=_account_for_image,
        account_for_video=_account_for_video,
        ensure_user_project=ensure_user_project,
        client_for_acc=_client_for_acc,
        keeper_for_acc=_keeper_for_acc,
        mark_image_account_failure=_mark_image_account_failure,
        mark_video_account_failure=_mark_video_account_failure,
        build_image_inputs=build_image_inputs,
        load_edit_capture=load_edit_capture,
        result_pairs=result_pairs,
        video_model_meta=video_model_meta,
        log=log,
    )


async def _backend_generate_images(req: dict) -> dict:
    """Compatibility wrapper for the internal text-to-image backend."""
    return await backend_service.generate_images(_backend_generation_deps(), req)


async def _backend_generate_i2i(req: dict) -> dict:
    """Compatibility wrapper for the internal image-to-image backend."""
    return await backend_service.generate_i2i(_backend_generation_deps(), req)


async def _backend_generate_video_ingredients(req: dict) -> dict:
    """Compatibility wrapper for the internal photo-to-video backend."""
    return await backend_service.generate_video_ingredients(_backend_generation_deps(), req)


async def _backend_generate(req: dict) -> dict:
    """Internal endpoint dispatcher by ``kind`` (image | i2i | video_ingredients)."""
    if req.get("kind") == "i2i":
        return await _backend_generate_i2i(req)
    if req.get("kind") == "video_ingredients":
        return await _backend_generate_video_ingredients(req)
    return await _backend_generate_images(req)


async def _seller_backend_call_and_send(
    message: types.Message, prompt: str, *, num_images: int, aspect_ratio: str,
    user_id: int, image_model: str, kind: str = "image", image_b64: str | None = None,
) -> tuple[bool, str | None]:
    """Seller-side: ask the consumer backend to generate, then send the URLs.

    Returns ``(sent_any, backend_account_id)`` — the account_id is the REAL
    consumer-backend account that did the work (for ``<acc>-sell`` event tagging)."""
    client = _backend_client()
    if client is None:
        await message.answer(flow_copy.msg("accounts_unavailable"))
        return False, None
    status_msg = await message.answer(flow_copy.msg("generating"))
    data = await client.generate(
        prompt=prompt, num_images=num_images, aspect_ratio=aspect_ratio,
        image_model=image_model, user_id=user_id, kind=kind, image_b64=image_b64,
    )
    images = data.get("images") or []
    if data.get("error") or not images:
        # Понятный финальный статус вместо технической ошибки. Кредиты вернёт
        # credit_gate (charge.ok остаётся False) — поэтому прямо говорим об этом.
        raw = str(data.get("error") or "").lower()
        if any(k in raw for k in ("rate", "limit", "429", "quota", "перегруж",
                                   "busy", "unavailable", "account", "капч", "captcha", "403")):
            friendly = "⏳ Сервис сейчас перегружен. Кредиты возвращены — попробуй ещё раз через пару минут 🙏"
        elif raw:
            friendly = "❌ Не получилось создать карточку. Кредиты возвращены — попробуй ещё раз или измени фото/описание."
        else:
            friendly = "❌ Карточка не получилась. Кредиты возвращены — попробуй ещё раз 🙏"
        try:
            await status_msg.edit_text(friendly)
        except Exception:
            pass
        return False, data.get("account_id")
    try:
        await status_msg.delete()
    except Exception:
        pass
    sent_any = False
    for index, im in enumerate(images, 1):
        url = im.get("url")
        if not url:
            continue
        try:
            await _send_one_image(
                message, url=url, img=im.get("img") or {"url": url},
                index=index, total=len(images), caption="", user_id=user_id,
                project_id=data.get("project_id"), prompt=prompt,
                aspect_ratio=aspect_ratio, account_id=data.get("account_id"),
            )
            sent_any = True
        except Exception:
            log.exception("seller backend send image failed")
    return sent_any, data.get("account_id")


async def _seller_generate_and_send(
    message: types.Message, prompt: str, *, num_images: int, aspect_ratio: str,
    user_id: int, action: str = "gen", image_model: str = DEFAULT_IMAGE_MODEL,
    kind: str = "image", image_b64: str | None = None,
) -> bool:
    """Seller generation: charge the seller wallet, generate via backend, send.

    ``kind="i2i"`` runs image-to-image on ``image_b64`` (the user's photo).
    """
    if _backend_client() is None:
        await message.answer(flow_copy.msg("accounts_unavailable"))
        return False
    metrics.log_event(
        _IMG_REQUEST_EVENT.get(action, "image_requested"), user_id=user_id,
        username=_username(message), source=action,
        payload={"count": num_images, "model": image_model, "surface": "seller", "kind": kind},
    )
    surcharge = image_model_extra(image_model) * max(1, num_images)
    started = time.monotonic()
    ok = False
    backend_acc = None
    try:
        async with user_slot(user_id, message):
            async with credit_gate(user_id, action, message, num_images, surcharge=surcharge) as charge:
                ok, backend_acc = await _seller_backend_call_and_send(
                    message, prompt, num_images=num_images, aspect_ratio=aspect_ratio,
                    user_id=user_id, image_model=image_model, kind=kind, image_b64=image_b64,
                )
                charge.ok = ok
    except RateLimited:
        _log_image_job(user_id, action, image_model, started, ok=False, error="user_busy",
                       account_id=_seller_acc_tag(backend_acc))
        return False
    except NotEnoughCredits:
        metrics.log_event("image_failed", user_id=user_id, source=action,
                          payload={"reason": "insufficient_credits"})
        return False
    charged = (action_price(action, num_images) + surcharge) if ok else 0
    metrics.log_event("image_success" if ok else "image_failed", user_id=user_id, source=action)
    if ok:
        metrics.log_event("credits_charged", user_id=user_id, source=action,
                          payload={"amount": charged, "action": action})
    _log_image_job(user_id, action, image_model, started, ok=ok, charged=charged,
                   account_id=_seller_acc_tag(backend_acc))
    if ok:
        await _post_generation_referral_hooks(message, user_id)
        await _maybe_brandkit_nudge(message, user_id)
    return ok


async def _maybe_brandkit_nudge(message: types.Message, user_id: int) -> None:
    """После первой удачной seller-генерации (один раз) предлагаем заполнить
    бренд-кит — чтобы карточки были в едином стиле магазина."""
    if not _cfg.IS_SELLER:
        return
    try:
        if _mp_brand_kit(user_id):
            return  # бренд-кит уже задан
        if metrics.has_user_event(user_id, "brandkit_nudge_shown"):
            return  # нудж уже показывали
        metrics.log_event("brandkit_nudge_shown", user_id=user_id, source="seller")
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🎨 Заполнить бренд-кит", callback_data="mp:brandkit")],
            [_menu_button("menu", "m:menu")],
        ])
        await message.answer(
            "🎉 <b>Поздравляем с первой карточкой!</b>\n\n"
            "Чтобы усилить качество и держать единый стиль магазина (цвета, тон, "
            "что показывать), заполни <b>бренд-кит</b> — я буду учитывать его в "
            "каждой карточке и серии.",
            reply_markup=kb, parse_mode="HTML",
        )
    except Exception:
        log.warning("brandkit nudge failed", exc_info=True)


async def _seller_i2i_from_file_id(
    message: types.Message, file_id: str, instruction: str, *, num_images: int,
    aspect_ratio: str, user_id: int, action: str,
) -> bool:
    """Seller marketplace photo job from a stored Telegram file_id: download, i2i."""
    try:
        buf = await bot.download(file_id)
        data = buf.read() if hasattr(buf, "read") else bytes(buf)
    except Exception:
        log.exception("seller photo download failed")
        await message.answer(flow_copy.msg("upload_failed"))
        return False
    image_b64 = base64.b64encode(data).decode("ascii")
    return await _seller_generate_and_send(
        message, instruction, num_images=num_images, aspect_ratio=aspect_ratio,
        user_id=user_id, action=action, kind="i2i", image_b64=image_b64,
    )


async def _seller_i2i_from_photo(
    message: types.Message, instruction: str, *, num_images: int, aspect_ratio: str,
    user_id: int, action: str,
) -> bool:
    """Seller marketplace photo job: download the product photo, run i2i via backend."""
    return await _seller_i2i_from_file_id(
        message, message.photo[-1].file_id, instruction,
        num_images=num_images, aspect_ratio=aspect_ratio, user_id=user_id, action=action,
    )


async def _seller_video_backend_call_and_send(
    message: types.Message, prompt: str, *, image_b64: str, aspect_ratio: str,
    user_id: int, video_model: str = VID_REF_DEFAULT_MODEL,
) -> bool:
    """Seller-side marketplace video: ask consumer backend, send returned mp4."""
    client = _backend_client()
    if client is None:
        await message.answer(flow_copy.msg("accounts_unavailable"))
        return False
    status_msg = await message.answer(flow_copy.msg("vid_working"))
    data = await client.generate(
        prompt=prompt,
        num_images=1,
        aspect_ratio=aspect_ratio,
        image_model=DEFAULT_IMAGE_MODEL,
        video_model=video_model,
        user_id=user_id,
        kind="video_ingredients",
        image_b64=image_b64,
    )
    videos = data.get("videos") or []
    if data.get("error") or not videos:
        err = str(data.get("error") or flow_copy.msg("vid_gen_failed"))[:300]
        try:
            await status_msg.edit_text(f"{flow_copy.msg('vid_gen_failed')}\n{html.escape(err)}")
        except Exception:
            pass
        return False

    from aiogram.types import BufferedInputFile

    sent_any = False
    for index, item in enumerate(videos, 1):
        raw = item.get("video_b64")
        if not raw:
            continue
        try:
            video_bytes = base64.b64decode(raw)
        except Exception:
            log.exception("seller backend video base64 decode failed")
            continue
        filename = f"marketplace_video_{index}.mp4"
        caption = flow_copy.msg(
            "vid_result_caption", i=index, n=len(videos),
            prompt=html.escape(_short_prompt(prompt, 60)),
        )
        try:
            await message.answer_video(
                BufferedInputFile(video_bytes, filename),
                caption=caption,
                reply_markup=_mp_back_kb(),
                parse_mode="HTML",
            )
            sent_any = True
        except Exception:
            log.exception("seller answer_video failed, falling back to document")
            try:
                await message.answer_document(
                    BufferedInputFile(video_bytes, filename),
                    caption=caption,
                    reply_markup=_mp_back_kb(),
                    parse_mode="HTML",
                )
                sent_any = True
            except Exception:
                log.exception("seller answer_document video fallback failed")

    try:
        if sent_any:
            await status_msg.delete()
        else:
            await status_msg.edit_text(flow_copy.msg("vid_gen_failed"))
    except Exception:
        pass
    return sent_any


async def _seller_video_generate_and_send(
    message: types.Message, prompt: str, *, image_b64: str, aspect_ratio: str,
    user_id: int, video_model: str = VID_REF_DEFAULT_MODEL,
) -> bool:
    """Charge seller credits, generate marketplace video via consumer backend."""
    if _backend_client() is None:
        await message.answer(flow_copy.msg("accounts_unavailable"))
        return False
    if not prompt or len(prompt.strip()) < 3:
        await message.answer(flow_copy.msg("vid_prompt_too_short"))
        return False
    price = video_price(video_model, 1, "ingredients")
    started = time.monotonic()
    charged = False
    ok = False
    try:
        async with user_slot(user_id, message):
            have = credit_store.balance(user_id)
            if have < price:
                if have == 0:
                    await message.answer(
                        flow_copy.msg("zero_balance"),
                        reply_markup=_zero_balance_kb(),
                        parse_mode="HTML",
                    )
                else:
                    kb = types.InlineKeyboardMarkup(inline_keyboard=[
                        [_menu_button("topup", "m:topup")], [_menu_button("menu", "m:menu")]
                    ])
                    await message.answer(
                        flow_copy.msg("low_balance", needed=price, have=have),
                        reply_markup=kb,
                        parse_mode="HTML",
                    )
                return False
            credit_store.charge(user_id, price)
            charged = True
            metrics.log_event(
                "video_requested", user_id=user_id, username=_username(message),
                source="mp_animate",
                payload={"model": video_model, "count": 1, "mode": "ingredients", "surface": "seller"},
            )
            ok = await _seller_video_backend_call_and_send(
                message, prompt, image_b64=image_b64, aspect_ratio=aspect_ratio,
                user_id=user_id, video_model=video_model,
            )
    except RateLimited:
        return False
    except Exception:
        log.exception("seller video backend generation failed")
        ok = False
    finally:
        if charged and not ok:
            credit_store.refund(user_id, price)

    metrics.log_event("video_success" if ok else "video_failed", user_id=user_id, source="mp_animate")
    if ok:
        metrics.log_event(
            "credits_charged", user_id=user_id, source="mp_animate",
            payload={"amount": price, "action": "video_mp_animate"},
        )
    elif charged:
        metrics.log_event(
            "credits_refunded", user_id=user_id, source="mp_animate",
            payload={"amount": price},
        )
    metrics.log_flow_job(
        user_id=user_id,
        account_id="consumer-backend",
        operation_type="video_mp_animate",
        model=video_model,
        bot_credits_charged=price if ok else 0,
        refund_amount=0 if ok else price if charged else 0,
        duration_ms=_ms_since(started),
        status="success" if ok else "fail",
        error_type=None if ok else "backend_failed",
    )
    if ok:
        await _post_generation_referral_hooks(message, user_id)
    return ok


async def _seller_video_from_photo(
    message: types.Message, prompt: str, *, aspect_ratio: str, user_id: int,
    video_model: str = VID_REF_DEFAULT_MODEL,
) -> bool:
    """Seller marketplace animate job: download product photo, run video backend."""
    try:
        photo = message.photo[-1]
        buf = await bot.download(photo.file_id)
        data = buf.read() if hasattr(buf, "read") else bytes(buf)
    except Exception:
        log.exception("seller video photo download failed")
        await message.answer(flow_copy.msg("upload_failed"))
        return False
    image_b64 = base64.b64encode(data).decode("ascii")
    return await _seller_video_generate_and_send(
        message, prompt, image_b64=image_b64, aspect_ratio=aspect_ratio,
        user_id=user_id, video_model=video_model,
    )


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
    # Снимок для кнопки «Повторить запрос» при ошибке (action нужен credit_gate).
    _ws(user_id)["img_retry"] = {
        "prompt": prompt, "num_images": num_images,
        "aspect_ratio": aspect_ratio, "image_model": image_model, "action": action,
    }

    # Селлер-бот не держит свой пул — генерация идёт в основной процесс через
    # общий backend (SELLER_BOT_PLAN.md §A). Кредиты списываются с seller-кошелька.
    if _cfg.IS_SELLER:
        await _seller_generate_and_send(
            message, prompt, num_images=num_images, aspect_ratio=aspect_ratio,
            user_id=user_id, action=action, image_model=image_model,
        )
        return

    # Весь пул аккаунтов недоступен — отказ ДО credit_gate (ничего не списываем,
    # без цикла «списали-вернули»). Внутри _do_generate_and_send есть та же
    # проверка (защита остальных входов), но здесь она до денег.
    if _account_for_image(user_id) is None:
        await message.answer(flow_copy.msg("accounts_unavailable"))
        return

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
        _log_image_job(user_id, action, image_model, started, ok=False, error="user_busy")
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
            metrics.save_prompt_history(user_id, prompt)
    _log_image_job(user_id, action, image_model, started, ok=ok, charged=charged)


def _ms_since(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _seller_acc_tag(backend_acc: str | None) -> str | None:
    """Tag a seller job with the real consumer-backend account, e.g. ``sub5-sell``."""
    acc = (backend_acc or "").strip()
    return f"{acc}-sell" if acc else None


def _log_image_job(user_id, action, image_model, started, *, ok, charged=0, error=None,
                   account_id: str | None = None):
    """flow_jobs-запись для картиночной операции (никогда не бросает).

    ``account_id`` — явный аккаунт (для seller передаём ``<acc>-sell``); иначе
    берём назначенный пользователю аккаунт из пула."""
    metrics.log_flow_job(
        user_id=user_id,
        account_id=account_id or account_pool.assigned_to(user_id) or FLOW_ACCOUNT_ID,
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
            await status_msg.edit_text(f"{text}\n📝 {_short_prompt(prompt, 80)}")
        except Exception:
            pass

    # Фоновая анимация статусных фраз: меняется каждые 2.5 сек пока идёт генерация.
    _img_phrases = flow_copy.MESSAGES.get("img_status_phrases") or []
    _img_anim_stop = asyncio.Event()

    async def _img_animate():
        await asyncio.sleep(2.5)
        for phrase in itertools.cycle(_img_phrases):
            if _img_anim_stop.is_set():
                return
            try:
                await status_msg.edit_text(f"{phrase}\n📝 {_short_prompt(prompt, 80)}")
            except Exception:
                pass
            await asyncio.sleep(2.5)
            if _img_anim_stop.is_set():
                return

    _img_anim_task = asyncio.create_task(_img_animate()) if _img_phrases else None

    # Генерация с тихим фейловером: попытка 0 — основной аккаунт, попытка 1 — другой.
    # 400 (prompt_rejected) = проблема юзера, не аккаунта — фейловер и кулдаун не нужны.
    # asyncio.wait_for(timeout=70) на каждую попытку: не ждём 120с зависший аккаунт —
    # фейловер стартует сразу, суммарное ожидание ≤ 140с вместо ≤ 240с.
    _GEN_ATTEMPT_TIMEOUT = 70  # секунд на одну попытку
    tried: set[str] = set()
    acc_id: str | None = None
    project_id: str | None = None
    result: dict = {}
    _uname = _username(message)

    try:
        for attempt in range(2):
            acc_id = _account_for_image(user_id, exclude=tried if tried else None)
            if acc_id is None:
                await status_msg.edit_text(flow_copy.msg("accounts_unavailable"))
                return False
            tried.add(acc_id)
            project_id = await ensure_user_project(user_id, account_id=acc_id)

            def _log_failover(from_acc: str, reason: str) -> None:
                metrics.log_event(
                    "gen_failover", user_id=user_id, username=_uname,
                    payload={"from_account": from_acc, "reason": reason[:120]},
                )

            # Notify user if this account is already at capacity — they'll wait in queue.
            if not account_pool.has_image_capacity(acc_id):
                try:
                    await status_msg.edit_text(flow_copy.msg("high_load"))
                except Exception:
                    pass

            async with account_pool.image_slot(acc_id):
                # Slot acquired — credits already reserved by credit_gate above.
                try:
                    result = await asyncio.wait_for(
                        _client_for_acc(acc_id).generate_images(
                            prompt,
                            aspect_ratio=aspect_ratio,
                            num_images=num_images,
                            progress_cb=update_status,
                            project_id=project_id,
                            image_model=image_model,
                        ),
                        timeout=_GEN_ATTEMPT_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    log.warning("Generation timed out after %ds (account %s, attempt %d)",
                                _GEN_ATTEMPT_TIMEOUT, acc_id, attempt)
                    if account_pool.mark_failure(acc_id):
                        _fire_owner_alert(
                            f"⚠️ <b>Аккаунт кулдаун</b>\n"
                            f"Аккаунт: <code>{acc_id}</code>\n"
                            f"Причина: timeout ({_GEN_ATTEMPT_TIMEOUT}s, image)"
                        )
                    if attempt == 0:
                        _log_failover(acc_id, "timeout")
                        continue  # releases image_slot, then picks next account
                    await status_msg.edit_text(flow_copy.msg("gen_failed"), reply_markup=_img_retry_kb())
                    return False
                except Exception:
                    log.exception("Generation failed (account %s, attempt %d)", acc_id, attempt)
                    if account_pool.mark_failure(acc_id):
                        _fire_owner_alert(
                            f"⚠️ <b>Аккаунт кулдаун</b>\n"
                            f"Аккаунт: <code>{acc_id}</code>\n"
                            f"Причина: exception (image)"
                        )
                    if attempt == 0:
                        _log_failover(acc_id, "exception")
                        continue  # releases image_slot, then picks next account
                    await status_msg.edit_text(flow_copy.msg("gen_failed"), reply_markup=_img_retry_kb())
                    return False

                if "error" in result:
                    if result.get("error_type") == "prompt_rejected":
                        # 400 — проблема запроса, не аккаунта: не трогаем health, не фейловеримся
                        await status_msg.edit_text(
                            f"❌ {html.escape(str(result['error'])[:300])}",
                            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                                [_menu_button("menu", "m:menu")],
                            ]),
                        )
                        return False
                    # Ошибка аккаунта — помечаем и пробуем другой (один раз)
                    _mark_image_account_failure(acc_id, result)
                    if attempt == 0:
                        reason = str(result.get("error", ""))[:80]
                        log.info("Тихий фейловер после ошибки аккаунта %s: %s", acc_id, reason)
                        _log_failover(acc_id, reason)
                        continue  # releases image_slot, then picks next account
                    # Оба аккаунта не справились
                    await status_msg.edit_text(
                        f"❌ {html.escape(str(result['error'])[:300])}",
                        reply_markup=_img_retry_kb(),
                    )
                    return False

            break  # успех (image_slot released by exiting async with)
        else:
            await status_msg.edit_text(flow_copy.msg("gen_failed"), reply_markup=_img_retry_kb())
            return False

        pairs = result_pairs(result)

        if not pairs:
            log.warning(f"Пустой ответ: {str(result)[:500]}")
            await status_msg.edit_text(
                flow_copy.msg("nothing_returned"),
                reply_markup=_img_retry_kb(),
            )
            return False

        account_pool.mark_success(acc_id)
        await update_status(flow_copy.msg("sending"))
        await _send_result_pairs(
            message, pairs, user_id=user_id, project_id=project_id,
            prompt=prompt, aspect_ratio=aspect_ratio, emoji="🎨", account_id=acc_id,
        )
        await status_msg.delete()
        streak_note = _streak_note(user_id)
        await _after_result(message, user_id, streak_note=streak_note)
        return True
    finally:
        _img_anim_stop.set()
        if _img_anim_task:
            _img_anim_task.cancel()


def _streak_note(user_id: int) -> str | None:
    """Return a short streak congratulation if today is the user's first generation.

    Returns ``None`` when the user already generated today (no-op call) or on
    any error, so the caller can always safely prepend it to a message.
    """
    try:
        current, _max, is_new_day = metrics.update_streak(user_id)
        if not is_new_day:
            return None
        if current == 1:
            return flow_copy.msg("streak_day_1")
        if current in (3, 7, 14, 30):
            return flow_copy.msg(f"streak_milestone_{current}")
        days_word = _days_word(current)
        return flow_copy.msg("streak_ongoing", n=current, days=days_word)
    except Exception:
        return None


def _days_word(n: int) -> str:
    """Russian plural form for 'день/дня/дней'."""
    if 11 <= n % 100 <= 19:
        return "дней"
    rem = n % 10
    if rem == 1:
        return "день"
    if 2 <= rem <= 4:
        return "дня"
    return "дней"


async def _after_result(message: types.Message, user_id: int, *, streak_note: str | None = None):
    """Короткое меню после результата: создать ещё · видео · друг · меню."""
    B = types.InlineKeyboardButton
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [_menu_button("gen", "m:gen"), _menu_button("vid_gen", "m:vid")],
            [_invite_button(user_id)],
            [_menu_button("menu", "m:menu")],
        ]
    )
    text = streak_note or flow_copy.msg("after_image_screen",
                                        credits=credit_store.balance(user_id))
    if streak_note:
        text = f"{streak_note}\n\n{flow_copy.msg('after_image_screen', credits=credit_store.balance(user_id))}"
    cta = _first_referral_cta_text(user_id)
    if cta:
        text = f"{text}\n\n{cta}"
    try:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
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
    account_id: str | None = None,
):
    """Отправить набор картинок с кнопками действий (общий для всех режимов)."""
    total = len(pairs)
    # Реферальная ссылка автора — под каждой картинкой
    ref_link = _referral_link(user_id)
    if BOT_USERNAME:
        ref_text = f'\n\n<a href="{html.escape(ref_link)}">Создай своё в @{html.escape(BOT_USERNAME)}</a>'
    else:
        ref_text = ""
    for i, (url, img) in enumerate(pairs, 1):
        counter = f" {i}/{total}" if total > 1 else ""
        caption = f"{emoji}{counter} · {html.escape(_short_prompt(prompt, 80))}{ref_text}"
        await _send_one_image(
            message,
            url=url,
            img=img,
            index=i,
            total=total,
            caption=caption,
            user_id=user_id,
            project_id=project_id,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            account_id=account_id,
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


async def _send_owner_alert(text: str) -> None:
    """Send a plain-text Telegram message to every OWNER_ID.  Never raises."""
    for oid in OWNER_IDS:
        try:
            await bot.send_message(oid, text, parse_mode="HTML")
        except Exception:
            pass


def _fire_owner_alert(text: str) -> None:
    """Schedule owner alert from a synchronous call-site (fire-and-forget)."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_send_owner_alert(text))
    except RuntimeError:
        pass  # no running loop — silently drop


def _mark_image_account_failure(account_id: str | None, result: dict | None = None) -> None:
    if not account_id:
        return
    if (result or {}).get("account_risk") == "unusual_activity":
        if account_pool.mark_cooldown(account_id):
            log.warning("Image account %s cooled down after provider unusual-activity", account_id)
            metrics.log_event(
                "account_cooldown",
                payload={"account": account_id, "reason": "unusual_activity", "op": "image"},
            )
            _fire_owner_alert(
                f"⚠️ <b>Аккаунт кулдаун</b>\n"
                f"Аккаунт: <code>{account_id}</code>\n"
                f"Причина: unusual_activity (image)"
            )
        return
    if _is_rate_limit_error(result):
        # Провайдер вернул 429 — сразу остужаем аккаунт. Кулдаун в пуле общий,
        # поэтому он блокирует и картинки, и видео на этом аккаунте; роутер
        # уводит трафик на здоровые аккаунты (owner-alert не шлём — при флоте
        # 429 может быть частым, не спамим).
        if account_pool.mark_cooldown(account_id):
            log.warning("Image account %s cooled down after provider 429 (rate limit)", account_id)
            metrics.log_event(
                "account_cooldown",
                payload={"account": account_id, "reason": "rate_limited", "op": "image"},
            )
        return
    if account_pool.mark_failure(account_id):
        _fire_owner_alert(
            f"⚠️ <b>Аккаунт кулдаун</b>\n"
            f"Аккаунт: <code>{account_id}</code>\n"
            f"Причина: N ошибок подряд (image)"
        )


def _mark_video_account_failure(account_id: str | None, result: dict | None = None) -> None:
    if not account_id:
        return
    risk = (result or {}).get("account_risk")
    if risk == "video_auth":
        # Auth/bearer — кулдаун сразу (запросы всё равно не пройдут до фикса).
        if account_pool.mark_cooldown(account_id):
            log.warning("Video account %s cooled down after provider account-risk signal", account_id)
            metrics.log_event(
                "account_cooldown",
                payload={"account": account_id, "reason": risk, "op": "video"},
            )
            _fire_owner_alert(
                f"⚠️ <b>Аккаунт кулдаун</b>\n"
                f"Аккаунт: <code>{account_id}</code>\n"
                f"Причина: {risk} (video)"
            )
        return
    if _is_rate_limit_error(result):
        # Провайдер вернул 429 — сразу остужаем аккаунт (общий cooldown пула
        # блокирует и видео, и картинки на нём). Без owner-alert, чтобы не
        # спамить при частых лимитах на флоте.
        if account_pool.mark_cooldown(account_id):
            log.warning("Video account %s cooled down after provider 429 (rate limit)", account_id)
            metrics.log_event(
                "account_cooldown",
                payload={"account": account_id, "reason": "rate_limited", "op": "video"},
            )
        return
    # video_recaptcha_403 (стохастичный score) и прочие ошибки — НЕ остужаем
    # аккаунт после одной серии 403: считаем как fail, кулдаун лишь после
    # нескольких подряд (mark_failure порог). Так не выжигаем годный аккаунт.
    if risk == "video_recaptcha_403":
        metrics.log_event("video_recaptcha_403", payload={"account": account_id})
    if account_pool.mark_failure(account_id):
        _fire_owner_alert(
            f"⚠️ <b>Аккаунт кулдаун</b>\n"
            f"Аккаунт: <code>{account_id}</code>\n"
            f"Причина: N ошибок подряд (video)"
        )


async def _download_ref_image_bytes(ref: ImageRef) -> bytes | None:
    source = ref.source if isinstance(ref.source, dict) else {}
    tg_file_id = source.get("_tg_file_id")
    if isinstance(tg_file_id, str) and tg_file_id:
        try:
            buf = await bot.download(tg_file_id)
            return buf.read() if hasattr(buf, "read") else bytes(buf)
        except Exception:
            log.exception("download failover tg image failed")

    url = download_url(source)
    if not url:
        return None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=60)) as r:
                if r.status == 200:
                    return await r.read()
                log.warning("download failover image returned HTTP %s", r.status)
    except Exception:
        log.exception("download failover image failed")
    return None


async def _reupload_ref_for_edit_failover(
    ref: ImageRef,
    user_id: int,
    *,
    current_account_id: str | None,
) -> ImageRef | None:
    if not current_account_id:
        return None
    acc_id = _account_for_image(
        user_id, prefer_image_only=True, exclude={current_account_id},
    )
    if not acc_id or acc_id == current_account_id:
        return None

    data = await _download_ref_image_bytes(ref)
    if not data:
        return None
    project_id = await ensure_user_project(user_id, account_id=acc_id)
    try:
        source = await _keeper_for_acc(acc_id).upload_image(
            data, filename=f"failover_{user_id}.png",
        )
    except Exception:
        log.exception("failover upload_image failed")
        source = None
    if not source or not source.get("mediaId"):
        return None

    source.setdefault("_project_id", project_id)
    if isinstance(ref.source, dict) and ref.source.get("_tg_file_id"):
        source.setdefault("_tg_file_id", ref.source["_tg_file_id"])
    upload_project = source.pop("_project_id", None) or project_id
    return ImageRef(
        user_id=user_id,
        project_id=upload_project,
        source=source,
        prompt=ref.prompt,
        aspect_ratio=ref.aspect_ratio,
        account_id=acc_id,
    )


async def _edit_and_send(
    message: types.Message,
    ref: ImageRef,
    instruction: str,
    *,
    actor_id: int | None = None,
    aspect_ratio: str | None = None,
    image_model: str = DEFAULT_IMAGE_MODEL,
    price_action: str = "edit",
) -> bool:
    """Применить правку ``instruction`` к конкретной картинке ``ref``.

    Правка уходит именно этому изображению (через ``imageInputs``) в проекте
    того же пользователя. Браузерный фолбэк отключён, чтобы вместо правки не
    прислать несвязанную картинку. ``aspect_ratio`` / ``image_model`` позволяют
    сменить формат и модель прямо при редактировании (по умолчанию — как у
    исходной картинки и базовая модель). ``price_action`` задаёт тариф: ``edit``
    (правка, 15/20) или ``gen`` (фото-референс в «Создать картинку», 10/15).
    """
    user_id = actor_id if actor_id is not None else message.from_user.id

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
            async with credit_gate(user_id, price_action, message, 1, surcharge=surcharge) as charge:
                ok = await _do_edit_and_send(
                    message, ref, instruction, image_inputs, user_id,
                    aspect_ratio=aspect, image_model=image_model,
                )
                charge.ok = ok
    except RateLimited:
        _log_image_job(user_id, "edit", image_model, started, ok=False, error="user_busy")
        return False
    except NotEnoughCredits:
        metrics.log_event("image_failed", user_id=user_id, source="edit",
                          payload={"reason": "insufficient_credits"})
        return False
    charged = (action_price(price_action, 1) + surcharge) if ok else 0
    metrics.log_event("image_success" if ok else "image_failed", user_id=user_id, source="edit")
    if ok:
        metrics.log_event("credits_charged", user_id=user_id, source="edit",
                          payload={"amount": charged, "action": price_action})
    _log_image_job(user_id, "edit", image_model, started, ok=ok, charged=charged)
    if ok:
        # Запоминаем правку для «🔁 Повторить» под результатом. Без этого «Изменить
        # моё фото» сбрасывает last (keep_last=False), и повтор выдавал «Нет
        # предыдущей генерации». kind="edit" → повтор переприменяет ту же правку.
        _ws(user_id)["last"] = {
            "kind": "edit",
            "ref": ref,
            "instruction": instruction,
            "aspect": aspect,
            "imodel": image_model,
            "price_action": price_action,
        }
        await _post_generation_referral_hooks(message, user_id)
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

    async def _generate_for(edit_ref: ImageRef, inputs: list) -> dict:
        return await _client_for_acc(edit_ref.account_id).generate_images(
            instruction,
            aspect_ratio=aspect,
            num_images=1,
            progress_cb=update_status,
            project_id=edit_ref.project_id,
            image_inputs=inputs,
            allow_browser_fallback=False,
            image_model=image_model,
        )

    active_ref = ref
    result: dict
    try:
        result = await _generate_for(active_ref, image_inputs)
    except Exception:
        log.exception("Edit failed")
        await status_msg.edit_text("❌ Ошибка редактирования. Попробуйте ещё раз.")
        return False

    if "error" in result:
        if _is_rate_limit_error(result):
            _mark_image_account_failure(ref.account_id, result)
            log.info("image_edit failover: аккаунт %s ушёл в кулдаун, пробую другой", ref.account_id)
            failover_ref = await _reupload_ref_for_edit_failover(
                ref, user_id, current_account_id=ref.account_id,
            )
            failover_inputs = (
                build_image_inputs(failover_ref.source, load_edit_capture(EDIT_CAPTURE_FILE))
                if failover_ref else []
            )
            if failover_ref and failover_inputs:
                try:
                    result = await _generate_for(failover_ref, failover_inputs)
                    if "error" not in result:
                        active_ref = failover_ref
                        account_pool.mark_success(failover_ref.account_id)
                except Exception:
                    log.exception("Edit failover retry failed")
                    result = {"error": flow_copy.msg("image_edit_rate_limited")}
            if "error" in result:
                if failover_ref and _is_rate_limit_error(result):
                    _mark_image_account_failure(failover_ref.account_id, result)
                await status_msg.edit_text(flow_copy.msg("image_edit_rate_limited"))
                return False
        else:
            await status_msg.edit_text(f"❌ {html.escape(str(result['error'])[:300])}")
            return False

    pairs = result_pairs(result)
    if not pairs:
        log.warning(f"Пустой ответ редактирования: {str(result)[:500]}")
        await status_msg.edit_text(flow_copy.msg("nothing_returned"))
        return False

    await update_status(flow_copy.msg("sending"))
    account_pool.mark_success(active_ref.account_id)
    await _send_result_pairs(
        message, pairs, user_id=user_id, project_id=active_ref.project_id,
        prompt=instruction, aspect_ratio=aspect, emoji="✏️",
        account_id=active_ref.account_id,
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
        _log_image_job(user_id, action, None, started, ok=False, error="user_busy")
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
    if ok:
        await _post_generation_referral_hooks(message, user_id)
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
        result = await _client_for_acc(ref.account_id).generate_images(
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
            _mark_image_account_failure(ref.account_id, result)
            await status_msg.edit_text(flow_copy.msg("image_edit_rate_limited"))
        else:
            await status_msg.edit_text(f"❌ {html.escape(str(result['error'])[:300])}")
        return False

    pairs = result_pairs(result)
    if not pairs:
        await status_msg.edit_text(fail_text)
        return False

    await _send_result_pairs(
        message, pairs, user_id=user_id, project_id=ref.project_id,
        prompt=prompt, aspect_ratio=ref.aspect_ratio, emoji=emoji,
        account_id=ref.account_id,
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
        _log_image_job(user_id, "realup", None, started, ok=False, error="user_busy")
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
        user_id=user_id,
        account_id=(ref.account_id or account_pool.assigned_to(user_id) or FLOW_ACCOUNT_ID),
        operation_type="upscale",
        model=None, bot_credits_charged=charged, duration_ms=_ms_since(started),
        status="success" if ok else "fail", error_type=None if ok else "upscale_failed",
    )
    if ok:
        await _post_generation_referral_hooks(message, user_id)


async def _do_real_upscale(message: types.Message, ref: ImageRef, media_id: str) -> bool:
    status_msg = await message.answer(flow_copy.msg("upscaling"))

    async def update_status(text: str):
        try:
            await status_msg.edit_text(text)
        except Exception:
            pass

    try:
        result = await _client_for_acc(ref.account_id).upsample_image(
            media_id, ref.project_id, progress_cb=update_status
        )
    except Exception:
        log.exception("real upscale failed")
        await status_msg.edit_text(flow_copy.msg("gen_failed"))
        return False

    if "error" in result:
        # Тексты ошибок здесь — наша копия из flow_copy, но эскейпим на случай
        # сырого текста от бэкенда (защита разметки от инъекции).
        await status_msg.edit_text(f"❌ {html.escape(str(result['error'])[:300])}")
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
        message, ref.prompt, num_images=1, aspect_ratio=ref.aspect_ratio,
        actor_id=ref.user_id, action="regen",
    )


def _image_ext_from_bytes(data: bytes, fallback: str = "png") -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    return fallback


def _marketplace_export_filename(ref: ImageRef, data: bytes) -> str:
    platform = (getattr(ref, "platform", "") or "").strip().lower()
    platform_slug = {
        "wb": "wildberries",
        "ozon": "ozon",
        "ym": "yandex_market",
    }.get(platform, "marketplace")
    aspect_slug = {
        "portrait_34": "3x4",
        "square": "1x1",
        "landscape": "16x9",
        "portrait": "9x16",
        "landscape_43": "4x3",
    }.get((getattr(ref, "aspect_ratio", "") or "").strip(), "card")
    media_id = ref.source.get("mediaId") if isinstance(ref.source, dict) else None
    suffix = str(media_id or "image")[-12:]
    ext = _image_ext_from_bytes(data)
    return f"photozhab_{platform_slug}_{aspect_slug}_{suffix}.{ext}"


def _marketplace_export_caption(ref: ImageRef) -> str:
    platform = (getattr(ref, "platform", "") or "").strip().lower()
    platform_name = _MP_PLAT_NAMES.get(platform, "маркетплейса")
    return (
        f"⬇️ Файл для {platform_name}: оригинал без сжатия Telegram. "
        "Подходит как исходник для загрузки в карточку; точный resize/zip серии будет отдельной функцией."
    )


async def _send_original_file(message: types.Message, ref: ImageRef, *, marketplace_export: bool = False):
    """⬇️ Оригинал: отдать картинку файлом в полном качестве.

    На сайте кнопка «upscale» — это клиентское скачивание файла, а не серверный
    запрос. Эквивалент в боте: скачать исходные байты и отправить ДОКУМЕНТОМ
    (Telegram не пережимает документы, в отличие от фото), сохранив полное
    разрешение сгенерированной картинки.
    """
    url = download_url(ref.source)
    if not url:
        await message.answer("⚠️ Нет ссылки на файл этой картинки.")
        return

    # Скачивание — бесплатная offline-операция: НЕ держим busy-слот, иначе
    # зависшая/идущая генерация мешает забрать уже готовый файл (audit Major 11).
    await _do_send_original_file(message, ref, url, marketplace_export=marketplace_export)


async def _do_send_original_file(
    message: types.Message,
    ref: ImageRef,
    url: str,
    *,
    marketplace_export: bool = False,
):
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
    if marketplace_export:
        filename = _marketplace_export_filename(ref, data)
        caption = _marketplace_export_caption(ref)
    else:
        filename = f"flow_{media_id or 'image'}.{_image_ext_from_bytes(data)}"
        caption = "⬇️ Оригинал в полном качестве (Telegram не сжимает документы)."
    try:
        await message.answer_document(
            BufferedInputFile(data, filename),
            caption=caption,
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
    acc_id = _account_for(user_id)
    try:
        result = await _client_for_acc(acc_id).generate_images(
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
        await status_msg.edit_text(f"❌ {html.escape(str(result['error'])[:300])}")
        return

    pairs = result_pairs(result)
    if not pairs:
        await status_msg.edit_text("⚠️ Микс не дал результата.")
        return

    mix_baskets[user_id] = []  # корзина израсходована
    await _send_result_pairs(
        message, pairs, user_id=user_id, project_id=project_id,
        prompt=prompt, aspect_ratio="landscape", emoji="🧩", account_id=acc_id,
    )
    await status_msg.delete()


async def _show_gallery(message: types.Message, *, user_id: int) -> None:
    """Показываем последние 20 изображений из галереи пользователя."""
    rows = metrics.get_gallery(user_id, limit=20)
    back_kb = types.InlineKeyboardMarkup(inline_keyboard=[[_menu_button("menu", "m:menu")]])
    if not rows:
        await message.answer(
            flow_copy.msg("gallery_empty"), reply_markup=back_kb, parse_mode="HTML"
        )
        return
    # Разбиваем на группы по 10 (Telegram media group limit)
    header_sent = False
    for chunk_start in range(0, len(rows), 10):
        chunk = rows[chunk_start:chunk_start + 10]
        media_group = [types.InputMediaPhoto(media=r["file_id"]) for r in chunk]
        if not header_sent:
            media_group[0] = types.InputMediaPhoto(
                media=chunk[0]["file_id"],
                caption=flow_copy.msg("gallery_header", total=len(rows)),
            )
            header_sent = True
        try:
            await message.answer_media_group(media=media_group)
        except Exception as exc:
            log.warning(f"Gallery send error: {exc}")

    # Кнопки под галереей — включая «Улучшить последнюю» если есть живой токен
    bottom_rows: list[list[types.InlineKeyboardButton]] = []
    latest_token = rows[0].get("token") if rows else None
    if latest_token and image_registry.get(latest_token):
        realup_price = action_price("realup")
        label_up = f"🔍 Улучшить последнюю · {realup_price} кр" if realup_price else "🔍 Улучшить последнюю"
        bottom_rows.append([types.InlineKeyboardButton(
            text=label_up, callback_data=action_callback_data("realup", latest_token)
        )])
    bottom_rows.append([_menu_button("menu", "m:menu")])
    await message.answer(
        "⬆️ Вот твои последние работы",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=bottom_rows),
    )


async def _show_prompt_history(message: types.Message, *, user_id: int) -> None:
    """Показать историю промптов пользователя с кнопками «Повторить»."""
    if _cfg.IS_SELLER:
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [_menu_button("gallery", "m:gallery")],
            [_menu_button("menu", "m:menu")],
        ])
        await message.answer(_seller_history_text(user_id), reply_markup=kb, parse_mode="HTML")
        return
    prompts = metrics.get_prompt_history(user_id, limit=10)
    back_kb = types.InlineKeyboardMarkup(inline_keyboard=[[_menu_button("menu", "m:menu")]])
    if not prompts:
        await message.answer(flow_copy.msg("history_empty"), reply_markup=back_kb, parse_mode="HTML")
        return
    B = types.InlineKeyboardButton
    # Build numbered list in message text
    lines = [flow_copy.msg("history_title", n=len(prompts))]
    rows: list[list[types.InlineKeyboardButton]] = []
    nums = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for i, p in enumerate(prompts):
        emoji = nums[i] if i < len(nums) else f"{i+1}."
        lines.append(f"{emoji} {html.escape(p[:100])}")
        rows.append([B(text=f"{emoji} Использовать", callback_data=f"w:hist:{i}")])
    rows.append([_menu_button("menu", "m:menu")])
    text = "\n".join(lines)
    # Stash the history list in wizard state so w:hist:N can retrieve it
    _ws(user_id)["_hist_cache"] = prompts
    await message.answer(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")


async def _show_support_menu(message: types.Message, *, user_id: int, edit: bool) -> None:
    _ws(user_id).pop("support_await", None)  # уход в меню поддержки снимает «жду вопрос»
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=L("support_new"), callback_data="m:support:new")],
        [types.InlineKeyboardButton(text=L("support_my"), callback_data="m:support:my")],
        [_menu_button("menu", "m:menu")],
    ])
    if edit:
        await message.edit_text(flow_copy.msg("support_menu"), reply_markup=kb)
    else:
        await message.answer(flow_copy.msg("support_menu"), reply_markup=kb)


async def _show_profile_screen(message: types.Message, *, user_id: int, edit: bool) -> None:
    """Экран «Мой профиль»: ссылки на галерею, историю запросов и поддержку."""
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [_menu_button("gallery", "m:gallery")],
        [_menu_button("history", "m:history")],
        [_menu_button("support", "m:support")],
        [_menu_button("menu", "m:menu")],
    ])
    text = "👤 Мой профиль\n\nТвои работы и история запросов — всё здесь."
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


async def _show_my_tickets(message: types.Message, *, user_id: int, edit: bool) -> None:
    tickets = metrics.get_user_tickets(user_id)
    back_kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [_menu_button("support", "m:support")],
        [_menu_button("menu", "m:menu")],
    ])
    if not tickets:
        text = flow_copy.msg("support_no_tickets")
    else:
        items = "\n\n".join(
            flow_copy.msg(
                "support_ticket_item",
                id=t["id"],
                status_emoji="✅" if t["status"] == "replied" else "⏳",
                status_label="Отвечен" if t["status"] == "replied" else "Ожидает",
                question=html.escape(t["message_text"][:80]),
                reply_line=(f"↪️ {html.escape(t['reply_text'][:120])}\n" if t["reply_text"] else ""),
                date=(t.get("created_at") or "")[:16],
            )
            for i, t in enumerate(tickets)
        )
        text = flow_copy.msg("support_tickets_list", items=items)
    if edit:
        await message.edit_text(text, reply_markup=back_kb)
    else:
        await message.answer(text, reply_markup=back_kb)


def _store_pending_photo_route(user_id: int, *, file_id: str, caption: str) -> None:
    pending_photo_routes[user_id] = {
        "file_id": file_id,
        "caption": caption.strip()[:2000],
    }


async def _offer_photo_route_choice(message: types.Message, *, user_id: int, caption: str) -> None:
    _store_pending_photo_route(
        user_id, file_id=message.photo[-1].file_id, caption=caption
    )
    await message.answer(
        flow_copy.msg("photo_route_choice", prompt=html.escape(_short_prompt(caption, 300))),
        reply_markup=_photo_route_kb(),
        parse_mode="HTML",
    )


async def _prepare_photo_edit_from_file_id(
    message: types.Message,
    *,
    user_id: int,
    file_id: str,
    caption: str,
    aspect_fmt: str = DEFAULT_FMT,
    image_model: str | None = None,
    as_generation: bool = False,
) -> bool:
    status_msg = await message.answer(flow_copy.msg("uploading_photo"))
    ref = await _upload_image_ref_from_file_id(
        message,
        user_id=user_id,
        status_msg=status_msg,
        file_id=file_id,
        prompt=caption or "uploaded image",
        aspect_ratio=_fmt_to_aspect(aspect_fmt),
    )
    try:
        await status_msg.delete()
    except Exception:
        pass
    if not ref:
        return False

    token = image_registry.add(ref)
    pending_edits[user_id] = token
    st = _ws(user_id)
    # Фото-референс из «Создать картинку» тарифицируется как генерация (10/15),
    # а не как правка (15/20). Флаг читают edit_confirm_kb/show_edit_confirm/es:apply.
    st["edit_as_gen"] = bool(as_generation)
    st["await"] = "edit_confirm" if caption else "edit"
    st["step"] = None
    st.pop("pending_prompt", None)
    st["edit_fmt"] = aspect_fmt
    st["edit_imodel"] = image_model or st.get("edit_imodel", DEFAULT_IMAGE_MODEL)
    st.pop("ag_variants", None)
    st.pop("edit_instruction", None)
    if caption:
        st["edit_instruction"] = caption
        await show_edit_confirm(message, user_id=user_id, edit=False)
    else:
        await message.answer(
            flow_copy.msg("photo_uploaded_ask_prompt"),
            reply_markup=edit_settings_kb(st["edit_fmt"], st["edit_imodel"]),
        )
    return True


async def _prepare_photo_video_from_file_id(
    message: types.Message,
    *,
    user_id: int,
    file_id: str,
    caption: str,
    vfmt: str | None = None,
    vstyle: str | None = None,
) -> bool:
    _vid_clear(user_id)
    st = _ws(user_id)
    _clear_image_flow_keys(st)
    status_msg = await message.answer(flow_copy.msg("uploading_photo"))
    source = await _upload_photo_source_from_file_id(
        message, user_id=user_id, status_msg=status_msg, file_id=file_id
    )
    try:
        await status_msg.delete()
    except Exception:
        pass
    if not source:
        return False
    st["vphoto"] = source
    st["vprompt"] = caption
    st["vstep"] = "vnewwiz"
    st["vmode"] = "ingredients"
    st["vfmt"] = vfmt or st.get("vfmt") or VID_DEFAULT_FMT
    st.setdefault("vdur", 4)
    st.setdefault("vquality", "lite")
    st["vstyle"] = vstyle if vstyle is not None else st.get("vstyle", "")
    st["vmodel"] = _nwiz_model(st)
    await show_new_video_wizard(message, user_id=user_id, edit=False)
    return True


# ── «Идеи и шаблоны»: готовые шаблоны (tp:) + подбор по шагам (gp:) ────

_IDEAS_PHOTO_KEYS = ("ideas_photo_file_id", "ideas_photo_caption", "ideas_extra_prompt")
_TP_STATE_KEYS = ("tp_tpl", "tp_step", "tp_answers", "tp_await")
_GP_STATE_KEYS = ("gp_step", "gp_answers", "gp_extra_prompt")


def _ideas_clear(st: dict, *, clear_photo: bool = False) -> None:
    for k in (*_TP_STATE_KEYS, *_GP_STATE_KEYS):
        st.pop(k, None)
    if clear_photo:
        for k in _IDEAS_PHOTO_KEYS:
            st.pop(k, None)
    st.pop("ideas_mode", None)


def _ideas_has_photo(st: dict) -> bool:
    return bool(st.get("ideas_photo_file_id"))


def _ideas_prompt_with_extra(prompt: str, st: dict) -> str:
    extra = (st.get("gp_extra_prompt") or st.get("ideas_extra_prompt") or "").strip()
    if not extra:
        return prompt
    base = (prompt or "high quality image").strip()
    return f"{base}. User note for the attached photo/reference: {extra}"


def _guided_image_fmt(answers: dict) -> str:
    fmt = (answers or {}).get("format")
    if fmt == "story":
        return "port"
    if fmt in ("square", "avatar"):
        return "sq"
    return "land"


def _guided_video_fmt(answers: dict) -> str:
    return "port" if (answers or {}).get("format") in ("story", "avatar") else "land"


async def _show_ideas_root(message: types.Message, *, user_id: int, edit: bool):
    st = _ws(user_id)
    _ideas_clear(st, clear_photo=False)
    st["ideas_mode"] = "root"
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [_menu_button("ideas_templates", "ih:templates")],
        [_menu_button("ideas_guided", "ih:guided")],
        [_menu_button("menu", "m:menu")],
    ])
    text = flow_copy.msg("ideas_root")
    if edit:
        await _edit_or_answer(message, text, kb, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


async def _render_template_step(message: types.Message, *, user_id: int):
    """Показать текущий вопрос шаблона (или скомпоновать промпт и уйти в визард)."""
    st = _ws(user_id)
    tid = st.get("tp_tpl")
    questions = prompts_lib.template_questions(tid) if tid else []
    step = st.get("tp_step", 0)
    if not tid or step >= len(questions):
        # Все ответы собраны → компонуем промпт и открываем экран генерации.
        prompt = prompts_lib.compose_template_prompt(tid, st.get("tp_answers", {}))
        prompt = _ideas_prompt_with_extra(prompt, st)
        target = prompts_lib.template_target(tid)
        metrics.log_event("template_used", user_id=user_id, source="ideas",
                          payload={"template": tid, "target": target})
        ideas_photo_file_id = st.get("ideas_photo_file_id")
        for k in (*_TP_STATE_KEYS, *_IDEAS_PHOTO_KEYS):
            st.pop(k, None)
        st.pop("ideas_mode", None)
        if target == "video":
            if ideas_photo_file_id:
                await _prepare_photo_video_from_file_id(
                    message,
                    user_id=user_id,
                    file_id=ideas_photo_file_id,
                    caption=prompt or "high quality video",
                )
                return
            # Шаблон-видео без фото: идём в новый video wizard с предзаполненным промптом.
            _vid_clear(user_id)
            _clear_image_flow_keys(st)
            st["vprompt"] = prompt or ""
            st.setdefault("vfmt", VID_DEFAULT_FMT)
            st.setdefault("vdur", 4)
            st.setdefault("vquality", "lite")
            st.setdefault("vstyle", "")
            await show_new_video_wizard(message, user_id=user_id, edit=True)
        elif ideas_photo_file_id:
            # «Фото = основа»: применяем собранный промпт шаблона как правку к
            # загруженному пользователем фото (тот же пайплайн, что «Изменить фото»).
            await _prepare_photo_edit_from_file_id(
                message,
                user_id=user_id,
                file_id=ideas_photo_file_id,
                caption=prompt or "high quality image",
            )
        else:
            st["pending_prompt"] = prompt or "high quality image"
            await show_wizard(message, user_id=user_id, edit=True)
        return
    q = questions[step]
    B = types.InlineKeyboardButton
    rows = []
    if q["type"] == "choice":
        st["tp_await"] = None
        for i, opt in enumerate(q["options"]):
            rows.append([B(text=opt["label"], callback_data=f"tp:ans:{i}")])
    else:
        st["tp_await"] = "text"  # ждём свободный текст в чат
    if q.get("optional"):
        rows.append([_menu_button("skip", "tp:skip")])
    nav = [_menu_button("back", "tp:back")] if step > 0 else []
    nav.append(_menu_button("cancel", "tp:cancel"))
    rows.append(nav)
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
    text = flow_copy.msg("ideas_qa_step", n=step + 1, total=len(questions), q=q["text"])
    if q["type"] == "text":
        text += "\n\n" + flow_copy.msg("ideas_type_hint")
    else:
        text += "\n\n" + flow_copy.msg("ideas_choice_hint")
    if _ideas_has_photo(st):
        text += "\n\n" + flow_copy.msg("ideas_photo_context_hint")
    await _edit_or_answer(message, text, kb)


def _tp_store_answer(st: dict, value: str):
    tid = st.get("tp_tpl")
    questions = prompts_lib.template_questions(tid) if tid else []
    step = st.get("tp_step", 0)
    if step < len(questions):
        st.setdefault("tp_answers", {})[questions[step]["key"]] = value
    st["tp_step"] = step + 1
    st["tp_await"] = None


async def _template_photo_received(message: types.Message, *, user_id: int) -> None:
    """Фото внутри «Идеи и шаблоны»: сохраняем как основу/референс до финального экрана."""
    st = _ws(user_id)
    caption = (message.caption or "").strip()
    st["ideas_photo_file_id"] = message.photo[-1].file_id
    if caption:
        st["ideas_photo_caption"] = caption

    if st.get("tp_tpl"):
        if caption and st.get("tp_await") == "text":
            _tp_store_answer(st, caption)
        elif caption:
            st["ideas_extra_prompt"] = caption
        await message.answer(flow_copy.msg("ideas_photo_attached_template"))
        await _render_template_step(message, user_id=user_id)
        return

    if "gp_step" in st:
        if caption:
            st["gp_extra_prompt"] = caption
        await message.answer(flow_copy.msg("ideas_photo_attached_guided"))
        await _render_guided_step(message, user_id=user_id)
        return

    if caption:
        st["ideas_extra_prompt"] = caption
    await message.answer(flow_copy.msg("ideas_photo_attached_root"))
    mode = st.get("ideas_mode")
    if mode == "templates":
        await message.answer(
            flow_copy.msg("ideas_templates_title"),
            reply_markup=_templates_picker_kb(),
            parse_mode="HTML",
        )
    else:
        await _show_ideas_root(message, user_id=user_id, edit=False)


async def _render_guided_step(message: types.Message, *, user_id: int):
    st = _ws(user_id)
    steps = prompts_lib.guided_steps()
    step = st.get("gp_step", 0)
    if step >= len(steps):
        answers = st.get("gp_answers", {})
        prompt = prompts_lib.compose_guided_prompt(answers)
        prompt = _ideas_prompt_with_extra(prompt, st)
        ideas_photo_file_id = st.get("ideas_photo_file_id")
        # Ветка «видео» уводит в видео-визард, остальное — в генерацию картинки.
        if answers.get("what") == "video":
            # Переносим формат и стиль из guided в видео-визард.
            gv_fmt = _guided_video_fmt(answers)
            gv_style = _GUIDED_TO_VID_STYLE.get(answers.get("style", ""), "")
            for k in (*_GP_STATE_KEYS, *_IDEAS_PHOTO_KEYS):
                st.pop(k, None)
            st.pop("ideas_mode", None)
            if ideas_photo_file_id:
                await _prepare_photo_video_from_file_id(
                    message,
                    user_id=user_id,
                    file_id=ideas_photo_file_id,
                    caption=prompt or "high quality video",
                    vfmt=gv_fmt,
                    vstyle=gv_style,
                )
                return
            await show_video_prompt_input(
                message, user_id=user_id, edit=True, vfmt=gv_fmt, vstyle=gv_style
            )
            return
        metrics.log_event("guided_completed", user_id=user_id, source="ideas")
        image_fmt = _guided_image_fmt(answers)
        for k in (*_GP_STATE_KEYS, *_IDEAS_PHOTO_KEYS):
            st.pop(k, None)
        st.pop("ideas_mode", None)
        if ideas_photo_file_id:
            await _prepare_photo_edit_from_file_id(
                message,
                user_id=user_id,
                file_id=ideas_photo_file_id,
                caption=prompt or "high quality image",
                aspect_fmt=image_fmt,
            )
            return
        st["pending_prompt"] = prompt or "high quality image"
        st["fmt"] = image_fmt
        await show_wizard(message, user_id=user_id, edit=True)
        return
    s = steps[step]
    text = flow_copy.msg("ideas_qa_step", n=step + 1, total=len(steps), q=s["text"])
    text += "\n\n" + flow_copy.msg("ideas_guided_hint")
    if _ideas_has_photo(st):
        text += "\n\n" + flow_copy.msg("ideas_photo_context_hint")
    await _edit_or_answer(message, text, _guided_step_kb(step))


def _agent_improve_instruction(prompt: str) -> str:
    """Wrap the user's draft into an instruction that asks the agent for three
    distinct, ready-to-use prompt variants (text only, no image generation)."""
    return (
        "Ты — помощник по промптам для генерации видео/изображений. "
        "Улучши промпт ниже и предложи 3 РАЗНЫХ варианта на выбор (разные стиль/"
        "настроение/детали), каждый — законченный готовый промпт. Только текст "
        "вариантов, не запускай генерацию.\n\nИсходный промпт: " + prompt
    )


def _agent_edit_instruction(prompt: str) -> str:
    """Wrap a photo-edit instruction so the agent returns 3 clearer edit
    instructions (not generative scene prompts)."""
    return (
        "Ты — помощник по правкам фото. Улучши и уточни инструкцию правки ниже и "
        "предложи 3 варианта (разной детализации/акцента), каждый — законченная "
        "инструкция, ЧТО изменить на фото. Только текст вариантов, без генерации."
        "\n\nИсходная правка: " + prompt
    )


async def _agent_improve_call(user_id: int, prompt: str, *, instruction_fn=None) -> dict:
    """Improve a prompt via the Flow agent, trying video-capable accounts until
    one returns content (some accounts' bearers are rejected by the endpoint;
    one account's captcha score is stochastic)."""
    instruction_fn = instruction_fn or _agent_improve_instruction
    try:
        project_id = await ensure_user_project(user_id, account_id=_account_for(user_id))
        candidates: list[str] = []
        primary = _account_for_video(user_id)
        if primary:
            candidates.append(primary)
        for acc in account_pool.account_ids():
            if acc not in candidates and account_pool.is_video_capable(acc):
                candidates.append(acc)
        instruction = instruction_fn(prompt)
        res: dict = {}
        for acc_id in candidates[:5]:
            res = await _client_for_acc(acc_id).improve_prompt(instruction, project_id=project_id)
            log.info(
                "✨ improve try acc=%s status=%s err=%s variants=%d",
                acc_id, (res or {}).get("status"), (res or {}).get("error"),
                len((res or {}).get("variants") or []),
            )
            if (res or {}).get("variants") or (res or {}).get("single"):
                break
        return res or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("prompt_improve exception: %s", exc.__class__.__name__, exc_info=True)
        return {"error": "exception"}


def _agent_variants_view(variants: list[dict], *, pick_prefix: str, keep_data: str):
    """Build the 3-variant picker message + keyboard (shared by image & video)."""
    rows = []
    for i, v in enumerate(variants):
        label = (v["title"] or v["prompt"])[:48]
        rows.append([types.InlineKeyboardButton(text=f"{i + 1}. {label}", callback_data=f"{pick_prefix}{i}")])
    rows.append([types.InlineKeyboardButton(text="↩️ Оставить мой", callback_data=keep_data)])
    body = "✨ <b>Варианты промпта</b> — выбери, какой использовать:\n\n" + "\n\n".join(
        f"<b>{i + 1}. {html.escape(v['title'])}</b>\n{html.escape(v['prompt'][:300])}"
        for i, v in enumerate(variants)
    )
    return body, types.InlineKeyboardMarkup(inline_keyboard=rows)


async def _agent_improve_flow(
    callback: types.CallbackQuery, *, user_id: int, prompt_key: str,
    source: str, pick_prefix: str, keep_data: str, rerender, edit_fn,
    empty_prompt_msg: str, instruction_fn=None,
) -> None:
    """Shared "✨ Улучшить промпт" flow for the image/video/edit wizards."""
    msg = callback.message
    st = _ws(user_id)
    prompt = (st.get(prompt_key) or "").strip()
    if not prompt:
        await callback.answer(empty_prompt_msg, show_alert=True)
        return
    price = action_price("prompt_improve")
    if credit_store.balance(user_id) < price:
        await callback.answer()
        kb_low = types.InlineKeyboardMarkup(inline_keyboard=[
            [_menu_button("topup", "m:topup")], [_menu_button("menu", "m:menu")]
        ])
        await edit_fn(msg, flow_copy.msg("low_balance", needed=price, have=credit_store.balance(user_id)),
                      kb_low, parse_mode="HTML")
        return
    credit_store.charge(user_id, price)
    metrics.log_event("prompt_improve", user_id=user_id, source=source)
    await callback.answer("✨ Думаю над вариантами…")
    try:
        await msg.edit_text("✨ Подбираю варианты промпта…")
    except Exception:
        pass
    res = await _agent_improve_call(user_id, prompt, instruction_fn=instruction_fn)
    variants = [v for v in (res.get("variants") or []) if v.get("prompt")][:3]
    single = res.get("single")
    if not variants and not single:
        credit_store.refund(user_id, price)
        await rerender(msg, user_id=user_id, edit=True)
        try:
            await msg.answer("Не получилось улучшить промпт — кредиты вернул. Попробуй ещё раз 🙏")
        except Exception:
            pass
        return
    if not variants and single:
        st[prompt_key] = single
        await rerender(msg, user_id=user_id, edit=True)
        return
    st["ag_variants"] = [{"title": v.get("title", ""), "prompt": v.get("prompt", "")} for v in variants]
    body, kb = _agent_variants_view(st["ag_variants"], pick_prefix=pick_prefix, keep_data=keep_data)
    await edit_fn(msg, body, kb, parse_mode="HTML")


async def _agent_pick(callback: types.CallbackQuery, *, user_id: int, idx_str: str,
                      prompt_key: str, rerender) -> None:
    st = _ws(user_id)
    try:
        idx = int(idx_str)
    except (ValueError, TypeError):
        await callback.answer()
        return
    variants = st.get("ag_variants") or []
    if 0 <= idx < len(variants):
        st[prompt_key] = variants[idx].get("prompt") or st.get(prompt_key)
        await callback.answer("Готово ✨")
    else:
        await callback.answer()
    st.pop("ag_variants", None)
    await rerender(callback.message, user_id=user_id, edit=True)


@dp.message(F.video | F.document)
async def handle_video_upload(message: types.Message):
    """Приём пользовательского видео для режима «Изменить своё видео»."""
    user_id = message.from_user.id
    st = _ws(user_id)
    if not _cfg.UPLOAD_VIDEO_EDIT_ENABLED or st.get("vawait") != "vu_video":
        return  # видео ждём только в этом режиме (и пока фича включена) — иначе игнор
    file_obj = message.video or message.document
    if file_obj is None:
        return
    # Документы-картинки сюда не относим (для них есть обычный фото-флоу).
    mime = (getattr(file_obj, "mime_type", "") or "")
    if message.document and not mime.startswith("video"):
        await message.answer(flow_copy.msg("vid_upload_need_video"))
        return
    status = await message.answer(flow_copy.msg("vid_upload_working"))
    try:
        buf = await bot.download(file_obj.file_id)
        data = buf.read() if hasattr(buf, "read") else bytes(buf)
    except Exception:
        log.exception("download user video failed")
        await status.edit_text(flow_copy.msg("vid_upload_failed"))
        return
    acc_id = _account_for_video(user_id)
    if acc_id is None:
        await status.edit_text(flow_copy.msg("accounts_unavailable"))
        return
    project_id = await ensure_user_project(user_id, account_id=acc_id)
    source = await _keeper_for_acc(acc_id).upload_video(
        data,
        filename=f"tg_{user_id}.mp4",
        project_id=project_id,
        content_type=mime or "video/mp4",
    )
    if not source or not source.get("mediaId"):
        await status.edit_text(flow_copy.msg("vid_upload_failed"))
        return
    # Транскод на стороне сервиса: без ожидания SUCCESSFUL правка падает FAILED.
    source.setdefault("_account_id", acc_id)
    ready_item = await _client_for_acc(acc_id).wait_video_ready(
        source["mediaId"], source.get("_project_id") or project_id or ""
    )
    if ready_item is None:
        await status.edit_text(flow_copy.msg("vid_upload_failed"))
        return
    # Реальная длительность клипа → endFrameIndex правки (кадры за концом клипа
    # роняют edit-джобу). Сервер надёжнее Telegram (документы без duration).
    source["duration_s"] = (
        video_duration_from_poll_item(ready_item)
        or float(getattr(file_obj, "duration", 0) or 0)
        or None
    )
    if source["duration_s"] is None:
        # endFrameIndex упадёт в дефолт 240: для клипа короче 8с правка уйдёт в
        # FAILED на стороне сервиса — пусть причина будет видна в логах.
        log.warning("🎬 upload: длительность не определена (ни poll, ни Telegram) — "
                    "endFrameIndex возьмёт дефолт %s", video_edit_end_frame(None))
    st["vu_source"] = source
    metrics.log_event("video_upload_edit_started", user_id=user_id, source="upload")
    try:
        await status.delete()
    except Exception:
        pass
    caption = (message.caption or "").strip()
    if len(caption) >= 3:
        # Видео пришло сразу с текстом правки — не переспрашиваем, генерируем.
        st["vawait"] = None
        await _video_edit_uploaded(message, caption, user_id=user_id)
        return
    st["vawait"] = "vu_edit_prompt"
    await message.answer(
        flow_copy.msg("vid_upload_ask_prompt", price=action_price("video_prompt_edit"))
    )


async def _video_edit_uploaded(message: types.Message, prompt: str, *, user_id: int) -> None:
    """Правка загруженного пользователем видео промптом (Extend недоступен)."""
    st = _ws(user_id)
    src = st.get("vu_source") or {}
    if not src.get("mediaId"):
        await message.answer(flow_copy.msg("vid_expired_wizard"))
        return
    # Ориентация — из реальных размеров загруженного видео (PUT-ответ),
    # иначе вертикальный ролик ушёл бы в правку как landscape.
    w, h = src.get("width"), src.get("height")
    fmt = "port" if isinstance(w, int) and isinstance(h, int) and h > w else "land"
    ref = VideoRef(
        user_id=user_id,
        project_id=src.get("_project_id"),
        media_id=src.get("mediaId"),
        prompt="",
        model_id="omni-flash-4s",
        aspect_ratio=_VID_FMT_TO_ASPECT[fmt],
        mode="edit",
        prompt_edited=True,  # навсегда блокирует Продлить у результата
        workflow_id=src.get("workflowId") or src.get("workflow_id"),
        duration_s=src.get("duration_s"),
        account_id=src.get("_account_id") or _account_for_video(user_id),
    )
    st["vmode"] = "edit"
    st["vmodel"] = "omni-flash-4s"
    st["vfmt"] = fmt
    st["vcount"] = 1
    st["vawait"] = None
    st.pop("vu_source", None)
    await _video_generate_and_send(
        message,
        prompt.strip(),
        user_id=user_id,
        unit_price_override=action_price("video_prompt_edit"),
        prompt_edited=True,
        status_text=flow_copy.msg("vid_edit_working"),
        source_video=ref,
        video_operation="edit",
    )




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

    # ── Новый prompt-first видео-wizard (v:n* callbacks) ─────────────────
    if data.startswith("v:n"):
        # Формат тоггл
        if data.startswith("v:nfmt:"):
            fmt = data.split(":", 2)[2]
            if fmt in ("land", "port"):
                st["vfmt"] = fmt
            await callback.answer()
            await show_new_video_wizard(msg, user_id=user_id, edit=True)
            return

        # Движок — ⚡ Быстро (Omni) / 💎 Качество (Veo)
        if data.startswith("v:neng:"):
            eng = data.split(":", 2)[2]
            if eng in ("omni", "veo"):
                st["vengine"] = eng
            await callback.answer()
            await show_new_video_wizard(msg, user_id=user_id, edit=True)
            return

        # Длительность (Omni)
        if data.startswith("v:ndur:"):
            try:
                dur = int(data.split(":", 2)[2])
            except (ValueError, IndexError):
                await callback.answer()
                return
            if dur in _VID_OMNI_DURATIONS:
                st["vdur"] = dur
            await callback.answer()
            await show_new_video_wizard(msg, user_id=user_id, edit=True)
            return

        # Качество Veo — цикл по значениям
        if data.startswith("v:nqual:"):
            q = data.split(":", 2)[2]
            if q in _VID_VEO_QUALITY_CYCLE:
                st["vquality"] = q
            await callback.answer()
            await show_new_video_wizard(msg, user_id=user_id, edit=True)
            return

        # Стили: открыть экран выбора
        if data == "v:nstyle:screen":
            await callback.answer()
            text_styles = _nwiz_text(user_id)
            await _vid_edit(msg, text_styles, _nwiz_styles_kb(), user_id, parse_mode="HTML")
            return

        # Стили: выбрать стиль или вернуться назад
        if data.startswith("v:nstyle:"):
            key = data.split(":", 2)[2]
            if key == "back":
                await callback.answer()
                await show_new_video_wizard(msg, user_id=user_id, edit=True)
                return
            if key in _VID_STYLES:
                st["vstyle"] = key
                await callback.answer(_VID_STYLES[key][0])
            else:
                await callback.answer()
            await show_new_video_wizard(msg, user_id=user_id, edit=True)
            return

        # Убрать фотографию
        if data == "v:nremove_photo":
            st.pop("vphoto", None)
            st["vmode"] = "text"
            st["vmodel"] = _nwiz_model(st)
            await callback.answer("Фото удалено")
            await show_new_video_wizard(msg, user_id=user_id, edit=True)
            return

        # Изменить промпт — вернуться к вводу описания БЕЗ сброса состояния.
        # show_video_prompt_input делает _vid_clear (стирает vphoto и ставит
        # vmode="text"), из-за чего «Оживить фото» теряло приложенное фото и
        # генерировало видео без него. Вместо этого помечаем ожидание нового
        # промпта (vstep остаётся "vnewwiz") — следующий текст ловит хендлер
        # vnewwiz+vnchange и перерисовывает визард, сохраняя vphoto.
        if data == "v:nchange":
            await callback.answer()
            st["vawait"] = "vnchange"
            has_photo = bool(st.get("vphoto"))
            text = (
                "🎬 <b>Оживить фото</b>\n\n"
                "📎 Фото сохранено. Опишите заново, что должно происходить в видео."
            ) if has_photo else (
                "🎬 <b>Создать видео</b>\n\n"
                "Опишите, что должно происходить в видео. "
                "Можно приложить фото — тогда оживим его в движение 📎"
            )
            kb = types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text=L("cancel"), callback_data="v:cancel")]
            ])
            await _vid_edit(msg, text, kb, user_id, parse_mode="HTML")
            return

        # Создать видео
        if data == "v:ngo":
            prompt = (st.get("vprompt") or "").strip()
            if not prompt:
                await callback.answer("Сначала введите описание видео", show_alert=True)
                return
            # Применяем стилевой суффикс к промпту
            style_key = st.get("vstyle", "")
            style_suffix = _VID_STYLES.get(style_key, ("", ""))[1]
            full_prompt = prompt + style_suffix
            # Если прикреплено фото — передаём как референс-изображение
            if st.get("vphoto"):
                st["ving_photos"] = [st["vphoto"]]
            # Финальная синхронизация модели/режима
            st["vmodel"] = _nwiz_model(st)
            st["vmode"] = "ingredients" if st.get("vphoto") else "text"
            st["vcount"] = 1
            # Проверка баланса
            price = _nwiz_price(st)
            if credit_store.balance(user_id) < price:
                kb_low = types.InlineKeyboardMarkup(inline_keyboard=[
                    [_menu_button("topup", "m:topup")], [_menu_button("menu", "m:menu")]
                ])
                await callback.answer()
                await _vid_edit(
                    msg,
                    flow_copy.msg("low_balance", needed=price, have=credit_store.balance(user_id)),
                    kb_low, user_id, parse_mode="HTML",
                )
                return
            await callback.answer()
            await _video_generate_and_send(msg, full_prompt, user_id=user_id)
            return

        # Неизвестный v:n* — просто игнорируем
        await callback.answer()
        return
    # ── конец нового wizard callbacks ────────────────────────────────────

    # ⚡ Быстрый старт — omni-flash-4s, пропускаем пикер семейства и модели.
    if data == "v:quick":
        await callback.answer()
        st["vfamily"] = _VID_QUICKSTART_FAMILY
        st["vmodel"] = _VID_QUICKSTART_MODEL
        await show_video_settings(msg, user_id=user_id)
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

    if data == "v:retrynew":
        # «Изменить промпт и снова» (после модерации): восстанавливаем настройки и
        # фото из снимка, но НЕ генерим сразу — ждём новый промпт от пользователя.
        snap = st.get("vretry")
        if not snap or not snap.get("vmodel"):
            await callback.answer(flow_copy.msg("vid_expired_wizard"), show_alert=True)
            await show_main_menu(msg, user_id=user_id, edit=True)
            return
        for k, v in snap.items():
            if k != "prompt" and v is not None:
                st[k] = v
        st["vawait"] = "vretry_prompt"
        await callback.answer()
        has_ref = bool(st.get("ving_photos") or st.get("vfrm_start") or st.get("vfrm_end"))
        text = (
            "✏️ <b>Изменить запрос</b>\n\n"
            "Опиши по-другому, что должно происходить в видео"
            + (" — фото и настройки сохранены 📎." if has_ref else ".")
        )
        kb = types.InlineKeyboardMarkup(inline_keyboard=[[_menu_button("menu", "m:menu")]])
        await _vid_edit(msg, text, kb, user_id, parse_mode="HTML")
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
        full_bytes = await _client_for_acc(ref.account_id).fetch_full_extended_video(
            ref.scene_id, ref.project_id
        )
        if full_bytes:
            return full_bytes, True
        log.warning("full stitched video unavailable; falling back to extension segment")

    video_bytes = fetched_bytes
    if video_bytes is None:
        video_bytes = await _client_for_acc(ref.account_id).fetch_video_bytes(ref.media_id)
    return video_bytes, False


async def _repeat_last(callback: types.CallbackQuery, user_id: int):
    last = _ws(user_id).get("last")
    if not last:
        await callback.message.answer("Нет предыдущей генерации.")
        return
    # Повтор правки фото: переприменяем ту же инструкцию к тому же исходнику.
    if last.get("kind") == "edit":
        ref = last.get("ref")
        if ref is None:
            await callback.message.answer("Нет предыдущей генерации.")
            return
        await _edit_and_send(
            callback.message,
            ref,
            last.get("instruction", ""),
            actor_id=user_id,
            aspect_ratio=last.get("aspect"),
            image_model=last.get("imodel", DEFAULT_IMAGE_MODEL),
            price_action=last.get("price_action", "edit"),
        )
        return
    await _generate_and_send(
        callback.message,
        last["prompt"],
        num_images=last["count"],
        aspect_ratio=last["aspect"],
        actor_id=user_id,
        image_model=last.get("imodel", DEFAULT_IMAGE_MODEL),
    )


def _source_account_id(source: dict | None) -> str | None:
    if isinstance(source, dict):
        value = source.get("_account_id")
        if isinstance(value, str) and value:
            return value
    return None


def _source_project_id(source: dict | None) -> str | None:
    if isinstance(source, dict):
        value = source.get("_project_id")
        if isinstance(value, str) and value:
            return value
    return None


def _video_reference_sources(st: dict, vmode: str) -> list[dict]:
    if vmode == "ingredients":
        return [s for s in (st.get("ving_photos") or []) if isinstance(s, dict)]
    if vmode == "frames":
        return [s for s in (st.get("vfrm_start"), st.get("vfrm_end")) if isinstance(s, dict)]
    return []


def _video_reference_account_id(st: dict, vmode: str) -> str | None:
    accounts = {
        aid for aid in (_source_account_id(s) for s in _video_reference_sources(st, vmode))
        if aid
    }
    if len(accounts) == 1:
        aid = next(iter(accounts))
        # Reference-медиа привязано к этому аккаунту → генерим там же даже если
        # он в кулдауне (иначе другой аккаунт = гарантированный 404). Только
        # hard-disabled / image-only отбраковываем.
        if account_pool.is_reference_usable(aid):
            return aid
    return None


def _video_reference_project_id(st: dict, vmode: str) -> str | None:
    projects = {
        pid for pid in (_source_project_id(s) for s in _video_reference_sources(st, vmode))
        if pid
    }
    return next(iter(projects)) if len(projects) == 1 else None


async def _reupload_reference_source(
    src: dict, *, user_id: int, acc_id: str, project_id: str | None
) -> dict | None:
    """Re-upload a reference photo to another account from its stored Telegram
    file id. Returns the new source dict (with _account_id/_project_id) or None."""
    tg_file_id = src.get("_tg_file_id") if isinstance(src, dict) else None
    if not tg_file_id:
        return None
    try:
        buf = await bot.download(tg_file_id)
        data = buf.read() if hasattr(buf, "read") else bytes(buf)
        new_src = await _keeper_for_acc(acc_id).upload_image(
            data, filename=f"tg_{user_id}.png", project_id=project_id
        )
    except Exception:
        log.exception("re-upload reference photo failed")
        return None
    if not new_src or not new_src.get("mediaId"):
        return None
    new_src.setdefault("_tg_file_id", tg_file_id)
    new_src.setdefault("_project_id", project_id)
    new_src.setdefault("_account_id", acc_id)
    return new_src


async def _ensure_reference_on_healthy_account(
    st: dict, vmode: str, *, user_id: int, model_id: str, min_credits: int
) -> str | None:
    """Pick a healthy account that holds the reference photo(s), re-uploading
    them transparently if the bound account isn't ready. Seamless: the user is
    never told that an account was unavailable. Returns None only if the whole
    pool is unusable for video."""
    sources = _video_reference_sources(st, vmode)
    if not sources:
        return _account_for_video(user_id, model_id=model_id, min_credits=min_credits)

    bound = _video_reference_account_id(st, vmode)
    if bound and not _video_account_health_reason(bound, model_id, min_credits):
        return bound  # bound account is healthy — use the existing upload

    target = _account_for_video(user_id, model_id=model_id, min_credits=min_credits)
    if target is None:
        # Whole pool unusable. Fall back to the bound account if it at least
        # has usable media there (better to try than to refuse).
        return bound
    if target == bound:
        return target

    project_id = await ensure_user_project(user_id, account_id=target)
    reuploaded: list[dict] = []
    for src in sources:
        new_src = await _reupload_reference_source(
            src, user_id=user_id, acc_id=target, project_id=project_id
        )
        if not new_src:
            # Can't move this photo (no file id / upload failed). Keep the bound
            # account if any — generation may still work there.
            return bound or target
        reuploaded.append(new_src)

    if vmode == "ingredients":
        st["ving_photos"] = reuploaded
    elif vmode == "frames":
        st["vfrm_start"] = reuploaded[0]
        if len(reuploaded) > 1:
            st["vfrm_end"] = reuploaded[1]
    log.info("🔁 video reference re-uploaded to healthy account %s (was %s)", target, bound)
    return target


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
    """Слот-обёртка: видео идёт через тот же per-user замок, что и картинки.

    Без него параллельные видео+картинка одного юзера читали баланс до
    списания друг друга (гонка проверь-потом-спиши на кредитах).
    """
    try:
        async with user_slot(user_id, message):
            await _do_video_generate_and_send(
                message, prompt, user_id=user_id,
                unit_price_override=unit_price_override,
                prompt_edited=prompt_edited, status_text=status_text,
                source_video=source_video, video_operation=video_operation,
                source_scene_id=source_scene_id,
            )
    except RateLimited:
        return


async def _do_video_generate_and_send(
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

    model_key = model_id
    single_price = unit_price_override if unit_price_override is not None else video_price(model_id, 1, vmode)
    total_price = single_price * vcount

    # Аккаунт пула: правки/продления держим на аккаунте исходного ролика
    # (media живёт только там), свежие генерации — на video-capable аккаунте.
    # Для reference-видео (ingredients/frames) загруженное фото привязано к
    # аккаунту → генерим строго на нём, без молчаливого фолбэка на другой
    # аккаунт (там медиа нет → 404). Если этот аккаунт стал недоступен (disabled)
    # — честно просим прислать фото заново, не списывая кредиты.
    has_reference = bool(_video_reference_sources(st, vmode))
    if source_video and source_video.account_id:
        # Extend/edit of an existing generated video: media lives only on that
        # account, can't be moved. (Rare; the source video was just created.)
        acc_id = source_video.account_id
    elif has_reference:
        # Photo-video (ingredients/frames): transparently (re)place the photo on
        # a healthy account so the user never sees an "account unavailable" error.
        acc_id = await _ensure_reference_on_healthy_account(
            st, vmode, user_id=user_id, model_id=model_id, min_credits=single_price
        )
    else:
        acc_id = _account_for_video(user_id, model_id=model_id, min_credits=single_price)
    if acc_id is None:
        # Нет доступных video-capable аккаунтов — отказ ДО списания кредитов.
        await message.answer(flow_copy.msg("accounts_unavailable"))
        return
    video_project_id = (
        source_video.project_id if source_video
        else _video_reference_project_id(st, vmode)
    )
    if not video_project_id:
        video_project_id = await ensure_user_project(user_id, account_id=acc_id)

    _vid_started = time.monotonic()
    metrics.log_event("video_requested", user_id=user_id, username=_username(message),
                      source=video_operation if video_operation != "generate" else vmode,
                      payload={"model": model_id, "count": vcount, "mode": vmode})

    have = credit_store.balance(user_id)
    if have < total_price:
        if have == 0:
            await message.answer(
                flow_copy.msg("zero_balance"),
                reply_markup=_zero_balance_kb(),
                parse_mode="HTML",
            )
        else:
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
            await status_msg.edit_text(f"{text}\n📝 {_short_prompt(prompt, 80)}")
        except Exception:
            pass

    # Фоновая анимация статусных фраз — как у генерации картинок: фразы меняются
    # каждые 2.5 сек, чтобы ожидание видео тоже было живым (раньше фраза
    # обновлялась только раз в ~15 сек на каждом 3-м polling-цикле).
    _vid_phrases = flow_copy.MESSAGES.get("vid_status_phrases") or []
    _vid_anim_stop = asyncio.Event()

    async def _vid_animate():
        await asyncio.sleep(2.5)
        for phrase in itertools.cycle(_vid_phrases):
            if _vid_anim_stop.is_set():
                return
            try:
                await status_msg.edit_text(f"{phrase}\n📝 {_short_prompt(prompt, 80)}")
            except Exception:
                pass
            await asyncio.sleep(2.5)
            if _vid_anim_stop.is_set():
                return

    _vid_anim_task = asyncio.create_task(_vid_animate()) if _vid_phrases else None

    def _stop_vid_anim():
        _vid_anim_stop.set()
        if _vid_anim_task:
            _vid_anim_task.cancel()

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

    async def _fail_retry(i: int, message_key: str = "vid_gen_failed", error_type: str = "video_gen_failed"):
        nonlocal refunded_units
        refund_amt = single_price * (vcount - i)
        credit_store.refund(user_id, refund_amt)
        refunded_units += vcount - i
        _stash_retry()
        _res = result if isinstance(result, dict) else {}
        # Модерация (danger_filter) = кривой промпт/картинка юзера, НЕ вина
        # аккаунта. Не пишем это в статистику аккаунта: ни в routing-score
        # (video_outcome), ни в flow_jobs как fail — иначе здоровый аккаунт
        # штрафуется за чужой промпт и проседает в выборе. Продуктовый счётчик
        # (video_failed) и возврат кредитов оставляем.
        content_moderation = error_type == "danger_filter"
        if not content_moderation:
            metrics.log_event(
                "video_outcome", user_id=user_id, source=acc_id,
                payload={"ok": False, "mode": vmode, "reason": error_type,
                         "model": model_id,
                         "model_key": _res.get("model_key") or model_key,
                         "model_family": _res.get("model_family") or meta.get("family"),
                         "endpoint": _res.get("endpoint") or vmode,
                         "transport": _res.get("transport") or "direct_http",
                         "attempts": int(_res.get("attempts") or 1),
                         "had_403": bool(_res.get("had_403")),
                         "unusual_403": bool(_res.get("unusual_403")),
                         "success_after_retry": False,
                         "browser_fallback": bool(_res.get("browser_fallback"))},
            )
        metrics.log_event("video_failed", user_id=user_id, source=vmode,
                          payload={"model": model_id, "reason": error_type})
        metrics.log_event("credits_refunded", user_id=user_id, source=vmode,
                          payload={"amount": refund_amt})
        if not content_moderation:
            metrics.log_flow_job(
                user_id=user_id, account_id=acc_id,
                operation_type=f"video_{vmode}", model=model_id,
                bot_credits_charged=0, refund_amount=refund_amt,
                duration_ms=_ms_since(_vid_started), status="fail", error_type=error_type,
            )
        # Модерация: повтор того же промпта бессмыслен — даём «изменить промпт».
        # Прочие сбои (в т.ч. audio_filtered, где помогает повтор) — обычный ретрай.
        retry_btn = (
            _menu_button("vid_retry_edit", "v:retrynew")
            if error_type == "danger_filter"
            else _menu_button("vid_retry", "v:retry")
        )
        fail_kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [retry_btn],
            [_menu_button("menu", "m:menu")],
        ])
        try:
            await status_msg.edit_text(flow_copy.msg(message_key), reply_markup=fail_kb)
        except Exception:
            await message.answer(flow_copy.msg(message_key), reply_markup=fail_kb)

    sent_count = 0
    try:
        from aiogram.types import BufferedInputFile

        for i in range(vcount):
            # Notify user if account is at video capacity before queuing.
            if not account_pool.has_video_capacity(acc_id):
                try:
                    await status_msg.edit_text(flow_copy.msg("high_load"))
                except Exception:
                    pass

            # danger_filter is stochastic on the provider side (the same prompt
            # often passes on a second try), so retry once on the SAME account
            # before surfacing it to the user.
            result = {}
            for _danger_attempt in range(2):
                async with account_pool.video_slot(acc_id):
                    result = await _client_for_acc(acc_id).generate_video(
                        prompt,
                        model_key=model_key,
                        aspect=aspect,
                        project_id=video_project_id,
                        reference_sources=st.get("ving_photos") if vmode == "ingredients" else None,
                        start_source=st.get("vfrm_start") if vmode == "frames" else None,
                        end_source=st.get("vfrm_end") if vmode == "frames" else None,
                        operation=video_operation,
                        source_media_id=source_video.media_id if source_video else None,
                        source_workflow_id=source_video.workflow_id if source_video else None,
                        source_scene_id=source_scene_id or (source_video.scene_id if source_video else None),
                        source_duration_s=source_video.duration_s if source_video else None,
                        progress_cb=update_status,
                    )
                if (result or {}).get("failure") != "danger_filter" or _danger_attempt == 1:
                    break
                log.info("🎬 danger_filter — ретрай 1 раз на том же аккаунте %s", acc_id)
                await update_status(flow_copy.msg("working"))

            # Slot released. Handle errors and optional failover.
            if "error" in result:
                # Контент-фейлы (звук/модерация) — это НЕ проблема аккаунта:
                # не остужаем аккаунт, не фейловеримся, показываем причину юзеру.
                _content_fail = (result or {}).get("failure")
                if _content_fail == "audio_filtered":
                    await _fail_retry(i, "video_audio_filtered", "audio_filtered")
                    return
                if _content_fail == "danger_filter":
                    await _fail_retry(i, "video_danger_filter", "danger_filter")
                    return
                # TEMP (capture-driven): surface why r2v/ingredients gen fails.
                log.warning(
                    "🎬 gen failed: mode=%s model=%s aspect=%s err=%s",
                    vmode, model_id, aspect, str(result.get("error"))[:300],
                )
                _mark_video_account_failure(acc_id, result)
                # Прозрачный фейловер на другой аккаунт для text-to-video (403/auth риски).
                # Ingredients/frames используют account-bound media — фейловер там невозможен.
                _failover_risk = (result or {}).get("account_risk")
                if (
                    _failover_risk in {"video_auth", "video_recaptcha_403"}
                    and vmode == "text"
                    and video_operation == "generate"
                    and not source_video
                ):
                    failover_acc = _account_for_video(user_id, model_id=model_id, min_credits=single_price)
                    if failover_acc and failover_acc != acc_id:
                        log.info("🔄 video failover: %s → %s", acc_id, failover_acc)
                        acc_id = failover_acc
                        video_project_id = await ensure_user_project(
                            user_id, account_id=acc_id
                        )
                        await update_status("⏳ Отправляю запрос на генерацию видео…")
                        # Acquire a fresh slot on the failover account.
                        async with account_pool.video_slot(acc_id):
                            result = await _client_for_acc(acc_id).generate_video(
                                prompt,
                                model_key=model_key,
                                aspect=aspect,
                                project_id=video_project_id,
                                reference_sources=None,
                                start_source=None,
                                end_source=None,
                                operation=video_operation,
                                source_media_id=None,
                                source_workflow_id=None,
                                source_scene_id=None,
                                source_duration_s=None,
                                progress_cb=update_status,
                            )
                        if "error" not in result:
                            # Фейловер успешен — продолжаем нормальный путь.
                            log.info("🎬 video failover succeeded on %s", acc_id)
                            # Не возвращаемся — упадём ниже в success-ветку.
                            pass
                        else:
                            log.warning("🎬 video failover also failed: %s", result.get("error"))
                            _mark_video_account_failure(acc_id, result)
                if "error" in result:
                    await _fail_retry(i)
                    return

            media_id = result["media_id"]
            metrics.log_event(
                "video_outcome", user_id=user_id, source=acc_id,
                payload={"ok": True, "mode": vmode,
                         "model": model_id,
                         "model_key": (result or {}).get("model_key") or model_key,
                         "model_family": (result or {}).get("model_family") or meta.get("family"),
                         "endpoint": (result or {}).get("endpoint") or vmode,
                         "transport": (result or {}).get("transport") or "direct_http",
                         "attempts": int((result or {}).get("attempts") or 1),
                         "had_403": bool((result or {}).get("had_403")),
                         "unusual_403": bool((result or {}).get("unusual_403")),
                         "success_after_retry": int((result or {}).get("attempts") or 1) > 1,
                         "browser_fallback": bool((result or {}).get("browser_fallback"))},
            )
            _stop_vid_anim()  # генерация готова — гасим анимацию фраз
            await update_status("⬇️ Готовлю видео для отправки…")
            video_bytes = await _client_for_acc(acc_id).fetch_video_bytes(media_id)

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
                # Правка не меняет длину клипа; свежая генерация = длина модели.
                # Нужна для endFrameIndex последующих правок (кадры за концом
                # клипа роняют edit-джобу).
                duration_s=(
                    source_video.duration_s if video_operation == "edit" and source_video
                    else float(meta["duration"]) if video_operation == "generate"
                    else None
                ),
                account_id=acc_id,
            )
            vtoken = video_registry.add(vref)
            account_pool.mark_success(acc_id)

            # Подпись держим чистой: только описание + реф-ссылка автора.
            # Цены/действия (Изменить · Продлить) живут на кнопках под роликом —
            # в подписи они мешали бы, если видео переслать другому человеку.
            caption = flow_copy.msg("vid_result_caption", i=i + 1, n=vcount, prompt=html.escape(_short_prompt(prompt, 60)))
            # Реферальная ссылка автора — как под картинками (см. _send_result_pairs).
            if BOT_USERNAME:
                ref_link = _referral_link(user_id)
                caption = (
                    f"{caption}\n\n"
                    f'<a href="{html.escape(ref_link)}">Создай своё в @{html.escape(BOT_USERNAME)}</a>'
                )
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
                    parse_mode="HTML",
                )
                sent_count += 1
            except Exception:
                log.exception("answer_video failed, falling back to document")
                try:
                    await message.answer_document(
                        BufferedInputFile(delivery_bytes, filename),
                        caption=caption,
                        reply_markup=video_result_kb(vtoken),
                        parse_mode="HTML",
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
                user_id=user_id, account_id=acc_id,
                operation_type=f"video_{vmode}", model=model_id,
                bot_credits_charged=charged,
                refund_amount=single_price * refunded_units,
                duration_ms=_ms_since(_vid_started), status="success",
            )
            try:
                await status_msg.delete()
            except Exception:
                pass
            await _post_generation_referral_hooks(message, user_id)
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
        _mark_video_account_failure(acc_id)
        metrics.log_flow_job(
            user_id=user_id, account_id=acc_id,
            operation_type=f"video_{vmode}", model=model_id, bot_credits_charged=0,
            refund_amount=total_price, duration_ms=_ms_since(_vid_started),
            status="error", error_type="exception",
        )
        try:
            await status_msg.edit_text(flow_copy.msg("vid_gen_failed"))
        except Exception:
            pass
    finally:
        _stop_vid_anim()
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
    video_bytes = await _client_for_acc(ref.account_id).fetch_video_bytes(ref.media_id)

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
    scene_id = ref.scene_id or await _client_for_acc(ref.account_id).prepare_video_extend_scene(
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
    # Fixed operator-set extend price.
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


def _robokassa_new_inv_id() -> int:
    return int(time.time() * 1000) * 1000 + random.randint(100, 999)


def _robokassa_receipt_json(pack_id: str, out_sum: str, credits: int) -> str:
    """Состав чека (номенклатура) для Робочеков СМЗ — сырой JSON.

    Одна позиция: купленные кредиты, её ``sum`` равен ``OutSum``. Самозанятый
    (НПД): без НДС (``tax=none``); ``sno`` не указываем — режим СМЗ держит сам
    Robokassa. Возвращаем компактный JSON без URL-encode: именно он идёт в
    подпись. Для ссылки его отдельно прогоняем через ``quote`` (rawurlencode).
    """
    receipt = {
        "items": [
            {
                "name": f"Пополнение баланса ФотоЖаб — {credits} кредитов",
                "quantity": 1,
                "sum": round(float(out_sum), 2),
                "payment_method": "full_payment",
                "payment_object": "service",
                "tax": "none",
            }
        ]
    }
    return json.dumps(receipt, ensure_ascii=False, separators=(",", ":"))


def _robokassa_payment_url(user_id: int, pack_id: str, inv_id: int) -> str:
    p = credit_pack(pack_id)
    if not p:
        raise ValueError(f"unknown pack: {pack_id!r}")
    out_sum = _robokassa_pack_amount(pack_id)
    shp = {"Shp_bot": ROBOKASSA_SCOPE, "Shp_pack": pack_id, "Shp_user": int(user_id)}
    receipt_json = _robokassa_receipt_json(pack_id, out_sum, int(p["credits"]))
    signature = robokassa_payment_signature(
        ROBOKASSA_MERCHANT_LOGIN,
        out_sum,
        inv_id,
        ROBOKASSA_PASSWORD1,
        shp_params=shp,
        receipt=receipt_json,
        algorithm=ROBOKASSA_HASH_ALGO,
    )
    params = {
        "MerchantLogin": ROBOKASSA_MERCHANT_LOGIN,
        "OutSum": out_sum,
        "InvId": str(inv_id),
        "Description": f"PhotoZhab credits: {p['credits']}",
        "SignatureValue": signature,
        "Culture": "ru",
        "Encoding": "utf-8",
        **shp,
    }
    if ROBOKASSA_INC_CURR_LABEL:
        params["IncCurrLabel"] = ROBOKASSA_INC_CURR_LABEL
    if ROBOKASSA_TEST:
        params["IsTest"] = "1"
    # Receipt идёт в подпись сырым JSON, а в ссылку — rawurlencode'нутым
    # (quote(..., safe="") ≡ PHP rawurlencode). Поэтому не пускаем его через
    # urlencode (иначе двойное кодирование) — добавляем вручную.
    query = urlencode(params) + "&Receipt=" + quote(receipt_json, safe="")
    return ROBOKASSA_PAY_URL + "?" + query


async def _start_robokassa_topup(callback: types.CallbackQuery, user_id: int, pack_id: str):
    p = credit_pack(pack_id)
    if not p or (p.get("test") and user_id not in ADMIN_IDS):
        await callback.answer("Пакет не найден", show_alert=True)
        return
    if not _robokassa_configured():
        await callback.answer("Оплата СБП пока не настроена", show_alert=True)
        return
    inv_id = _robokassa_new_inv_id()
    try:
        pay_url = _robokassa_payment_url(user_id, pack_id, inv_id)
    except Exception:
        log.exception("robokassa payment url failed")
        await callback.answer("Оплата временно недоступна", show_alert=True)
        return
    await callback.answer()
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Оплатить через СБП/карту", url=pay_url)],
        [_menu_button("back", "m:pay:robo")],
    ])
    await callback.message.answer(
        f"Счёт на {p['credits']} кр. Сумма: {_rub_display(_robokassa_pack_amount(pack_id))} ₽.\n"
        "После оплаты баланс пополнится автоматически.",
        reply_markup=kb,
    )


@dp.pre_checkout_query()
async def on_pre_checkout(query: types.PreCheckoutQuery):
    """Последний рубеж перед списанием звёзд: подтверждаем только наш payload."""
    payload = query.invoice_payload or ""
    parts = payload.split(":")
    ok = len(parts) >= 2 and parts[0] == "credits" and credit_pack(parts[1]) is not None
    if not ok:
        log.warning("pre_checkout отклонён: payload=%r", payload[:64])
    await query.answer(
        ok=ok,
        error_message=None if ok else "Пакет не найден — обновите меню и попробуйте снова.",
    )


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
    charge_id = getattr(sp, "telegram_payment_charge_id", "") or ""
    stars_paid = getattr(sp, "total_amount", p["stars"])
    # Идемпотентность ДО зачисления: Telegram может редоставить successful_payment
    # (бот упал до подтверждения offset и т.п.) — кредиты нельзя зачислять дважды.
    # Fallback-ключ включает message_id: редоставка того же апдейта дедупится,
    # а честная вторая покупка того же пака приходит новым сообщением.
    provider_payment_id = charge_id or f"nocharge:{user_id}:{pack_id}:{message.message_id}"
    tx_status = metrics.record_transaction_status(
        provider="telegram_stars",
        provider_payment_id=provider_payment_id,
        user_id=user_id, package_id=pack_id,
        amount_rub=round(stars_paid * STARS_TO_RUB, 2), stars_amount=stars_paid,
        credits_issued=p["credits"], status="paid",
    )
    if tx_status == "duplicate":
        log.warning("💳 Дубль доставки платежа проигнорирован: %s", provider_payment_id)
        return
    if tx_status == "error":
        # Метрики недоступны — звёзды уже уплачены, кредиты всё равно отдаём,
        # но громко логируем: дедуп-защита на этот платёж не сработала.
        log.error("💳 metrics недоступны, зачисляю без дедуп-гарантии: %s", provider_payment_id)
    new_balance = credit_store.add(user_id, p["credits"])
    # Запоминаем платёж (charge_id) — нужен для возврата звёзд через /refund.
    if charge_id:
        try:
            payment_store.add(user_id, charge_id, stars_paid, p["credits"], pack_id)
        except Exception:
            log.exception("payment_store.add failed")
    metrics.log_event("payment_success", user_id=user_id, username=_username(message),
                      source="stars",
                      payload={"pack": pack_id, "stars": stars_paid, "credits": p["credits"]})
    # Реферальная награда пригласившему (идемпотентно; не ломает оплату).
    _maybe_apply_referral_rewards(
        user_id, stars_paid=stars_paid, credits_issued=p["credits"],
        pack_id=pack_id, provider_payment_id=provider_payment_id,
    )
    log.info(f"💳 Оплата: +{p['credits']} кр пользователю {user_id} (баланс {new_balance})")
    await message.answer(
        flow_copy.msg("topup_done", credits=p["credits"], balance=new_balance)
    )
    await show_main_menu(message, user_id=user_id)


async def _robokassa_request_data(request: web.Request) -> dict[str, str]:
    data = {k: str(v) for k, v in request.query.items()}
    if request.method == "POST":
        post = await request.post()
        data.update({k: str(v) for k, v in post.items()})
    return data


def _robokassa_param(data: dict[str, str], *names: str) -> str:
    lower = {k.lower(): v for k, v in data.items()}
    for name in names:
        if name in data:
            return data[name]
        value = lower.get(name.lower())
        if value is not None:
            return value
    return ""


def _robokassa_shp_params(data: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in data.items() if k.startswith("Shp_")}


def _robokassa_amount_matches(actual: str, expected: str) -> bool:
    try:
        return abs(Decimal(actual) - Decimal(expected)) <= Decimal("0.01")
    except (InvalidOperation, TypeError):
        return False


def _robokassa_target_scope(shp: dict[str, str]) -> str:
    # Old Robokassa invoices did not carry Shp_bot; keep them on consumer.
    return _robokassa_clean_scope(shp.get("Shp_bot", "") or "consumer")


def _robokassa_result_url_for_scope(scope: str) -> str:
    return ROBOKASSA_SELLER_RESULT_URL if scope == "seller" else ROBOKASSA_CONSUMER_RESULT_URL


def _robokassa_provider_payment_id(inv_id: str, scope: str, *, legacy: bool = False) -> str:
    return f"robokassa:{inv_id}" if legacy else f"robokassa:{scope}:{inv_id}"


async def _robokassa_forward_result(target_scope: str, data: dict[str, str]) -> web.Response:
    target_url = _robokassa_result_url_for_scope(target_scope)
    if not target_url:
        return web.Response(status=503, text="route unavailable")
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(target_url, data=data) as resp:
                text = await resp.text()
                return web.Response(status=resp.status, text=text)
    except Exception:
        log.exception("robokassa route forward failed target=%s", target_scope)
        return web.Response(status=503, text="route unavailable")


def _robokassa_bot_username_for_scope(scope: str) -> str:
    if scope == "seller":
        return ROBOKASSA_SELLER_BOT_USERNAME or "photozhab_wb_bot"
    return ROBOKASSA_CONSUMER_BOT_USERNAME or "photozhab_bot"


async def _notify_robokassa_success(user_id: int, credits: int, balance: int) -> None:
    try:
        await bot.send_message(
            user_id,
            flow_copy.msg("topup_done", credits=credits, balance=balance),
            parse_mode="HTML",
            reply_markup=main_menu_kb(show_repeat=bool(_ws(user_id).get("last"))),
        )
    except TelegramForbiddenError:
        metrics.mark_user_blocked(user_id)
    except TelegramBadRequest as exc:
        if "chat not found" in str(exc).lower():
            log.warning("robokassa success notify skipped: chat not found user_id=%s", user_id)
        else:
            log.exception("robokassa success notify bad request")
    except Exception:
        log.exception("robokassa success notify failed")


async def robokassa_result(request: web.Request) -> web.Response:
    if not _robokassa_configured():
        return web.Response(status=503, text="Robokassa is not configured")
    data = await _robokassa_request_data(request)
    out_sum = _robokassa_param(data, "OutSum")
    inv_id = _robokassa_param(data, "InvId", "InvID")
    signature = _robokassa_param(data, "SignatureValue")
    shp = _robokassa_shp_params(data)
    expected = robokassa_result_signature(
        out_sum,
        inv_id,
        ROBOKASSA_PASSWORD2,
        shp_params=shp,
        algorithm=ROBOKASSA_HASH_ALGO,
    )
    if not signature or signature.lower() != expected.lower():
        log.warning("robokassa bad signature inv_id=%s", inv_id[:32])
        return web.Response(status=400, text="bad signature")

    target_scope = _robokassa_target_scope(shp)
    if target_scope != ROBOKASSA_SCOPE:
        return await _robokassa_forward_result(target_scope, data)

    pack_id = shp.get("Shp_pack", "")
    user_raw = shp.get("Shp_user", "")
    p = credit_pack(pack_id)
    if not p or not user_raw.isdigit() or not inv_id:
        log.warning(
            "robokassa unmatched payment inv_id=%s pack=%r user=%r amount=%r",
            inv_id[:32], pack_id[:64], user_raw[:32], out_sum[:32],
        )
        metrics.log_event(
            "robokassa_unmatched_payment",
            user_id=int(user_raw) if user_raw.isdigit() else 0,
            source="robokassa",
            payload={
                "inv_id": inv_id[:64],
                "pack": pack_id[:64],
                "user": user_raw[:64],
                "amount_rub": out_sum[:32],
                "reason": "bad_order",
            },
        )
        return web.Response(status=400, text="bad order")
    expected_amount = _robokassa_pack_amount(pack_id)
    if not _robokassa_amount_matches(out_sum, expected_amount):
        log.warning("robokassa amount mismatch inv_id=%s", inv_id[:32])
        return web.Response(status=400, text="bad amount")

    user_id = int(user_raw)
    provider_payment_id = _robokassa_provider_payment_id(
        inv_id, target_scope, legacy=("Shp_bot" not in shp)
    )
    tx_status = metrics.record_transaction_status(
        provider="robokassa",
        provider_payment_id=provider_payment_id,
        user_id=user_id,
        package_id=pack_id,
        amount_rub=float(Decimal(out_sum)),
        stars_amount=0,
        credits_issued=p["credits"],
        status="paid",
    )
    if tx_status == "duplicate":
        return web.Response(text=f"OK{inv_id}")
    if tx_status == "error":
        return web.Response(status=500, text="temporary error")

    new_balance = credit_store.add(user_id, p["credits"])
    metrics.log_event(
        "payment_success",
        user_id=user_id,
        source="robokassa",
        payload={"pack": pack_id, "amount_rub": out_sum, "credits": p["credits"]},
    )
    _maybe_apply_referral_rewards(
        user_id,
        stars_paid=p["stars"],
        credits_issued=p["credits"],
        pack_id=pack_id,
        provider_payment_id=provider_payment_id,
    )
    await _notify_robokassa_success(user_id, p["credits"], new_balance)
    return web.Response(text=f"OK{inv_id}")


async def _robokassa_status_page(request: web.Request, *, ok: bool) -> web.Response:
    data = await _robokassa_request_data(request)
    scope = _robokassa_target_scope(_robokassa_shp_params(data))
    username = html.escape(_robokassa_bot_username_for_scope(scope))
    if ok:
        title = "Оплата прошла"
        body = "Баланс пополнится автоматически. Можно вернуться в Telegram."
    else:
        title = "Оплата не завершена"
        body = "Деньги не списаны или платёж отменён. Вернись в бот и попробуй ещё раз."
    return web.Response(
        text=(
            "<!doctype html><meta charset='utf-8'>"
            f"<title>{html.escape(title)}</title>"
            "<body style='font-family:system-ui;max-width:560px;margin:48px auto;padding:0 20px'>"
            f"<h1>{html.escape(title)}</h1>"
            f"<p>{html.escape(body)}</p>"
            f"<p><a href='https://t.me/{username}'>Открыть бота</a></p>"
            "</body>"
        ),
        content_type="text/html",
    )


async def robokassa_success(request: web.Request) -> web.Response:
    return await _robokassa_status_page(request, ok=True)
    return web.Response(
        text=(
            "<!doctype html><meta charset='utf-8'>"
            "<title>Оплата прошла</title>"
            "<body style='font-family:system-ui;max-width:560px;margin:48px auto;padding:0 20px'>"
            "<h1>Оплата прошла</h1>"
            "<p>Баланс пополнится автоматически. Можно вернуться в Telegram.</p>"
            "<p><a href='https://t.me/photozhab_bot'>Открыть бота</a></p>"
            "</body>"
        ),
        content_type="text/html",
    )


async def robokassa_fail(request: web.Request) -> web.Response:
    return await _robokassa_status_page(request, ok=False)
    return web.Response(
        text=(
            "<!doctype html><meta charset='utf-8'>"
            "<title>Оплата не завершена</title>"
            "<body style='font-family:system-ui;max-width:560px;margin:48px auto;padding:0 20px'>"
            "<h1>Оплата не завершена</h1>"
            "<p>Деньги не списаны или платёж отменён. Вернись в бот и попробуй ещё раз.</p>"
            "<p><a href='https://t.me/photozhab_bot'>Открыть бота</a></p>"
            "</body>"
        ),
        content_type="text/html",
    )


async def robokassa_health(request: web.Request) -> web.Response:
    return web.Response(text="OK")


async def _start_web_server() -> web.AppRunner:
    import admin_api as _admin_api
    try:
        client_max_size = max(
            1024 * 1024,
            int(os.getenv("WEB_CLIENT_MAX_SIZE", str(32 * 1024 * 1024))),
        )
    except (TypeError, ValueError):
        client_max_size = 32 * 1024 * 1024
    app = web.Application(client_max_size=client_max_size)
    _admin_api.register_admin_routes(app, account_pool, keepers, clients,
                                     startup_state=startup_state,
                                     proxy_supervisor=local_proxy_sup)
    if local_proxy_sup is not None:
        # Re-spawn persisted local proxies and keep them alive across crashes.
        try:
            await local_proxy_sup.ensure_all_running()
            asyncio.create_task(local_proxy_sup.supervise_loop())
        except Exception:
            log.warning("local proxy supervisor startup failed", exc_info=True)
    if not _cfg.IS_SELLER:
        # Только consumer (с пулом) отдаёт генерацию для seller-бота (§A).
        try:
            import seller_backend
            seller_backend.register_internal_routes(app, _backend_generate)
        except Exception:
            log.exception("internal generation endpoint registration failed")
    if _robokassa_configured():
        app.router.add_route("*", "/robokassa/result", robokassa_result)
        app.router.add_get("/robokassa/success", robokassa_success)
        app.router.add_post("/robokassa/success", robokassa_success)
        app.router.add_get("/robokassa/fail", robokassa_fail)
        app.router.add_post("/robokassa/fail", robokassa_fail)
        app.router.add_get("/robokassa/health", robokassa_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, ROBOKASSA_WEB_HOST, ROBOKASSA_WEB_PORT)
    await site.start()
    log.info("Web server listening on %s:%s (admin API + robokassa=%s)",
             ROBOKASSA_WEB_HOST, ROBOKASSA_WEB_PORT, _robokassa_configured())
    return runner


async def _start_robokassa_web_server() -> web.AppRunner | None:
    return await _start_web_server()




async def _upload_photo_source_from_message(
    message: types.Message,
    *,
    user_id: int,
    status_msg: types.Message,
) -> dict | None:
    photo = message.photo[-1]
    return await _upload_photo_source_from_file_id(
        message, user_id=user_id, status_msg=status_msg, file_id=photo.file_id
    )


async def _upload_photo_source_from_file_id(
    message: types.Message,
    *,
    user_id: int,
    status_msg: types.Message,
    file_id: str,
) -> dict | None:
    try:
        buf = await bot.download(file_id)
        data = buf.read() if hasattr(buf, "read") else bytes(buf)
    except Exception:
        log.exception("download user photo failed")
        await status_msg.edit_text("❌ Не удалось получить ваше фото.")
        return None

    acc_id = _account_for_video(user_id)
    if acc_id is None:
        await status_msg.edit_text(flow_copy.msg("accounts_unavailable"))
        return None
    project_id = await ensure_user_project(user_id, account_id=acc_id)
    try:
        source = await _keeper_for_acc(acc_id).upload_image(data, filename=f"tg_{user_id}.png", project_id=project_id)
    except Exception:
        log.exception("upload_image failed")
        source = None

    if not source or not source.get("mediaId"):
        await status_msg.edit_text(flow_copy.msg("upload_failed"))
        return None

    # TEMP (capture-driven): same diagnostic family as the "🎬 r2v req" log —
    # lets us confirm the upload account/project matches the one later used
    # for the reference-to-video generate call.
    log.info(
        "📤 video ref photo uploaded account=%s project=%s media_id=%s",
        acc_id, project_id, source.get("mediaId"),
    )
    source.setdefault("_project_id", project_id)
    source.setdefault("_account_id", acc_id)
    source.setdefault("_tg_file_id", file_id)  # для бесшовного ре-аплоада
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


async def _upload_image_ref_from_photo_message(
    message: types.Message,
    *,
    user_id: int,
    status_msg: types.Message,
    prompt: str,
    aspect_ratio: str,
) -> ImageRef | None:
    photo = message.photo[-1]
    return await _upload_image_ref_from_file_id(
        message,
        user_id=user_id,
        status_msg=status_msg,
        file_id=photo.file_id,
        prompt=prompt,
        aspect_ratio=aspect_ratio,
    )


async def _upload_image_ref_from_file_id(
    message: types.Message,
    *,
    user_id: int,
    status_msg: types.Message,
    file_id: str,
    prompt: str,
    aspect_ratio: str,
) -> ImageRef | None:
    try:
        buf = await bot.download(file_id)
        data = buf.read() if hasattr(buf, "read") else bytes(buf)
    except Exception:
        log.exception("download user photo failed")
        await status_msg.edit_text("❌ Не удалось получить ваше фото.")
        return None

    acc_id = _account_for_image(user_id, prefer_image_only=True)
    if acc_id is None:
        await status_msg.edit_text(flow_copy.msg("accounts_unavailable"))
        return None
    project_id = await ensure_user_project(user_id, account_id=acc_id)
    try:
        source = await _keeper_for_acc(acc_id).upload_image(data, filename=f"tg_{user_id}.png", project_id=project_id)
    except Exception:
        log.exception("upload_image failed")
        source = None

    if not source or not source.get("mediaId"):
        await status_msg.edit_text(flow_copy.msg("upload_failed"))
        return None

    source.setdefault("_tg_file_id", file_id)
    upload_project = source.pop("_project_id", None) or project_id
    return ImageRef(
        user_id=user_id,
        project_id=upload_project,
        source=source,
        prompt=prompt,
        aspect_ratio=aspect_ratio,
        account_id=acc_id,
    )


@dp.message(F.photo)
async def handle_photo(message: types.Message):
    """Пользователь прислал фото (+ опц. подпись).

    В явных режимах сохраняем контекст и показываем настройки/подтверждение;
    без выбранного режима фото+подпись сначала просит выбрать картинку или видео.
    """
    user_id = message.from_user.id
    st = _ws(user_id)
    vawait = st.get("vawait")
    caption = (message.caption or "").strip()

    # Альбом в видео-режимах: буферизуем и обрабатываем пачкой (см. _flush_album).
    mgid = message.media_group_id
    if mgid and vawait in ("ving_photo", "vfrm_start", "vfrm_end"):
        _album_buf.setdefault(mgid, []).append(message)
        task = _album_tasks.get(mgid)
        if task:
            task.cancel()
        _album_tasks[mgid] = asyncio.create_task(_flush_album(mgid, user_id))
        return

    if st.get("support_await"):
        await message.answer(
            "🙌 Сначала пришли текстовый бриф одним сообщением: товар, площадка, "
            "сколько слайдов и что важно показать. Фото добавим после заявки."
        )
        return

    # Явный режим «Изменить моё фото»: фото+подпись не генерит сразу, а открывает
    # подтверждение с настройками формата/модели.
    if st.get("await") == "photo":
        await _prepare_photo_edit_from_file_id(
            message,
            user_id=user_id,
            file_id=message.photo[-1].file_id,
            caption=caption,
            aspect_fmt=st.get("edit_fmt", DEFAULT_FMT),
            image_model=st.get("edit_imodel", DEFAULT_IMAGE_MODEL),
        )
        return

    # В «Создать картинку» фото с подписью тоже не должно обходить настройки.
    # Фото = референс к новому изображению → тариф генерации (10/15), не правки.
    if caption and (
        st.get("step") in ("prompt_picker", "wizard")
        or st.get("await") == "prompt"
        or st.get("pending_prompt")
    ):
        await _prepare_photo_edit_from_file_id(
            message,
            user_id=user_id,
            file_id=message.photo[-1].file_id,
            caption=caption,
            aspect_fmt=st.get("fmt", DEFAULT_FMT),
            image_model=st.get("imodel", DEFAULT_IMAGE_MODEL),
            as_generation=True,
        )
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
        if caption:
            st["vcaption_prompt"] = caption
        try:
            await status_msg.delete()
        except Exception:
            pass
        await show_video_frames(message, user_id=user_id, edit=False)
        return

    # Новый wizard: фото на шаге ввода промпта или на экране настроек.
    vstep = st.get("vstep")
    if vstep in ("vprompt_input", "vnewwiz"):
        await _animate_photo_scenario().attach_uploaded_photo(
            _TelegramAnimatePhotoContext(message, user_id),
            file_id=message.photo[-1].file_id,
            caption=caption,
        )
        return


    # Активны «Идеи и шаблоны»: фото остаётся внутри этой ветки (основа для
    # image/edit или референс для video), а не уходит в общий выбор image/video.
    if st.get("tp_tpl") or "gp_step" in st or st.get("ideas_mode") in ("root", "templates", "guided"):
        await _template_photo_received(message, user_id=user_id)
        return

    # Video wizard expects text, not a photo.
    if vawait in ("vprompt", "vedit_prompt", "vextend_prompt"):
        await message.answer(flow_copy.msg("vid_text_only_hint"))
        return

    # Текстовый видео-визард (omni/veo без референсов) — юзер прислал фото вместо текста.
    # Если есть подпись — берём её как промпт и стартуем видео. Без подписи — напоминаем.
    if _video_plain_text_ready(st):
        if caption:
            await _video_generate_and_send(message, caption, user_id=user_id)
        else:
            await message.answer(flow_copy.msg("vid_text_only_hint"))
        return

    if st.get("await") == "mp_video_photo":
        plat = st.get("mp_platform", "wb")
        caption_text = caption
        prompt = _mp_video_prompt(
            plat,
            caption_text,
            brand_kit=_mp_brand_kit(user_id),
            niche=_mp_niche(user_id),
        )
        metrics.log_event("mp_video_photo_uploaded", user_id=user_id, source=f"{plat}:animate")
        ok = await _seller_video_from_photo(
            message, prompt, aspect_ratio="portrait", user_id=user_id,
            video_model=st.get("vmodel") or VID_REF_DEFAULT_MODEL,
        )
        if ok:
            st["await"] = None
            pending_edits.pop(user_id, None)
        return

    if st.get("await") == "mp_series_photo":
        plat = st.get("mp_platform", "wb")
        count = st.get("mp_series_count", 3)
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 3
        if count not in _MP_SERIES_COUNTS:
            count = 3
        caption_text = caption
        prompt = _mp_series_prompt(
            plat,
            count,
            caption_text,
            brand_kit=_mp_brand_kit(user_id),
            niche=_mp_niche(user_id),
        )
        metrics.log_event("mp_series_photo_uploaded", user_id=user_id, source=f"{plat}:{count}")
        if _cfg.IS_SELLER:
            # Не генерируем сразу: показываем подтверждение с ценой, генерим по кнопке.
            st["mp_pending_file_id"] = message.photo[-1].file_id
            st["mp_pending_caption"] = caption_text
            st["mp_pending_kind"] = "series"
            st["await"] = None
            ctext, ckb = _mp_confirm_screen(user_id)
            _sent = await message.answer(ctext, reply_markup=ckb, parse_mode="HTML")
            _mp_stamp_message(user_id, _sent)  # чтобы stale-guard не отшил «Создать»
            return
        status_msg = await message.answer(flow_copy.msg("uploading_photo"))
        ref = await _upload_image_ref_from_photo_message(
            message,
            user_id=user_id,
            status_msg=status_msg,
            prompt=prompt,
            aspect_ratio=_mp_platform_aspect(plat),
        )
        if not ref:
            return
        try:
            await status_msg.delete()
        except Exception:
            pass
        ok = await _run_i2i(
            message,
            ref,
            prompt,
            num_images=count,
            emoji="🧩",
            action="mp_series",
            fail_text=flow_copy.msg("nothing_returned"),
        )
        if ok:
            st["await"] = None
            st.pop("mp_series_count", None)
            pending_edits.pop(user_id, None)
        return

    if st.get("await") == "mp_photo":
        plat = st.get("mp_platform", "wb")
        job = st.get("mp_preset", "whitebg")
        caption_text = caption
        instruction = _mp_job_instruction(
            job,
            plat,
            caption_text,
            brand_kit=_mp_brand_kit(user_id),
            niche=_mp_niche(user_id),
        )
        metrics.log_event("mp_photo_uploaded", user_id=user_id, source=f"{plat}:{job}")
        if _cfg.IS_SELLER:
            # Не генерируем сразу: показываем подтверждение с ценой, генерим по кнопке.
            st["mp_pending_file_id"] = message.photo[-1].file_id
            st["mp_pending_caption"] = caption_text
            st["mp_pending_kind"] = "photo"
            st["await"] = None
            ctext, ckb = _mp_confirm_screen(user_id)
            _sent = await message.answer(ctext, reply_markup=ckb, parse_mode="HTML")
            _mp_stamp_message(user_id, _sent)  # чтобы stale-guard не отшил «Создать»
            return
        status_msg = await message.answer(flow_copy.msg("uploading_photo"))
        ref = await _upload_image_ref_from_photo_message(
            message,
            user_id=user_id,
            status_msg=status_msg,
            prompt=instruction,
            aspect_ratio=_mp_platform_aspect(plat),
        )
        if not ref:
            return
        try:
            await status_msg.delete()
        except Exception:
            pass
        token = image_registry.add(ref)
        pending_edits[user_id] = token
        st["await"] = "edit"
        st["edit_fmt"] = _mp_platform_fmt(plat)
        st["edit_imodel"] = st.get("edit_imodel", DEFAULT_IMAGE_MODEL)
        ok = await _edit_and_send(
            message,
            ref,
            instruction,
            aspect_ratio=_mp_platform_aspect(plat),
            image_model=st.get("edit_imodel", DEFAULT_IMAGE_MODEL),
        )
        if ok:
            st["await"] = None
            pending_edits.pop(user_id, None)
        return

    if caption:
        await _offer_photo_route_choice(message, user_id=user_id, caption=caption)
        return

    # Без подписи — запоминаем как «текущую картинку для правки».
    await _prepare_photo_edit_from_file_id(
        message,
        user_id=user_id,
        file_id=message.photo[-1].file_id,
        caption="",
    )


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_plain_text(message: types.Message):
    """Текст без команды: кнопки нижнего меню, ответ визарду или прямая генерация."""
    user_id = message.from_user.id
    metrics.upsert_user(user_id, username=_username(message),
                        first_name=getattr(message.from_user, "first_name", None))
    text = message.text.strip()
    st = _ws(user_id)

    # Постоянная нижняя клавиатура: её нажатия приходят как обычный текст.
    if text == L("kb_gen"):
        _reset_image_flow(user_id)  # чистит и pending_edits (залипшее фото)
        await show_prompt_picker(message, user_id=user_id, edit=False)
        return
    if text == L("kb_menu"):
        pending_edits.pop(user_id, None)
        pending_photo_routes.pop(user_id, None)
        st["await"] = None
        await show_main_menu(message, user_id=user_id, ensure_kb=True)
        return
    if _is_balance_reply_text(text):
        await show_balance(message, user_id=user_id, edit=False)
        return
    if text == L("ideas"):
        _reset_image_flow(user_id)
        _vid_clear(user_id)
        await _show_ideas_root(message, user_id=user_id, edit=False)
        return
    if text == L("myphoto"):
        _reset_image_flow(user_id, keep_last=False)
        _ws(user_id)["await"] = "photo"
        await message.answer(flow_copy.msg("ask_photo"))
        return
    if text == L("invite"):
        await _show_referral_screen(message, user_id=user_id, edit=False)
        return
    if text == L("help"):
        await _show_help_screen(message, edit=False)
        return
    if text == L("kb_vid"):
        vlast = st.get("vlast")
        _vid_clear(user_id)
        pending_edits.pop(user_id, None)  # бросаем залипшее фото-правку при переходе в видео
        pending_photo_routes.pop(user_id, None)
        if vlast:
            st["vlast"] = vlast
        await show_video_prompt_input(message, user_id=user_id, edit=False)
        return

    # Свободный текстовый ответ в Q&A готового шаблона («Идеи и шаблоны»).
    if st.get("tp_await") == "text" and st.get("tp_tpl"):
        _tp_store_answer(st, text)
        await _render_template_step(message, user_id=user_id)
        return

    if st.get("tp_tpl"):
        st["ideas_extra_prompt"] = text
        await message.answer(flow_copy.msg("ideas_text_attached_template"))
        await _render_template_step(message, user_id=user_id)
        return

    if "gp_step" in st:
        st["gp_extra_prompt"] = text
        await message.answer(flow_copy.msg("ideas_text_attached_guided"))
        await _render_guided_step(message, user_id=user_id)
        return

    if st.get("ideas_mode") in ("root", "templates", "guided"):
        st["ideas_extra_prompt"] = text
        await message.answer(flow_copy.msg("ideas_text_attached_root"))
        if st.get("ideas_mode") == "templates":
            await message.answer(
                flow_copy.msg("ideas_templates_title"),
                reply_markup=_templates_picker_kb(),
                parse_mode="HTML",
            )
        else:
            await _show_ideas_root(message, user_id=user_id, edit=False)
        return

    # Промпт-правка для загруженного пользователем видео.
    if st.get("vawait") == "vu_edit_prompt" and st.get("vu_source"):
        if len(text) < 3:
            await message.answer(flow_copy.msg("vid_prompt_too_short"))
            return
        await _video_edit_uploaded(message, text, user_id=user_id)
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

    # Ретрай после модерации: пользователь прислал новый промпт — генерим с теми
    # же настройками/фото, восстановленными из снимка кнопкой «Изменить промпт».
    if st.get("vawait") == "vretry_prompt":
        st["vawait"] = None
        await _video_generate_and_send(message, text, user_id=user_id)
        return

    # «Оживить фото» from the main menu requires a photo. Text is saved as the
    # future scenario, but generation cannot proceed without an image reference.
    if st.get("vawait") == "vanimate_photo":
        await _animate_photo_scenario().remember_prompt_until_photo(
            _TelegramAnimatePhotoContext(message, user_id),
            text,
        )
        return

    # Новый wizard: пользователь ввёл описание (шаг 1).
    if st.get("vstep") == "vprompt_input":
        st["vprompt"] = text
        await show_new_video_wizard(message, user_id=user_id, edit=True)
        return

    # Новый wizard: пользователь отредактировал промпт на экране настроек.
    if st.get("vstep") == "vnewwiz" and st.get("vawait") == "vnchange":
        st["vawait"] = None
        st["vprompt"] = text
        await show_new_video_wizard(message, user_id=user_id, edit=False)
        return

    # Видео-визард ждёт промпт.
    if st.get("vawait") == "vprompt":
        if message.photo:
            await message.answer(flow_copy.msg("vid_text_only_hint"))
            return
        st["vawait"] = None
        await _video_generate_and_send(message, text, user_id=user_id)
        return

    # «Оживить фото» / видео-из-фото: фото(и) уже выбраны (через кнопку или
    # загрузку) — промпт из чата запускает генерацию сразу, как «Готово» + промпт.
    # Кнопка «Готово» остаётся опциональной. Проверяем ДО image-фолбэка, иначе
    # залипший image-визард перехватил бы текст и сгенерил картинки.
    # ВАЖНО: этот блок должен быть ВЫШЕ проверки vawait == "ving_photo", иначе
    # show_video_ingredients (которая всегда выставляет vawait=ving_photo) блокирует
    # текстовый промпт даже когда фото уже добавлены («Оживить фото» не работает).
    if st.get("vmode") == "ingredients" and (st.get("ving_photos") or []):
        await _animate_photo_scenario().generate_from_ready_references(
            _TelegramAnimatePhotoContext(message, user_id),
            text,
        )
        return

    if st.get("vawait") == "ving_photo":
        await message.answer(flow_copy.msg("vid_ing_send_photo"))
        return

    if st.get("vawait") == "vfrm_start":
        await message.answer(flow_copy.msg("vid_frm_send_photo_start"))
        return

    if st.get("vawait") == "vfrm_end":
        await message.answer(flow_copy.msg("vid_frm_send_photo_end"))
        return

    # Видео по двум кадрам: оба кадра загружены — промпт из чата запускает генерацию.
    if st.get("vmode") == "frames" and st.get("vfrm_start") and st.get("vfrm_end"):
        st["vawait"] = None
        await _video_generate_and_send(message, text, user_id=user_id)
        return

    if _video_plain_text_ready(st):
        st["vawait"] = None
        await _video_generate_and_send(message, text, user_id=user_id)
        return

    awaiting = st.get("await")

    # ─── Поддержка: пользователь вводит сообщение для нового тикета ───────────
    if st.get("support_await"):
        st.pop("support_await", None)
        support_kind = st.pop("support_kind", "support")
        plat = st.get("mp_platform")
        ticket_text = text
        admin_title = "🎫 Тикет"
        if support_kind == "mp_done4you":
            platform_name = _MP_PLAT_NAMES.get(plat or "", plat or "не выбрана")
            ticket_text = f"🙌 Заявка под ключ\nПлощадка: {platform_name}\n\n{text}"
            admin_title = "🙌 Заявка под ключ"
            metrics.log_event("mp_done4you_submitted", user_id=user_id, source=plat or "seller")
        ticket_id = metrics.create_ticket(user_id, username=_username(message), text=ticket_text)
        # Пересылаем администратору
        admin_id = ADMIN_IDS[0] if ADMIN_IDS else None
        if admin_id:
            reply_btn = types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text=f"📝 Ответить #{ticket_id}", callback_data=f"m:sreply:{ticket_id}")]
            ])
            try:
                fwd = await message.bot.send_message(
                    admin_id,
                    f"{admin_title} #{ticket_id} от @{_username(message) or user_id}:\n\n{ticket_text}",
                    reply_markup=reply_btn,
                )
                metrics.set_ticket_admin_msg(ticket_id, fwd.message_id)
            except Exception as exc:
                log.warning(f"Не удалось переслать тикет #{ticket_id} админу: {exc}")
        back_kb = types.InlineKeyboardMarkup(inline_keyboard=[[_menu_button("menu", "m:menu")]])
        await message.answer(flow_copy.msg("support_submitted", ticket_id=ticket_id), reply_markup=back_kb)
        return

    # ─── Ответ администратора на тикет ──────────────────────────────────────
    if user_id in ADMIN_IDS and st.get("admin_reply_ticket"):
        ticket_id = st.pop("admin_reply_ticket")
        ticket_row = metrics.reply_ticket(ticket_id, reply_text=text)
        if ticket_row:
            try:
                await message.bot.send_message(
                    ticket_row["user_id"],
                    flow_copy.msg("support_reply", ticket_id=ticket_id, reply=text),
                )
                await message.answer(f"✅ Ответ на тикет #{ticket_id} отправлен пользователю.")
            except Exception as exc:
                await message.answer(f"⚠️ Ответ записан, но не доставлен: {exc}")
        else:
            await message.answer(f"⚠️ Тикет #{ticket_id} не найден.")
        return

    # Промокод: ждём ввода кода после /promo без аргумента.
    if awaiting == "promo":
        st["await"] = None
        code_raw = text.strip()
        credits_got = metrics.redeem_promo(code_raw, user_id)
        if credits_got is None:
            await message.answer(flow_copy.msg("promo_invalid"))
        else:
            balance = credit_store.add(user_id, credits_got)
            metrics.log_event("promo_redeemed", user_id=user_id, payload={"code": code_raw.upper(), "credits": credits_got})
            uname = _username(message)
            uname_str = f"@{uname}" if uname else f"id {user_id}"
            asyncio.create_task(_send_owner_alert(
                f"🎟 <b>Промокод активирован</b>\n"
                f"Юзер: {uname_str}\n"
                f"Код: <code>{code_raw.upper()}</code>  +{credits_got} кр."
            ))
            await message.answer(
                flow_copy.msg("promo_success", credits=credits_got, balance=balance),
                parse_mode="HTML",
            )
        return

    # Photo-edit entry is waiting for an upload; plain text must not open the image wizard.
    if awaiting == "photo":
        await message.answer(flow_copy.msg("ask_photo"))
        return
    if awaiting == "mp_photo":
        await message.answer(
            _mp_photo_request_text(st.get("mp_platform", "wb"), st.get("mp_preset", "whitebg")),
            reply_markup=_mp_back_kb(),
            parse_mode="HTML",
        )
        return
    if awaiting == "mp_series_photo":
        count = st.get("mp_series_count", 3)
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 3
        if count not in _MP_SERIES_COUNTS:
            count = 3
        await message.answer(
            _mp_series_request_text(st.get("mp_platform", "wb"), count),
            reply_markup=_mp_back_kb(),
            parse_mode="HTML",
        )
        return
    if awaiting == "mp_sku_name":
        sku = text.strip()
        if len(sku) < 2:
            await message.answer("📦 Название SKU слишком короткое. Пришли артикул или название товара.")
            return
        if _pending_sku_payload(user_id):
            await _save_pending_sku_item(message, user_id, sku)
            return
        ok = metrics.create_seller_sku_project(user_id, sku, platform=st.get("mp_platform"))
        st["await"] = None
        if not ok:
            await message.answer("Не удалось создать SKU. Проверь название и попробуй ещё раз.")
            return
        st["mp_sku_open"] = sku
        metrics.log_event("mp_sku_created", user_id=user_id, source=str(st.get("mp_platform") or "seller"))
        await message.answer(
            _mp_sku_open_text(user_id, sku),
            reply_markup=_mp_sku_open_kb(),
            parse_mode="HTML",
        )
        return
    if awaiting == "mp_sku_rename":
        new_sku = text.strip()
        old_sku = str(st.get("mp_sku_open") or "").strip()
        if len(new_sku) < 2:
            await message.answer("📦 Новое название слишком короткое. Пришли артикул или название товара.")
            return
        if not old_sku:
            st["await"] = None
            await message.answer("Сначала открой SKU в разделе «Мои товары».")
            return
        ok = metrics.rename_seller_sku_project(user_id, old_sku, new_sku)
        st["await"] = None
        if not ok:
            await message.answer("Не удалось переименовать SKU. Попробуй ещё раз позже.")
            return
        st["mp_sku_open"] = new_sku
        metrics.log_event("mp_sku_renamed", user_id=user_id, source="seller")
        await message.answer(
            _mp_sku_open_text(user_id, new_sku),
            reply_markup=_mp_sku_open_kb(),
            parse_mode="HTML",
        )
        return
    if awaiting == "mp_brandkit":
        brand = text.strip()
        if len(brand) < 5:
            await message.answer("🎨 Опиши бренд-кит чуть подробнее: цвета, стиль и что важно сохранить.")
            return
        ok = metrics.save_seller_profile(user_id, brand_kit=brand)
        st["await"] = None
        metrics.log_event("mp_brandkit_saved", user_id=user_id, source="seller")
        if ok:
            await message.answer(
                "🎨 Бренд-кит сохранён. Теперь seller-карточки и серии будут учитывать этот стиль.",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="🛒 Маркетплейсы", callback_data="m:mp")],
                    [_menu_button("menu", "m:menu")],
                ]),
            )
        else:
            await message.answer("Не удалось сохранить бренд-кит. Попробуй ещё раз позже.")
        return

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
            if awaiting == "revary":
                ok = await _run_i2i(
                    message, ref, text, num_images=2, emoji="🎲",
                    fail_text=flow_copy.msg("nothing_returned"),
                )
                if ok:
                    st["await"] = None
                    pending_edits.pop(user_id, None)
                return
            # Edit: show a confirm screen so the user can улучшить запрос (agent)
            # or apply it as-is — instead of generating immediately.
            st["edit_instruction"] = text
            st["await"] = "edit_confirm"
            await show_edit_confirm(message, user_id=user_id, edit=False)
            return
        st["await"] = None
        pending_edits.pop(user_id, None)
        await message.answer(flow_copy.msg("expired"))
        await show_main_menu(message, user_id=user_id)
        return

    # На экране подтверждения правки новый текст = новый запрос правки.
    if awaiting == "edit_confirm":
        st["edit_instruction"] = text
        st.pop("ag_variants", None)
        await show_edit_confirm(message, user_id=user_id, edit=False)
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

    # Пользователь на экране идей (шаг 1) и ввёл свой промпт — переходим к настройкам.
    if st.get("step") == "prompt_picker":
        if len(text) < 3:
            await message.answer(flow_copy.msg("prompt_too_short"))
            return
        st["pending_prompt"] = text
        st["step"] = "wizard"
        picker_msg_id = st.get("picker_msg_id")
        if picker_msg_id:
            kb = wizard_kb(st.get("count", DEFAULT_COUNT), st.get("fmt", DEFAULT_FMT), st.get("imodel", DEFAULT_IMAGE_MODEL), show_improve=True)
            wtext = _wizard_text(user_id)
            try:
                await message.bot.edit_message_text(
                    wtext,
                    chat_id=message.chat.id,
                    message_id=picker_msg_id,
                    reply_markup=kb,
                    parse_mode="HTML",
                )
                return
            except Exception:
                pass  # fallback: send new message below
        await show_wizard(message, user_id=user_id, edit=False)
        return

    # Seller-бот — это инструмент для карточек, а не свободный генератор. Случайный
    # текст НЕ должен открывать платный image-визард: подсказываем выбрать задачу.
    if _cfg.IS_SELLER:
        await message.answer(
            "🛒 Я делаю карточки для маркетплейсов. Выбери задачу в меню "
            "«Карточки» и пришли фото товара — там подберём формат и стиль.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🛒 Карточки", callback_data="m:mp")],
                [_menu_button("menu", "m:menu")],
            ]),
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


def _install_shutdown_exception_filter() -> None:
    loop = asyncio.get_running_loop()
    default_handler = loop.get_exception_handler()

    def _handler(loop, context):
        exc = context.get("exception")
        message = context.get("message", "")
        if (
            message == "Future exception was never retrieved"
            and exc is not None
            and "Connection closed while reading from the driver" in str(exc)
        ):
            log.debug("Suppressed Playwright driver shutdown future: %s", exc)
            return
        if default_handler is not None:
            default_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)


async def _main_impl():
    log.info("🚀 Запуск Flow Bot...")
    _install_shutdown_exception_filter()

    if TELEGRAM_TOKEN == "PASTE_YOUR_TOKEN_HERE":
        log.error("❌ Укажите TELEGRAM_TOKEN в .env или прямо в коде!")
        return

    robokassa_runner = None
    warmup_tasks: list[asyncio.Task] = []
    try:
        _startup_set_phase("web_starting")
        try:
            robokassa_runner = await _start_robokassa_web_server()
            _startup_set_phase("web_ready")
        except Exception as exc:
            _startup_set_phase("web_failed", error=exc.__class__.__name__)
            log.exception("Robokassa callback server failed to start")

        # Узнаём собственный @username — нужен для реферальных deep-link.
        global BOT_USERNAME
        try:
            me = await bot.get_me()
            if me.username:
                BOT_USERNAME = me.username
        except Exception:
            log.warning("get_me failed; referral links use the env fallback")

        # Нативное меню команд Telegram (синяя кнопка «Меню» у поля ввода).
        if _cfg.IS_SELLER:
            _bot_commands = [
                types.BotCommand(command="start", description="Запуск и меню"),
                types.BotCommand(command="menu", description="🛒 Карточки для маркетплейсов"),
                types.BotCommand(command="balance", description="💳 Баланс и пополнение"),
                types.BotCommand(command="help", description="Как пользоваться"),
            ]
        else:
            _bot_commands = [
                types.BotCommand(command="start", description="Запуск и главное меню"),
                types.BotCommand(command="menu", description="🏠 Главное меню"),
                types.BotCommand(command="balance", description="💳 Баланс и пополнение"),
                types.BotCommand(command="ideas", description="Ideas and templates"),
                types.BotCommand(command="help", description="How to use the bot"),
                types.BotCommand(command="referral", description="Invite a friend"),
            ]
        try:
            await bot.set_my_commands(_bot_commands)
        except Exception:
            log.warning("Не удалось установить меню команд")

        if _cfg.IS_SELLER:
            log.info("🛒 Seller mode: пропускаю прогрев пула (генерация через основной бот)")
            startup_state["min_ready"] = 0
            for acc_id in keepers:
                account_pool.set_runtime_ready(acc_id, True, "skipped")
                _startup_set_account(acc_id, "skipped", ready=True)
            _startup_set_phase("ready", ready_accounts=len(keepers))
        else:
            try:
                warmup_concurrency = max(1, int(os.getenv("WARMUP_CONCURRENCY", "3")))
            except (TypeError, ValueError):
                warmup_concurrency = 3
            warmup_sem = asyncio.Semaphore(warmup_concurrency)
            total_accounts = len(keepers)
            min_ready = min(max(1, MIN_READY_ACCOUNTS), total_accounts)
            startup_state["min_ready"] = min_ready
            startup_state["total_accounts"] = total_accounts
            ready_event = asyncio.Event()
            warm_started = time.time()
            ready_count = 0
            completed_count = 0

            for acc_id in keepers:
                account_pool.set_runtime_ready(acc_id, False, "warming")
                _startup_set_account(acc_id, "pending", ready=False)
            _startup_set_phase("warming")

            async def _warm_account(acc_id: str, kp: "SessionKeeper") -> bool:
                nonlocal ready_count, completed_count
                ok = False
                async with warmup_sem:
                    try:
                        account_pool.set_runtime_ready(acc_id, False, "warming")
                        _startup_set_account(acc_id, "running", ready=False)
                        log.info("🌐 Запускаю аккаунт пула: %s", acc_id)
                        await kp.start()
                        ok = True
                        ready_count += 1
                        account_pool.set_runtime_ready(acc_id, True, "ready")
                        _startup_set_account(acc_id, "ready", ready=True)
                    except Exception as exc:
                        log.exception("❌ Аккаунт %s не стартовал — отключаю в пуле", acc_id)
                        account_pool.set_runtime_ready(acc_id, False, "failed")
                        account_pool.set_disabled(acc_id, True)
                        _startup_set_account(acc_id, "failed", ready=False, error=exc.__class__.__name__)
                    finally:
                        completed_count += 1
                        if ready_count >= min_ready or completed_count >= total_accounts:
                            ready_event.set()
                return ok

            warmup_tasks = [
                asyncio.create_task(_warm_account(acc_id, kp))
                for acc_id, kp in keepers.items()
            ]
            await ready_event.wait()
            if ready_count < min_ready:
                await asyncio.gather(*warmup_tasks, return_exceptions=True)
                _startup_set_phase("blocked", ready_accounts=ready_count)
                log.error("❌ Прогрев пула не достиг threshold %d/%d — выходим.", ready_count, min_ready)
                return
            _startup_set_phase("ready_threshold_met", ready_accounts=ready_count)
            log.info(
                "🌐 Прогрев threshold: %d/%d аккаунтов за %.1f c (минимум %d, лимит %d)",
                ready_count, total_accounts, time.time() - warm_started,
                min_ready, warmup_concurrency,
            )

        log.info("🤖 Бот запущен!")
        asyncio.create_task(_daily_digest_loop())
        if not _cfg.IS_SELLER:
            asyncio.create_task(_video_pool_health_loop())
        startup_state["polling"] = True
        _startup_set_phase("polling")
        await dp.start_polling(bot)
    finally:
        startup_state["polling"] = False
        _startup_set_phase("stopping")
        log.info("Shutting down bot resources...")
        for task in warmup_tasks:
            if not task.done():
                task.cancel()
        if warmup_tasks:
            await asyncio.gather(*warmup_tasks, return_exceptions=True)
        if robokassa_runner is not None:
            try:
                await robokassa_runner.cleanup()
            except Exception:
                log.exception("Robokassa callback server cleanup failed")
        for acc_id, kp in keepers.items():
            try:
                await kp.close()
            except Exception:
                log.exception("Flow account %s cleanup failed", acc_id)


_DIGEST_INTERVAL_H = 6    # Как часто проверяем (не чаще чем раз в N часов)
_DIGEST_HOUR = 12         # Целевой час UTC для отправки (12:00 UTC = 15:00 MSK)
_DIGEST_BATCH = 80        # Юзеров за один прогон (throttle)
_DIGEST_DELAY_S = 0.5     # Пауза между отправками (не спамим Telegram API)


async def _daily_digest_loop() -> None:
    """Фоновый луп: раз в 6 часов шлём дайджест пользователям, неактивным 3-7 дней."""
    import random as _random
    while True:
        try:
            now_h = __import__("datetime").datetime.utcnow().hour
            # Отправляем только в окно 11:00–13:00 UTC (гибко).
            if abs(now_h - _DIGEST_HOUR) <= 1:
                users = metrics.get_users_for_digest(min_days=3, max_days=7, limit=_DIGEST_BATCH)
                log.info("📨 Дайджест: найдено %d кандидатов", len(users))
                ideas = list(_QUICK_IDEAS)
                for entry in users:
                    uid = entry["user_id"]
                    try:
                        bal = credit_store.balance(uid)
                        if bal <= 0:
                            text = flow_copy.msg("digest_nudge_empty")
                        else:
                            idea = _random.choice(ideas)
                            text = flow_copy.msg("digest_nudge", idea=idea)
                        kb = types.InlineKeyboardMarkup(inline_keyboard=[
                            [types.InlineKeyboardButton(text="🎨 Создать", callback_data="m:gen")],
                            [types.InlineKeyboardButton(text="🏠 Меню", callback_data="m:menu")],
                        ])
                        await bot.send_message(uid, text, reply_markup=kb, parse_mode="HTML")
                        metrics.log_event("digest_sent", user_id=uid)
                        await asyncio.sleep(_DIGEST_DELAY_S)
                    except Exception as exc:
                        log.debug("Дайджест не доставлен uid=%s: %s", uid, exc)
        except Exception:
            log.warning("_daily_digest_loop iteration failed", exc_info=True)
        # Спим 6 часов до следующей проверки
        await asyncio.sleep(_DIGEST_INTERVAL_H * 3600)


_VIDEO_POOL_CHECK_INTERVAL_S = 300   # как часто проверяем здоровье видео-пула
_VIDEO_POOL_MIN_SCORE = 10           # ниже — аккаунт считаем «нездоровым»
_pool_degraded_alerted = False       # шлём алерт один раз на переход состояния


async def _video_pool_health_loop() -> None:
    """Фоновый монитор: алерт владельцу, когда не осталось ни одного здорового
    video-аккаунта — РАНЬШЕ, чем юзеры начнут ловить отказы. Алерт шлём один раз
    на переход (degraded ↔ recovered), чтобы не спамить."""
    global _pool_degraded_alerted
    await asyncio.sleep(120)  # дать прогреву устаканиться
    while True:
        try:
            scores = _video_scores_for_model("omni-flash-4s")
            usable = [a for a in account_pool.account_ids() if account_pool.is_video_capable(a)]
            # «Нездоров» = есть скор и он ниже порога; без данных = нейтрально (ок).
            healthy = [
                a for a in usable
                if not (a in scores and (scores[a].get("score") or 0) < _VIDEO_POOL_MIN_SCORE)
            ]
            if not healthy:
                if not _pool_degraded_alerted:
                    _pool_degraded_alerted = True
                    detail = ", ".join(
                        f"{a}:{round((scores.get(a, {}).get('score') or 0), 1)}" for a in usable
                    ) or "нет video-capable аккаунтов"
                    await _send_owner_alert(
                        "⚠️ <b>Видео-пул деградировал</b>\n"
                        f"Здоровых video-аккаунтов: 0 из {len(usable)} доступных.\n"
                        f"Score: {detail}\n"
                        "Видео-запросы юзеров начнут падать — проверь аккаунты/прокси."
                    )
                    metrics.log_event("video_pool_degraded", source="monitor",
                                      payload={"usable": len(usable)})
            elif _pool_degraded_alerted:
                _pool_degraded_alerted = False
                await _send_owner_alert(
                    f"✅ Видео-пул восстановлен: здоровых аккаунтов {len(healthy)}."
                )
        except Exception:
            log.warning("_video_pool_health_loop iteration failed", exc_info=True)
        await asyncio.sleep(_VIDEO_POOL_CHECK_INTERVAL_S)


# Tail fallback for unmatched callback queries (Phase 6). Must stay the LAST
# include_router call in this module: aiogram matches included routers in
# inclusion order, so every future extracted callback router has to be
# included above this line. Preserves the old dp-level catch-all behaviour
# for unknown callbacks: expired/legacy buttons get a silent ack instead of
# an endless spinner.
dp.include_router(
    tg_ideas_hub_router.create_router(
        tg_ideas_hub_router.IdeasHubDeps(
            workspace=_ws,
            show_ideas_root=_show_ideas_root,
            edit_or_answer=_edit_or_answer,
            render_guided_step=_render_guided_step,
        )
    )
)
dp.include_router(
    tg_ideas_flow_router.create_router(
        tg_ideas_flow_router.IdeasFlowDeps(
            workspace=_ws,
            ideas_clear=_ideas_clear,
            tp_store_answer=_tp_store_answer,
            show_ideas_root=_show_ideas_root,
            render_template_step=_render_template_step,
            render_guided_step=_render_guided_step,
            log_event=metrics.log_event,
        )
    )
)
dp.include_router(
    tg_agent_router.create_router(
        tg_agent_router.AgentDeps(
            workspace=_ws,
            agent_pick=_agent_pick,
            agent_improve_flow=_agent_improve_flow,
            show_new_video_wizard=show_new_video_wizard,
            show_wizard=show_wizard,
            show_edit_confirm=show_edit_confirm,
            video_edit=_vid_edit,
            edit_or_answer=_edit_or_answer,
            agent_edit_instruction=_agent_edit_instruction,
        )
    )
)
dp.include_router(
    tg_video_upload_router.create_router(
        tg_video_upload_router.VideoUploadDeps(
            workspace=_ws,
            upload_video_edit_enabled=lambda: _cfg.UPLOAD_VIDEO_EDIT_ENABLED,
            vid_clear=_vid_clear,
            edit_or_answer=_edit_or_answer,
        )
    )
)
dp.include_router(
    tg_edit_settings_router.create_router(
        tg_edit_settings_router.EditSettingsDeps(
            workspace=_ws,
            pending_edits=pending_edits,
            image_registry=image_registry,
            edit_and_send=_edit_and_send,
            fmt_to_aspect=_fmt_to_aspect,
            aspect_to_fmt=_aspect_to_fmt,
            image_model_meta=image_model_meta,
            edit_confirm_kb=edit_confirm_kb,
            edit_settings_kb=edit_settings_kb,
            default_fmt=DEFAULT_FMT,
            default_image_model=DEFAULT_IMAGE_MODEL,
        )
    )
)
dp.include_router(
    tg_animate_router.create_router(
        tg_animate_router.AnimateDeps(
            image_registry=image_registry,
            animate_photo_scenario=_animate_photo_scenario,
            context_factory=lambda msg, user_id: _TelegramAnimatePhotoContext(msg, user_id),
        )
    )
)
dp.include_router(
    tg_wizard_router.create_router(
        tg_wizard_router.WizardDeps(
            workspace=_ws,
            clamp_num_images=clamp_num_images,
            image_model_meta=image_model_meta,
            reset_image_flow=_reset_image_flow,
            show_main_menu=show_main_menu,
            show_wizard=show_wizard,
            show_prompt_picker=show_prompt_picker,
            boost_prompt_with_gemini=_boost_prompt_with_gemini,
            generate_and_send=_generate_and_send,
            fmt_to_aspect=_fmt_to_aspect,
            log_event=metrics.log_event,
            quick_ideas=_QUICK_IDEAS,
            default_count=DEFAULT_COUNT,
            default_fmt=DEFAULT_FMT,
            default_image_model=DEFAULT_IMAGE_MODEL,
        )
    )
)
dp.include_router(
    tg_marketplace_router.create_router(
        tg_marketplace_router.MarketplaceDeps(
            workspace=_ws,
            pending_edits=pending_edits,
            metrics=metrics,
            is_seller=lambda: _cfg.IS_SELLER,
            product_photo_jobs=_MP_PRODUCT_PHOTO_JOBS,
            mp_is_stale_callback=_mp_is_stale_callback,
            mp_reject_stale_callback=_mp_reject_stale_callback,
            mp_stamp_message=_mp_stamp_message,
            mp_job_instruction=_mp_job_instruction,
            mp_series_prompt=_mp_series_prompt,
            seller_i2i_from_file_id=_seller_i2i_from_file_id,
            show_sku_projects=_show_sku_projects,
            pending_sku_payload=_pending_sku_payload,
            latest_sku_payload=_latest_sku_payload,
            save_sku_payload=_save_sku_payload,
            save_pending_sku_item=_save_pending_sku_item,
            reset_image_flow=_reset_image_flow,
            vid_clear=_vid_clear,
            clear_image_flow_keys=_clear_image_flow_keys,
            show_video_ingredients=show_video_ingredients,
        )
    )
)
dp.include_router(
    tg_menu_router.create_router(
        tg_menu_router.MenuDeps(
            workspace=_ws,
            pending_edits=pending_edits,
            pending_photo_routes=pending_photo_routes,
            admin_ids=ADMIN_IDS,
            upsert_user=metrics.upsert_user,
            log_event=metrics.log_event,
            reset_image_flow=_reset_image_flow,
            clear_image_flow_keys=_clear_image_flow_keys,
            show_prompt_picker=show_prompt_picker,
            show_video_prompt_input=show_video_prompt_input,
            show_animate_photo_input=show_animate_photo_input,
            mp_jobs_text=_mp_jobs_text,
            mp_jobs_kb=mp_jobs_kb,
            mp_stamp_message=_mp_stamp_message,
            show_ideas_root=_show_ideas_root,
            repeat_last=_repeat_last,
            show_balance=show_balance,
            topup_copy=_topup_copy,
            topup_kb=topup_kb,
            topup_stars_kb=topup_stars_kb,
            topup_robo_kb=topup_robo_kb,
            robokassa_configured=_robokassa_configured,
            start_topup=_start_topup,
            start_robokassa_topup=_start_robokassa_topup,
            show_help_screen=_show_help_screen,
            show_referral_screen=_show_referral_screen,
            show_main_menu=show_main_menu,
            show_profile_screen=_show_profile_screen,
            show_gallery=_show_gallery,
            show_prompt_history=_show_prompt_history,
            show_support_menu=_show_support_menu,
            show_my_tickets=_show_my_tickets,
        )
    )
)
dp.include_router(
    tg_image_action_router.create_router(
        tg_image_action_router.ImageActionDeps(
            workspace=_ws,
            image_registry=image_registry,
            pending_edits=pending_edits,
            mix_baskets=mix_baskets,
            is_seller=lambda: _cfg.IS_SELLER,
            aspect_to_fmt=_aspect_to_fmt,
            edit_settings_kb=edit_settings_kb,
            mp_sku_choice_kb=_mp_sku_choice_kb,
            mp_stamp_message=_mp_stamp_message,
            vary_and_send=_vary_and_send,
            regen_and_send=_regen_and_send,
            enhance_and_send=_enhance_and_send,
            real_upscale_and_send=_real_upscale_and_send,
            send_original_file=_send_original_file,
            default_image_model=DEFAULT_IMAGE_MODEL,
            mix_max=MIX_MAX,
        )
    )
)
dp.include_router(
    tg_onboarding_router.create_router(
        tg_onboarding_router.OnboardingDeps(
            balance=credit_store.balance,
            show_main_menu=show_main_menu,
            reset_image_flow=_reset_image_flow,
            show_prompt_picker=show_prompt_picker,
            vid_clear=_vid_clear,
            show_video_prompt_input=show_video_prompt_input,
            workspace=_ws,
        )
    )
)
dp.include_router(
    tg_photo_route_router.create_router(
        tg_photo_route_router.PhotoRouteDeps(
            pending_photo_routes=pending_photo_routes,
            prepare_photo_edit_from_file_id=_prepare_photo_edit_from_file_id,
            prepare_photo_video_from_file_id=_prepare_photo_video_from_file_id,
        )
    )
)
dp.include_router(
    tg_image_retry_router.create_router(
        tg_image_retry_router.ImageRetryDeps(
            workspace=_ws,
            generate_and_send=_generate_and_send,
            default_image_model=DEFAULT_IMAGE_MODEL,
        )
    )
)
dp.include_router(tg_fallback_router.create_router())


async def main():
    try:
        await _main_impl()
    finally:
        log.info("Shutting down remaining bot resources...")
        for acc_id, kp in keepers.items():
            try:
                await kp.close()
            except Exception:
                log.exception("Flow account %s cleanup failed", acc_id)
        try:
            await bot.session.close()
        except Exception:
            log.exception("Telegram bot session cleanup failed")


if __name__ == "__main__":
    asyncio.run(main())
