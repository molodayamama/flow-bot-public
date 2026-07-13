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
import os
import re
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from aiohttp import web

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


@dataclass(frozen=True)
class _Session:
    sid: str
    internal_user_id: int
    needs_cookie: bool


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
        self._media = _MediaStore(deps.config)

    def register(self, app: web.Application) -> None:
        app.router.add_get("/web/api/session", self.session)
        app.router.add_post("/web/api/generate", self.generate)
        app.router.add_post("/web/api/payment", self.payment)
        app.router.add_get("/web/api/media/{token}", self.media)

    def _session(
        self,
        request: web.Request,
        *,
        create: bool = True,
        allocate_identity: bool = True,
    ) -> _Session | None:
        encoded = request.cookies.get(self._d.config.cookie_name, "")
        sid = self._codec.decode(encoded)
        needs_cookie = sid is None
        if sid is None:
            if not create:
                return None
            sid = self._codec.new()
        if allocate_identity:
            internal_id = int(self._d.metrics.ensure_user_identity("web", sid))
            if internal_id >= 0:
                return None
        else:
            lookup = getattr(self._d.metrics, "get_user_identity", None)
            identity = lookup("web", sid) if callable(lookup) else None
            internal_id = int(identity.get("internal_user_id") or 0) if identity else 0
        return _Session(sid, internal_id, needs_cookie)

    def _allocate_identity(self, session: _Session) -> _Session | None:
        internal_id = int(self._d.metrics.ensure_user_identity("web", session.sid))
        if internal_id >= 0:
            return None
        return _Session(session.sid, internal_id, session.needs_cookie)

    def _response(self, payload: dict[str, Any], *, status: int = 200, session: _Session | None = None) -> web.Response:
        response = web.json_response(payload, status=status)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
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
        return response

    def _same_origin(self, request: web.Request) -> bool:
        return request.headers.get("Origin", "").rstrip("/") == self._d.config.public_origin

    async def _json_body(self, request: web.Request) -> dict[str, Any] | None:
        if request.content_type != "application/json":
            return None
        try:
            value = await request.json()
        except Exception:
            return None
        return value if isinstance(value, dict) else None

    async def session(self, request: web.Request) -> web.Response:
        session = self._session(request, allocate_identity=False)
        if session is None:
            return self._response({"error": "session_unavailable"}, status=503)
        packs = []
        if self._d.robokassa_configured():
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
            "balance": (
                self._credits.balance(session.internal_user_id)
                if session.internal_user_id < 0
                else self._d.config.starter_credits
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
        return self._response(payload, session=session)

    async def generate(self, request: web.Request) -> web.Response:
        if not self._same_origin(request):
            return self._response({"error": "origin_rejected"}, status=403)
        session = self._session(request, allocate_identity=False)
        if session is None:
            return self._response({"error": "session_unavailable"}, status=503)
        body = await self._json_body(request)
        if body is None:
            return self._response({"error": "invalid_json"}, status=400, session=session)
        mode = str(body.get("mode") or "").strip().lower()
        prompt = str(body.get("prompt") or "").strip()
        if mode not in _MODES or not 3 <= len(prompt) <= 2000:
            return self._response({"error": "invalid_request"}, status=400, session=session)

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

        session = self._allocate_identity(session)
        if session is None:
            return self._response({"error": "session_unavailable"}, status=503)

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
            "prompt": prompt,
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
        result["balance"] = self._credits.balance(session.internal_user_id)
        result["charged"] = price
        return self._response(result, session=session)

    async def _public_result(self, session: _Session, raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict) or raw.get("error"):
            raise ValueError("backend failure")
        images = []
        for item in raw.get("images") or []:
            if isinstance(item, dict):
                url = _safe_https_url(item.get("url"))
                if url:
                    images.append({"type": "image", "url": url})
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
            videos.append({"type": "video", "url": f"/web/api/media/{token}"})
        media = [*images, *videos]
        if not media:
            raise ValueError("backend returned no valid media")
        return {"media": media}

    async def payment(self, request: web.Request) -> web.Response:
        if not self._same_origin(request):
            return self._response({"error": "origin_rejected"}, status=403)
        session = self._session(request, allocate_identity=False)
        if session is None:
            return self._response({"error": "session_unavailable"}, status=503)
        body = await self._json_body(request)
        pack_id = str((body or {}).get("pack_id") or "")
        if pack_id not in self._d.public_pack_ids() or self._d.credit_pack(pack_id) is None:
            return self._response({"error": "invalid_pack"}, status=400, session=session)
        if not self._d.robokassa_configured():
            return self._response({"error": "payment_unavailable"}, status=503, session=session)
        session = self._allocate_identity(session)
        if session is None:
            return self._response({"error": "session_unavailable"}, status=503)
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
        session = self._session(request, create=False, allocate_identity=False)
        if session is None:
            raise web.HTTPNotFound()
        path = await self._media.get(session.sid, request.match_info.get("token", ""))
        if path is None:
            raise web.HTTPNotFound()
        response = web.FileResponse(path, headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'inline; filename="photozhab-video.mp4"',
        })
        response.content_type = "video/mp4"
        return response


def register_web_app(app: web.Application, deps: WebAppDeps) -> bool:
    """Register the public web API when explicitly enabled."""
    if not deps.config.enabled:
        return False
    adapter = _WebAdapter(deps)
    adapter.register(app)
    return True
