"""MAX MVP handler.

Platform-neutral router that turns parsed MAX updates
(:class:`channels.base.IncomingMessage` / :class:`channels.base.IncomingCallback`)
into menu, balance, top-up and generation actions.

Scope is intentionally small (see ``Photozhab Core Split`` plan, Phase 7):

* ``/start`` / menu
* create image
* edit photo
* animate photo
* balance / help
* identity-bound Robokassa top-up links

Out of scope for the MVP: Telegram Stars, cross-platform referrals, shared
Telegram+MAX balance, seller flow, gallery/history/support, and the full video
wizard. Billing is identity-aware (a MAX user gets a separate negative internal
id, so a colliding Telegram numeric id never shares a balance).

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
    PRICE_PER_IMAGE,
    STARTER_CREDITS,
    action_price,
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
CB_ANIMATE = "m:animate"
CB_BALANCE = "m:balance"
CB_HELP = "m:help"
CB_TOPUP = "m:topup"

# Pending per-user flow markers.
AWAIT_CREATE_IMAGE = "create_image"
AWAIT_EDIT_PHOTO = "edit_photo"
AWAIT_ANIMATE_PHOTO = "animate_photo"

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
        "Я делаю картинки и короткие видео.\n\n"
        "• Создать картинку — опишите словами.\n"
        "• Изменить фото — пришлите фото и опишите правку.\n"
        "• Оживить фото — пришлите фото, получите видео.\n\n"
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
        self, *, internal_user_id: int, prompt: str
    ) -> Mapping[str, Any]:
        ...

    async def edit_photo(
        self, *, internal_user_id: int, prompt: str, photo_file_id: str
    ) -> Mapping[str, Any]:
        ...

    async def animate_photo(
        self, *, internal_user_id: int, prompt: str, photo_file_id: str
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

    def set(self, user_id: str, action: str) -> None:
        ...

    def clear(self, user_id: str) -> None:
        ...


class _MappingStateStore:
    def __init__(self, state: MutableMapping[str, dict]) -> None:
        self._state = state

    def get(self, user_id: str) -> dict | None:
        return self._state.get(user_id)

    def set(self, user_id: str, action: str) -> None:
        self._state[user_id] = {"await": action}

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
            self._set_await(uid, AWAIT_CREATE_IMAGE)
            await self.platform.send_message(chat, self.copy.ask_image_prompt)
        elif data == CB_EDIT_PHOTO:
            self._set_await(uid, AWAIT_EDIT_PHOTO)
            await self.platform.send_message(chat, self.copy.ask_edit_photo)
        elif data == CB_ANIMATE:
            self._set_await(uid, AWAIT_ANIMATE_PHOTO)
            await self.platform.send_message(chat, self.copy.ask_animate_photo)
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
            await self._run_create_image(chat, uid, prompt=text)
        elif action in (AWAIT_EDIT_PHOTO, AWAIT_ANIMATE_PHOTO):
            photo = msg.photo_file_ids[0] if msg.photo_file_ids else None
            if not photo:
                await self.platform.send_message(chat, self.copy.need_photo)
                return
            await self._run_photo_job(
                chat, uid, action=action, photo_file_id=photo, prompt=text
            )

    # -- flows ------------------------------------------------------------

    async def _run_create_image(self, chat: str, uid: str, *, prompt: str) -> None:
        prompt = (prompt or "").strip()
        if len(prompt) < _MIN_PROMPT_LEN:
            await self.platform.send_message(chat, self.copy.prompt_too_short)
            return
        await self._charged_generation(
            chat,
            uid,
            price=self.config.image_price,
            call=lambda internal_id: self.service.create_image(
                internal_user_id=internal_id, prompt=prompt
            ),
        )

    async def _run_photo_job(
        self,
        chat: str,
        uid: str,
        *,
        action: str,
        photo_file_id: str,
        prompt: str,
    ) -> None:
        prompt = (prompt or "").strip()
        if action == AWAIT_EDIT_PHOTO:
            price = self.config.edit_price
            call = lambda internal_id: self.service.edit_photo(  # noqa: E731
                internal_user_id=internal_id,
                prompt=prompt,
                photo_file_id=photo_file_id,
            )
        else:
            price = self.config.animate_price
            call = lambda internal_id: self.service.animate_photo(  # noqa: E731
                internal_user_id=internal_id,
                prompt=prompt,
                photo_file_id=photo_file_id,
            )
        await self._charged_generation(chat, uid, price=price, call=call)

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
        return Keyboard.from_rows(
            [
                [Button.callback(f"🎨 Создать картинку · {c.image_price} кр", CB_CREATE_IMAGE)],
                [Button.callback(f"✏️ Изменить фото · {c.edit_price} кр", CB_EDIT_PHOTO)],
                [Button.callback(f"🎬 Оживить фото · от {c.animate_price} кр", CB_ANIMATE)],
                [
                    Button.callback("💰 Баланс", CB_BALANCE),
                    Button.callback("❓ Помощь", CB_HELP),
                ],
                [Button.callback(self.copy.topup_button, CB_TOPUP)],
            ]
        )

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

    def _set_await(self, uid: str, action: str) -> None:
        self._state_store.set(uid, action)

    def _clear(self, uid: str) -> None:
        self._state_store.clear(uid)

    @staticmethod
    def _callback_id(cb: IncomingCallback) -> str:
        raw = cb.raw if isinstance(cb.raw, Mapping) else {}
        inner = raw.get("callback")
        if isinstance(inner, Mapping) and inner.get("callback_id"):
            return str(inner["callback_id"])
        return ""
