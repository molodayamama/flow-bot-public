"""Platform-neutral internal generation backend.

This module owns the consumer-side work behind ``/internal/generate``. It keeps
the wire contract used by the seller bot, while receiving Flow/account/runtime
dependencies explicitly from ``flow_bot``.
"""

from __future__ import annotations

import base64
from typing import Any


async def generate_images(deps: Any, req: dict) -> dict:
    """Run a text-to-image generation on the consumer account pool."""
    prompt = str(req.get("prompt") or "").strip()
    if len(prompt) < 3:
        return {"error": "empty prompt"}
    num_images = max(1, min(int(req.get("num_images") or 1), 4))
    aspect_ratio = str(req.get("aspect_ratio") or "portrait")
    image_model = str(req.get("image_model") or deps.default_image_model)
    user_id = int(req.get("user_id") or 0)

    tried: set[str] = set()
    for attempt in range(2):
        acc_id = deps.account_for_image(user_id, exclude=tried if tried else None)
        if acc_id is None:
            return {"error": "accounts_unavailable"}
        tried.add(acc_id)
        project_id = await deps.ensure_user_project(user_id, account_id=acc_id)
        try:
            async with deps.account_pool.image_slot(acc_id):
                result = await deps.client_for_acc(acc_id).generate_images(
                    prompt,
                    aspect_ratio=aspect_ratio,
                    num_images=num_images,
                    project_id=project_id,
                    image_model=image_model,
                )
        except Exception:
            deps.log.exception("backend gen failed (account %s, attempt %d)", acc_id, attempt)
            deps.account_pool.mark_failure(acc_id)
            if attempt == 0:
                continue
            return {"error": "generation failed"}
        if "error" in result:
            deps.mark_image_account_failure(acc_id, result)
            if attempt == 0 and result.get("error_type") != "prompt_rejected":
                continue
            return {"error": str(result.get("error"))[:300]}
        pairs = deps.result_pairs(result)
        if not pairs:
            if attempt == 0:
                continue
            return {"error": "nothing_returned"}
        deps.account_pool.mark_success(acc_id)
        return {
            "images": [{"url": url, "img": img} for url, img in pairs],
            "account_id": acc_id,
            "project_id": project_id,
        }
    return {"error": "generation failed"}


async def generate_i2i(deps: Any, req: dict) -> dict:
    """Run image-to-image on the user's uploaded photo and return URLs."""
    prompt = str(req.get("prompt") or "").strip()
    if len(prompt) < 3:
        return {"error": "empty prompt"}
    image_b64 = req.get("image_b64")
    if not image_b64:
        return {"error": "missing image"}
    try:
        data = base64.b64decode(image_b64)
    except Exception:
        return {"error": "bad image"}
    num_images = max(1, min(int(req.get("num_images") or 1), 8))
    aspect_ratio = str(req.get("aspect_ratio") or "portrait")
    image_model = str(req.get("image_model") or deps.default_image_model)
    user_id = int(req.get("user_id") or 0)

    tried: set[str] = set()
    last_error = "generation failed"
    for attempt in range(2):
        acc_id = deps.account_for_image(
            user_id, prefer_image_only=True, exclude=tried if tried else None,
        )
        if acc_id is None:
            return {"error": "accounts_unavailable" if not tried else last_error}
        tried.add(acc_id)
        project_id = await deps.ensure_user_project(user_id, account_id=acc_id)
        try:
            source = await deps.keeper_for_acc(acc_id).upload_image(
                data, filename=f"tg_{user_id}.png", project_id=project_id
            )
        except Exception:
            deps.log.exception("backend upload_image failed (account %s, attempt %d)", acc_id, attempt)
            deps.account_pool.mark_failure(acc_id)
            last_error = "upload failed"
            continue
        if not source:
            last_error = "upload failed"
            continue
        upload_project = source.pop("_project_id", None) or project_id
        inputs = deps.build_image_inputs(source, deps.load_edit_capture(deps.edit_capture_file))
        if not inputs:
            last_error = "no image inputs"
            continue
        try:
            async with deps.account_pool.image_slot(acc_id):
                result = await deps.client_for_acc(acc_id).generate_images(
                    prompt,
                    aspect_ratio=aspect_ratio,
                    num_images=num_images,
                    project_id=upload_project,
                    image_inputs=inputs,
                    allow_browser_fallback=False,
                    image_model=image_model,
                )
        except Exception:
            deps.log.exception("backend i2i failed (account %s, attempt %d)", acc_id, attempt)
            deps.account_pool.mark_failure(acc_id)
            last_error = "generation failed"
            continue
        if "error" in result:
            deps.mark_image_account_failure(acc_id, result)
            last_error = str(result.get("error"))[:300]
            if result.get("error_type") == "prompt_rejected":
                return {"error": last_error}
            continue
        pairs = deps.result_pairs(result)
        if not pairs:
            last_error = "nothing_returned"
            continue
        deps.account_pool.mark_success(acc_id)
        return {
            "images": [{"url": url, "img": img} for url, img in pairs],
            "account_id": acc_id,
            "project_id": upload_project,
        }
    return {"error": last_error}


