"""MAX bot handler.

Platform-neutral router that turns parsed MAX updates
(:class:`channels.base.IncomingMessage` / :class:`channels.base.IncomingCallback`)
into menu, balance, top-up and generation actions.

Scope is intentionally small (see ``Photozhab Core Split`` plan, Phase 7):

* ``/start`` / menu
* create/edit images with model, format and count settings
* text-to-video, photo references and start/end frame video
* balance / help
* identity-bound Robokassa top-up links

Telegram-only Stars, seller/admin/community flows and video edit/extend remain
out of scope. Billing is identity-aware (a MAX user gets a separate negative
internal id, so a colliding Telegram numeric id never shares a balance).

The handler performs no network or provider calls itself. The chat platform,
wallet, generation service and signed top-up link builder are injected, so the
whole flow is exercised by fakes in the offline test suite.
"""

from __future__ import annotations

import base64
import binascii
import inspect
import logging
from dataclasses import dataclass, field
from collections.abc import Callable, Sequence
from typing import Any, Mapping, MutableMapping, Protocol, runtime_checkable
from urllib.parse import quote

import prompts_lib
from channels.base import (
    Button,
    IncomingCallback,
    IncomingMessage,
    Keyboard,
    PlatformFile,
    PlatformMedia,
)
from channels.max.library import MaxUserLibrary, MetricsMaxUserLibrary
from billing.credit_gate import NotEnoughCredits, open_credit_gate
from flow_core import (
    DEFAULT_IMAGE_MODEL,
    IMAGE_MODELS,
    PRICE_PER_IMAGE,
    STARTER_CREDITS,
    VIDEO_MODELS,
    action_price,
    image_model_extra,
    price_gen,
    video_price,
    video_animate_min_price,
)


