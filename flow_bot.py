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

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
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
    fmt_to_aspect,
    aspect_to_fmt,
    aspect_to_vfmt,
)
import flow_copy
from generation import backend_service
from generation.prompt_boost import boost_prompt_with_gemini as _gemini_boost_prompt
import metrics
from mediautil import image_ext_from_bytes
from textutil import _days_word, _short_prompt, parse_ids as _parse_ids
from storage.session_state import reset_image_flow as _reset_image_flow
from product.scenarios.animate_photo import AnimatePhotoConfig, AnimatePhotoScenario
from product import video_reference
from product.video_delivery import video_delivery_bytes
from product.streak import streak_note
from channels.telegram.image_delivery import ImageDelivery, ImageDeliveryDeps
from channels.telegram.generation_flow import GenerationFlow, GenerationFlowDeps
from channels.telegram.video_flow import VideoFlow, VideoFlowDeps
from channels.telegram.seller_flow import SellerFlow, SellerFlowDeps
from channels.telegram.monitors import Monitors, MonitorsDeps
from channels.telegram.photo_intake import PhotoIntake, PhotoIntakeDeps
from channels.telegram.ideas_screens import IdeasScreens, IdeasScreensDeps
from channels.telegram.owner_alerts import OwnerAlerts, OwnerAlertsDeps
from channels.telegram.reference_routing import ReferenceRouting, ReferenceRoutingDeps
from channels.telegram.animate_photo import AnimatePhotoDeps, make_animate_photo_context_factory
from channels.telegram.robokassa_topup import RobokassaTopup, RobokassaTopupDeps
from channels.telegram.max_bootstrap import MaxBootstrap, MaxBootstrapDeps
from channels.telegram.bot_factory import make_bot, BotFactoryDeps
from channels.telegram.web_server import WebServer, WebServerDeps
from channels.telegram.stars_topup import StarsTopup, StarsTopupDeps
from channels.telegram.admin_help import AdminHelp, AdminHelpDeps, HELP_SECTIONS
from channels.telegram.renderer import edit_or_answer as _edit_or_answer
from channels.telegram.shutdown_filter import install_shutdown_exception_filter
from channels.telegram.marketplace_stale import MarketplaceStale, MarketplaceStaleDeps
from channels.telegram.marketplace_sku import MarketplaceSku, MarketplaceSkuDeps
from channels.telegram.agent_flow import (
    AgentFlow,
    AgentFlowDeps,
    agent_edit_instruction as _agent_edit_instruction,
)
from channels.telegram.photo_route_offer import PhotoRouteOffer, PhotoRouteOfferDeps
from channels.telegram.referral_flow import ReferralFlow, ReferralFlowDeps
from product.job_log import (
    ImageJobLogger,
    ms_since as _ms_since,
    seller_acc_tag as _seller_acc_tag,
)
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
    nwiz_engine as _nwiz_engine,
    nwiz_model as _nwiz_model,
    nwiz_price as _nwiz_price,
    _VID_FMT_TO_ASPECT,
    video_plain_text_ready as _video_plain_text_ready,
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
import billing.robokassa as robokassa_billing


