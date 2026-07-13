"""Plain-text message input router (Phase 6)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import Any

from aiogram import F, Router, types

import flow_copy
from channels.telegram.keyboards import (
    L,
    _menu_button,
    _mp_back_kb,
    _mp_sku_open_kb,
    _templates_picker_kb,
    wizard_kb,
    _MP_PLAT_NAMES,
)
from channels.telegram.texts import (
    _MP_SERIES_COUNTS,
    _mp_photo_request_text,
    _mp_series_request_text,
    _mp_sku_open_text,
)


@dataclass(frozen=True)
class PlainTextDeps:
    """Injected state, rendering, generation, support, and seller helpers."""

    workspace: Callable[[int], MutableMapping[str, Any]]
    metrics: Any
    username: Callable[[types.Message], Any]
    reset_image_flow: Callable[..., Any]
    show_prompt_picker: Callable[..., Awaitable[Any]]
    pending_edits: MutableMapping[int, Any]
    pending_photo_routes: MutableMapping[int, Any]
    show_main_menu: Callable[..., Awaitable[Any]]
    is_balance_reply_text: Callable[[str], bool]
    show_balance: Callable[..., Awaitable[Any]]
    vid_clear: Callable[[int], Any]
    show_ideas_root: Callable[..., Awaitable[Any]]
    show_referral_screen: Callable[..., Awaitable[Any]]
    show_help_screen: Callable[..., Awaitable[Any]]
    show_video_prompt_input: Callable[..., Awaitable[Any]]
    tp_store_answer: Callable[..., Any]
    render_template_step: Callable[..., Awaitable[Any]]
    render_guided_step: Callable[..., Awaitable[Any]]
    video_edit_uploaded: Callable[..., Awaitable[Any]]
    video_registry: Any
    video_prompt_edit_and_send: Callable[..., Awaitable[Any]]
    video_extend_and_send: Callable[..., Awaitable[Any]]
    video_generate_and_send: Callable[..., Awaitable[Any]]
    animate_photo_scenario: Callable[[], Any]
    telegram_animate_photo_context: Callable[[types.Message, int], Any]
    show_new_video_wizard: Callable[..., Awaitable[Any]]
    video_plain_text_ready: Callable[[MutableMapping[str, Any]], bool]
    admin_ids: Any
    credit_store: Any
    send_owner_alert: Callable[..., Awaitable[Any]]
    pending_sku_payload: Callable[[int], Any]
    save_pending_sku_item: Callable[..., Awaitable[Any]]
    log: Any
    wizard_text: Callable[[int], str]
    default_count: int
    default_fmt: str
    default_image_model: str
    fmt_to_aspect: Callable[[str], str]
    aspect_to_fmt: Callable[[str], str]
    image_registry: Any
    run_i2i: Callable[..., Awaitable[Any]]
    show_edit_confirm: Callable[..., Awaitable[Any]]
    generate_and_send: Callable[..., Awaitable[Any]]
    config: Any
    show_wizard: Callable[..., Awaitable[Any]]


def create_router(deps: PlainTextDeps) -> Router:
    """Build the plain-text catch-all router."""

    router = Router(name="tg-plain-text")

    ADMIN_IDS = deps.admin_ids
    DEFAULT_COUNT = deps.default_count
    DEFAULT_FMT = deps.default_fmt
    DEFAULT_IMAGE_MODEL = deps.default_image_model
    _TelegramAnimatePhotoContext = deps.telegram_animate_photo_context
    _animate_photo_scenario = deps.animate_photo_scenario
    _aspect_to_fmt = deps.aspect_to_fmt
    _cfg = deps.config
    _fmt_to_aspect = deps.fmt_to_aspect
    _generate_and_send = deps.generate_and_send
    _is_balance_reply_text = deps.is_balance_reply_text
    _pending_sku_payload = deps.pending_sku_payload
    _render_guided_step = deps.render_guided_step
    _render_template_step = deps.render_template_step
    _reset_image_flow = deps.reset_image_flow
    _run_i2i = deps.run_i2i
    _save_pending_sku_item = deps.save_pending_sku_item
    _send_owner_alert = deps.send_owner_alert
    _show_help_screen = deps.show_help_screen
    _show_ideas_root = deps.show_ideas_root
    _show_referral_screen = deps.show_referral_screen
    _tp_store_answer = deps.tp_store_answer
    _username = deps.username
    _vid_clear = deps.vid_clear
    _video_edit_uploaded = deps.video_edit_uploaded
    _video_extend_and_send = deps.video_extend_and_send
    _video_generate_and_send = deps.video_generate_and_send
    _video_plain_text_ready = deps.video_plain_text_ready
    _video_prompt_edit_and_send = deps.video_prompt_edit_and_send
    _wizard_text = deps.wizard_text
    _ws = deps.workspace
    credit_store = deps.credit_store
    image_registry = deps.image_registry
    log = deps.log
    metrics = deps.metrics
    pending_edits = deps.pending_edits
    pending_photo_routes = deps.pending_photo_routes
    show_balance = deps.show_balance
    show_edit_confirm = deps.show_edit_confirm
    show_main_menu = deps.show_main_menu
    show_new_video_wizard = deps.show_new_video_wizard
    show_prompt_picker = deps.show_prompt_picker
    show_video_prompt_input = deps.show_video_prompt_input
    show_wizard = deps.show_wizard
    video_registry = deps.video_registry

    @router.message(F.text & ~F.text.startswith("/"))
    async def handle_plain_text(message: types.Message):
        """Текст без команды: кнопки нижнего меню, ответ визарду или прямая генерация."""
        user_id = message.from_user.id
        metrics.upsert_user(user_id, username=_username(message),
                            first_name=getattr(message.from_user, "first_name", None))
        text = message.text.strip()
        st = _ws(user_id)

        # Постоянная нижняя клавиатура: её нажатия приходят как обычный текст.
        if text == L("kb_gen"):
            _reset_image_flow(user_id)  # чистит и pending_edits (залипшее фото)
            await show_prompt_picker(message, user_id=user_id, edit=False)
            return
        if text == L("kb_menu"):
            pending_edits.pop(user_id, None)
            pending_photo_routes.pop(user_id, None)
            st["await"] = None
            await show_main_menu(message, user_id=user_id, ensure_kb=True)
            return
        if _is_balance_reply_text(text):
            await show_balance(message, user_id=user_id, edit=False)
            return
        if text == L("ideas"):
            _reset_image_flow(user_id)
            _vid_clear(user_id)
            await _show_ideas_root(message, user_id=user_id, edit=False)
            return
        if text == L("myphoto"):
            _reset_image_flow(user_id, keep_last=False)
            _ws(user_id)["await"] = "photo"
            await message.answer(flow_copy.msg("ask_photo"))
            return
        if text == L("invite"):
            await _show_referral_screen(message, user_id=user_id, edit=False)
            return
        if text == L("help"):
            await _show_help_screen(message, edit=False)
            return
        if text == L("kb_vid"):
            vlast = st.get("vlast")
            _vid_clear(user_id)
            pending_edits.pop(user_id, None)  # бросаем залипшее фото-правку при переходе в видео
            pending_photo_routes.pop(user_id, None)
            if vlast:
                st["vlast"] = vlast
            await show_video_prompt_input(message, user_id=user_id, edit=False)
            return

        # Свободный текстовый ответ в Q&A готового шаблона («Идеи и шаблоны»).
        if st.get("tp_await") == "text" and st.get("tp_tpl"):
            _tp_store_answer(st, text)
            await _render_template_step(message, user_id=user_id)
            return

        if st.get("tp_tpl"):
            st["ideas_extra_prompt"] = text
            await message.answer(flow_copy.msg("ideas_text_attached_template"))
            await _render_template_step(message, user_id=user_id)
            return

        if "gp_step" in st:
            st["gp_extra_prompt"] = text
            await message.answer(flow_copy.msg("ideas_text_attached_guided"))
            await _render_guided_step(message, user_id=user_id)
            return

        if st.get("ideas_mode") in ("root", "templates", "guided"):
            st["ideas_extra_prompt"] = text
            await message.answer(flow_copy.msg("ideas_text_attached_root"))
            if st.get("ideas_mode") == "templates":
                await message.answer(
                    flow_copy.msg("ideas_templates_title"),
                    reply_markup=_templates_picker_kb(),
                    parse_mode="HTML",
                )
            else:
                await _show_ideas_root(message, user_id=user_id, edit=False)
            return

        # Промпт-правка для загруженного пользователем видео.
        if st.get("vawait") == "vu_edit_prompt" and st.get("vu_source"):
            if len(text) < 3:
                await message.answer(flow_copy.msg("vid_prompt_too_short"))
                return
            await _video_edit_uploaded(message, text, user_id=user_id)
            return

        # Видео-правка ждёт инструкцию к уже готовому ролику.
        if st.get("vawait") == "vedit_prompt":
            token = st.get("vedit_token")
            ref = video_registry.get(token) if token else None
            if ref is None or ref.user_id != user_id:
                _vid_clear(user_id)
                await message.answer(flow_copy.msg("expired"))
                await show_main_menu(message, user_id=user_id)
                return
            await _video_prompt_edit_and_send(message, ref, text, user_id=user_id)
            return

        if st.get("vawait") == "vextend_prompt":
            token = st.get("vextend_token")
            ref = video_registry.get(token) if token else None
            if ref is None or ref.user_id != user_id:
                _vid_clear(user_id)
                await message.answer(flow_copy.msg("expired"))
                await show_main_menu(message, user_id=user_id)
                return
            await _video_extend_and_send(message, ref, text, user_id=user_id)
            return

        # Ретрай после модерации: пользователь прислал новый промпт — генерим с теми
        # же настройками/фото, восстановленными из снимка кнопкой «Изменить промпт».
        if st.get("vawait") == "vretry_prompt":
            st["vawait"] = None
            await _video_generate_and_send(message, text, user_id=user_id)
            return

        # «Оживить фото» from the main menu requires a photo. Text is saved as the
        # future scenario, but generation cannot proceed without an image reference.
        if st.get("vawait") == "vanimate_photo":
            await _animate_photo_scenario().remember_prompt_until_photo(
                _TelegramAnimatePhotoContext(message, user_id),
                text,
            )
            return

        # Новый wizard: пользователь ввёл описание (шаг 1).
        if st.get("vstep") == "vprompt_input":
            st["vprompt"] = text
            await show_new_video_wizard(message, user_id=user_id, edit=True)
            return

        # Новый wizard: пользователь отредактировал промпт на экране настроек.
        if st.get("vstep") == "vnewwiz" and st.get("vawait") == "vnchange":
            st["vawait"] = None
            st["vprompt"] = text
            await show_new_video_wizard(message, user_id=user_id, edit=False)
            return

        # Видео-визард ждёт промпт.
        if st.get("vawait") == "vprompt":
            if message.photo:
                await message.answer(flow_copy.msg("vid_text_only_hint"))
                return
            st["vawait"] = None
            await _video_generate_and_send(message, text, user_id=user_id)
            return

        # «Оживить фото» / видео-из-фото: фото(и) уже выбраны (через кнопку или
        # загрузку) — промпт из чата запускает генерацию сразу, как «Готово» + промпт.
        # Кнопка «Готово» остаётся опциональной. Проверяем ДО image-фолбэка, иначе
        # залипший image-визард перехватил бы текст и сгенерил картинки.
        # ВАЖНО: этот блок должен быть ВЫШЕ проверки vawait == "ving_photo", иначе
        # show_video_ingredients (которая всегда выставляет vawait=ving_photo) блокирует
        # текстовый промпт даже когда фото уже добавлены («Оживить фото» не работает).
        if st.get("vmode") == "ingredients" and (st.get("ving_photos") or []):
            await _animate_photo_scenario().generate_from_ready_references(
                _TelegramAnimatePhotoContext(message, user_id),
                text,
            )
            return

        if st.get("vawait") == "ving_photo":
            await message.answer(flow_copy.msg("vid_ing_send_photo"))
            return

        if st.get("vawait") == "vfrm_start":
            await message.answer(flow_copy.msg("vid_frm_send_photo_start"))
            return

        if st.get("vawait") == "vfrm_end":
            await message.answer(flow_copy.msg("vid_frm_send_photo_end"))
            return

        # Видео по двум кадрам: оба кадра загружены — промпт из чата запускает генерацию.
        if st.get("vmode") == "frames" and st.get("vfrm_start") and st.get("vfrm_end"):
            st["vawait"] = None
            await _video_generate_and_send(message, text, user_id=user_id)
            return

        if _video_plain_text_ready(st):
            st["vawait"] = None
            await _video_generate_and_send(message, text, user_id=user_id)
            return

        awaiting = st.get("await")

        # ─── Поддержка: пользователь вводит сообщение для нового тикета ───────────
        if st.get("support_await"):
            st.pop("support_await", None)
            support_kind = st.pop("support_kind", "support")
            plat = st.get("mp_platform")
            ticket_text = text
            admin_title = "🎫 Тикет"
            if support_kind == "mp_done4you":
                platform_name = _MP_PLAT_NAMES.get(plat or "", plat or "не выбрана")
                ticket_text = f"🙌 Заявка под ключ\nПлощадка: {platform_name}\n\n{text}"
                admin_title = "🙌 Заявка под ключ"
                metrics.log_event("mp_done4you_submitted", user_id=user_id, source=plat or "seller")
            ticket_id = metrics.create_ticket(user_id, username=_username(message), text=ticket_text)
            # Пересылаем администратору
            admin_id = ADMIN_IDS[0] if ADMIN_IDS else None
            if admin_id:
                reply_btn = types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text=f"📝 Ответить #{ticket_id}", callback_data=f"m:sreply:{ticket_id}")]
                ])
                try:
                    fwd = await message.bot.send_message(
                        admin_id,
                        f"{admin_title} #{ticket_id} от @{_username(message) or user_id}:\n\n{ticket_text}",
                        reply_markup=reply_btn,
                    )
                    metrics.set_ticket_admin_msg(ticket_id, fwd.message_id)
                except Exception as exc:
                    log.warning(
                        "Не удалось переслать тикет #%s админу: %s",
                        ticket_id,
                        exc.__class__.__name__,
                    )
            back_kb = types.InlineKeyboardMarkup(inline_keyboard=[[_menu_button("menu", "m:menu")]])
            await message.answer(flow_copy.msg("support_submitted", ticket_id=ticket_id), reply_markup=back_kb)
            return

        # ─── Ответ администратора на тикет ──────────────────────────────────────
        if user_id in ADMIN_IDS and st.get("admin_reply_ticket"):
            ticket_id = st.pop("admin_reply_ticket")
            ticket_row = metrics.reply_ticket(ticket_id, reply_text=text)
            if ticket_row:
                try:
                    await message.bot.send_message(
                        ticket_row["user_id"],
                        flow_copy.msg("support_reply", ticket_id=ticket_id, reply=text),
                    )
                    await message.answer(f"✅ Ответ на тикет #{ticket_id} отправлен пользователю.")
                except Exception as exc:
                    await message.answer(f"⚠️ Ответ записан, но не доставлен: {exc}")
            else:
                await message.answer(f"⚠️ Тикет #{ticket_id} не найден.")
            return

        # Промокод: ждём ввода кода после /promo без аргумента.
        if awaiting == "promo":
            st["await"] = None
            code_raw = text.strip()
            credits_got = metrics.redeem_promo(code_raw, user_id)
            if credits_got is None:
                await message.answer(flow_copy.msg("promo_invalid"))
            else:
                balance = credit_store.add(user_id, credits_got)
                metrics.log_event("promo_redeemed", user_id=user_id, payload={"code": code_raw.upper(), "credits": credits_got})
                uname = _username(message)
                uname_str = f"@{uname}" if uname else f"id {user_id}"
                asyncio.create_task(_send_owner_alert(
                    f"🎟 <b>Промокод активирован</b>\n"
                    f"Юзер: {uname_str}\n"
                    f"Код: <code>{code_raw.upper()}</code>  +{credits_got} кр."
                ))
                await message.answer(
                    flow_copy.msg("promo_success", credits=credits_got, balance=balance),
                    parse_mode="HTML",
                )
            return

        # Photo-edit entry is waiting for an upload; plain text must not open the image wizard.
        if awaiting == "photo":
            await message.answer(flow_copy.msg("ask_photo"))
            return
        if awaiting == "mp_photo":
            await message.answer(
                _mp_photo_request_text(st.get("mp_platform", "wb"), st.get("mp_preset", "whitebg")),
                reply_markup=_mp_back_kb(),
                parse_mode="HTML",
            )
            return
        if awaiting == "mp_series_photo":
            count = st.get("mp_series_count", 3)
            try:
                count = int(count)
            except (TypeError, ValueError):
                count = 3
            if count not in _MP_SERIES_COUNTS:
                count = 3
            await message.answer(
                _mp_series_request_text(st.get("mp_platform", "wb"), count),
                reply_markup=_mp_back_kb(),
                parse_mode="HTML",
            )
            return
        if awaiting == "mp_sku_name":
            sku = text.strip()
            if len(sku) < 2:
                await message.answer("📦 Название SKU слишком короткое. Пришли артикул или название товара.")
                return
            if _pending_sku_payload(user_id):
                await _save_pending_sku_item(message, user_id, sku)
                return
            ok = metrics.create_seller_sku_project(user_id, sku, platform=st.get("mp_platform"))
            st["await"] = None
            if not ok:
                await message.answer("Не удалось создать SKU. Проверь название и попробуй ещё раз.")
                return
            st["mp_sku_open"] = sku
            metrics.log_event("mp_sku_created", user_id=user_id, source=str(st.get("mp_platform") or "seller"))
            await message.answer(
                _mp_sku_open_text(user_id, sku),
                reply_markup=_mp_sku_open_kb(),
                parse_mode="HTML",
            )
            return
        if awaiting == "mp_sku_rename":
            new_sku = text.strip()
            old_sku = str(st.get("mp_sku_open") or "").strip()
            if len(new_sku) < 2:
                await message.answer("📦 Новое название слишком короткое. Пришли артикул или название товара.")
                return
            if not old_sku:
                st["await"] = None
                await message.answer("Сначала открой SKU в разделе «Мои товары».")
                return
            ok = metrics.rename_seller_sku_project(user_id, old_sku, new_sku)
            st["await"] = None
            if not ok:
                await message.answer("Не удалось переименовать SKU. Попробуй ещё раз позже.")
                return
            st["mp_sku_open"] = new_sku
            metrics.log_event("mp_sku_renamed", user_id=user_id, source="seller")
            await message.answer(
                _mp_sku_open_text(user_id, new_sku),
                reply_markup=_mp_sku_open_kb(),
                parse_mode="HTML",
            )
            return
        if awaiting == "mp_brandkit":
            brand = text.strip()
            if len(brand) < 5:
                await message.answer("🎨 Опиши бренд-кит чуть подробнее: цвета, стиль и что важно сохранить.")
                return
            ok = metrics.save_seller_profile(user_id, brand_kit=brand)
            st["await"] = None
            metrics.log_event("mp_brandkit_saved", user_id=user_id, source="seller")
            if ok:
                await message.answer(
                    "🎨 Бренд-кит сохранён. Теперь seller-карточки и серии будут учитывать этот стиль.",
                    reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                        [types.InlineKeyboardButton(text="🛒 Маркетплейсы", callback_data="m:mp")],
                        [_menu_button("menu", "m:menu")],
                    ]),
                )
            else:
                await message.answer("Не удалось сохранить бренд-кит. Попробуй ещё раз позже.")
            return

        # Ждём промпт генерации из визарда.
        if awaiting == "prompt":
            st["await"] = None
            count = st.get("count", DEFAULT_COUNT)
            fmt = st.get("fmt", DEFAULT_FMT)
            await _generate_and_send(
                message, text, num_images=count,
                aspect_ratio=_fmt_to_aspect(fmt), actor_id=user_id,
                image_model=st.get("imodel", DEFAULT_IMAGE_MODEL),
            )
            return

        # Ждём текст правки/вариаций для конкретной картинки.
        if awaiting in ("edit", "revary"):
            token = pending_edits.get(user_id)
            ref = image_registry.get(token) if token else None
            if ref is not None and ref.user_id == user_id:
                if awaiting == "revary":
                    ok = await _run_i2i(
                        message, ref, text, num_images=2, emoji="🎲",
                        fail_text=flow_copy.msg("nothing_returned"),
                    )
                    if ok:
                        st["await"] = None
                        pending_edits.pop(user_id, None)
                    return
                # Edit: show a confirm screen so the user can улучшить запрос (agent)
                # or apply it as-is — instead of generating immediately.
                st["edit_instruction"] = text
                st["await"] = "edit_confirm"
                await show_edit_confirm(message, user_id=user_id, edit=False)
                return
            st["await"] = None
            pending_edits.pop(user_id, None)
            await message.answer(flow_copy.msg("expired"))
            await show_main_menu(message, user_id=user_id)
            return

        # На экране подтверждения правки новый текст = новый запрос правки.
        if awaiting == "edit_confirm":
            st["edit_instruction"] = text
            st.pop("ag_variants", None)
            await show_edit_confirm(message, user_id=user_id, edit=False)
            return

        # ВАЖНО: раньше тут был «старый путь», который редактировал pending_edits-фото
        # даже без awaiting=="edit". Из-за этого после «фото → меню → генерация» промпт
        # уходил на правку залипшего фото. Убрано намеренно: правка идёт ТОЛЬКО при
        # awaiting in ("edit","revary") выше; навигация по меню чистит pending_edits.

        # Визард уже открыт и пользователь выбрал настройки — промпт из чата запускает
        # генерацию сразу, с текущими количеством/форматом/моделью (без «Сгенерировать»).
        if st.get("step") == "wizard":
            count = st.get("count", DEFAULT_COUNT)
            fmt = st.get("fmt", DEFAULT_FMT)
            imodel = st.get("imodel", DEFAULT_IMAGE_MODEL)
            st["step"] = None  # экран отработал — случайный текст потом не сгенерит повторно
            st.pop("pending_prompt", None)
            await _generate_and_send(
                message, text, num_images=count,
                aspect_ratio=_fmt_to_aspect(fmt), actor_id=user_id,
                image_model=imodel,
            )
            return

        # Пользователь на экране идей (шаг 1) и ввёл свой промпт — переходим к настройкам.
        if st.get("step") == "prompt_picker":
            if len(text) < 3:
                await message.answer(flow_copy.msg("prompt_too_short"))
                return
            st["pending_prompt"] = text
            st["step"] = "wizard"
            picker_msg_id = st.get("picker_msg_id")
            if picker_msg_id:
                kb = wizard_kb(st.get("count", DEFAULT_COUNT), st.get("fmt", DEFAULT_FMT), st.get("imodel", DEFAULT_IMAGE_MODEL), show_improve=True)
                wtext = _wizard_text(user_id)
                try:
                    await message.bot.edit_message_text(
                        wtext,
                        chat_id=message.chat.id,
                        message_id=picker_msg_id,
                        reply_markup=kb,
                        parse_mode="HTML",
                    )
                    return
                except Exception:
                    pass  # fallback: send new message below
            await show_wizard(message, user_id=user_id, edit=False)
            return

        # Seller-бот — это инструмент для карточек, а не свободный генератор. Случайный
        # текст НЕ должен открывать платный image-визард: подсказываем выбрать задачу.
        if _cfg.IS_SELLER:
            await message.answer(
                "🛒 Я делаю карточки для маркетплейсов. Выбери задачу в меню "
                "«Карточки» и пришли фото товара — там подберём формат и стиль.",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="🛒 Карточки", callback_data="m:mp")],
                    [_menu_button("menu", "m:menu")],
                ]),
            )
            return

        # Иначе пользователь прислал промпт «вхолодную», не открыв визард. Не генерируем
        # вслепую: показываем выбор количества/формата/модели с уже сохранённым
        # промптом — после «Сгенерировать» сразу пойдёт генерация.
        last = st.get("last")
        st.clear()
        if last:  # сохраняем прошлые настройки как дефолт визарда
            st["last"] = last
            st["count"] = last.get("count", DEFAULT_COUNT)
            st["fmt"] = _aspect_to_fmt(last.get("aspect", "landscape"))
            st["imodel"] = last.get("imodel", DEFAULT_IMAGE_MODEL)
        st["pending_prompt"] = text
        await show_wizard(message, user_id=user_id, edit=False)

    return router
