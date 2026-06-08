import asyncio
import getpass
from typing import Any

from .config import TelegramE2EConfig
from .runner import TelegramBatch


class TelethonBotClient:
    def __init__(self, config: TelegramE2EConfig):
        self._config = config
        self._client: Any | None = None

    async def send_and_collect(
        self,
        *,
        bot_username: str,
        text: str,
        timeout_sec: int,
        expected_min_photos: int = 0,
    ) -> TelegramBatch:
        client = await self._get_client()
        await self.ensure_authorized()
        try:
            entity = await client.get_entity(bot_username)
            sent = await client.send_message(entity, text)
            return await self._collect(
                entity,
                min_id=int(sent.id),
                timeout_sec=timeout_sec,
                expected_min_photos=expected_min_photos,
            )
        except Exception as exc:
            if exc.__class__.__name__ == "FloodWaitError":
                return TelegramBatch(
                    error_type=exc.__class__.__name__,
                    error_message="Telegram flood wait",
                    flood_wait_sec=getattr(exc, "seconds", None),
                )
            raise

    async def ensure_authorized(self) -> str:
        client = await self._get_client()
        if await client.is_user_authorized():
            return "authorized"

        assert self._config.tg_phone is not None
        try:
            await client.start(
                phone=self._config.tg_phone,
                code_callback=lambda: input("Telegram code: ").strip().replace(" ", ""),
                password=lambda: getpass.getpass("Telegram 2FA password: "),
            )
        except Exception as exc:
            name = exc.__class__.__name__
            if name == "PhoneCodeInvalidError":
                raise RuntimeError(
                    "Telegram login code was rejected. Check TELEGRAM_PHONE/TG_PHONE and use the newest code.",
                ) from exc
            if name == "PhoneCodeExpiredError":
                raise RuntimeError("Telegram login code expired. Re-run telegram-login to request a new code.") from exc
            raise RuntimeError(f"Telegram login failed: {name}") from exc
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram login failed: session is not authorized")
        return "authorized"

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from telethon import TelegramClient
            from telethon import connection
        except ImportError as exc:
            raise RuntimeError("Telethon is not installed. Install is an assumption: pip install telethon") from exc

        assert self._config.session_file is not None
        assert self._config.tg_api_id is not None
        assert self._config.tg_api_hash is not None
        assert self._config.tg_phone is not None
        self._config.session_file.parent.mkdir(parents=True, exist_ok=True)
        client_kwargs: dict[str, Any] = {}
        if self._config.mtproxy is not None:
            client_kwargs["connection"] = connection.ConnectionTcpMTProxyRandomizedIntermediate
            client_kwargs["proxy"] = (
                self._config.mtproxy.host,
                self._config.mtproxy.port,
                self._config.mtproxy.secret,
            )
        self._client = TelegramClient(
            str(self._config.session_file),
            self._config.tg_api_id,
            self._config.tg_api_hash,
            **client_kwargs,
        )
        await self._client.connect()
        return self._client

    async def _collect(
        self,
        entity: Any,
        *,
        min_id: int,
        timeout_sec: int,
        expected_min_photos: int = 0,
    ) -> TelegramBatch:
        deadline = asyncio.get_running_loop().time() + timeout_sec
        last_seen_ids: set[int] = set()
        text_parts: list[str] = []
        photo_count = 0
        quiet_ticks = 0

        while asyncio.get_running_loop().time() < deadline:
            seen_this_round = False
            async for message in self._client.iter_messages(entity, min_id=min_id, reverse=True, limit=30):
                message_id = int(getattr(message, "id", 0) or 0)
                if message_id in last_seen_ids:
                    continue
                last_seen_ids.add(message_id)
                seen_this_round = True
                body = getattr(message, "message", None)
                if body:
                    text_parts.append(str(body))
                if getattr(message, "photo", None) is not None:
                    photo_count += 1

            combined = "\n".join(text_parts)
            if photo_count >= max(1, expected_min_photos) and expected_min_photos > 0:
                return TelegramBatch(text=combined, photo_count=photo_count)
            if expected_min_photos == 0 and photo_count > 0:
                return TelegramBatch(text=combined, photo_count=photo_count)
            if _is_terminal_text(combined):
                return TelegramBatch(text=combined, photo_count=photo_count)
            quiet_ticks = 0 if seen_this_round else quiet_ticks + 1
            if expected_min_photos == 0 and combined and quiet_ticks >= 3:
                return TelegramBatch(text=combined, photo_count=photo_count)
            await asyncio.sleep(1)
        return TelegramBatch(text="\n".join(text_parts), photo_count=photo_count)


def make_telethon_client(config: TelegramE2EConfig) -> TelethonBotClient:
    return TelethonBotClient(config)


def _is_terminal_text(text: str) -> bool:
    lower = text.lower()
    markers = (
        "429",
        "403",
        "401",
        "too many requests",
        "rate limit",
        "quota",
        "error",
        "cooldown",
        "wait",
        "\u043f\u043e\u0434\u043e\u0436\u0434",
        "\u043e\u0448\u0438\u0431\u043a",
    )
    return any(marker in lower for marker in markers)
