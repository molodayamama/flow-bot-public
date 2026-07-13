"""Pure offline tests for MAX retry/error/rate-limit policy."""
from __future__ import annotations

import asyncio
import unittest

from channels.max.transport import (
    MaxApiError,
    SlidingWindowRateLimiter,
    error_from_response,
    retry_delay,
)


class MaxTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_rate_limiter_delays_request_beyond_window(self) -> None:
        now = [10.0]
        delays = []

        async def advance(delay: float) -> None:
            delays.append(delay)
            now[0] += delay

        limiter = SlidingWindowRateLimiter(2, clock=lambda: now[0], sleep=advance)
        await limiter.acquire()
        await limiter.acquire()
        await limiter.acquire()
        self.assertEqual(delays, [1.0])

    async def test_concurrent_callers_share_one_limit(self) -> None:
        now = [0.0]

        async def advance(delay: float) -> None:
            now[0] += delay
            await asyncio.sleep(0)

        limiter = SlidingWindowRateLimiter(2, clock=lambda: now[0], sleep=advance)
        await asyncio.gather(*(limiter.acquire() for _ in range(3)))
        self.assertGreaterEqual(now[0], 1.0)

    def test_error_exposes_only_safe_symbolic_fields(self) -> None:
        error = error_from_response(
            429,
            {"code": "bad code TOKEN", "message": "private payload"},
            {"Retry-After": "999"},
        )
        self.assertEqual(error.code, "provider_error")
        self.assertEqual(error.retry_after, 60.0)
        self.assertTrue(error.retryable)
        self.assertNotIn("private payload", str(error))
        self.assertEqual(retry_delay(error, 0), 60.0)

    def test_non_retryable_status_is_classified(self) -> None:
        error = error_from_response(400, {"code": "invalid_request"})
        self.assertIsInstance(error, MaxApiError)
        self.assertFalse(error.retryable)
        self.assertEqual(error.code, "invalid_request")

    def test_attachment_not_ready_is_retryable_even_on_400(self) -> None:
        error = error_from_response(400, {"code": "attachment.not.ready"})
        self.assertTrue(error.retryable)
        self.assertEqual(error.code, "attachment.not.ready")


if __name__ == "__main__":
    unittest.main()
