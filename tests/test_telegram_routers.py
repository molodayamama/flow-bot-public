from __future__ import annotations

import asyncio
import inspect
import unittest
from types import SimpleNamespace

from aiogram import Router

from channels.telegram.routers import commands as commands_router
from channels.telegram.routers import image_retry as image_retry_router
from channels.telegram.routers import ideas_hub as ideas_hub_router
from channels.telegram.routers import onboarding as onboarding_router
from channels.telegram.routers import photo_route as photo_route_router


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class RecordingDeps:
    """Fake CommandsDeps-shaped object: records every renderer call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def _make(self, name):
        async def renderer(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None

        return renderer


def _deps() -> tuple[commands_router.CommandsDeps, RecordingDeps]:
    rec = RecordingDeps()
    deps = commands_router.CommandsDeps(
        show_main_menu=rec._make("show_main_menu"),
        show_help_screen=rec._make("show_help_screen"),
        show_referral_screen=rec._make("show_referral_screen"),
        show_balance=rec._make("show_balance"),
    )
    return deps, rec


def _handler_commands(handler) -> set[str]:
    """Extract the command names a registered handler's Command filter matches."""

    names: set[str] = set()
    for filter_object in handler.filters or ():
        commands = getattr(filter_object.callback, "commands", None) or ()
        for command in commands:
            names.add(str(getattr(command, "command", command)))
    return names


class CommandsRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deps, self.rec = _deps()
        self.router = commands_router.create_router(self.deps)

    def test_creates_router_with_four_command_handlers(self) -> None:
        self.assertIsInstance(self.router, Router)
        handlers = self.router.message.handlers
        self.assertEqual(len(handlers), 4)
        self.assertEqual(self.router.callback_query.handlers, [])

        matched = [_handler_commands(h) for h in handlers]
        self.assertIn({"menu"}, matched)
        self.assertIn({"help"}, matched)
        self.assertIn({"referral", "ref"}, matched)
        self.assertIn({"balance"}, matched)

    def _call(self, commands: set[str], user_id: int = 42):
        for handler in self.router.message.handlers:
            if _handler_commands(handler) == commands:
                message = SimpleNamespace(from_user=SimpleNamespace(id=user_id))
                run(handler.callback(message))
                return message
        raise AssertionError(f"no handler for {commands}")

    def test_menu_delegates_with_ensure_kb(self) -> None:
        message = self._call({"menu"})
        name, args, kwargs = self.rec.calls[-1]
        self.assertEqual(name, "show_main_menu")
        self.assertEqual(args, (message,))
        self.assertEqual(kwargs, {"user_id": 42, "ensure_kb": True})

    def test_help_delegates_without_edit(self) -> None:
        message = self._call({"help"})
        name, args, kwargs = self.rec.calls[-1]
        self.assertEqual(name, "show_help_screen")
        self.assertEqual(args, (message,))
        self.assertEqual(kwargs, {"edit": False})

    def test_referral_delegates_with_user_id(self) -> None:
        message = self._call({"referral", "ref"})
        name, args, kwargs = self.rec.calls[-1]
        self.assertEqual(name, "show_referral_screen")
        self.assertEqual(args, (message,))
        self.assertEqual(kwargs, {"user_id": 42, "edit": False})

    def test_balance_delegates_with_user_id(self) -> None:
        message = self._call({"balance"})
        name, args, kwargs = self.rec.calls[-1]
        self.assertEqual(name, "show_balance")
        self.assertEqual(args, (message,))
        self.assertEqual(kwargs, {"user_id": 42, "edit": False})

    def test_deps_dataclass_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            self.deps.show_balance = None  # type: ignore[misc]

    # -- purity guard -------------------------------------------------------

    def test_router_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(commands_router)
        self.assertNotIn("flow_bot", src)


class RecordingPhotoRouteDeps:
    def __init__(self) -> None:
        self.pending_photo_routes: dict[int, dict[str, str]] = {}
        self.calls: list[tuple[str, tuple, dict]] = []

    async def prepare_photo_edit_from_file_id(self, *args, **kwargs):
        self.calls.append(("edit", args, kwargs))
        return True

    async def prepare_photo_video_from_file_id(self, *args, **kwargs):
        self.calls.append(("video", args, kwargs))
        return True


