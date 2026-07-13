"""Adapter from the MAX handler's GenerationService contract to the shared
generation backend (`generation.backend_service`).

`BackendGenerationService` maps the platform-neutral image/video calls onto the
injected backend function(s)
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
        generate_video_text: Callable[[Any, dict], Awaitable[Mapping[str, Any]]] | None = None,
        generate_video_ingredients: Callable[[Any, dict], Awaitable[Mapping[str, Any]]] | None = None,
        generate_video_frames: Callable[[Any, dict], Awaitable[Mapping[str, Any]]] | None = None,
        download_bytes: Callable[[str], Awaitable[bytes]] | None = None,
    ) -> None:
        self._generate_images = generate_images
        self._deps = deps
        self._default_aspect = default_aspect
        self._photo_error = photo_unavailable_error
        self._generate_i2i = generate_i2i
        self._generate_video_text = generate_video_text
        self._generate_video_ingredients = generate_video_ingredients
        self._generate_video_frames = generate_video_frames
        self._download_bytes = download_bytes

    async def create_image(
        self,
        *,
        internal_user_id: int,
        prompt: str,
        image_model: str = "nb2",
        aspect_ratio: str | None = None,
        count: int = 1,
    ) -> Mapping[str, Any]:
        req = {
            "prompt": prompt,
            "num_images": count,
            "aspect_ratio": aspect_ratio or self._default_aspect,
            "image_model": image_model,
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
        self,
        *,
        internal_user_id: int,
        prompt: str,
        photo_file_id: str,
        image_model: str = "nb2",
        aspect_ratio: str | None = None,
        count: int = 1,
    ) -> Mapping[str, Any]:
        if self._generate_i2i is None:
            return {"error": self._photo_error}
        image_b64 = await self._photo_bytes_b64(photo_file_id)
        if not image_b64:
            return {"error": self._photo_error}
        req = {
            "prompt": prompt,
            "image_b64": image_b64,
            "num_images": count,
            "aspect_ratio": aspect_ratio or self._default_aspect,
            "image_model": image_model,
            "user_id": int(internal_user_id),
        }
        return await self._generate_i2i(self._deps, req)

    async def animate_photo(
        self,
        *,
        internal_user_id: int,
        prompt: str,
        photo_file_id: str,
        video_model: str = "veo-lite",
        aspect_ratio: str | None = None,
    ) -> Mapping[str, Any]:
        return await self.create_video_ingredients(
            internal_user_id=internal_user_id,
            prompt=prompt,
            photo_file_ids=(photo_file_id,),
            video_model=video_model,
            aspect_ratio=aspect_ratio,
        )

    async def create_video(
        self,
        *,
        internal_user_id: int,
        prompt: str,
        video_model: str = "omni-flash-4s",
        aspect_ratio: str | None = None,
    ) -> Mapping[str, Any]:
        if self._generate_video_text is None:
            return {"error": self._photo_error}
        return await self._generate_video_text(self._deps, {
            "prompt": prompt,
            "video_model": video_model,
            "aspect_ratio": aspect_ratio,
            "user_id": int(internal_user_id),
        })

    async def _photos_b64(self, photo_refs: tuple[str, ...]) -> list[str] | None:
        encoded: list[str] = []
        for photo_ref in photo_refs:
            image_b64 = await self._photo_bytes_b64(photo_ref)
            if not image_b64:
                return None
            encoded.append(image_b64)
        return encoded

    async def create_video_ingredients(
        self,
        *,
        internal_user_id: int,
        prompt: str,
        photo_file_ids: tuple[str, ...],
        video_model: str = "veo-lite",
        aspect_ratio: str | None = None,
    ) -> Mapping[str, Any]:
        if self._generate_video_ingredients is None:
            return {"error": self._photo_error}
        images_b64 = await self._photos_b64(photo_file_ids[:4])
        if not images_b64:
            return {"error": self._photo_error}
        req = {
            "prompt": prompt,
            "images_b64": images_b64,
            "image_b64": images_b64[0],
            "aspect_ratio": aspect_ratio or self._default_aspect,
            "video_model": video_model,
            "user_id": int(internal_user_id),
        }
        return await self._generate_video_ingredients(self._deps, req)

    async def create_video_frames(
        self,
        *,
        internal_user_id: int,
        prompt: str,
        photo_file_ids: tuple[str, str],
        video_model: str = "veo-lite",
        aspect_ratio: str | None = None,
    ) -> Mapping[str, Any]:
        if self._generate_video_frames is None:
            return {"error": self._photo_error}
        images_b64 = await self._photos_b64(tuple(photo_file_ids))
        if not images_b64 or len(images_b64) != 2:
            return {"error": self._photo_error}
        return await self._generate_video_frames(self._deps, {
            "prompt": prompt,
            "images_b64": images_b64,
            "aspect_ratio": aspect_ratio or self._default_aspect,
            "video_model": video_model,
            "user_id": int(internal_user_id),
        })
