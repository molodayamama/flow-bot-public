"""Video wizard UI configuration (Phase 5).

Video format/model/style constants extracted verbatim from flow_bot. flow_bot
re-exports these.
"""

from __future__ import annotations

from flow_core import VIDEO_MODELS


SELECT_STYLE = "success"  # green — Bot API 9.4 colour for the chosen wizard option
VID_DEFAULT_FMT = "land"
_VID_FMT_NAMES = {"land": "16:9", "port": "9:16"}
_VID_QUICKSTART_MODEL = "omni-flash-4s"
VID_REF_DEFAULT_MODEL = "omni-flash-4s"
VIDEO_EXTEND_MODEL = "veo-lite"
VID_REF_VARIANTS = tuple(VIDEO_MODELS.keys())
VID_FRAMES_VARIANTS = ("veo-lite", "veo-fast", "veo-quality")
_VID_STYLES: dict[str, tuple[str, str]] = {
    "":       ("Никакой",           ""),
    "cine":   ("Кинематографичный", ", cinematic style, film look, dramatic lighting"),
    "anime":  ("Аниме",             ", anime style, Studio Ghibli animation"),
    "3d":     ("3D",                ", 3D render, CGI animation, volumetric lighting"),
    "photo":  ("Фотореализм",       ", photorealistic, 8K, professional photography"),
    "retro":  ("Ретро",             ", vintage 80s, film grain, retro cinematography"),
}
_VID_OMNI_DURATIONS = [4, 6, 8, 10]
_VID_OMNI_DUR_MODEL = {4: "omni-flash-4s", 6: "omni-flash-6s",
                        8: "omni-flash-8s", 10: "omni-flash-10s"}
_VID_VEO_QUALITY_CYCLE = ["lite", "fast", "quality"]
_VID_VEO_QUAL_MODEL = {"lite": "veo-lite", "fast": "veo-fast", "quality": "veo-quality"}
_VID_VEO_QUAL_NAMES = {"lite": "Lite", "fast": "Fast", "quality": "Quality"}
