"""Telegram inline-keyboard builders (Phase 5).

Extracted verbatim from flow_bot. Pure rendering: build aiogram markup from copy
keys and literal callback_data. flow_bot re-exports these for its handlers.
"""

from __future__ import annotations

import html
from collections.abc import Callable

from aiogram import types

import flow_copy
import prompts_lib

import config.settings as _cfg
from config.settings import SBP_PAYMENT_ENABLED, STARS_PAYMENT_ENABLED
from billing.pricing import _robokassa_pack_label, _stars_pack_label
from config.video import (
    SELECT_STYLE,
    VIDEO_EXTEND_MODEL,
    VID_FRAMES_VARIANTS,
    VID_REF_VARIANTS,
    _VID_STYLES,
    _VID_QUICKSTART_MODEL,
)
from flow_core import (
    DEFAULT_IMAGE_MODEL,
    IMAGE_MODELS,
    VideoRef,
    action_callback_data,
    action_price,
    image_model_extra,
    price_gen,
    public_pack_ids,
    video_animate_min_price,
    video_extend_price,
    video_models_in_family,
    video_price,
)
from storage.media_registry import video_registry

L = flow_copy.label


def _balance_reply_label(
    user_id: int | None = None,
    *,
    balance_fn: Callable[[int], int] | None = None,
) -> str:
    label = L("kb_balance")
    if user_id is None or balance_fn is None:
        return label
    try:
        return f"{label} · {balance_fn(user_id)}кр"
    except Exception:
        return label


def _is_balance_reply_text(text: str) -> bool:
    label = L("kb_balance")
    return text == label or text.startswith(f"{label} ·")


def reply_menu_kb(
    user_id: int | None = None,
    *,
    balance_fn: Callable[[int], int] | None = None,
    is_seller: bool | None = None,
) -> types.ReplyKeyboardMarkup:
    """Persistent bottom chat keyboard."""
    B = types.KeyboardButton
    seller_mode = _cfg.IS_SELLER if is_seller is None else is_seller
    if seller_mode:
        return types.ReplyKeyboardMarkup(
            keyboard=[[B(text=L("kb_menu")), B(text=_balance_reply_label(user_id, balance_fn=balance_fn))]],
            resize_keyboard=True,
            is_persistent=True,
        )
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [B(text=L("kb_gen")), B(text=L("kb_vid"))],
            [B(text=L("kb_menu")), B(text=_balance_reply_label(user_id, balance_fn=balance_fn))],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Опиши картинку или жми «🎨 Создать картинку»",
    )


# --- Marketplace render data + keyboards (Phase 5: moved from flow_bot) ---

_MP_PLAT_NAMES = {"wb": "Wildberries", "ozon": "Ozon", "ym": "Яндекс Маркет"}

_MP_PLATFORM_FMT = {"wb": "f34", "ozon": "f34", "ym": "sq"}

_MP_PLATFORM_SIZE = {"wb": "1080x1440", "ozon": "1080x1440", "ym": "1000x1000"}

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

_MP_JOB_LABELS = {
    "whitebg": "белый фон для каталога",
    "info": "готовая карточка с инфографикой",
    "model": "товар на модели / в сцене",
    "cover": "обложка / главный слайд",
    "bg": "заменить фон",
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

def _mp_jobs_text(platform: str) -> str:
    plat = platform if platform in _MP_PLAT_NAMES else "wb"
    return (
        "🛒 <b>Что сделать с товаром?</b>\n\n"
        "Выбери результат, пришли фото товара и проверь цену перед созданием.\n"
        f"По умолчанию: <b>{html.escape(_MP_PLAT_NAMES[plat])}</b>, "
        f"{html.escape(_mp_platform_format_label(plat))}. Площадку можно поменять на следующем шаге."
    )

def _mp_more_text(platform: str) -> str:
    return (
        "⚙️ <b>Ещё для карточки</b>\n\n"
        "Дополнительные задачи и настройки магазина. Если нужен быстрый результат, "
        "вернись к основным задачам."
    )

def _mp_photo_settings_kb(plat: str) -> types.InlineKeyboardMarkup:
    """Экран приёма фото: выбор площадки (формат/стиль) прямо здесь, перед
    генерацией — вместо отдельного шага выбора площадки в начале."""
    B = types.InlineKeyboardButton
    plat = plat if plat in _MP_PLAT_NAMES else "wb"
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            _sel_btn("🟣 WB 3:4", plat == "wb", "mp:setplat:wb"),
            _sel_btn("🔵 Ozon 3:4", plat == "ozon", "mp:setplat:ozon"),
            _sel_btn("🟡 ЯМ 1:1", plat == "ym", "mp:setplat:ym"),
        ],
        [B(text="◀️ Назад", callback_data="m:mp")],
        [_menu_button("menu", "m:menu")],
    ])

