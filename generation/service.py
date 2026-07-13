"""Composed generation facade over the typed services (PR-3b).

``BackendGenerationService`` satisfies the shape a chat channel injects as its
generation backend (see ``channels.max.handler.GenerationService``): the methods
``create_image`` / ``edit_photo`` / ``animate_photo`` return the raw dict the
handlers already understand (``GenerateResult.as_backend_dict()``), but the work
now flows through the typed services and the shared ``backend_service`` core —
one engine for every client.

Photo-based flows (edit / animate) need the actual image bytes, which is a
platform concern (downloading a file by its platform id). The download is
injected as an async ``fetch_bytes`` callable and base64-encoded here, so this
module stays platform-neutral: no channels/aiogram/flow_bot import.
"""

from __future__ import annotations

import base64
from typing import Any, Awaitable, Callable, Mapping

from generation import backend_service
from generation.contracts import (
    GenerateEditRequest,
    GenerateImageRequest,
    GenerateVideoRequest,
    result_from_backend,
)
from generation.edit_service import EditService
from generation.image_service import ImageService
from generation.video_service import VideoService

FetchBytes = Callable[[str], Awaitable[bytes | None]]

# Returned when a photo-based flow cannot obtain the source bytes. Uses the same
# retryable error key the backend already emits for upload trouble.
_UPLOAD_FAILED: dict[str, Any] = {"error": "upload failed"}


class BackendGenerationService:
    """Concrete generation backend for chat channels, over the shared core."""

    def __init__(
        self,
        image_service: ImageService,
        edit_service: EditService,
        video_service: VideoService,
        *,
        fetch_bytes: FetchBytes | None = None,
        source: str = "internal",
    ) -> None:
        self._image = image_service
        self._edit = edit_service
        self._video = video_service
        self._fetch_bytes = fetch_bytes
        self._source = source
        self._deps = image_service._deps
        self._video_backend = video_service._backend

    @classmethod
    def from_deps(
        cls,
        deps: Any,
        *,
        fetch_bytes: FetchBytes | None = None,
        source: str = "internal",
    ) -> "BackendGenerationService":
        """Build the facade from the runtime deps that back ``backend_service``."""
        return cls(
            ImageService(deps),
            EditService(deps),
            VideoService(deps),
            fetch_bytes=fetch_bytes,
            source=source,
        )

    async def create_image(
        self, *, internal_user_id: int, prompt: str, image_model: str = "nb2",
        aspect_ratio: str = "portrait", count: int = 1,
    ) -> Mapping[str, Any]:
        result = await self._image.generate(
            GenerateImageRequest(
                user_id=internal_user_id, prompt=prompt, source=self._source,
                image_model=image_model, aspect_ratio=aspect_ratio,
                num_images=count,
            )
        )
        return result.as_backend_dict()

    async def edit_photo(
        self, *, internal_user_id: int, prompt: str, photo_file_id: str,
        image_model: str = "nb2", aspect_ratio: str = "portrait", count: int = 1,
    ) -> Mapping[str, Any]:
        image_b64 = await self._photo_b64(photo_file_id)
        if image_b64 is None:
            return dict(_UPLOAD_FAILED)
        result = await self._edit.generate(
            GenerateEditRequest(
                user_id=internal_user_id,
                prompt=prompt,
                image_b64=image_b64,
                source=self._source,
                image_model=image_model,
                aspect_ratio=aspect_ratio,
                num_images=count,
            )
        )
        return result.as_backend_dict()

    async def animate_photo(
        self, *, internal_user_id: int, prompt: str, photo_file_id: str,
        video_model: str = "veo-lite", aspect_ratio: str = "portrait",
    ) -> Mapping[str, Any]:
        image_b64 = await self._photo_b64(photo_file_id)
        if image_b64 is None:
            return dict(_UPLOAD_FAILED)
        result = await self._video.generate(
            GenerateVideoRequest(
                user_id=internal_user_id,
                prompt=prompt,
                image_b64=image_b64,
                source=self._source,
                video_model=video_model,
                aspect_ratio=aspect_ratio,
            )
        )
        return result.as_backend_dict()

    async def create_video(
        self, *, internal_user_id: int, prompt: str,
        video_model: str = "omni-flash-4s", aspect_ratio: str = "portrait",
    ) -> Mapping[str, Any]:
        raw = await backend_service.generate_video_text(self._deps, {
            "user_id": internal_user_id, "prompt": prompt,
            "video_model": video_model, "aspect_ratio": aspect_ratio,
        })
        return result_from_backend(raw).as_backend_dict()

    async def create_video_ingredients(
        self, *, internal_user_id: int, prompt: str,
        photo_file_ids: tuple[str, ...], video_model: str = "veo-lite",
        aspect_ratio: str = "portrait",
    ) -> Mapping[str, Any]:
        images_b64 = await self._photos_b64(photo_file_ids[:4])
        if not images_b64:
            return dict(_UPLOAD_FAILED)
        raw = await self._video_backend(self._deps, {
            "user_id": internal_user_id, "prompt": prompt,
            "images_b64": images_b64, "image_b64": images_b64[0],
            "video_model": video_model, "aspect_ratio": aspect_ratio,
        })
        return result_from_backend(raw).as_backend_dict()

    async def create_video_frames(
        self, *, internal_user_id: int, prompt: str,
        photo_file_ids: tuple[str, str], video_model: str = "veo-lite",
        aspect_ratio: str = "portrait",
    ) -> Mapping[str, Any]:
        images_b64 = await self._photos_b64(tuple(photo_file_ids))
        if not images_b64 or len(images_b64) != 2:
            return dict(_UPLOAD_FAILED)
        raw = await backend_service.generate_video_frames(self._deps, {
            "user_id": internal_user_id, "prompt": prompt,
            "images_b64": images_b64, "video_model": video_model,
            "aspect_ratio": aspect_ratio,
        })
        return result_from_backend(raw).as_backend_dict()

    async def _photo_b64(self, photo_file_id: str) -> str | None:
        if self._fetch_bytes is None:
            return None
        data = await self._fetch_bytes(photo_file_id)
        if not data:
            return None
        return base64.b64encode(data).decode("ascii")

    async def _photos_b64(self, photo_file_ids: tuple[str, ...]) -> list[str] | None:
        encoded: list[str] = []
        for photo_file_id in photo_file_ids:
            image_b64 = await self._photo_b64(photo_file_id)
            if image_b64 is None:
                return None
            encoded.append(image_b64)
        return encoded