async def generate_video_text(deps: Any, req: dict) -> dict:
    """Run text-to-video and return downloaded MP4 bytes as base64."""
    prompt = str(req.get("prompt") or "").strip()
    if len(prompt) < 3:
        return {"error": "empty prompt"}
    model_id = str(req.get("video_model") or "omni-flash-4s")
    meta = deps.video_model_meta(model_id)
    if not meta:
        return {"error": "bad video model"}
    aspect = str(req.get("aspect_ratio") or "portrait")
    if aspect not in {"portrait", "landscape"}:
        aspect = "portrait"
    user_id = int(req.get("user_id") or 0)

    acc_id = deps.account_for_video(user_id)
    if acc_id is None:
        return {"error": "accounts_unavailable"}
    project_id = await deps.ensure_user_project(user_id, account_id=acc_id)
    try:
        async with deps.account_pool.video_slot(acc_id):
            result = await deps.client_for_acc(acc_id).generate_video(
                prompt,
                model_key=meta["key"],
                aspect=aspect,
                project_id=project_id,
                reference_sources=None,
                operation="generate",
            )
    except Exception:
        deps.log.exception("backend text video generation failed (account %s)", acc_id)
        deps.mark_video_account_failure(acc_id)
        return {"error": "generation failed"}
    if "error" in result:
        deps.mark_video_account_failure(acc_id, result)
        return {"error": str(result.get("error"))[:300]}

    media_id = result.get("media_id")
    if not media_id:
        return {"error": "media_id missing"}
    try:
        video_bytes = await deps.client_for_acc(acc_id).fetch_video_bytes(media_id)
    except Exception:
        deps.log.exception("backend text video download failed (account %s)", acc_id)
        return {"error": "download failed"}
    if not video_bytes:
        return {"error": "download failed"}
    deps.account_pool.mark_success(acc_id)
    return {
        "videos": [{
            "video_b64": base64.b64encode(video_bytes).decode("ascii"),
            "media_id": media_id,
            "model_id": model_id,
            "aspect_ratio": aspect,
            "workflow_id": result.get("workflow_id"),
            "scene_id": result.get("scene_id"),
        }],
        "account_id": acc_id,
        "project_id": result.get("project_id") or project_id,
    }


async def generate_video_ingredients(deps: Any, req: dict) -> dict:
    """Run photo+prompt reference-to-video and return mp4 bytes as base64."""
    prompt = str(req.get("prompt") or "").strip()
    if len(prompt) < 3:
        return {"error": "empty prompt"}
    image_b64 = req.get("image_b64")
    if not image_b64:
        return {"error": "missing image"}
    try:
        data = base64.b64decode(image_b64)
    except Exception:
        return {"error": "bad image"}

    model_id = str(req.get("video_model") or deps.vid_ref_default_model)
    meta = deps.video_model_meta(model_id)
    if not meta:
        return {"error": "bad video model"}
    aspect = str(req.get("aspect_ratio") or "portrait")
    if aspect not in {"portrait", "landscape"}:
        aspect = "portrait"
    user_id = int(req.get("user_id") or 0)

    acc_id = deps.account_for_video(user_id)
    if acc_id is None:
        return {"error": "accounts_unavailable"}
    project_id = await deps.ensure_user_project(user_id, account_id=acc_id)
    try:
        source = await deps.keeper_for_acc(acc_id).upload_image(
            data, filename=f"tg_video_{user_id}.png", project_id=project_id
        )
    except Exception:
        deps.log.exception("backend video upload_image failed (account %s)", acc_id)
        return {"error": "upload failed"}
    if not source or not source.get("mediaId"):
        return {"error": "upload failed"}
    video_project_id = source.pop("_project_id", None) or project_id

    try:
        async with deps.account_pool.video_slot(acc_id):
            result = await deps.client_for_acc(acc_id).generate_video(
                prompt,
                model_key=meta["key"],
                aspect=aspect,
                project_id=video_project_id,
                reference_sources=[source],
                operation="generate",
            )
    except Exception:
        deps.log.exception("backend video generation failed (account %s)", acc_id)
        deps.mark_video_account_failure(acc_id)
        return {"error": "generation failed"}
    if "error" in result:
        deps.mark_video_account_failure(acc_id, result)
        return {"error": str(result.get("error"))[:300]}

    media_id = result.get("media_id")
    if not media_id:
        return {"error": "media_id missing"}
    video_bytes = await deps.client_for_acc(acc_id).fetch_video_bytes(media_id)
    if not video_bytes:
        return {"error": "download failed"}
    deps.account_pool.mark_success(acc_id)
    return {
        "videos": [{
            "video_b64": base64.b64encode(video_bytes).decode("ascii"),
            "media_id": media_id,
            "model_id": model_id,
            "aspect_ratio": aspect,
            "workflow_id": result.get("workflow_id"),
            "scene_id": result.get("scene_id"),
        }],
        "account_id": acc_id,
        "project_id": result.get("project_id") or video_project_id,
    }
