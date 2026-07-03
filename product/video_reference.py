"""Video reference-source resolution (Phase 11 core split).

Pure helpers that read reference-photo sources out of a video-wizard session
dict and resolve the account/project they are bound to. Channel-neutral: the
one health-aware resolver takes an ``is_reference_usable`` callback so this
module never imports the runtime account pool. No local imports.
"""

from __future__ import annotations

from typing import Callable


def source_account_id(source: dict | None) -> str | None:
    if isinstance(source, dict):
        value = source.get("_account_id")
        if isinstance(value, str) and value:
            return value
    return None


def source_project_id(source: dict | None) -> str | None:
    if isinstance(source, dict):
        value = source.get("_project_id")
        if isinstance(value, str) and value:
            return value
    return None


def video_reference_sources(st: dict, vmode: str) -> list[dict]:
    if vmode == "ingredients":
        return [s for s in (st.get("ving_photos") or []) if isinstance(s, dict)]
    if vmode == "frames":
        return [s for s in (st.get("vfrm_start"), st.get("vfrm_end")) if isinstance(s, dict)]
    return []


def video_reference_account_id(
    st: dict, vmode: str, *, is_reference_usable: Callable[[str], bool]
) -> str | None:
    accounts = {
        aid for aid in (source_account_id(s) for s in video_reference_sources(st, vmode))
        if aid
    }
    if len(accounts) == 1:
        aid = next(iter(accounts))
        # Reference media is bound to this account → generate there even if it
        # is in cooldown (any other account = guaranteed 404). Only hard-disabled
        # / image-only accounts are rejected via is_reference_usable.
        if is_reference_usable(aid):
            return aid
    return None


def video_reference_project_id(st: dict, vmode: str) -> str | None:
    projects = {
        pid for pid in (source_project_id(s) for s in video_reference_sources(st, vmode))
        if pid
    }
    return next(iter(projects)) if len(projects) == 1 else None
