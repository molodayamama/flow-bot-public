"""Structural interfaces between Flow provider adapters."""
from __future__ import annotations

from typing import Protocol


class FlowSession(Protocol):
    """The narrow session surface consumed by :class:`FlowHttpClient`."""

    account_id: str
    api_proxy_url: str | None

    async def get_session(self) -> dict: ...

    async def solve_captcha(self, action: str = "IMAGE_GENERATION") -> str: ...

    async def post_json_via_browser(
        self,
        url: str,
        headers: dict,
        payload: dict,
        timeout_ms: int = 60_000,
    ) -> dict | None: ...

    async def generate_via_browser(self, prompt: str) -> dict: ...

    async def _refresh_bearer(self) -> None: ...

    def _flag_needs_relogin(self, value: bool) -> None: ...
