"""Video wizard callback router (Phase 6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, MutableMapping

from aiogram import F, Router, types

import flow_copy
from flow_core import clamp_num_videos, video_model_meta, video_price
from config.video import (
    VID_REF_DEFAULT_MODEL,
    _VID_OMNI_DURATIONS,
    _VID_QUICKSTART_MODEL,
    _VID_STYLES,
    _VID_VEO_QUALITY_CYCLE,
)
from channels.telegram.keyboards import L, _menu_button, _nwiz_styles_kb


@dataclass(frozen=True)
class VideoDeps:
    """Injected state, renderers, and generation helpers for ``v:`` callbacks."""

    workspace: Callable[[int], MutableMapping[str, Any]]
    vid_clear: Callable[[int], Any]
    show_main_menu: Callable[..., Awaitable[Any]]
    video_download: Callable[..., Awaitable[Any]]
    video_segment_download: Callable[..., Awaitable[Any]]
    video_edit_start: Callable[..., Awaitable[Any]]
    video_extend_start: Callable[..., Awaitable[Any]]
    video_repeat_last: Callable[..., Awaitable[Any]]
    show_new_video_wizard: Callable[..., Awaitable[Any]]
    nwiz_text: Callable[[int], str]
    vid_edit: Callable[..., Awaitable[Any]]
    nwiz_model: Callable[[dict], str]
    nwiz_price: Callable[[dict], int]
    video_generate_and_send: Callable[..., Awaitable[Any]]
    show_video_settings: Callable[..., Awaitable[Any]]
    show_video_variant: Callable[..., Awaitable[Any]]
    show_video_ingredients: Callable[..., Awaitable[Any]]
    show_video_frames: Callable[..., Awaitable[Any]]
    show_video_family: Callable[..., Awaitable[Any]]
    vid_rerender_settings: Callable[..., Awaitable[Any]]
    balance: Callable[[int], int]
    vid_frames_default_model: str
    vid_code_family: Mapping[str, str]
    vid_quickstart_family: str
    default_video_count: int


def create_router(deps: VideoDeps) -> Router:
    """Build the ``v:`` video wizard callback router."""

    router = Router(name="tg-video")

    @router.callback_query(F.data.startswith("v:"))
    async def on_video_action(callback: types.CallbackQuery):
        """Видео-визард: выбор семейства/модели/формата/кол-ва и запуск генерации."""
        user_id = callback.from_user.id
        data = callback.data or ""
        st = deps.workspace(user_id)
        msg = callback.message

        # Идёт генерация — блокируем любые нажатия визарда.
        if st.get("vstep") == "vgenerating" and data != "v:dl" and not data.startswith("v:dl:") and not data.startswith("v:dl_seg:"):
            await callback.answer(flow_copy.msg("vid_busy"), show_alert=True)
            return

        # Отмена → главное меню.
        if data == "v:cancel":
            await callback.answer("Отменено")
            deps.vid_clear(user_id)
            await deps.show_main_menu(msg, user_id=user_id, edit=True)
            return

        # Скачать готовое видео по токену.
        if data.startswith("v:dl:"):
            await deps.video_download(callback, user_id, data.split(":", 2)[2])
            return
        if data.startswith("v:dl_seg:"):
            await deps.video_segment_download(callback, user_id, data.split(":", 2)[2])
            return
        if data.startswith("v:edit:"):
            await deps.video_edit_start(callback, user_id, data.split(":", 2)[2])
            return
        if data.startswith("v:extend:"):
            await deps.video_extend_start(callback, user_id, data.split(":", 2)[2])
            return

        # Повторить последнюю генерацию.
        if data == "v:repeat":
            await callback.answer("Повторяю 🔁")
            await deps.video_repeat_last(callback, user_id)
            return

        # ── Новый prompt-first видео-wizard (v:n* callbacks) ─────────────────
        if data.startswith("v:n"):
            # Формат тоггл
            if data.startswith("v:nfmt:"):
                fmt = data.split(":", 2)[2]
                if fmt in ("land", "port"):
                    st["vfmt"] = fmt
                await callback.answer()
                await deps.show_new_video_wizard(msg, user_id=user_id, edit=True)
                return

            # Движок — ⚡ Быстро (Omni) / 💎 Качество (Veo)
            if data.startswith("v:neng:"):
                eng = data.split(":", 2)[2]
                if eng in ("omni", "veo"):
                    st["vengine"] = eng
                await callback.answer()
                await deps.show_new_video_wizard(msg, user_id=user_id, edit=True)
                return

            # Длительность (Omni)
            if data.startswith("v:ndur:"):
                try:
                    dur = int(data.split(":", 2)[2])
                except (ValueError, IndexError):
                    await callback.answer()
                    return
                if dur in _VID_OMNI_DURATIONS:
                    st["vdur"] = dur
                await callback.answer()
                await deps.show_new_video_wizard(msg, user_id=user_id, edit=True)
                return

            # Качество Veo — цикл по значениям
            if data.startswith("v:nqual:"):
                q = data.split(":", 2)[2]
                if q in _VID_VEO_QUALITY_CYCLE:
                    st["vquality"] = q
                await callback.answer()
                await deps.show_new_video_wizard(msg, user_id=user_id, edit=True)
                return

            # Стили: открыть экран выбора
            if data == "v:nstyle:screen":
                await callback.answer()
                text_styles = deps.nwiz_text(user_id)
                await deps.vid_edit(msg, text_styles, _nwiz_styles_kb(), user_id, parse_mode="HTML")
                return

            # Стили: выбрать стиль или вернуться назад
            if data.startswith("v:nstyle:"):
                key = data.split(":", 2)[2]
                if key == "back":
                    await callback.answer()
                    await deps.show_new_video_wizard(msg, user_id=user_id, edit=True)
                    return
                if key in _VID_STYLES:
                    st["vstyle"] = key
                    await callback.answer(_VID_STYLES[key][0])
                else:
                    await callback.answer()
                await deps.show_new_video_wizard(msg, user_id=user_id, edit=True)
                return

            # Убрать фотографию
            if data == "v:nremove_photo":
                st.pop("vphoto", None)
                st.pop("vphotos", None)
                st["vmode"] = "text"
                st["vmodel"] = deps.nwiz_model(st)
                await callback.answer("Фото удалено")
                await deps.show_new_video_wizard(msg, user_id=user_id, edit=True)
                return

            # Изменить промпт — вернуться к вводу описания БЕЗ сброса состояния.
            # show_video_prompt_input делает _vid_clear (стирает vphoto и ставит
            # vmode="text"), из-за чего «Оживить фото» теряло приложенное фото и
            # генерировало видео без него. Вместо этого помечаем ожидание нового
            # промпта (vstep остаётся "vnewwiz") — следующий текст ловит хендлер
            # vnewwiz+vnchange и перерисовывает визард, сохраняя vphoto.
            if data == "v:nchange":
                await callback.answer()
                st["vawait"] = "vnchange"
                has_photo = bool(st.get("vphotos") or st.get("vphoto"))
                text = (
                    "🎬 <b>Оживить фото</b>\n\n"
                    "📎 Фото сохранено. Опишите заново, что должно происходить в видео."
                ) if has_photo else (
                    "🎬 <b>Создать видео</b>\n\n"
                    "Опишите, что должно происходить в видео. "
                    "Можно приложить фото — тогда оживим его в движение 📎"
                )
                kb = types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text=L("cancel"), callback_data="v:cancel")]
                ])
                await deps.vid_edit(msg, text, kb, user_id, parse_mode="HTML")
                return

            # Создать видео
            if data == "v:ngo":
                prompt = (st.get("vprompt") or "").strip()
                if not prompt:
                    await callback.answer("Сначала введите описание видео", show_alert=True)
                    return
                # Применяем стилевой суффикс к промпту
                style_key = st.get("vstyle", "")
                style_suffix = _VID_STYLES.get(style_key, ("", ""))[1]
                full_prompt = prompt + style_suffix
                # Если прикреплено фото — передаём как референс-изображение
                photo_sources = st.get("vphotos") or (
                    [st["vphoto"]] if st.get("vphoto") else []
                )
                if photo_sources:
                    st["ving_photos"] = photo_sources[:4]
                # Финальная синхронизация модели/режима
                st["vmodel"] = deps.nwiz_model(st)
                st["vmode"] = "ingredients" if photo_sources else "text"
                st["vcount"] = 1
                # Проверка баланса
                price = deps.nwiz_price(st)
                if deps.balance(user_id) < price:
                    kb_low = types.InlineKeyboardMarkup(inline_keyboard=[
                        [_menu_button("topup", "m:topup")], [_menu_button("menu", "m:menu")]
                    ])
                    await callback.answer()
                    await deps.vid_edit(
                        msg,
                        flow_copy.msg("low_balance", needed=price, have=deps.balance(user_id)),
                        kb_low, user_id, parse_mode="HTML",
                    )
                    return
                await callback.answer()
                await deps.video_generate_and_send(msg, full_prompt, user_id=user_id)
                return

            # Неизвестный v:n* — просто игнорируем
            await callback.answer()
            return
        # ── конец нового wizard callbacks ────────────────────────────────────

        # ⚡ Быстрый старт — omni-flash-4s, пропускаем пикер семейства и модели.
        if data == "v:quick":
            await callback.answer()
            st["vfamily"] = deps.vid_quickstart_family
            st["vmodel"] = _VID_QUICKSTART_MODEL
            await deps.show_video_settings(msg, user_id=user_id)
            return

        # Выбор семейства.
        if data.startswith("v:fam:"):
            code = data.split(":")[2]
            if code == "ing":
                await callback.answer()
                deps.vid_clear(user_id)
                st["vmode"] = "ingredients"
                st["vmodel"] = VID_REF_DEFAULT_MODEL
                st["vcount"] = 1
                await deps.show_video_ingredients(msg, user_id=user_id, edit=True)
                return
            if code == "frm":
                await callback.answer()
                deps.vid_clear(user_id)
                st["vmode"] = "frames"
                st["vmodel"] = deps.vid_frames_default_model
                st["vcount"] = 1
                await deps.show_video_frames(msg, user_id=user_id, edit=True)
                return
            family = deps.vid_code_family.get(code)
            if not family:
                await callback.answer()
                return
            st["vfamily"] = family
            st["vmodel"] = None
            await callback.answer()
            await deps.show_video_variant(msg, user_id=user_id)
            return

        # Ingredients actions.
        if data == "v:ing:done":
            photos = st.get("ving_photos") or []
            if len(photos) < 1:
                await callback.answer(flow_copy.msg("vid_ing_need_more"), show_alert=True)
                return
            model_id = st.get("vmodel") or VID_REF_DEFAULT_MODEL
            price = video_price(model_id, st.get("vcount", 1), "ingredients")
            if deps.balance(user_id) < price:
                kb = types.InlineKeyboardMarkup(inline_keyboard=[
                    [_menu_button("topup", "m:topup")], [_menu_button("menu", "m:menu")]
                ])
                await callback.answer()
                await msg.answer(
                    flow_copy.msg("low_balance", needed=price, have=deps.balance(user_id)),
                    reply_markup=kb,
                    parse_mode="HTML",
                )
                return
            # Подпись к фото уже задаёт описание — генерируем сразу.
            caption = st.pop("vcaption_prompt", None)
            if caption:
                st["vawait"] = None
                await callback.answer()
                await deps.video_generate_and_send(msg, caption, user_id=user_id)
                return
            st["vawait"] = "vprompt"
            st["vstep"] = "vprompt"
            await callback.answer()
            await msg.answer(flow_copy.msg("vid_ing_ask_prompt"))
            return

        if data == "v:ing:clear":
            await callback.answer()
            st["ving_photos"] = []
            await deps.show_video_ingredients(msg, user_id=user_id, edit=True)
            return

        # Frames actions.
        if data == "v:frm:go":
            if not (st.get("vfrm_start") and st.get("vfrm_end")):
                await callback.answer(flow_copy.msg("vid_frm_need_both"), show_alert=True)
                return
            model_id = st.get("vmodel") or deps.vid_frames_default_model
            price = video_price(model_id, st.get("vcount", 1), "frames")
            if deps.balance(user_id) < price:
                kb = types.InlineKeyboardMarkup(inline_keyboard=[
                    [_menu_button("topup", "m:topup")], [_menu_button("menu", "m:menu")]
                ])
                await callback.answer()
                await msg.answer(
                    flow_copy.msg("low_balance", needed=price, have=deps.balance(user_id)),
                    reply_markup=kb,
                    parse_mode="HTML",
                )
                return
            caption = st.pop("vcaption_prompt", None)
            if caption:
                st["vawait"] = None
                await callback.answer()
                await deps.video_generate_and_send(msg, caption, user_id=user_id)
                return
            st["vawait"] = "vprompt"
            st["vstep"] = "vprompt"
            await callback.answer()
            await msg.answer(flow_copy.msg("vid_frm_ask_prompt"))
            return

        if data == "v:frm:clear":
            await callback.answer()
            st.pop("vfrm_start", None)
            st.pop("vfrm_end", None)
            st.pop("vawait", None)
            await deps.show_video_frames(msg, user_id=user_id, edit=True)
            return

        # Назад к выбору семейства.
        if data == "v:back:fam":
            st["vmodel"] = None
            await callback.answer()
            await deps.show_video_family(msg, user_id=user_id, edit=True)
            return

        # Выбор конкретной модели → экран настроек.
        if data.startswith("v:model:"):
            model_id = data.split(":", 2)[2]
            if not video_model_meta(model_id):
                await callback.answer()
                return
            st["vmodel"] = model_id
            await callback.answer()
            await deps.show_video_settings(msg, user_id=user_id)
            return

        # Назад к выбору модели (в том же семействе).
        if data == "v:back:model":
            await callback.answer()
            await deps.show_video_variant(msg, user_id=user_id)
            return

        # Смена формата / количества на экране настроек (текст / кадры / ингредиенты).
        if data.startswith("v:fmt:"):
            st["vfmt"] = data.split(":")[2]
            await callback.answer()
            await deps.vid_rerender_settings(msg, user_id=user_id)
            return
        if data.startswith("v:cnt:"):
            st["vcount"] = clamp_num_videos(data.split(":")[2])
            await callback.answer()
            await deps.vid_rerender_settings(msg, user_id=user_id)
            return
        # Выбор модели (Veo Lite/Fast/Quality) в режимах Frames/Ingredients.
        if data.startswith("v:vmod:"):
            mid = data.split(":", 2)[2]
            if video_model_meta(mid):
                st["vmodel"] = mid
            await callback.answer()
            await deps.vid_rerender_settings(msg, user_id=user_id)
            return

        # Повтор после ошибки — снова просим промпт с теми же настройками.
        if data == "v:retry":
            snap = st.get("vretry")
            if not snap or not snap.get("vmodel"):
                await callback.answer(flow_copy.msg("vid_expired_wizard"), show_alert=True)
                await deps.show_main_menu(msg, user_id=user_id, edit=True)
                return
            # Восстанавливаем настройки и фото из снимка и повторяем тот же запрос.
            for k, v in snap.items():
                if k != "prompt" and v is not None:
                    st[k] = v
            await callback.answer()
            await deps.video_generate_and_send(msg, snap["prompt"], user_id=user_id)
            return

        if data == "v:retrynew":
            # «Изменить промпт и снова» (после модерации): восстанавливаем настройки и
            # фото из снимка, но НЕ генерим сразу — ждём новый промпт от пользователя.
            snap = st.get("vretry")
            if not snap or not snap.get("vmodel"):
                await callback.answer(flow_copy.msg("vid_expired_wizard"), show_alert=True)
                await deps.show_main_menu(msg, user_id=user_id, edit=True)
                return
            for k, v in snap.items():
                if k != "prompt" and v is not None:
                    st[k] = v
            st["vawait"] = "vretry_prompt"
            await callback.answer()
            has_ref = bool(st.get("ving_photos") or st.get("vfrm_start") or st.get("vfrm_end"))
            text = (
                "✏️ <b>Изменить запрос</b>\n\n"
                "Опиши по-другому, что должно происходить в видео"
                + (" — фото и настройки сохранены 📎." if has_ref else ".")
            )
            kb = types.InlineKeyboardMarkup(inline_keyboard=[[_menu_button("menu", "m:menu")]])
            await deps.vid_edit(msg, text, kb, user_id, parse_mode="HTML")
            return

        # Подтверждение → промпт или сразу генерация (если промпт уже есть).
        if data == "v:go":
            model_id = st.get("vmodel")
            if not model_id:
                await callback.answer(flow_copy.msg("vid_expired_wizard"), show_alert=True)
                await deps.show_main_menu(msg, user_id=user_id, edit=True)
                return
            # Проверка баланса до входа в ввод промпта.
            price = video_price(model_id, st.get("vcount", deps.default_video_count))
            if deps.balance(user_id) < price:
                kb = types.InlineKeyboardMarkup(inline_keyboard=[
                    [_menu_button("topup", "m:topup")], [_menu_button("menu", "m:menu")]
                ])
                await callback.answer()
                await deps.vid_edit(
                    msg,
                    flow_copy.msg("low_balance", needed=price, have=deps.balance(user_id)),
                    kb, user_id,
                    parse_mode="HTML",
                )
                return
            pending = st.get("vpending_prompt")
            if pending:
                st["vpending_prompt"] = None
                await callback.answer()
                await deps.video_generate_and_send(msg, pending, user_id=user_id)
                return
            st["vawait"] = "vprompt"
            st["vstep"] = "vprompt"
            await callback.answer()
            await msg.edit_text(flow_copy.msg("vid_ask_prompt"))
            return

        await callback.answer()

    return router
