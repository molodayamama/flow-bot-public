"""Adapter from the MAX handler's GenerationService contract to the shared
generation backend (`generation.backend_service`).

`BackendGenerationService` maps the platform-neutral
create_image/edit_photo/animate_photo calls onto the injected backend function(s)
and the app's runtime deps (account routing, project, keeper, ...). Deps are
injected by the composition root, so this module imports neither flow_bot nor the
backend internals directly.

Text-to-image is always wired. Photo edit/animate work when the composition also
injects `generate_i2i` / `generate_video_ingredients` and a `download_bytes`
callable: the incoming photo reference (a URL captured by the MAX update parser)
is downloaded, base64-encoded and handed to the backend as ``image_b64``. When
that wiring or a usable photo reference is missing they return a graceful error
so the handler shows its standard "try again" copy rather than crashing.
"""

from __future__ import annotations

import base64
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
        generate_i2i: Callable[[Any, dict], Awaitable[Mapping[str, Any]]] | None = None,
        generate_video_ingredients: Callable[[Any, dict], Awaitable[Mapping[str, Any]]] | None = None,
        download_bytes: Callable[[str], Awaitable[bytes]] | None = None,
    ) -> None:
        self._generate_images = generate_images
        self._deps = deps
        self._default_aspect = default_aspect
        self._photo_error = photo_unavailable_error
        self._generate_i2i = generate_i2i
        self._generate_video_ingredients = generate_video_ingredients
        self._download_bytes = download_bytes

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

    async def _photo_bytes_b64(self, photo_ref: str) -> str | None:
        """Download the incoming photo and base64-encode it, or None if we can't.

        The MAX update parser stores a downloadable URL as the photo reference;
        only http(s) references are fetchable here.
        """
        if not self._download_bytes or not photo_ref:
            return None
        if not photo_ref.lower().startswith(("http://", "https://")):
            return None
        data = await self._download_bytes(photo_ref)
        if not data:
            return None
        return base64.b64encode(data).decode("ascii")

    async def edit_photo(
        self, *, internal_user_id: int, prompt: str, photo_file_id: str
    ) -> Mapping[str, Any]:
        if self._generate_i2i is None:
            return {"error": self._photo_error}
        image_b64 = await self._photo_bytes_b64(photo_file_id)
        if not image_b64:
            return {"error": self._photo_error}
        req = {
            "prompt": prompt,
            "image_b64": image_b64,
            "num_images": 1,
            "aspect_ratio": self._default_aspect,
            "user_id": int(internal_user_id),
        }
        return await self._generate_i2i(self._deps, req)

    async def animate_photo(
        self, *, internal_user_id: int, prompt: str, photo_file_id: str
    ) -> Mapping[str, Any]:
        if self._generate_video_ingredients is None:
            return {"error": self._photo_error}
        image_b64 = await self._photo_bytes_b64(photo_file_id)
        if not image_b64:
            return {"error": self._photo_error}
        req = {
            "prompt": prompt,
            "image_b64": image_b64,
            "aspect_ratio": self._default_aspect,
            "user_id": int(internal_user_id),
        }
        return await self._generate_video_ingredients(self._deps, req)
