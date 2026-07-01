"""Telegram inline-keyboard builders (Phase 5).

Extracted verbatim from flow_bot. Pure rendering: build aiogram markup from copy
keys and literal callback_data. flow_bot re-exports these for its handlers.
"""

from __future__ import annotations

from aiogram import types

import flow_copy
import prompts_lib

L = flow_copy.label


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
