from __future__ import annotations

import asyncio
import inspect
import unittest
from types import SimpleNamespace

from aiogram import Router

from channels.telegram.routers import commands as commands_router
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
        self.message = SimpleNamespace(edit_reply_markup=self._edit_reply_markup)
        self.answers: list[tuple[tuple, dict]] = []
        self.edits: list[dict] = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))

    async def _edit_reply_markup(self, **kwargs):
        self.edits.append(kwargs)


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