def mp_series_kb(platform: str) -> types.InlineKeyboardMarkup:
    """Выбор размера серии слайдов для одного товара."""
    B = types.InlineKeyboardButton
    plat = platform if platform in _MP_PLAT_NAMES else "wb"
    rows = [
        [B(text=f"🧩 Мини-серия · 3 слайда · {action_price('mp_series', 3)} кр", callback_data="mp:series:3")],
        [B(text=f"🧩 Стандарт · 5 слайдов · {action_price('mp_series', 5)} кр", callback_data="mp:series:5")],
        [B(text=f"🧩 Полная карточка · 8 слайдов · {action_price('mp_series', 8)} кр", callback_data="mp:series:8")],
        [B(text="◀️ Задачи", callback_data="m:mp")],
        [_menu_button("menu", "m:menu")],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=rows)

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

def _mp_niche_guidance(niche: str | None) -> str:
    item = _MP_NICHES.get((niche or "").strip())
    if not item:
        return ""
    label, guidance = item
    return f"{label}: {guidance}"

def _mp_niche_kb() -> types.InlineKeyboardMarkup:
    B = types.InlineKeyboardButton
    rows = [
        [B(text=f"🏷️ {label}", callback_data=f"mp:niche:{niche_id}")]
        for niche_id, (label, _guidance) in _MP_NICHES.items()
    ]
    rows.append([B(text="◀️ Назад", callback_data="m:mp")])
    rows.append([_menu_button("menu", "m:menu")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _menu_button(copy_key: str, data: str) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text=L(copy_key), callback_data=data)


def _img_retry_kb() -> types.InlineKeyboardMarkup:
    """Клавиатура под сообщением об ошибке картинки: повтор + меню."""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [_menu_button("img_retry", "img:retry")],
        [_menu_button("menu", "m:menu")],
    ])


