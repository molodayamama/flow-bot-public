"""Telegram profile/gallery/support screen renderers."""

from __future__ import annotations

import html
import random
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any

from aiogram import types

import flow_copy
from channels.telegram.keyboards import (
    L,
    _MP_JOB_LABELS,
    _MP_PLAT_NAMES,
    _menu_button,
    _mp_platform_format_label,
    _slides_word,
    frames_kb,
    ingredients_kb,
    main_menu_kb,
    video_family_kb,
    video_variant_kb,
    video_wizard_kb,
)
from channels.telegram.texts import _MP_SERIES_COUNTS
from flow_core import action_callback_data, action_price, price_gen, video_price


@dataclass(frozen=True)
class ProfileScreensDeps:
    """Injected state and stores for profile-area screens."""

    metrics: Any
    workspace: Callable[[int], MutableMapping[str, Any]]
    image_registry: Any
    seller_history_text: Callable[[int], str]
    is_seller: Callable[[], bool]
    log: Any


@dataclass(frozen=True)
class PublicScreensDeps:
    """Injected state and stores for public menu/help/referral screens."""

    metrics: Any
    workspace: Callable[[int], MutableMapping[str, Any]]
    credit_store: Any
    is_seller: Callable[[], bool]
    invite_button: Callable[[int], types.InlineKeyboardButton]
    referral_link: Callable[[int], str]
    reply_menu_kb: Callable[[int], types.ReplyKeyboardMarkup]
    referral_referred_bonus: int
    referral_tier1_bonus: int
    referral_tier2_bonus: int
    referral_tier3_bonus: int
    referral_ongoing_pct: float


@dataclass(frozen=True)
class VideoReferenceScreensDeps:
    """Injected state and callbacks for video reference-mode screens."""

    wizard_state: MutableMapping[int, MutableMapping[str, Any]]
    credit_store: Any
    vid_edit: Callable[..., Awaitable[Any]]
    vid_default_fmt: str
    vid_default_count: int
    vid_ref_default_model: str
    vid_frames_default_model: str
    vid_fmt_names: Mapping[str, str]


@dataclass(frozen=True)
class VideoSettingsScreensDeps:
    """Injected state and callbacks for legacy video settings screens."""

    wizard_state: MutableMapping[int, MutableMapping[str, Any]]
    credit_store: Any
    vid_clear: Callable[[int], None]
    aspect_to_vfmt: Callable[[str], str]
    vid_edit: Callable[..., Awaitable[Any]]
    vid_default_fmt: str
    vid_default_count: int
    vid_fmt_names: Mapping[str, str]


@dataclass(frozen=True)
class MarketplaceScreensDeps:
    """Injected state and stores for marketplace seller screens."""

    workspace: Callable[[int], MutableMapping[str, Any]]
    metrics: Any
    credit_store: Any
    brand_kit: Callable[[int], str]
    niche_label: Callable[[int], str]
    stamp_message: Callable[[int, Any], None]


async def show_referral_screen(
    message: types.Message, *, user_id: int, edit: bool, deps: PublicScreensDeps
) -> None:
    stats = deps.metrics.referral_stats(user_id)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [deps.invite_button(user_id)],
        [_menu_button("menu", "m:menu")],
    ])
    text = flow_copy.msg(
        "referral_screen",
        link=html.escape(deps.referral_link(user_id)),
        invited=stats["invited"],
        earned=stats["earned"],
        referred=deps.referral_referred_bonus,
        t1=deps.referral_tier1_bonus,
        t2=deps.referral_tier2_bonus,
        t3=deps.referral_tier3_bonus,
        pct=int(round(deps.referral_ongoing_pct * 100)),
    )
    if edit:
        await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


