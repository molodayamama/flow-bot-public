"""In-memory media reference registries (Phase 10).

``token -> ImageRef/VideoRef`` maps backing inline buttons ("edit", "download
video", ...). Bounded size; process-local. Extracted verbatim from flow_bot.
"""

from __future__ import annotations

from flow_core import ImageRegistry

# token -> ImageRef for inline buttons.
image_registry = ImageRegistry()
# token -> VideoRef for the "download video" button (same registry class).
video_registry = ImageRegistry(max_entries=2000)
