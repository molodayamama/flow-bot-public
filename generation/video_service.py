"""Photo-to-video ("animate photo") generation service (PR-3a).

Typed façade over :func:`generation.backend_service.generate_video_ingredients`
— the photo + prompt reference-to-video path that powers "Оживить фото". Returns
a :class:`~generation.contracts.GenerateResult`; billing- and channel-free.

The full video wizard (frames / start-end / extend / concat) is not part of this
service yet; it stays in the Telegram handlers until a later PR.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping

from generation import backend_service
from generation.contracts import (
    GenerateResult,
    GenerateVideoRequest,
    result_from_backend,
)

BackendCall = Callable[[Any, dict], Awaitable[Mapping[str, Any]]]


class VideoService:
    """Generate a short video from a photo + prompt (reference-to-video)."""

    def __init__(
        self,
        deps: Any,
        *,
        backend: BackendCall = backend_service.generate_video_ingredients,
    ):
        self._deps = deps
        self._backend = backend

    async def generate(self, request: GenerateVideoRequest) -> GenerateResult:
        raw = await self._backend(self._deps, request.as_backend_dict())
        return result_from_backend(raw)

    async def animate(self, request: GenerateVideoRequest) -> GenerateResult:
        """Alias for :meth:`generate` — the "animate photo" product wording."""
        return await self.generate(request)
