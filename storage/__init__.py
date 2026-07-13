"""Process-local storage: per-user session state + media registries (Phase 10).

Centralizes the in-memory dicts/registries that used to be flow_bot module
globals, so the session-mixing surface (North Star: no global mutable state that
can mix user sessions) lives in one documented place. flow_bot re-imports these,
so existing references keep working.
"""

from storage.media_registry import image_registry, video_registry
from storage.session_state import (
    mix_baskets,
    pending_edits,
    pending_edit_groups,
    pending_photo_routes,
    user_busy,
    user_last_request,
    wizard_state,
)

__all__ = [
    "user_last_request",
    "user_busy",
    "pending_edits",
    "pending_edit_groups",
    "pending_photo_routes",
    "mix_baskets",
    "wizard_state",
    "image_registry",
    "video_registry",
]
