"""Telegram text render helpers (Phase 5)."""

from __future__ import annotations

import html

import flow_copy
import metrics
from channels.telegram.keyboards import _MP_JOB_LABELS, _MP_PLAT_NAMES, _slides_word


def _seller_history_job_platform(row: dict) -> tuple[str, str]:
    source = str(row.get("mp_source") or "").strip()
    platform = ""
    job = ""
    if source:
        parts = source.split(":")
        if parts:
            platform = _MP_PLAT_NAMES.get(parts[0], parts[0])
        if len(parts) >= 2:
            if parts[1] == "series":
                count = parts[2] if len(parts) >= 3 else ""
                job = f"серия · {count} {_slides_word(int(count))}" if str(count).isdigit() else "серия слайдов"
            elif parts[1] == "animate":
                job = "оживить фото"
            else:
                job = _MP_JOB_LABELS.get(parts[1], parts[1])
    if not job:
        op = str(row.get("operation_type") or "")
        job = {
            "mp_series": "серия слайдов",
            "video_mp_animate": "оживить фото",
            "edit": "карточка товара",
            "image": "картинка",
            "enhance": "улучшение",
        }.get(op, op or "запрос")
    return job, platform



def _seller_history_status(row: dict) -> str:
    status = str(row.get("status") or "").strip().lower()
    if status == "success":
        return "✅ готово"
    if status in {"fail", "failed", "error"}:
        reason = str(row.get("error_type") or "").strip()
        return f"❌ ошибка: {html.escape(reason)}" if reason else "❌ ошибка"
    return f"⏳ {html.escape(status)}" if status else "⏳ в работе"



def _seller_history_cost(row: dict) -> str:
    charged = int(row.get("bot_credits_charged") or 0)
    refunded = int(row.get("refund_amount") or 0)
    if charged > 0:
        return f"списано {charged} кр"
    if refunded > 0:
        return f"возврат {refunded} кр"
    return "без списания"



def _seller_history_text(user_id: int, *, metrics_module=metrics) -> str:
    get_history = getattr(metrics_module, "get_seller_history", None)
    jobs = get_history(user_id, limit=10) if callable(get_history) else []
    if jobs:
        lines = [flow_copy.msg("seller_history_title", n=len(jobs))]
        for idx, row in enumerate(jobs, 1):
            job, platform = _seller_history_job_platform(row)
            platform_line = f" · {html.escape(platform)}" if platform else ""
            lines.append(flow_copy.msg(
                "seller_history_item",
                idx=idx,
                date=html.escape((row.get("created_at") or "")[:16]),
                job=html.escape(job),
                platform=platform_line,
                status=_seller_history_status(row),
                cost=html.escape(_seller_history_cost(row)),
            ))
        return "\n".join(lines)

    gallery = metrics_module.get_gallery(user_id, limit=5)
    if gallery:
        items = []
        for item in gallery:
            prompt = str(item.get("prompt") or "готовая работа").strip()[:90]
            items.append(flow_copy.msg(
                "seller_history_gallery_item",
                date=html.escape((item.get("created_at") or "")[:16]),
                prompt=html.escape(prompt or "готовая работа"),
            ))
        return flow_copy.msg("seller_history_gallery_fallback", items="\n".join(items))
    return flow_copy.msg("seller_history_empty")
