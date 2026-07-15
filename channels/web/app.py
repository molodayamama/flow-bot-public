"""Secure first-party web adapter over shared generation, credits and billing.

The browser receives only the public catalog and a signed opaque session. Flow
credentials, internal API tokens, provider responses and account identifiers
remain inside the consumer process.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

from aiohttp import ClientSession, ClientTimeout, web

from billing.credit_gate import NotEnoughCredits, open_credit_gate
from flow_core import (
    DEFAULT_IMAGE_MODEL,
    IMAGE_MODELS,
    VIDEO_MODELS,
    action_price,
    image_model_extra,
    price_gen,
    video_price,
)


_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{32,96}$")
_MEDIA_RE = re.compile(r"^[A-Za-z0-9_-]{24,96}$")
_IMAGE_MAGIC = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"RIFF",
)
_ASPECTS = frozenset({"portrait", "landscape", "square", "portrait_34", "landscape_43"})
_VIDEO_ASPECTS = frozenset({"portrait", "landscape"})
_MODES = frozenset({"image", "edit", "video", "animate"})
_CONFIRMATION_RE = re.compile(r"^[0-9]{6}$")
_TELEGRAM_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")
_WEB_CHAT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,96}$")
_REMOTE_MEDIA_HOSTS = (
    "flow-content.google",
    "labs.google",
    "googleusercontent.com",
    "googleapis.com",
    "google.com",
)
_REMOTE_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


@dataclass(frozen=True)
class WebAppConfig:
    enabled: bool
    session_secret: str
    public_origin: str = "https://photozhab.ru"
    cookie_name: str = "photozhab_web_session"
    cookie_secure: bool = True
    starter_credits: int = 0
    max_image_bytes: int = 10 * 1024 * 1024
    max_video_bytes: int = 64 * 1024 * 1024
    media_dir: Path = Path("/tmp/photozhab-web-media")
    media_ttl_seconds: int = 3600
    rate_limit_count: int = 6
    rate_limit_window_seconds: int = 60
    auth_session_ttl_seconds: int = 30 * 24 * 60 * 60
    auth_challenge_ttl_seconds: int = 10 * 60
    max_auth_age_seconds: int = 60 * 60
    telegram_bot_username: str = "photozhab_bot"
    max_bot_token: str = ""
    max_mini_app_url: str = "https://max.ru/se13461237_bot?startapp=web"
    yandex_client_id: str = ""
    yandex_client_secret: str = ""
    yandex_starter_credits: int = 30

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "WebAppConfig":
        source = os.environ if env is None else env

        def enabled(name: str, default: str = "0") -> bool:
            return str(source.get(name, default)).strip().lower() in {"1", "true", "yes", "on"}

        def integer(name: str, default: int, minimum: int, maximum: int) -> int:
            try:
                value = int(str(source.get(name, default)))
            except (TypeError, ValueError):
                value = default
            return max(minimum, min(maximum, value))

        return cls(
            enabled=enabled("WEB_APP_ENABLED"),
            session_secret=str(source.get("WEB_SESSION_SECRET", "")),
            public_origin=str(source.get("WEB_PUBLIC_ORIGIN", "https://photozhab.ru")).rstrip("/"),
            cookie_name=str(source.get("WEB_COOKIE_NAME", "photozhab_web_session")),
            cookie_secure=enabled("WEB_COOKIE_SECURE", "1"),
            starter_credits=integer("WEB_STARTER_CREDITS", 0, 0, 1000),
            max_image_bytes=integer("WEB_MAX_IMAGE_BYTES", 10 * 1024 * 1024, 1024, 20 * 1024 * 1024),
            max_video_bytes=integer("WEB_MAX_VIDEO_BYTES", 64 * 1024 * 1024, 1024, 100 * 1024 * 1024),
            media_dir=Path(str(source.get("WEB_MEDIA_DIR", "/tmp/photozhab-web-media"))),
            media_ttl_seconds=integer("WEB_MEDIA_TTL_SECONDS", 3600, 60, 86400),
            rate_limit_count=integer("WEB_RATE_LIMIT_COUNT", 6, 1, 60),
            rate_limit_window_seconds=integer("WEB_RATE_LIMIT_WINDOW_SECONDS", 60, 10, 3600),
            auth_session_ttl_seconds=integer(
                "WEB_AUTH_SESSION_TTL_SECONDS", 30 * 24 * 60 * 60, 3600, 365 * 24 * 60 * 60
            ),
            auth_challenge_ttl_seconds=integer(
                "WEB_AUTH_CHALLENGE_TTL_SECONDS", 10 * 60, 60, 3600
            ),
            max_auth_age_seconds=integer("WEB_MAX_AUTH_AGE_SECONDS", 60 * 60, 60, 86400),
            telegram_bot_username=str(source.get("BOT_USERNAME", "photozhab_bot")).lstrip("@"),
            max_bot_token=str(source.get("MAX_BOT_TOKEN", "")),
            max_mini_app_url=str(
                source.get("WEB_MAX_MINI_APP_URL", "https://max.ru/se13461237_bot?startapp=web")
            ),
            yandex_client_id=str(source.get("WEB_YANDEX_CLIENT_ID", "")),
            yandex_client_secret=str(source.get("WEB_YANDEX_CLIENT_SECRET", "")),
            yandex_starter_credits=integer("WEB_YANDEX_STARTER_CREDITS", 30, 0, 1000),
        )

    def validate(self) -> None:
        if not self.enabled:
            return
        if len(self.session_secret) < 32:
            raise ValueError("WEB_SESSION_SECRET must contain at least 32 characters")
        origin = urlparse(self.public_origin)
        if origin.scheme != "https" or not origin.netloc or origin.path not in {"", "/"}:
            raise ValueError("WEB_PUBLIC_ORIGIN must be an HTTPS origin without a path")
        if not re.fullmatch(r"[A-Za-z0-9_-]{3,64}", self.cookie_name):
            raise ValueError("WEB_COOKIE_NAME is invalid")
        if not _TELEGRAM_USERNAME_RE.fullmatch(self.telegram_bot_username):
            raise ValueError("BOT_USERNAME is invalid for website login")
        max_url = urlparse(self.max_mini_app_url)
        if max_url.scheme != "https" or max_url.hostname != "max.ru":
            raise ValueError("WEB_MAX_MINI_APP_URL must use https://max.ru")
        if bool(self.yandex_client_id) != bool(self.yandex_client_secret):
            raise ValueError("WEB_YANDEX_CLIENT_ID and WEB_YANDEX_CLIENT_SECRET must be set together")


@dataclass(frozen=True)
class WebAppDeps:
    config: WebAppConfig
    backend_generate: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
    metrics: Any
    credit_pack: Callable[[str], dict[str, Any] | None]
    public_pack_ids: Callable[[], list[str]]
    robokassa_pack_amount: Callable[[str], str]
    robokassa_configured: Callable[[], bool]
    new_inv_id: Callable[[], int]
    payment_url: Callable[[int, str, int, str], str]
    log: Any
    prompt_improve: Callable[[int, str, str], Awaitable[dict[str, Any]]] | None = None


@dataclass(frozen=True)
class _Session:
    sid: str
    internal_user_id: int
    needs_cookie: bool
    platform: str | None = None
    display_name: str | None = None

    @property
    def authenticated(self) -> bool:
        return bool(self.platform and self.internal_user_id)


class _SessionCodec:
    def __init__(self, secret: str) -> None:
        self._secret = secret.encode("utf-8")

    def new(self) -> str:
        return secrets.token_urlsafe(32)

    def encode(self, sid: str) -> str:
        digest = hmac.new(self._secret, sid.encode("ascii"), hashlib.sha256).digest()
        signature = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return f"{sid}.{signature}"

    def decode(self, value: str) -> str | None:
        try:
            sid, signature = str(value or "").split(".", 1)
        except ValueError:
            return None
        if not _SESSION_RE.fullmatch(sid) or not _SESSION_RE.fullmatch(signature):
            return None
        return sid if hmac.compare_digest(self.encode(sid), f"{sid}.{signature}") else None


class _CreditStore:
    def __init__(self, metrics: Any, starter_credits: int) -> None:
        self._metrics = metrics
        self._starter = starter_credits

    def balance(self, user_id: int) -> int:
        return int(self._metrics.credits_balance(user_id, self._starter))

    def charge(self, user_id: int, amount: int) -> bool:
        return bool(self._metrics.credits_charge(user_id, amount, self._starter))

    def refund(self, user_id: int, amount: int) -> None:
        self._metrics.credits_refund(user_id, amount)


class _Busy(Exception):
    pass


class _RateLimited(Exception):
    pass


class _GenerationGate:
    def __init__(self, count: int, window_seconds: int) -> None:
        self._count = count
        self._window = window_seconds
        self._lock = asyncio.Lock()
        self._inflight: set[str] = set()
        self._requests: dict[str, deque[float]] = defaultdict(deque)

    @asynccontextmanager
    async def enter(self, sid: str):
        now = time.monotonic()
        async with self._lock:
            if sid in self._inflight:
                raise _Busy
            history = self._requests[sid]
            while history and now - history[0] >= self._window:
                history.popleft()
            if len(history) >= self._count:
                raise _RateLimited
            history.append(now)
            self._inflight.add(sid)
        try:
            yield
        finally:
            async with self._lock:
                self._inflight.discard(sid)


@dataclass(frozen=True)
class _MediaEntry:
    path: Path
    owner: str
    expires_at: float


@dataclass(frozen=True)
class _RemoteMediaEntry:
    url: str
    owner: str
    expires_at: float


class _MediaStore:
    def __init__(self, config: WebAppConfig) -> None:
        self._dir = config.media_dir.resolve()
        self._ttl = config.media_ttl_seconds
        self._max_bytes = config.max_video_bytes
        self._entries: dict[str, _MediaEntry] = {}
        self._lock = asyncio.Lock()
        self._dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self._dir.chmod(0o700)
        except OSError:
            pass
        # Tokens live only in memory. After a process restart no old file can be
        # authorized, so remove only our own immediate-child MP4 artifacts.
        for orphan in self._dir.glob("web-*.mp4"):
            try:
                resolved = orphan.resolve()
                if resolved.parent == self._dir:
                    resolved.unlink(missing_ok=True)
            except OSError:
                continue

    async def put(self, owner: str, data: bytes) -> str:
        if not data or len(data) > self._max_bytes or b"ftyp" not in data[:64]:
            raise ValueError("invalid video")
        token = secrets.token_urlsafe(32)
        path = (self._dir / f"web-{token}.mp4").resolve()
        if path.parent != self._dir:
            raise ValueError("invalid media path")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        async with self._lock:
            await self._cleanup_locked()
            self._entries[token] = _MediaEntry(path, owner, time.monotonic() + self._ttl)
        return token

    async def get(self, owner: str, token: str) -> Path | None:
        if not _MEDIA_RE.fullmatch(token):
            return None
        async with self._lock:
            await self._cleanup_locked()
            entry = self._entries.get(token)
            if entry is None or entry.owner != owner or not entry.path.is_file():
                return None
            return entry.path

    async def _cleanup_locked(self) -> None:
        now = time.monotonic()
        expired = [token for token, entry in self._entries.items() if entry.expires_at <= now]
        for token in expired:
            entry = self._entries.pop(token)
            entry.path.unlink(missing_ok=True)


class _RemoteMediaStore:
    """Bounded owner-scoped registry for provider images already generated."""

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, _RemoteMediaEntry] = {}
        self._lock = asyncio.Lock()

    async def put(self, owner: str, url: str) -> str:
        if not _safe_remote_media_url(url):
            raise ValueError("unsupported remote media url")
        token = secrets.token_urlsafe(32)
        async with self._lock:
            self._cleanup_locked()
            self._entries[token] = _RemoteMediaEntry(
                url, owner, time.monotonic() + self._ttl
            )
        return token

    async def get(self, owner: str, token: str) -> str | None:
        if not _MEDIA_RE.fullmatch(token):
            return None
        async with self._lock:
            self._cleanup_locked()
            entry = self._entries.get(token)
            if entry is None or entry.owner != owner:
                return None
            return entry.url

    def _cleanup_locked(self) -> None:
        now = time.monotonic()
        for token in [
            token for token, entry in self._entries.items() if entry.expires_at <= now
        ]:
            self._entries.pop(token, None)


def _image_label(model_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": model_id,
        "label": str(meta.get("label") or model_id),
        "price": price_gen(1) + image_model_extra(model_id),
    }


def _video_label(model_id: str, meta: dict[str, Any]) -> str:
    if meta.get("family") == "veo":
        suffix = model_id.removeprefix("veo-").capitalize()
        return f"Veo {suffix}"
    return f"Omni Flash · {int(meta.get('duration') or 0)} сек"


def _safe_https_url(value: Any) -> str | None:
    url = str(value or "")
    parsed = urlparse(url)
    if len(url) > 4096 or parsed.scheme != "https" or not parsed.hostname:
        return None
    return url


def _safe_remote_media_url(value: Any) -> str | None:
    """Allow only known Google media hosts; never accept generic HTTPS URLs."""
    url = _safe_https_url(value)
    if url is None:
        return None
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if not any(host == allowed or host.endswith(f".{allowed}") for allowed in _REMOTE_MEDIA_HOSTS):
        return None
    return url


def _image_content_type(data: bytes) -> tuple[str, str] | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


def _decode_image(value: Any, maximum: int) -> str | None:
    raw = str(value or "")
    if raw.startswith("data:"):
        prefix, separator, raw = raw.partition(",")
        if not separator or not prefix.lower().startswith("data:image/") or ";base64" not in prefix.lower():
            return None
    if not raw or len(raw) > ((maximum + 2) // 3) * 4 + 8:
        return None
    try:
        data = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error):
        return None
    if not data or len(data) > maximum:
        return None
    is_webp = data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    if not (data.startswith(_IMAGE_MAGIC[0]) or data.startswith(_IMAGE_MAGIC[1]) or is_webp):
        return None
    return base64.b64encode(data).decode("ascii")


def _display_name(*parts: Any) -> str:
    value = " ".join(str(part or "").strip() for part in parts).strip()
    value = " ".join(value.split())
    return "".join(char for char in value if char.isprintable())[:80] or "Пользователь"


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _validate_max_init_data(
    init_data: str,
    bot_token: str,
    *,
    now: int,
    max_age_seconds: int,
) -> dict[str, Any] | None:
    """Validate MAX Mini App WebAppData according to the official HMAC contract."""
    if not bot_token or not init_data or len(init_data) > 8192:
        return None
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True, max_num_fields=32)
    except ValueError:
        return None
    keys = [key for key, _ in pairs]
    if not pairs or len(keys) != len(set(keys)) or keys.count("hash") != 1:
        return None
    values = dict(pairs)
    supplied_hash = values.get("hash", "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", supplied_hash):
        return None
    launch_params = "\n".join(
        f"{key}={value}" for key, value in sorted(pairs) if key != "hash"
    )
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(
        secret_key, launch_params.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, supplied_hash.lower()):
        return None
    try:
        auth_date = int(values.get("auth_date", ""))
        user = json.loads(values.get("user", ""))
        user_id = int(user.get("id"))
    except (TypeError, ValueError, json.JSONDecodeError, AttributeError):
        return None
    if user_id <= 0 or auth_date > now + 60 or now - auth_date > max_age_seconds:
        return None
    return {
        "platform": "max",
        "platform_user_id": str(user_id),
        "display_name": _display_name(
            user.get("first_name"), user.get("last_name"), user.get("username")
        ),
        "assertion_expires_at": auth_date + max_age_seconds,
    }


async def _fetch_yandex_identity(
    config: WebAppConfig,
    code: str,
    code_verifier: str,
) -> dict[str, str]:
    """Exchange a Yandex code and return the minimal identity; never persist tokens."""
    timeout = ClientTimeout(total=15, connect=5)
    async with ClientSession(timeout=timeout) as session:
        async with session.post(
            "https://oauth.yandex.ru/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": config.yandex_client_id,
                "client_secret": config.yandex_client_secret,
                "code_verifier": code_verifier,
            },
        ) as response:
            if response.status != 200:
                raise ValueError("yandex token exchange failed")
            token_payload = await response.json(content_type=None)
        access_token = str(token_payload.get("access_token") or "")
        if not access_token or len(access_token) > 4096:
            raise ValueError("yandex token missing")
        async with session.get(
            "https://login.yandex.ru/info?format=json",
            headers={"Authorization": f"OAuth {access_token}"},
        ) as response:
            if response.status != 200:
                raise ValueError("yandex profile fetch failed")
            profile = await response.json(content_type=None)
    user_id = str(profile.get("id") or "").strip()
    if not user_id or len(user_id) > 128:
        raise ValueError("yandex identity missing")
    return {
        "platform": "yandex",
        "platform_user_id": user_id,
        "display_name": _display_name(
            profile.get("display_name"), profile.get("real_name"), profile.get("login")
        ),
    }


class _WebAdapter:
    def __init__(self, deps: WebAppDeps) -> None:
        deps.config.validate()
        self._d = deps
        self._codec = _SessionCodec(deps.config.session_secret)
        self._credits = _CreditStore(deps.metrics, deps.config.starter_credits)
        self._gate = _GenerationGate(
            deps.config.rate_limit_count, deps.config.rate_limit_window_seconds
        )
        self._payment_gate = _GenerationGate(10, 60)
        self._experiment_gate = _GenerationGate(20, 60)
        self._prompt_gate = _GenerationGate(10, 60)
        self._media = _MediaStore(deps.config)
        self._remote_media = _RemoteMediaStore(deps.config.media_ttl_seconds)

    def register(self, app: web.Application) -> None:
        app.router.add_get("/web/api/session", self.session)
        app.router.add_get("/web/api/chats", self.chats)
        app.router.add_get("/web/api/chats/{chat_id}", self.chat)
        app.router.add_post("/web/api/auth/telegram/start", self.telegram_start)
        app.router.add_post("/web/api/auth/telegram/complete", self.telegram_complete)
        app.router.add_post("/web/api/auth/max", self.max_complete)
        app.router.add_get("/web/api/auth/yandex/start", self.yandex_start)
        app.router.add_get("/web/api/auth/yandex/callback", self.yandex_callback)
        app.router.add_post("/web/api/auth/logout", self.logout)
        app.router.add_post("/web/api/experiment", self.experiment)
        app.router.add_post("/web/api/prompt-improve", self.prompt_improve)
        app.router.add_post("/web/api/generate", self.generate)
        app.router.add_post("/web/api/payment", self.payment)
        app.router.add_get("/web/api/download/{token}", self.download)
        app.router.add_get("/web/api/media/{token}", self.media)

    def _session(
        self,
        request: web.Request,
        *,
        create: bool = True,
    ) -> _Session | None:
        encoded = request.cookies.get(self._d.config.cookie_name, "")
        sid = self._codec.decode(encoded)
        needs_cookie = sid is None
        if sid is None:
            if not create:
                return None
            sid = self._codec.new()
        identity = self._d.metrics.get_web_auth_session(sid)
        if not identity:
            return _Session(sid, 0, needs_cookie)
        internal_id = int(identity.get("internal_user_id") or 0)
        if not internal_id:
            return _Session(sid, 0, needs_cookie)
        observe_user = getattr(self._d.metrics, "upsert_user", None)
        if callable(observe_user):
            try:
                observe_user(
                    internal_id,
                    first_name=str(identity.get("display_name") or "") or None,
                    channel=f"web_{str(identity.get('platform') or '').strip().lower()}",
                )
            except Exception:
                self._d.log.warning("web user projection failed", exc_info=True)
        return _Session(
            sid,
            internal_id,
            needs_cookie,
            str(identity.get("platform") or ""),
            str(identity.get("display_name") or "") or None,
        )

    def _bind_authenticated(self, session: _Session, identity: dict[str, Any]) -> _Session | None:
        new_sid = self._codec.new()
        bound = self._d.metrics.bind_web_auth_session(
            new_sid,
            str(identity.get("platform") or ""),
            str(identity.get("platform_user_id") or ""),
            _display_name(identity.get("display_name")),
            expires_at=int(time.time()) + self._d.config.auth_session_ttl_seconds,
        )
        if not bound:
            return None
        self._d.metrics.delete_web_auth_session(session.sid)
        return _Session(
            new_sid,
            int(bound["internal_user_id"]),
            True,
            str(bound["platform"]),
            str(bound.get("display_name") or "") or None,
        )

    def _response(self, payload: dict[str, Any], *, status: int = 200, session: _Session | None = None) -> web.Response:
        response = web.json_response(payload, status=status)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        self._set_session_cookie(response, session)
        return response

    def _set_session_cookie(
        self, response: web.StreamResponse, session: _Session | None
    ) -> None:
        if session is not None and session.needs_cookie:
            response.set_cookie(
                self._d.config.cookie_name,
                self._codec.encode(session.sid),
                max_age=365 * 24 * 60 * 60,
                httponly=True,
                secure=self._d.config.cookie_secure,
                samesite="Lax",
                path="/",
            )

    def _same_origin(self, request: web.Request) -> bool:
        return request.headers.get("Origin", "").rstrip("/") == self._d.config.public_origin

    def _auth_required(self, session: _Session) -> web.Response:
        return self._response({"error": "auth_required"}, status=401, session=session)

    async def _json_body(self, request: web.Request) -> dict[str, Any] | None:
        if request.content_type != "application/json":
            return None
        try:
            value = await request.json()
        except Exception:
            return None
        return value if isinstance(value, dict) else None

    async def session(self, request: web.Request) -> web.Response:
        session = self._session(request)
        if session is None:
            return self._response({"error": "session_unavailable"}, status=503)
        packs = []
        if session.authenticated and self._d.robokassa_configured():
            for pack_id in self._d.public_pack_ids():
                pack = self._d.credit_pack(pack_id)
                if pack:
                    packs.append({
                        "id": pack_id,
                        "credits": int(pack["credits"]),
                        "rub": self._d.robokassa_pack_amount(pack_id),
                        "best": bool(pack.get("best")),
                    })
        payload = {
            "authenticated": session.authenticated,
            "identity": (
                {"provider": session.platform, "display_name": session.display_name}
                if session.authenticated
                else None
            ),
            "auth": {
                "providers": {
                    "telegram": True,
                    "max": bool(self._d.config.max_bot_token),
                    "yandex": bool(
                        self._d.config.yandex_client_id and self._d.config.yandex_client_secret
                    ),
                },
                "max_url": self._d.config.max_mini_app_url,
            },
            "balance": (
                self._credits.balance(session.internal_user_id) if session.authenticated else 0
            ),
            "packs": packs,
            "image_models": [_image_label(mid, meta) for mid, meta in IMAGE_MODELS.items()],
            "video_models": [
                {
                    "id": mid,
                    "label": _video_label(mid, meta),
                    "duration": int(meta.get("duration") or 0),
                    "price": video_price(mid, 1),
                    "animate_price": video_price(mid, 1, "ingredients"),
                    "confirmed": bool(meta.get("confirmed")),
                }
                for mid, meta in VIDEO_MODELS.items()
            ],
            "prices": {
                "image": price_gen(1),
                "edit": action_price("edit"),
                "video": min(video_price(mid, 1) for mid in VIDEO_MODELS),
                "animate": min(video_price(mid, 1, "ingredients") for mid in VIDEO_MODELS),
            },
            "limits": {"prompt": 2000, "image_bytes": self._d.config.max_image_bytes},
        }
        if session.authenticated:
            payload["history"] = self._d.metrics.get_prompt_history(
                session.internal_user_id, limit=20
            )
            payload["chats"] = self._d.metrics.list_web_chats(
                session.internal_user_id, limit=30
            )
        return self._response(payload, session=session)

    async def chats(self, request: web.Request) -> web.Response:
        """List authenticated website conversations owned by this account."""
        session = self._session(request)
        if session is None:
            return self._response({"error": "session_unavailable"}, status=503)
        if not session.authenticated:
            return self._auth_required(session)
        return self._response(
            {"chats": self._d.metrics.list_web_chats(session.internal_user_id, limit=30)},
            session=session,
        )

    async def chat(self, request: web.Request) -> web.Response:
        """Load one owned chat; ids are opaque and never accepted cross-user."""
        session = self._session(request, create=False)
        if session is None or not session.authenticated:
            return self._auth_required(session or _Session("", 0, False))
        chat_id = str(request.match_info.get("chat_id") or "")
        if not _WEB_CHAT_ID_RE.fullmatch(chat_id):
            return self._response({"error": "invalid_chat"}, status=400, session=session)
        value = self._d.metrics.get_web_chat(session.internal_user_id, chat_id, limit=80)
        if value is None:
            return self._response({"error": "chat_not_found"}, status=404, session=session)
        await self._refresh_download_urls(session, value)
        return self._response(value, session=session)

    async def _refresh_download_urls(self, session: _Session, chat: dict[str, Any]) -> None:
        """Issue fresh owner-scoped image download tokens when restoring a chat."""
        for message in chat.get("messages") or []:
            for media in message.get("media") or []:
                if not isinstance(media, dict) or media.get("type") != "image":
                    continue
                url = _safe_remote_media_url(media.get("url"))
                if url is None:
                    media.pop("download_url", None)
                    continue
                token = await self._remote_media.put(session.sid, url)
                media["download_url"] = f"/web/api/download/{token}"

    @staticmethod
    def _context_prompt(chat: dict | None, prompt: str) -> str:
        """Bound prior turns and delimit them from the new user instruction."""
        if not chat:
            return prompt
        turns = []
        for item in (chat.get("messages") or [])[-10:]:
            role = "user" if item.get("role") == "user" else "assistant"
            text = str(item.get("text") or "").strip()
            if text:
                turns.append(f"{role}: {text[:1200]}")
        if not turns:
            return prompt
        context = "\n".join(turns)
        return (
            "Контекст предыдущих сообщений этого чата. Используй его только "
            "для понимания ссылок и продолжения задачи; текущая инструкция "
            "имеет приоритет.\n<chat_context>\n"
            f"{context}\n</chat_context>\nТекущая инструкция пользователя:\n{prompt}"
        )[:8000]

    async def experiment(self, request: web.Request) -> web.Response:
        """Record a bounded, non-identifying public landing experiment event."""
        if not self._same_origin(request):
            return self._response({"error": "origin_rejected"}, status=403)
        session = self._session(request)
        if session is None:
            return self._response({"error": "session_unavailable"}, status=503)
        if request.content_length is not None and request.content_length > 512:
            return self._response({"error": "invalid_experiment"}, status=413, session=session)
        body = await self._json_body(request)
        name = str((body or {}).get("name") or "")
        variant = str((body or {}).get("variant") or "")
        event = str((body or {}).get("event") or "")
        if (
            name != "landing_hero"
            or variant not in {"a", "b", "c", "d", "e"}
            or event not in {"exposure", "cta"}
        ):
            return self._response({"error": "invalid_experiment"}, status=400, session=session)
        try:
            async with self._experiment_gate.enter(session.sid):
                self._d.metrics.log_event(
                    f"landing_hero_{event}",
                    user_id=None,
                    source="web",
                    payload={"variant": variant},
                )
        except (_Busy, _RateLimited):
            return self._response({"error": "rate_limited"}, status=429, session=session)
        return self._response({"ok": True}, session=session)

    async def telegram_start(self, request: web.Request) -> web.Response:
        if not self._same_origin(request):
            return self._response({"error": "origin_rejected"}, status=403)
        session = self._session(request)
        if session is None:
            return self._response({"error": "session_unavailable"}, status=503)
        challenge = secrets.token_urlsafe(24)
        expires_in = self._d.config.auth_challenge_ttl_seconds
        created = self._d.metrics.create_web_login_challenge(
            session.sid,
            challenge,
            expires_at=int(time.time()) + expires_in,
        )
        if not created:
            return self._response({"error": "auth_unavailable"}, status=503, session=session)
        url = (
            f"https://t.me/{self._d.config.telegram_bot_username}"
            f"?start=web_{challenge}"
        )
        return self._response(
            {"url": url, "expires_in": expires_in}, session=session
        )

    async def telegram_complete(self, request: web.Request) -> web.Response:
        if not self._same_origin(request):
            return self._response({"error": "origin_rejected"}, status=403)
        session = self._session(request)
        if session is None:
            return self._response({"error": "session_unavailable"}, status=503)
        body = await self._json_body(request)
        code = str((body or {}).get("code") or "").strip()
        if not _CONFIRMATION_RE.fullmatch(code):
            return self._response({"error": "invalid_code"}, status=400, session=session)
        identity = self._d.metrics.complete_web_login_challenge(session.sid, code)
        if not identity:
            return self._response({"error": "invalid_code"}, status=401, session=session)
        authenticated = self._bind_authenticated(session, identity)
        if authenticated is None:
            return self._response({"error": "auth_unavailable"}, status=503, session=session)
        self._d.metrics.log_event(
            "web_login_success",
            user_id=authenticated.internal_user_id,
            source="telegram",
        )
        return self._response(
            {
                "authenticated": True,
                "identity": {
                    "provider": authenticated.platform,
                    "display_name": authenticated.display_name,
                },
                "balance": self._credits.balance(authenticated.internal_user_id),
            },
            session=authenticated,
        )

    async def max_complete(self, request: web.Request) -> web.Response:
        if not self._same_origin(request):
            return self._response({"error": "origin_rejected"}, status=403)
        session = self._session(request)
        if session is None:
            return self._response({"error": "session_unavailable"}, status=503)
        body = await self._json_body(request)
        init_data = str((body or {}).get("init_data") or "")
        now = int(time.time())
        identity = _validate_max_init_data(
            init_data,
            self._d.config.max_bot_token,
            now=now,
            max_age_seconds=self._d.config.max_auth_age_seconds,
        )
        if identity is None:
            return self._response({"error": "invalid_max_data"}, status=401, session=session)
        consumed = self._d.metrics.consume_web_auth_assertion(
            "max",
            init_data,
            expires_at=int(identity.pop("assertion_expires_at")),
            now=now,
        )
        if not consumed:
            return self._response({"error": "auth_replayed"}, status=409, session=session)
        authenticated = self._bind_authenticated(session, identity)
        if authenticated is None:
            return self._response({"error": "auth_unavailable"}, status=503, session=session)
        self._d.metrics.log_event(
            "web_login_success", user_id=authenticated.internal_user_id, source="max"
        )
        return self._response(
            {
                "authenticated": True,
                "identity": {
                    "provider": authenticated.platform,
                    "display_name": authenticated.display_name,
                },
                "balance": self._credits.balance(authenticated.internal_user_id),
            },
            session=authenticated,
        )

    async def yandex_start(self, request: web.Request) -> web.StreamResponse:
        if not (self._d.config.yandex_client_id and self._d.config.yandex_client_secret):
            raise web.HTTPFound("/app.html?auth=unavailable")
        session = self._session(request)
        if session is None:
            raise web.HTTPServiceUnavailable()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        created = self._d.metrics.create_web_oauth_state(
            session.sid,
            state,
            "yandex",
            verifier,
            expires_at=int(time.time()) + self._d.config.auth_challenge_ttl_seconds,
        )
        if not created:
            raise web.HTTPServiceUnavailable()
        params = {
            "response_type": "code",
            "client_id": self._d.config.yandex_client_id,
            "redirect_uri": f"{self._d.config.public_origin}/web/api/auth/yandex/callback",
            "scope": "login:info",
            "state": state,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
        response = web.HTTPFound(f"https://oauth.yandex.ru/authorize?{urlencode(params)}")
        response.headers["Cache-Control"] = "no-store"
        self._set_session_cookie(response, session)
        raise response

    async def yandex_callback(self, request: web.Request) -> web.StreamResponse:
        session = self._session(request, create=False)
        values = {
            name: request.query.getall(name, []) for name in ("code", "state", "error")
        }
        if (
            session is None
            or len(values["code"]) != 1
            or len(values["state"]) != 1
            or values["error"]
        ):
            raise web.HTTPFound("/app.html?auth=failed")
        code = str(values["code"][0])
        state = str(values["state"][0])
        if not 1 <= len(code) <= 2048 or not 20 <= len(state) <= 256:
            raise web.HTTPFound("/app.html?auth=failed")
        verifier = self._d.metrics.consume_web_oauth_state(
            session.sid, state, "yandex"
        )
        if not verifier:
            raise web.HTTPFound("/app.html?auth=failed")
        try:
            identity = await _fetch_yandex_identity(self._d.config, code, verifier)
        except Exception:
            self._d.log.warning("yandex web login failed")
            raise web.HTTPFound("/app.html?auth=failed")
        authenticated = self._bind_authenticated(session, identity)
        if authenticated is None:
            raise web.HTTPFound("/app.html?auth=failed")
        welcome = self._d.metrics.grant_identity_welcome_credits(
            "yandex",
            str(identity.get("platform_user_id") or ""),
            authenticated.internal_user_id,
            self._d.config.yandex_starter_credits,
        )
        if welcome is None:
            self._d.metrics.delete_web_auth_session(authenticated.sid)
            raise web.HTTPFound("/app.html?auth=failed")
        self._d.metrics.log_event(
            "web_login_success", user_id=authenticated.internal_user_id, source="yandex"
        )
        response = web.HTTPFound("/app.html?auth=success")
        response.headers["Cache-Control"] = "no-store"
        self._set_session_cookie(response, authenticated)
        raise response

    async def logout(self, request: web.Request) -> web.Response:
        if not self._same_origin(request):
            return self._response({"error": "origin_rejected"}, status=403)
        session = self._session(request)
        if session is None:
            return self._response({"error": "session_unavailable"}, status=503)
        self._d.metrics.delete_web_auth_session(session.sid)
        fresh = _Session(self._codec.new(), 0, True)
        return self._response({"authenticated": False, "balance": 0}, session=fresh)

    async def generate(self, request: web.Request) -> web.Response:
        if not self._same_origin(request):
            return self._response({"error": "origin_rejected"}, status=403)
        session = self._session(request)
        if session is None:
            return self._response({"error": "session_unavailable"}, status=503)
        if not session.authenticated:
            return self._auth_required(session)
        body = await self._json_body(request)
        if body is None:
            return self._response({"error": "invalid_json"}, status=400, session=session)
        mode = str(body.get("mode") or "").strip().lower()
        prompt = str(body.get("prompt") or "").strip()
        if mode not in _MODES or not 3 <= len(prompt) <= 2000:
            return self._response({"error": "invalid_request"}, status=400, session=session)

        requested_chat_id = str(body.get("chat_id") or "").strip()
        chat = None
        if requested_chat_id:
            if not _WEB_CHAT_ID_RE.fullmatch(requested_chat_id):
                return self._response({"error": "invalid_chat"}, status=400, session=session)
            chat = self._d.metrics.get_web_chat(
                session.internal_user_id, requested_chat_id, limit=80
            )
            if chat is None:
                return self._response({"error": "chat_not_found"}, status=404, session=session)

        image_model = str(body.get("image_model") or DEFAULT_IMAGE_MODEL).strip().lower()
        video_model = str(body.get("video_model") or "omni-flash-4s").strip().lower()
        if image_model not in IMAGE_MODELS or video_model not in VIDEO_MODELS:
            return self._response({"error": "invalid_model"}, status=400, session=session)
        try:
            count = int(body.get("count") or 1)
        except (TypeError, ValueError):
            count = 0
        if mode != "image":
            count = 1
        if not 1 <= count <= 4:
            return self._response({"error": "invalid_count"}, status=400, session=session)
        aspect = str(body.get("aspect") or "portrait").strip().lower()
        allowed_aspects = _VIDEO_ASPECTS if mode in {"video", "animate"} else _ASPECTS
        if aspect not in allowed_aspects:
            return self._response({"error": "invalid_aspect"}, status=400, session=session)

        image_b64: str | None = None
        if mode in {"edit", "animate"}:
            image_b64 = _decode_image(body.get("image_b64"), self._d.config.max_image_bytes)
            if image_b64 is None:
                return self._response({"error": "invalid_image"}, status=400, session=session)

        if mode == "image":
            price = price_gen(count) + image_model_extra(image_model) * count
            kind = "image"
        elif mode == "edit":
            price = action_price("edit") + image_model_extra(image_model)
            kind = "i2i"
        elif mode == "video":
            price = video_price(video_model, 1, "text")
            kind = "video_text"
        else:
            price = video_price(video_model, 1, "ingredients")
            kind = "video_ingredients"

        backend_request: dict[str, Any] = {
            "kind": kind,
            "prompt": self._context_prompt(chat, prompt),
            "user_id": session.internal_user_id,
            "aspect_ratio": aspect,
            "num_images": count,
            "image_model": image_model,
            "video_model": video_model,
        }
        if image_b64 is not None:
            backend_request["image_b64"] = image_b64

        try:
            async with self._gate.enter(session.sid):
                async with open_credit_gate(
                    self._credits, session.internal_user_id, price
                ) as charge:
                    raw = await self._d.backend_generate(backend_request)
                    result = await self._public_result(session, raw)
                    charge.ok = True
        except NotEnoughCredits:
            return self._response(
                {
                    "error": "insufficient_credits",
                    "balance": self._credits.balance(session.internal_user_id),
                    "required": price,
                },
                status=402,
                session=session,
            )
        except _Busy:
            return self._response({"error": "generation_in_progress"}, status=409, session=session)
        except _RateLimited:
            return self._response({"error": "rate_limited"}, status=429, session=session)
        except Exception:
            self._d.log.exception("web generation failed mode=%s", mode)
            return self._response({"error": "generation_failed"}, status=502, session=session)

        self._d.metrics.log_event(
            "web_generation_success",
            user_id=session.internal_user_id,
            source="web",
            payload={"mode": mode, "model": image_model if mode in {"image", "edit"} else video_model, "count": count, "price": price},
        )
        self._d.metrics.save_prompt_history(session.internal_user_id, prompt)
        chat_id = requested_chat_id or secrets.token_urlsafe(18)
        if not _WEB_CHAT_ID_RE.fullmatch(chat_id):
            self._d.log.warning("web chat id generation failed after successful request")
        elif self._d.metrics.create_web_chat(session.internal_user_id, chat_id, prompt[:120]):
            if not self._d.metrics.append_web_chat_message(
                session.internal_user_id,
                chat_id,
                role="user",
                text=prompt,
                mode=mode,
                model=image_model if mode in {"image", "edit"} else video_model,
                aspect=aspect,
            ):
                self._d.log.warning("web chat user message persistence failed")
            result_balance = self._credits.balance(session.internal_user_id)
            if not self._d.metrics.append_web_chat_message(
                session.internal_user_id,
                chat_id,
                role="assistant",
                text="Готово",
                media=result.get("media") or [],
                mode=mode,
                model=image_model if mode in {"image", "edit"} else video_model,
                aspect=aspect,
                charged=price,
                balance=result_balance,
            ):
                self._d.log.warning("web chat assistant message persistence failed")
            result["chat_id"] = chat_id
            chat_rows = self._d.metrics.list_web_chats(session.internal_user_id, limit=30)
            result["chat"] = next((row for row in chat_rows if row.get("chat_id") == chat_id), {
                "chat_id": chat_id, "title": prompt[:120], "messages": 2,
            })
        else:
            # Generation and billing already succeeded.  Never turn a storage
            # outage into a paid 502; the prompt ledger still records the turn.
            self._d.log.warning("web chat creation failed after successful request")
        result["balance"] = self._credits.balance(session.internal_user_id)
        result["charged"] = price
        return self._response(result, session=session)

    @staticmethod
    def _public_prompt_result(raw: dict[str, Any]) -> dict[str, Any]:
        """Expose only the parsed prompt text returned by the Flow agent."""
        if not isinstance(raw, dict) or raw.get("error"):
            raise ValueError("backend failure")
        variants: list[dict[str, str]] = []
        for item in raw.get("variants") or []:
            if not isinstance(item, dict):
                continue
            prompt = str(item.get("prompt") or "").strip()
            if not 3 <= len(prompt) <= 2000:
                continue
            title = str(item.get("title") or "Вариант").strip()
            variants.append({
                "tag": (title[:64] or "Вариант"),
                "text": prompt,
            })
        single = str(raw.get("single") or "").strip()
        if not variants and 3 <= len(single) <= 2000:
            variants.append({"tag": "Улучшенный", "text": single})
        if not variants:
            raise ValueError("backend returned no valid variants")
        return {"variants": variants[:3]}

    async def prompt_improve(self, request: web.Request) -> web.Response:
        """Improve a prompt through the same Flow agent as Telegram."""
        if not self._same_origin(request):
            return self._response({"error": "origin_rejected"}, status=403)
        session = self._session(request)
        if session is None:
            return self._response({"error": "session_unavailable"}, status=503)
        if not session.authenticated:
            return self._auth_required(session)
        if self._d.prompt_improve is None:
            return self._response(
                {"error": "prompt_improve_unavailable"}, status=503, session=session
            )
        body = await self._json_body(request)
        prompt = str((body or {}).get("prompt") or "").strip()
        mode = str((body or {}).get("mode") or "image").strip().lower()
        if mode not in _MODES or not 3 <= len(prompt) <= 2000:
            return self._response({"error": "invalid_request"}, status=400, session=session)
        price = action_price("prompt_improve")
        try:
            async with self._prompt_gate.enter(session.sid):
                async with open_credit_gate(
                    self._credits, session.internal_user_id, price
                ) as charge:
                    raw = await self._d.prompt_improve(
                        session.internal_user_id, prompt, mode
                    )
                    result = self._public_prompt_result(raw)
                    charge.ok = True
        except NotEnoughCredits:
            return self._response(
                {
                    "error": "insufficient_credits",
                    "balance": self._credits.balance(session.internal_user_id),
                    "required": price,
                },
                status=402,
                session=session,
            )
        except (_Busy, _RateLimited):
            return self._response({"error": "rate_limited"}, status=429, session=session)
        except Exception:
            self._d.log.exception("web prompt improvement failed mode=%s", mode)
            return self._response(
                {"error": "prompt_improve_failed"}, status=502, session=session
            )
        self._d.metrics.log_event(
            "prompt_improve", user_id=session.internal_user_id, source="web",
            payload={"mode": mode, "price": price},
        )
        result["balance"] = self._credits.balance(session.internal_user_id)
        result["charged"] = price
        return self._response(result, session=session)

    async def _public_result(self, session: _Session, raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict) or raw.get("error"):
            raise ValueError("backend failure")
        images = []
        for item in raw.get("images") or []:
            if isinstance(item, dict):
                url = _safe_remote_media_url(item.get("url"))
                if url:
                    token = await self._remote_media.put(session.sid, url)
                    images.append({
                        "type": "image",
                        "url": url,
                        "download_url": f"/web/api/download/{token}",
                    })
        videos = []
        for item in raw.get("videos") or []:
            if not isinstance(item, dict):
                continue
            encoded = str(item.get("video_b64") or "")
            if not encoded or len(encoded) > ((self._d.config.max_video_bytes + 2) // 3) * 4 + 8:
                continue
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                continue
            try:
                token = await self._media.put(session.sid, data)
            except ValueError:
                continue
            videos.append({
                "type": "video",
                "url": f"/web/api/media/{token}",
                "download_url": f"/web/api/media/{token}?download=1",
            })
        media = [*images, *videos]
        if not media:
            raise ValueError("backend returned no valid media")
        return {"media": media}

    async def payment(self, request: web.Request) -> web.Response:
        if not self._same_origin(request):
            return self._response({"error": "origin_rejected"}, status=403)
        session = self._session(request)
        if session is None:
            return self._response({"error": "session_unavailable"}, status=503)
        if not session.authenticated:
            return self._auth_required(session)
        body = await self._json_body(request)
        pack_id = str((body or {}).get("pack_id") or "")
        if pack_id not in self._d.public_pack_ids() or self._d.credit_pack(pack_id) is None:
            return self._response({"error": "invalid_pack"}, status=400, session=session)
        if not self._d.robokassa_configured():
            return self._response({"error": "payment_unavailable"}, status=503, session=session)
        try:
            async with self._payment_gate.enter(session.sid):
                url = self._d.payment_url(
                    session.internal_user_id, pack_id, self._d.new_inv_id(), "web"
                )
        except (_Busy, _RateLimited):
            return self._response({"error": "rate_limited"}, status=429, session=session)
        except Exception:
            self._d.log.exception("web payment invoice build failed pack=%s", pack_id)
            return self._response({"error": "payment_unavailable"}, status=503, session=session)
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            return self._response({"error": "payment_unavailable"}, status=503, session=session)
        return self._response({"url": url}, session=session)

    async def media(self, request: web.Request) -> web.StreamResponse:
        session = self._session(request, create=False)
        if session is None or not session.authenticated:
            raise web.HTTPNotFound()
        path = await self._media.get(session.sid, request.match_info.get("token", ""))
        if path is None:
            raise web.HTTPNotFound()
        disposition = "attachment" if request.query.get("download") == "1" else "inline"
        response = web.FileResponse(path, headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'{disposition}; filename="photozhab-video.mp4"',
        })
        response.content_type = "video/mp4"
        return response

    async def download(self, request: web.Request) -> web.StreamResponse:
        """Proxy a generated Google image as a real, owner-scoped attachment."""
        session = self._session(request, create=False)
        if session is None or not session.authenticated:
            raise web.HTTPNotFound()
        url = await self._remote_media.get(
            session.sid, request.match_info.get("token", "")
        )
        if url is None:
            raise web.HTTPNotFound()
        try:
            body, content_type, extension = await self._fetch_remote_image(url)
        except Exception:
            self._d.log.warning("web image download proxy failed", exc_info=True)
            raise web.HTTPBadGateway()
        return web.Response(
            body=body,
            content_type=content_type,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": (
                    f'attachment; filename="photozhab-image.{extension}"'
                ),
            },
        )

    async def _fetch_remote_image(self, initial_url: str) -> tuple[bytes, str, str]:
        timeout = ClientTimeout(total=30, connect=8)
        current = initial_url
        async with ClientSession(timeout=timeout) as client:
            for redirect_count in range(5):
                safe_url = _safe_remote_media_url(current)
                if safe_url is None:
                    raise ValueError("unsafe media redirect")
                async with client.get(
                    safe_url,
                    allow_redirects=False,
                    headers={"Accept": "image/avif,image/webp,image/png,image/jpeg"},
                ) as response:
                    if response.status in _REMOTE_REDIRECT_STATUSES:
                        location = response.headers.get("Location", "")
                        if not location or redirect_count == 4:
                            raise ValueError("invalid media redirect")
                        current = urljoin(safe_url, location)
                        continue
                    if response.status != 200:
                        raise ValueError("media download failed")
                    length = response.headers.get("Content-Length", "")
                    if length:
                        try:
                            if int(length) > self._d.config.max_image_bytes:
                                raise ValueError("media too large")
                        except ValueError as exc:
                            raise ValueError("invalid media length") from exc
                    chunks = bytearray()
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        chunks.extend(chunk)
                        if len(chunks) > self._d.config.max_image_bytes:
                            raise ValueError("media too large")
                    kind = _image_content_type(bytes(chunks))
                    if kind is None:
                        raise ValueError("invalid image payload")
                    return bytes(chunks), kind[0], kind[1]
        raise ValueError("too many media redirects")


def register_web_app(app: web.Application, deps: WebAppDeps) -> bool:
    """Register the public web API when explicitly enabled."""
    if not deps.config.enabled:
        return False
    adapter = _WebAdapter(deps)
    adapter.register(app)
    return True
