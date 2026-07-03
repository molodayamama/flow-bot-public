"""Image-job flow_jobs logging (Phase 11 core split).

Timing/tagging helpers plus :class:`ImageJobLogger`, which writes a flow_jobs
row for an image operation and never raises. Channel-neutral: metrics, the
account pool and the default account id are injected, so this carries no
import-time coupling to the Telegram bot.
"""

from __future__ import annotations

import time
from typing import Any

# Callback action → flow_jobs operation_type.
_IMG_OP = {
    "gen": "image", "regen": "image", "revary": "variations",
    "up2x": "enhance", "edit": "edit", "myphoto": "edit",
    "mp_series": "mp_series",
}


def ms_since(started: float) -> int:
    """Elapsed milliseconds since a ``time.monotonic()`` start stamp."""
    return int((time.monotonic() - started) * 1000)


def seller_acc_tag(backend_acc: str | None) -> str | None:
    """Tag a seller job with the real consumer-backend account, e.g. ``sub5-sell``."""
    acc = (backend_acc or "").strip()
    return f"{acc}-sell" if acc else None


class ImageJobLogger:
    def __init__(self, *, metrics: Any, account_pool: Any, flow_account_id: str) -> None:
        self._metrics = metrics
        self._pool = account_pool
        self._flow_account_id = flow_account_id

    def log(
        self, user_id, action, image_model, started, *,
        ok, charged: int = 0, error: str | None = None, account_id: str | None = None,
    ) -> None:
        """Write a flow_jobs row for an image operation (never raises).

        ``account_id`` is the explicit account (seller passes ``<acc>-sell``);
        otherwise the user's assigned pool account is used."""
        self._metrics.log_flow_job(
            user_id=user_id,
            account_id=account_id or self._pool.assigned_to(user_id) or self._flow_account_id,
            operation_type=_IMG_OP.get(action, "image"), model=image_model,
            bot_credits_charged=charged, duration_ms=ms_since(started),
            status="success" if ok else "fail",
            error_type=error or (None if ok else "gen_failed"),
        )
