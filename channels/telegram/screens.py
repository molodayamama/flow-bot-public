"""Telegram profile/gallery/support screen renderers."""

from __future__ import annotations

import html
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from typing import Any

from aiogram import types

import flow_copy
from channels.telegram.keyboards import L, _menu_button
from flow_core import action_callback_data, action_price


@dataclass(frozen=True)
class ProfileScreensDeps:
    """Injected state and stores for profile-area screens."""

    metrics: Any
    workspace: Callable[[int], MutableMapping[str, Any]]
    image_registry: Any
    seller_history_text: Callable[[int], str]
    is_seller: Callable[[], bool]
    log: Any


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
