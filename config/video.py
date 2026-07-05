"""Video wizard UI configuration (Phase 5).

Video format/model/style constants extracted verbatim from flow_bot. flow_bot
re-exports these.
"""

from __future__ import annotations

from flow_core import VIDEO_MODELS, video_price, video_model_meta, clamp_num_videos


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
_VID_FMT_TO_ASPECT = {"land": "landscape", "port": "portrait"}
_VID_QUICKSTART_FAMILY = "omni-flash"
_VID_FAMILY_CODE = {"omni-flash": "omni", "veo": "veo"}
_VID_CODE_FAMILY = {v: k for k, v in _VID_FAMILY_CODE.items()}
_GUIDED_TO_VID_STYLE: dict[str, str] = {
    "anime":     "anime",
    "3d":        "3d",
    "realism":   "photo",
    "cinematic": "cine",
}


def video_plain_text_ready(st: dict) -> bool:
    """True when the text-to-video settings screen can accept a chat prompt."""
    if st.get("vawait"):
        return False
    if st.get("vstep") != "vsettings":
        return False
    if st.get("vmode", "text") != "text":
        return False
    model_id = st.get("vmodel")
    if not model_id or not video_model_meta(model_id):
        return False
    vfmt = st.get("vfmt")
    if vfmt not in _VID_FMT_TO_ASPECT:
        return False
    try:
        vcount = int(st.get("vcount"))
    except (TypeError, ValueError):
        return False
    return clamp_num_videos(vcount) == vcount


# ── new-video-wizard resolvers ───────────────────────────────────────────────
# Engine/model/price derived from the wizard session dict. The engine is a user
# choice (⚡ omni / 💎 veo), independent of whether a photo is attached; the
# model then follows from the engine + duration/quality knob.
def nwiz_engine(st: dict) -> str:
    eng = (st.get("vengine") or "").lower()
    return eng if eng in ("omni", "veo") else "omni"


def nwiz_model(st: dict) -> str:
    if nwiz_engine(st) == "veo":
        return _VID_VEO_QUAL_MODEL.get(st.get("vquality", "lite"), "veo-lite")
    return _VID_OMNI_DUR_MODEL.get(st.get("vdur", 4), "omni-flash-4s")


def nwiz_price(st: dict) -> int:
    mid = nwiz_model(st)
    vmode = "ingredients" if st.get("vphoto") else "text"
    return video_price(mid, 1, vmode)
