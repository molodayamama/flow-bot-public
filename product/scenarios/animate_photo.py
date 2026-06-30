from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping, Protocol


@dataclass(frozen=True)
class AnimatePhotoConfig:
    default_fmt: str
    min_price: int
    default_duration: int = 4
    default_quality: str = "lite"
    default_style: str = ""


class InteractionContext(Protocol):
    """Adapter boundary between the scenario and a concrete chat platform."""

    @property
    def state(self) -> MutableMapping[str, Any]:
        ...

    def clear_pending_edit(self) -> None:
        ...

    def clear_video_flow(self) -> None:
        ...

    def clear_image_flow(self) -> None:
        ...

    def current_video_model(self) -> str:
        ...

    def log_event(self, name: str, *, source: str) -> None:
        ...

    async def show_photo_input(self, *, edit: bool, price: int) -> Any:
        ...

    async def show_selected_photo_prompt(self) -> Any:
        ...

    async def show_need_photo(self) -> Any:
        ...

    async def upload_photo_source(self, file_id: str) -> Mapping[str, Any] | None:
        ...

    async def show_video_wizard(self, *, edit: bool) -> Any:
        ...

    async def generate_video(self, prompt: str) -> Any:
        ...


class AnimatePhotoScenario:
    """Shared state machine for the "animate photo" product flow."""

    wait_photo_key = "vanimate_photo"

    def __init__(self, config: AnimatePhotoConfig):
        self.config = config

    async def start_from_menu(self, ctx: InteractionContext, *, edit: bool) -> None:
        ctx.clear_pending_edit()
        ctx.clear_video_flow()
        ctx.clear_image_flow()
        self._seed_waiting_for_photo(ctx.state)
        await ctx.show_photo_input(edit=edit, price=self.config.min_price)

    async def start_from_generated_image(
        self,
        ctx: InteractionContext,
        *,
        source: Mapping[str, Any],
        account_id: str | None = None,
        project_id: str | None = None,
    ) -> None:
        ctx.clear_pending_edit()
        ctx.clear_video_flow()
        ctx.clear_image_flow()

        video_source = dict(source)
        if account_id:
            video_source.setdefault("_account_id", account_id)
        if project_id:
            video_source.setdefault("_project_id", project_id)

        st = ctx.state
        st["vphoto"] = video_source
        st["vstep"] = "vprompt_input"
        st["vawait"] = None
        st["vmode"] = "ingredients"
        self._seed_defaults(st)
        st["vmodel"] = ctx.current_video_model()
        ctx.log_event("animate_started", source="image")
        await ctx.show_selected_photo_prompt()

    async def remember_prompt_until_photo(
        self,
        ctx: InteractionContext,
        prompt: str,
    ) -> bool:
        if ctx.state.get("vawait") != self.wait_photo_key:
            return False
        ctx.state["vprompt"] = prompt
        await ctx.show_need_photo()
        return True

    async def attach_uploaded_photo(
        self,
        ctx: InteractionContext,
        *,
        file_id: str,
        caption: str = "",
    ) -> bool:
        st = ctx.state
        previous_step = st.get("vstep")
        if previous_step not in ("vprompt_input", "vnewwiz"):
            return False
        if caption:
            st["vprompt"] = caption

        source = await ctx.upload_photo_source(file_id)
        if not source:
            return True

        st["vphoto"] = dict(source)
        st["vmode"] = "ingredients"
        self._seed_defaults(st)
        st["vmodel"] = ctx.current_video_model()
        st["vstep"] = "vnewwiz"
        st["vawait"] = None
        await ctx.show_video_wizard(edit=(previous_step == "vnewwiz"))
        return True

    async def generate_from_ready_references(
        self,
        ctx: InteractionContext,
        prompt: str,
    ) -> bool:
        if ctx.state.get("vmode") != "ingredients":
            return False
        if not (ctx.state.get("ving_photos") or []):
            return False
        ctx.state["vawait"] = None
        await ctx.generate_video(prompt)
        return True

    def _seed_waiting_for_photo(self, st: MutableMapping[str, Any]) -> None:
        st["vstep"] = "vprompt_input"
        st["vawait"] = self.wait_photo_key
        st["vmode"] = "ingredients"
        self._seed_defaults(st)

    def _seed_defaults(self, st: MutableMapping[str, Any]) -> None:
        st.setdefault("vfmt", self.config.default_fmt)
        st.setdefault("vdur", self.config.default_duration)
        st.setdefault("vquality", self.config.default_quality)
        st.setdefault("vstyle", self.config.default_style)

