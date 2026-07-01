"""In-memory per-user session/runtime state (Phase 10).

Process-local dicts keyed by Telegram user_id, extracted verbatim from flow_bot.
"""

from __future__ import annotations

from collections import defaultdict

# uid -> last request time (cooldown / rate-limit).
user_last_request: dict[int, float] = defaultdict(float)
# Users with a request "in flight" (uid -> start time) to stop parallel abuse.
# The slot auto-expires after BUSY_MAX_SEC (enforced by the caller).
user_busy: dict[int, float] = {}
# uid -> token: user tapped "Edit" and we await their edit text.
pending_edits: dict[int, str] = {}
# uid -> transient photo+caption route choice (Telegram file_id + prompt).
pending_photo_routes: dict[int, dict[str, str]] = {}
# uid -> picked "ingredient" images (the "+ to mix" button).
mix_baskets: dict[int, list[dict]] = defaultdict(list)
# uid -> button-wizard state {"step","count","fmt","msg_id","await",...}.
wizard_state: dict[int, dict] = defaultdict(dict)


def _ws(user_id: int) -> dict:
    """Per-user wizard state bucket (created on first access)."""
    return wizard_state[user_id]
