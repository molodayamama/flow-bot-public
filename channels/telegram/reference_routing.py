"""Reference-photo routing for the Telegram adapter (Phase 11 core split).

Picks a healthy account that holds the reference photo(s) for ingredients/frames
video and re-uploads them transparently when the bound account is unavailable;
also handles image-edit failover (re-upload the original image to a different
account when the edit's rate-limit kicks in). Seamless: the user is never told
that an account was unavailable.

Extracted out of the flow_bot composition root; runtime singletons are injected
via :class:`ReferenceRoutingDeps` so this module never imports flow_bot.
Telegram adapter code (uses aiogram/aiohttp), not a platform-neutral core module.
"""

from __future__ import annotations

import aiohttp
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from flow_core import ImageRef, download_url
from product import video_reference


@dataclass(frozen=True)
class ReferenceRoutingDeps:
    log: Any
    bot_download: Callable[[str], Awaitable[Any]]
    account_pool: Any
    account_for_image: Callable[..., str | None]
    account_for_video: Callable[..., str | None]
    ensure_user_project: Callable[..., Awaitable[str | None]]
    keeper_for_acc: Callable[[str | None], Any]
    video_account_health_reason: Callable[..., str | None]


class ReferenceRouting:
    def __init__(self, deps: ReferenceRoutingDeps) -> None:
        self._d = deps
        self._video_reference_sources = video_reference.video_reference_sources
        self._video_reference_account_id = video_reference.video_reference_account_id

    async def _download_bytes(self, file_id: str) -> bytes:
        buf = await self._d.bot_download(file_id)
        return buf.read() if hasattr(buf, "read") else bytes(buf)

    async def download_ref_image_bytes(self, ref: ImageRef) -> bytes | None:
        d = self._d
        source = ref.source if isinstance(ref.source, dict) else {}
        tg_file_id = source.get("_tg_file_id")
        if isinstance(tg_file_id, str) and tg_file_id:
            try:
                return await self._download_bytes(tg_file_id)
            except Exception:
                d.log.exception("download failover tg image failed")

        url = download_url(source)
        if not url:
            return None
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=60)) as r:
                    if r.status == 200:
                        return await r.read()
                    d.log.warning("download failover image returned HTTP %s", r.status)
        except Exception:
            d.log.exception("download failover image failed")
        return None

    async def reupload_ref_for_edit_failover(
        self, ref: ImageRef, user_id: int, *, current_account_id: str | None,
    ) -> ImageRef | None:
        d = self._d
        if not current_account_id:
            return None
        acc_id = d.account_for_image(
            user_id, prefer_image_only=True, exclude={current_account_id},
        )
        if not acc_id or acc_id == current_account_id:
            return None

        data = await self.download_ref_image_bytes(ref)
        if not data:
            return None
        project_id = await d.ensure_user_project(user_id, account_id=acc_id)
        try:
            source = await d.keeper_for_acc(acc_id).upload_image(
                data, filename=f"failover_{user_id}.png",
            )
        except Exception:
            d.log.exception("failover upload_image failed")
            source = None
        if not source or not source.get("mediaId"):
            return None

        source.setdefault("_project_id", project_id)
        if isinstance(ref.source, dict) and ref.source.get("_tg_file_id"):
            source.setdefault("_tg_file_id", ref.source["_tg_file_id"])
        upload_project = source.pop("_project_id", None) or project_id
        return ImageRef(
            user_id=user_id,
            project_id=upload_project,
            source=source,
            prompt=ref.prompt,
            aspect_ratio=ref.aspect_ratio,
            account_id=acc_id,
        )

    async def reupload_refs_for_edit_failover(
        self, refs: list[ImageRef], user_id: int, *, current_account_id: str | None,
    ) -> list[ImageRef] | None:
        """Move an image-reference group to one alternate account atomically."""
        d = self._d
        clean = [ref for ref in refs if isinstance(ref, ImageRef) and ref.user_id == user_id]
        if not clean or not current_account_id:
            return None
        acc_id = d.account_for_image(
            user_id, prefer_image_only=True, exclude={current_account_id},
        )
        if not acc_id or acc_id == current_account_id:
            return None
        project_id = await d.ensure_user_project(user_id, account_id=acc_id)
        moved: list[ImageRef] = []
        for index, ref in enumerate(clean):
            data = await self.download_ref_image_bytes(ref)
            if not data:
                return None
            try:
                source = await d.keeper_for_acc(acc_id).upload_image(
                    data, filename=f"failover_{user_id}_{index}.png", project_id=project_id,
                )
            except Exception:
                d.log.exception("group failover upload_image failed")
                return None
            if not source or not source.get("mediaId"):
                return None
            source.setdefault("_project_id", project_id)
            if isinstance(ref.source, dict) and ref.source.get("_tg_file_id"):
                source.setdefault("_tg_file_id", ref.source["_tg_file_id"])
            moved.append(ImageRef(
                user_id=user_id,
                project_id=source.pop("_project_id", None) or project_id,
                source=source,
                prompt=ref.prompt,
                aspect_ratio=ref.aspect_ratio,
                account_id=acc_id,
            ))
        return moved

    async def reupload_reference_source(
        self, src: dict, *, user_id: int, acc_id: str, project_id: str | None
    ) -> dict | None:
        """Re-upload a reference photo to another account from its stored Telegram
        file id. Returns the new source dict (with _account_id/_project_id) or None."""
        d = self._d
        tg_file_id = src.get("_tg_file_id") if isinstance(src, dict) else None
        if not tg_file_id:
            return None
        try:
            data = await self._download_bytes(tg_file_id)
            new_src = await d.keeper_for_acc(acc_id).upload_image(
                data, filename=f"tg_{user_id}.png", project_id=project_id
            )
        except Exception:
            d.log.exception("re-upload reference photo failed")
            return None
        if not new_src or not new_src.get("mediaId"):
            return None
        new_src.setdefault("_tg_file_id", tg_file_id)
        new_src.setdefault("_project_id", project_id)
        new_src.setdefault("_account_id", acc_id)
        return new_src

    async def ensure_reference_on_healthy_account(
        self, st: dict, vmode: str, *, user_id: int, model_id: str, min_credits: int,
        exclude: set[str] | None = None, force_reupload: bool = False,
    ) -> str | None:
        """Pick a healthy account that holds the reference photo(s), re-uploading
        them transparently if the bound account isn't ready. Seamless: the user is
        never told that an account was unavailable. Returns None only if the whole
        pool is unusable for video."""
        d = self._d
        excluded = set(exclude or set())
        sources = self._video_reference_sources(st, vmode)
        if not sources:
            return d.account_for_video(
                user_id, model_id=model_id, min_credits=min_credits, exclude=excluded,
            )

        bound = self._video_reference_account_id(
            st, vmode, is_reference_usable=d.account_pool.is_reference_usable
        )
        if (
            bound and not force_reupload and bound not in excluded
            and not d.video_account_health_reason(bound, model_id, min_credits)
        ):
            return bound  # bound account is healthy — use the existing upload

        target = d.account_for_video(
            user_id, model_id=model_id, min_credits=min_credits, exclude=excluded,
        )
        if target is None:
            # Whole pool unusable. Fall back to the bound account if it at least
            # has usable media there (better to try than to refuse).
            return None if force_reupload else bound
        if target == bound:
            return target

        project_id = await d.ensure_user_project(user_id, account_id=target)
        reuploaded: list[dict] = []
        for src in sources:
            new_src = await self.reupload_reference_source(
                src, user_id=user_id, acc_id=target, project_id=project_id
            )
            if not new_src:
                # Can't move this photo (no file id / upload failed). Keep the bound
                # account if any — generation may still work there.
                return None if force_reupload else (bound or target)
            reuploaded.append(new_src)

        if vmode == "ingredients":
            st["ving_photos"] = reuploaded
        elif vmode == "frames":
            st["vfrm_start"] = reuploaded[0]
            if len(reuploaded) > 1:
                st["vfrm_end"] = reuploaded[1]
        d.log.info("🔁 video reference re-uploaded to healthy account %s (was %s)", target, bound)
        return target