class FakeCallback:
    def __init__(self, data: str, user_id: int = 42) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = SimpleNamespace(
            edit_reply_markup=self._edit_reply_markup,
            edit_text=self._edit_text,
        )
        self.answers: list[tuple[tuple, dict]] = []
        self.edits: list[dict] = []
        self.text_edits: list[tuple[tuple, dict]] = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))

    async def _edit_reply_markup(self, **kwargs):
        self.edits.append(kwargs)

    async def _edit_text(self, *args, **kwargs):
        self.text_edits.append((args, kwargs))


class RecordingOnboardingDeps:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.workspaces: dict[int, dict] = {}

    def balance(self, user_id: int) -> int:
        self.calls.append(("balance", (user_id,), {}))
        return 7

    def reset_image_flow(self, *args, **kwargs):
        self.calls.append(("reset_image_flow", args, kwargs))

    def vid_clear(self, user_id: int) -> None:
        self.calls.append(("vid_clear", (user_id,), {}))

    def workspace(self, user_id: int) -> dict:
        self.calls.append(("workspace", (user_id,), {}))
        return self.workspaces.setdefault(user_id, {})

    def _make_async(self, name):
        async def renderer(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None

        return renderer


def _onboarding_deps() -> tuple[onboarding_router.OnboardingDeps, RecordingOnboardingDeps]:
    rec = RecordingOnboardingDeps()
    deps = onboarding_router.OnboardingDeps(
        balance=rec.balance,
        show_main_menu=rec._make_async("show_main_menu"),
        reset_image_flow=rec.reset_image_flow,
        show_prompt_picker=rec._make_async("show_prompt_picker"),
        vid_clear=rec.vid_clear,
        show_video_prompt_input=rec._make_async("show_video_prompt_input"),
        workspace=rec.workspace,
    )
    return deps, rec


class OnboardingRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deps, self.rec = _onboarding_deps()
        self.router = onboarding_router.create_router(self.deps)
        self.handler = self.router.callback_query.handlers[0].callback

    def test_creates_ob_callback_router(self) -> None:
        self.assertIsInstance(self.router, Router)
        self.assertEqual(self.router.name, "tg-onboarding")
        self.assertEqual(len(self.router.callback_query.handlers), 1)
        self.assertEqual(self.router.callback_query.handlers[0].callback.__name__, "on_onboarding_action")

    def test_skip_delegates_to_main_menu(self) -> None:
        callback = FakeCallback("ob:skip")
        run(self.handler(callback))
        self.assertEqual(self.rec.calls[-1][0], "show_main_menu")
        self.assertEqual(self.rec.calls[-1][1], (callback.message,))
        self.assertEqual(self.rec.calls[-1][2], {"user_id": 42, "edit": True})

    def test_kind_choice_edits_step2_screen(self) -> None:
        callback = FakeCallback("ob:img")
        run(self.handler(callback))
        self.assertTrue(callback.answers)
        self.assertTrue(callback.text_edits)
        args, kwargs = callback.text_edits[-1]
        self.assertTrue(args[0])
        self.assertIn("reply_markup", kwargs)
        self.assertEqual(kwargs["parse_mode"], "HTML")

    def test_go_image_resets_and_opens_prompt_picker(self) -> None:
        callback = FakeCallback("ob:go:img")
        run(self.handler(callback))
        self.assertIn(("reset_image_flow", (42,), {}), self.rec.calls)
        self.assertEqual(self.rec.calls[-1][0], "show_prompt_picker")
        self.assertEqual(self.rec.calls[-1][2], {"user_id": 42, "edit": True})

    def test_go_video_clears_video_and_opens_video_prompt(self) -> None:
        callback = FakeCallback("ob:go:vid")
        run(self.handler(callback))
        self.assertIn(("vid_clear", (42,), {}), self.rec.calls)
        self.assertEqual(self.rec.calls[-1][0], "show_video_prompt_input")
        self.assertEqual(self.rec.calls[-1][2], {"user_id": 42, "edit": True})

    def test_go_photo_sets_photo_await_state(self) -> None:
        callback = FakeCallback("ob:go:photo")
        run(self.handler(callback))
        self.assertIn(("reset_image_flow", (42,), {"keep_last": False}), self.rec.calls)
        self.assertEqual(self.rec.workspaces[42]["await"], "photo")
        self.assertTrue(callback.text_edits)

    def test_onboarding_deps_dataclass_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            self.deps.balance = None  # type: ignore[misc]

    def test_onboarding_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(onboarding_router)
        self.assertNotIn("flow_bot", src)


class RecordingIdeasHubDeps:
    def __init__(self) -> None:
        self.workspaces: dict[int, dict] = {}
        self.calls: list[tuple[str, tuple, dict]] = []

    def workspace(self, user_id: int) -> dict:
        self.calls.append(("workspace", (user_id,), {}))
        return self.workspaces.setdefault(user_id, {})

    def _make_async(self, name):
        async def renderer(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None

        return renderer


def _ideas_hub_deps() -> tuple[ideas_hub_router.IdeasHubDeps, RecordingIdeasHubDeps]:
    rec = RecordingIdeasHubDeps()
    deps = ideas_hub_router.IdeasHubDeps(
        workspace=rec.workspace,
        show_ideas_root=rec._make_async("show_ideas_root"),
        edit_or_answer=rec._make_async("edit_or_answer"),
        render_guided_step=rec._make_async("render_guided_step"),
    )
    return deps, rec


class IdeasHubRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deps, self.rec = _ideas_hub_deps()
        self.router = ideas_hub_router.create_router(self.deps)
        self.handler = self.router.callback_query.handlers[0].callback

    def test_creates_ih_callback_router(self) -> None:
        self.assertIsInstance(self.router, Router)
        self.assertEqual(self.router.name, "tg-ideas-hub")
        self.assertEqual(len(self.router.callback_query.handlers), 1)
        self.assertEqual(self.router.callback_query.handlers[0].callback.__name__, "on_ideas_hub_action")

    def test_root_delegates_to_ideas_root(self) -> None:
        callback = FakeCallback("ih:root")
        run(self.handler(callback))
        self.assertEqual(self.rec.calls[-1][0], "show_ideas_root")
        self.assertEqual(self.rec.calls[-1][1], (callback.message,))
        self.assertEqual(self.rec.calls[-1][2], {"user_id": 42, "edit": True})

    def test_templates_sets_mode_and_edits_picker(self) -> None:
        callback = FakeCallback("ih:templates")
        run(self.handler(callback))
        self.assertEqual(self.rec.workspaces[42]["ideas_mode"], "templates")
        self.assertEqual(self.rec.calls[-1][0], "edit_or_answer")
        self.assertEqual(self.rec.calls[-1][1][0], callback.message)
        self.assertEqual(self.rec.calls[-1][2], {"parse_mode": "HTML"})

    def test_guided_sets_initial_state_and_renders_step(self) -> None:
        callback = FakeCallback("ih:guided")
        run(self.handler(callback))
        self.assertEqual(self.rec.workspaces[42]["ideas_mode"], "guided")
        self.assertEqual(self.rec.workspaces[42]["gp_step"], 0)
        self.assertEqual(self.rec.workspaces[42]["gp_answers"], {})
        self.assertEqual(self.rec.calls[-1][0], "render_guided_step")
        self.assertEqual(self.rec.calls[-1][2], {"user_id": 42})

    def test_ideas_hub_deps_dataclass_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            self.deps.workspace = None  # type: ignore[misc]

    def test_ideas_hub_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(ideas_hub_router)
        self.assertNotIn("flow_bot", src)


class RecordingImageRetryDeps:
    def __init__(self) -> None:
        self.workspaces: dict[int, dict] = {}
        self.calls: list[tuple[tuple, dict]] = []

    def workspace(self, user_id: int) -> dict:
        return self.workspaces.setdefault(user_id, {})

    async def generate_and_send(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return None


def _image_retry_deps() -> tuple[image_retry_router.ImageRetryDeps, RecordingImageRetryDeps]:
    rec = RecordingImageRetryDeps()
    deps = image_retry_router.ImageRetryDeps(
        workspace=rec.workspace,
        generate_and_send=rec.generate_and_send,
        default_image_model="default-model",
    )
    return deps, rec


class ImageRetryRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deps, self.rec = _image_retry_deps()
        self.router = image_retry_router.create_router(self.deps)
        self.handler = self.router.callback_query.handlers[0].callback

    def test_creates_exact_img_retry_callback_router(self) -> None:
        self.assertIsInstance(self.router, Router)
        self.assertEqual(self.router.name, "tg-image-retry")
        self.assertEqual(len(self.router.callback_query.handlers), 1)
        self.assertEqual(self.router.callback_query.handlers[0].callback.__name__, "on_img_retry")

    def test_stale_retry_alerts_without_generation(self) -> None:
        callback = FakeCallback("img:retry")
        run(self.handler(callback))
        self.assertEqual(callback.answers[-1][1], {"show_alert": True})
        self.assertEqual(self.rec.calls, [])

    def test_retry_delegates_to_generation_with_snapshot(self) -> None:
        self.rec.workspaces[42] = {
            "img_retry": {
                "prompt": "draw cat",
                "num_images": 2,
                "aspect_ratio": "portrait",
                "action": "imgn",
                "image_model": "model-a",
            }
        }
        callback = FakeCallback("img:retry")
        run(self.handler(callback))
        args, kwargs = self.rec.calls[-1]
        self.assertEqual(args, (callback.message, "draw cat"))
        self.assertEqual(kwargs, {
            "num_images": 2,
            "aspect_ratio": "portrait",
            "actor_id": 42,
            "action": "imgn",
            "image_model": "model-a",
        })

    def test_retry_uses_defaults_for_partial_snapshot(self) -> None:
        self.rec.workspaces[42] = {"img_retry": {"prompt": "draw dog"}}
        callback = FakeCallback("img:retry")
        run(self.handler(callback))
        _args, kwargs = self.rec.calls[-1]
        self.assertEqual(kwargs["num_images"], 1)
        self.assertEqual(kwargs["aspect_ratio"], "landscape")
        self.assertEqual(kwargs["action"], "gen")
        self.assertEqual(kwargs["image_model"], "default-model")

    def test_image_retry_deps_dataclass_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            self.deps.default_image_model = "x"  # type: ignore[misc]

    def test_image_retry_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(image_retry_router)
        self.assertNotIn("flow_bot", src)


def _photo_route_deps() -> tuple[photo_route_router.PhotoRouteDeps, RecordingPhotoRouteDeps]:
    rec = RecordingPhotoRouteDeps()
    deps = photo_route_router.PhotoRouteDeps(
        pending_photo_routes=rec.pending_photo_routes,
        prepare_photo_edit_from_file_id=rec.prepare_photo_edit_from_file_id,
        prepare_photo_video_from_file_id=rec.prepare_photo_video_from_file_id,
    )
    return deps, rec


class PhotoRouteRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deps, self.rec = _photo_route_deps()
        self.router = photo_route_router.create_router(self.deps)
        self.handler = self.router.callback_query.handlers[0].callback

    def test_creates_pr_callback_router(self) -> None:
        self.assertIsInstance(self.router, Router)
        self.assertEqual(self.router.name, "tg-photo-route")
        self.assertEqual(len(self.router.callback_query.handlers), 1)
        self.assertEqual(self.router.message.handlers, [])
        self.assertEqual(self.router.callback_query.handlers[0].callback.__name__, "on_photo_route_choice")

    def test_cancel_drops_pending_route_and_removes_markup(self) -> None:
        self.rec.pending_photo_routes[42] = {"file_id": "f1", "caption": "cap"}
        callback = FakeCallback("pr:cancel")
        run(self.handler(callback))
        self.assertNotIn(42, self.rec.pending_photo_routes)
        self.assertEqual(len(callback.answers[-1][0]), 1)
        self.assertEqual(callback.edits[-1], {"reply_markup": None})
        self.assertEqual(self.rec.calls, [])

    def test_stale_route_alerts_without_prepare_call(self) -> None:
        callback = FakeCallback("pr:img")
        run(self.handler(callback))
        self.assertEqual(callback.answers[-1][1], {"show_alert": True})
        self.assertEqual(self.rec.calls, [])

    def test_img_route_delegates_to_edit_prepare_as_generation(self) -> None:
        self.rec.pending_photo_routes[42] = {"file_id": "f1", "caption": "cap"}
        callback = FakeCallback("pr:img")
        run(self.handler(callback))
        self.assertNotIn(42, self.rec.pending_photo_routes)
        name, args, kwargs = self.rec.calls[-1]
        self.assertEqual(name, "edit")
        self.assertEqual(args, (callback.message,))
        self.assertEqual(kwargs, {
            "user_id": 42,
            "file_id": "f1",
            "caption": "cap",
            "as_generation": True,
        })

    def test_vid_route_delegates_to_video_prepare(self) -> None:
        self.rec.pending_photo_routes[42] = {"file_id": "f2", "caption": "video cap"}
        callback = FakeCallback("pr:vid")
        run(self.handler(callback))
        name, args, kwargs = self.rec.calls[-1]
        self.assertEqual(name, "video")
        self.assertEqual(args, (callback.message,))
        self.assertEqual(kwargs, {
            "user_id": 42,
            "file_id": "f2",
            "caption": "video cap",
        })

    def test_photo_route_deps_dataclass_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            self.deps.prepare_photo_edit_from_file_id = None  # type: ignore[misc]

    def test_photo_route_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(photo_route_router)
        self.assertNotIn("flow_bot", src)


if __name__ == "__main__":
    unittest.main()
