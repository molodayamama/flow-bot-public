"""Ideas Hub session-state helpers (Phase 11 core split).

Pure helpers over a user's wizard session dict for the Ideas Hub (templates +
guided flows): clearing transient keys, photo presence, prompt composition and
aspect-format mapping. Channel-neutral, no local imports.
"""

from __future__ import annotations

# Session-dict keys owned by the Ideas Hub sub-flows.
_IDEAS_PHOTO_KEYS = ("ideas_photo_file_id", "ideas_photo_caption", "ideas_extra_prompt")
_TP_STATE_KEYS = ("tp_tpl", "tp_step", "tp_answers", "tp_await")
_GP_STATE_KEYS = ("gp_step", "gp_answers", "gp_extra_prompt")


def ideas_clear(st: dict, *, clear_photo: bool = False) -> None:
    for k in (*_TP_STATE_KEYS, *_GP_STATE_KEYS):
        st.pop(k, None)
    if clear_photo:
        for k in _IDEAS_PHOTO_KEYS:
            st.pop(k, None)
    st.pop("ideas_mode", None)


def ideas_has_photo(st: dict) -> bool:
    return bool(st.get("ideas_photo_file_id"))


def ideas_prompt_with_extra(prompt: str, st: dict) -> str:
    extra = (st.get("gp_extra_prompt") or st.get("ideas_extra_prompt") or "").strip()
    if not extra:
        return prompt
    base = (prompt or "high quality image").strip()
    return f"{base}. User note for the attached photo/reference: {extra}"


def guided_image_fmt(answers: dict) -> str:
    fmt = (answers or {}).get("format")
    if fmt == "story":
        return "port"
    if fmt in ("square", "avatar"):
        return "sq"
    return "land"


def guided_video_fmt(answers: dict) -> str:
    return "port" if (answers or {}).get("format") in ("story", "avatar") else "land"
