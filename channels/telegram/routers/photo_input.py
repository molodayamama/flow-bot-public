"""Photo message input router (Phase 6)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import Any

from aiogram import F, Router, types

import flow_copy


@dataclass(frozen=True)
class PhotoInputDeps:
    """Injected state, media, marketplace, and video-photo helpers."""

    workspace: Callable[[int], MutableMapping[str, Any]]
    album_buffer: Callable[[], MutableMapping[str, list[types.Message]]]
    album_tasks: Callable[[], MutableMapping[str, Any]]
    create_task: Callable[[Awaitable[Any]], Any]
    flush_album: Callable[..., Awaitable[Any]]
    prepare_photo_edit_from_file_id: Callable[..., Awaitable[Any]]
    upload_photo_source_from_message: Callable[..., Awaitable[Any]]
    show_video_ingredients: Callable[..., Awaitable[Any]]
    show_video_frames: Callable[..., Awaitable[Any]]
    animate_photo_scenario: Callable[[], Any]
    context_factory: Callable[[types.Message, int], Any]
    template_photo_received: Callable[..., Awaitable[Any]]
    video_plain_text_ready: Callable[[MutableMapping[str, Any]], bool]
    video_generate_and_send: Callable[..., Awaitable[Any]]
    mp_video_prompt: Callable[..., str]
    mp_brand_kit: Callable[[int], Any]
    mp_niche: Callable[[int], Any]
    log_event: Callable[..., Any]
    seller_video_from_photo: Callable[..., Awaitable[Any]]
    mp_series_counts: Any
    mp_series_prompt: Callable[..., str]
    is_seller: Callable[[], bool]
    mp_confirm_screen: Callable[[int], tuple[Any, Any]]
    mp_stamp_message: Callable[..., Any]
    upload_image_ref_from_photo_message: Callable[..., Awaitable[Any]]
    mp_platform_aspect: Callable[[str], str]
    run_i2i: Callable[..., Awaitable[Any]]
    pending_edits: MutableMapping[int, Any]
    mp_job_instruction: Callable[..., str]
    mp_platform_fmt: Callable[[str], str]
    image_registry: Any
    edit_and_send: Callable[..., Awaitable[Any]]
    offer_photo_route_choice: Callable[..., Awaitable[Any]]
    default_fmt: str
    default_image_model: str
    vid_ref_default_model: str


def create_router(deps: PhotoInputDeps) -> Router:
    """Build the photo message-input router."""

    router = Router(name="tg-photo-input")

    @router.message(F.photo)
    async def handle_photo(message: types.Message):
        """Route uploaded photos through the active Telegram state branch."""
        user_id = message.from_user.id
        st = deps.workspace(user_id)
        vawait = st.get("vawait")
        caption = (message.caption or "").strip()

        mgid = message.media_group_id
        if mgid and vawait in ("ving_photo", "vfrm_start", "vfrm_end"):
            deps.album_buffer().setdefault(mgid, []).append(message)
            task = deps.album_tasks().get(mgid)
            if task:
                task.cancel()
            deps.album_tasks()[mgid] = deps.create_task(deps.flush_album(mgid, user_id))
            return

        if st.get("support_await"):
            await message.answer(
                "🙌 Сначала пришли текстовый бриф одним сообщением: товар, площадка, "
                "сколько слайдов и что важно показать. Фото добавим после заявки."
            )
            return

        if st.get("await") == "photo":
            await deps.prepare_photo_edit_from_file_id(
                message,
                user_id=user_id,
                file_id=message.photo[-1].file_id,
                caption=caption,
                aspect_fmt=st.get("edit_fmt", deps.default_fmt),
                image_model=st.get("edit_imodel", deps.default_image_model),
            )
            return

        if caption and (
            st.get("step") in ("prompt_picker", "wizard")
            or st.get("await") == "prompt"
            or st.get("pending_prompt")
        ):
            await deps.prepare_photo_edit_from_file_id(
                message,
                user_id=user_id,
                file_id=message.photo[-1].file_id,
                caption=caption,
                aspect_fmt=st.get("fmt", deps.default_fmt),
                image_model=st.get("imodel", deps.default_image_model),
                as_generation=True,
            )
            return

        if vawait == "ving_photo":
            photos: list = st.setdefault("ving_photos", [])
            if len(photos) < 4:
                status_msg = await message.answer(flow_copy.msg("uploading_photo"))
                source = await deps.upload_photo_source_from_message(
                    message,
                    user_id=user_id,
                    status_msg=status_msg,
                )
                if not source:
                    return
                photos.append(source)
                if caption:
                    st["vcaption_prompt"] = caption
                try:
                    await status_msg.delete()
                except Exception:
                    pass
            await deps.show_video_ingredients(message, user_id=user_id, edit=False)
            return

        if vawait == "vfrm_start":
            status_msg = await message.answer(flow_copy.msg("uploading_photo"))
            source = await deps.upload_photo_source_from_message(
                message,
                user_id=user_id,
                status_msg=status_msg,
            )
            if not source:
                return
            st["vfrm_start"] = source
            st["vawait"] = "vfrm_end"
            if caption:
                st["vcaption_prompt"] = caption
            try:
                await status_msg.delete()
            except Exception:
                pass
            await deps.show_video_frames(message, user_id=user_id, edit=False)
            return

        if vawait == "vfrm_end":
            status_msg = await message.answer(flow_copy.msg("uploading_photo"))
            source = await deps.upload_photo_source_from_message(
                message,
                user_id=user_id,
                status_msg=status_msg,
            )
            if not source:
                return
            st["vfrm_end"] = source
            st["vawait"] = None
            if caption:
                st["vcaption_prompt"] = caption
            try:
                await status_msg.delete()
            except Exception:
                pass
            await deps.show_video_frames(message, user_id=user_id, edit=False)
            return

        vstep = st.get("vstep")
        if vstep in ("vprompt_input", "vnewwiz"):
            await deps.animate_photo_scenario().attach_uploaded_photo(
                deps.context_factory(message, user_id),
                file_id=message.photo[-1].file_id,
                caption=caption,
            )
            return

        if st.get("tp_tpl") or "gp_step" in st or st.get("ideas_mode") in (
            "root",
            "templates",
            "guided",
        ):
            await deps.template_photo_received(message, user_id=user_id)
            return

        if vawait in ("vprompt", "vedit_prompt", "vextend_prompt"):
            await message.answer(flow_copy.msg("vid_text_only_hint"))
            return

        if deps.video_plain_text_ready(st):
            if caption:
                await deps.video_generate_and_send(message, caption, user_id=user_id)
            else:
                await message.answer(flow_copy.msg("vid_text_only_hint"))
            return

        if st.get("await") == "mp_video_photo":
            plat = st.get("mp_platform", "wb")
            caption_text = caption
            prompt = deps.mp_video_prompt(
                plat,
                caption_text,
                brand_kit=deps.mp_brand_kit(user_id),
                niche=deps.mp_niche(user_id),
            )
            deps.log_event("mp_video_photo_uploaded", user_id=user_id, source=f"{plat}:animate")
            ok = await deps.seller_video_from_photo(
                message,
                prompt,
                aspect_ratio="portrait",
                user_id=user_id,
                video_model=st.get("vmodel") or deps.vid_ref_default_model,
            )
            if ok:
                st["await"] = None
                deps.pending_edits.pop(user_id, None)
            return

        if st.get("await") == "mp_series_photo":
            plat = st.get("mp_platform", "wb")
            count = st.get("mp_series_count", 3)
            try:
                count = int(count)
            except (TypeError, ValueError):
                count = 3
            if count not in deps.mp_series_counts:
                count = 3
            caption_text = caption
            prompt = deps.mp_series_prompt(
                plat,
                count,
                caption_text,
                brand_kit=deps.mp_brand_kit(user_id),
                niche=deps.mp_niche(user_id),
            )
            deps.log_event("mp_series_photo_uploaded", user_id=user_id, source=f"{plat}:{count}")
            if deps.is_seller():
                st["mp_pending_file_id"] = message.photo[-1].file_id
                st["mp_pending_caption"] = caption_text
                st["mp_pending_kind"] = "series"
                st["await"] = None
                ctext, ckb = deps.mp_confirm_screen(user_id)
                sent = await message.answer(ctext, reply_markup=ckb, parse_mode="HTML")
                deps.mp_stamp_message(user_id, sent)
                return
            status_msg = await message.answer(flow_copy.msg("uploading_photo"))
            ref = await deps.upload_image_ref_from_photo_message(
                message,
                user_id=user_id,
                status_msg=status_msg,
                prompt=prompt,
                aspect_ratio=deps.mp_platform_aspect(plat),
            )
            if not ref:
                return
            try:
                await status_msg.delete()
            except Exception:
                pass
            ok = await deps.run_i2i(
                message,
                ref,
                prompt,
                num_images=count,
                emoji="🧩",
                action="mp_series",
                fail_text=flow_copy.msg("nothing_returned"),
            )
            if ok:
                st["await"] = None
                st.pop("mp_series_count", None)
                deps.pending_edits.pop(user_id, None)
            return

        if st.get("await") == "mp_photo":
            plat = st.get("mp_platform", "wb")
            job = st.get("mp_preset", "whitebg")
            caption_text = caption
            instruction = deps.mp_job_instruction(
                job,
                plat,
                caption_text,
                brand_kit=deps.mp_brand_kit(user_id),
                niche=deps.mp_niche(user_id),
            )
            deps.log_event("mp_photo_uploaded", user_id=user_id, source=f"{plat}:{job}")
            if deps.is_seller():
                st["mp_pending_file_id"] = message.photo[-1].file_id
                st["mp_pending_caption"] = caption_text
                st["mp_pending_kind"] = "photo"
                st["await"] = None
                ctext, ckb = deps.mp_confirm_screen(user_id)
                sent = await message.answer(ctext, reply_markup=ckb, parse_mode="HTML")
                deps.mp_stamp_message(user_id, sent)
                return
            status_msg = await message.answer(flow_copy.msg("uploading_photo"))
            ref = await deps.upload_image_ref_from_photo_message(
                message,
                user_id=user_id,
                status_msg=status_msg,
                prompt=instruction,
                aspect_ratio=deps.mp_platform_aspect(plat),
            )
            if not ref:
                return
            try:
                await status_msg.delete()
            except Exception:
                pass
            token = deps.image_registry.add(ref)
            deps.pending_edits[user_id] = token
            st["await"] = "edit"
            st["edit_fmt"] = deps.mp_platform_fmt(plat)
            st["edit_imodel"] = st.get("edit_imodel", deps.default_image_model)
            ok = await deps.edit_and_send(
                message,
                ref,
                instruction,
                aspect_ratio=deps.mp_platform_aspect(plat),
                image_model=st.get("edit_imodel", deps.default_image_model),
            )
            if ok:
                st["await"] = None
                deps.pending_edits.pop(user_id, None)
            return

        if caption:
            await deps.offer_photo_route_choice(message, user_id=user_id, caption=caption)
            return

        await deps.prepare_photo_edit_from_file_id(
            message,
            user_id=user_id,
            file_id=message.photo[-1].file_id,
            caption="",
        )

    return router