def mp_root_kb() -> types.InlineKeyboardMarkup:
    """Выбор площадки — задаёт формат/стиль карточки."""
    B = types.InlineKeyboardButton
    rows = [
        [B(text="🟣 Wildberries", callback_data="mp:plat:wb")],
        [B(text="🔵 Ozon", callback_data="mp:plat:ozon")],
        [B(text="🟡 Яндекс Маркет", callback_data="mp:plat:ym")],
        [_menu_button("menu", "m:menu")],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def mp_jobs_kb(platform: str) -> types.InlineKeyboardMarkup:
    """Primary seller jobs. Secondary actions live behind ``mp:more``."""
    B = types.InlineKeyboardButton
    rows = [
        [B(text="✨ Готовая карточка с инфографикой", callback_data="mp:job:info")],
        [B(text="📸 Белый фон для каталога", callback_data="mp:job:whitebg")],
        [B(text="🧍 Товар на модели / в сцене", callback_data="mp:job:model")],
        [B(text="🧩 Серия слайдов", callback_data="mp:series")],
        [B(text="⚙️ Ещё", callback_data="mp:more")],
        [_menu_button("menu", "m:menu")],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def mp_more_kb(platform: str) -> types.InlineKeyboardMarkup:
    """Secondary seller jobs and workspace/settings actions."""
    B = types.InlineKeyboardButton
    rows = [
        [B(text="🖼 Обложка / главный слайд", callback_data="mp:job:cover")],
        [B(text="✂️ Заменить фон", callback_data="mp:job:bg")],
        [B(text="🎬 Видео из фото товара", callback_data="mp:job:animate")],
        [
            B(text="🎨 Стиль бренда", callback_data="mp:brandkit"),
            B(text="🏷️ Ниша", callback_data="mp:niche"),
        ],
        [B(text="📦 Мои товары (SKU)", callback_data="mp:projects")],
        [B(text="💡 Советы по карточке", callback_data="mp:tips")],
        [B(text="◀️ Основные задачи", callback_data="m:mp")],
        [_menu_button("menu", "m:menu")],
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _mp_back_kb() -> types.InlineKeyboardMarkup:
    B = types.InlineKeyboardButton
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [B(text="◀️ Назад", callback_data="m:mp")],
        [_menu_button("menu", "m:menu")],
    ])


def _mp_sku_open_kb() -> types.InlineKeyboardMarkup:
    B = types.InlineKeyboardButton
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [B(text="➕ Добавить текущую/последнюю карточку", callback_data="mp:sku:addlast")],
        [B(text="✏️ Переименовать", callback_data="mp:sku:rename")],
        [B(text="🗑 Удалить", callback_data="mp:sku:delete")],
        [B(text="◀️ Все SKU", callback_data="mp:projects")],
        [_menu_button("menu", "m:menu")],
    ])


def _onboarding_step1_kb() -> types.InlineKeyboardMarkup:
    """Первый шаг онбординга: что хочет создать новый пользователь?"""
    B = types.InlineKeyboardButton
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [B(text=L("ob_img"),   callback_data="ob:img")],
        [B(text=L("ob_vid"),   callback_data="ob:vid")],
        [B(text=L("ob_photo"), callback_data="ob:photo")],
        [B(text=L("ob_skip"),  callback_data="ob:skip")],
    ])


def _onboarding_step2_kb(kind: str) -> types.InlineKeyboardMarkup:
    """Второй шаг онбординга: пример + кнопка «Попробовать»."""
    B = types.InlineKeyboardButton
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [B(text=L("ob_go"), callback_data=f"ob:go:{kind}")],
        [B(text=L("ob_skip"), callback_data="ob:skip")],
    ])


def _prompt_picker_kb(_ideas: list[str]) -> types.InlineKeyboardMarkup:
    """Клавиатура шага 1: только обновление идей и выход (пользователь пишет текстом)."""
    B = types.InlineKeyboardButton
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [B(text="🔄 Ещё идеи", callback_data="w:idea:next")],
        [_menu_button("cancel", "w:cancel")],
    ])


def _photo_route_kb() -> types.InlineKeyboardMarkup:
    B = types.InlineKeyboardButton
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [B(text="🎨 Создать изображение", callback_data="pr:img")],
        [B(text="🎬 Создать видео", callback_data="pr:vid")],
        [_menu_button("cancel", "pr:cancel")],
    ])


