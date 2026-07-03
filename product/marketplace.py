"""Marketplace seller-domain data + pure helpers (Phase 11 core split).

Platform-neutral seller config (marketplace names, card formats, per-platform
and per-niche prompt guidance) relocated out of the Telegram adapter so core
and any channel can use it without importing UI code. No local imports.
"""

_MP_PLAT_NAMES = {"wb": "Wildberries", "ozon": "Ozon", "ym": "Яндекс Маркет"}

_MP_PLATFORM_FMT = {"wb": "f34", "ozon": "f34", "ym": "sq"}

_MP_PLATFORM_SIZE = {"wb": "1080x1440", "ozon": "1080x1440", "ym": "1000x1000"}

_MP_SERIES_COUNTS = (3, 5, 8)  # seller-selectable slide-series sizes

_MP_PLATFORM_GUIDANCE = {
    "wb": (
        "Wildberries: вертикальная 3:4 карточка, товар крупно; "
        "оставь верхнюю зону под короткий заголовок или выгоду."
    ),
    "ozon": (
        "Ozon: чистая светлая композиция, аккуратный белый или светло-серый фон, "
        "понятная зона под преимущества без визуального шума."
    ),
    "ym": (
        "Яндекс Маркет: квадратная 1:1 карточка, товар по центру, умеренные подписи; "
        "важное не прижимать к краям."
    ),
}

_MP_NICHES = {
    "clothes": (
        "Одежда",
        "показать посадку, фактуру ткани, сезонность и размер; уместны модель, flat lay и детали швов",
    ),
    "beauty": (
        "Косметика",
        "чистый премиальный свет, текстура продукта, оттенок, состав/эффект и аккуратные макро-детали",
    ),
    "electronics": (
        "Электроника",
        "выделить экран/разъёмы/комплектацию, сценарий использования, масштаб и ощущение надёжности",
    ),
    "kids": (
        "Детские товары",
        "мягкие светлые сцены, безопасность, возраст, комплектация и доверие для родителей",
    ),
    "food": (
        "Еда",
        "аппетитный свет, свежесть, упаковка, состав/вкус и аккуратная сервировка без лишнего шума",
    ),
}

def _mp_platform_fmt(platform: str) -> str:
    return _MP_PLATFORM_FMT.get(platform, "f34")

def _mp_platform_format_label(platform: str) -> str:
    fmt = _mp_platform_fmt(platform)
    name = {"f34": "3:4", "sq": "1:1"}.get(fmt, fmt)
    size = _MP_PLATFORM_SIZE.get(platform)
    return f"{name} ({size})" if size else name

def _mp_platform_guidance(platform: str) -> str:
    return _MP_PLATFORM_GUIDANCE.get(platform, _MP_PLATFORM_GUIDANCE["wb"])

def _mp_niche_guidance(niche: str | None) -> str:
    item = _MP_NICHES.get((niche or "").strip())
    if not item:
        return ""
    label, guidance = item
    return f"{label}: {guidance}"
