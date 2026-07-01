"""Image-to-image (edit uploaded photo) generation service (PR-3a).

Typed façade over :func:`generation.backend_service.generate_i2i`. Returns a
:class:`~generation.contracts.GenerateResult`; billing- and channel-free.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping

from generation import backend_service
from generation.contracts import (
    GenerateEditRequest,
    GenerateResult,
    result_from_backend,
)

BackendCall = Callable[[Any, dict], Awaitable[Mapping[str, Any]]]


class EditService:
    """Edit a user's uploaded photo from a text instruction."""

    def __init__(self, deps: Any, *, backend: BackendCall = backend_service.generate_i2i):
        self._deps = deps
        self._backend = backend

    async def generate(self, request: GenerateEditRequest) -> GenerateResult:
        raw = await self._backend(self._deps, request.as_backend_dict())
        return result_from_backend(raw)
