"""Main menu callback router (Phase 6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, MutableMapping

from aiogram import F, Router, types

import flow_copy
from channels.telegram.keyboards import _menu_button


@dataclass(frozen=True)
class MenuDeps:
    """Injected state/render/payment helpers for ``m:`` callbacks."""

    workspace: Callable[[int], MutableMapping[str, Any]]
    pending_edits: MutableMapping[int, Any]
    pending_photo_routes: MutableMapping[int, Any]
    admin_ids: set[int]
    upsert_user: Callable[..., Any]
    log_event: Callable[..., Any]
    reset_image_flow: Callable[..., Any]
    clear_image_flow_keys: Callable[[MutableMapping[str, Any]], Any]
    show_prompt_picker: Callable[..., Awaitable[Any]]
    show_video_prompt_input: Callable[..., Awaitable[Any]]
    show_animate_photo_input: Callable[..., Awaitable[Any]]
    mp_jobs_text: Callable[[str], str]
    mp_jobs_kb: Callable[[str], types.InlineKeyboardMarkup]
    mp_stamp_message: Callable[[int, types.Message], Any]
    show_ideas_root: Callable[..., Awaitable[Any]]
    repeat_last: Callable[..., Awaitable[Any]]
    show_balance: Callable[..., Awaitable[Any]]
    topup_copy: Callable[[str], str]
    topup_kb: Callable[..., types.InlineKeyboardMarkup]
    topup_stars_kb: Callable[..., types.InlineKeyboardMarkup]
    topup_robo_kb: Callable[..., types.InlineKeyboardMarkup]
    robokassa_configured: Callable[[], bool]
    start_topup: Callable[..., Awaitable[Any]]
    start_robokassa_topup: Callable[..., Awaitable[Any]]
    show_help_screen: Callable[..., Awaitable[Any]]
    show_referral_screen: Callable[..., Awaitable[Any]]
    show_main_menu: Callable[..., Awaitable[Any]]
    show_profile_screen: Callable[..., Awaitable[Any]]
    show_gallery: Callable[..., Awaitable[Any]]
    show_prompt_history: Callable[..., Awaitable[Any]]
    show_support_menu: Callable[..., Awaitable[Any]]
    show_my_tickets: Callable[..., Awaitable[Any]]


def create_router(deps: MenuDeps) -> Router:
    """Build the ``m:`` main menu callback router."""

    router = Router(name="tg-menu")

    @router.callback_query(F.data.startswith("m:"))
    async def on_menu_action(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        deps.upsert_user(
            user_id,
            username=getattr(callback.from_user, "username", None),
            first_name=getattr(callback.from_user, "first_name", None),
        )
        data = callback.data or ""
        msg = callback.message

        if data == "m:gen":
            await callback.answer()
            deps.reset_image_flow(user_id)
            await deps.show_prompt_picker(msg, user_id=user_id, edit=True)
        elif data == "m:vid":
            await callback.answer()
            deps.pending_edits.pop(user_id, None)
            deps.pending_photo_routes.pop(user_id, None)
            await deps.show_video_prompt_input(msg, user_id=user_id, edit=True)
        elif data == "m:animate":
            await callback.answer()
            deps.pending_edits.pop(user_id, None)
            st = deps.workspace(user_id)
            deps.clear_image_flow_keys(st)
            await deps.show_animate_photo_input(msg, user_id=user_id, edit=True)
        elif data == "m:mp":
            await callback.answer()
            deps.reset_image_flow(user_id)
            deps.workspace(user_id).setdefault("mp_platform", "wb")
            platform = deps.workspace(user_id).get("mp_platform", "wb")
            await msg.edit_text(
                deps.mp_jobs_text(platform),
                reply_markup=deps.mp_jobs_kb(platform),
                parse_mode="HTML",
            )
            deps.mp_stamp_message(user_id, msg)
        elif data == "m:ideas":
            await callback.answer()
            deps.reset_image_flow(user_id)
            await deps.show_ideas_root(msg, user_id=user_id, edit=True)
        elif data == "m:repeat":
            await callback.answer("Повторяю 🔁")
            await deps.repeat_last(callback, user_id)
        elif data == "m:balance":
            await callback.answer()
            await deps.show_balance(msg, user_id=user_id, edit=True)
        elif data == "m:topup":
            await callback.answer()
            deps.log_event("topup_opened", user_id=user_id, source="menu")
            await msg.edit_text(
                deps.topup_copy("topup_screen"),
                reply_markup=deps.topup_kb(is_admin=user_id in deps.admin_ids),
            )
        elif data == "m:pay:stars":
            await callback.answer()
            await msg.edit_text(
                deps.topup_copy("topup_stars_screen"),
                reply_markup=deps.topup_stars_kb(is_admin=user_id in deps.admin_ids),
            )
        elif data == "m:pay:robo":
            if not deps.robokassa_configured():
                await callback.answer("Оплата по СБП/карте пока недоступна", show_alert=True)
                return
            await callback.answer()
            await msg.edit_text(
                deps.topup_copy("topup_robo_screen"),
                reply_markup=deps.topup_robo_kb(is_admin=user_id in deps.admin_ids),
            )
        elif data.startswith("m:pack:"):
            await deps.start_topup(callback, user_id, data.split(":", 2)[2])
        elif data.startswith("m:robo:"):
            await deps.start_robokassa_topup(callback, user_id, data.split(":", 2)[2])
        elif data == "m:help":
            await callback.answer()
            await deps.show_help_screen(msg, edit=True)
        elif data == "m:invite":
            await callback.answer()
            await deps.show_referral_screen(msg, user_id=user_id, edit=True)
        elif data == "m:myphoto":
            await callback.answer()
            deps.reset_image_flow(user_id, keep_last=False)
            deps.workspace(user_id)["await"] = "photo"
            await msg.edit_text(flow_copy.msg("ask_photo"))
        elif data == "m:menu":
            await callback.answer()
            deps.pending_edits.pop(user_id, None)
            deps.pending_photo_routes.pop(user_id, None)
            deps.workspace(user_id)["await"] = None
            await deps.show_main_menu(msg, user_id=user_id, edit=True)
        elif data == "m:profile":
            await callback.answer()
            await deps.show_profile_screen(msg, user_id=user_id, edit=True)
        elif data == "m:gallery":
            await callback.answer()
            await deps.show_gallery(msg, user_id=user_id)
        elif data == "m:history":
            await callback.answer()
            await deps.show_prompt_history(msg, user_id=user_id)
        elif data == "m:support":
            await callback.answer()
            await deps.show_support_menu(msg, user_id=user_id, edit=True)
        elif data == "m:support:new":
            await callback.answer()
            st = deps.workspace(user_id)
            st["support_await"] = True
            cancel_kb = types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="◀️ Отмена", callback_data="m:support")],
                [_menu_button("menu", "m:menu")],
            ])
            await msg.edit_text(flow_copy.msg("support_ask"), reply_markup=cancel_kb)
        elif data == "m:support:my":
            await callback.answer()
            await deps.show_my_tickets(msg, user_id=user_id, edit=True)
        elif data.startswith("m:sreply:"):
            if user_id in deps.admin_ids:
                ticket_id = int(data.split(":", 2)[2])
                st = deps.workspace(user_id)
                st["admin_reply_ticket"] = ticket_id
                await callback.answer()
                await msg.reply(f"✏️ Введи ответ на тикет #{ticket_id}:")
            else:
                await callback.answer()
        else:
            await callback.answer()

    return router
