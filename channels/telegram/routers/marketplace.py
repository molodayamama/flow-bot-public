"""Marketplace callback router (Phase 6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Collection, MutableMapping

from aiogram import F, Router, types

import html
from config.video import VID_REF_DEFAULT_MODEL
from flow_core import DEFAULT_IMAGE_MODEL
from channels.telegram.keyboards import (
    _MP_NICHES,
    _MP_PLAT_NAMES,
    _mp_back_kb,
    _mp_jobs_text,
    _mp_more_text,
    _mp_niche_kb,
    _mp_photo_settings_kb,
    _mp_platform_fmt,
    _mp_sku_open_kb,
    mp_jobs_kb,
    mp_more_kb,
    mp_series_kb,
)
from channels.telegram.texts import (
    _MP_SERIES_COUNTS,
    _mp_brand_kit,
    _mp_brandkit_text,
    _mp_niche,
    _mp_niche_text,
    _mp_photo_request_text,
    _mp_series_request_text,
    _mp_sku_open_text,
    _mp_video_request_text,
)


@dataclass(frozen=True)
class MarketplaceDeps:
    """Injected state, metrics, generation, and seller helpers for ``mp:`` callbacks."""

    workspace: Callable[[int], MutableMapping[str, Any]]
    pending_edits: MutableMapping[int, str]
    metrics: Any
    is_seller: Callable[[], bool]
    product_photo_jobs: Collection[str]
    mp_is_stale_callback: Callable[[int, types.CallbackQuery], bool]
    mp_reject_stale_callback: Callable[[types.CallbackQuery], Awaitable[Any]]
    mp_stamp_message: Callable[[int, types.Message], Any]
    mp_job_instruction: Callable[..., str]
    mp_series_prompt: Callable[..., str]
    seller_i2i_from_file_id: Callable[..., Awaitable[Any]]
    show_sku_projects: Callable[..., Awaitable[Any]]
    pending_sku_payload: Callable[[int], dict | None]
    latest_sku_payload: Callable[..., dict | None]
    save_sku_payload: Callable[..., Awaitable[bool]]
    save_pending_sku_item: Callable[..., Awaitable[Any]]
    reset_image_flow: Callable[..., Any]
    vid_clear: Callable[[int], Any]
    clear_image_flow_keys: Callable[[MutableMapping[str, Any]], Any]
    show_video_ingredients: Callable[..., Awaitable[Any]]


def create_router(deps: MarketplaceDeps) -> Router:
    """Build the seller marketplace callback router."""

    router = Router(name="tg-marketplace")

    @router.callback_query(F.data.startswith("mp:"))
    async def on_marketplace_action(callback: types.CallbackQuery):
        """Селлер-меню «Маркетплейсы»: площадка → задача (docs/SELLER_BOT_PLAN.md §4)."""
        user_id = callback.from_user.id
        deps.metrics.upsert_user(user_id, username=getattr(callback.from_user, "username", None),
                            first_name=getattr(callback.from_user, "first_name", None))
        data = callback.data or ""
        msg = callback.message
        if deps.mp_is_stale_callback(user_id, callback):
            await deps.mp_reject_stale_callback(callback)
            return
        deps.mp_stamp_message(user_id, msg)

        if data.startswith("mp:plat:"):
            plat = data.split(":", 2)[2]
            if plat not in _MP_PLAT_NAMES:
                await callback.answer()
                return
            deps.workspace(user_id)["mp_platform"] = plat
            await callback.answer()
            deps.metrics.log_event("mp_platform", user_id=user_id, source=plat)
            await msg.edit_text(
                _mp_jobs_text(plat),
                reply_markup=mp_jobs_kb(plat),
                parse_mode="HTML",
            )
            return

        # Выбор площадки прямо на экране настроек (перед загрузкой фото).
        if data.startswith("mp:setplat:"):
            plat = data.split(":", 2)[2]
            if plat not in _MP_PLAT_NAMES:
                await callback.answer()
                return
            st = deps.workspace(user_id)
            st["mp_platform"] = plat
            st["edit_fmt"] = _mp_platform_fmt(plat)
            await callback.answer(_MP_PLAT_NAMES[plat])
            deps.metrics.log_event("mp_platform", user_id=user_id, source=f"setplat:{plat}")
            try:
                if st.get("await") == "mp_video_photo":
                    await msg.edit_text(
                        _mp_video_request_text(plat),
                        reply_markup=_mp_photo_settings_kb(plat),
                        parse_mode="HTML",
                    )
                else:
                    job = st.get("mp_preset", "whitebg")
                    await msg.edit_text(
                        _mp_photo_request_text(plat, job),
                        reply_markup=_mp_photo_settings_kb(plat),
                        parse_mode="HTML",
                    )
            except Exception:
                pass
            return

        if data == "mp:more":
            plat = deps.workspace(user_id).get("mp_platform", "wb")
            if plat not in _MP_PLAT_NAMES:
                plat = "wb"
            await callback.answer()
            deps.metrics.log_event("mp_more_open", user_id=user_id, source=plat)
            await msg.edit_text(
                _mp_more_text(plat),
                reply_markup=mp_more_kb(plat),
                parse_mode="HTML",
            )
            return

        # Подтверждение генерации карточки: генерим по ранее загруженному фото.
        if data == "mp:create":
            st = deps.workspace(user_id)
            file_id = st.get("mp_pending_file_id")
            if not file_id:
                await callback.answer("Сначала пришли фото товара 🙏", show_alert=True)
                return
            plat = st.get("mp_platform", "wb")
            st["edit_fmt"] = _mp_platform_fmt(plat)
            kind = st.get("mp_pending_kind", "photo")
            caption_text = (st.get("mp_pending_caption") or "").strip()
            aspect = _mp_platform_aspect(plat)
            await callback.answer("Запускаю…")
            st.pop("mp_pending_file_id", None)  # защита от повторного клика → двойной генерации
            if kind == "series":
                count = st.get("mp_series_count", 3)
                if count not in _MP_SERIES_COUNTS:
                    count = 3
                prompt = deps.mp_series_prompt(plat, count, caption_text,
                                               brand_kit=_mp_brand_kit(user_id), niche=_mp_niche(user_id))
                ok = await deps.seller_i2i_from_file_id(
                    callback.message, file_id, prompt, num_images=count,
                    aspect_ratio=aspect, user_id=user_id, action="mp_series",
                )
                if ok:
                    st.pop("mp_series_count", None)
            else:
                job = st.get("mp_preset", "whitebg")
                instruction = deps.mp_job_instruction(job, plat, caption_text,
                                                      brand_kit=_mp_brand_kit(user_id), niche=_mp_niche(user_id))
                await deps.seller_i2i_from_file_id(
                    callback.message, file_id, instruction, num_images=1,
                    aspect_ratio=aspect, user_id=user_id, action="edit",
                )
            return

        if data == "mp:series":
            plat = deps.workspace(user_id).get("mp_platform", "wb")
            if plat not in _MP_PLAT_NAMES:
                plat = "wb"
            await callback.answer()
            deps.metrics.log_event("mp_series_open", user_id=user_id, source=plat)
            await msg.edit_text(
                f"🧩 <b>{_MP_PLAT_NAMES[plat]}</b> — выбери размер серии",
                reply_markup=mp_series_kb(plat),
                parse_mode="HTML",
            )
            return

        if data == "mp:projects":
            await callback.answer()
            deps.metrics.log_event("mp_projects_open", user_id=user_id, source="seller")
            await deps.show_sku_projects(msg, user_id=user_id, edit=True)
            return

        if data.startswith("mp:sku:open:"):
            try:
                idx = int(data.rsplit(":", 1)[1])
            except (TypeError, ValueError):
                await callback.answer()
                return
            st = deps.workspace(user_id)
            choices = st.get("mp_sku_project_choices") or [
                str(p.get("sku") or "") for p in deps.metrics.list_seller_sku_projects(user_id, limit=12)
            ]
            if idx < 0 or idx >= len(choices) or not choices[idx]:
                await callback.answer("SKU не найден", show_alert=True)
                return
            sku = str(choices[idx])
            st["mp_sku_open"] = sku
            await callback.answer()
            await msg.edit_text(_mp_sku_open_text(user_id, sku), reply_markup=_mp_sku_open_kb(), parse_mode="HTML")
            return

        if data == "mp:sku:addlast":
            st = deps.workspace(user_id)
            sku = str(st.get("mp_sku_open") or "").strip()
            if not sku:
                await callback.answer("Сначала открой SKU", show_alert=True)
                return
            project = deps.metrics.get_seller_sku_project(user_id, sku) or {}
            payload = deps.pending_sku_payload(user_id) or deps.latest_sku_payload(
                user_id, platform=str(project.get("platform") or st.get("mp_platform") or "")
            )
            if not payload:
                await callback.answer("Нет карточки для добавления", show_alert=True)
                return
            if not await deps.save_sku_payload(msg, user_id, sku, payload):
                await callback.answer("Не удалось сохранить", show_alert=True)
                return
            st.pop("mp_sku_pending", None)
            st.pop("mp_sku_choices", None)
            st["await"] = None
            deps.metrics.log_event("mp_sku_saved", user_id=user_id, source=str(payload.get("platform") or "seller"))
            await callback.answer("Добавлено в SKU")
            await msg.edit_text(_mp_sku_open_text(user_id, sku), reply_markup=_mp_sku_open_kb(), parse_mode="HTML")
            return

        if data == "mp:sku:rename":
            sku = str(deps.workspace(user_id).get("mp_sku_open") or "").strip()
            if not sku:
                await callback.answer("Сначала открой SKU", show_alert=True)
                return
            deps.workspace(user_id)["await"] = "mp_sku_rename"
            await callback.answer()
            await msg.answer(
                f"✏️ Пришли новое название для SKU <b>{html.escape(sku)}</b> одним сообщением.",
                parse_mode="HTML",
            )
            return

        if data == "mp:sku:delete":
            sku = str(deps.workspace(user_id).get("mp_sku_open") or "").strip()
            if not sku:
                await callback.answer("Сначала открой SKU", show_alert=True)
                return
            await callback.answer()
            kb = types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🗑 Да, удалить SKU", callback_data="mp:sku:delete:yes")],
                [types.InlineKeyboardButton(text="◀️ Оставить", callback_data="mp:sku:backopen")],
            ])
            await msg.edit_text(
                f"🗑 Удалить SKU <b>{html.escape(sku)}</b> и сохранённые слайды?",
                reply_markup=kb,
                parse_mode="HTML",
            )
            return

        if data == "mp:sku:backopen":
            sku = str(deps.workspace(user_id).get("mp_sku_open") or "").strip()
            if not sku:
                await callback.answer()
                await deps.show_sku_projects(msg, user_id=user_id, edit=True)
                return
            await callback.answer()
            await msg.edit_text(_mp_sku_open_text(user_id, sku), reply_markup=_mp_sku_open_kb(), parse_mode="HTML")
            return

        if data == "mp:sku:delete:yes":
            st = deps.workspace(user_id)
            sku = str(st.get("mp_sku_open") or "").strip()
            if not sku:
                await callback.answer("Сначала открой SKU", show_alert=True)
                return
            deleted = deps.metrics.delete_seller_sku_project(user_id, sku)
            st.pop("mp_sku_open", None)
            deps.metrics.log_event("mp_sku_deleted", user_id=user_id, source="seller", payload={"rows": deleted})
            await callback.answer("SKU удалён")
            await deps.show_sku_projects(msg, user_id=user_id, edit=True)
            return

        if data == "mp:brandkit":
            await callback.answer()
            st = deps.workspace(user_id)
            st["await"] = "mp_brandkit"
            deps.metrics.log_event("mp_brandkit_open", user_id=user_id, source="seller")
            await msg.edit_text(
                _mp_brandkit_text(user_id),
                reply_markup=_mp_back_kb(),
                parse_mode="HTML",
            )
            return

        if data == "mp:niche":
            await callback.answer()
            deps.metrics.log_event("mp_niche_open", user_id=user_id, source="seller")
            await msg.edit_text(
                _mp_niche_text(user_id),
                reply_markup=_mp_niche_kb(),
                parse_mode="HTML",
            )
            return

        if data.startswith("mp:niche:"):
            niche_id = data.rsplit(":", 1)[1]
            if niche_id not in _MP_NICHES:
                await callback.answer()
                return
            label = _MP_NICHES[niche_id][0]
            ok = deps.metrics.save_seller_profile(user_id, niche=niche_id)
            deps.metrics.log_event("mp_niche_saved", user_id=user_id, source=niche_id)
            await callback.answer("Ниша сохранена" if ok else "Не удалось сохранить")
            if ok:
                await msg.edit_text(
                    f"🏷️ Ниша сохранена: <b>{html.escape(label)}</b>\n\n"
                    "Теперь seller-карточки и серии будут учитывать эту категорию.",
                    reply_markup=_mp_back_kb(),
                    parse_mode="HTML",
                )
            else:
                await msg.edit_text(
                    "Не удалось сохранить нишу. Попробуй ещё раз позже.",
                    reply_markup=_mp_niche_kb(),
                    parse_mode="HTML",
                )
            return

        if data == "mp:sku:new":
            await callback.answer()
            deps.workspace(user_id)["await"] = "mp_sku_name"
            has_pending = bool(deps.pending_sku_payload(user_id))
            title = "название нового SKU для этого результата" if has_pending else "название нового SKU"
            await msg.answer(
                f"📦 Пришли {title} одним сообщением. "
                "Например: <code>SKU-104 красные ботинки</code>",
                parse_mode="HTML",
            )
            return

        if data.startswith("mp:sku:"):
            payload = deps.pending_sku_payload(user_id)
            if not payload:
                await callback.answer("Кнопка устарела", show_alert=True)
                return
            try:
                idx = int(data.rsplit(":", 1)[1])
            except (TypeError, ValueError):
                await callback.answer()
                return
            choices = deps.workspace(user_id).get("mp_sku_choices") or []
            if idx < 0 or idx >= len(choices):
                await callback.answer()
                return
            await callback.answer()
            await deps.save_pending_sku_item(msg, user_id, str(choices[idx]))
            return

        if data.startswith("mp:series:"):
            plat = deps.workspace(user_id).get("mp_platform", "wb")
            if plat not in _MP_PLAT_NAMES:
                plat = "wb"
            try:
                count = int(data.rsplit(":", 1)[1])
            except (TypeError, ValueError):
                await callback.answer()
                return
            if count not in _MP_SERIES_COUNTS:
                await callback.answer()
                return
            await callback.answer()
            deps.reset_image_flow(user_id)
            st = deps.workspace(user_id)
            st["mp_platform"] = plat
            st["mp_series_count"] = count
            st["await"] = "mp_series_photo"
            st["edit_fmt"] = _mp_platform_fmt(plat)
            deps.metrics.log_event(
                "mp_job", user_id=user_id, source=f"{plat}:series:{count}",
                payload={"count": count},
            )
            await msg.edit_text(
                _mp_series_request_text(plat, count),
                reply_markup=_mp_back_kb(),
                parse_mode="HTML",
            )
            return

        if data.startswith("mp:job:"):
            job = data.split(":", 2)[2]
            plat = deps.workspace(user_id).get("mp_platform", "wb")
            if job == "animate":
                await callback.answer()
                if deps.is_seller():
                    deps.reset_image_flow(user_id)
                    st = deps.workspace(user_id)
                    st["mp_platform"] = plat
                    st["await"] = "mp_video_photo"
                    st["vmodel"] = VID_REF_DEFAULT_MODEL
                    st["vfmt"] = "port"
                    st["vcount"] = 1
                    deps.metrics.log_event("mp_job", user_id=user_id, source=f"{plat}:animate")
                    await msg.edit_text(
                        _mp_video_request_text(plat),
                        reply_markup=_mp_photo_settings_kb(plat),
                        parse_mode="HTML",
                    )
                else:
                    deps.pending_edits.pop(user_id, None)
                    deps.vid_clear(user_id)
                    st = deps.workspace(user_id)
                    deps.clear_image_flow_keys(st)
                    st["vmode"] = "ingredients"
                    st["vmodel"] = VID_REF_DEFAULT_MODEL
                    st["vcount"] = 1
                    st["mp_platform"] = plat
                    await deps.show_video_ingredients(msg, user_id=user_id, edit=True)
                return
            if job not in deps.product_photo_jobs:
                await callback.answer()
                return
            await callback.answer()
            deps.reset_image_flow(user_id)
            st = deps.workspace(user_id)
            st["mp_platform"] = plat
            st["mp_preset"] = job
            st["await"] = "mp_photo"
            st["edit_fmt"] = _mp_platform_fmt(plat)
            st["edit_imodel"] = DEFAULT_IMAGE_MODEL
            deps.metrics.log_event("mp_job", user_id=user_id, source=f"{plat}:{job}")
            await msg.edit_text(
                _mp_photo_request_text(plat, job),
                reply_markup=_mp_photo_settings_kb(plat),
                parse_mode="HTML",
            )
            return

        if data == "mp:done4you":
            await callback.answer()
            plat = deps.workspace(user_id).get("mp_platform", "wb")
            if plat not in _MP_PLAT_NAMES:
                plat = "wb"
            deps.reset_image_flow(user_id)
            st = deps.workspace(user_id)
            st["mp_platform"] = plat
            st["support_await"] = True
            st["support_kind"] = "mp_done4you"
            deps.metrics.log_event("mp_done4you_open", user_id=user_id, source="seller")
            await msg.edit_text(
                "🙌 <b>Сделаем карточки под ключ</b>\n\n"
                "Опиши задачу прямо здесь одним сообщением: что за товар, площадка, "
                "сколько слайдов, ссылки/артикулы и что важно показать. Я создам "
                "заявку для оператора, дальше можно будет добавить фото товара. "
                "Оплата по тарифу.",
                reply_markup=_mp_back_kb(),
                parse_mode="HTML",
            )
            return

        if data == "mp:tips":
            await callback.answer()
            await msg.edit_text(
                "💡 <b>Что делает карточку продающей</b>\n\n"
                "• Главное фото: товар крупно, чистый фон, без лишнего.\n"
                "• 1-й слайд = оффер: заголовок + ключевая выгода.\n"
                "• Слайды: характеристики, состав/гарантия, до/после.\n"
                "• Текст крупный и читаемый, важное — не в углах (safe-зоны).\n"
                "• Единый стиль: один цвет/шрифт во всей серии.",
                reply_markup=_mp_back_kb(),
            )
            return

        await callback.answer()

    return router
