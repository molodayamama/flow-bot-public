"""Video delivery byte-fetching (Phase 11 core split).

Resolves the bytes to hand a user for a finished video. For an Extend result
the default is the service's server-side stitched FULL video; ``ref.media_id``
is only the newly added segment, used as a fallback when stitching is
unavailable. Channel-neutral: the per-account client accessor and logger are
injected, so this module imports no runtime state.
"""

from __future__ import annotations

from typing import Any, Callable


async def video_delivery_bytes(
    ref: Any,
    *,
    client_for_acc: Callable[[str | None], Any],
    log: Any,
    fetched_bytes: bytes | None = None,
) -> tuple[bytes | None, bool]:
    """Return ``(bytes, is_full)`` for delivering ``ref``.

    ``is_full`` is True only when the server-side stitched Extend video was
    fetched; otherwise the (possibly pre-fetched) single-segment bytes."""
    if ref.mode == "extend" and ref.scene_id and ref.project_id:
        full_bytes = await client_for_acc(ref.account_id).fetch_full_extended_video(
            ref.scene_id, ref.project_id
        )
        if full_bytes:
            return full_bytes, True
        log.warning("full stitched video unavailable; falling back to extension segment")

    video_bytes = fetched_bytes
    if video_bytes is None:
        video_bytes = await client_for_acc(ref.account_id).fetch_video_bytes(ref.media_id)
    return video_bytes, False
