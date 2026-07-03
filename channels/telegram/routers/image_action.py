"""Generated-image action callback router (Phase 6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, MutableMapping

from aiogram import Router, types

import flow_copy
from flow_core import parse_action_callback


@dataclass(frozen=True)
class ImageActionDeps:
    """Injected state, registry, render, and image operation helpers."""

    workspace: Callable[[int], MutableMapping[str, Any]]
    image_registry: Any
    pending_edits: MutableMapping[int, str]
    mix_baskets: MutableMapping[int, list[Any]]
    is_seller: Callable[[], bool]
    aspect_to_fmt: Callable[[str], str]
    edit_settings_kb: Callable[..., types.InlineKeyboardMarkup]
    mp_sku_choice_kb: Callable[[int], types.InlineKeyboardMarkup]
    mp_stamp_message: Callable[[int, types.Message], Any]
    vary_and_send: Callable[..., Awaitable[Any]]
    regen_and_send: Callable[..., Awaitable[Any]]
    enhance_and_send: Callable[..., Awaitable[Any]]
    real_upscale_and_send: Callable[..., Awaitable[Any]]
    send_original_file: Callable[..., Awaitable[Any]]
    default_image_model: str
    mix_max: int


def _is_action_callback(callback: types.CallbackQuery) -> bool:
    """Precise routing filter for the image-action handler below.

    Matches exactly the callbacks the old bare catch-all actually processed
    (every flow_core.ACTION_PREFIXES prefix). Anything else now falls through
    to the tail fallback router, which keeps the old silent-ack behaviour.
    """
    return parse_action_callback(callback.data or "") is not None


def create_router(deps: ImageActionDeps) -> Router:
    """Build the generated-image action callback router."""

    router = Router(name="tg-image-action")

    @router.callback_query(_is_action_callback)
    async def on_image_action(callback: types.CallbackQuery):
        """Единый обработчик инлайн-кнопок под картинкой (edit/vary/regen/mix/up)."""
        parsed = parse_action_callback(callback.data or "")
        if parsed is None:
            await callback.answer()
            return
        action, token = parsed
        user_id = callback.from_user.id
        ref = deps.image_registry.get(token)
        if ref is None or ref.user_id != user_id:
            await callback.answer(
                "Кнопка устарела. Сгенерируйте изображение заново.", show_alert=True
            )
            return

        if action == "edit":
            deps.pending_edits[user_id] = token
            st = deps.workspace(user_id)
            st["await"] = "edit"
            st.pop("edit_as_gen", None)  # правка готовой картинки = тариф правки (15/20)
            # Формат по умолчанию = формат исходной картинки; модель — последняя выбранная.
            st["edit_fmt"] = deps.aspect_to_fmt(ref.aspect_ratio)
            st.setdefault("edit_imodel", deps.default_image_model)
            await callback.answer()
            await callback.message.answer(
                flow_copy.msg("ask_edit_prompt"),
                reply_markup=deps.edit_settings_kb(st["edit_fmt"], st["edit_imodel"]),
            )
        elif action == "revary":
            deps.pending_edits[user_id] = token
            deps.workspace(user_id)["await"] = "revary"
            await callback.answer()
            await callback.message.answer(flow_copy.msg("ask_revary_prompt"))
        elif action == "vary":
            await callback.answer("Делаю вариации 🎲")
            await deps.vary_and_send(callback.message, ref)
        elif action == "regen":
            await callback.answer("Генерирую ещё 🔄")
            await deps.regen_and_send(callback.message, ref)
        elif action == "up2x":
            await callback.answer("Повышаю чёткость ✨")
            await deps.enhance_and_send(callback.message, ref)
        elif action == "realup":
            await callback.answer("Увеличиваю разрешение 🔍")
            await deps.real_upscale_and_send(callback.message, ref)
        elif action == "skuadd":
            if not deps.is_seller():
                await callback.answer()
                return
            photos = getattr(callback.message, "photo", None) or []
            if not photos:
                await callback.answer("Не нашёл файл картинки", show_alert=True)
                return
            st = deps.workspace(user_id)
            st["mp_sku_pending"] = {
                "token": token,
                "file_id": photos[-1].file_id,
                "prompt": ref.prompt or "",
                "platform": ref.platform or st.get("mp_platform", ""),
            }
            st["await"] = "mp_sku_name"
            await callback.answer()
            sent = await callback.message.answer(
                "📦 В какой SKU добавить этот результат?",
                reply_markup=deps.mp_sku_choice_kb(user_id),
            )
            deps.mp_stamp_message(user_id, sent)
        elif action == "mpexport":
            if not deps.is_seller():
                await callback.answer()
                return
            await callback.answer("Готовлю файл для маркетплейса ⬇️")
            await deps.send_original_file(callback.message, ref, marketplace_export=True)
        elif action in ("download", "upscale"):
            await callback.answer("Готовлю файл ⬇️")
            await deps.send_original_file(callback.message, ref)
        elif action == "mix":
            basket = deps.mix_baskets[user_id]
            if len(basket) >= deps.mix_max:
                await callback.answer(f"В миксе уже {deps.mix_max}", show_alert=True)
                return
            basket.append(ref.source)
            await callback.answer(f"Добавлено в микс: {len(basket)}")
            if len(basket) >= 2:
                await callback.message.answer(
                    f"🧩 В миксе {len(basket)} картинок. Пришлите `/mix ваш промпт`.",
                    parse_mode="Markdown",
                )

    return router
