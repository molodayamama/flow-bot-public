"""Text-to-image generation service (PR-3a).

Typed façade over :func:`generation.backend_service.generate_images`. Reuses the
existing platform-neutral core; adds no logic of its own beyond request/result
translation. Returns a :class:`~generation.contracts.GenerateResult`; it never
charges credits and never sends chat messages.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping

from generation import backend_service
from generation.contracts import (
    GenerateImageRequest,
    GenerateResult,
    result_from_backend,
)

BackendCall = Callable[[Any, dict], Awaitable[Mapping[str, Any]]]


class ImageService:
    """Generate images from a text prompt on the shared Flow account pool."""

    def __init__(self, deps: Any, *, backend: BackendCall = backend_service.generate_images):
        self._deps = deps
        self._backend = backend

    async def generate(self, request: GenerateImageRequest) -> GenerateResult:
        raw = await self._backend(self._deps, request.as_backend_dict())
        return result_from_backend(raw)