async def show_help_screen(message: types.Message, *, edit: bool, deps: PublicScreensDeps) -> None:
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[_menu_button("menu", "m:menu")]])
    help_text = flow_copy.msg("seller_help" if deps.is_seller() else "help")
    if edit:
        await message.edit_text(help_text, reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer(help_text, reply_markup=kb, parse_mode="HTML")


async def show_main_menu(
    message: types.Message, *, user_id: int, edit: bool = False,
    ensure_kb: bool = False, deps: PublicScreensDeps,
) -> None:
    if ensure_kb:
        try:
            await message.answer("\u041c\u0435\u043d\u044e \u043e\u0442\u043a\u0440\u044b\u0442\u043e \U0001f447", reply_markup=deps.reply_menu_kb(user_id))
        except Exception:
            pass
    last = deps.workspace(user_id).get("last")
    credits = deps.credit_store.balance(user_id)
    kb = main_menu_kb(show_repeat=bool(last), credits=credits)
    if deps.is_seller():
        text = flow_copy.msg("seller_menu_title")
    else:
        variants = flow_copy.MESSAGES.get("menu_title_variants") or [flow_copy.msg("menu_title")]
        text = random.choice(variants)
    try:
        if edit:
            await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        else:
            await message.answer(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


async def show_balance(
    message: types.Message, *, user_id: int, edit: bool = True, deps: PublicScreensDeps
) -> None:
    credits = deps.credit_store.balance(user_id)
    text = flow_copy.msg(
        "balance_screen",
        credits=credits,
        price=price_gen(1),
        vprice=video_price("omni-flash-4s", 1),
    )
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [_menu_button("topup", "m:topup")],
            [_menu_button("menu", "m:menu")],
        ]
    )
    try:
        if edit:
            await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        else:
            await message.answer(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")


async def show_video_ingredients(
    message: types.Message, *, user_id: int, edit: bool = False,
    deps: VideoReferenceScreensDeps,
) -> None:
    st = deps.wizard_state[user_id]
    st["vstep"] = "ving"
    st["vawait"] = "ving_photo"
    st.setdefault("vmode", "ingredients")
    st.setdefault("vmodel", deps.vid_ref_default_model)
    st.setdefault("vfmt", deps.vid_default_fmt)
    st.setdefault("vcount", deps.vid_default_count)
    vfmt = st.get("vfmt", deps.vid_default_fmt)
    vcount = st.get("vcount", deps.vid_default_count)
    model_id = st.get("vmodel", deps.vid_ref_default_model)
    n = len(st.get("ving_photos") or [])
    text = flow_copy.msg(
        "vid_ing_screen",
        n=n,
        model=L(f"vid_model_name:{model_id}"),
        fmt=deps.vid_fmt_names.get(vfmt, vfmt),
        count=vcount,
        price=video_price(model_id, vcount, "ingredients"),
        credits=deps.credit_store.balance(user_id),
    )
    caption = st.get("vcaption_prompt")
    if caption:
        text += "\n\n" + flow_copy.msg(
            "vid_ing_ready_with_caption", prompt=html.escape(caption[:300])
        )
    kb = ingredients_kb(n, vfmt, vcount, model_id, has_caption=bool(caption))
    if edit:
        await deps.vid_edit(message, text, kb, user_id, parse_mode="HTML")
    else:
        sent = await message.answer(text, reply_markup=kb, parse_mode="HTML")
        st["vmsg_id"] = sent.message_id


async def show_video_frames(
    message: types.Message, *, user_id: int, edit: bool = False,
    deps: VideoReferenceScreensDeps,
) -> None:
    st = deps.wizard_state[user_id]
    st["vstep"] = "vfrm"
    st.setdefault("vmode", "frames")
    st.setdefault("vmodel", deps.vid_frames_default_model)
    st.setdefault("vfmt", deps.vid_default_fmt)
    st.setdefault("vcount", deps.vid_default_count)
    vfmt = st.get("vfmt", deps.vid_default_fmt)
    vcount = st.get("vcount", deps.vid_default_count)
    model_id = st.get("vmodel", deps.vid_frames_default_model)
    has_start = bool(st.get("vfrm_start"))
    has_end = bool(st.get("vfrm_end"))
    text = flow_copy.msg(
        "vid_frm_screen",
        model=L(f"vid_model_name:{model_id}"),
        start_mark="✅" if has_start else "⬜",
        end_mark="✅" if has_end else "⬜",
        fmt=deps.vid_fmt_names.get(vfmt, vfmt),
        count=vcount,
        price=video_price(model_id, vcount, "frames"),
        credits=deps.credit_store.balance(user_id),
    )
    if not has_start:
        text += "\n\n" + flow_copy.msg("vid_frm_send_photo_start")
        st["vawait"] = "vfrm_start"
    elif not has_end:
        text += "\n\n" + flow_copy.msg("vid_frm_send_photo_end")
        st["vawait"] = "vfrm_end"
    else:
        caption = st.get("vcaption_prompt")
        if caption:
            text += "\n\n" + flow_copy.msg(
                "vid_frm_ready_next_with_caption", prompt=html.escape(caption[:300])
            )
        else:
            text += "\n\n" + flow_copy.msg("vid_frm_ready_next")
        st["vawait"] = None
    kb = frames_kb(has_start, has_end, vfmt, vcount, model_id)
    if edit:
        await deps.vid_edit(message, text, kb, user_id, parse_mode="HTML")
    else:
        sent = await message.answer(text, reply_markup=kb, parse_mode="HTML")
        st["vmsg_id"] = sent.message_id


def video_settings_text(user_id: int, *, deps: VideoSettingsScreensDeps) -> str:
    st = deps.wizard_state[user_id]
    model_id = st.get("vmodel", "")
    vfmt = st.get("vfmt", deps.vid_default_fmt)
    vcount = st.get("vcount", deps.vid_default_count)
    model_name = L(f"vid_model_name:{model_id}") if model_id else model_id
    return flow_copy.msg(
        "vid_settings_screen",
        model=model_name,
        fmt=deps.vid_fmt_names.get(vfmt, vfmt),
        count=vcount,
        price=video_price(model_id, vcount),
        credits=deps.credit_store.balance(user_id),
    )


async def show_video_family(
    message: types.Message, *, user_id: int, edit: bool,
    deps: VideoSettingsScreensDeps,
) -> None:
    deps.vid_clear(user_id)
    st = deps.wizard_state[user_id]
    st.pop("vretry", None)
    st["vstep"] = "vfam"
    st.setdefault("vfmt", deps.vid_default_fmt)
    st.setdefault("vcount", deps.vid_default_count)
    vlast = st.get("vlast")
    if vlast:
        st["vfmt"] = deps.aspect_to_vfmt(vlast.get("aspect", "landscape"))
        st["vcount"] = vlast.get("count", deps.vid_default_count)
    text = flow_copy.msg("vid_family_screen")
    kb = video_family_kb()
    if edit:
        await deps.vid_edit(message, text, kb, user_id)
    else:
        sent = await message.answer(text, reply_markup=kb)
        st["vmsg_id"] = sent.message_id


async def show_video_variant(
    message: types.Message, *, user_id: int, deps: VideoSettingsScreensDeps
) -> None:
    st = deps.wizard_state[user_id]
    family = st.get("vfamily", "")
    st["vstep"] = "vmodel"
    text = flow_copy.msg("vid_variant_screen", family=family)
    kb = video_variant_kb(family, st.get("vmodel"))
    await deps.vid_edit(message, text, kb, user_id)


async def show_video_settings(
    message: types.Message, *, user_id: int, deps: VideoSettingsScreensDeps
) -> None:
    st = deps.wizard_state[user_id]
    st["vstep"] = "vsettings"
    st.setdefault("vfmt", deps.vid_default_fmt)
    st.setdefault("vcount", deps.vid_default_count)
    text = video_settings_text(user_id, deps=deps)
    kb = video_wizard_kb(st["vfmt"], st["vcount"])
    await deps.vid_edit(message, text, kb, user_id, parse_mode="HTML")


def mp_confirm_screen(
    user_id: int, *, deps: MarketplaceScreensDeps
) -> tuple[str, types.InlineKeyboardMarkup]:
    st = deps.workspace(user_id)
    plat = st.get("mp_platform", "wb")
    plat_name = _MP_PLAT_NAMES.get(plat, plat)
    format_label = _mp_platform_format_label(plat)
    kind = st.get("mp_pending_kind", "photo")
    try:
        brand = deps.brand_kit(user_id)
    except Exception:
        brand = ""
    try:
        niche = deps.niche_label(user_id)
    except Exception:
        niche = ""
    caption = (st.get("mp_pending_caption") or "").strip()
    try:
        credits = deps.credit_store.balance(user_id)
    except Exception:
        credits = 0
    if kind == "series":
        count = st.get("mp_series_count", 3)
        if count not in _MP_SERIES_COUNTS:
            count = 3
        price = action_price("mp_series", count)
        job_label = f"серия · {count} {_slides_word(count)}"
    else:
        job = st.get("mp_preset", "whitebg")
        price = action_price("edit")
        job_label = _MP_JOB_LABELS.get(job, job)
    lines = [
        f"🛒 <b>{html.escape(plat_name)}</b> · {html.escape(job_label)}",
        "📎 Фото товара принято.",
        "",
        f"🎨 Бренд-кит: {html.escape(brand) if brand else '— (не задан)'}",
        f"🏷️ Ниша: {html.escape(niche) if niche else '— (не задана)'}",
    ]
    if caption:
        lines.append(f"📝 Пожелание: {html.escape(caption[:150])}")
    lines.append(f"📐 Формат: {html.escape(format_label)}")
    lines.append(f"💰 Стоимость: <b>{price} кр</b> · Баланс: {credits} кр")
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=f"✅ Создать · {price} кр", callback_data="mp:create")],
        [types.InlineKeyboardButton(text="✏️ Сменить задачу", callback_data="m:mp")],
        [_menu_button("cancel", "m:menu")],
    ])
    return "\n".join(lines), kb