def _templates_picker_kb() -> types.InlineKeyboardMarkup:
    B = types.InlineKeyboardButton
    rows = [[B(text=prompts_lib.get_template(tid)["title"], callback_data=f"tp:tpl:{tid}")]
            for tid in prompts_lib.template_ids()]
    rows.append([_menu_button("back", "ih:root")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _guided_step_kb(step: int) -> types.InlineKeyboardMarkup:
    B = types.InlineKeyboardButton
    s = prompts_lib.guided_steps()[step]
    rows = [[B(text=opt["label"], callback_data=f"gp:opt:{i}")]
            for i, opt in enumerate(s["options"])]
    nav = [_menu_button("back", "gp:back")] if step > 0 else [_menu_button("back", "ih:root")]
    nav.append(_menu_button("cancel", "gp:cancel"))
    rows.append(nav)
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def main_menu_kb(show_repeat: bool = False, credits: int | None = None) -> types.InlineKeyboardMarkup:
    B = types.InlineKeyboardButton
    balance_label = (
        f"💳 {credits} кр · Пополнить" if credits is not None
        else L("balance")
    )
    if _cfg.IS_SELLER:
        # Селлер-бот (@photozhab_wb_bot): маркетплейс-ориентированное меню —
        # карточки первым экраном, без консьюмерских пунктов (свободная
        # генерация/видео/идеи/мои фото).
        rows = [
            [B(text="🛒 Карточки для маркетплейсов", callback_data="m:mp")],
            [B(text=balance_label, callback_data="m:balance")],
            [_menu_button("profile", "m:profile"), _menu_button("invite", "m:invite")],
        ]
        return types.InlineKeyboardMarkup(inline_keyboard=rows)

    rows = [
        [_menu_button("gen", "m:gen")],
        [_menu_button("vid_gen", "m:vid")],
        [B(text=f"{L('animate')} · от {video_animate_min_price()} кр", callback_data="m:animate")],
        [_menu_button("myphoto", "m:myphoto")],
        [_menu_button("ideas", "m:ideas")],
        [B(text=balance_label, callback_data="m:balance")],
        [_menu_button("profile", "m:profile"), _menu_button("invite", "m:invite")],
    ]
    # show_repeat parameter kept for backward compatibility but ignored
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def topup_method_kb() -> types.InlineKeyboardMarkup:
    try:
        import config_store as _cs
        _flags = _cs.get_section("flags")
        stars_on = bool(_flags.get("stars_pay", STARS_PAYMENT_ENABLED))
        sbp_on   = bool(_flags.get("sbp_pay",   SBP_PAYMENT_ENABLED))
    except Exception:
        stars_on, sbp_on = STARS_PAYMENT_ENABLED, SBP_PAYMENT_ENABLED
    rows = []
    if stars_on:
        rows.append([_menu_button("pay_stars", "m:pay:stars")])
    if sbp_on:
        rows.append([_menu_button("pay_robo", "m:pay:robo")])
    rows.append([_menu_button("back", "m:balance")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def topup_kb(is_admin: bool = False) -> types.InlineKeyboardMarkup:
    return topup_method_kb()


# --- Topup pack keyboards (Phase 5: moved from flow_bot) ---

def _include_test_packs(is_admin: bool = False) -> bool:
    return bool(_cfg.TOPUP_TEST_PACKS_ENABLED and is_admin)

def topup_stars_kb(is_admin: bool = False) -> types.InlineKeyboardMarkup:
    rows = []
    for pid in public_pack_ids(include_test=_include_test_packs(is_admin), seller=_cfg.IS_SELLER):
        rows.append([types.InlineKeyboardButton(text=_stars_pack_label(pid), callback_data=f"m:pack:{pid}")])
    rows.append([_menu_button("back", "m:topup")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)

def topup_robo_kb(is_admin: bool = False) -> types.InlineKeyboardMarkup:
    rows = [
        [types.InlineKeyboardButton(text=_robokassa_pack_label(pid), callback_data=f"m:robo:{pid}")]
        for pid in public_pack_ids(include_test=_include_test_packs(is_admin), seller=_cfg.IS_SELLER)
    ]
    rows.append([_menu_button("back", "m:topup")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


# --- Leaf image/video option keyboards (Phase 5: moved from flow_bot) ---

def _imodel_row(
    selected: str,
    prefix: str = "w:imodel",
    *,
    base_price: int | None = None,
) -> list:
    """Ряд выбора модели картинки (Nano Banana 2 / Pro) с наценкой в подписи."""
    B = types.InlineKeyboardButton
    row = []
    base = price_gen(1) if base_price is None else int(base_price)
    for mid, meta in IMAGE_MODELS.items():
        price = base + int(meta.get("extra") or 0)
        label = f"{meta['label']} · {price} кр"
        row.append(_sel_btn(label, mid == selected, f"{prefix}:{mid}"))
    return row

def _nwiz_styles_kb() -> types.InlineKeyboardMarkup:
    B = types.InlineKeyboardButton
    _STYLE_EMOJI = {"": "❌", "cine": "🎬", "anime": "🎌", "3d": "🖥", "photo": "📷", "retro": "📼"}
    rows = [
        [B(text=f"{_STYLE_EMOJI.get(k,'•')} {name}", callback_data=f"v:nstyle:{k}")]
        for k, (name, _) in _VID_STYLES.items()
    ]
    rows.append([B(text="← Назад", callback_data="v:nstyle:back")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


# --- Image result keyboards (Phase 5: moved verbatim from flow_bot) ---

def _image_keyboard(token: str) -> types.InlineKeyboardMarkup:
    """Инлайн-кнопки под картинкой: Изменить · Повторить · Улучшить качество · Оживить."""
    B = types.InlineKeyboardButton

    edit_price = action_price("edit")
    edit_label = f"✏️ Изменить · {edit_price} кр" if edit_price > 0 else "✏️ Изменить"
    upscale_price = action_price("realup")
    upscale_label = (
        f"{L('realup')} · {upscale_price} кр" if upscale_price > 0 else L("realup")
    )
    animate_price = _vid_family_min_price("ing")
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                B(text=edit_label, callback_data=action_callback_data("edit", token)),
                B(text="🔁 Повторить", callback_data="m:repeat"),
            ],
            [
                # Родной апскейл сервиса (2K): присылает улучшенную картинку.
                B(text=upscale_label, callback_data=action_callback_data("realup", token)),
            ],
            [
                B(text=f"🎬 Оживить фото · от {animate_price} кр", callback_data=f"an:img:{token}"),
            ],
        ]
    )


def _seller_image_keyboard(token: str) -> types.InlineKeyboardMarkup:
    """Тулбар под seller-карточкой. Без «🔁 Повторить» — для seller он не работал
    (нет сохранённого prompt-состояния; повтор = просто прислать фото заново)."""
    B = types.InlineKeyboardButton
    edit_price = action_price("edit")
    edit_label = f"✏️ Изменить · {edit_price} кр" if edit_price > 0 else "✏️ Изменить"
    upscale_price = action_price("realup")
    upscale_label = (
        f"{L('realup')} · {upscale_price} кр" if upscale_price > 0 else L("realup")
    )
    animate_price = _vid_family_min_price("ing")
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [B(text=edit_label, callback_data=action_callback_data("edit", token))],
        [B(text=upscale_label, callback_data=action_callback_data("realup", token))],
        [B(text=f"🎬 Оживить фото · от {animate_price} кр", callback_data=f"an:img:{token}")],
        [B(text="⬇️ Скачать для маркетплейса", callback_data=action_callback_data("mpexport", token))],
        [B(text="➕ В серию SKU", callback_data=action_callback_data("skuadd", token))],
        [B(text="🛒 Новая карточка", callback_data="m:mp")],
    ])


# --- Video wizard + edit keyboards (Phase 5: moved verbatim from flow_bot) ---

def _slides_word(n: int) -> str:
    """Правильная форма слова «слайд» для числа (1 слайд, 3 слайда, 5 слайдов)."""
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return "слайдов"
    d = n % 10
    if d == 1:
        return "слайд"
    if 2 <= d <= 4:
        return "слайда"
    return "слайдов"

def _sel_btn(label: str, chosen: bool, callback_data: str) -> types.InlineKeyboardButton:
    """Wizard-option button; the chosen one turns green via Bot API 9.4 ``style``.

    ``style="success"`` (Bot API 9.4, Feb 2026) colours the button green on the
    client. aiogram 3.24 doesn't type the field, but pydantic forwards it in the
    outgoing JSON, so we pass it as an extra kwarg only when selected. Clients
    older than 9.4 simply ignore the unknown field — the label stays readable, just
    without the colour (graceful degradation), so no checkmark prefix is needed.
    """
    kwargs = {"text": label, "callback_data": callback_data}
    if chosen:
        kwargs["style"] = SELECT_STYLE
    return types.InlineKeyboardButton(**kwargs)

def _imodel_toggle_btn(selected: str, prefix: str = "w:imodel") -> types.InlineKeyboardButton:
    """Одна кнопка-тогл модели: показывает текущую, клик → следующая по кругу."""
    ids = list(IMAGE_MODELS.keys())
    meta = IMAGE_MODELS.get(selected, IMAGE_MODELS[ids[0]])
    # Следующая модель по кругу
    cur_idx = ids.index(selected) if selected in ids else 0
    next_id = ids[(cur_idx + 1) % len(ids)]
    label = f"🎨 Модель: {meta['label']}"
    return types.InlineKeyboardButton(text=label, callback_data=f"{prefix}:{next_id}")

def _fmt_rows(fmt: str, prefix: str = "w:fmt") -> list:
    """Два ряда выбора формата картинки (16:9 / 4:3 / 1:1 / 3:4 / 9:16)."""
    B = types.InlineKeyboardButton

    def fb(code: str, key: str):
        return _sel_btn(L(key), fmt == code, f"{prefix}:{code}")

    return [
        [fb("land", "fmt:land"), fb("f43", "fmt:f43"), fb("sq", "fmt:sq")],
        [fb("f34", "fmt:f34"), fb("port", "fmt:port")],
    ]

def wizard_kb(
    count: int,
    fmt: str,
    imodel: str = DEFAULT_IMAGE_MODEL,
    *,
    show_boost: bool = False,
    show_improve: bool = False,
) -> types.InlineKeyboardMarkup:
    """Шаг 2: настройки генерации (количество + формат + модель-тогл + «Сгенерировать»)."""
    B = types.InlineKeyboardButton
    total_price = price_gen(count) + image_model_extra(imodel) * count
    go_label = f"{L('go')} · {total_price} кр"
    rows: list[list[types.InlineKeyboardButton]] = [
        [
            _sel_btn(L("cnt:1"), count == 1, "w:cnt:1"),
            _sel_btn(L("cnt:2"), count == 2, "w:cnt:2"),
            _sel_btn(L("cnt:4"), count == 4, "w:cnt:4"),
        ],
        *_fmt_rows(fmt, "w:fmt"),
        [_imodel_toggle_btn(imodel, "w:imodel")],
    ]
    # AI-агент: улучшить промпт (3 варианта). Заменяет старый бесплатный boost.
    # «Улучшить промпт» стоит выше «Сгенерировать», чтобы сначала предложить
    # доработку запроса, и только потом — финальный запуск.
    if show_improve:
        rows.append([B(
            text=f"✨ Улучшить промпт · {action_price('prompt_improve')} кр",
            callback_data="ag:improve",
        )])
    elif show_boost:
        rows.append([B(text=L("boost_prompt"), callback_data="w:boost_prompt")])
    rows.append([B(text=go_label, callback_data="w:go")])
    rows.append([
        B(text=L("change_prompt"), callback_data="w:change_prompt"),
        _menu_button("cancel", "w:cancel"),
    ])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)

def edit_settings_kb(fmt: str, imodel: str) -> types.InlineKeyboardMarkup:
    """Формат + модель для редактирования фото (правку пользователь вводит текстом).

    Callback-префикс ``es:`` намеренно не пересекается с ``edit:`` (кнопка
    «Изменить» под картинкой), иначе хендлер перехватил бы её.
    """
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            *_fmt_rows(fmt, "es:fmt"),
            [_imodel_toggle_btn(imodel, "es:imodel")],
            [_menu_button("cancel", "es:cancel")],
        ]
    )

def edit_confirm_kb(fmt: str, imodel: str, *, as_generation: bool = False) -> types.InlineKeyboardMarkup:
    """Подтверждение правки фото: настройки + сгенерировать/улучшить запрос.

    ``as_generation`` — фото пришло из «Создать картинку» (референс к новому
    изображению), поэтому цена как у генерации (10/15), а не как у правки (15/20).
    """
    B = types.InlineKeyboardButton
    # Цена = базовая + надбавка модели (как в _edit_and_send), чтобы менялась при
    # переключении модели.
    base = price_gen(1) if as_generation else action_price("edit")
    edit_price = base + image_model_extra(imodel)
    return types.InlineKeyboardMarkup(inline_keyboard=[
        *_fmt_rows(fmt, "es:fmt"),
        [_imodel_toggle_btn(imodel, "es:imodel")],
        [B(text=f"✨ Улучшить запрос · {action_price('prompt_improve')} кр", callback_data="ag:eimprove")],
        [B(text=f"✅ Сгенерировать · {edit_price} кр", callback_data="es:apply")],
        [B(text="✏️ Изменить запрос", callback_data="es:change"),
         _menu_button("cancel", "es:cancel")],
    ])

def _vid_family_min_price(code: str) -> int:
    """Минимальная цена в семействе — для подписи кнопки «· от N кр» (без хардкода)."""
    if code == "omni":
        return min(video_price(m, 1, "text") for m, _ in video_models_in_family("omni-flash"))
    if code == "veo":
        return min(video_price(m, 1, "text") for m, _ in video_models_in_family("veo"))
    if code == "ing":
        return video_animate_min_price()
    if code == "frm":
        return min(video_price(m, 1, "frames") for m in VID_REF_VARIANTS)
    return 0

def video_family_kb() -> types.InlineKeyboardMarkup:
    B = types.InlineKeyboardButton

    def fam(code: str) -> types.InlineKeyboardButton:
        return B(
            text=f"{L('vid_fam:' + code)} · от {_vid_family_min_price(code)} кр",
            callback_data=f"v:fam:{code}",
        )

    # Быстрый старт — omni-flash-4s без пикера модели
    quick_price = video_price(_VID_QUICKSTART_MODEL, 1, "text")
    quick_btn = B(text=f"⚡ Быстро · {quick_price} кр", callback_data="v:quick")

    return types.InlineKeyboardMarkup(inline_keyboard=[
        [quick_btn],
        [fam("omni")],
        [fam("veo")],
        [fam("ing")],
        [fam("frm")],
    ] + (
        [[B(text=f"{L('vid_upload_edit')} · {action_price('video_prompt_edit')} кр",
            callback_data="vu:start")]]
        if _cfg.UPLOAD_VIDEO_EDIT_ENABLED else []
    ) + [
        [B(text=L("cancel"), callback_data="v:cancel")],
    ])

def video_variant_kb(family: str, selected_model: str | None) -> types.InlineKeyboardMarkup:
    """Кнопки выбора конкретной модели внутри семейства."""
    B = types.InlineKeyboardButton
    rows = []
    for model_id, meta in video_models_in_family(family):
        name = L(f"vid_model_name:{model_id}")
        label = f"{name} · {meta['price']} кр"
        rows.append([_sel_btn(
            label, model_id == selected_model, f"v:model:{model_id}",
        )])
    rows.append([B(text=L("vid_back:fam"), callback_data="v:back:fam")])
    rows.append([B(text=L("cancel"), callback_data="v:cancel")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)

def video_wizard_kb(vfmt: str, vcount: int) -> types.InlineKeyboardMarkup:
    """Экран настроек: формат (16:9 / 9:16) + количество (1–4) + действия."""
    B = types.InlineKeyboardButton
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            _sel_btn(L("fmt:land"), vfmt == "land", "v:fmt:land"),
            _sel_btn(L("fmt:port"), vfmt == "port", "v:fmt:port"),
        ],
        [
            _sel_btn(f"{n}", vcount == n, f"v:cnt:{n}")
            for n in (1, 2, 3, 4)
        ],
        [_menu_button("vid_go", "v:go")],
        [_menu_button("vid_back:model", "v:back:model")],
        [_menu_button("cancel", "v:cancel")],
    ])

