"""Bounded, redacted transport policy for the MAX Bot API client."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
_SAFE_CODES = frozenset({
    "attachment.not.ready",
    "http_error",
    "invalid_request",
    "invalid_response",
    "media_download_failed",
    "media_too_large",
    "media_upload_failed",
    "media_url_invalid",
    "rate_limit",
    "transport_error",
    "upload_url_invalid",
})


class MaxApiError(RuntimeError):
    """Provider failure whose string form never includes body, URL, or token."""

    def __init__(
        self,
        *,
        status: int | None,
        code: str,
        retryable: bool,
        retry_after: float | None = None,
    ) -> None:
        self.status = status
        self.code = safe_error_code(code)
        self.retryable = bool(retryable)
        self.retry_after = retry_after
        status_text = str(status) if status is not None else "transport"
        super().__init__(f"MAX API request failed (status={status_text}, code={self.code})")


def safe_error_code(value: Any) -> str:
    text = str(value or "unknown")
    return text if text in _SAFE_CODES else "provider_error"


def error_from_response(
    status: int,
    payload: Any,
    headers: Mapping[str, Any] | None = None,
) -> MaxApiError:
    code: Any = "http_error"
    if isinstance(payload, Mapping):
        candidate = payload.get("code") or payload.get("error")
        if isinstance(candidate, Mapping):
            candidate = candidate.get("code")
        if candidate:
            code = candidate
    retry_after: float | None = None
    raw_retry = (headers or {}).get("Retry-After")
    try:
        if raw_retry is not None:
            retry_after = max(0.0, min(float(raw_retry), 60.0))
    except (TypeError, ValueError):
        retry_after = None
    safe_code = safe_error_code(code)
    return MaxApiError(
        status=status,
        code=safe_code,
        retryable=(
            status in RETRYABLE_HTTP_STATUSES or safe_code == "attachment.not.ready"
        ),
        retry_after=retry_after,
    )


def retry_delay(error: MaxApiError, attempt: int) -> float:
    if error.retry_after is not None:
        return error.retry_after
    return min(8.0, 0.5 * (2 ** max(0, attempt)))


class SlidingWindowRateLimiter:
    """Coroutine-safe sliding-window guard below MAX's documented 30 rps."""

    def __init__(
        self,
        rate: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.rate = max(1, int(rate))
        self._clock = clock
        self._sleep = sleep
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = self._clock()
                while self._timestamps and self._timestamps[0] <= now - 1.0:
                    self._timestamps.popleft()
                if len(self._timestamps) < self.rate:
                    self._timestamps.append(now)
                    return
                delay = max(0.001, self._timestamps[0] + 1.0 - now)
            await self._sleep(delay)