def mp_sku_projects(
    user_id: int, limit: int = 12, *, deps: MarketplaceScreensDeps
) -> list[dict]:
    projects = deps.metrics.list_seller_sku_projects(user_id, limit=limit)
    deps.workspace(user_id)["mp_sku_project_choices"] = [
        str(p.get("sku") or "") for p in projects
    ]
    return projects


def mp_sku_projects_text(
    user_id: int, projects: list[dict] | None = None, *,
    deps: MarketplaceScreensDeps,
) -> str:
    projects = mp_sku_projects(user_id, deps=deps) if projects is None else projects
    if not projects:
        return (
            "📦 <b>Мои товары (SKU)</b>\n\n"
            "Пока здесь пусто. Создай SKU сейчас или добавь результат кнопкой "
            "«➕ В серию SKU» под готовой карточкой."
        )
    lines = [
        "📦 <b>Мои товары (SKU)</b>\n\n"
        "Нажми на SKU ниже, чтобы открыть рабочее пространство."
    ]
    for item in projects:
        sku = html.escape(str(item.get("sku") or "SKU"))
        count = int(item.get("items") or 0)
        platform = item.get("platform") or ""
        platform_line = f" · {html.escape(platform)}" if platform else ""
        updated = (item.get("updated_at") or "")[:16]
        update_line = f", обновлено {updated}" if updated else ""
        lines.append(
            f"• <b>{sku}</b>{platform_line}: {count} "
            f"{_slides_word(count)}{update_line}"
        )
    lines.append("\nДобавляй текущую или последнюю карточку внутри нужного SKU.")
    return "\n".join(lines)


