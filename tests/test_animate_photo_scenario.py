from __future__ import annotations

import unittest

from product.scenarios.animate_photo import AnimatePhotoConfig, AnimatePhotoScenario


class FakeAnimateContext:
    def __init__(self, source=None):
        self._state = {"await": "prompt", "step": "wizard", "pending_prompt": "old"}
        self.source = source if source is not None else {"mediaId": "m1"}
        self.calls: list[tuple] = []

    @property
    def state(self):
        return self._state

    def clear_pending_edit(self) -> None:
        self.calls.append(("clear_pending_edit",))

    def clear_video_flow(self) -> None:
        self.calls.append(("clear_video_flow",))
        for key in list(self._state):
            if key.startswith("v"):
                self._state.pop(key, None)

    def clear_image_flow(self) -> None:
        self.calls.append(("clear_image_flow",))
        self._state["await"] = None
        self._state["step"] = None
        self._state.pop("pending_prompt", None)

    def current_video_model(self) -> str:
        return "omni-flash-4s"

    def log_event(self, name: str, *, source: str) -> None:
        self.calls.append(("log_event", name, source))

    async def show_photo_input(self, *, edit: bool, price: int) -> None:
        self.calls.append(("show_photo_input", edit, price))

    async def show_selected_photo_prompt(self) -> None:
        self.calls.append(("show_selected_photo_prompt",))

    async def show_need_photo(self) -> None:
        self.calls.append(("show_need_photo",))

    async def upload_photo_source(self, file_id: str):
        self.calls.append(("upload_photo_source", file_id))
        return self.source

    async def show_video_wizard(self, *, edit: bool) -> None:
        self.calls.append(("show_video_wizard", edit))

    async def generate_video(self, prompt: str) -> None:
        self.calls.append(("generate_video", prompt))


class AnimatePhotoScenarioTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.scenario = AnimatePhotoScenario(
            AnimatePhotoConfig(default_fmt="port", min_price=50)
        )

    async def test_start_from_menu_waits_for_photo_and_clears_image_flow(self) -> None:
        ctx = FakeAnimateContext()

        await self.scenario.start_from_menu(ctx, edit=True)

        self.assertEqual(ctx.state["vawait"], "vanimate_photo")
        self.assertEqual(ctx.state["vstep"], "vprompt_input")
        self.assertEqual(ctx.state["vmode"], "ingredients")
        self.assertEqual(ctx.state["vfmt"], "port")
        self.assertIsNone(ctx.state["await"])
        self.assertIsNone(ctx.state["step"])
        self.assertNotIn("pending_prompt", ctx.state)
        self.assertIn(("show_photo_input", True, 50), ctx.calls)

    async def test_generated_image_becomes_photo_prompt_state(self) -> None:
        ctx = FakeAnimateContext()

        await self.scenario.start_from_generated_image(
            ctx,
            source={"mediaId": "m1"},
            account_id="acc1",
            project_id="proj1",
        )

        self.assertEqual(ctx.state["vphoto"]["mediaId"], "m1")
        self.assertEqual(ctx.state["vphoto"]["_account_id"], "acc1")
        self.assertEqual(ctx.state["vphoto"]["_project_id"], "proj1")
        self.assertEqual(ctx.state["vstep"], "vprompt_input")
        self.assertEqual(ctx.state["vmodel"], "omni-flash-4s")
        self.assertIn(("log_event", "animate_started", "image"), ctx.calls)
        self.assertIn(("show_selected_photo_prompt",), ctx.calls)

    async def test_text_before_photo_is_remembered(self) -> None:
        ctx = FakeAnimateContext()
        await self.scenario.start_from_menu(ctx, edit=False)

        handled = await self.scenario.remember_prompt_until_photo(ctx, "slow zoom")

        self.assertTrue(handled)
        self.assertEqual(ctx.state["vprompt"], "slow zoom")
        self.assertIn(("show_need_photo",), ctx.calls)

    async def test_uploaded_photo_opens_video_wizard_without_aiogram(self) -> None:
        ctx = FakeAnimateContext(source={"mediaId": "m2"})
        await self.scenario.start_from_menu(ctx, edit=False)
        ctx.state["vprompt"] = "make it move"

        handled = await self.scenario.attach_uploaded_photo(
            ctx,
            file_id="tg-file",
            caption="",
        )

        self.assertTrue(handled)
        self.assertEqual(ctx.state["vphoto"], {"mediaId": "m2"})
        self.assertEqual(ctx.state["vstep"], "vnewwiz")
        self.assertIsNone(ctx.state["vawait"])
        self.assertEqual(ctx.state["vprompt"], "make it move")
        self.assertIn(("upload_photo_source", "tg-file"), ctx.calls)
        self.assertIn(("show_video_wizard", False), ctx.calls)

    async def test_ready_reference_prompt_uses_generation_context(self) -> None:
        ctx = FakeAnimateContext()
        ctx.state.update({"vmode": "ingredients", "ving_photos": [{"mediaId": "m1"}], "vawait": "ving_photo"})

        handled = await self.scenario.generate_from_ready_references(ctx, "wave")

        self.assertTrue(handled)
        self.assertIsNone(ctx.state["vawait"])
        self.assertIn(("generate_video", "wave"), ctx.calls)


if __name__ == "__main__":
    unittest.main()