def _video_can_edit(ref: VideoRef | None) -> bool:
    return bool(ref and ref.media_id and ref.project_id and ref.workflow_id)

def _video_can_extend(ref: VideoRef | None) -> bool:
    return bool(
        ref
        and ref.media_id
        and ref.project_id
        and ref.workflow_id
        and str(ref.model_id).startswith("veo-")
        and not ref.prompt_edited
    )

def video_result_kb(vtoken: str) -> types.InlineKeyboardMarkup:
    """Клавиатура под результатом видео: Продлить · Изменить (раздельными строками)."""
    B = types.InlineKeyboardButton
    ref = video_registry.get(vtoken)

    can_extend = _video_can_extend(ref)
    can_edit = _video_can_edit(ref)

    rows: list[list[types.InlineKeyboardButton]] = []
    if can_extend:
        next_price = video_extend_price(VIDEO_EXTEND_MODEL, ref.extend_index + 1)
        rows.append([B(text=f"➕ Продлить · {next_price} кр", callback_data=f"v:extend:{vtoken}")])
    if can_edit:
        edit_price = action_price("video_prompt_edit")
        rows.append([B(text=f"✏️ Изменить · {edit_price} кр", callback_data=f"v:edit:{vtoken}")])
    if not rows:
        rows.append([_menu_button("menu", "m:menu")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)

def _vid_model_row(mode: str, selected: str | None) -> list:
    """Rows for selecting a video model with the current mode price."""
    variants = VID_REF_VARIANTS if mode == "ingredients" else VID_FRAMES_VARIANTS
    rows = []
    for mid in variants:
        price = video_price(mid, 1, mode)
        label = f"{L('vid_model_name:' + mid)} {price} кр"
        rows.append([_sel_btn(label, mid == selected, f"v:vmod:{mid}")])
    return rows

def _vid_fmt_count_rows(vfmt: str, vcount: int) -> list:
    """Общие ряды кнопок «формат + количество» для видео-экранов."""
    B = types.InlineKeyboardButton
    return [
        [
            _sel_btn(L("fmt:land"), vfmt == "land", "v:fmt:land"),
            _sel_btn(L("fmt:port"), vfmt == "port", "v:fmt:port"),
        ],
        [
            _sel_btn(f"{n}", vcount == n, f"v:cnt:{n}")
            for n in (1, 2, 3, 4)
        ],
    ]

def ingredients_kb(
    n: int, vfmt: str, vcount: int, vmodel: str | None, has_caption: bool = False
) -> types.InlineKeyboardMarkup:
    B = types.InlineKeyboardButton
    rows = _vid_model_row("ingredients", vmodel) + _vid_fmt_count_rows(vfmt, vcount)
    if n >= 1:
        # Подпись к фото уже задаёт описание → кнопка ведёт сразу на генерацию.
        done_key = "vid_ing_done_ready" if has_caption else "vid_ing_done"
        rows.append([B(text=L(done_key), callback_data="v:ing:done")])
    rows.append([B(text=L("vid_ing_clear"), callback_data="v:ing:clear")])
    rows.append([B(text=L("vid_back:fam"),  callback_data="v:back:fam")])
    rows.append([B(text=L("cancel"),        callback_data="v:cancel")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)

def frames_kb(has_start: bool, has_end: bool, vfmt: str, vcount: int, vmodel: str | None) -> types.InlineKeyboardMarkup:
    B = types.InlineKeyboardButton
    rows = _vid_model_row("frames", vmodel) + _vid_fmt_count_rows(vfmt, vcount)
    if has_start and has_end:
        rows.append([B(text=L("vid_frm_go"), callback_data="v:frm:go")])
    rows.append([B(text=L("vid_frm_clear"), callback_data="v:frm:clear")])
    rows.append([B(text=L("vid_back:fam"),  callback_data="v:back:fam")])
    rows.append([B(text=L("cancel"),        callback_data="v:cancel")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)
