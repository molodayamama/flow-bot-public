"""Generation services for platform-neutral Photozhab workflows.

``backend_service`` is the platform-neutral generation core (deps-injected). The
``*_service`` modules add a typed façade over it (PR-3a) so Telegram, MAX and the
seller endpoint can share one engine and return typed results instead of raw
dicts. Generation is billing-free and channel-free.
"""

from generation.contracts import (
    GeneratedImage,
    GeneratedVideo,
    GenerateEditRequest,
    GenerateImageRequest,
    GenerateResult,
    GenerateVideoRequest,
    result_from_backend,
)
from generation.edit_service import EditService
from generation.image_service import ImageService
from generation.service import BackendGenerationService
from generation.video_service import VideoService

__all__ = [
    "GenerateImageRequest",
    "GenerateEditRequest",
    "GenerateVideoRequest",
    "GeneratedImage",
    "GeneratedVideo",
    "GenerateResult",
    "result_from_backend",
    "ImageService",
    "EditService",
    "VideoService",
    "BackendGenerationService",
]
