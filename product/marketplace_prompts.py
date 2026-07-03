"""Marketplace product-prompt builders (Phase 11 core split).

Pure seller-domain prompt generation moved out of flow_bot.py. Depends on
display constants still owned by the Telegram adapter (transitional import;
the constants themselves are a later relocation). No flow_bot import.
"""

from __future__ import annotations

from channels.telegram.keyboards import (
    _MP_PLAT_NAMES,
    _mp_platform_format_label,
    _mp_platform_guidance,
    _mp_niche_guidance,
)
from channels.telegram.texts import _MP_SERIES_COUNTS


_MP_JOB_SEED = {
    "whitebg": "товар на чистом белом фоне для карточки маркетплейса, студийный свет",
    "info": "инфографика-карточка товара: крупный товар, место под заголовок и буллеты",
    "model": "товар на модели / в интерьере, реалистичная сцена для карточки",
    "cover": "обложка/главный слайд карточки товара, цепляющий ракурс",
    "bg": "заменить фон у фото товара на чистый и продающий",
}



def _mp_job_instruction(
    job: str,
    platform: str,
    seller_note: str | None = None,
    brand_kit: str | None = None,
    niche: str | None = None,
) -> str:
    seed = _MP_JOB_SEED.get(job, "сделать продающую карточку товара для маркетплейса")
    platform_name = _MP_PLAT_NAMES.get(platform, platform)
    format_label = _mp_platform_format_label(platform)
    platform_guidance = _mp_platform_guidance(platform)
    prompt = (
        f"{seed}. Используй загруженное фото как исходный товар, сохрани товар узнаваемым. "
        f"Формат карточки {format_label}, площадка: {platform_name}. {platform_guidance}"
    )
    guidance = _mp_niche_guidance(niche)
    if guidance:
        prompt += f" Ниша товара: {guidance}"
    note = (seller_note or "").strip()
    if note:
        prompt += f" Уточнение продавца: {note}"
    brand = (brand_kit or "").strip()
    if brand:
        prompt += f" Бренд-кит продавца: {brand}"
    return prompt


def _mp_video_prompt(
    platform: str,
    seller_note: str | None = None,
    *,
    brand_kit: str | None = None,
    niche: str | None = None,
) -> str:
    platform_name = _MP_PLAT_NAMES.get(platform, platform)
    parts = [
        "Create a short marketplace product video from the provided product photo.",
        f"Marketplace: {platform_name}.",
        "Use the exact product from the photo; preserve its shape, color, material, logo/text, and packaging.",
        "Make clean premium product-card motion: slow camera push-in, subtle parallax, soft studio light, tidy commercial background.",
        "No extra hands, no fake labels, no distorted text, no unrelated objects, no aggressive zoom.",
        "The result should feel ready for a product card or short marketplace listing video.",
    ]
    guidance = _mp_niche_guidance(niche)
    if guidance:
        parts.append(guidance)
    note = (seller_note or "").strip()
    if note:
        parts.append(f"Seller's video direction: {note}.")
    brand = (brand_kit or "").strip()
    if brand:
        parts.append(f"Brand kit / visual rules: {brand}.")
    return " ".join(parts)


def _mp_series_prompt(
    platform: str,
    count: int,
    seller_note: str | None = None,
    brand_kit: str | None = None,
    niche: str | None = None,
) -> str:
    platform_name = _MP_PLAT_NAMES.get(platform, platform)
    count = count if count in _MP_SERIES_COUNTS else 3
    format_label = _mp_platform_format_label(platform)
    platform_guidance = _mp_platform_guidance(platform)
    prompt = (
        f"Создай {count} разных слайдов формата {format_label} для карточки товара на {platform_name}. "
        "Используй загруженное фото как исходный товар, сохрани товар узнаваемым. "
        "Каждый результат должен быть отдельным слайдом одной серии: главный слайд, "
        "выгоды, характеристики, детали применения и доверие/гарантия. "
        "Единый аккуратный стиль, крупный товар, чистая композиция, место под короткий читаемый текст. "
        f"{platform_guidance}"
    )
    guidance = _mp_niche_guidance(niche)
    if guidance:
        prompt += f" Ниша товара: {guidance}"
    note = (seller_note or "").strip()
    if note:
        prompt += f" Уточнение продавца: {note}"
    brand = (brand_kit or "").strip()
    if brand:
        prompt += f" Бренд-кит продавца: {brand}"
    return prompt
