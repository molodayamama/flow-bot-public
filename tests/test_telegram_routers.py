from __future__ import annotations

import asyncio
import inspect
import unittest
from types import SimpleNamespace

from aiogram import Router

from channels.telegram.routers import commands as commands_router


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


if __name__ == "__main__":
    unittest.main()
