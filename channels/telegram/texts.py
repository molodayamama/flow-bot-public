"""Telegram text render helpers (Phase 5)."""

from __future__ import annotations

import html

import flow_copy
import metrics
from channels.telegram.keyboards import (
    L,
    _MP_JOB_LABELS,
    _MP_NICHES,
    _MP_PLAT_NAMES,
    _mp_platform_format_label,
    _slides_word,
)
from config.video import VID_REF_DEFAULT_MODEL
from flow_core import action_price, video_price



_MP_JOB_OUTCOMES = {
    "whitebg": "чистое каталожное фото товара на белом фоне.",
    "info": "карточка с крупным товаром, местом под заголовок и ключевые выгоды.",
    "model": "реалистичная сцена с моделью или фоном, где товар выглядит в использовании.",
    "cover": "главный слайд с крупным товаром и цепляющим ракурсом.",
    "bg": "аккуратный новый фон без лишних деталей.",
}
_MP_SERIES_COUNTS = (3, 5, 8)
_MP_SERIES_LABELS = {
    3: "мини-серия",
    5: "стандартная серия",
    8: "полная карточка",
}


def _mp_photo_request_text(platform: str, job: str) -> str:
    platform_name = html.escape(_MP_PLAT_NAMES.get(platform, platform))
    job_label = html.escape(_MP_JOB_LABELS.get(job, job))
    outcome = html.escape(_MP_JOB_OUTCOMES.get(job, "готовая карточка товара для маркетплейса."))
    format_label = html.escape(_mp_platform_format_label(platform))
    price = action_price("edit")
    return (
        f"🛒 <b>{platform_name}</b> · {job_label}\n\n"
        f"📐 Формат: <b>{format_label}</b> · стоимость: <b>{price} кр</b>\n\n"
        "Пришли фото товара. Можно добавить короткую подпись: ниша, УТП, цвет бренда "
        "или что обязательно показать.\n\n"
        f"Что получится: {outcome}"
    )


def _mp_video_request_text(platform: str) -> str:
    platform_name = html.escape(_MP_PLAT_NAMES.get(platform, platform))
    model_name = html.escape(L(f"vid_model_name:{VID_REF_DEFAULT_MODEL}"))
    price = video_price(VID_REF_DEFAULT_MODEL, 1, "ingredients")
    return (
        f"🎬 <b>{platform_name}</b> · оживить фото товара\n\n"
        "Пришли одно фото товара. Подпись к фото можно использовать как сценарий: "
        "например, «медленный поворот, мягкий свет, акцент на фактуре».\n\n"
        f"По умолчанию: <b>{model_name}</b>, 9:16, 1 видео · {price} кр."
    )


def _mp_series_request_text(platform: str, count: int) -> str:
    platform_name = html.escape(_MP_PLAT_NAMES.get(platform, platform))
    count = count if count in _MP_SERIES_COUNTS else 3
    label = html.escape(_MP_SERIES_LABELS[count])
    format_label = html.escape(_mp_platform_format_label(platform))
    price = action_price("mp_series", count)
    return (
        f"🧩 <b>{platform_name}</b> · {label} · {count} {_slides_word(count)} · {price} кр\n\n"
        f"📐 Формат серии: <b>{format_label}</b>\n\n"
        "Пришли одно фото товара. Я соберу серию слайдов "
        "для карточки маркетплейса на основе этого товара.\n\n"
        "Можно добавить подпись к фото — например нишу, УТП, цвет бренда или "
        "что обязательно показать в серии."
    )

def _mp_brand_kit(user_id: int) -> str:
    profile = metrics.get_seller_profile(user_id)
    return str(profile.get("brand_kit") or "").strip()


def _mp_niche(user_id: int) -> str:
    profile = metrics.get_seller_profile(user_id)
    return str(profile.get("niche") or "").strip()


def _mp_niche_label(user_id: int) -> str:
    niche_id = _mp_niche(user_id)
    item = _MP_NICHES.get(niche_id)
    return item[0] if item else ""


def _mp_sku_open_text(user_id: int, sku: str) -> str:
    project = metrics.get_seller_sku_project(user_id, sku) or {"sku": sku, "items": 0}
    sku_name = str(project.get("sku") or sku or "SKU")
    count = int(project.get("items") or 0)
    platform = str(project.get("platform") or "").strip()
    platform_label = _MP_PLAT_NAMES.get(platform, platform) if platform else "не задана"
    updated = (project.get("updated_at") or "")[:16] or "—"
    latest_prompt = str(project.get("latest_prompt") or "").strip()
    lines = [
        f"📦 <b>{html.escape(sku_name)}</b>",
        "",
        f"Слайдов: <b>{count} {_slides_word(count)}</b>",
        f"Площадка: {html.escape(platform_label)}",
        f"Обновлено: {html.escape(updated)}",
    ]
    if latest_prompt:
        lines.append(f"Последний запрос: <blockquote>{html.escape(latest_prompt[:180])}</blockquote>")
    else:
        lines.append("В этом SKU пока нет сохранённых карточек.")
    return "\n".join(lines)


def _mp_brandkit_text(user_id: int) -> str:
    brand = _mp_brand_kit(user_id)
    current = (
        f"\n\nТекущий бренд-кит:\n<blockquote>{html.escape(brand)}</blockquote>"
        if brand else
        "\n\nТекущий бренд-кит не задан."
    )
    return (
        "🎨 <b>Бренд-кит</b>\n\n"
        "Пришли одним сообщением цвета, стиль, тон и правила для карточек. "
        "Например: «чёрный/золото, премиальный минимализм, крупный товар, "
        "без кислотных фонов, логотип не рисовать». "
        "Я буду учитывать это в карточках и сериях."
        f"{current}"
    )


def _mp_niche_text(user_id: int) -> str:
    niche = _mp_niche(user_id)
    current = _MP_NICHES.get(niche, ("не задана", ""))[0] if niche else "не задана"
    return (
        "🏷️ <b>Ниша товара</b>\n\n"
        "Выбери основную категорию магазина. Я буду добавлять её как подсказку "
        "к карточкам и сериям, чтобы ракурсы, фон и акценты были ближе к товару.\n\n"
        f"Текущая ниша: <b>{html.escape(current)}</b>"
    )


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
