"""Shared generation backend (SELLER_BOT_PLAN.md §A).

The consumer process is the only one that owns the browser account pool. It
exposes a localhost-only ``POST /internal/generate`` endpoint, guarded by a
shared secret (``INTERNAL_API_TOKEN``). The seller process (``BOT_MODE=seller``),
which runs no pool, calls that endpoint via :class:`BackendClient` and sends the
returned image URLs to its own Telegram chat.

Flow image URLs are publicly fetchable (Telegram fetches them directly), so the
backend returns URLs and the seller does not need any Flow auth.
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Any, Awaitable, Callable

from aiohttp import ClientSession, ClientTimeout, web

log = logging.getLogger("flow.seller_backend")

INTERNAL_TOKEN_ENV = "INTERNAL_API_TOKEN"

GenerateCb = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def internal_token() -> str:
    return (os.getenv(INTERNAL_TOKEN_ENV) or "").strip()


def _token_ok(received: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(received or "", expected)


def build_request(
    *,
    prompt: str,
    num_images: int,
    aspect_ratio: str,
    image_model: str,
    user_id: int,
    kind: str = "image",
) -> dict[str, Any]:
    """Serialize a generation request (shared by client and tests)."""
    return {
        "kind": kind,
        "prompt": str(prompt or ""),
        "num_images": int(num_images),
        "aspect_ratio": str(aspect_ratio or "portrait"),
        "image_model": str(image_model or ""),
        "user_id": int(user_id),
    }


def register_internal_routes(
    app: web.Application,
    generate_cb: GenerateCb,
    *,
    token: str | None = None,
) -> bool:
    """Register ``POST /internal/generate`` on the consumer app.

    Returns False (and registers nothing) when no token is configured, so the
    endpoint is disabled rather than open.
    """
    tok = token if token is not None else internal_token()
    if not tok:
        log.warning("%s not set — internal generation endpoint disabled", INTERNAL_TOKEN_ENV)
        return False

    async def handle_generate(request: web.Request) -> web.Response:
        if not _token_ok(request.headers.get("X-Internal-Token", ""), tok):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "bad json"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "bad body"}, status=400)
        try:
            result = await generate_cb(body)
        except Exception:  # noqa: BLE001 — never leak internals to the caller
            log.exception("internal generate failed")
            return web.json_response({"error": "generation failed"}, status=500)
        return web.json_response(result)

    app.router.add_post("/internal/generate", handle_generate)
    log.info("Internal generation endpoint registered on /internal/generate")
    return True


class BackendClient:
    """Seller-side client for the consumer's internal generation endpoint."""

    def __init__(self, base_url: str, token: str, *, timeout_sec: float = 120.0) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout_sec

    @classmethod
    def from_env(cls) -> "BackendClient | None":
        tok = internal_token()
        if not tok:
            return None
        host = os.getenv("BACKEND_HOST", "127.0.0.1")
        port = os.getenv("BACKEND_PORT") or os.getenv("CONSUMER_WEB_PORT") or "8081"
        return cls(f"http://{host}:{port}", tok)

    async def generate(self, **kwargs: Any) -> dict[str, Any]:
        payload = build_request(**kwargs)
        try:
            async with ClientSession(timeout=ClientTimeout(total=self._timeout)) as session:
                async with session.post(
                    f"{self._base}/internal/generate",
                    json=payload,
                    headers={"X-Internal-Token": self._token},
                ) as resp:
                    data = await resp.json()
                    if resp.status != 200:
                        return {"error": str(data.get("error", f"http {resp.status}"))}
                    return data if isinstance(data, dict) else {"error": "bad response"}
        except Exception as exc:  # noqa: BLE001
            log.warning("backend generate request failed: %s", exc)
            return {"error": "backend unavailable"}
