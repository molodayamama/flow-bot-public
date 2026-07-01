"""Topup / pack pricing + display helpers (Phase 5 wave 3).

Extracted verbatim from flow_bot. Pure pricing/label helpers over flow_core
pricing and config.settings; flow_bot re-exports them.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from config.settings import IS_SELLER, ROBOKASSA_CARD_DISCOUNT_PCT, STARS_TO_RUB
from flow_core import (
    VIDEO_MODELS,
    action_price,
    pack as credit_pack,
    price_gen,
    robokassa_pack_amount,
    video_price,
)


def _robokassa_pack_amount(pack_id: str) -> str:
    return robokassa_pack_amount(pack_id, STARS_TO_RUB, ROBOKASSA_CARD_DISCOUNT_PCT)


def _rub_display(amount: str) -> str:
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return str(amount)
    if value == value.to_integral_value():
        return str(int(value))
    return f"{value:.2f}"


def _topup_image_price() -> int:
    return action_price("edit") if IS_SELLER else price_gen(1)


def _topup_video_price() -> int:
    prices = [int(m.get("price") or 0) for m in VIDEO_MODELS.values() if int(m.get("price") or 0) > 0]
    return min(prices) if prices else video_price("omni-flash-4s", 1)


def _pack_usage_hint(credits: int) -> str:
    """Краткий hint, сколько карточек/видео можно создать на пакет."""
    credits = max(0, int(credits))
    cards = credits // max(_topup_image_price(), 1)
    videos = credits // max(_topup_video_price(), 1)
    parts = []
    if cards:
        parts.append(f"≈{cards} карточек")
    if videos:
        parts.append(f"≈{videos} видео")
    return " / ".join(parts) if parts else "для старта"


def _stars_pack_label(pack_id: str) -> str:
    p = credit_pack(pack_id)
    if not p:
        return pack_id
    if p.get("test"):
        return f"🧪 Тест · {p['credits']} кр · {p['stars']}⭐"
    return f"{p['credits']} кр · {_pack_usage_hint(p['credits'])} · {p['stars']}⭐"


def _robokassa_pack_label(pack_id: str) -> str:
    p = credit_pack(pack_id)
    if not p:
        return "СБП/карта"
    amount = _rub_display(_robokassa_pack_amount(pack_id))
    return f"{p['credits']} кр · {_pack_usage_hint(p['credits'])} · {amount} ₽"
