"""Daily-streak congratulation copy (Phase 11 core split).

Channel-neutral: picks the streak message for a user's first generation of the
day. The streak counter itself lives in metrics; the ``update_streak`` reader is
injected so this module imports no runtime state (only leaf copy/text helpers).
"""

from __future__ import annotations

from typing import Callable, Tuple

import flow_copy
from textutil import _days_word

_MILESTONES = (3, 7, 14, 30)


def streak_note(
    user_id: int,
    *,
    update_streak: Callable[[int], Tuple[int, int, bool]],
) -> str | None:
    """Return a short streak congratulation on the user's first generation today.

    Returns ``None`` when the user already generated today (no-op) or on any
    error, so the caller can always safely prepend it to a message.
    ``update_streak`` returns ``(current, max, is_new_day)``.
    """
    try:
        current, _max, is_new_day = update_streak(user_id)
        if not is_new_day:
            return None
        if current == 1:
            return flow_copy.msg("streak_day_1")
        if current in _MILESTONES:
            return flow_copy.msg(f"streak_milestone_{current}")
        return flow_copy.msg("streak_ongoing", n=current, days=_days_word(current))
    except Exception:
        return None
