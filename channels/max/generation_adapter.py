"""Adapter from the MAX handler's GenerationService contract to the shared
generation backend (`generation.backend_service`).

`BackendGenerationService` maps the platform-neutral
create_image/edit_photo/animate_photo calls onto the injected backend function(s)
and the app's runtime deps (account routing, project, keeper, ...). Deps are
injected by the composition root, so this module imports neither flow_bot nor the
backend internals directly.

Text-to-image is fully wired. Photo-based flows (edit/animate) need a
MAX-attachment -> bytes -> Flow-upload bridge (the MAX update currently carries a
file id, not raw bytes); until that bridge exists they return a graceful error
so the handler shows its standard "try again" copy rather than crashing.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping


class BackendGenerationService:
    """`channels.max.handler.GenerationService` over the generation backend."""

    def __init__(
        self,
        *,
        generate_images: Callable[[Any, dict], Awaitable[Mapping[str, Any]]],
        deps: Any,
        default_aspect: str = "portrait",
        photo_unavailable_error: str = "photo_generation_unavailable",
    ) -> None:
        self._generate_images = generate_images
        self._deps = deps
        self._default_aspect = default_aspect
        self._photo_error = photo_unavailable_error

    async def create_image(
        self, *, internal_user_id: int, prompt: str
    ) -> Mapping[str, Any]:
        req = {
            "prompt": prompt,
            "num_images": 1,
            "aspect_ratio": self._default_aspect,
            "user_id": int(internal_user_id),
        }
        return await self._generate_images(self._deps, req)

    async def edit_photo(
        self, *, internal_user_id: int, prompt: str, photo_file_id: str
    ) -> Mapping[str, Any]:
        # TODO(max-photo-bridge): download the MAX attachment bytes and upload to
        # Flow before calling generate_i2i (needs image_b64). Deferred.
        return {"error": self._photo_error}

    async def animate_photo(
        self, *, internal_user_id: int, prompt: str, photo_file_id: str
    ) -> Mapping[str, Any]:
        # TODO(max-photo-bridge): same bridge as edit_photo, then
        # generate_video_ingredients. Deferred.
        return {"error": self._photo_error}
