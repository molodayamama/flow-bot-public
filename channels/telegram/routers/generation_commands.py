"""Ideas-root and image-generation slash command router (Phase 6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aiogram import Router, types
from aiogram.filters import Command

from flow_core import clamp_num_images


@dataclass(frozen=True)
class GenerationCommandsDeps:
    """Injected state/render/generation helpers for /ideas and image commands."""

    reset_image_flow: Callable[..., Any]
    vid_clear: Callable[[int], Any]
    show_ideas_root: Callable[..., Awaitable[Any]]
    generate_and_send: Callable[..., Awaitable[Any]]
    mix_and_send: Callable[..., Awaitable[Any]]


def create_router(deps: GenerationCommandsDeps) -> Router:
    """Build the /ideas + /img /one /portrait /square /imgn /mix router."""

    router = Router(name="tg-generation-commands")

    @router.message(Command("ideas"))
    async def cmd_ideas(message: types.Message):
        user_id = message.from_user.id
        deps.reset_image_flow(user_id)
        deps.vid_clear(user_id)
        await deps.show_ideas_root(message, user_id=user_id, edit=False)


    @router.message(Command("img"))
    async def cmd_img(message: types.Message):
        prompt = " ".join(message.text.split()[1:]).strip()
        await deps.generate_and_send(message, prompt, num_images=4)


    @router.message(Command("one"))
    async def cmd_one(message: types.Message):
        prompt = " ".join(message.text.split()[1:]).strip()
        await deps.generate_and_send(message, prompt, num_images=1)


    @router.message(Command("portrait"))
    async def cmd_portrait(message: types.Message):
        prompt = " ".join(message.text.split()[1:]).strip()
        await deps.generate_and_send(message, prompt, num_images=2, aspect_ratio="portrait")


    @router.message(Command("square"))
    async def cmd_square(message: types.Message):
        prompt = " ".join(message.text.split()[1:]).strip()
        await deps.generate_and_send(message, prompt, num_images=2, aspect_ratio="square")


    @router.message(Command("imgn"))
    async def cmd_imgn(message: types.Message):
        """`/imgn N <промпт>` — N изображений (1–8)."""
        parts = message.text.split()
        n = clamp_num_images(parts[1]) if len(parts) > 1 else 4
        # Если первый аргумент был числом — это счётчик, иначе он часть промпта.
        if len(parts) > 1 and parts[1].lstrip("-").isdigit():
            prompt = " ".join(parts[2:]).strip()
        else:
            prompt = " ".join(parts[1:]).strip()
        await deps.generate_and_send(message, prompt, num_images=n)


    @router.message(Command("mix"))
    async def cmd_mix(message: types.Message):
        """`/mix <промпт>` — собрать картинку из выбранных «ингредиентов»."""
        prompt = " ".join(message.text.split()[1:]).strip()
        await deps.mix_and_send(message, prompt)

    return router
