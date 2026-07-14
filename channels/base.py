from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class PlatformUser:
    """A user identity as seen by one chat platform."""

    platform: str
    platform_user_id: str
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    language_code: str | None = None


@dataclass(frozen=True)
class Button:
    """Platform-neutral inline button."""

    text: str
    callback_data: str | None = None
    url: str | None = None
    intent: str | None = None

    def __post_init__(self) -> None:
        actions = [self.callback_data is not None, self.url is not None]
        if sum(actions) != 1:
            raise ValueError("Button must have exactly one action")
        if self.intent not in {None, "positive", "negative"}:
            raise ValueError("Button intent must be positive, negative or None")
        if self.url is not None and self.intent is not None:
            raise ValueError("Button intent is supported for callbacks only")

    @classmethod
    def callback(
        cls, text: str, callback_data: str, *, intent: str | None = None
    ) -> "Button":
        return cls(text=text, callback_data=callback_data, intent=intent)

    @classmethod
    def link(cls, text: str, url: str) -> "Button":
        return cls(text=text, url=url)


@dataclass(frozen=True)
class Keyboard:
    """Platform-neutral inline keyboard arranged as button rows."""

    rows: Sequence[Sequence[Button]] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "rows",
            tuple(tuple(button for button in row) for row in self.rows),
        )

    @classmethod
    def from_rows(cls, rows: Sequence[Sequence[Button]]) -> "Keyboard":
        return cls(rows=rows)

    @classmethod
    def single(cls, button: Button) -> "Keyboard":
        return cls(rows=((button,),))

    @property
    def is_empty(self) -> bool:
        return not self.rows


@dataclass(frozen=True)
class IncomingMessage:
    """Platform-neutral incoming message event."""

    platform: str
    user: PlatformUser
    chat_id: str
    message_id: str | None = None
    text: str | None = None
    photo_file_ids: Sequence[str] = ()
    raw: Any = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "photo_file_ids", tuple(self.photo_file_ids))


@dataclass(frozen=True)
class IncomingCallback:
    """Platform-neutral callback/button event."""

    platform: str
    user: PlatformUser
    chat_id: str
    data: str
    message_id: str | None = None
    raw: Any = None


@dataclass(frozen=True)
class PlatformFile:
    """A file reference already known to one chat platform."""

    file_id: str
    url: str | None = None
    size: int | None = None
    mime_type: str | None = None


@dataclass(frozen=True)
class PlatformMedia:
    """Platform-neutral outbound media (photo/video/document).

    At least one content source (``file``, ``url`` or ``bytes_data``) must be
    provided so a channel adapter always has something to send.
    """

    kind: str
    file: PlatformFile | None = None
    url: str | None = None
    bytes_data: bytes | None = None
    caption: str | None = None

    def __post_init__(self) -> None:
        if self.file is None and self.url is None and self.bytes_data is None:
            raise ValueError("PlatformMedia needs a file, url or bytes_data source")


@runtime_checkable
class BotPlatform(Protocol):
    """Minimal async channel contract consumed by shared scenarios."""

    name: str

    async def send_message(
        self,
        chat_id: str,
        text: str,
        keyboard: Keyboard | None = None,
    ) -> Any:
        ...

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        text: str,
        keyboard: Keyboard | None = None,
    ) -> Any:
        ...

    async def answer_callback(self, callback_id: str, text: str | None = None) -> Any:
        ...

    async def send_photo(
        self,
        chat_id: str,
        media: PlatformMedia,
        keyboard: Keyboard | None = None,
    ) -> Any:
        ...

    async def send_video(
        self,
        chat_id: str,
        media: PlatformMedia,
        keyboard: Keyboard | None = None,
    ) -> Any:
        ...

    async def send_document(
        self,
        chat_id: str,
        media: PlatformMedia,
        keyboard: Keyboard | None = None,
    ) -> Any:
        ...

    async def get_file_bytes(self, file: PlatformFile) -> bytes:
        ...
