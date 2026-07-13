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
import logging
from dataclasses import dataclass, field
from collections.abc import Callable, Sequence
from typing import Any, Mapping, MutableMapping, Protocol, runtime_checkable

from channels.base import (
    Button,
    IncomingCallback,
    IncomingMessage,
    Keyboard,
    PlatformFile,
    PlatformMedia,
)
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
CB_CREATE_VIDEO = "m:video"
CB_ANIMATE = "m:animate"
CB_VIDEO_INGREDIENTS = "m:ving"
CB_VIDEO_FRAMES = "m:vframes"
CB_BALANCE = "m:balance"
CB_HELP = "m:help"
CB_TOPUP = "m:topup"

# Pending per-user flow markers.
AWAIT_CREATE_IMAGE = "create_image"
AWAIT_EDIT_PHOTO = "edit_photo"
AWAIT_ANIMATE_PHOTO = "animate_photo"
AWAIT_CREATE_VIDEO = "create_video"
AWAIT_VIDEO_INGREDIENTS = "video_ingredients"
AWAIT_VIDEO_FRAMES = "video_frames"

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

    menu_title: str = "Что делаем?"
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


@runtime_checkable
class ChatPlatform(Protocol):
    """Outbound side of a chat channel (subset of ``channels.base.BotPlatform``)."""

    async def send_message(
        self, chat_id: str, text: str, keyboard: Keyboard | None = None
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

    # -- dispatch ---------------------------------------------------------

    async def handle(
        self, event: IncomingMessage | IncomingCallback
    ) -> None:
        if isinstance(event, IncomingCallback):
            await self.handle_callback(event)
        elif isinstance(event, IncomingMessage):
            await self.handle_message(event)

    async def handle_callback(self, cb: IncomingCallback) -> None:
        data = cb.data
        uid = cb.user.platform_user_id
        chat = cb.chat_id

        if data == CB_MENU:
            self._clear(uid)
            await self._show_menu(chat)
        elif data == CB_CREATE_IMAGE:
            await self._begin(chat, uid, AWAIT_CREATE_IMAGE)
        elif data == CB_EDIT_PHOTO:
            await self._begin(chat, uid, AWAIT_EDIT_PHOTO)
        elif data == CB_CREATE_VIDEO:
            await self._begin(chat, uid, AWAIT_CREATE_VIDEO)
        elif data == CB_ANIMATE:
            await self._begin(chat, uid, AWAIT_ANIMATE_PHOTO)
        elif data == CB_VIDEO_INGREDIENTS:
            await self._begin(chat, uid, AWAIT_VIDEO_INGREDIENTS)
        elif data == CB_VIDEO_FRAMES:
            await self._begin(chat, uid, AWAIT_VIDEO_FRAMES)
        elif data.startswith("s:"):
            await self._update_setting(chat, uid, data)
        elif data == CB_BALANCE:
            await self._show_balance(chat, uid)
        elif data == CB_HELP:
            await self.platform.send_message(
                chat, self.copy.help_text, self._menu_keyboard()
            )
        elif data == CB_TOPUP:
            await self._show_topup(chat, uid)

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
            self._clear(uid)
            await self._show_menu(chat)
            return
        if lowered in _HELP_COMMANDS:
            await self.platform.send_message(
                chat, self.copy.help_text, self._menu_keyboard()
            )
            return
        if lowered in _BALANCE_COMMANDS:
            await self._show_balance(chat, uid)
            return

        pending = self._state_store.get(uid)
        if not pending:
            await self._show_menu(chat)
            return

        action = pending.get("await")
        if action == AWAIT_CREATE_IMAGE:
            await self._run_create_image(chat, uid, pending=pending, prompt=text)
        elif action in _IMAGE_ACTIONS | _VIDEO_ACTIONS:
            await self._run_configured_job(
                chat,
                uid,
                pending=pending,
                prompt=text,
                photos=tuple(msg.photo_file_ids[:_MAX_REFERENCE_PHOTOS]),
            )

    # -- flows ------------------------------------------------------------

    async def _begin(self, chat: str, uid: str, action: str) -> None:
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
        self._set_await(uid, action, data)
        await self._show_flow_prompt(chat, {"await": action, **data})

    async def _update_setting(self, chat: str, uid: str, callback: str) -> None:
        pending = self._state_store.get(uid)
        if not pending:
            await self._show_menu(chat)
            return
        action = str(pending.get("await") or "")
        parts = callback.split(":", 2)
        if len(parts) != 3:
            return
        _, key, value = parts
        if key == "im" and action in _IMAGE_ACTIONS and value in IMAGE_MODELS:
            pending["image_model"] = value
        elif key == "ia" and action in _IMAGE_ACTIONS and value in _IMAGE_ASPECTS:
            pending["aspect_ratio"] = value
        elif key == "ic" and action in _IMAGE_ACTIONS and value.isdigit():
            pending["count"] = max(1, min(int(value), _MAX_IMAGE_COUNT))
        elif key == "vm" and action in _VIDEO_ACTIONS and value in VIDEO_MODELS:
            if action != AWAIT_CREATE_VIDEO and VIDEO_MODELS[value]["family"] != "veo":
                return
            pending["video_model"] = value
        elif key == "va" and action in _VIDEO_ACTIONS and value in _VIDEO_ASPECTS:
            pending["aspect_ratio"] = value
        else:
            return
        data = {key: value for key, value in pending.items() if key != "await"}
        self._set_await(uid, action, data)
        await self._show_flow_prompt(chat, pending)

    async def _show_flow_prompt(self, chat: str, pending: Mapping[str, Any]) -> None:
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
        await self.platform.send_message(
            chat,
            f"{prompts.get(action, self.copy.menu_title)}\n\nСтоимость: {price} кр.",
            self._settings_keyboard(pending),
        )

    def _price_for(self, pending: Mapping[str, Any]) -> int:
        action = str(pending.get("await") or "")
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
        action = str(pending.get("await") or "")
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
            chat, uid, price=self._price_for(pending), call=call
        )

    async def _charged_generation(self, chat: str, uid: str, *, price: int, call) -> None:
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
                    self._clear(uid)
                    return  # charge.ok stays False -> refunded on gate exit
                if not result or result.get("error"):
                    await self._fail(chat)
                    self._clear(uid)
                    return  # refunded on gate exit
                try:
                    await self._deliver(chat, result)
                except _UndeliverableMediaError:
                    # Invalid provider output is deterministic. Swallow it after
                    # refunding so the durable webhook inbox does not regenerate
                    # the same paid media on every retry.
                    self._clear(uid)
                    await self._fail(chat)
                    return
                self._clear(uid)
                charge.ok = True
        except NotEnoughCredits:
            self._clear(uid)

    async def _deliver(self, chat: str, result: Mapping[str, Any]) -> None:
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
            keyboard = self._menu_keyboard()
            for index, url in enumerate(image_urls):
                caption = self.copy.delivered_image if index == 0 else None
                media = PlatformMedia(kind="photo", url=url, caption=caption)
                await self.platform.send_photo(
                    chat, media, keyboard if index == len(image_urls) - 1 else None
                )
            return

        video_media: list[PlatformMedia] = []
        for vid in (result.get("videos") or []):
            if not isinstance(vid, Mapping):
                continue
            caption = self.copy.delivered_video if not video_media else None
            if vid.get("url"):
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
            keyboard = self._menu_keyboard()
            for index, media in enumerate(video_media):
                await self.platform.send_video(
                    chat, media, keyboard if index == len(video_media) - 1 else None
                )
            return

        if result.get("videos"):
            raise _UndeliverableMediaError(
                "generation returned no deliverable MAX video"
            )
        await self.platform.send_message(
            chat, self.copy.delivered_image, self._menu_keyboard()
        )

    # -- small screens ----------------------------------------------------

    async def _show_menu(self, chat: str) -> None:
        await self.platform.send_message(
            chat, self.copy.menu_title, self._menu_keyboard()
        )

    async def _show_balance(self, chat: str, uid: str) -> None:
        balance = self.wallet.balance(MAX_PLATFORM, uid)
        await self.platform.send_message(
            chat,
            self.copy.balance_tpl.format(balance=balance),
            self._menu_keyboard(),
        )

    async def _show_topup(self, chat: str, uid: str) -> None:
        keyboard = self._topup_keyboard(uid)
        text = (
            self.copy.topup_text
            if len(keyboard.rows) > 1
            else self.copy.topup_unavailable
        )
        await self.platform.send_message(
            chat, text, keyboard
        )

    async def _show_low_balance(self, chat: str, uid: str) -> None:
        await self.platform.send_message(
            chat, self.copy.low_balance, self._topup_keyboard(uid)
        )

    async def _fail(self, chat: str) -> None:
        await self.platform.send_message(
            chat, self.copy.gen_failed, self._menu_keyboard()
        )

    # -- keyboards --------------------------------------------------------

    def _menu_keyboard(self) -> Keyboard:
        c = self.config
        text_video_price = video_price(c.default_video_model, 1, "text")
        image_price = price_gen(1) if c.image_price == PRICE_PER_IMAGE else c.image_price
        return Keyboard.from_rows(
            [
                [Button.callback(f"🎨 Создать картинку · {image_price} кр", CB_CREATE_IMAGE)],
                [Button.callback(f"✏️ Изменить фото · {c.edit_price} кр", CB_EDIT_PHOTO)],
                [Button.callback(f"🎥 Создать видео · от {text_video_price} кр", CB_CREATE_VIDEO)],
                [Button.callback(f"🎬 Оживить фото · от {c.animate_price} кр", CB_ANIMATE)],
                [Button.callback("🧩 Видео из 1–4 фото", CB_VIDEO_INGREDIENTS)],
                [Button.callback("🎞 Первый + последний кадр", CB_VIDEO_FRAMES)],
                [
                    Button.callback("💰 Баланс", CB_BALANCE),
                    Button.callback("❓ Помощь", CB_HELP),
                ],
                [Button.callback(self.copy.topup_button, CB_TOPUP)],
            ]
        )

    def _settings_keyboard(self, pending: Mapping[str, Any]) -> Keyboard:
        action = str(pending.get("await") or "")
        rows: list[list[Button]] = []
        if action in _IMAGE_ACTIONS:
            selected_model = str(pending.get("image_model") or self.config.default_image_model)
            rows.append([
                Button.callback(
                    ("✓ " if model_id == selected_model else "") + str(meta["label"]),
                    f"s:im:{model_id}",
                )
                for model_id, meta in IMAGE_MODELS.items()
            ])
            selected_aspect = str(pending.get("aspect_ratio") or "portrait")
            rows.extend([
                [
                    Button.callback(
                        ("✓ " if key == selected_aspect else "") + label,
                        f"s:ia:{key}",
                    )
                    for key, label in list(_IMAGE_ASPECTS.items())[:3]
                ],
                [
                    Button.callback(
                        ("✓ " if key == selected_aspect else "") + label,
                        f"s:ia:{key}",
                    )
                    for key, label in list(_IMAGE_ASPECTS.items())[3:]
                ],
            ])
            selected_count = self._image_count(pending)
            rows.append([
                Button.callback(
                    ("✓ " if count == selected_count else "") + f"{count} шт.",
                    f"s:ic:{count}",
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
                    )
                    for model_id, meta in models[index:index + 2]
                ])
            selected_aspect = str(pending.get("aspect_ratio") or "portrait")
            rows.append([
                Button.callback(
                    ("✓ " if key == selected_aspect else "") + label,
                    f"s:va:{key}",
                )
                for key, label in _VIDEO_ASPECTS.items()
            ])
        rows.append([Button.callback(self.copy.menu_button, CB_MENU)])
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
        rows = [[Button.link(label, url)] for label, url in options]
        rows.append([Button.callback(self.copy.menu_button, CB_MENU)])
        return Keyboard.from_rows(rows)

    # -- helpers ----------------------------------------------------------

    def _set_await(self, uid: str, action: str, data: dict | None = None) -> None:
        self._state_store.set(uid, action, data)

    def _clear(self, uid: str) -> None:
        self._state_store.clear(uid)

    @staticmethod
    def _callback_id(cb: IncomingCallback) -> str:
        raw = cb.raw if isinstance(cb.raw, Mapping) else {}
        inner = raw.get("callback")
        if isinstance(inner, Mapping) and inner.get("callback_id"):
            return str(inner["callback_id"])
        return ""
