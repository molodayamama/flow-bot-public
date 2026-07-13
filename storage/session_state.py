"""In-memory per-user session/runtime state (Phase 10).

Process-local dicts keyed by Telegram user_id, extracted verbatim from flow_bot.
"""

from __future__ import annotations

from collections import defaultdict

from config.settings import DEFAULT_COUNT
from flow_core import DEFAULT_IMAGE_MODEL, aspect_to_fmt

# uid -> last request time (cooldown / rate-limit).
user_last_request: dict[int, float] = defaultdict(float)
# Users with a request "in flight" (uid -> start time) to stop parallel abuse.
# The slot auto-expires after BUSY_MAX_SEC (enforced by the caller).
user_busy: dict[int, float] = {}
# uid -> token: user tapped "Edit" and we await their edit text.
pending_edits: dict[int, str] = {}
pending_edit_groups: dict[int, list[str]] = {}
# uid -> transient photo+caption route choice (Telegram file_id + prompt).
pending_photo_routes: dict[int, dict] = {}
# uid -> picked "ingredient" images (the "+ to mix" button).
mix_baskets: dict[int, list[dict]] = defaultdict(list)
# uid -> button-wizard state {"step","count","fmt","msg_id","await",...}.
wizard_state: dict[int, dict] = defaultdict(dict)


def _ws(user_id: int) -> dict:
    """Per-user wizard state bucket (created on first access)."""
    return wizard_state[user_id]


def clear_pending_edit(user_id: int) -> None:
    pending_edits.pop(user_id, None)
    pending_edit_groups.pop(user_id, None)


def store_pending_edit_refs(user_id: int, refs: list, image_registry, *, limit: int = 4) -> None:
    tokens = [
        image_registry.add(ref)
        for ref in refs[:limit]
        if getattr(ref, "user_id", None) == user_id
    ]
    if not tokens:
        clear_pending_edit(user_id)
        return
    pending_edits[user_id] = tokens[0]
    if len(tokens) > 1:
        pending_edit_groups[user_id] = tokens
    else:
        pending_edit_groups.pop(user_id, None)


def pending_edit_refs(user_id: int, image_registry, *, limit: int = 4) -> list:
    first = pending_edits.get(user_id)
    if not first:
        pending_edit_groups.pop(user_id, None)
        return []
    tokens = pending_edit_groups.get(user_id) or [first]
    if first not in tokens:
        tokens = [first]
    refs = []
    for token in tokens[:limit]:
        ref = image_registry.get(token)
        if ref is None or getattr(ref, "user_id", None) != user_id:
            return []
        refs.append(ref)
    return refs


def _vid_clear(user_id: int) -> None:
    """Очистить только видео-ключи (сохранив vlast для повтора и vretry для ретрая)."""
    st = wizard_state[user_id]
    keep = {k: st.get(k) for k in ("vlast", "vretry") if k in st}
    for key in list(st):
        if key.startswith("v") and key not in keep:
            st.pop(key, None)
    st.update(keep)


def _clear_image_flow_keys(st: dict) -> None:
    """Снять image-визард (await/step/pending_prompt), не трогая видео-ключи.

    Нужно при входе в видео-из-фото («Оживить фото»): иначе залипший
    ``step=="wizard"`` после прошлой генерации картинок перехватывал промпт из
    чата и генерил картинки вместо видео.
    """
    st["await"] = None
    st["step"] = None
    st.pop("pending_prompt", None)


def _vid_clear_reference_inputs(user_id: int) -> None:
    """Drop mode-specific image/caption inputs before a plain text video run."""
    st = wizard_state[user_id]
    for key in ("ving_photos", "vfrm_start", "vfrm_end", "vcaption_prompt"):
        st.pop(key, None)


def reset_image_flow(user_id: int, *, keep_last: bool = True) -> None:
    """Сбросить незавершённый image-флоу: состояние визарда И ожидание правки.

    ``pending_edits`` живёт отдельно от ``_ws``, поэтому ``_ws.clear()`` его не
    трогает — без этого сброса загруженное для правки фото «залипает» и
    следующий промпт уходит на правку старой картинки. ``keep_last`` сохраняет
    настройки прошлой генерации как дефолты визарда.
    """
    st = _ws(user_id)
    last = st.get("last") if keep_last else None
    st.clear()
    clear_pending_edit(user_id)
    pending_photo_routes.pop(user_id, None)
    if last:
        st["last"] = last
        st["count"] = last.get("count", DEFAULT_COUNT)
        st["fmt"] = aspect_to_fmt(last.get("aspect", "landscape"))
        st["imodel"] = last.get("imodel", DEFAULT_IMAGE_MODEL)