MAX_PLATFORM = "max"
_MAX_VIDEO_BYTES = 250 * 1024 * 1024
_MAX_VIDEO_B64_CHARS = 4 * ((_MAX_VIDEO_BYTES + 2) // 3)
log = logging.getLogger(__name__)


class _UndeliverableMediaError(ValueError):
    """A successful generation result that can never be sent as MAX media."""

# Callback-data namespace. Kept short and stable; mirrors the Telegram ``m:*``
# menu ids so copy/analytics stay recognisable across platforms.
CB_MENU = "m:menu"
CB_CREATE_IMAGE = "m:gen"
CB_EDIT_PHOTO = "m:edit"
CB_CREATE_VIDEO = "m:vid"
CB_CREATE_VIDEO_LEGACY = "m:video"
CB_ANIMATE = "m:animate"
CB_MY_PHOTO = "m:myphoto"
CB_IDEAS = "m:ideas"
CB_PROFILE = "m:profile"
CB_GALLERY = "m:gallery"
CB_HISTORY = "m:history"
CB_SUPPORT = "m:support"
CB_SUPPORT_NEW = "m:support:new"
CB_SUPPORT_MY = "m:support:my"
CB_INVITE = "m:invite"
CB_VIDEO_INGREDIENTS = "m:ving"
CB_VIDEO_FRAMES = "m:vframes"
CB_VIDEO_TEXT = "v:text"
CB_BALANCE = "m:balance"
CB_HELP = "m:help"
CB_TOPUP = "m:topup"
CB_RUN_READY = "run:go"
CB_CHANGE_PROMPT = "run:change"

# Pending per-user flow markers.
AWAIT_CREATE_IMAGE = "create_image"
AWAIT_EDIT_PHOTO = "edit_photo"
AWAIT_ANIMATE_PHOTO = "animate_photo"
AWAIT_CREATE_VIDEO = "create_video"
AWAIT_VIDEO_INGREDIENTS = "video_ingredients"
AWAIT_VIDEO_FRAMES = "video_frames"
AWAIT_PHOTO_ROUTE = "photo_route"
AWAIT_SUPPORT = "support"
AWAIT_IDEA_TEMPLATE = "idea_template"
AWAIT_IDEA_GUIDED = "idea_guided"
AWAIT_READY_IMAGE = "ready_image"
AWAIT_READY_VIDEO = "ready_video"
AWAIT_IDLE = "idle"

_IMAGE_ACTIONS = {AWAIT_CREATE_IMAGE, AWAIT_EDIT_PHOTO}
_VIDEO_ACTIONS = {
    AWAIT_CREATE_VIDEO,
    AWAIT_ANIMATE_PHOTO,
    AWAIT_VIDEO_INGREDIENTS,
    AWAIT_VIDEO_FRAMES,
}
_IMAGE_ASPECTS = {
    "portrait": "9:16",
    "landscape": "16:9",
    "square": "1:1",
    "portrait_34": "3:4",
    "landscape_43": "4:3",
}
_VIDEO_ASPECTS = {"portrait": "9:16", "landscape": "16:9"}
_MAX_IMAGE_COUNT = 4
_MAX_REFERENCE_PHOTOS = 4

_START_COMMANDS = {"/start", "/menu", "start", "menu", "меню"}
_HELP_COMMANDS = {"/help", "help", "помощь"}
_BALANCE_COMMANDS = {"/balance", "balance", "баланс"}
_MIN_PROMPT_LEN = 3


@dataclass(frozen=True)
class MaxCopy:
    """User-facing Russian copy. Provider-neutral by contract."""

    menu_title: str = "Что создадим сегодня?"
    ask_image_prompt: str = "Опишите картинку одним сообщением."
    ask_edit_photo: str = "Пришлите фото и добавьте, что изменить."
    ask_animate_photo: str = "Пришлите фото — оживим его в короткое видео."
    ask_video_prompt: str = "Опишите короткое видео одним сообщением."
    ask_ingredients: str = "Пришлите от 1 до 4 фото и описание движения в подписи."
    ask_frames: str = "Пришлите ровно 2 фото: первый и последний кадр, а в подписи — движение."
    need_photo: str = "Нужно фото. Пришлите изображение."
    prompt_too_short: str = "Слишком короткое описание. Добавьте деталей."
    low_balance: str = "Недостаточно кредитов. Пополните баланс и попробуйте снова."
    gen_failed: str = "Не получилось. Кредиты возвращены, попробуйте ещё раз."
    delivered_image: str = "Готово!"
    delivered_video: str = "Видео готово!"
    balance_tpl: str = "Ваш баланс: {balance} кр."
    topup_text: str = "Выберите пакет. Счёт будет привязан к вашему MAX-профилю."
    topup_unavailable: str = "Пополнение временно недоступно. Попробуйте позже."
    photo_route: str = (
        "Фото принято. Что сделать?\n\n"
        "Можно изменить изображение или оживить его в короткое видео."
    )
    profile_text: str = (
        "👤 Мой профиль\n\n"
        "Ваши работы, история запросов и обращения в поддержку — всё здесь."
    )
    gallery_empty: str = "🖼 Пока нет сохранённых работ. Создайте первую картинку."
    history_empty: str = "📋 История запросов пока пуста."
    support_text: str = "🛟 Поддержка\n\nСоздайте обращение или посмотрите ответы."
    support_ask: str = "Опишите вопрос одним сообщением. Мы сохраним обращение."
    support_created: str = "✅ Обращение #{ticket_id} создано. Ответ появится в «Моих обращениях»."
    ideas_root: str = (
        "💡 Идеи и шаблоны\n\n"
        "Выберите готовое решение, подбор по шагам или один из быстрых сюжетов."
    )
    result_missing: str = "Эта работа уже недоступна. Откройте «Мои работы» или создайте новую."
    help_text: str = (
        "Я создаю и редактирую картинки, а также делаю короткие видео.\n\n"
        "• Картинки: Nano Banana 2/Pro, 5 форматов, до 4 результатов.\n"
        "• Видео: Omni Flash или Veo, вертикальное и горизонтальное.\n"
        "• Фото → видео: одно фото, 1–4 референса или первый/последний кадр.\n\n"
        "Баланс и пополнение — в меню."
    )
    menu_button: str = "⬅️ Меню"
    topup_button: str = "➕ Пополнить"


@dataclass(frozen=True)
class MaxMvpConfig:
    """Pricing and links for the MAX MVP.

    Defaults are pulled from ``flow_core`` so MAX charges the same as the
    Telegram monolith without duplicating pricing logic.
    """

    topup_url: str = ""
    starter_credits: int = STARTER_CREDITS
    image_price: int = PRICE_PER_IMAGE
    edit_price: int = field(default_factory=lambda: action_price("edit"))
    animate_price: int = field(default_factory=video_animate_min_price)
    default_image_model: str = DEFAULT_IMAGE_MODEL
    default_video_model: str = "omni-flash-4s"
    default_reference_model: str = "veo-lite"
    bot_public_url: str = "https://max.ru/se13461237_bot"


@runtime_checkable
class ChatPlatform(Protocol):
    """Outbound side of a chat channel (subset of ``channels.base.BotPlatform``)."""

    async def send_message(
        self, chat_id: str, text: str, keyboard: Keyboard | None = None
    ) -> Any:
        ...

    async def edit_message(
        self, chat_id: str, message_id: str, text: str,
        keyboard: Keyboard | None = None,
    ) -> Any:
        ...

    async def answer_callback(self, callback_id: str, text: str | None = None) -> Any:
        ...

    async def send_photo(
        self, chat_id: str, media: PlatformMedia, keyboard: Keyboard | None = None
    ) -> Any:
        ...

    async def send_video(
        self, chat_id: str, media: PlatformMedia, keyboard: Keyboard | None = None
    ) -> Any:
        ...


@runtime_checkable
class GenerationService(Protocol):
    """Injected generation backend.

    Each method returns a mapping. Success carries ``images`` (list of
    ``{"url": ...}``) or ``videos``; failure carries a non-empty ``error``.
    """

    async def create_image(
        self, *, internal_user_id: int, prompt: str, image_model: str,
        aspect_ratio: str, count: int,
    ) -> Mapping[str, Any]:
        ...

    async def edit_photo(
        self, *, internal_user_id: int, prompt: str, photo_file_id: str,
        image_model: str, aspect_ratio: str, count: int,
    ) -> Mapping[str, Any]:
        ...

    async def animate_photo(
        self, *, internal_user_id: int, prompt: str, photo_file_id: str,
        video_model: str, aspect_ratio: str,
    ) -> Mapping[str, Any]:
        ...

    async def create_video(
        self, *, internal_user_id: int, prompt: str, video_model: str,
        aspect_ratio: str,
    ) -> Mapping[str, Any]:
        ...

    async def create_video_ingredients(
        self, *, internal_user_id: int, prompt: str,
        photo_file_ids: tuple[str, ...], video_model: str, aspect_ratio: str,
    ) -> Mapping[str, Any]:
        ...

    async def create_video_frames(
        self, *, internal_user_id: int, prompt: str,
        photo_file_ids: tuple[str, str], video_model: str, aspect_ratio: str,
    ) -> Mapping[str, Any]:
        ...


@runtime_checkable
class Wallet(Protocol):
    """Identity-aware credit wallet."""

    def internal_id(self, platform: str, user_id: str) -> int:
        ...

    def balance(self, platform: str, user_id: str) -> int:
        ...

    def charge(self, platform: str, user_id: str, amount: int) -> bool:
        ...

    def refund(self, platform: str, user_id: str, amount: int) -> None:
        ...


@runtime_checkable
class PendingStateStore(Protocol):
    def get(self, user_id: str) -> dict | None:
        ...

    def set(self, user_id: str, action: str, data: dict | None = None) -> None:
        ...

    def clear(self, user_id: str) -> None:
        ...


class _MappingStateStore:
    def __init__(self, state: MutableMapping[str, dict]) -> None:
        self._state = state

    def get(self, user_id: str) -> dict | None:
        return self._state.get(user_id)

    def set(self, user_id: str, action: str, data: dict | None = None) -> None:
        self._state[user_id] = {"await": action, **(data or {})}

    def clear(self, user_id: str) -> None:
        self._state.pop(user_id, None)


@dataclass
class MetricsWallet:
    """Default wallet backed by the identity-aware ``metrics`` credit store.

    ``metrics`` is imported lazily so the handler module stays importable (and
    testable with a fake wallet) without touching the SQLite layer.
    """

    starter_credits: int = STARTER_CREDITS

    def internal_id(self, platform: str, user_id: str) -> int:
        import metrics

        return metrics.ensure_user_identity(platform, user_id)

    def observe_user(self, platform: str, user: Any) -> int:
        """Project the latest MAX profile into shared admin analytics."""
        import metrics

        internal_id = metrics.ensure_user_identity(platform, user.platform_user_id)
        if internal_id:
            metrics.upsert_user(
                internal_id,
                username=user.username,
                first_name=user.first_name,
                channel=platform,
            )
        return internal_id

    def balance(self, platform: str, user_id: str) -> int:
        import metrics

        return metrics.credits_balance_for_identity(
            platform, user_id, self.starter_credits
        )

    def charge(self, platform: str, user_id: str, amount: int) -> bool:
        import metrics

        return metrics.credits_charge_for_identity(
            platform, user_id, amount, self.starter_credits
        )

    def refund(self, platform: str, user_id: str, amount: int) -> None:
        import metrics

        metrics.credits_refund_for_identity(platform, user_id, amount)


class _WalletGate:
    """Adapt an identity-aware Wallet to the store shape ``open_credit_gate`` wants.

    ``open_credit_gate`` uses ``balance/charge/refund(user_id, ...)``; MAX wallets
    are keyed by ``(platform, platform_user_id)``, so we bind the platform and let
    the platform user id flow through as the gate's ``user_id``.
    """

    __slots__ = ("_wallet", "_platform")

    def __init__(self, wallet: Wallet, platform: str) -> None:
        self._wallet = wallet
        self._platform = platform

    def balance(self, user_id: str) -> int:
        return self._wallet.balance(self._platform, user_id)

    def charge(self, user_id: str, amount: int) -> Any:
        return self._wallet.charge(self._platform, user_id, amount)

    def refund(self, user_id: str, amount: int) -> Any:
        return self._wallet.refund(self._platform, user_id, amount)


class MaxMvpBot:
    """Minimal MAX product flow over the platform-neutral event contract."""

    def __init__(
        self,
        *,
        platform: ChatPlatform,
        service: GenerationService,
        config: MaxMvpConfig | None = None,
        wallet: Wallet | None = None,
        copy: MaxCopy | None = None,
        state: MutableMapping[str, dict] | None = None,
        state_store: PendingStateStore | None = None,
        topup_options: Callable[[str], Sequence[tuple[str, str]]] | None = None,
        user_library: MaxUserLibrary | None = None,
        support_notify: Callable[..., Any] | None = None,
    ) -> None:
        self.platform = platform
        self.service = service
        self.config = config or MaxMvpConfig()
        self.wallet = wallet or MetricsWallet(self.config.starter_credits)
        self.copy = copy or MaxCopy()
        self._state_store = state_store or _MappingStateStore(
            state if state is not None else {}
        )
        self._topup_options = topup_options
        self.user_library = user_library or MetricsMaxUserLibrary()
        self._support_notify = support_notify

    # -- dispatch ---------------------------------------------------------

    async def handle(
        self, event: IncomingMessage | IncomingCallback
    ) -> None:
        observe_user = getattr(self.wallet, "observe_user", None)
        if callable(observe_user):
            observe_user(event.platform, event.user)
        if isinstance(event, IncomingCallback):
            await self.handle_callback(event)
        elif isinstance(event, IncomingMessage):
            await self.handle_message(event)

    async def handle_callback(self, cb: IncomingCallback) -> None:
        data = cb.data
        uid = cb.user.platform_user_id
        chat = cb.chat_id
        message_id = cb.message_id

        if data == CB_MENU:
            self._reset_to_idle(uid)
            await self._show_menu(chat, uid, message_id=message_id)
        elif data == CB_CREATE_IMAGE:
            await self._show_image_prompt_picker(
                chat, uid, message_id=message_id
            )
        elif data == CB_EDIT_PHOTO:
            await self._begin(chat, uid, AWAIT_EDIT_PHOTO, message_id=message_id)
        elif data in {CB_CREATE_VIDEO, CB_CREATE_VIDEO_LEGACY}:
            await self._show_video_menu(chat, uid, message_id=message_id)
        elif data == CB_VIDEO_TEXT:
            await self._begin(chat, uid, AWAIT_CREATE_VIDEO, message_id=message_id)
        elif data == CB_ANIMATE:
            await self._begin(chat, uid, AWAIT_ANIMATE_PHOTO, message_id=message_id)
        elif data == CB_MY_PHOTO:
            self._set_await(uid, AWAIT_PHOTO_ROUTE)
            await self._screen(
                chat,
                "Пришлите фото. После загрузки выберете: изменить или оживить.",
                self._cancel_keyboard(),
                message_id=message_id,
            )
        elif data == CB_IDEAS:
            await self._show_ideas(chat, uid, message_id=message_id)
        elif data == CB_PROFILE:
            await self._show_profile(chat, uid, message_id=message_id)
        elif data == CB_GALLERY:
            await self._show_gallery(chat, uid, message_id=message_id)
        elif data == CB_HISTORY:
            await self._show_history(chat, uid, message_id=message_id)
        elif data == CB_SUPPORT:
            await self._show_support(chat, uid, message_id=message_id)
        elif data == CB_SUPPORT_NEW:
            self._set_await(uid, AWAIT_SUPPORT)
            await self._screen(
                chat, self.copy.support_ask, self._support_cancel_keyboard(),
                message_id=message_id,
            )
        elif data == CB_SUPPORT_MY:
            await self._show_tickets(chat, uid, message_id=message_id)
        elif data == CB_INVITE:
            await self._show_invite(chat, uid, message_id=message_id)
        elif data == CB_VIDEO_INGREDIENTS:
            await self._begin(chat, uid, AWAIT_VIDEO_INGREDIENTS, message_id=message_id)
        elif data == CB_VIDEO_FRAMES:
            await self._begin(chat, uid, AWAIT_VIDEO_FRAMES, message_id=message_id)
        elif data.startswith("s:"):
            await self._update_setting(chat, uid, data, message_id=message_id)
        elif data == CB_RUN_READY:
            await self._run_ready(chat, uid, message_id=message_id)
        elif data == CB_CHANGE_PROMPT:
            await self._change_ready_prompt(chat, uid, message_id=message_id)
        elif data.startswith("idea:"):
            await self._pick_quick_idea(chat, uid, data, message_id=message_id)
        elif data.startswith("ih:") or data.startswith("tp:") or data.startswith("gp:"):
            await self._handle_ideas_callback(chat, uid, data, message_id=message_id)
        elif data.startswith("pr:"):
            await self._handle_photo_route(chat, uid, data, message_id=message_id)
        elif data.startswith("h:use:"):
            await self._use_history_prompt(chat, uid, data, message_id=message_id)
        elif data.startswith("r:"):
            # Editing a media message with a text-only PUT can remove its media;
            # result actions therefore open a fresh control screen.
            await self._handle_result_action(chat, uid, data, message_id=None)
        elif data == CB_BALANCE:
            await self._show_balance(chat, uid, message_id=message_id)
        elif data == CB_HELP:
            await self._screen(
                chat, self.copy.help_text, self._back_menu_keyboard(),
                message_id=message_id,
            )
        elif data == CB_TOPUP:
            await self._show_topup(chat, uid, message_id=message_id)

        callback_id = self._callback_id(cb)
        if callback_id:
            try:
                await self.platform.answer_callback(callback_id)
            except Exception as exc:
                # The action above has already completed. A stale/rejected MAX
                # acknowledgement must not make the durable inbox replay that
                # business action (and potentially duplicate generation).
                log.warning(
                    "MAX callback acknowledgement failed: %s",
                    exc.__class__.__name__,
                )

    async def handle_message(self, msg: IncomingMessage) -> None:
        uid = msg.user.platform_user_id
        chat = msg.chat_id
        text = (msg.text or "").strip()
        lowered = text.lower()

        if lowered in _START_COMMANDS:
            self._maybe_bind_referral(msg)
            self._reset_to_idle(uid)
            await self._show_menu(chat, uid)
            return
        if lowered in _HELP_COMMANDS:
            await self.platform.send_message(
                chat, self.copy.help_text, self._back_menu_keyboard()
            )
            return
        if lowered in _BALANCE_COMMANDS:
            await self._show_balance(chat, uid)
            return

        pending = self._state_store.get(uid)
        if not pending or pending.get("await") == AWAIT_IDLE:
            if msg.photo_file_ids:
                await self._offer_photo_route(chat, uid, msg)
            else:
                await self._show_menu(chat, uid)
            return

        action = pending.get("await")
        if action == AWAIT_SUPPORT:
            await self._create_support_ticket(chat, uid, msg, text)
        elif action == AWAIT_IDEA_TEMPLATE:
            await self._handle_template_text(chat, uid, pending, text)
        elif action == AWAIT_PHOTO_ROUTE:
            if msg.photo_file_ids:
                await self._offer_photo_route(chat, uid, msg, pending=pending)
            else:
                await self.platform.send_message(chat, self.copy.need_photo, self._cancel_keyboard())
        elif action == AWAIT_CREATE_IMAGE:
            if pending.get("confirm_prompt"):
                if len(text.strip()) < _MIN_PROMPT_LEN:
                    await self.platform.send_message(chat, self.copy.prompt_too_short)
                    return
                await self._prepare_ready(
                    chat, uid, prompt=text, action=AWAIT_CREATE_IMAGE,
                    settings=pending,
                )
            else:
                await self._run_create_image(chat, uid, pending=pending, prompt=text)
        elif action in _IMAGE_ACTIONS | _VIDEO_ACTIONS:
            photos = tuple(msg.photo_file_ids[:_MAX_REFERENCE_PHOTOS]) or tuple(
                str(value) for value in pending.get("photo_file_ids") or ()
            )
            if pending.get("confirm_prompt"):
                if len(text.strip()) < _MIN_PROMPT_LEN:
                    await self.platform.send_message(chat, self.copy.prompt_too_short)
                    return
                if action in {
                    AWAIT_EDIT_PHOTO, AWAIT_ANIMATE_PHOTO,
                    AWAIT_VIDEO_INGREDIENTS,
                } and not photos:
                    await self.platform.send_message(chat, self.copy.need_photo)
                    return
                if action == AWAIT_VIDEO_FRAMES and len(photos) != 2:
                    await self.platform.send_message(chat, self.copy.ask_frames)
                    return
                await self._prepare_ready(
                    chat, uid, prompt=text, action=str(action),
                    photo_file_ids=photos, settings=pending,
                )
            else:
                await self._run_configured_job(
                    chat, uid, pending=pending, prompt=text, photos=photos,
                )
        elif action in {AWAIT_READY_IMAGE, AWAIT_READY_VIDEO}:
            # ``Изменить запрос`` reuses the ready screen and waits for one new
            # message instead of silently charging on text input.
            data = {key: value for key, value in pending.items() if key != "await"}
            data["prompt"] = text
            self._set_await(uid, action, data)
            await self._show_ready(chat, uid, {"await": action, **data})
        elif msg.photo_file_ids:
            await self._offer_photo_route(chat, uid, msg)
        else:
            await self._show_menu(chat, uid)

    # -- flows ------------------------------------------------------------

    async def _begin(
        self, chat: str, uid: str, action: str, *, message_id: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        if action in _IMAGE_ACTIONS:
            data = {
                "image_model": self.config.default_image_model,
                "aspect_ratio": "portrait",
                "count": 1,
            }
        else:
            model = (
                self.config.default_video_model
                if action == AWAIT_CREATE_VIDEO
                else self.config.default_reference_model
            )
            data = {"video_model": model, "aspect_ratio": "portrait"}
        data.update(dict(extra or {}))
        data["confirm_prompt"] = True
        self._set_await(uid, action, data)
        await self._show_flow_prompt(
            chat, {"await": action, **data}, message_id=message_id
        )

    async def _update_setting(
        self, chat: str, uid: str, callback: str, *, message_id: str | None = None
    ) -> None:
        pending = self._state_store.get(uid)
        if not pending:
            await self._show_menu(chat, uid, message_id=message_id)
            return
        action = str(pending.get("await") or "")
        setting_action = str(pending.get("ready_action") or action)
        parts = callback.split(":", 2)
        if len(parts) != 3:
            return
        _, key, value = parts
        if key == "im" and setting_action in _IMAGE_ACTIONS and value in IMAGE_MODELS:
            pending["image_model"] = value
        elif key == "ia" and setting_action in _IMAGE_ACTIONS and value in _IMAGE_ASPECTS:
            pending["aspect_ratio"] = value
        elif key == "ic" and setting_action in _IMAGE_ACTIONS and value.isdigit():
            pending["count"] = max(1, min(int(value), _MAX_IMAGE_COUNT))
        elif key == "vm" and setting_action in _VIDEO_ACTIONS and value in VIDEO_MODELS:
            if setting_action != AWAIT_CREATE_VIDEO and VIDEO_MODELS[value]["family"] != "veo":
                return
            pending["video_model"] = value
        elif key == "va" and setting_action in _VIDEO_ACTIONS and value in _VIDEO_ASPECTS:
            pending["aspect_ratio"] = value
        else:
            return
        data = {key: value for key, value in pending.items() if key != "await"}
        self._set_await(uid, action, data)
        if action in {AWAIT_READY_IMAGE, AWAIT_READY_VIDEO}:
            await self._show_ready(chat, uid, pending, message_id=message_id)
        else:
            await self._show_flow_prompt(chat, pending, message_id=message_id)

    async def _show_flow_prompt(
        self, chat: str, pending: Mapping[str, Any], *, message_id: str | None = None
    ) -> None:
        action = pending.get("await")
        prompts = {
            AWAIT_CREATE_IMAGE: self.copy.ask_image_prompt,
            AWAIT_EDIT_PHOTO: self.copy.ask_edit_photo,
            AWAIT_CREATE_VIDEO: self.copy.ask_video_prompt,
            AWAIT_ANIMATE_PHOTO: self.copy.ask_animate_photo,
            AWAIT_VIDEO_INGREDIENTS: self.copy.ask_ingredients,
            AWAIT_VIDEO_FRAMES: self.copy.ask_frames,
        }
        price = self._price_for(pending)
        await self._screen(
            chat,
            f"{prompts.get(action, self.copy.menu_title)}\n\nСтоимость: {price} кр.",
            self._settings_keyboard(pending),
            message_id=message_id,
        )

    def _price_for(self, pending: Mapping[str, Any]) -> int:
        action = str(pending.get("ready_action") or pending.get("await") or "")
        count = self._image_count(pending)
        if action in _IMAGE_ACTIONS:
            if action == AWAIT_CREATE_IMAGE and self.config.image_price == PRICE_PER_IMAGE:
                base_total = price_gen(count)
            else:
                base = self.config.image_price if action == AWAIT_CREATE_IMAGE else self.config.edit_price
                base_total = int(base) * count
            model = self._image_model(pending)
            return base_total + image_model_extra(model) * count
        model = self._video_model(pending, action)
        if action == AWAIT_CREATE_VIDEO:
            return video_price(model, 1, "text")
        if action == AWAIT_VIDEO_FRAMES:
            return video_price(model, 1, "frames")
        if action == AWAIT_ANIMATE_PHOTO and model == self.config.default_reference_model:
            return int(self.config.animate_price)
        return video_price(model, 1, "ingredients")

    async def _run_create_image(
        self, chat: str, uid: str, *, pending: Mapping[str, Any], prompt: str
    ) -> None:
        prompt = (prompt or "").strip()
        if len(prompt) < _MIN_PROMPT_LEN:
            await self.platform.send_message(chat, self.copy.prompt_too_short)
            return
        await self._charged_generation(
            chat,
            uid,
            price=self._price_for(pending),
            prompt=prompt,
            job={
                "action": AWAIT_CREATE_IMAGE,
                "prompt": prompt,
                "image_model": self._image_model(pending),
                "aspect_ratio": self._aspect(pending, video=False),
                "count": self._image_count(pending),
            },
            call=lambda internal_id: self.service.create_image(
                internal_user_id=internal_id,
                prompt=prompt,
                image_model=self._image_model(pending),
                aspect_ratio=self._aspect(pending, video=False),
                count=self._image_count(pending),
            ),
        )

    async def _run_configured_job(
        self,
        chat: str,
        uid: str,
        *,
        pending: Mapping[str, Any],
        prompt: str,
        photos: tuple[str, ...],
    ) -> None:
        prompt = (prompt or "").strip()
        if len(prompt) < _MIN_PROMPT_LEN:
            await self.platform.send_message(chat, self.copy.prompt_too_short)
            return
        action = str(pending.get("ready_action") or pending.get("await") or "")
        stored_photos = pending.get("photo_file_ids") or ()
        photos = tuple(photos or tuple(str(value) for value in stored_photos))[
            :_MAX_REFERENCE_PHOTOS
        ]
        image_model = self._image_model(pending)
        video_model = self._video_model(pending, action)
        aspect = self._aspect(pending, video=action in _VIDEO_ACTIONS)
        count = self._image_count(pending)
        if action == AWAIT_EDIT_PHOTO:
            if not photos:
                await self.platform.send_message(chat, self.copy.need_photo)
                return
            call = lambda internal_id: self.service.edit_photo(  # noqa: E731
                internal_user_id=internal_id,
                prompt=prompt,
                photo_file_id=photos[0],
                image_model=image_model,
                aspect_ratio=aspect,
                count=count,
            )
        elif action == AWAIT_CREATE_VIDEO:
            call = lambda internal_id: self.service.create_video(  # noqa: E731
                internal_user_id=internal_id,
                prompt=prompt,
                video_model=video_model,
                aspect_ratio=aspect,
            )
        elif action == AWAIT_ANIMATE_PHOTO:
            if not photos:
                await self.platform.send_message(chat, self.copy.need_photo)
                return
            call = lambda internal_id: self.service.animate_photo(  # noqa: E731
                internal_user_id=internal_id,
                prompt=prompt,
                photo_file_id=photos[0],
                video_model=video_model,
                aspect_ratio=aspect,
            )
        elif action == AWAIT_VIDEO_INGREDIENTS:
            if not photos:
                await self.platform.send_message(chat, self.copy.need_photo)
                return
            call = lambda internal_id: self.service.create_video_ingredients(  # noqa: E731
                internal_user_id=internal_id,
                prompt=prompt,
                photo_file_ids=photos,
                video_model=video_model,
                aspect_ratio=aspect,
            )
        elif action == AWAIT_VIDEO_FRAMES:
            if len(photos) != 2:
                await self.platform.send_message(chat, self.copy.ask_frames)
                return
            call = lambda internal_id: self.service.create_video_frames(  # noqa: E731
                internal_user_id=internal_id,
                prompt=prompt,
                photo_file_ids=(photos[0], photos[1]),
                video_model=video_model,
                aspect_ratio=aspect,
            )
        else:
            return
        await self._charged_generation(
            chat,
            uid,
            price=self._price_for(pending),
            prompt=prompt,
            job={
                "action": action,
                "prompt": prompt,
                "photo_file_ids": list(photos),
                "image_model": image_model,
                "video_model": video_model,
                "aspect_ratio": aspect,
                "count": count,
            },
            call=call,
        )

    async def _charged_generation(
        self,
        chat: str,
        uid: str,
        *,
        price: int,
        prompt: str,
        job: Mapping[str, Any],
        call,
    ) -> None:
        """Run the injected generation behind the shared billing credit gate.

        The charge-on-success / refund-on-failure rule lives in
        ``billing.open_credit_gate`` — the same rule the Telegram bot uses — so a
        failed generation never keeps the user's credits.
        """
        store = _WalletGate(self.wallet, MAX_PLATFORM)
        internal_id = self.wallet.internal_id(MAX_PLATFORM, uid)

        async def _on_insufficient(have: int, needed: int) -> None:
            await self._show_low_balance(chat, uid)

        try:
            async with open_credit_gate(
                store, uid, price, on_insufficient=_on_insufficient
            ) as charge:
                try:
                    result = await call(internal_id)
                except Exception:
                    await self._fail(chat)
                    self._reset_to_idle(uid)
                    return  # charge.ok stays False -> refunded on gate exit
                if not result or result.get("error"):
                    await self._fail(chat)
                    self._reset_to_idle(uid)
                    return  # refunded on gate exit
                try:
                    image_urls, video_urls = await self._deliver(chat, uid, result)
                except _UndeliverableMediaError:
                    # Invalid provider output is deterministic. Swallow it after
                    # refunding so the durable webhook inbox does not regenerate
                    # the same paid media on every retry.
                    self._reset_to_idle(uid)
                    await self._fail(chat)
                    return
                state = {
                    "last_job": dict(job),
                    "last_images": list(image_urls),
                    "last_videos": list(video_urls),
                }
                self._set_await(uid, AWAIT_IDLE, state)
                try:
                    self.user_library.record_generation(
                        internal_id, prompt=prompt, image_urls=image_urls
                    )
                except Exception as exc:
                    log.warning(
                        "MAX result library persistence failed: %s",
                        exc.__class__.__name__,
                    )
                charge.ok = True
        except NotEnoughCredits:
            self._reset_to_idle(uid)

    async def _deliver(
        self, chat: str, uid: str, result: Mapping[str, Any]
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Send generated media through the platform media API.

        Images and videos are delivered as attachments (``send_photo`` /
        ``send_video``) rather than as raw URLs in a text message. If the
        generation result carries no recognisable media, fall back to a plain
        text confirmation so the flow never silently drops a reply.
        """
        image_urls = [
            str(img.get("url"))
            for img in (result.get("images") or [])
            if isinstance(img, Mapping) and img.get("url")
        ]
        if image_urls:
            for index, url in enumerate(image_urls):
                caption = self.copy.delivered_image if index == 0 else None
                media = PlatformMedia(kind="photo", url=url, caption=caption)
                await self.platform.send_photo(
                    chat, media, self._image_result_keyboard(index, url)
                )
            return tuple(image_urls), ()

        video_media: list[PlatformMedia] = []
        video_urls: list[str] = []
        for vid in (result.get("videos") or []):
            if not isinstance(vid, Mapping):
                continue
            caption = self.copy.delivered_video if not video_media else None
            if vid.get("url"):
                video_urls.append(str(vid["url"]))
                video_media.append(
                    PlatformMedia(kind="video", url=str(vid["url"]), caption=caption)
                )
                continue
            encoded = vid.get("video_b64")
            if not isinstance(encoded, str) or not encoded:
                continue
            if len(encoded) > _MAX_VIDEO_B64_CHARS:
                continue
            try:
                video_bytes = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                continue
            if not video_bytes or len(video_bytes) > _MAX_VIDEO_BYTES:
                continue
            video_media.append(
                PlatformMedia(
                    kind="video",
                    file=PlatformFile(
                        file_id="generated-video.mp4",
                        size=len(video_bytes),
                        mime_type="video/mp4",
                    ),
                    bytes_data=video_bytes,
                    caption=caption,
                )
            )
        if video_media:
            for index, media in enumerate(video_media):
                await self.platform.send_video(
                    chat, media, self._video_result_keyboard(index, media.url)
                )
            return (), tuple(video_urls)

        if result.get("videos"):
            raise _UndeliverableMediaError(
                "generation returned no deliverable MAX video"
            )
        raise _UndeliverableMediaError(
            "generation returned no deliverable MAX media"
        )

    # -- product screens -------------------------------------------------

    async def _screen(
        self,
        chat: str,
        text: str,
        keyboard: Keyboard | None = None,
        *,
        message_id: str | None = None,
    ) -> Any:
        if message_id:
            try:
                return await self.platform.edit_message(
                    chat, message_id, text, keyboard
                )
            except Exception as exc:
                log.info("MAX screen edit fell back to send: %s", exc.__class__.__name__)
        return await self.platform.send_message(chat, text, keyboard)

    async def _show_menu(
        self, chat: str, uid: str, *, message_id: str | None = None
    ) -> None:
        balance = self.wallet.balance(MAX_PLATFORM, uid)
        await self._screen(
            chat,
            f"{self.copy.menu_title}\n\nБаланс: {balance} кр.",
            self._menu_keyboard(uid),
            message_id=message_id,
        )

    async def _show_video_menu(
        self, chat: str, uid: str, *, message_id: str | None = None
    ) -> None:
        await self._screen(
            chat,
            "🎥 Создать видео\n\nВыберите исходник. Модель, формат и стоимость настраиваются на следующем экране.",
            Keyboard.from_rows([
                [Button.callback("✍️ По описанию", CB_VIDEO_TEXT)],
                [Button.callback("🧩 Из 1–4 фото", CB_VIDEO_INGREDIENTS)],
                [Button.callback("🎞 Первый + последний кадр", CB_VIDEO_FRAMES)],
                [Button.callback(self.copy.menu_button, CB_MENU, intent="negative")],
            ]),
            message_id=message_id,
        )

    async def _show_image_prompt_picker(
        self, chat: str, uid: str, *, message_id: str | None = None
    ) -> None:
        self._set_await(uid, AWAIT_CREATE_IMAGE, {
            "image_model": self.config.default_image_model,
            "aspect_ratio": "portrait",
            "count": 1,
            "confirm_prompt": True,
        })
        rows = [
            [Button.callback(f"✨ {str(idea)[:52]}", f"idea:{index}")]
            for index, idea in enumerate(tuple(prompts_lib.QUICK_IDEAS)[:4])
        ]
        rows.append([Button.callback(self.copy.menu_button, CB_MENU, intent="negative")])
        await self._screen(
            chat,
            "🎨 Создать картинку\n\nНапишите свой запрос одним сообщением или выберите быструю идею:",
            Keyboard.from_rows(rows),
            message_id=message_id,
        )

    async def _show_balance(
        self, chat: str, uid: str, *, message_id: str | None = None
    ) -> None:
        balance = self.wallet.balance(MAX_PLATFORM, uid)
        await self._screen(
            chat,
            self.copy.balance_tpl.format(balance=balance),
            Keyboard.from_rows([
                [Button.callback(self.copy.topup_button, CB_TOPUP, intent="positive")],
                [Button.callback(self.copy.menu_button, CB_MENU)],
            ]),
            message_id=message_id,
        )

    async def _show_topup(
        self, chat: str, uid: str, *, message_id: str | None = None
    ) -> None:
        keyboard = self._topup_keyboard(uid)
        text = self.copy.topup_text if len(keyboard.rows) > 2 else self.copy.topup_unavailable
        await self._screen(chat, text, keyboard, message_id=message_id)

    async def _show_profile(
        self, chat: str, uid: str, *, message_id: str | None = None
    ) -> None:
        await self._screen(
            chat,
            self.copy.profile_text,
            Keyboard.from_rows([
                [Button.callback("🖼 Мои работы", CB_GALLERY)],
                [Button.callback("📋 Мои запросы", CB_HISTORY)],
                [Button.callback("🛟 Поддержка", CB_SUPPORT)],
                [Button.callback(self.copy.menu_button, CB_MENU)],
            ]),
            message_id=message_id,
        )

    async def _show_gallery(
        self, chat: str, uid: str, *, message_id: str | None = None
    ) -> None:
        internal_id = self.wallet.internal_id(MAX_PLATFORM, uid)
        rows = self.user_library.gallery(internal_id, limit=10)
        if not rows:
            await self._screen(
                chat, self.copy.gallery_empty, self._profile_back_keyboard(),
                message_id=message_id,
            )
            return
        cache = [
            {"url": str(row.get("file_id") or ""), "prompt": str(row.get("prompt") or "")}
            for row in rows
            if str(row.get("file_id") or "").startswith("https://")
        ]
        self._merge_state(
            uid,
            gallery_cache=cache,
            last_images=[item["url"] for item in cache],
        )
        await self._screen(
            chat,
            f"🖼 Мои работы — последние {len(cache)}.\n\n"
            "Картинку можно изменить, улучшить, оживить или открыть в оригинале.",
            self._profile_back_keyboard(),
            message_id=message_id,
        )
        for index, item in enumerate(cache):
            try:
                await self.platform.send_photo(
                    chat,
                    PlatformMedia(kind="photo", url=item["url"], caption=f"Работа {index + 1}/{len(cache)}"),
                    self._gallery_result_keyboard(index, item["url"]),
                )
            except Exception as exc:
                log.warning("MAX gallery item delivery failed: %s", exc.__class__.__name__)

    async def _show_history(
        self, chat: str, uid: str, *, message_id: str | None = None
    ) -> None:
        internal_id = self.wallet.internal_id(MAX_PLATFORM, uid)
        prompts = self.user_library.prompts(internal_id, limit=10)
        if not prompts:
            await self._screen(
                chat, self.copy.history_empty, self._profile_back_keyboard(),
                message_id=message_id,
            )
            return
        self._merge_state(uid, history_cache=list(prompts))
        lines = ["📋 Мои запросы"]
        rows: list[list[Button]] = []
        for index, prompt in enumerate(prompts):
            lines.append(f"{index + 1}. {prompt[:100]}")
            rows.append([Button.callback(f"{index + 1}. Использовать", f"h:use:{index}")])
        rows.extend(self._profile_back_keyboard().rows)
        await self._screen(
            chat, "\n\n".join(lines), Keyboard.from_rows(rows), message_id=message_id
        )

    async def _show_support(
        self, chat: str, uid: str, *, message_id: str | None = None
    ) -> None:
        await self._screen(
            chat,
            self.copy.support_text,
            Keyboard.from_rows([
                [Button.callback("✉️ Написать вопрос", CB_SUPPORT_NEW)],
                [Button.callback("📋 Мои обращения", CB_SUPPORT_MY)],
                [Button.callback("👤 Профиль", CB_PROFILE)],
                [Button.callback(self.copy.menu_button, CB_MENU)],
            ]),
            message_id=message_id,
        )

    async def _show_tickets(
        self, chat: str, uid: str, *, message_id: str | None = None
    ) -> None:
        internal_id = self.wallet.internal_id(MAX_PLATFORM, uid)
        tickets = self.user_library.tickets(internal_id)
        if not tickets:
            text = "📭 Обращений пока нет."
        else:
            chunks = ["📋 Мои обращения"]
            for ticket in tickets[:10]:
                answered = ticket.get("status") == "replied"
                chunks.append(
                    f"{'✅' if answered else '⏳'} #{ticket.get('id')}\n"
                    f"{str(ticket.get('message_text') or '')[:240]}"
                    + (f"\nОтвет: {str(ticket.get('reply_text') or '')[:400]}" if answered else "")
                )
            text = "\n\n".join(chunks)
        await self._screen(
            chat,
            text,
            Keyboard.from_rows([
                [Button.callback("✉️ Новый вопрос", CB_SUPPORT_NEW)],
                [Button.callback("◀️ Поддержка", CB_SUPPORT)],
                [Button.callback(self.copy.menu_button, CB_MENU)],
            ]),
            message_id=message_id,
        )

    async def _create_support_ticket(
        self, chat: str, uid: str, msg: IncomingMessage, text: str
    ) -> None:
        if len(text.strip()) < _MIN_PROMPT_LEN:
            await self.platform.send_message(chat, self.copy.prompt_too_short)
            return
        internal_id = self.wallet.internal_id(MAX_PLATFORM, uid)
        username = msg.user.username or msg.user.first_name
        ticket_id = self.user_library.create_ticket(
            internal_id, username=username, text=text.strip()
        )
        if ticket_id and self._support_notify is not None:
            try:
                notified = self._support_notify(
                    ticket_id=ticket_id,
                    internal_user_id=internal_id,
                    username=username,
                    text=text.strip(),
                )
                if inspect.isawaitable(notified):
                    await notified
            except Exception as exc:
                log.warning(
                    "MAX support admin notification failed: %s",
                    exc.__class__.__name__,
                )
        self._reset_to_idle(uid)
        response = (
            self.copy.support_created.format(ticket_id=ticket_id)
            if ticket_id
            else "Не удалось создать обращение. Попробуйте позже."
        )
        await self.platform.send_message(chat, response, self._profile_back_keyboard())

    async def _show_invite(
        self, chat: str, uid: str, *, message_id: str | None = None
    ) -> None:
        internal_id = self.wallet.internal_id(MAX_PLATFORM, uid)
        stats = self.user_library.referral_stats(internal_id)
        referral_url = f"{self.config.bot_public_url}?start=ref_{abs(internal_id)}"
        share_text = quote(
            f"Создавай картинки и видео в Photozhab: {referral_url}", safe=""
        )
        share_url = f"https://max.ru/:share?text={share_text}"
        await self._screen(
            chat,
            "🎁 Пригласить друга\n\n"
            f"Приглашено: {int(stats.get('invited') or 0)}\n"
            f"Заработано: {int(stats.get('earned') or 0)} кр.",
            Keyboard.from_rows([
                [Button.link("📤 Поделиться в MAX", share_url)],
                [Button.callback(self.copy.menu_button, CB_MENU)],
            ]),
            message_id=message_id,
        )

    async def _show_ideas(
        self, chat: str, uid: str, *, message_id: str | None = None
    ) -> None:
        rows: list[list[Button]] = [
            [Button.callback("📦 Готовые решения", "ih:templates")],
            [Button.callback("🧭 Подбор по шагам", "ih:guided")],
        ]
        for index, idea in enumerate(tuple(prompts_lib.QUICK_IDEAS)[:4]):
            rows.append([Button.callback(f"✨ {str(idea)[:52]}", f"idea:{index}")])
        rows.append([Button.callback(self.copy.menu_button, CB_MENU)])
        await self._screen(
            chat, self.copy.ideas_root, Keyboard.from_rows(rows), message_id=message_id
        )

    async def _pick_quick_idea(
        self, chat: str, uid: str, data: str, *, message_id: str | None = None
    ) -> None:
        try:
            index = int(data.split(":", 1)[1])
            prompt = tuple(prompts_lib.QUICK_IDEAS)[index]
        except (IndexError, TypeError, ValueError):
            await self._show_ideas(chat, uid, message_id=message_id)
            return
        await self._prepare_ready(
            chat, uid, prompt=str(prompt), action=AWAIT_CREATE_IMAGE,
            message_id=message_id,
        )

    async def _handle_ideas_callback(
        self, chat: str, uid: str, data: str, *, message_id: str | None = None
    ) -> None:
        if data == "ih:templates":
            rows = [
                [Button.callback(str(prompts_lib.get_template(tid)["title"]), f"tp:pick:{index}")]
                for index, tid in enumerate(prompts_lib.template_ids())
                if prompts_lib.get_template(tid)
            ]
            rows.append([Button.callback("◀️ Идеи", CB_IDEAS)])
            await self._screen(
                chat, "📦 Готовые решения\n\nВыберите сценарий:",
                Keyboard.from_rows(rows), message_id=message_id,
            )
            return
        if data == "ih:guided":
            self._set_await(uid, AWAIT_IDEA_GUIDED, {"idea_step": 0, "idea_answers": {}})
            await self._render_guided_step(chat, uid, message_id=message_id)
            return
        if data.startswith("tp:pick:"):
            try:
                tid = prompts_lib.template_ids()[int(data.rsplit(":", 1)[1])]
            except (IndexError, ValueError):
                await self._show_ideas(chat, uid, message_id=message_id)
                return
            self._set_await(
                uid, AWAIT_IDEA_TEMPLATE,
                {"idea_template": tid, "idea_step": 0, "idea_answers": {}},
            )
            await self._render_template_step(chat, uid, message_id=message_id)
            return
        pending = self._state_store.get(uid) or {}
        if data in {"tp:cancel", "gp:cancel"}:
            await self._show_ideas(chat, uid, message_id=message_id)
            return
        if data.startswith("tp:") and pending.get("await") == AWAIT_IDEA_TEMPLATE:
            await self._advance_template(chat, uid, pending, data, message_id=message_id)
            return
        if data.startswith("gp:") and pending.get("await") == AWAIT_IDEA_GUIDED:
            await self._advance_guided(chat, uid, pending, data, message_id=message_id)

    async def _render_template_step(
        self, chat: str, uid: str, *, message_id: str | None = None
    ) -> None:
        pending = self._state_store.get(uid) or {}
        tid = str(pending.get("idea_template") or "")
        questions = prompts_lib.template_questions(tid)
        step = int(pending.get("idea_step") or 0)
        if step >= len(questions):
            prompt = prompts_lib.compose_template_prompt(tid, pending.get("idea_answers") or {})
            target = prompts_lib.template_target(tid)
            action = AWAIT_CREATE_VIDEO if target == "video" else AWAIT_CREATE_IMAGE
            await self._prepare_ready(chat, uid, prompt=prompt, action=action, message_id=message_id)
            return
        question = questions[step]
        rows: list[list[Button]] = []
        if question["type"] == "choice":
            rows.extend([
                [Button.callback(str(option["label"]), f"tp:ans:{index}")]
                for index, option in enumerate(question["options"])
            ])
        if question.get("optional"):
            rows.append([Button.callback("Пропустить", "tp:skip")])
        nav: list[Button] = []
        if step:
            nav.append(Button.callback("◀️ Назад", "tp:back"))
        nav.append(Button.callback("Отмена", "tp:cancel", intent="negative"))
        rows.append(nav)
        suffix = "\n\nНапишите ответ сообщением." if question["type"] == "text" else ""
        await self._screen(
            chat, f"Шаг {step + 1}/{len(questions)}\n\n{question['text']}{suffix}",
            Keyboard.from_rows(rows), message_id=message_id,
        )

    async def _advance_template(
        self, chat: str, uid: str, pending: Mapping[str, Any], data: str,
        *, message_id: str | None = None,
    ) -> None:
        tid = str(pending.get("idea_template") or "")
        questions = prompts_lib.template_questions(tid)
        step = int(pending.get("idea_step") or 0)
        answers = dict(pending.get("idea_answers") or {})
        if data == "tp:back":
            step = max(0, step - 1)
            if step < len(questions):
                answers.pop(str(questions[step]["key"]), None)
        elif data == "tp:skip" and step < len(questions) and questions[step].get("optional"):
            answers[str(questions[step]["key"])] = ""
            step += 1
        elif data.startswith("tp:ans:") and step < len(questions):
            question = questions[step]
            if question["type"] != "choice":
                return
            try:
                option = question["options"][int(data.rsplit(":", 1)[1])]
            except (IndexError, ValueError):
                return
            answers[str(question["key"])] = str(option["value"])
            step += 1
        else:
            return
        self._set_await(uid, AWAIT_IDEA_TEMPLATE, {
            "idea_template": tid, "idea_step": step, "idea_answers": answers,
        })
        await self._render_template_step(chat, uid, message_id=message_id)

    async def _handle_template_text(
        self, chat: str, uid: str, pending: Mapping[str, Any], text: str
    ) -> None:
        tid = str(pending.get("idea_template") or "")
        questions = prompts_lib.template_questions(tid)
        step = int(pending.get("idea_step") or 0)
        if step >= len(questions) or questions[step]["type"] != "text":
            await self._render_template_step(chat, uid)
            return
        if len(text.strip()) < _MIN_PROMPT_LEN and not questions[step].get("optional"):
            await self.platform.send_message(chat, self.copy.prompt_too_short)
            return
        answers = dict(pending.get("idea_answers") or {})
        answers[str(questions[step]["key"])] = text.strip()
        self._set_await(uid, AWAIT_IDEA_TEMPLATE, {
            "idea_template": tid, "idea_step": step + 1, "idea_answers": answers,
        })
        await self._render_template_step(chat, uid)

    async def _render_guided_step(
        self, chat: str, uid: str, *, message_id: str | None = None
    ) -> None:
        pending = self._state_store.get(uid) or {}
        steps = prompts_lib.guided_steps()
        step = int(pending.get("idea_step") or 0)
        if step >= len(steps):
            answers = dict(pending.get("idea_answers") or {})
            prompt = prompts_lib.compose_guided_prompt(answers)
            action = AWAIT_CREATE_VIDEO if answers.get("what") == "video" else AWAIT_CREATE_IMAGE
            await self._prepare_ready(chat, uid, prompt=prompt, action=action, message_id=message_id)
            return
        spec = steps[step]
        rows = [
            [Button.callback(str(option["label"]), f"gp:opt:{index}")]
            for index, option in enumerate(spec["options"])
        ]
        nav: list[Button] = []
        if step:
            nav.append(Button.callback("◀️ Назад", "gp:back"))
        nav.append(Button.callback("Отмена", "gp:cancel", intent="negative"))
        rows.append(nav)
        await self._screen(
            chat, f"Шаг {step + 1}/{len(steps)}\n\n{spec['text']}",
            Keyboard.from_rows(rows), message_id=message_id,
        )

    async def _advance_guided(
        self, chat: str, uid: str, pending: Mapping[str, Any], data: str,
        *, message_id: str | None = None,
    ) -> None:
        steps = prompts_lib.guided_steps()
        step = int(pending.get("idea_step") or 0)
        answers = dict(pending.get("idea_answers") or {})
        if data == "gp:back":
            step = max(0, step - 1)
            if step < len(steps):
                answers.pop(str(steps[step]["key"]), None)
        elif data.startswith("gp:opt:") and step < len(steps):
            try:
                option = steps[step]["options"][int(data.rsplit(":", 1)[1])]
            except (IndexError, ValueError):
                return
            answers[str(steps[step]["key"])] = str(option["value"])
            step += 1
        else:
            return
        self._set_await(uid, AWAIT_IDEA_GUIDED, {"idea_step": step, "idea_answers": answers})
        await self._render_guided_step(chat, uid, message_id=message_id)

    async def _prepare_ready(
        self, chat: str, uid: str, *, prompt: str, action: str,
        message_id: str | None = None, photo_file_ids: Sequence[str] = (),
        image_model: str | None = None,
        settings: Mapping[str, Any] | None = None,
    ) -> None:
        source = settings or {}
        if action in _IMAGE_ACTIONS:
            await_action = AWAIT_READY_IMAGE
            data = {
                "ready_action": action,
                "prompt": prompt,
                "image_model": image_model or self._image_model(source),
                "aspect_ratio": self._aspect(source, video=False),
                "count": self._image_count(source),
                "photo_file_ids": list(photo_file_ids),
            }
        else:
            await_action = AWAIT_READY_VIDEO
            model = self._video_model(source, action)
            data = {
                "ready_action": action,
                "prompt": prompt,
                "video_model": model,
                "aspect_ratio": self._aspect(source, video=True),
                "photo_file_ids": list(photo_file_ids),
            }
        self._set_await(uid, await_action, data)
        await self._show_ready(chat, uid, {"await": await_action, **data}, message_id=message_id)

    async def _show_ready(
        self, chat: str, uid: str, pending: Mapping[str, Any],
        *, message_id: str | None = None,
    ) -> None:
        prompt = str(pending.get("prompt") or "").strip()
        price = self._price_for(pending)
        await self._screen(
            chat,
            f"Готово к запуску\n\n{prompt[:600]}\n\nСтоимость: {price} кр. Баланс: {self.wallet.balance(MAX_PLATFORM, uid)} кр.",
            self._ready_keyboard(pending, price),
            message_id=message_id,
        )

    async def _run_ready(
        self, chat: str, uid: str, *, message_id: str | None = None
    ) -> None:
        pending = self._state_store.get(uid) or {}
        if pending.get("await") not in {AWAIT_READY_IMAGE, AWAIT_READY_VIDEO}:
            await self._show_menu(chat, uid, message_id=message_id)
            return
        prompt = str(pending.get("prompt") or "")
        action = str(pending.get("ready_action") or "")
        runnable = {**pending, "await": action}
        await self._screen(chat, "⏳ Запускаю генерацию…", message_id=message_id)
        if action == AWAIT_CREATE_IMAGE:
            await self._run_create_image(chat, uid, pending=runnable, prompt=prompt)
        else:
            await self._run_configured_job(
                chat, uid, pending=runnable, prompt=prompt,
                photos=tuple(str(value) for value in pending.get("photo_file_ids") or ()),
            )

    async def _change_ready_prompt(
        self, chat: str, uid: str, *, message_id: str | None = None
    ) -> None:
        pending = self._state_store.get(uid) or {}
        if pending.get("await") not in {AWAIT_READY_IMAGE, AWAIT_READY_VIDEO}:
            await self._show_menu(chat, uid, message_id=message_id)
            return
        await self._screen(
            chat, "Отправьте новый запрос одним сообщением.",
            self._cancel_keyboard(), message_id=message_id,
        )

    async def _offer_photo_route(
        self, chat: str, uid: str, msg: IncomingMessage,
        *, pending: Mapping[str, Any] | None = None,
    ) -> None:
        photos = list(msg.photo_file_ids[:_MAX_REFERENCE_PHOTOS])
        caption = (msg.text or "").strip()
        self._set_await(uid, AWAIT_PHOTO_ROUTE, {
            "photo_file_ids": photos,
            "source_prompt": caption,
        })
        await self.platform.send_message(
            chat,
            self.copy.photo_route,
            Keyboard.from_rows([
                [Button.callback("🎨 Изменить изображение", "pr:img")],
                [Button.callback("🎬 Оживить фото", "pr:vid")],
                [Button.callback(self.copy.menu_button, CB_MENU, intent="negative")],
            ]),
        )

    async def _handle_photo_route(
        self, chat: str, uid: str, data: str, *, message_id: str | None = None
    ) -> None:
        pending = self._state_store.get(uid) or {}
        photos = tuple(str(value) for value in pending.get("photo_file_ids") or ())
        if data not in {"pr:img", "pr:vid"}:
            await self._show_menu(chat, uid, message_id=message_id)
            return
        if not photos:
            await self._screen(
                chat, self.copy.result_missing, self._back_menu_keyboard(),
                message_id=message_id,
            )
            return
        prompt = str(pending.get("source_prompt") or "").strip()
        action = AWAIT_EDIT_PHOTO if data == "pr:img" else AWAIT_ANIMATE_PHOTO
        if prompt:
            await self._prepare_ready(
                chat, uid, prompt=prompt, action=action,
                photo_file_ids=photos, message_id=message_id,
            )
        else:
            await self._begin(
                chat, uid, action, message_id=message_id,
                extra={"photo_file_ids": list(photos)},
            )

    async def _use_history_prompt(
        self, chat: str, uid: str, data: str, *, message_id: str | None = None
    ) -> None:
        pending = self._state_store.get(uid) or {}
        cache = pending.get("history_cache") or []
        try:
            prompt = str(cache[int(data.rsplit(":", 1)[1])])
        except (IndexError, TypeError, ValueError):
            await self._show_history(chat, uid, message_id=message_id)
            return
        await self._prepare_ready(
            chat, uid, prompt=prompt, action=AWAIT_CREATE_IMAGE, message_id=message_id
        )

    async def _handle_result_action(
        self, chat: str, uid: str, data: str, *, message_id: str | None = None
    ) -> None:
        pending = self._state_store.get(uid) or {}
        if data == "r:menu":
            self._reset_to_idle(uid)
            await self._show_menu(chat, uid)
            return
        if data in {"r:repeat", "r:video-repeat"}:
            await self._repeat_last_job(chat, uid, pending, message_id=message_id)
            return
        if data == "r:new-image":
            await self._begin(chat, uid, AWAIT_CREATE_IMAGE, message_id=message_id)
            return
        if data == "r:new-video":
            await self._show_video_menu(chat, uid, message_id=message_id)
            return
        parts = data.split(":")
        if len(parts) != 3 or parts[1] not in {"edit", "up", "animate"}:
            return
        try:
            url = str((pending.get("last_images") or [])[int(parts[2])])
        except (IndexError, TypeError, ValueError):
            await self._screen(
                chat, self.copy.result_missing, self._back_menu_keyboard(), message_id=message_id
            )
            return
        if parts[1] == "edit":
            await self._begin(
                chat, uid, AWAIT_EDIT_PHOTO, message_id=message_id,
                extra={"photo_file_ids": [url]},
            )
        elif parts[1] == "animate":
            await self._begin(
                chat, uid, AWAIT_ANIMATE_PHOTO, message_id=message_id,
                extra={"photo_file_ids": [url]},
            )
        else:
            await self._prepare_ready(
                chat, uid,
                prompt="Повысить качество и детализацию изображения, сохранить композицию и содержание без изменений",
                action=AWAIT_EDIT_PHOTO,
                photo_file_ids=(url,), image_model="nbpro", message_id=message_id,
            )

    async def _repeat_last_job(
        self, chat: str, uid: str, pending: Mapping[str, Any],
        *, message_id: str | None = None,
    ) -> None:
        job = pending.get("last_job")
        if not isinstance(job, Mapping):
            await self._screen(
                chat, self.copy.result_missing, self._back_menu_keyboard(), message_id=message_id
            )
            return
        action = str(job.get("action") or "")
        runnable = {"await": action, **dict(job)}
        await self._screen(chat, "⏳ Повторяю генерацию…", message_id=message_id)
        if action == AWAIT_CREATE_IMAGE:
            await self._run_create_image(
                chat, uid, pending=runnable, prompt=str(job.get("prompt") or "")
            )
        else:
            await self._run_configured_job(
                chat, uid, pending=runnable, prompt=str(job.get("prompt") or ""),
                photos=tuple(str(value) for value in job.get("photo_file_ids") or ()),
            )

    async def _show_low_balance(self, chat: str, uid: str) -> None:
        await self.platform.send_message(
            chat, self.copy.low_balance, self._topup_keyboard(uid)
        )

    async def _fail(self, chat: str) -> None:
        await self.platform.send_message(
            chat, self.copy.gen_failed, self._back_menu_keyboard()
        )

    # -- keyboards --------------------------------------------------------

    def _menu_keyboard(self, uid: str) -> Keyboard:
        c = self.config
        balance = self.wallet.balance(MAX_PLATFORM, uid)
        return Keyboard.from_rows([
            [Button.callback("🎨 Создать картинку", CB_CREATE_IMAGE)],
            [Button.callback("🎬 Создать видео", CB_CREATE_VIDEO)],
            [Button.callback(f"🎬 Оживить фото · от {c.animate_price} кр", CB_ANIMATE)],
            [Button.callback("🖼 Изменить моё фото", CB_MY_PHOTO)],
            [Button.callback("💡 Идеи и шаблоны", CB_IDEAS)],
            [Button.callback(f"💳 {balance} кр · Пополнить", CB_BALANCE)],
            [Button.callback("👤 Мой профиль", CB_PROFILE), Button.callback("🤝 Пригласи друга 🎁", CB_INVITE)],
        ])

    def _settings_keyboard(self, pending: Mapping[str, Any]) -> Keyboard:
        action = str(pending.get("ready_action") or pending.get("await") or "")
        rows: list[list[Button]] = []
        if action in _IMAGE_ACTIONS:
            selected_model = str(pending.get("image_model") or self.config.default_image_model)
            rows.append([
                Button.callback(
                    ("✓ " if model_id == selected_model else "") + str(meta["label"]),
                    f"s:im:{model_id}",
                    intent="positive" if model_id == selected_model else None,
                )
                for model_id, meta in IMAGE_MODELS.items()
            ])
            selected_aspect = str(pending.get("aspect_ratio") or "portrait")
            rows.extend([
                [
                    Button.callback(
                        ("✓ " if key == selected_aspect else "") + label,
                        f"s:ia:{key}",
                        intent="positive" if key == selected_aspect else None,
                    )
                    for key, label in list(_IMAGE_ASPECTS.items())[:3]
                ],
                [
                    Button.callback(
                        ("✓ " if key == selected_aspect else "") + label,
                        f"s:ia:{key}",
                        intent="positive" if key == selected_aspect else None,
                    )
                    for key, label in list(_IMAGE_ASPECTS.items())[3:]
                ],
            ])
            selected_count = self._image_count(pending)
            rows.append([
                Button.callback(
                    ("✓ " if count == selected_count else "") + f"{count} шт.",
                    f"s:ic:{count}",
                    intent="positive" if count == selected_count else None,
                )
                for count in range(1, _MAX_IMAGE_COUNT + 1)
            ])
        else:
            selected_model = str(pending.get("video_model") or self.config.default_video_model)
            models = [
                (model_id, meta)
                for model_id, meta in VIDEO_MODELS.items()
                if action == AWAIT_CREATE_VIDEO or meta["family"] == "veo"
            ]
            for index in range(0, len(models), 2):
                mode = "text" if action == AWAIT_CREATE_VIDEO else (
                    "frames" if action == AWAIT_VIDEO_FRAMES else "ingredients"
                )
                rows.append([
                    Button.callback(
                        ("✓ " if model_id == selected_model else "")
                        + self._video_model_label(model_id, meta, mode),
                        f"s:vm:{model_id}",
                        intent="positive" if model_id == selected_model else None,
                    )
                    for model_id, meta in models[index:index + 2]
                ])
            selected_aspect = str(pending.get("aspect_ratio") or "portrait")
            rows.append([
                Button.callback(
                    ("✓ " if key == selected_aspect else "") + label,
                    f"s:va:{key}",
                    intent="positive" if key == selected_aspect else None,
                )
                for key, label in _VIDEO_ASPECTS.items()
            ])
        rows.append([Button.callback(self.copy.menu_button, CB_MENU, intent="negative")])
        return Keyboard.from_rows(rows)

    def _ready_keyboard(self, pending: Mapping[str, Any], price: int) -> Keyboard:
        rows = [list(row) for row in self._settings_keyboard(pending).rows[:-1]]
        rows.append([
            Button.callback(
                f"✅ Создать · {price} кр", CB_RUN_READY, intent="positive"
            )
        ])
        rows.append([
            Button.callback("✏️ Изменить запрос", CB_CHANGE_PROMPT),
            Button.callback("Отмена", CB_MENU, intent="negative"),
        ])
        return Keyboard.from_rows(rows)

    def _back_menu_keyboard(self) -> Keyboard:
        return Keyboard.single(Button.callback(self.copy.menu_button, CB_MENU))

    def _profile_back_keyboard(self) -> Keyboard:
        return Keyboard.from_rows([
            [Button.callback("👤 Профиль", CB_PROFILE)],
            [Button.callback(self.copy.menu_button, CB_MENU)],
        ])

    def _cancel_keyboard(self) -> Keyboard:
        return Keyboard.single(
            Button.callback("Отмена", CB_MENU, intent="negative")
        )

    def _support_cancel_keyboard(self) -> Keyboard:
        return Keyboard.from_rows([
            [Button.callback("◀️ Поддержка", CB_SUPPORT)],
            [Button.callback(self.copy.menu_button, CB_MENU, intent="negative")],
        ])

    @staticmethod
    def _safe_link_button(label: str, url: str) -> Button | None:
        value = str(url or "")
        if value.startswith("https://") and len(value) <= 2048:
            return Button.link(label, value)
        return None

    def _image_result_keyboard(self, index: int, url: str) -> Keyboard:
        rows: list[list[Button]] = [
            [
                Button.callback("✏️ Изменить", f"r:edit:{index}"),
                Button.callback("🔁 Повторить", "r:repeat"),
            ],
            [
                Button.callback("🔍 Улучшить", f"r:up:{index}"),
                Button.callback("🎬 Оживить", f"r:animate:{index}"),
            ],
        ]
        original = self._safe_link_button("⬇️ Открыть оригинал", url)
        if original:
            rows.append([original])
        rows.append([Button.callback(self.copy.menu_button, "r:menu")])
        return Keyboard.from_rows(rows)

    def _gallery_result_keyboard(self, index: int, url: str) -> Keyboard:
        rows: list[list[Button]] = [
            [
                Button.callback("✏️ Изменить", f"r:edit:{index}"),
                Button.callback("🔍 Улучшить", f"r:up:{index}"),
            ],
            [Button.callback("🎬 Оживить", f"r:animate:{index}")],
        ]
        original = self._safe_link_button("⬇️ Открыть оригинал", url)
        if original:
            rows.append([original])
        rows.append([Button.callback(self.copy.menu_button, "r:menu")])
        return Keyboard.from_rows(rows)

    def _video_result_keyboard(self, index: int, url: str | None) -> Keyboard:
        rows: list[list[Button]] = [
            [Button.callback("🔁 Повторить видео", "r:video-repeat")],
            [Button.callback("🎥 Новое видео", "r:new-video")],
        ]
        original = self._safe_link_button("⬇️ Открыть видео", url or "")
        if original:
            rows.append([original])
        rows.append([Button.callback(self.copy.menu_button, "r:menu")])
        return Keyboard.from_rows(rows)

    @staticmethod
    def _video_model_label(
        model_id: str, meta: Mapping[str, Any], mode: str
    ) -> str:
        labels = {
            "veo-lite": "Veo Lite",
            "veo-fast": "Veo Fast",
            "veo-quality": "Veo Quality",
        }
        price = video_price(model_id, 1, mode)
        if model_id in labels:
            return f"{labels[model_id]} · {price} кр"
        return f"Omni {meta['duration']}с · {price} кр"

    def _image_model(self, pending: Mapping[str, Any]) -> str:
        value = str(pending.get("image_model") or self.config.default_image_model)
        return value if value in IMAGE_MODELS else self.config.default_image_model

    def _video_model(self, pending: Mapping[str, Any], action: str) -> str:
        default = (
            self.config.default_video_model
            if action == AWAIT_CREATE_VIDEO
            else self.config.default_reference_model
        )
        value = str(pending.get("video_model") or default)
        meta = VIDEO_MODELS.get(value)
        if not meta or (action != AWAIT_CREATE_VIDEO and meta["family"] != "veo"):
            return default
        return value

    @staticmethod
    def _aspect(pending: Mapping[str, Any], *, video: bool) -> str:
        value = str(pending.get("aspect_ratio") or "portrait")
        allowed = _VIDEO_ASPECTS if video else _IMAGE_ASPECTS
        return value if value in allowed else "portrait"

    @staticmethod
    def _image_count(pending: Mapping[str, Any]) -> int:
        try:
            value = int(pending.get("count") or 1)
        except (TypeError, ValueError):
            value = 1
        return max(1, min(value, _MAX_IMAGE_COUNT))

    def _topup_keyboard(self, uid: str) -> Keyboard:
        options: Sequence[tuple[str, str]] = ()
        if self._topup_options is not None and uid:
            options = self._topup_options(uid)
        elif self.config.topup_url:
            options = ((self.copy.topup_button, self.config.topup_url),)
        rows = [
            [button]
            for label, url in options
            if (button := self._safe_link_button(str(label), str(url))) is not None
        ]
        rows.append([Button.callback("◀️ Баланс", CB_BALANCE)])
        rows.append([Button.callback(self.copy.menu_button, CB_MENU)])
        return Keyboard.from_rows(rows)

    # -- helpers ----------------------------------------------------------

    def _set_await(self, uid: str, action: str, data: dict | None = None) -> None:
        self._state_store.set(uid, action, data)

    def _merge_state(self, uid: str, **values: Any) -> None:
        current = self._state_store.get(uid) or {"await": AWAIT_IDLE}
        action = str(current.get("await") or AWAIT_IDLE)
        data = {key: value for key, value in current.items() if key != "await"}
        data.update(values)
        self._set_await(uid, action, data)

    def _reset_to_idle(self, uid: str) -> None:
        current = self._state_store.get(uid) or {}
        keep = {
            key: current[key]
            for key in ("last_job", "last_images", "last_videos")
            if key in current
        }
        self._set_await(uid, AWAIT_IDLE, keep)

    def _maybe_bind_referral(self, msg: IncomingMessage) -> None:
        raw = msg.raw if isinstance(msg.raw, Mapping) else {}
        payload = str(raw.get("payload") or "")
        if not payload.startswith("ref_") or not payload[4:].isdigit():
            return
        referrer_internal_id = -int(payload[4:])
        referred_internal_id = self.wallet.internal_id(
            MAX_PLATFORM, msg.user.platform_user_id
        )
        try:
            self.user_library.bind_referrer(
                referrer_internal_id=referrer_internal_id,
                referred_internal_id=referred_internal_id,
            )
        except Exception as exc:
            log.warning(
                "MAX referral binding failed: %s", exc.__class__.__name__
            )

    def _clear(self, uid: str) -> None:
        self._state_store.clear(uid)

    @staticmethod
    def _callback_id(cb: IncomingCallback) -> str:
        raw = cb.raw if isinstance(cb.raw, Mapping) else {}
        inner = raw.get("callback")
        if isinstance(inner, Mapping) and inner.get("callback_id"):
            return str(inner["callback_id"])
        return ""
