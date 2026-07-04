"""Ideas Hub screen renderers for the Telegram adapter (Phase 11 core split).

The button-driven "Идеи и шаблоны" surface: root menu, ready-made template Q&A
stepper, guided (step-by-step) picker, and the photo-received handler that saves
an uploaded photo as the base/reference before the final generation screen. Pure
session-state helpers + copy live in :mod:`product.ideas_hub` and
:mod:`prompts_lib`; this renders them into Telegram messages and hands off to the
image/video wizards or the photo-intake pipeline.

Extracted out of the flow_bot composition root; runtime singletons and sibling
screens are injected via :class:`IdeasScreensDeps` so this module never imports
flow_bot. Telegram adapter code (uses aiogram), not a platform-neutral core
module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aiogram import types

import flow_copy
import prompts_lib
from product.ideas_hub import (
    _GP_STATE_KEYS,
    _IDEAS_PHOTO_KEYS,
    _TP_STATE_KEYS,
    guided_image_fmt as _guided_image_fmt,
    guided_video_fmt as _guided_video_fmt,
    ideas_clear as _ideas_clear,
    ideas_has_photo as _ideas_has_photo,
    ideas_prompt_with_extra as _ideas_prompt_with_extra,
    tp_store_answer as _ideas_tp_store_answer,
)


@dataclass(frozen=True)
class IdeasScreensDeps:
    workspace: Callable[[int], dict]
    menu_button: Callable[..., Any]
    edit_or_answer: Callable[..., Awaitable[Any]]
    metrics: Any
    prepare_photo_video_from_file_id: Callable[..., Awaitable[Any]]
    prepare_photo_edit_from_file_id: Callable[..., Awaitable[Any]]
    vid_clear: Callable[[int], None]
    clear_image_flow_keys: Callable[[dict], None]
    vid_default_fmt: str
    show_new_video_wizard: Callable[..., Awaitable[Any]]
    show_wizard: Callable[..., Awaitable[Any]]
    show_video_prompt_input: Callable[..., Awaitable[Any]]
    templates_picker_kb: Callable[[], Any]
    guided_step_kb: Callable[[int], Any]
    guided_to_vid_style: dict


class IdeasScreens:
    def __init__(self, deps: IdeasScreensDeps) -> None:
        self._d = deps

    def tp_store_answer(self, st: dict, value: str) -> None:
        _ideas_tp_store_answer(st, value, template_questions=prompts_lib.template_questions)

    async def show_ideas_root(self, message: types.Message, *, user_id: int, edit: bool) -> None:
        d = self._d
        st = d.workspace(user_id)
        _ideas_clear(st, clear_photo=False)
        st["ideas_mode"] = "root"
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [d.menu_button("ideas_templates", "ih:templates")],
            [d.menu_button("ideas_guided", "ih:guided")],
            [d.menu_button("menu", "m:menu")],
        ])
        text = flow_copy.msg("ideas_root")
        if edit:
            await d.edit_or_answer(message, text, kb, parse_mode="HTML")
        else:
            await message.answer(text, reply_markup=kb, parse_mode="HTML")

    async def render_template_step(self, message: types.Message, *, user_id: int) -> None:
        """Показать текущий вопрос шаблона (или скомпоновать промпт и уйти в визард)."""
        d = self._d
        st = d.workspace(user_id)
        tid = st.get("tp_tpl")
        questions = prompts_lib.template_questions(tid) if tid else []
        step = st.get("tp_step", 0)
        if not tid or step >= len(questions):
            # Все ответы собраны → компонуем промпт и открываем экран генерации.
            prompt = prompts_lib.compose_template_prompt(tid, st.get("tp_answers", {}))
            prompt = _ideas_prompt_with_extra(prompt, st)
            target = prompts_lib.template_target(tid)
            d.metrics.log_event("template_used", user_id=user_id, source="ideas",
                                payload={"template": tid, "target": target})
            ideas_photo_file_id = st.get("ideas_photo_file_id")
            for k in (*_TP_STATE_KEYS, *_IDEAS_PHOTO_KEYS):
                st.pop(k, None)
            st.pop("ideas_mode", None)
            if target == "video":
                if ideas_photo_file_id:
                    await d.prepare_photo_video_from_file_id(
                        message, user_id=user_id, file_id=ideas_photo_file_id,
                        caption=prompt or "high quality video",
                    )
                    return
                # Шаблон-видео без фото: идём в новый video wizard с предзаполненным промптом.
                d.vid_clear(user_id)
                d.clear_image_flow_keys(st)
                st["vprompt"] = prompt or ""
                st.setdefault("vfmt", d.vid_default_fmt)
                st.setdefault("vdur", 4)
                st.setdefault("vquality", "lite")
                st.setdefault("vstyle", "")
                await d.show_new_video_wizard(message, user_id=user_id, edit=True)
            elif ideas_photo_file_id:
                # «Фото = основа»: применяем собранный промпт шаблона как правку к
                # загруженному пользователем фото (тот же пайплайн, что «Изменить фото»).
                await d.prepare_photo_edit_from_file_id(
                    message, user_id=user_id, file_id=ideas_photo_file_id,
                    caption=prompt or "high quality image",
                )
            else:
                st["pending_prompt"] = prompt or "high quality image"
                await d.show_wizard(message, user_id=user_id, edit=True)
            return
        q = questions[step]
        B = types.InlineKeyboardButton
        rows = []
        if q["type"] == "choice":
            st["tp_await"] = None
            for i, opt in enumerate(q["options"]):
                rows.append([B(text=opt["label"], callback_data=f"tp:ans:{i}")])
        else:
            st["tp_await"] = "text"  # ждём свободный текст в чат
        if q.get("optional"):
            rows.append([d.menu_button("skip", "tp:skip")])
        nav = [d.menu_button("back", "tp:back")] if step > 0 else []
        nav.append(d.menu_button("cancel", "tp:cancel"))
        rows.append(nav)
        kb = types.InlineKeyboardMarkup(inline_keyboard=rows)
        text = flow_copy.msg("ideas_qa_step", n=step + 1, total=len(questions), q=q["text"])
        if q["type"] == "text":
            text += "\n\n" + flow_copy.msg("ideas_type_hint")
        else:
            text += "\n\n" + flow_copy.msg("ideas_choice_hint")
        if _ideas_has_photo(st):
            text += "\n\n" + flow_copy.msg("ideas_photo_context_hint")
        await d.edit_or_answer(message, text, kb)

    async def template_photo_received(self, message: types.Message, *, user_id: int) -> None:
        """Фото внутри «Идеи и шаблоны»: сохраняем как основу/референс до финального экрана."""
        d = self._d
        st = d.workspace(user_id)
        caption = (message.caption or "").strip()
        st["ideas_photo_file_id"] = message.photo[-1].file_id
        if caption:
            st["ideas_photo_caption"] = caption

        if st.get("tp_tpl"):
            if caption and st.get("tp_await") == "text":
                self.tp_store_answer(st, caption)
            elif caption:
                st["ideas_extra_prompt"] = caption
            await message.answer(flow_copy.msg("ideas_photo_attached_template"))
            await self.render_template_step(message, user_id=user_id)
            return

        if "gp_step" in st:
            if caption:
                st["gp_extra_prompt"] = caption
            await message.answer(flow_copy.msg("ideas_photo_attached_guided"))
            await self.render_guided_step(message, user_id=user_id)
            return

        if caption:
            st["ideas_extra_prompt"] = caption
        await message.answer(flow_copy.msg("ideas_photo_attached_root"))
        mode = st.get("ideas_mode")
        if mode == "templates":
            await message.answer(
                flow_copy.msg("ideas_templates_title"),
                reply_markup=d.templates_picker_kb(),
                parse_mode="HTML",
            )
        else:
            await self.show_ideas_root(message, user_id=user_id, edit=False)

    async def render_guided_step(self, message: types.Message, *, user_id: int) -> None:
        d = self._d
        st = d.workspace(user_id)
        steps = prompts_lib.guided_steps()
        step = st.get("gp_step", 0)
        if step >= len(steps):
            answers = st.get("gp_answers", {})
            prompt = prompts_lib.compose_guided_prompt(answers)
            prompt = _ideas_prompt_with_extra(prompt, st)
            ideas_photo_file_id = st.get("ideas_photo_file_id")
            # Ветка «видео» уводит в видео-визард, остальное — в генерацию картинки.
            if answers.get("what") == "video":
                # Переносим формат и стиль из guided в видео-визард.
                gv_fmt = _guided_video_fmt(answers)
                gv_style = d.guided_to_vid_style.get(answers.get("style", ""), "")
                for k in (*_GP_STATE_KEYS, *_IDEAS_PHOTO_KEYS):
                    st.pop(k, None)
                st.pop("ideas_mode", None)
                if ideas_photo_file_id:
                    await d.prepare_photo_video_from_file_id(
                        message, user_id=user_id, file_id=ideas_photo_file_id,
                        caption=prompt or "high quality video", vfmt=gv_fmt, vstyle=gv_style,
                    )
                    return
                await d.show_video_prompt_input(
                    message, user_id=user_id, edit=True, vfmt=gv_fmt, vstyle=gv_style
                )
                return
            d.metrics.log_event("guided_completed", user_id=user_id, source="ideas")
            image_fmt = _guided_image_fmt(answers)
            for k in (*_GP_STATE_KEYS, *_IDEAS_PHOTO_KEYS):
                st.pop(k, None)
            st.pop("ideas_mode", None)
            if ideas_photo_file_id:
                await d.prepare_photo_edit_from_file_id(
                    message, user_id=user_id, file_id=ideas_photo_file_id,
                    caption=prompt or "high quality image", aspect_fmt=image_fmt,
                )
                return
            st["pending_prompt"] = prompt or "high quality image"
            st["fmt"] = image_fmt
            await d.show_wizard(message, user_id=user_id, edit=True)
            return
        s = steps[step]
        text = flow_copy.msg("ideas_qa_step", n=step + 1, total=len(steps), q=s["text"])
        text += "\n\n" + flow_copy.msg("ideas_guided_hint")
        if _ideas_has_photo(st):
            text += "\n\n" + flow_copy.msg("ideas_photo_context_hint")
        await d.edit_or_answer(message, text, d.guided_step_kb(step))