# App/payment config extracted to config/settings.py (Phase 5 wave 2);
# re-exported so flow_bot keeps its references unchanged.
from config.settings import (
    DEFAULT_COUNT,
    DEFAULT_FMT,
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
from product.marketplace import (
    marketplace_export_filename,
    marketplace_export_caption,
)
from channels.telegram.keyboards import (
    main_menu_kb,
    reply_menu_kb as _telegram_reply_menu_kb,
    topup_kb,
    topup_method_kb,
    topup_stars_kb,
    topup_robo_kb,
    _include_test_packs,
    L,
    _menu_button,
    zero_balance_kb as _zero_balance_kb,
    _robokassa_configured,
    _balance_reply_label as _telegram_balance_reply_label,
    _is_balance_reply_text as _telegram_is_balance_reply_text,
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
from channels.telegram import screens as tg_screens
from channels.telegram.routers import commands as tg_commands_router
from channels.telegram.routers import payments as tg_payments_router
from channels.telegram.routers import menu as tg_menu_router
from channels.telegram.routers import marketplace as tg_marketplace_router
from channels.telegram.routers import onboarding as tg_onboarding_router
from channels.telegram.routers import photo_input as tg_photo_input_router
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
from channels.telegram.routers import video as tg_video_router
from channels.telegram.routers import generation_commands as tg_generation_commands_router
from channels.telegram.routers import admin_accounts as tg_admin_accounts_router
from channels.telegram.routers import admin_status as tg_admin_status_router
from channels.telegram.routers import admin_reports as tg_admin_reports_router
from channels.telegram.routers import admin_credits as tg_admin_credits_router
from channels.telegram.routers import video_upload_input as tg_video_upload_input_router
from channels.telegram.routers import plain_text as tg_plain_text_router
from channels.telegram.routers import start as tg_start_router
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
# OWNER_ID и ADMIN_IDS оба поддерживают НЕСКОЛЬКО ID через запятую/точку с запятой.
# _parse_ids вынесен в textutil.parse_ids (импортирован выше как _parse_ids).
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
    return make_bot(BotFactoryDeps(token=TELEGRAM_TOKEN, proxy_url=TG_PROXY_URL, log=log))


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

# Video account routing/health scoring moved to accounts/routing.py (Phase 11
# core split). Instantiated with the live pool + keepers so runtime state stays
# visible; the flow_bot-level names below stay as thin backward-compatible
# delegates.
from accounts.routing import VideoAccountRouter
from accounts.health import AccountFailurePolicy, is_rate_limit_error as _is_rate_limit_error

_video_router = VideoAccountRouter(
    account_pool=account_pool,
    keepers=keepers,
    metrics=metrics,
    video_model_meta=video_model_meta,
)

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
    return _video_router.cached_gcredits_hints()


def _video_family_for_model(model_id: str) -> str:
    return _video_router.family_for_model(model_id)


def _video_scores_for_model(model_id: str, min_credits: int = 0) -> dict:
    return _video_router.scores_for_model(model_id, min_credits)


def _video_account_health_reason(account_id: str | None, model_id: str, min_credits: int = 0) -> str | None:
    return _video_router.account_health_reason(account_id, model_id, min_credits)


def _account_for_video(user_id: int, *, model_id: str = "omni-flash-4s", min_credits: int = 0) -> str | None:
    """Аккаунт для видео-джобы — только среди video_capable, None — нет доступных."""
    return _video_router.account_for_video(user_id, model_id=model_id, min_credits=min_credits)


def _keeper_for_acc(account_id: str | None) -> SessionKeeper:
    return keepers.get(account_id or "", keeper)


def _client_for_acc(account_id: str | None) -> FlowHttpClient:
    return clients.get(account_id or "", client)


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
    _vid_clear,
    _clear_image_flow_keys,
    _vid_clear_reference_inputs,
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

def _referral_flow() -> ReferralFlow:
    return ReferralFlow(ReferralFlowDeps(
        bot_username=lambda: BOT_USERNAME,
        referral_param_prefix=REFERRAL_PARAM_PREFIX,
        referred_bonus=REFERRAL_REFERRED_BONUS,
        referral_service=_referral_service,
        metrics=metrics,
        bot=bot,
    ))


def _referral_link(user_id: int) -> str:
    """Личная реферальная ссылка пользователя (deep-link /start ref_<id>)."""
    return _referral_flow().link(user_id)


def _invite_button(user_id: int) -> types.InlineKeyboardButton:
    """Кнопка «поделиться» под результатом — открывает диалог пересылки."""
    return _referral_flow().invite_button(user_id)


def _public_screens_deps() -> tg_screens.PublicScreensDeps:
    return tg_screens.PublicScreensDeps(
        metrics=metrics,
        workspace=_ws,
        credit_store=credit_store,
        is_seller=lambda: _cfg.IS_SELLER,
        invite_button=_invite_button,
        referral_link=_referral_link,
        reply_menu_kb=reply_menu_kb,
        referral_referred_bonus=REFERRAL_REFERRED_BONUS,
        referral_tier1_bonus=REFERRAL_TIER1_BONUS,
        referral_tier2_bonus=REFERRAL_TIER2_BONUS,
        referral_tier3_bonus=REFERRAL_TIER3_BONUS,
        referral_ongoing_pct=REFERRAL_ONGOING_PCT,
    )


async def _show_referral_screen(message: types.Message, *, user_id: int, edit: bool) -> None:
    await tg_screens.show_referral_screen(
        message, user_id=user_id, edit=edit, deps=_public_screens_deps()
    )


def _maybe_apply_referral_rewards(
    referred_user_id: int, *, stars_paid: int, credits_issued: int,
    pack_id: str, provider_payment_id: str,
) -> None:
    """Reward the referrer for a referred user's payment (Phase 9 service)."""
    _referral_flow().maybe_apply_rewards(
        referred_user_id, stars_paid=stars_paid, credits_issued=credits_issued,
        pack_id=pack_id, provider_payment_id=provider_payment_id,
    )


def _first_referral_cta_text(user_id: int) -> str | None:
    """Invite CTA shown after every successful generation until user has referrals.

    Реферальные награды пригласившему начисляются ТОЛЬКО в tg-payments handler
    (anti-farm, REFERRAL.md §3) — здесь лишь зовём пригласить друга.
    """
    return _referral_flow().first_cta_text(user_id)


async def _post_generation_referral_hooks(
    message: types.Message,
    user_id: int,
    *,
    send_cta: bool = True,
) -> None:
    await _referral_flow().post_generation_hooks(message, user_id, send_cta=send_cta)


def _clawback_referral_rewards(referred_user_id: int, charge_id: str) -> None:
    """Reverse referral rewards for a refunded payment (Phase 9 service)."""
    _referral_flow().clawback_rewards(referred_user_id, charge_id)


def _notify_referrer(referrer_id: int, bonus: int, *, message_key: str = "referral_reward_got") -> None:
    """Best-effort уведомление реферера о начислении (не блокирует оплату)."""
    _referral_flow().notify_referrer(referrer_id, bonus, message_key=message_key)


# Referral reward orchestration moved to referrals/ (Phase 9); wired with this
# process's credit store and Telegram notifier.
_referral_service = ReferralService(
    store=credit_store, metrics=metrics, notify=_notify_referrer, log=log
)

# wizard_state (button-wizard per-user state) now lives in storage/session_state (Phase 10).


# _ws moved to storage/session_state.py (Phase 5); re-imported below.


def _fmt_to_aspect(fmt: str) -> str:
    return fmt_to_aspect(fmt)

# Per-user Flow project lifecycle + auto-disable circuit breaker moved to
# accounts/projects.py (Phase 11 core split). The manager owns the failure
# counter/enabled flag as instance state; flow_bot keeps thin delegates below.
from accounts.projects import ProjectManager

_project_mgr = ProjectManager(
    project_store=project_store,
    account_for=_account_for,
    keeper_for_acc=_keeper_for_acc,
    default_account_id=DEFAULT_ACCOUNT_ID,
    max_failures=PROJECT_CREATION_MAX_FAILURES,
    per_user_enabled=PER_USER_PROJECTS,
    log=log,
)

# parse_result / result_pairs импортированы из flow_core (общая логика разбора).


from channels.telegram.request_gate import (  # noqa: E402
    RateLimited,
    RequestGate,
    RequestGateDeps,
)
from billing.credit_gate import NotEnoughCredits  # noqa: E402


def user_slot(user_id: int, message: types.Message):
    return _request_gate.user_slot(user_id, message)


def credit_gate(
    user_id: int, action: str, message: types.Message, num_images: int = 1, *, surcharge: int = 0
):
    return _request_gate.credit_gate(user_id, action, message, num_images, surcharge=surcharge)


def _project_key(account_id: str, user_id: int) -> str:
    return ProjectManager.project_key(account_id, user_id)


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
    return await _project_mgr.ensure(user_id, account_id=account_id)


# ── меню и визард (кнопочный UX) ──────────────────────────────────────


# ── Маркетплейс-меню селлер-бота (docs/SELLER_BOT_PLAN.md §4) ─────────────


def _mp_platform_aspect(platform: str) -> str:
    return _fmt_to_aspect(_mp_platform_fmt(platform))


_marketplace_stale = MarketplaceStale(MarketplaceStaleDeps(workspace=_ws))


def _mp_message_id(message) -> int:
    return _marketplace_stale.message_id(message)


def _mp_stamp_message(user_id: int, message) -> None:
    _marketplace_stale.stamp_message(user_id, message)


def _mp_is_stale_callback(user_id: int, callback: types.CallbackQuery) -> bool:
    return _marketplace_stale.is_stale_callback(user_id, callback)


async def _mp_reject_stale_callback(callback: types.CallbackQuery) -> None:
    await _marketplace_stale.reject_stale_callback(callback)


# Подсказка-сид к промпту под каждую задачу (формат подставляется отдельно).
_MP_JOB_SEED = {
    "whitebg": "товар на чистом белом фоне для карточки маркетплейса, студийный свет",
    "info": "инфографика-карточка товара: крупный товар, место под заголовок и буллеты",
    "model": "товар на модели / в интерьере, реалистичная сцена для карточки",
    "cover": "обложка/главный слайд карточки товара, цепляющий ракурс",
    "bg": "заменить фон у фото товара на чистый и продающий",
}
from product.marketplace_prompts import (
    _MP_JOB_SEED,
    _mp_job_instruction,
    _mp_video_prompt,
    _mp_series_prompt,
)
_MP_PRODUCT_PHOTO_JOBS = frozenset(_MP_JOB_SEED)


def _marketplace_screens_deps() -> tg_screens.MarketplaceScreensDeps:
    return tg_screens.MarketplaceScreensDeps(
        workspace=_ws,
        metrics=metrics,
        credit_store=credit_store,
        brand_kit=_mp_brand_kit,
        niche_label=_mp_niche_label,
        stamp_message=_mp_stamp_message,
    )


def _mp_confirm_screen(user_id: int):
    return tg_screens.mp_confirm_screen(
        user_id, deps=_marketplace_screens_deps()
    )


def _mp_sku_projects(user_id: int, limit: int = 12) -> list[dict]:
    return tg_screens.mp_sku_projects(
        user_id, limit=limit, deps=_marketplace_screens_deps()
    )


def _mp_sku_projects_text(user_id: int, projects: list[dict] | None = None) -> str:
    return tg_screens.mp_sku_projects_text(
        user_id, projects, deps=_marketplace_screens_deps()
    )


def _mp_sku_projects_kb(user_id: int, projects: list[dict] | None = None) -> types.InlineKeyboardMarkup:
    return tg_screens.mp_sku_projects_kb(
        user_id, projects, deps=_marketplace_screens_deps()
    )


async def _show_sku_projects(message: types.Message, *, user_id: int, edit: bool) -> None:
    await tg_screens.show_sku_projects(
        message, user_id=user_id, edit=edit, deps=_marketplace_screens_deps()
    )


_marketplace_sku = MarketplaceSku(MarketplaceSkuDeps(
    workspace=_ws,
    metrics=metrics,
    menu_button=_menu_button,
))


def _mp_sku_choice_kb(user_id: int) -> types.InlineKeyboardMarkup:
    return _marketplace_sku.choice_kb(user_id)


def _pending_sku_payload(user_id: int) -> dict | None:
    return _marketplace_sku.pending_payload(user_id)


def _latest_sku_payload(user_id: int, *, platform: str | None = None) -> dict | None:
    return _marketplace_sku.latest_payload(user_id, platform=platform)


async def _save_sku_payload(message: types.Message, user_id: int, sku: str, payload: dict) -> bool:
    return await _marketplace_sku.save_payload(message, user_id, sku, payload)


async def _save_pending_sku_item(message: types.Message, user_id: int, sku: str) -> bool:
    return await _marketplace_sku.save_pending_item(message, user_id, sku)


# DEFAULT_COUNT / DEFAULT_FMT moved to config/settings.py (Phase 11); imported above.
_FMT_NAMES = {"land": "16:9", "port": "9:16", "sq": "1:1", "f43": "4:3", "f34": "3:4"}


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


def _image_wizard_screens_deps() -> tg_screens.ImageWizardScreensDeps:
    return tg_screens.ImageWizardScreensDeps(
        workspace=_ws,
        credit_store=credit_store,
        log_event=metrics.log_event,
        edit_or_answer=_edit_or_answer,
        quick_ideas=tuple(_QUICK_IDEAS),
        default_count=DEFAULT_COUNT,
        default_fmt=DEFAULT_FMT,
        default_image_model=DEFAULT_IMAGE_MODEL,
        fmt_names=_FMT_NAMES,
    )


async def show_edit_confirm(message: types.Message, *, user_id: int, edit: bool):
    await tg_screens.show_edit_confirm(
        message, user_id=user_id, edit=edit, deps=_image_wizard_screens_deps()
    )


def _balance_reply_label(user_id: int | None = None) -> str:
    return _telegram_balance_reply_label(user_id, balance_fn=credit_store.balance)


def _is_balance_reply_text(text: str) -> bool:
    return _telegram_is_balance_reply_text(text)


def reply_menu_kb(user_id: int | None = None) -> types.ReplyKeyboardMarkup:
    return _telegram_reply_menu_kb(
        user_id,
        balance_fn=credit_store.balance,
        is_seller=_cfg.IS_SELLER,
    )


async def _show_help_screen(message: types.Message, *, edit: bool) -> None:
    await tg_screens.show_help_screen(
        message, edit=edit, deps=_public_screens_deps()
    )


def _wizard_text(user_id: int) -> str:
    return tg_screens.wizard_text(user_id, deps=_image_wizard_screens_deps())


async def show_wizard(message: types.Message, *, user_id: int, edit: bool):
    await tg_screens.show_wizard(
        message, user_id=user_id, edit=edit, deps=_image_wizard_screens_deps()
    )


async def _boost_prompt_with_gemini(prompt: str) -> str | None:
    return await _gemini_boost_prompt(prompt, api_key=GEMINI_API_KEY, log=log)


def _prompt_picker_text(ideas: list[str]) -> str:
    return tg_screens.prompt_picker_text(ideas)


async def show_prompt_picker(message: types.Message, *, user_id: int, edit: bool):
    await tg_screens.show_prompt_picker(
        message, user_id=user_id, edit=edit, deps=_image_wizard_screens_deps()
    )


# ── видео-визард (кнопочный UX, префикс v:) ────────────────────────────
# Состояние живёт в том же wizard_state[user_id], но ключи с префиксом v*,
# чтобы не пересекаться с визардом картинок (step/count/fmt/await/...).

VID_DEFAULT_COUNT = 1
# Frames (старт/финиш-кадр) дефолтится на veo-lite: единственный interpolation-
# ключ, подтверждённый живым захватом (veo_3_1_interpolation_lite). Остальные
# tiers — догадка по паттерну, пока не подтверждены живым прогоном.
VID_FRAMES_DEFAULT_MODEL = "veo-lite"

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


# _video_plain_text_ready + _VID_FMT_TO_ASPECT live in config.video (imported).


_VID_QUICKSTART_FAMILY = "omni-flash"


# family id (в каталоге) -> короткий код в callback_data и обратно
_VID_FAMILY_CODE = {"omni-flash": "omni", "veo": "veo"}
_VID_CODE_FAMILY = {v: k for k, v in _VID_FAMILY_CODE.items()}


# Продление всегда выполняется моделью veo-lite, но ИСХОДНИК может быть любым
# veo-видео (lite/fast/quality) — оператор подтвердил. Omni продлевать нельзя.


# Варианты модели, доступные в reference-to-video. Ingredients умеет Omni и Veo;
# Frames/interpolation остаётся Veo-only.


def _video_reference_screens_deps() -> tg_screens.VideoReferenceScreensDeps:
    return tg_screens.VideoReferenceScreensDeps(
        wizard_state=wizard_state,
        credit_store=credit_store,
        vid_edit=_vid_edit,
        vid_default_fmt=VID_DEFAULT_FMT,
        vid_default_count=VID_DEFAULT_COUNT,
        vid_ref_default_model=VID_REF_DEFAULT_MODEL,
        vid_frames_default_model=VID_FRAMES_DEFAULT_MODEL,
        vid_fmt_names=_VID_FMT_NAMES,
    )


async def show_video_ingredients(message: types.Message, *, user_id: int, edit: bool = False):
    await tg_screens.show_video_ingredients(
        message, user_id=user_id, edit=edit, deps=_video_reference_screens_deps()
    )


async def show_video_frames(message: types.Message, *, user_id: int, edit: bool = False):
    await tg_screens.show_video_frames(
        message, user_id=user_id, edit=edit, deps=_video_reference_screens_deps()
    )


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


def _new_video_wizard_screens_deps() -> tg_screens.NewVideoWizardScreensDeps:
    return tg_screens.NewVideoWizardScreensDeps(
        wizard_state=wizard_state,
        credit_store=credit_store,
        vid_clear=_vid_clear,
        vid_edit=_vid_edit,
        nwiz_engine=_nwiz_engine,
        nwiz_model=_nwiz_model,
        nwiz_price=_nwiz_price,
        vid_default_fmt=VID_DEFAULT_FMT,
        vid_fmt_names=_VID_FMT_NAMES,
        vid_styles=_VID_STYLES,
        vid_omni_durations=tuple(_VID_OMNI_DURATIONS),
        vid_veo_quality_cycle=tuple(_VID_VEO_QUALITY_CYCLE),
        vid_veo_quality_names=_VID_VEO_QUAL_NAMES,
    )


def _nwiz_text(user_id: int) -> str:
    return tg_screens.new_video_wizard_text(
        user_id, deps=_new_video_wizard_screens_deps()
    )


def _nwiz_kb(user_id: int) -> types.InlineKeyboardMarkup:
    return tg_screens.new_video_wizard_kb(
        user_id, deps=_new_video_wizard_screens_deps()
    )


async def show_video_prompt_input(
    message: types.Message, *, user_id: int, edit: bool,
    vfmt: str | None = None, vstyle: str | None = None,
):
    await tg_screens.show_video_prompt_input(
        message,
        user_id=user_id,
        edit=edit,
        vfmt=vfmt,
        vstyle=vstyle,
        deps=_new_video_wizard_screens_deps(),
    )


def _animate_photo_scenario() -> AnimatePhotoScenario:
    return AnimatePhotoScenario(
        AnimatePhotoConfig(
            default_fmt=VID_DEFAULT_FMT,
            min_price=video_animate_min_price(),
        )
    )


_animate_photo_deps = AnimatePhotoDeps(
    workspace=_ws,
    pending_edits=pending_edits,
    vid_clear=_vid_clear,
    clear_image_flow_keys=_clear_image_flow_keys,
    nwiz_model=_nwiz_model,
    metrics=metrics,
    vid_edit=lambda *a, **k: _vid_edit(*a, **k),
    upload_photo_source_from_file_id=lambda *a, **k: _upload_photo_source_from_file_id(*a, **k),
    show_new_video_wizard=lambda *a, **k: show_new_video_wizard(*a, **k),
    video_generate_and_send=lambda *a, **k: _video_generate_and_send(*a, **k),
)
_TelegramAnimatePhotoContext = make_animate_photo_context_factory(_animate_photo_deps)


async def show_animate_photo_input(message: types.Message, *, user_id: int, edit: bool):
    """Entry point for "Оживить фото": require a photo, then use the new wizard."""
    await _animate_photo_scenario().start_from_menu(
        _TelegramAnimatePhotoContext(message, user_id),
        edit=edit,
    )


async def show_new_video_wizard(message: types.Message, *, user_id: int, edit: bool):
    await tg_screens.show_new_video_wizard(
        message,
        user_id=user_id,
        edit=edit,
        deps=_new_video_wizard_screens_deps(),
    )


# ── конец нового wizard ──────────────────────────────────────────────────


def _video_settings_screens_deps() -> tg_screens.VideoSettingsScreensDeps:
    return tg_screens.VideoSettingsScreensDeps(
        wizard_state=wizard_state,
        credit_store=credit_store,
        vid_clear=_vid_clear,
        aspect_to_vfmt=_aspect_to_vfmt,
        vid_edit=_vid_edit,
        vid_default_fmt=VID_DEFAULT_FMT,
        vid_default_count=VID_DEFAULT_COUNT,
        vid_fmt_names=_VID_FMT_NAMES,
    )


def _vid_settings_text(user_id: int) -> str:
    return tg_screens.video_settings_text(
        user_id, deps=_video_settings_screens_deps()
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
    await tg_screens.show_video_family(
        message, user_id=user_id, edit=edit, deps=_video_settings_screens_deps()
    )


async def show_video_variant(message: types.Message, *, user_id: int):
    await tg_screens.show_video_variant(
        message, user_id=user_id, deps=_video_settings_screens_deps()
    )


async def show_video_settings(message: types.Message, *, user_id: int):
    await tg_screens.show_video_settings(
        message, user_id=user_id, deps=_video_settings_screens_deps()
    )


def _aspect_to_vfmt(aspect: str) -> str:
    return aspect_to_vfmt(aspect)


def _topup_copy(key: str) -> str:
    return flow_copy.msg(key, image_price=_topup_image_price(), video_price=_topup_video_price())


_request_gate = RequestGate(RequestGateDeps(
    user_busy=user_busy,
    user_last_request=user_last_request,
    busy_max_sec=BUSY_MAX_SEC,
    cooldown_sec=COOLDOWN_SEC,
    max_auto_wait_sec=MAX_AUTO_WAIT_SEC,
    log=log,
    credit_store=credit_store,
    zero_balance_kb=_zero_balance_kb,
    menu_button=_menu_button,
))


async def show_main_menu(
    message: types.Message, *, user_id: int, edit: bool = False, ensure_kb: bool = False
):
    await tg_screens.show_main_menu(
        message,
        user_id=user_id,
        edit=edit,
        ensure_kb=ensure_kb,
        deps=_public_screens_deps(),
    )


async def show_balance(message: types.Message, *, user_id: int, edit: bool = True):
    await tg_screens.show_balance(
        message, user_id=user_id, edit=edit, deps=_public_screens_deps()
    )


# Image result delivery lives in channels.telegram.image_delivery; bind it to
# the runtime registries/stores/keyboards + the late-bound bot username here.
_image_delivery = ImageDelivery(ImageDeliveryDeps(
    keeper_for_acc=_keeper_for_acc,
    image_registry=image_registry,
    workspace=_ws,
    is_seller=lambda: _cfg.IS_SELLER,
    seller_image_keyboard=_seller_image_keyboard,
    image_keyboard=_image_keyboard,
    metrics=metrics,
    log=log,
    referral_link=_referral_link,
    bot_username=lambda: BOT_USERNAME,
    credit_store=credit_store,
    menu_button=_menu_button,
    invite_button=_invite_button,
    first_referral_cta_text=_first_referral_cta_text,
))


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
    await _image_delivery.send_one_image(
        message, url=url, img=img, index=index, total=total, caption=caption,
        user_id=user_id, project_id=project_id, prompt=prompt,
        aspect_ratio=aspect_ratio, account_id=account_id,
    )


# ── хэндлеры ──────────────────────────────


_start_router_deps = tg_start_router.StartDeps(
    metrics=metrics,
    username=_username,
    reset_image_flow=_reset_image_flow,
    vid_clear=_vid_clear,
    credit_store=credit_store,
    send_owner_alert=lambda text: _send_owner_alert(text),
    referral_param_prefix=REFERRAL_PARAM_PREFIX,
    referral_referred_bonus=REFERRAL_REFERRED_BONUS,
    parse_channel_seed=parse_channel_seed,
    reply_menu_kb=reply_menu_kb,
    config=_cfg,
    workspace=_ws,
    mp_jobs_kb=mp_jobs_kb,
    mp_stamp_message=_mp_stamp_message,
    price_gen=price_gen,
    onboarding_step1_kb=_onboarding_step1_kb,
    show_main_menu=show_main_menu,
)
cmd_start = tg_start_router.create_handler(_start_router_deps)


# /start lives in channels/telegram/routers/start.py (Phase 6). Include it
# first to preserve the former dp-level precedence for deep-link commands in
# text or captions.
dp.include_router(tg_start_router.create_router(_start_router_deps, handler=cmd_start))


# Photo input lives in channels/telegram/routers/photo_input.py (Phase 6).
# It is included before command routers to preserve the former dp-level
# precedence for captioned photos such as "/menu"; /start router still wins.
dp.include_router(
    tg_photo_input_router.create_router(
        tg_photo_input_router.PhotoInputDeps(
            workspace=_ws,
            album_buffer=lambda: _album_buf,
            album_tasks=lambda: _album_tasks,
            create_task=asyncio.create_task,
            flush_album=lambda *args, **kwargs: _flush_album(*args, **kwargs),
            prepare_photo_edit_from_file_id=lambda *args, **kwargs: _prepare_photo_edit_from_file_id(*args, **kwargs),
            upload_photo_source_from_message=lambda *args, **kwargs: _upload_photo_source_from_message(*args, **kwargs),
            show_video_ingredients=lambda *args, **kwargs: show_video_ingredients(*args, **kwargs),
            show_video_frames=lambda *args, **kwargs: show_video_frames(*args, **kwargs),
            animate_photo_scenario=lambda: _animate_photo_scenario(),
            context_factory=lambda message, user_id: _TelegramAnimatePhotoContext(message, user_id),
            template_photo_received=lambda *args, **kwargs: _template_photo_received(*args, **kwargs),
            video_plain_text_ready=_video_plain_text_ready,
            video_generate_and_send=lambda *args, **kwargs: _video_generate_and_send(*args, **kwargs),
            mp_video_prompt=lambda *args, **kwargs: _mp_video_prompt(*args, **kwargs),
            mp_brand_kit=lambda user_id: _mp_brand_kit(user_id),
            mp_niche=lambda user_id: _mp_niche(user_id),
            log_event=lambda *args, **kwargs: metrics.log_event(*args, **kwargs),
            seller_video_from_photo=lambda *args, **kwargs: _seller_video_from_photo(*args, **kwargs),
            mp_series_counts=_MP_SERIES_COUNTS,
            mp_series_prompt=lambda *args, **kwargs: _mp_series_prompt(*args, **kwargs),
            is_seller=lambda: _cfg.IS_SELLER,
            mp_confirm_screen=lambda user_id: _mp_confirm_screen(user_id),
            mp_stamp_message=lambda *args, **kwargs: _mp_stamp_message(*args, **kwargs),
            upload_image_ref_from_photo_message=lambda *args, **kwargs: _upload_image_ref_from_photo_message(*args, **kwargs),
            mp_platform_aspect=lambda plat: _mp_platform_aspect(plat),
            run_i2i=lambda *args, **kwargs: _run_i2i(*args, **kwargs),
            pending_edits=pending_edits,
            mp_job_instruction=lambda *args, **kwargs: _mp_job_instruction(*args, **kwargs),
            mp_platform_fmt=lambda plat: _mp_platform_fmt(plat),
            image_registry=image_registry,
            edit_and_send=lambda *args, **kwargs: _edit_and_send(*args, **kwargs),
            offer_photo_route_choice=lambda *args, **kwargs: _offer_photo_route_choice(*args, **kwargs),
            default_fmt=DEFAULT_FMT,
            default_image_model=DEFAULT_IMAGE_MODEL,
            vid_ref_default_model=VID_REF_DEFAULT_MODEL,
        )
    )
)


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


dp.include_router(
    tg_admin_status_router.create_router(
        tg_admin_status_router.AdminStatusDeps(
            admin_ids=ADMIN_IDS,
            keeper=keeper,
            account_pool=account_pool,
            metrics=metrics,
            captcha_provider=lambda: CAPTCHA_PROVIDER,
            capmonster_key=lambda: CAPMONSTER_KEY,
            twocaptcha_key=lambda: TWOCAPTCHA_KEY,
            time=time.time,
            bearer_timestamp=lambda: keeper._bearer_ts,
        )
    )
)

# ── админ-метрики (только для ADMIN_IDS; read-only) ───────────────────

def _admin_only(message: types.Message) -> bool:
    if message.from_user.id not in ADMIN_IDS:
        return False
    return True


_admin_help = AdminHelp(AdminHelpDeps(owner_ids=OWNER_IDS))

# Re-exported so the existing source guards and call sites keep working.
_HELP_SECTIONS = HELP_SECTIONS


def _owner_only(message: types.Message) -> bool:
    return _admin_help.owner_only(message)


def _render_admin_help() -> str:
    return _admin_help.render_admin_help()


# Метрики: действие → имя события запроса / тип операции для flow_jobs.
_IMG_REQUEST_EVENT = {
    "gen": "image_requested", "regen": "image_requested",
    "revary": "variations_requested",
    "up2x": "upscale_requested",
    "edit": "image_edit_requested", "myphoto": "image_edit_requested",
    "mp_series": "image_edit_requested",
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


_max_bootstrap = MaxBootstrap(MaxBootstrapDeps(
    backend_generation_deps=_backend_generation_deps,
    log=log,
))


def _build_max_runtime():
    return _max_bootstrap.build_runtime()


def _maybe_start_max_bot() -> None:
    _max_bootstrap.maybe_start_polling()


def _maybe_register_max_webhook(app) -> None:
    _max_bootstrap.maybe_register_webhook(app)


async def _backend_generate(req: dict) -> dict:
    """Internal endpoint dispatcher by ``kind`` (image | i2i | video_ingredients)."""
    if req.get("kind") == "i2i":
        return await _backend_generate_i2i(req)
    if req.get("kind") == "video_ingredients":
        return await _backend_generate_video_ingredients(req)
    return await _backend_generate_images(req)


_seller_flow = SellerFlow(SellerFlowDeps(
    backend_client=_backend_client,
    send_one_image=_send_one_image,
    download=bot.download,
    log=log,
    metrics=metrics,
    username=_username,
    image_request_event=_IMG_REQUEST_EVENT,
    user_slot=user_slot,
    credit_gate=credit_gate,
    rate_limited_error=RateLimited,
    not_enough_credits_error=NotEnoughCredits,
    log_image_job=lambda *a, **k: _log_image_job(*a, **k),
    post_generation_referral_hooks=_post_generation_referral_hooks,
    credit_store=credit_store,
    zero_balance_kb=_zero_balance_kb,
    menu_button=_menu_button,
    mp_back_kb=_mp_back_kb,
    is_seller=lambda: _cfg.IS_SELLER,
    mp_brand_kit=_mp_brand_kit,
))


async def _seller_backend_call_and_send(
    message: types.Message, prompt: str, *, num_images: int, aspect_ratio: str,
    user_id: int, image_model: str, kind: str = "image", image_b64: str | None = None,
) -> tuple[bool, str | None]:
    return await _seller_flow.backend_call_and_send(
        message, prompt, num_images=num_images, aspect_ratio=aspect_ratio,
        user_id=user_id, image_model=image_model, kind=kind, image_b64=image_b64,
    )


async def _seller_generate_and_send(
    message: types.Message, prompt: str, *, num_images: int, aspect_ratio: str,
    user_id: int, action: str = "gen", image_model: str = DEFAULT_IMAGE_MODEL,
    kind: str = "image", image_b64: str | None = None,
) -> bool:
    return await _seller_flow.generate_and_send(
        message, prompt, num_images=num_images, aspect_ratio=aspect_ratio,
        user_id=user_id, action=action, image_model=image_model, kind=kind, image_b64=image_b64,
    )


async def _seller_i2i_from_file_id(
    message: types.Message, file_id: str, instruction: str, *, num_images: int,
    aspect_ratio: str, user_id: int, action: str,
) -> bool:
    return await _seller_flow.i2i_from_file_id(
        message, file_id, instruction, num_images=num_images,
        aspect_ratio=aspect_ratio, user_id=user_id, action=action,
    )


async def _seller_i2i_from_photo(
    message: types.Message, instruction: str, *, num_images: int, aspect_ratio: str,
    user_id: int, action: str,
) -> bool:
    return await _seller_flow.i2i_from_photo(
        message, instruction, num_images=num_images, aspect_ratio=aspect_ratio,
        user_id=user_id, action=action,
    )


async def _seller_video_backend_call_and_send(
    message: types.Message, prompt: str, *, image_b64: str, aspect_ratio: str,
    user_id: int, video_model: str = VID_REF_DEFAULT_MODEL,
) -> bool:
    return await _seller_flow.video_backend_call_and_send(
        message, prompt, image_b64=image_b64, aspect_ratio=aspect_ratio,
        user_id=user_id, video_model=video_model,
    )


async def _seller_video_generate_and_send(
    message: types.Message, prompt: str, *, image_b64: str, aspect_ratio: str,
    user_id: int, video_model: str = VID_REF_DEFAULT_MODEL,
) -> bool:
    return await _seller_flow.video_generate_and_send(
        message, prompt, image_b64=image_b64, aspect_ratio=aspect_ratio,
        user_id=user_id, video_model=video_model,
    )


async def _seller_video_from_photo(
    message: types.Message, prompt: str, *, aspect_ratio: str, user_id: int,
    video_model: str = VID_REF_DEFAULT_MODEL,
) -> bool:
    return await _seller_flow.video_from_photo(
        message, prompt, aspect_ratio=aspect_ratio, user_id=user_id, video_model=video_model,
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
    await _generation_flow.generate_and_send(
        message, prompt, num_images=num_images, aspect_ratio=aspect_ratio,
        actor_id=actor_id, action=action, image_model=image_model,
    )

# Image-job logging + timing helpers live in product.job_log (channel-neutral);
# bind the logger to the runtime metrics/pool here.
_image_job_logger = ImageJobLogger(
    metrics=metrics, account_pool=account_pool, flow_account_id=FLOW_ACCOUNT_ID,
)


def _log_image_job(user_id, action, image_model, started, *, ok, charged=0, error=None,
                   account_id: str | None = None):
    _image_job_logger.log(
        user_id, action, image_model, started,
        ok=ok, charged=charged, error=error, account_id=account_id,
    )


async def _do_generate_and_send(
    message: types.Message,
    prompt: str,
    num_images: int,
    aspect_ratio: str,
    user_id: int,
    image_model: str = DEFAULT_IMAGE_MODEL,
) -> bool:
    return await _generation_flow.do_generate_and_send(
        message, prompt, num_images, aspect_ratio, user_id, image_model=image_model,
    )


def _streak_note(user_id: int) -> str | None:
    # Streak congratulation copy lives in product.streak (channel-neutral);
    # bind the runtime metrics reader here.
    return streak_note(user_id, update_streak=metrics.update_streak)


async def _after_result(message: types.Message, user_id: int, *, streak_note: str | None = None):
    await _image_delivery.after_result(message, user_id, streak_note=streak_note)


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
    await _image_delivery.send_result_pairs(
        message, pairs, user_id=user_id, project_id=project_id, prompt=prompt,
        aspect_ratio=aspect_ratio, emoji=emoji, account_id=account_id,
    )


_owner_alerts = OwnerAlerts(OwnerAlertsDeps(owner_ids=OWNER_IDS, bot=bot))


async def _send_owner_alert(text: str) -> None:
    await _owner_alerts.send(text)


def _fire_owner_alert(text: str) -> None:
    _owner_alerts.fire(text)


# Account failure/cooldown policy lives in accounts.health (channel-neutral);
# here we bind it to the runtime pool/metrics/log and the owner-alert callback.
_account_failure_policy = AccountFailurePolicy(
    account_pool=account_pool,
    log=log,
    metrics=metrics,
    fire_owner_alert=_fire_owner_alert,
)


def _mark_image_account_failure(account_id: str | None, result: dict | None = None) -> None:
    _account_failure_policy.mark_image_failure(account_id, result)


def _mark_video_account_failure(account_id: str | None, result: dict | None = None) -> None:
    _account_failure_policy.mark_video_failure(account_id, result)


# Image generate-and-send orchestration lives in channels.telegram.generation_flow;
# bind it to the runtime singletons + sibling flows here.
_generation_flow = GenerationFlow(GenerationFlowDeps(
    workspace=_ws,
    is_seller=lambda: _cfg.IS_SELLER,
    seller_generate_and_send=_seller_generate_and_send,
    user_slot=user_slot,
    credit_gate=credit_gate,
    rate_limited_error=RateLimited,
    not_enough_credits_error=NotEnoughCredits,
    image_request_event=_IMG_REQUEST_EVENT,
    username=_username,
    account_for_image=_account_for_image,
    ensure_user_project=ensure_user_project,
    metrics=metrics,
    account_pool=account_pool,
    client_for_acc=_client_for_acc,
    log=log,
    fire_owner_alert=_fire_owner_alert,
    img_retry_kb=_img_retry_kb,
    menu_button=_menu_button,
    mark_image_account_failure=_mark_image_account_failure,
    send_result_pairs=_send_result_pairs,
    after_result=_after_result,
    streak_note=_streak_note,
    log_image_job=_log_image_job,
    edit_capture_file=EDIT_CAPTURE_FILE,
    reupload_ref_for_edit_failover=(
        lambda *args, **kwargs: _reupload_ref_for_edit_failover(*args, **kwargs)
    ),
    is_rate_limit_error=_is_rate_limit_error,
    post_generation_referral_hooks=_post_generation_referral_hooks,
    flow_account_id=FLOW_ACCOUNT_ID,
    mix_baskets=mix_baskets,
    account_for=_account_for,
))


async def _download_ref_image_bytes(ref: ImageRef) -> bytes | None:
    return await _reference_routing.download_ref_image_bytes(ref)


async def _reupload_ref_for_edit_failover(
    ref: ImageRef,
    user_id: int,
    *,
    current_account_id: str | None,
) -> ImageRef | None:
    return await _reference_routing.reupload_ref_for_edit_failover(
        ref, user_id, current_account_id=current_account_id
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
    return await _generation_flow.edit_and_send(
        message, ref, instruction, actor_id=actor_id, aspect_ratio=aspect_ratio,
        image_model=image_model, price_action=price_action,
    )


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
    return await _generation_flow.do_edit_and_send(
        message, ref, instruction, image_inputs, user_id,
        aspect_ratio=aspect_ratio, image_model=image_model,
    )

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
    return await _generation_flow.run_i2i(
        message, ref, prompt, num_images=num_images, emoji=emoji,
        fail_text=fail_text, action=action,
    )


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
    return await _generation_flow.do_run_i2i(
        message, ref, prompt, image_inputs,
        num_images=num_images, emoji=emoji, fail_text=fail_text,
    )


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
    await _generation_flow.real_upscale_and_send(message, ref)


async def _do_real_upscale(message: types.Message, ref: ImageRef, media_id: str) -> bool:
    return await _generation_flow.do_real_upscale(message, ref, media_id)


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
    return image_ext_from_bytes(data, fallback)


async def _send_original_file(message: types.Message, ref: ImageRef, *, marketplace_export: bool = False):
    await _generation_flow.send_original_file(message, ref, marketplace_export=marketplace_export)


async def _do_send_original_file(
    message: types.Message,
    ref: ImageRef,
    url: str,
    *,
    marketplace_export: bool = False,
):
    await _generation_flow.do_send_original_file(message, ref, url, marketplace_export=marketplace_export)


async def _mix_and_send(message: types.Message, prompt: str):
    await _generation_flow.mix_and_send(message, prompt)


async def _do_mix_and_send(
    message: types.Message, prompt: str, image_inputs: list, user_id: int
):
    await _generation_flow.do_mix_and_send(message, prompt, image_inputs, user_id)


def _profile_screens_deps() -> tg_screens.ProfileScreensDeps:
    return tg_screens.ProfileScreensDeps(
        metrics=metrics,
        workspace=_ws,
        image_registry=image_registry,
        seller_history_text=_seller_history_text,
        is_seller=lambda: _cfg.IS_SELLER,
        log=log,
    )


async def _show_gallery(message: types.Message, *, user_id: int) -> None:
    await tg_screens.show_gallery(
        message, user_id=user_id, deps=_profile_screens_deps()
    )


async def _show_prompt_history(message: types.Message, *, user_id: int) -> None:
    await tg_screens.show_prompt_history(
        message, user_id=user_id, deps=_profile_screens_deps()
    )


async def _show_support_menu(message: types.Message, *, user_id: int, edit: bool) -> None:
    await tg_screens.show_support_menu(
        message, user_id=user_id, edit=edit, deps=_profile_screens_deps()
    )


async def _show_profile_screen(message: types.Message, *, user_id: int, edit: bool) -> None:
    await tg_screens.show_profile_screen(
        message, user_id=user_id, edit=edit, deps=_profile_screens_deps()
    )


async def _show_my_tickets(message: types.Message, *, user_id: int, edit: bool) -> None:
    await tg_screens.show_my_tickets(
        message, user_id=user_id, edit=edit, deps=_profile_screens_deps()
    )


_photo_route_offer = PhotoRouteOffer(PhotoRouteOfferDeps(
    pending_photo_routes=pending_photo_routes,
    photo_route_kb=_photo_route_kb,
))


def _store_pending_photo_route(user_id: int, *, file_id: str, caption: str) -> None:
    _photo_route_offer.store(user_id, file_id=file_id, caption=caption)


async def _offer_photo_route_choice(message: types.Message, *, user_id: int, caption: str) -> None:
    await _photo_route_offer.offer(message, user_id=user_id, caption=caption)


_photo_intake = PhotoIntake(PhotoIntakeDeps(
    bot_download=bot.download,
    log=log,
    account_for_video=_account_for_video,
    account_for_image=_account_for_image,
    ensure_user_project=ensure_user_project,
    keeper_for_acc=_keeper_for_acc,
    workspace=_ws,
    image_registry=image_registry,
    pending_edits=pending_edits,
    fmt_to_aspect=_fmt_to_aspect,
    show_edit_confirm=lambda *a, **k: show_edit_confirm(*a, **k),
    edit_settings_kb=edit_settings_kb,
    vid_clear=_vid_clear,
    clear_image_flow_keys=_clear_image_flow_keys,
    show_new_video_wizard=lambda *a, **k: show_new_video_wizard(*a, **k),
    show_video_frames=lambda *a, **k: show_video_frames(*a, **k),
    show_video_ingredients=lambda *a, **k: show_video_ingredients(*a, **k),
    nwiz_model=_nwiz_model,
    default_fmt=DEFAULT_FMT,
    default_image_model=DEFAULT_IMAGE_MODEL,
    vid_default_fmt=VID_DEFAULT_FMT,
))


async def _prepare_photo_edit_from_file_id(
    message: types.Message, *, user_id: int, file_id: str, caption: str,
    aspect_fmt: str = DEFAULT_FMT, image_model: str | None = None,
    as_generation: bool = False,
) -> bool:
    return await _photo_intake.prepare_photo_edit_from_file_id(
        message, user_id=user_id, file_id=file_id, caption=caption,
        aspect_fmt=aspect_fmt, image_model=image_model, as_generation=as_generation,
    )


async def _prepare_photo_video_from_file_id(
    message: types.Message, *, user_id: int, file_id: str, caption: str,
    vfmt: str | None = None, vstyle: str | None = None,
) -> bool:
    return await _photo_intake.prepare_photo_video_from_file_id(
        message, user_id=user_id, file_id=file_id, caption=caption, vfmt=vfmt, vstyle=vstyle,
    )


# ── «Идеи и шаблоны»: готовые шаблоны (tp:) + подбор по шагам (gp:) ────
# Pure session-state helpers + key sets live in product.ideas_hub.
from product.ideas_hub import ideas_clear as _ideas_clear


_ideas_screens = IdeasScreens(IdeasScreensDeps(
    workspace=_ws,
    menu_button=_menu_button,
    edit_or_answer=lambda *a, **k: _edit_or_answer(*a, **k),
    metrics=metrics,
    prepare_photo_video_from_file_id=lambda *a, **k: _prepare_photo_video_from_file_id(*a, **k),
    prepare_photo_edit_from_file_id=lambda *a, **k: _prepare_photo_edit_from_file_id(*a, **k),
    vid_clear=_vid_clear,
    clear_image_flow_keys=_clear_image_flow_keys,
    vid_default_fmt=VID_DEFAULT_FMT,
    show_new_video_wizard=lambda *a, **k: show_new_video_wizard(*a, **k),
    show_wizard=lambda *a, **k: show_wizard(*a, **k),
    show_video_prompt_input=lambda *a, **k: show_video_prompt_input(*a, **k),
    templates_picker_kb=_templates_picker_kb,
    guided_step_kb=_guided_step_kb,
    guided_to_vid_style=_GUIDED_TO_VID_STYLE,
))


async def _show_ideas_root(message: types.Message, *, user_id: int, edit: bool):
    await _ideas_screens.show_ideas_root(message, user_id=user_id, edit=edit)


async def _render_template_step(message: types.Message, *, user_id: int):
    await _ideas_screens.render_template_step(message, user_id=user_id)


def _tp_store_answer(st: dict, value: str):
    _ideas_screens.tp_store_answer(st, value)


async def _template_photo_received(message: types.Message, *, user_id: int) -> None:
    await _ideas_screens.template_photo_received(message, user_id=user_id)


async def _render_guided_step(message: types.Message, *, user_id: int):
    await _ideas_screens.render_guided_step(message, user_id=user_id)


_agent_flow = AgentFlow(AgentFlowDeps(
    workspace=_ws,
    ensure_user_project=lambda *args, **kwargs: ensure_user_project(*args, **kwargs),
    account_for=_account_for,
    account_for_video=_account_for_video,
    account_pool=account_pool,
    client_for_acc=_client_for_acc,
    log=log,
    credit_store=credit_store,
    metrics=metrics,
    menu_button=_menu_button,
))


async def _agent_improve_call(user_id: int, prompt: str, *, instruction_fn=None) -> dict:
    return await _agent_flow.improve_call(user_id, prompt, instruction_fn=instruction_fn)


def _agent_variants_view(variants: list[dict], *, pick_prefix: str, keep_data: str):
    return _agent_flow.variants_view(
        variants, pick_prefix=pick_prefix, keep_data=keep_data
    )


async def _agent_improve_flow(
    callback: types.CallbackQuery, *, user_id: int, prompt_key: str,
    source: str, pick_prefix: str, keep_data: str, rerender, edit_fn,
    empty_prompt_msg: str, instruction_fn=None,
) -> None:
    await _agent_flow.improve_flow(
        callback,
        user_id=user_id,
        prompt_key=prompt_key,
        source=source,
        pick_prefix=pick_prefix,
        keep_data=keep_data,
        rerender=rerender,
        edit_fn=edit_fn,
        empty_prompt_msg=empty_prompt_msg,
        instruction_fn=instruction_fn,
    )


async def _agent_pick(callback: types.CallbackQuery, *, user_id: int, idx_str: str,
                      prompt_key: str, rerender) -> None:
    await _agent_flow.pick(
        callback, user_id=user_id, idx_str=idx_str,
        prompt_key=prompt_key, rerender=rerender,
    )


async def _video_edit_uploaded(message: types.Message, prompt: str, *, user_id: int) -> None:
    await _video_flow.edit_uploaded(message, prompt, user_id=user_id)


def _aspect_to_fmt(aspect: str) -> str:
    return aspect_to_fmt(aspect)


async def _video_delivery_bytes(
    ref: VideoRef, *, fetched_bytes: bytes | None = None
) -> tuple[bytes | None, bool]:
    # Byte-fetch logic lives in product.video_delivery (channel-neutral);
    # bind the per-account client accessor + logger here.
    return await video_delivery_bytes(
        ref, client_for_acc=_client_for_acc, log=log, fetched_bytes=fetched_bytes,
    )


async def _repeat_last(callback: types.CallbackQuery, user_id: int):
    await _generation_flow.repeat_last(callback, user_id)


# Video reference-source resolution lives in product.video_reference
# (channel-neutral, pure); the account resolver is bound to the runtime pool's
# health check here. Thin wrappers keep existing call sites unchanged.
_source_account_id = video_reference.source_account_id
_source_project_id = video_reference.source_project_id
_video_reference_sources = video_reference.video_reference_sources
_video_reference_project_id = video_reference.video_reference_project_id


_reference_routing = ReferenceRouting(ReferenceRoutingDeps(
    log=log,
    bot_download=bot.download,
    account_pool=account_pool,
    account_for_image=_account_for_image,
    account_for_video=_account_for_video,
    ensure_user_project=ensure_user_project,
    keeper_for_acc=_keeper_for_acc,
    video_account_health_reason=_video_account_health_reason,
))


def _video_reference_account_id(st: dict, vmode: str) -> str | None:
    return video_reference.video_reference_account_id(
        st, vmode, is_reference_usable=account_pool.is_reference_usable
    )


async def _reupload_reference_source(
    src: dict, *, user_id: int, acc_id: str, project_id: str | None
) -> dict | None:
    return await _reference_routing.reupload_reference_source(
        src, user_id=user_id, acc_id=acc_id, project_id=project_id
    )


async def _ensure_reference_on_healthy_account(
    st: dict, vmode: str, *, user_id: int, model_id: str, min_credits: int
) -> str | None:
    return await _reference_routing.ensure_reference_on_healthy_account(
        st, vmode, user_id=user_id, model_id=model_id, min_credits=min_credits
    )


_video_flow = VideoFlow(VideoFlowDeps(
    workspace=_ws,
    user_slot=user_slot,
    rate_limited_error=RateLimited,
    vid_default_count=VID_DEFAULT_COUNT,
    username=_username,
    video_reference_sources=_video_reference_sources,
    video_reference_project_id=_video_reference_project_id,
    ensure_reference_on_healthy_account=_ensure_reference_on_healthy_account,
    account_for_video=_account_for_video,
    ensure_user_project=ensure_user_project,
    credit_store=credit_store,
    metrics=metrics,
    account_pool=account_pool,
    client_for_acc=_client_for_acc,
    mark_video_account_failure=_mark_video_account_failure,
    video_registry=video_registry,
    video_result_kb=video_result_kb,
    referral_link=_referral_link,
    bot_username=lambda: BOT_USERNAME,
    video_delivery_bytes=_video_delivery_bytes,
    post_generation_referral_hooks=_post_generation_referral_hooks,
    vid_clear=_vid_clear,
    zero_balance_kb=_zero_balance_kb,
    menu_button=_menu_button,
    log=log,
    aspect_to_vfmt=_aspect_to_vfmt,
    vid_clear_reference_inputs=_vid_clear_reference_inputs,
))


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
    await _video_flow.generate_and_send(
        message, prompt, user_id=user_id,
        unit_price_override=unit_price_override,
        prompt_edited=prompt_edited, status_text=status_text,
        source_video=source_video, video_operation=video_operation,
        source_scene_id=source_scene_id,
    )


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
    await _video_flow.do_generate_and_send(
        message, prompt, user_id=user_id,
        unit_price_override=unit_price_override,
        prompt_edited=prompt_edited, status_text=status_text,
        source_video=source_video, video_operation=video_operation,
        source_scene_id=source_scene_id,
    )


async def _video_download(callback: types.CallbackQuery, user_id: int, token: str) -> None:
    await _video_flow.download(callback, user_id, token)


async def _video_segment_download(callback: types.CallbackQuery, user_id: int, token: str) -> None:
    await _video_flow.segment_download(callback, user_id, token)


async def _video_edit_start(callback: types.CallbackQuery, user_id: int, token: str) -> None:
    await _video_flow.edit_start(callback, user_id, token)


async def _video_extend_start(callback: types.CallbackQuery, user_id: int, token: str) -> None:
    await _video_flow.extend_start(callback, user_id, token)


def _video_prompt_edit_prompt(ref: VideoRef, instruction: str) -> str:
    instruction = (instruction or "").strip()
    return instruction


async def _video_prompt_edit_and_send(
    message: types.Message, ref: VideoRef, instruction: str, *, user_id: int
) -> None:
    await _video_flow.prompt_edit_and_send(message, ref, instruction, user_id=user_id)


async def _video_extend_and_send(
    message: types.Message, ref: VideoRef, prompt: str, *, user_id: int
) -> None:
    await _video_flow.extend_and_send(message, ref, prompt, user_id=user_id)


async def _video_repeat_last(callback: types.CallbackQuery, user_id: int) -> None:
    await _video_flow.repeat_last(callback, user_id)


_stars_topup = StarsTopup(StarsTopupDeps(
    credit_pack=credit_pack,
    admin_ids=ADMIN_IDS,
    bot_send_invoice=bot.send_invoice,
    log=log,
))


async def _start_topup(callback: types.CallbackQuery, user_id: int, pack_id: str):
    await _stars_topup.start_topup(callback, user_id, pack_id)


def _robokassa_runtime_config() -> robokassa_billing.RobokassaConfig:
    return robokassa_billing.RobokassaConfig(
        merchant_login=ROBOKASSA_MERCHANT_LOGIN,
        password1=ROBOKASSA_PASSWORD1,
        password2=ROBOKASSA_PASSWORD2,
        hash_algo=ROBOKASSA_HASH_ALGO,
        inc_curr_label=ROBOKASSA_INC_CURR_LABEL,
        test=ROBOKASSA_TEST,
        pay_url=ROBOKASSA_PAY_URL,
        scope=ROBOKASSA_SCOPE,
        consumer_result_url=ROBOKASSA_CONSUMER_RESULT_URL,
        seller_result_url=ROBOKASSA_SELLER_RESULT_URL,
        consumer_bot_username=ROBOKASSA_CONSUMER_BOT_USERNAME,
        seller_bot_username=ROBOKASSA_SELLER_BOT_USERNAME,
    )


def _robokassa_web_deps() -> robokassa_billing.RobokassaWebDeps:
    return robokassa_billing.RobokassaWebDeps(
        config=_robokassa_runtime_config(),
        is_configured=_robokassa_configured,
        credit_pack=credit_pack,
        robokassa_pack_amount=_robokassa_pack_amount,
        payment_signature=robokassa_payment_signature,
        result_signature=robokassa_result_signature,
        clean_scope=_robokassa_clean_scope,
        credit_store=credit_store,
        metrics=metrics,
        log=log,
        maybe_apply_referral_rewards=_maybe_apply_referral_rewards,
        notify_success=_notify_robokassa_success,
        forward_result=_robokassa_forward_result,
    )


def _robokassa_new_inv_id() -> int:
    return robokassa_billing.new_inv_id()


def _robokassa_receipt_json(pack_id: str, out_sum: str, credits: int) -> str:
    return robokassa_billing.receipt_json(pack_id, out_sum, credits)


def _robokassa_payment_url(user_id: int, pack_id: str, inv_id: int) -> str:
    return robokassa_billing.payment_url(
        user_id,
        pack_id,
        inv_id,
        _robokassa_runtime_config(),
        credit_pack=credit_pack,
        robokassa_pack_amount=_robokassa_pack_amount,
        payment_signature=robokassa_payment_signature,
    )


_robokassa_topup = RobokassaTopup(RobokassaTopupDeps(
    credit_pack=credit_pack,
    admin_ids=ADMIN_IDS,
    is_configured=_robokassa_configured,
    new_inv_id=_robokassa_new_inv_id,
    payment_url=_robokassa_payment_url,
    pack_amount=_robokassa_pack_amount,
    rub_display=_rub_display,
    menu_button=_menu_button,
    bot_send_message=bot.send_message,
    workspace=_ws,
    main_menu_kb=main_menu_kb,
    metrics=metrics,
    log=log,
))


async def _start_robokassa_topup(callback: types.CallbackQuery, user_id: int, pack_id: str):
    await _robokassa_topup.start_topup(callback, user_id, pack_id)


dp.include_router(
    tg_payments_router.create_router(
        tg_payments_router.PaymentDeps(
            credit_pack=credit_pack,
            stars_to_rub=STARS_TO_RUB,
            credit_store=credit_store,
            payment_store=payment_store,
            metrics=metrics,
            log=log,
            username=_username,
            maybe_apply_referral_rewards=_maybe_apply_referral_rewards,
            show_main_menu=show_main_menu,
        )
    )
)


async def _robokassa_request_data(request: web.Request) -> dict[str, str]:
    return await robokassa_billing.request_data(request)


def _robokassa_param(data: dict[str, str], *names: str) -> str:
    return robokassa_billing.param(data, *names)


def _robokassa_shp_params(data: dict[str, str]) -> dict[str, str]:
    return robokassa_billing.shp_params(data)


def _robokassa_amount_matches(actual: str, expected: str) -> bool:
    return robokassa_billing.amount_matches(actual, expected)


def _robokassa_target_scope(shp: dict[str, str]) -> str:
    return robokassa_billing.target_scope(shp, clean_scope=_robokassa_clean_scope)


def _robokassa_result_url_for_scope(scope: str) -> str:
    return robokassa_billing.result_url_for_scope(scope, _robokassa_runtime_config())


def _robokassa_provider_payment_id(inv_id: str, scope: str, *, legacy: bool = False) -> str:
    return robokassa_billing.provider_payment_id(inv_id, scope, legacy=legacy)


async def _robokassa_forward_result(target_scope: str, data: dict[str, str]) -> web.Response:
    return await robokassa_billing.forward_result(
        target_scope,
        data,
        config=_robokassa_runtime_config(),
        log=log,
    )


def _robokassa_bot_username_for_scope(scope: str) -> str:
    return robokassa_billing.bot_username_for_scope(scope, _robokassa_runtime_config())


async def _notify_robokassa_success(user_id: int, credits: int, balance: int) -> None:
    await _robokassa_topup.notify_success(user_id, credits, balance)


async def robokassa_result(request: web.Request) -> web.Response:
    return await robokassa_billing.handle_result(request, _robokassa_web_deps())


async def _robokassa_status_page(request: web.Request, *, ok: bool) -> web.Response:
    return await robokassa_billing.status_page(request, _robokassa_web_deps(), ok=ok)


async def robokassa_success(request: web.Request) -> web.Response:
    return await robokassa_billing.success(request, _robokassa_web_deps())


async def robokassa_fail(request: web.Request) -> web.Response:
    return await robokassa_billing.fail(request, _robokassa_web_deps())


async def robokassa_health(request: web.Request) -> web.Response:
    return await robokassa_billing.health(request)


def _register_robokassa_routes(app: web.Application) -> None:
    robokassa_billing.register_routes(
        app,
        is_configured=_robokassa_configured,
        result_handler=robokassa_result,
        success_handler=robokassa_success,
        fail_handler=robokassa_fail,
        health_handler=robokassa_health,
    )


_web_server = WebServer(WebServerDeps(
    account_pool=account_pool,
    keepers=keepers,
    clients=clients,
    startup_state=startup_state,
    proxy_supervisor=local_proxy_sup,
    is_seller=lambda: _cfg.IS_SELLER,
    backend_generate=_backend_generate,
    register_robokassa_routes=_register_robokassa_routes,
    maybe_register_max_webhook=_maybe_register_max_webhook,
    robokassa_configured=_robokassa_configured,
    web_host=ROBOKASSA_WEB_HOST,
    web_port=ROBOKASSA_WEB_PORT,
    log=log,
))


async def _start_web_server() -> web.AppRunner:
    return await _web_server.start()


async def _start_robokassa_web_server() -> web.AppRunner | None:
    return await _web_server.start_robokassa()


async def _upload_photo_source_from_message(message, *, user_id, status_msg):
    return await _photo_intake.upload_photo_source_from_message(message, user_id=user_id, status_msg=status_msg)


async def _upload_photo_source_from_file_id(message, *, user_id, status_msg, file_id):
    return await _photo_intake.upload_photo_source_from_file_id(message, user_id=user_id, status_msg=status_msg, file_id=file_id)


_album_buf = _photo_intake._album_buf
_album_tasks = _photo_intake._album_tasks


def _vid_caption(message: types.Message) -> str:
    return _photo_intake.vid_caption(message)


async def _flush_album(media_group_id: str, user_id: int):
    return await _photo_intake.flush_album(media_group_id, user_id)


async def _handle_album_photos(messages: list, *, user_id: int):
    return await _photo_intake.handle_album_photos(messages, user_id=user_id)


async def _upload_image_ref_from_photo_message(message, *, user_id, status_msg, prompt, aspect_ratio):
    return await _photo_intake.upload_image_ref_from_photo_message(
        message, user_id=user_id, status_msg=status_msg, prompt=prompt, aspect_ratio=aspect_ratio,
    )


async def _upload_image_ref_from_file_id(message, *, user_id, status_msg, file_id, prompt, aspect_ratio):
    return await _photo_intake.upload_image_ref_from_file_id(
        message, user_id=user_id, status_msg=status_msg, file_id=file_id, prompt=prompt, aspect_ratio=aspect_ratio,
    )


# ───────────────────────────────────────────
# ТОЧКА ВХОДА
# ───────────────────────────────────────────


def _install_shutdown_exception_filter() -> None:
    install_shutdown_exception_filter()


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
            _maybe_start_max_bot()
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
    await _monitors.daily_digest_loop()


_VIDEO_POOL_CHECK_INTERVAL_S = 300   # как часто проверяем здоровье видео-пула
_VIDEO_POOL_MIN_SCORE = 10           # ниже — аккаунт считаем «нездоровым»


_monitors = Monitors(MonitorsDeps(
    metrics=metrics,
    credit_store=credit_store,
    log=log,
    send_message=bot.send_message,
    send_owner_alert=lambda text: _send_owner_alert(text),
    account_pool=account_pool,
    video_scores_for_model=_video_scores_for_model,
    quick_ideas=tuple(_QUICK_IDEAS),
    digest_hour=_DIGEST_HOUR,
    digest_batch=_DIGEST_BATCH,
    digest_delay_s=_DIGEST_DELAY_S,
    digest_interval_h=_DIGEST_INTERVAL_H,
    video_pool_min_score=_VIDEO_POOL_MIN_SCORE,
    video_pool_check_interval_s=_VIDEO_POOL_CHECK_INTERVAL_S,
))


async def _video_pool_health_loop() -> None:
    await _monitors.video_pool_health_loop()


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
dp.include_router(
    tg_video_router.create_router(
        tg_video_router.VideoDeps(
            workspace=_ws,
            vid_clear=_vid_clear,
            show_main_menu=show_main_menu,
            video_download=_video_download,
            video_segment_download=_video_segment_download,
            video_edit_start=_video_edit_start,
            video_extend_start=_video_extend_start,
            video_repeat_last=_video_repeat_last,
            show_new_video_wizard=show_new_video_wizard,
            nwiz_text=_nwiz_text,
            vid_edit=_vid_edit,
            nwiz_model=_nwiz_model,
            nwiz_price=_nwiz_price,
            video_generate_and_send=_video_generate_and_send,
            show_video_settings=show_video_settings,
            show_video_variant=show_video_variant,
            show_video_ingredients=show_video_ingredients,
            show_video_frames=show_video_frames,
            show_video_family=show_video_family,
            vid_rerender_settings=_vid_rerender_settings,
            balance=credit_store.balance,
            vid_frames_default_model=VID_FRAMES_DEFAULT_MODEL,
            vid_code_family=_VID_CODE_FAMILY,
            vid_quickstart_family=_VID_QUICKSTART_FAMILY,
            default_video_count=VID_DEFAULT_COUNT,
        )
    )
)
dp.include_router(
    tg_generation_commands_router.create_router(
        tg_generation_commands_router.GenerationCommandsDeps(
            reset_image_flow=_reset_image_flow,
            vid_clear=_vid_clear,
            show_ideas_root=_show_ideas_root,
            generate_and_send=_generate_and_send,
            mix_and_send=_mix_and_send,
        )
    )
)
dp.include_router(
    tg_admin_accounts_router.create_router(
        tg_admin_accounts_router.AdminAccountsDeps(
            admin_only=_admin_only,
            owner_only=_owner_only,
            account_pool=account_pool,
            log_event=metrics.log_event,
            render_admin_help=_render_admin_help,
        )
    )
)
dp.include_router(
    tg_admin_reports_router.create_router(
        tg_admin_reports_router.AdminReportsDeps(
            admin_only=_admin_only,
            metrics=metrics,
            account_pool=account_pool,
            keeper_for_acc=_keeper_for_acc,
            # BOT_USERNAME is hot-patched at runtime in main() via get_me();
            # inject a live reader so the deep-link uses the real username.
            bot_username=lambda: BOT_USERNAME,
        )
    )
)
dp.include_router(
    tg_admin_credits_router.create_router(
        tg_admin_credits_router.AdminCreditsDeps(
            workspace=_ws,
            admin_ids=ADMIN_IDS,
            owner_ids=OWNER_IDS,
            metrics=metrics,
            credit_store=credit_store,
            payment_store=payment_store,
            bot=bot,
            username=_username,
            send_owner_alert=_send_owner_alert,
            clawback_referral_rewards=_clawback_referral_rewards,
            log=log,
        )
    )
)
dp.include_router(
    tg_video_upload_input_router.create_router(
        tg_video_upload_input_router.VideoUploadInputDeps(
            workspace=_ws,
            upload_video_edit_enabled=lambda: _cfg.UPLOAD_VIDEO_EDIT_ENABLED,
            bot=bot,
            log=log,
            metrics=metrics,
            account_for_video=_account_for_video,
            ensure_user_project=ensure_user_project,
            keeper_for_acc=_keeper_for_acc,
            client_for_acc=_client_for_acc,
            video_edit_uploaded=_video_edit_uploaded,
        )
    )
)
dp.include_router(
    tg_plain_text_router.create_router(
        tg_plain_text_router.PlainTextDeps(
            workspace=_ws,
            metrics=metrics,
            username=_username,
            reset_image_flow=_reset_image_flow,
            show_prompt_picker=show_prompt_picker,
            pending_edits=pending_edits,
            pending_photo_routes=pending_photo_routes,
            show_main_menu=show_main_menu,
            is_balance_reply_text=_is_balance_reply_text,
            show_balance=show_balance,
            vid_clear=_vid_clear,
            show_ideas_root=_show_ideas_root,
            show_referral_screen=_show_referral_screen,
            show_help_screen=_show_help_screen,
            show_video_prompt_input=show_video_prompt_input,
            tp_store_answer=_tp_store_answer,
            render_template_step=_render_template_step,
            render_guided_step=_render_guided_step,
            video_edit_uploaded=_video_edit_uploaded,
            video_registry=video_registry,
            video_prompt_edit_and_send=_video_prompt_edit_and_send,
            video_extend_and_send=_video_extend_and_send,
            video_generate_and_send=_video_generate_and_send,
            animate_photo_scenario=_animate_photo_scenario,
            telegram_animate_photo_context=_TelegramAnimatePhotoContext,
            show_new_video_wizard=show_new_video_wizard,
            video_plain_text_ready=_video_plain_text_ready,
            admin_ids=ADMIN_IDS,
            credit_store=credit_store,
            send_owner_alert=_send_owner_alert,
            pending_sku_payload=_pending_sku_payload,
            save_pending_sku_item=_save_pending_sku_item,
            log=log,
            wizard_text=_wizard_text,
            default_count=DEFAULT_COUNT,
            default_fmt=DEFAULT_FMT,
            default_image_model=DEFAULT_IMAGE_MODEL,
            fmt_to_aspect=_fmt_to_aspect,
            aspect_to_fmt=_aspect_to_fmt,
            image_registry=image_registry,
            run_i2i=_run_i2i,
            show_edit_confirm=show_edit_confirm,
            generate_and_send=_generate_and_send,
            config=_cfg,
            show_wizard=show_wizard,
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
