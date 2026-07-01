"""Typed request/result contracts for the generation services (PR-3a).

These sit on top of :mod:`generation.backend_service` (the platform-neutral
generation core) and give callers a typed API instead of raw dicts. Generation
is billing-free and channel-free: it returns a :class:`GenerateResult`, never
charges credits and never sends chat messages. Billing/telemetry stay in the
caller until the billing extraction (a later PR).

The dataclasses mirror the wire shape ``backend_service`` already produces, so
``as_backend_dict`` round-trips to the exact dict the seller ``/internal/generate``
endpoint and the Telegram handlers expect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


# Error keys that indicate a transient/infra failure a caller may safely retry,
# as opposed to terminal ones (bad input / moderation). Mirrors the strings
# backend_service returns.
_RETRYABLE_ERRORS = frozenset(
    {
        "accounts_unavailable",
        "generation failed",
        "upload failed",
        "upload_failed",
        "nothing_returned",
        "no image inputs",
        "download failed",
        "media_id missing",
    }
)


@dataclass(frozen=True)
class GenerateImageRequest:
    """Text-to-image request."""

    user_id: int
    prompt: str
    num_images: int = 1
    aspect_ratio: str = "portrait"
    image_model: str | None = None
    source: str = "internal"  # telegram | max | seller | internal (telemetry only)

    def as_backend_dict(self) -> dict[str, Any]:
        req: dict[str, Any] = {
            "prompt": self.prompt,
            "num_images": self.num_images,
            "aspect_ratio": self.aspect_ratio,
            "user_id": self.user_id,
        }
        if self.image_model:
            req["image_model"] = self.image_model
        return req


@dataclass(frozen=True)
class GenerateEditRequest:
    """Image-to-image (edit an uploaded photo) request."""

    user_id: int
    prompt: str
    image_b64: str
    num_images: int = 1
    aspect_ratio: str = "portrait"
    image_model: str | None = None
    source: str = "internal"

    def as_backend_dict(self) -> dict[str, Any]:
        req: dict[str, Any] = {
            "prompt": self.prompt,
            "image_b64": self.image_b64,
            "num_images": self.num_images,
            "aspect_ratio": self.aspect_ratio,
            "user_id": self.user_id,
        }
        if self.image_model:
            req["image_model"] = self.image_model
        return req


@dataclass(frozen=True)
class GenerateVideoRequest:
    """Photo + prompt reference-to-video (the "animate photo" path) request."""

    user_id: int
    prompt: str
    image_b64: str
    video_model: str | None = None
    aspect_ratio: str = "portrait"
    source: str = "internal"

    def as_backend_dict(self) -> dict[str, Any]:
        req: dict[str, Any] = {
            "prompt": self.prompt,
            "image_b64": self.image_b64,
            "aspect_ratio": self.aspect_ratio,
            "user_id": self.user_id,
        }
        if self.video_model:
            req["video_model"] = self.video_model
        return req


@dataclass(frozen=True)
class GeneratedImage:
    url: str
    img: Any = None  # raw per-image payload as returned by the backend


@dataclass(frozen=True)
class GeneratedVideo:
    video_b64: str
    media_id: str = ""
    model_id: str = ""
    aspect_ratio: str = ""
    workflow_id: str | None = None
    scene_id: str | None = None


@dataclass(frozen=True)
class GenerateResult:
    """Outcome of a generation call. ``ok`` False carries a non-empty ``error``."""

    ok: bool
    images: tuple[GeneratedImage, ...] = ()
    videos: tuple[GeneratedVideo, ...] = ()
    account_id: str | None = None
    project_id: str | None = None
    error: str | None = None
    retry_possible: bool = False

    @property
    def media_count(self) -> int:
        return len(self.images) + len(self.videos)

    def as_backend_dict(self) -> dict[str, Any]:
        """Round-trip back to the raw dict shape callers already consume."""
        if not self.ok:
            return {"error": self.error or "generation failed"}
        out: dict[str, Any] = {}
        if self.images:
            out["images"] = [{"url": i.url, "img": i.img} for i in self.images]
        if self.videos:
            out["videos"] = [
                {
                    "video_b64": v.video_b64,
                    "media_id": v.media_id,
                    "model_id": v.model_id,
                    "aspect_ratio": v.aspect_ratio,
                    "workflow_id": v.workflow_id,
                    "scene_id": v.scene_id,
                }
                for v in self.videos
            ]
        if self.account_id is not None:
            out["account_id"] = self.account_id
        if self.project_id is not None:
            out["project_id"] = self.project_id
        return out


def result_from_backend(raw: Mapping[str, Any] | None) -> GenerateResult:
    """Parse a ``backend_service`` dict into a typed :class:`GenerateResult`."""
    if not raw or raw.get("error"):
        error = str(raw.get("error")) if raw else "no_result"
        return GenerateResult(
            ok=False, error=error, retry_possible=error in _RETRYABLE_ERRORS
        )

    images = tuple(
        GeneratedImage(url=str(item.get("url") or ""), img=item.get("img"))
        for item in (raw.get("images") or [])
        if isinstance(item, Mapping) and item.get("url")
    )
    videos = tuple(
        GeneratedVideo(
            video_b64=str(item.get("video_b64") or ""),
            media_id=str(item.get("media_id") or ""),
            model_id=str(item.get("model_id") or ""),
            aspect_ratio=str(item.get("aspect_ratio") or ""),
            workflow_id=item.get("workflow_id"),
            scene_id=item.get("scene_id"),
        )
        for item in (raw.get("videos") or [])
        if isinstance(item, Mapping)
    )
    return GenerateResult(
        ok=True,
        images=images,
        videos=videos,
        account_id=raw.get("account_id"),
        project_id=raw.get("project_id"),
    )