def mp_sku_projects_kb(
    user_id: int, projects: list[dict] | None = None, *,
    deps: MarketplaceScreensDeps,
) -> types.InlineKeyboardMarkup:
    B = types.InlineKeyboardButton
    projects = mp_sku_projects(user_id, deps=deps) if projects is None else projects
    deps.workspace(user_id)["mp_sku_project_choices"] = [
        str(p.get("sku") or "") for p in projects
    ]
    rows: list[list[types.InlineKeyboardButton]] = []
    for idx, item in enumerate(projects):
        sku = str(item.get("sku") or "SKU")
        count = int(item.get("items") or 0)
        rows.append([
            B(
                text=f"📦 {sku[:42]} · {count} {_slides_word(count)}",
                callback_data=f"mp:sku:open:{idx}",
            )
        ])
    rows.append([B(text="➕ Новый SKU", callback_data="mp:sku:new")])
    rows.append([B(text="◀️ Маркетплейсы", callback_data="m:mp")])
    rows.append([_menu_button("menu", "m:menu")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


async def show_sku_projects(
    message: types.Message, *, user_id: int, edit: bool,
    deps: MarketplaceScreensDeps,
) -> None:
    projects = mp_sku_projects(user_id, deps=deps)
    text = mp_sku_projects_text(user_id, projects, deps=deps)
    kb = mp_sku_projects_kb(user_id, projects, deps=deps)
    if edit:
        await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        deps.stamp_message(user_id, message)
    else:
        sent = await message.answer(text, reply_markup=kb, parse_mode="HTML")
        deps.stamp_message(user_id, sent)


async def show_gallery(message: types.Message, *, user_id: int, deps: ProfileScreensDeps) -> None:
    """Show the user's latest gallery images."""

    rows = deps.metrics.get_gallery(user_id, limit=20)
    back_kb = types.InlineKeyboardMarkup(inline_keyboard=[[_menu_button("menu", "m:menu")]])
    if not rows:
        await message.answer(
            flow_copy.msg("gallery_empty"), reply_markup=back_kb, parse_mode="HTML"
        )
        return

    header_sent = False
    for chunk_start in range(0, len(rows), 10):
        chunk = rows[chunk_start:chunk_start + 10]
        media_group = [types.InputMediaPhoto(media=r["file_id"]) for r in chunk]
        if not header_sent:
            media_group[0] = types.InputMediaPhoto(
                media=chunk[0]["file_id"],
                caption=flow_copy.msg("gallery_header", total=len(rows)),
            )
            header_sent = True
        try:
            await message.answer_media_group(media=media_group)
        except Exception as exc:
            deps.log.warning(f"Gallery send error: {exc}")

    bottom_rows: list[list[types.InlineKeyboardButton]] = []
    latest_token = rows[0].get("token") if rows else None
    if latest_token and deps.image_registry.get(latest_token):
        realup_price = action_price("realup")
        label_up = (
            f"\U0001f50d \u0423\u043b\u0443\u0447\u0448\u0438\u0442\u044c "
            f"\u043f\u043e\u0441\u043b\u0435\u0434\u043d\u044e\u044e \u00b7 {realup_price} "
            "\u043a\u0440"
            if realup_price
            else "\U0001f50d \u0423\u043b\u0443\u0447\u0448\u0438\u0442\u044c "
            "\u043f\u043e\u0441\u043b\u0435\u0434\u043d\u044e\u044e"
        )
        bottom_rows.append([types.InlineKeyboardButton(
            text=label_up, callback_data=action_callback_data("realup", latest_token)
        )])
    bottom_rows.append([_menu_button("menu", "m:menu")])
    await message.answer(
        "\u2b06\ufe0f \u0412\u043e\u0442 \u0442\u0432\u043e\u0438 "
        "\u043f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0435 "
        "\u0440\u0430\u0431\u043e\u0442\u044b",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=bottom_rows),
    )


async def show_prompt_history(message: types.Message, *, user_id: int, deps: ProfileScreensDeps) -> None:
    """Show prompt history with buttons to reuse prompts."""

    if deps.is_seller():
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [_menu_button("gallery", "m:gallery")],
            [_menu_button("menu", "m:menu")],
        ])
        await message.answer(deps.seller_history_text(user_id), reply_markup=kb, parse_mode="HTML")
        return

    prompts = deps.metrics.get_prompt_history(user_id, limit=10)
    back_kb = types.InlineKeyboardMarkup(inline_keyboard=[[_menu_button("menu", "m:menu")]])
    if not prompts:
        await message.answer(flow_copy.msg("history_empty"), reply_markup=back_kb, parse_mode="HTML")
        return

    B = types.InlineKeyboardButton
    lines = [flow_copy.msg("history_title", n=len(prompts))]
    rows: list[list[types.InlineKeyboardButton]] = []
    nums = [
        "1\ufe0f\u20e3", "2\ufe0f\u20e3", "3\ufe0f\u20e3", "4\ufe0f\u20e3",
        "5\ufe0f\u20e3", "6\ufe0f\u20e3", "7\ufe0f\u20e3", "8\ufe0f\u20e3",
        "9\ufe0f\u20e3", "\U0001f51f",
    ]
    for i, p in enumerate(prompts):
        emoji = nums[i] if i < len(nums) else f"{i + 1}."
        lines.append(f"{emoji} {html.escape(p[:100])}")
        rows.append([B(text=f"{emoji} \u0418\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u044c", callback_data=f"w:hist:{i}")])
    rows.append([_menu_button("menu", "m:menu")])
    deps.workspace(user_id)["_hist_cache"] = prompts
    await message.answer(
        "\n".join(lines),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )


async def show_support_menu(
    message: types.Message, *, user_id: int, edit: bool, deps: ProfileScreensDeps
) -> None:
    deps.workspace(user_id).pop("support_await", None)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=L("support_new"), callback_data="m:support:new")],
        [types.InlineKeyboardButton(text=L("support_my"), callback_data="m:support:my")],
        [_menu_button("menu", "m:menu")],
    ])
    if edit:
        await message.edit_text(flow_copy.msg("support_menu"), reply_markup=kb)
    else:
        await message.answer(flow_copy.msg("support_menu"), reply_markup=kb)


async def show_profile_screen(
    message: types.Message, *, user_id: int, edit: bool, deps: ProfileScreensDeps
) -> None:
    """Show profile navigation."""

    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [_menu_button("gallery", "m:gallery")],
        [_menu_button("history", "m:history")],
        [_menu_button("support", "m:support")],
        [_menu_button("menu", "m:menu")],
    ])
    text = (
        "\U0001f464 \u041c\u043e\u0439 \u043f\u0440\u043e\u0444\u0438\u043b\u044c\n\n"
        "\u0422\u0432\u043e\u0438 \u0440\u0430\u0431\u043e\u0442\u044b \u0438 "
        "\u0438\u0441\u0442\u043e\u0440\u0438\u044f \u0437\u0430\u043f\u0440\u043e\u0441\u043e\u0432 "
        "\u2014 \u0432\u0441\u0451 \u0437\u0434\u0435\u0441\u044c."
    )
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


async def show_my_tickets(
    message: types.Message, *, user_id: int, edit: bool, deps: ProfileScreensDeps
) -> None:
    tickets = deps.metrics.get_user_tickets(user_id)
    back_kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [_menu_button("support", "m:support")],
        [_menu_button("menu", "m:menu")],
    ])
    if not tickets:
        text = flow_copy.msg("support_no_tickets")
    else:
        items = "\n\n".join(
            flow_copy.msg(
                "support_ticket_item",
                id=t["id"],
                status_emoji="\u2705" if t["status"] == "replied" else "\u23f3",
                status_label=(
                    "\u041e\u0442\u0432\u0435\u0447\u0435\u043d"
                    if t["status"] == "replied"
                    else "\u041e\u0436\u0438\u0434\u0430\u0435\u0442"
                ),
                question=html.escape(t["message_text"][:80]),
                reply_line=(f"\u21aa\ufe0f {html.escape(t['reply_text'][:120])}\n" if t["reply_text"] else ""),
                date=(t.get("created_at") or "")[:16],
            )
            for t in tickets
        )
        text = flow_copy.msg("support_tickets_list", items=items)
    if edit:
        await message.edit_text(text, reply_markup=back_kb)
    else:
        await message.answer(text, reply_markup=back_kb)
