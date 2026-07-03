from __future__ import annotations

import asyncio
import inspect
import unittest
from collections import defaultdict
from types import SimpleNamespace

from aiogram import Router

from channels.telegram.routers import animate as animate_router
from channels.telegram.routers import agent as agent_router
from channels.telegram.routers import commands as commands_router
from channels.telegram.routers import edit_settings as edit_settings_router
from channels.telegram.routers import image_action as image_action_router
from channels.telegram.routers import ideas_flow as ideas_flow_router
from channels.telegram.routers import image_retry as image_retry_router
from channels.telegram.routers import ideas_hub as ideas_hub_router
from channels.telegram.routers import onboarding as onboarding_router
from channels.telegram.routers import menu as menu_router
from channels.telegram.routers import photo_route as photo_route_router
from channels.telegram.routers import video_upload as video_upload_router
from channels.telegram.routers import wizard as wizard_router
from flow_core import action_callback_data


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
        self.message_answers: list[tuple[tuple, dict]] = []
        self.replies: list[tuple[tuple, dict]] = []
        self.deletes: int = 0
        self.message.answer = self._message_answer
        self.message.delete = self._message_delete
        self.message.reply = self._message_reply

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))

    async def _edit_reply_markup(self, **kwargs):
        self.edits.append(kwargs)

    async def _edit_text(self, *args, **kwargs):
        self.text_edits.append((args, kwargs))

    async def _message_answer(self, *args, **kwargs):
        self.message_answers.append((args, kwargs))

    async def _message_delete(self):
        self.deletes += 1

    async def _message_reply(self, *args, **kwargs):
        self.replies.append((args, kwargs))


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


class FakeAnimateScenario:
    def __init__(self, rec) -> None:
        self.rec = rec

    async def start_from_generated_image(self, *args, **kwargs):
        self.rec.calls.append(("start_from_generated_image", args, kwargs))


class RecordingAnimateDeps:
    def __init__(self) -> None:
        self.registry: dict[str, object] = {}
        self.calls: list[tuple[str, tuple, dict]] = []

    def animate_photo_scenario(self):
        self.calls.append(("animate_photo_scenario", (), {}))
        return FakeAnimateScenario(self)

    def context_factory(self, message, user_id: int):
        self.calls.append(("context_factory", (message, user_id), {}))
        return {"message": message, "user_id": user_id}


def _animate_deps() -> tuple[animate_router.AnimateDeps, RecordingAnimateDeps]:
    rec = RecordingAnimateDeps()
    deps = animate_router.AnimateDeps(
        image_registry=rec.registry,
        animate_photo_scenario=rec.animate_photo_scenario,
        context_factory=rec.context_factory,
    )
    return deps, rec


class AnimateRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deps, self.rec = _animate_deps()
        self.router = animate_router.create_router(self.deps)
        self.handler = self.router.callback_query.handlers[0].callback

    def test_creates_an_callback_router(self) -> None:
        self.assertIsInstance(self.router, Router)
        self.assertEqual(self.router.name, "tg-animate")
        self.assertEqual(len(self.router.callback_query.handlers), 1)
        self.assertEqual(self.router.callback_query.handlers[0].callback.__name__, "on_animate_action")

    def test_generated_image_starts_animate_scenario(self) -> None:
        self.rec.registry["tok"] = SimpleNamespace(
            user_id=42,
            source={"mediaId": "m1"},
            account_id="acc1",
            project_id="proj1",
        )
        callback = FakeCallback("an:img:tok")
        run(self.handler(callback))
        self.assertTrue(callback.answers)
        self.assertEqual([c[0] for c in self.rec.calls], [
            "animate_photo_scenario",
            "context_factory",
            "start_from_generated_image",
        ])
        start = self.rec.calls[-1]
        self.assertEqual(start[2]["source"], {"mediaId": "m1"})
        self.assertEqual(start[2]["account_id"], "acc1")
        self.assertEqual(start[2]["project_id"], "proj1")

    def test_generated_image_wrong_owner_expires(self) -> None:
        self.rec.registry["tok"] = SimpleNamespace(user_id=7, source={}, account_id=None, project_id=None)
        callback = FakeCallback("an:img:tok")
        run(self.handler(callback))
        self.assertEqual(callback.answers[-1][1], {"show_alert": True})
        self.assertFalse(self.rec.calls)

    def test_unknown_animate_callback_is_acknowledged(self) -> None:
        callback = FakeCallback("an:unknown")
        run(self.handler(callback))
        self.assertTrue(callback.answers)

    def test_animate_deps_dataclass_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            self.deps.image_registry = None  # type: ignore[misc]

    def test_animate_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(animate_router)
        self.assertNotIn("flow_bot", src)


class RecordingWizardDeps:
    def __init__(self) -> None:
        self.workspaces: dict[int, dict] = {}
        self.calls: list[tuple[str, tuple, dict]] = []
        self.boost_result: str | None = "boosted"

    def workspace(self, user_id: int) -> dict:
        self.calls.append(("workspace", (user_id,), {}))
        return self.workspaces.setdefault(user_id, {})

    def clamp_num_images(self, raw: str) -> int:
        self.calls.append(("clamp_num_images", (raw,), {}))
        return int(raw)

    def image_model_meta(self, model: str):
        self.calls.append(("image_model_meta", (model,), {}))
        return {"id": model} if model == "model-ok" else None

    def reset_image_flow(self, *args, **kwargs):
        self.calls.append(("reset_image_flow", args, kwargs))
        self.workspaces.setdefault(args[0], {}).clear()

    async def boost_prompt_with_gemini(self, prompt: str) -> str | None:
        self.calls.append(("boost_prompt_with_gemini", (prompt,), {}))
        return self.boost_result

    async def generate_and_send(self, *args, **kwargs):
        self.calls.append(("generate_and_send", args, kwargs))

    def fmt_to_aspect(self, fmt: str) -> str:
        self.calls.append(("fmt_to_aspect", (fmt,), {}))
        return {"port": "portrait", "land": "landscape"}.get(fmt, fmt)

    def log_event(self, *args, **kwargs):
        self.calls.append(("log_event", args, kwargs))

    def _make_async(self, name):
        async def renderer(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None

        return renderer


def _wizard_deps() -> tuple[wizard_router.WizardDeps, RecordingWizardDeps]:
    rec = RecordingWizardDeps()
    deps = wizard_router.WizardDeps(
        workspace=rec.workspace,
        clamp_num_images=rec.clamp_num_images,
        image_model_meta=rec.image_model_meta,
        reset_image_flow=rec.reset_image_flow,
        show_main_menu=rec._make_async("show_main_menu"),
        show_wizard=rec._make_async("show_wizard"),
        show_prompt_picker=rec._make_async("show_prompt_picker"),
        boost_prompt_with_gemini=rec.boost_prompt_with_gemini,
        generate_and_send=rec.generate_and_send,
        fmt_to_aspect=rec.fmt_to_aspect,
        log_event=rec.log_event,
        quick_ideas=("idea1", "idea2", "idea3"),
        default_count=1,
        default_fmt="land",
        default_image_model="default-model",
    )
    return deps, rec


class WizardRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deps, self.rec = _wizard_deps()
        self.router = wizard_router.create_router(self.deps)
        self.handler = self.router.callback_query.handlers[0].callback

    def test_creates_w_callback_router(self) -> None:
        self.assertIsInstance(self.router, Router)
        self.assertEqual(self.router.name, "tg-wizard")
        self.assertEqual(len(self.router.callback_query.handlers), 1)
        self.assertEqual(self.router.callback_query.handlers[0].callback.__name__, "on_wizard_action")

    def test_cancel_clears_state_and_opens_menu(self) -> None:
        self.rec.workspaces[42] = {"pending_prompt": "x"}
        callback = FakeCallback("w:cancel")
        run(self.handler(callback))
        self.assertEqual(self.rec.workspaces[42], {})
        self.assertEqual(self.rec.calls[-1][0], "show_main_menu")

    def test_count_format_and_model_choices_rerender_wizard(self) -> None:
        for data, expected in (
            ("w:cnt:3", ("count", 3)),
            ("w:fmt:port", ("fmt", "port")),
            ("w:imodel:model-ok", ("imodel", "model-ok")),
        ):
            with self.subTest(data=data):
                callback = FakeCallback(data)
                run(self.handler(callback))
                self.assertEqual(self.rec.workspaces[42][expected[0]], expected[1])
                self.assertEqual(self.rec.calls[-1][0], "show_wizard")

    def test_history_choice_resets_flow_and_sets_pending_prompt(self) -> None:
        self.rec.workspaces[42] = {"_hist_cache": ["old prompt"]}
        callback = FakeCallback("w:hist:0")
        run(self.handler(callback))
        self.assertEqual(self.rec.workspaces[42]["pending_prompt"], "old prompt")
        self.assertIn(("reset_image_flow", (42,), {"keep_last": True}), self.rec.calls)
        self.assertEqual(self.rec.calls[-1][0], "show_wizard")

    def test_idea_choice_and_next_page(self) -> None:
        self.rec.workspaces[42] = {"ideas_pool": ["a", "b", "c"], "ideas_offset": 0}
        callback = FakeCallback("w:idea:1")
        run(self.handler(callback))
        self.assertEqual(self.rec.workspaces[42]["pending_prompt"], "b")
        self.assertEqual(self.rec.calls[-1][0], "show_wizard")

        callback = FakeCallback("w:idea:next")
        run(self.handler(callback))
        self.assertEqual(self.rec.workspaces[42]["ideas_offset"], 0)
        self.assertEqual(self.rec.calls[-1][0], "show_prompt_picker")

    def test_boost_prompt_updates_prompt_and_logs(self) -> None:
        self.rec.workspaces[42] = {"pending_prompt": "draft"}
        callback = FakeCallback("w:boost_prompt")
        run(self.handler(callback))
        self.assertEqual(self.rec.workspaces[42]["pending_prompt"], "boosted")
        self.assertTrue(any(c[0] == "log_event" for c in self.rec.calls))
        self.assertEqual(self.rec.calls[-1][0], "show_wizard")

    def test_go_with_pending_prompt_generates_image(self) -> None:
        self.rec.workspaces[42] = {
            "pending_prompt": "draw",
            "count": 2,
            "fmt": "port",
            "imodel": "model-ok",
        }
        callback = FakeCallback("w:go")
        run(self.handler(callback))
        self.assertIsNone(self.rec.workspaces[42]["pending_prompt"])
        gen = next(c for c in self.rec.calls if c[0] == "generate_and_send")
        self.assertEqual(gen[1], (callback.message, "draw"))
        self.assertEqual(gen[2]["num_images"], 2)
        self.assertEqual(gen[2]["aspect_ratio"], "portrait")
        self.assertEqual(gen[2]["actor_id"], 42)
        self.assertEqual(gen[2]["image_model"], "model-ok")

    def test_go_without_pending_prompt_asks_for_prompt(self) -> None:
        callback = FakeCallback("w:go")
        run(self.handler(callback))
        self.assertEqual(self.rec.workspaces[42]["await"], "prompt")
        self.assertEqual(self.rec.workspaces[42]["step"], "prompt")
        self.assertTrue(callback.text_edits)

    def test_wizard_deps_dataclass_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            self.deps.workspace = None  # type: ignore[misc]

    def test_wizard_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(wizard_router)
        self.assertNotIn("flow_bot", src)


class RecordingMenuDeps:
    def __init__(self) -> None:
        self.workspaces: dict[int, dict] = {}
        self.pending_edits: dict[int, object] = {}
        self.pending_photo_routes: dict[int, object] = {}
        self.admin_ids = {42}
        self.calls: list[tuple[str, tuple, dict]] = []
        self.robo_enabled = True

    def workspace(self, user_id: int) -> dict:
        self.calls.append(("workspace", (user_id,), {}))
        return self.workspaces.setdefault(user_id, {})

    def upsert_user(self, *args, **kwargs):
        self.calls.append(("upsert_user", args, kwargs))

    def log_event(self, *args, **kwargs):
        self.calls.append(("log_event", args, kwargs))

    def reset_image_flow(self, *args, **kwargs):
        self.calls.append(("reset_image_flow", args, kwargs))
        self.workspaces.setdefault(args[0], {}).clear()

    def clear_image_flow_keys(self, st: dict):
        self.calls.append(("clear_image_flow_keys", (st,), {}))
        st.pop("pending_prompt", None)

    def mp_jobs_text(self, platform: str) -> str:
        self.calls.append(("mp_jobs_text", (platform,), {}))
        return f"jobs:{platform}"

    def mp_jobs_kb(self, platform: str):
        self.calls.append(("mp_jobs_kb", (platform,), {}))
        return f"kb:{platform}"

    def mp_stamp_message(self, *args, **kwargs):
        self.calls.append(("mp_stamp_message", args, kwargs))

    def topup_copy(self, key: str) -> str:
        self.calls.append(("topup_copy", (key,), {}))
        return key

    def topup_kb(self, *args, **kwargs):
        self.calls.append(("topup_kb", args, kwargs))
        return "topup-kb"

    def topup_stars_kb(self, *args, **kwargs):
        self.calls.append(("topup_stars_kb", args, kwargs))
        return "stars-kb"

    def topup_robo_kb(self, *args, **kwargs):
        self.calls.append(("topup_robo_kb", args, kwargs))
        return "robo-kb"

    def robokassa_configured(self) -> bool:
        self.calls.append(("robokassa_configured", (), {}))
        return self.robo_enabled

    async def start_topup(self, *args, **kwargs):
        self.calls.append(("start_topup", args, kwargs))

    async def start_robokassa_topup(self, *args, **kwargs):
        self.calls.append(("start_robokassa_topup", args, kwargs))

    def _make_async(self, name):
        async def renderer(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None

        return renderer


def _menu_deps() -> tuple[menu_router.MenuDeps, RecordingMenuDeps]:
    rec = RecordingMenuDeps()
    deps = menu_router.MenuDeps(
        workspace=rec.workspace,
        pending_edits=rec.pending_edits,
        pending_photo_routes=rec.pending_photo_routes,
        admin_ids=rec.admin_ids,
        upsert_user=rec.upsert_user,
        log_event=rec.log_event,
        reset_image_flow=rec.reset_image_flow,
        clear_image_flow_keys=rec.clear_image_flow_keys,
        show_prompt_picker=rec._make_async("show_prompt_picker"),
        show_video_prompt_input=rec._make_async("show_video_prompt_input"),
        show_animate_photo_input=rec._make_async("show_animate_photo_input"),
        mp_jobs_text=rec.mp_jobs_text,
        mp_jobs_kb=rec.mp_jobs_kb,
        mp_stamp_message=rec.mp_stamp_message,
        show_ideas_root=rec._make_async("show_ideas_root"),
        repeat_last=rec._make_async("repeat_last"),
        show_balance=rec._make_async("show_balance"),
        topup_copy=rec.topup_copy,
        topup_kb=rec.topup_kb,
        topup_stars_kb=rec.topup_stars_kb,
        topup_robo_kb=rec.topup_robo_kb,
        robokassa_configured=rec.robokassa_configured,
        start_topup=rec.start_topup,
        start_robokassa_topup=rec.start_robokassa_topup,
        show_help_screen=rec._make_async("show_help_screen"),
        show_referral_screen=rec._make_async("show_referral_screen"),
        show_main_menu=rec._make_async("show_main_menu"),
        show_profile_screen=rec._make_async("show_profile_screen"),
        show_gallery=rec._make_async("show_gallery"),
        show_prompt_history=rec._make_async("show_prompt_history"),
        show_support_menu=rec._make_async("show_support_menu"),
        show_my_tickets=rec._make_async("show_my_tickets"),
    )
    return deps, rec


class MenuRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deps, self.rec = _menu_deps()
        self.router = menu_router.create_router(self.deps)
        self.handler = self.router.callback_query.handlers[0].callback

    def test_creates_m_callback_router(self) -> None:
        self.assertIsInstance(self.router, Router)
        self.assertEqual(self.router.name, "tg-menu")
        self.assertEqual(len(self.router.callback_query.handlers), 1)
        self.assertEqual(self.router.callback_query.handlers[0].callback.__name__, "on_menu_action")

    def test_gen_resets_image_flow_and_opens_prompt_picker(self) -> None:
        callback = FakeCallback("m:gen")
        run(self.handler(callback))
        self.assertIn(("reset_image_flow", (42,), {}), self.rec.calls)
        self.assertEqual(self.rec.calls[-1][0], "show_prompt_picker")

    def test_vid_clears_pending_photo_and_opens_video_prompt(self) -> None:
        self.rec.pending_edits[42] = object()
        self.rec.pending_photo_routes[42] = object()
        callback = FakeCallback("m:vid")
        run(self.handler(callback))
        self.assertNotIn(42, self.rec.pending_edits)
        self.assertNotIn(42, self.rec.pending_photo_routes)
        self.assertEqual(self.rec.calls[-1][0], "show_video_prompt_input")

    def test_marketplace_sets_default_platform_and_edits_jobs(self) -> None:
        callback = FakeCallback("m:mp")
        run(self.handler(callback))
        self.assertEqual(self.rec.workspaces[42]["mp_platform"], "wb")
        self.assertTrue(callback.text_edits)
        self.assertEqual(self.rec.calls[-1][0], "mp_stamp_message")

    def test_topup_logs_and_uses_admin_keyboard(self) -> None:
        callback = FakeCallback("m:topup")
        run(self.handler(callback))
        self.assertTrue(any(c[0] == "log_event" for c in self.rec.calls))
        self.assertIn(("topup_kb", (), {"is_admin": True}), self.rec.calls)
        self.assertTrue(callback.text_edits)

    def test_robokassa_disabled_alerts(self) -> None:
        self.rec.robo_enabled = False
        callback = FakeCallback("m:pay:robo")
        run(self.handler(callback))
        self.assertEqual(callback.answers[-1][1], {"show_alert": True})

    def test_pack_and_robo_pack_delegate_to_payment_helpers(self) -> None:
        callback = FakeCallback("m:pack:basic")
        run(self.handler(callback))
        self.assertEqual(self.rec.calls[-1][0], "start_topup")
        self.assertEqual(self.rec.calls[-1][1], (callback, 42, "basic"))

        callback = FakeCallback("m:robo:basic")
        run(self.handler(callback))
        self.assertEqual(self.rec.calls[-1][0], "start_robokassa_topup")
        self.assertEqual(self.rec.calls[-1][1], (callback, 42, "basic"))

    def test_myphoto_and_menu_update_state(self) -> None:
        callback = FakeCallback("m:myphoto")
        run(self.handler(callback))
        self.assertEqual(self.rec.workspaces[42]["await"], "photo")
        self.assertTrue(callback.text_edits)

        self.rec.pending_edits[42] = object()
        self.rec.pending_photo_routes[42] = object()
        callback = FakeCallback("m:menu")
        run(self.handler(callback))
        self.assertIsNone(self.rec.workspaces[42]["await"])
        self.assertEqual(self.rec.calls[-1][0], "show_main_menu")

    def test_support_new_and_admin_reply_set_state(self) -> None:
        callback = FakeCallback("m:support:new")
        run(self.handler(callback))
        self.assertTrue(self.rec.workspaces[42]["support_await"])
        self.assertTrue(callback.text_edits)

        callback = FakeCallback("m:sreply:77")
        run(self.handler(callback))
        self.assertEqual(self.rec.workspaces[42]["admin_reply_ticket"], 77)
        self.assertTrue(callback.replies)

    def test_menu_deps_dataclass_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            self.deps.workspace = None  # type: ignore[misc]

    def test_menu_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(menu_router)
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


class RecordingIdeasFlowDeps:
    def __init__(self) -> None:
        self.workspaces: dict[int, dict] = {}
        self.calls: list[tuple[str, tuple, dict]] = []

    def workspace(self, user_id: int) -> dict:
        return self.workspaces.setdefault(user_id, {})

    def ideas_clear(self, st: dict, *, clear_photo: bool = False) -> None:
        self.calls.append(("ideas_clear", (st,), {"clear_photo": clear_photo}))
        st.clear()

    def tp_store_answer(self, st: dict, value: str) -> None:
        self.calls.append(("tp_store_answer", (st, value), {}))
        st.setdefault("answers", []).append(value)

    def log_event(self, *args, **kwargs) -> None:
        self.calls.append(("log_event", args, kwargs))

    def _make_async(self, name):
        async def renderer(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None

        return renderer


def _ideas_flow_deps() -> tuple[ideas_flow_router.IdeasFlowDeps, RecordingIdeasFlowDeps]:
    rec = RecordingIdeasFlowDeps()
    deps = ideas_flow_router.IdeasFlowDeps(
        workspace=rec.workspace,
        ideas_clear=rec.ideas_clear,
        tp_store_answer=rec.tp_store_answer,
        show_ideas_root=rec._make_async("show_ideas_root"),
        render_template_step=rec._make_async("render_template_step"),
        render_guided_step=rec._make_async("render_guided_step"),
        log_event=rec.log_event,
    )
    return deps, rec


class IdeasFlowRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deps, self.rec = _ideas_flow_deps()
        self.router = ideas_flow_router.create_router(self.deps)
        self.template_handler = self.router.callback_query.handlers[0].callback
        self.guided_handler = self.router.callback_query.handlers[1].callback

    def test_creates_tp_and_gp_callback_router(self) -> None:
        self.assertIsInstance(self.router, Router)
        self.assertEqual(self.router.name, "tg-ideas-flow")
        self.assertEqual([h.callback.__name__ for h in self.router.callback_query.handlers], [
            "on_template_action",
            "on_guided_picker_action",
        ])

    def test_template_open_initializes_state_and_logs(self) -> None:
        callback = FakeCallback("tp:tpl:product_card")
        run(self.template_handler(callback))
        st = self.rec.workspaces[42]
        self.assertEqual(st["tp_tpl"], "product_card")
        self.assertEqual(st["tp_step"], 0)
        self.assertEqual(st["tp_answers"], {})
        self.assertEqual(st["ideas_mode"], "templates")
        self.assertEqual(self.rec.calls[-1][0], "render_template_step")
        self.assertTrue(any(c[0] == "log_event" for c in self.rec.calls))

    def test_template_answer_stores_choice_and_renders_next_step(self) -> None:
        self.rec.workspaces[42] = {"tp_tpl": "product_card", "tp_step": 0}
        callback = FakeCallback("tp:ans:0")
        run(self.template_handler(callback))
        self.assertEqual(self.rec.calls[-2][0], "tp_store_answer")
        self.assertEqual(self.rec.calls[-1][0], "render_template_step")

    def test_template_missing_state_expires_to_root(self) -> None:
        callback = FakeCallback("tp:ans:0")
        run(self.template_handler(callback))
        self.assertEqual(callback.answers[-1][1], {"show_alert": True})
        self.assertEqual(self.rec.calls[-1][0], "show_ideas_root")

    def test_template_cancel_clears_state_and_opens_root(self) -> None:
        self.rec.workspaces[42] = {"tp_tpl": "product_card", "tp_step": 1}
        callback = FakeCallback("tp:cancel")
        run(self.template_handler(callback))
        self.assertEqual(self.rec.calls[-2][0], "ideas_clear")
        self.assertEqual(self.rec.calls[-1][0], "show_ideas_root")

    def test_guided_option_advances_state_and_renders(self) -> None:
        self.rec.workspaces[42] = {"gp_step": 0, "gp_answers": {}}
        callback = FakeCallback("gp:opt:0")
        run(self.guided_handler(callback))
        st = self.rec.workspaces[42]
        self.assertEqual(st["gp_step"], 1)
        self.assertTrue(st["gp_answers"])
        self.assertEqual(self.rec.calls[-1][0], "render_guided_step")

    def test_guided_missing_state_expires_to_root(self) -> None:
        callback = FakeCallback("gp:opt:0")
        run(self.guided_handler(callback))
        self.assertEqual(callback.answers[-1][1], {"show_alert": True})
        self.assertEqual(self.rec.calls[-1][0], "show_ideas_root")

    def test_guided_cancel_clears_state_and_opens_root(self) -> None:
        self.rec.workspaces[42] = {"gp_step": 1, "gp_answers": {"x": "y"}}
        callback = FakeCallback("gp:cancel")
        run(self.guided_handler(callback))
        self.assertEqual(self.rec.calls[-2][0], "ideas_clear")
        self.assertEqual(self.rec.calls[-1][0], "show_ideas_root")

    def test_ideas_flow_deps_dataclass_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            self.deps.workspace = None  # type: ignore[misc]

    def test_ideas_flow_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(ideas_flow_router)
        self.assertNotIn("flow_bot", src)


class RecordingAgentDeps:
    def __init__(self) -> None:
        self.workspaces: dict[int, dict] = {}
        self.calls: list[tuple[str, tuple, dict]] = []

    def workspace(self, user_id: int) -> dict:
        self.calls.append(("workspace", (user_id,), {}))
        return self.workspaces.setdefault(user_id, {})

    async def agent_pick(self, *args, **kwargs):
        self.calls.append(("agent_pick", args, kwargs))

    async def agent_improve_flow(self, *args, **kwargs):
        self.calls.append(("agent_improve_flow", args, kwargs))

    async def video_edit(self, *args, **kwargs):
        self.calls.append(("video_edit", args, kwargs))

    async def edit_or_answer(self, *args, **kwargs):
        self.calls.append(("edit_or_answer", args, kwargs))

    def agent_edit_instruction(self, prompt: str) -> str:
        self.calls.append(("agent_edit_instruction", (prompt,), {}))
        return f"edit:{prompt}"

    def _make_async(self, name):
        async def renderer(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None

        return renderer


def _agent_deps() -> tuple[agent_router.AgentDeps, RecordingAgentDeps]:
    rec = RecordingAgentDeps()
    deps = agent_router.AgentDeps(
        workspace=rec.workspace,
        agent_pick=rec.agent_pick,
        agent_improve_flow=rec.agent_improve_flow,
        show_new_video_wizard=rec._make_async("show_new_video_wizard"),
        show_wizard=rec._make_async("show_wizard"),
        show_edit_confirm=rec._make_async("show_edit_confirm"),
        video_edit=rec.video_edit,
        edit_or_answer=rec.edit_or_answer,
        agent_edit_instruction=rec.agent_edit_instruction,
    )
    return deps, rec


class AgentRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deps, self.rec = _agent_deps()
        self.router = agent_router.create_router(self.deps)
        self.handler = self.router.callback_query.handlers[0].callback

    def test_creates_ag_callback_router(self) -> None:
        self.assertIsInstance(self.router, Router)
        self.assertEqual(self.router.name, "tg-agent")
        self.assertEqual(len(self.router.callback_query.handlers), 1)
        self.assertEqual(self.router.callback_query.handlers[0].callback.__name__, "on_agent_action")

    def test_video_pick_delegates_to_agent_pick(self) -> None:
        callback = FakeCallback("ag:vpick:2")
        run(self.handler(callback))
        name, args, kwargs = self.rec.calls[-1]
        self.assertEqual(name, "agent_pick")
        self.assertEqual(args, (callback,))
        self.assertEqual(kwargs["user_id"], 42)
        self.assertEqual(kwargs["idx_str"], "2")
        self.assertEqual(kwargs["prompt_key"], "vprompt")

    def test_video_keep_clears_variants_and_renders_wizard(self) -> None:
        self.rec.workspaces[42] = {"ag_variants": [{"prompt": "x"}]}
        callback = FakeCallback("ag:vkeep")
        run(self.handler(callback))
        self.assertNotIn("ag_variants", self.rec.workspaces[42])
        self.assertEqual(self.rec.calls[-1][0], "show_new_video_wizard")
        self.assertEqual(self.rec.calls[-1][2], {"user_id": 42, "edit": True})

    def test_video_improve_delegates_with_video_edit_adapter(self) -> None:
        callback = FakeCallback("ag:vimprove")
        run(self.handler(callback))
        name, args, kwargs = self.rec.calls[-1]
        self.assertEqual(name, "agent_improve_flow")
        self.assertEqual(args, (callback,))
        self.assertEqual(kwargs["prompt_key"], "vprompt")
        self.assertEqual(kwargs["source"], "video")
        run(kwargs["edit_fn"](callback.message, "body", "kb", parse_mode="HTML"))
        self.assertEqual(self.rec.calls[-1][0], "video_edit")
        self.assertEqual(self.rec.calls[-1][1], (callback.message, "body", "kb", 42))

    def test_image_pick_and_keep_delegate_to_image_wizard(self) -> None:
        callback = FakeCallback("ag:pick:1")
        run(self.handler(callback))
        self.assertEqual(self.rec.calls[-1][0], "agent_pick")
        self.assertEqual(self.rec.calls[-1][2]["prompt_key"], "pending_prompt")

        self.rec.workspaces[42] = {"ag_variants": [{"prompt": "x"}]}
        callback = FakeCallback("ag:keep")
        run(self.handler(callback))
        self.assertNotIn("ag_variants", self.rec.workspaces[42])
        self.assertEqual(self.rec.calls[-1][0], "show_wizard")

    def test_image_improve_uses_edit_or_answer(self) -> None:
        callback = FakeCallback("ag:improve")
        run(self.handler(callback))
        self.assertEqual(self.rec.calls[-1][0], "agent_improve_flow")
        self.assertEqual(self.rec.calls[-1][2]["prompt_key"], "pending_prompt")
        self.assertIs(self.rec.calls[-1][2]["edit_fn"], self.deps.edit_or_answer)

    def test_edit_pick_keep_and_improve_delegate_to_edit_confirm(self) -> None:
        callback = FakeCallback("ag:epick:0")
        run(self.handler(callback))
        self.assertEqual(self.rec.calls[-1][0], "agent_pick")
        self.assertEqual(self.rec.calls[-1][2]["prompt_key"], "edit_instruction")

        self.rec.workspaces[42] = {"ag_variants": [{"prompt": "x"}]}
        callback = FakeCallback("ag:ekeep")
        run(self.handler(callback))
        self.assertNotIn("ag_variants", self.rec.workspaces[42])
        self.assertEqual(self.rec.calls[-1][0], "show_edit_confirm")

        callback = FakeCallback("ag:eimprove")
        run(self.handler(callback))
        self.assertEqual(self.rec.calls[-1][0], "agent_improve_flow")
        self.assertEqual(self.rec.calls[-1][2]["prompt_key"], "edit_instruction")
        self.assertIs(self.rec.calls[-1][2]["instruction_fn"], self.deps.agent_edit_instruction)

    def test_unknown_agent_callback_is_acknowledged(self) -> None:
        callback = FakeCallback("ag:unknown")
        run(self.handler(callback))
        self.assertTrue(callback.answers)

    def test_agent_deps_dataclass_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            self.deps.workspace = None  # type: ignore[misc]

    def test_agent_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(agent_router)
        self.assertNotIn("flow_bot", src)


class RecordingVideoUploadDeps:
    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled
        self.workspaces: dict[int, dict] = {}
        self.calls: list[tuple[str, tuple, dict]] = []

    def workspace(self, user_id: int) -> dict:
        self.calls.append(("workspace", (user_id,), {}))
        return self.workspaces.setdefault(user_id, {})

    def upload_video_edit_enabled(self) -> bool:
        self.calls.append(("upload_video_edit_enabled", (), {}))
        return self.enabled

    def vid_clear(self, user_id: int) -> None:
        self.calls.append(("vid_clear", (user_id,), {}))
        self.workspaces.setdefault(user_id, {}).clear()

    async def edit_or_answer(self, *args, **kwargs):
        self.calls.append(("edit_or_answer", args, kwargs))


def _video_upload_deps(
    *, enabled: bool = False,
) -> tuple[video_upload_router.VideoUploadDeps, RecordingVideoUploadDeps]:
    rec = RecordingVideoUploadDeps(enabled=enabled)
    deps = video_upload_router.VideoUploadDeps(
        workspace=rec.workspace,
        upload_video_edit_enabled=rec.upload_video_edit_enabled,
        vid_clear=rec.vid_clear,
        edit_or_answer=rec.edit_or_answer,
    )
    return deps, rec


class VideoUploadRouterTests(unittest.TestCase):
    def test_creates_vu_callback_router(self) -> None:
        deps, _ = _video_upload_deps()
        router = video_upload_router.create_router(deps)
        self.assertIsInstance(router, Router)
        self.assertEqual(router.name, "tg-video-upload")
        self.assertEqual(len(router.callback_query.handlers), 1)
        self.assertEqual(router.callback_query.handlers[0].callback.__name__, "on_video_upload_action")

    def test_start_disabled_alerts_without_state_change(self) -> None:
        deps, rec = _video_upload_deps(enabled=False)
        handler = video_upload_router.create_router(deps).callback_query.handlers[0].callback
        callback = FakeCallback("vu:start")
        run(handler(callback))
        self.assertEqual(callback.answers[-1][1], {"show_alert": True})
        self.assertNotIn("vmode", rec.workspaces[42])
        self.assertFalse(any(c[0] == "edit_or_answer" for c in rec.calls))

    def test_start_enabled_clears_video_state_and_asks_for_upload(self) -> None:
        deps, rec = _video_upload_deps(enabled=True)
        rec.workspaces[42] = {"old": "value"}
        handler = video_upload_router.create_router(deps).callback_query.handlers[0].callback
        callback = FakeCallback("vu:start")
        run(handler(callback))
        self.assertIn(("vid_clear", (42,), {}), rec.calls)
        self.assertEqual(rec.workspaces[42]["vmode"], "edit")
        self.assertEqual(rec.workspaces[42]["vawait"], "vu_video")
        self.assertEqual(rec.calls[-1][0], "edit_or_answer")
        self.assertTrue(callback.answers)

    def test_unknown_video_upload_callback_is_acknowledged(self) -> None:
        deps, _ = _video_upload_deps(enabled=True)
        handler = video_upload_router.create_router(deps).callback_query.handlers[0].callback
        callback = FakeCallback("vu:unknown")
        run(handler(callback))
        self.assertTrue(callback.answers)

    def test_video_upload_deps_dataclass_is_frozen(self) -> None:
        deps, _ = _video_upload_deps()
        with self.assertRaises(Exception):
            deps.workspace = None  # type: ignore[misc]

    def test_video_upload_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(video_upload_router)
        self.assertNotIn("flow_bot", src)


class RecordingEditSettingsDeps:
    def __init__(self) -> None:
        self.workspaces: dict[int, dict] = {}
        self.pending_edits: dict[int, str] = {}
        self.registry: dict[str, object] = {}
        self.calls: list[tuple[str, tuple, dict]] = []

    def workspace(self, user_id: int) -> dict:
        self.calls.append(("workspace", (user_id,), {}))
        return self.workspaces.setdefault(user_id, {})

    async def edit_and_send(self, *args, **kwargs) -> bool:
        self.calls.append(("edit_and_send", args, kwargs))
        return True

    def fmt_to_aspect(self, fmt: str) -> str:
        self.calls.append(("fmt_to_aspect", (fmt,), {}))
        return {"port": "portrait", "land": "landscape"}.get(fmt, fmt)

    def aspect_to_fmt(self, aspect: str) -> str:
        self.calls.append(("aspect_to_fmt", (aspect,), {}))
        return {"portrait": "port", "landscape": "land"}.get(aspect, "land")

    def image_model_meta(self, model: str):
        self.calls.append(("image_model_meta", (model,), {}))
        return {"id": model} if model in {"default-model", "pro-model"} else None

    def edit_confirm_kb(self, *args, **kwargs):
        self.calls.append(("edit_confirm_kb", args, kwargs))
        return f"confirm:{args}:{kwargs}"

    def edit_settings_kb(self, *args, **kwargs):
        self.calls.append(("edit_settings_kb", args, kwargs))
        return f"settings:{args}:{kwargs}"


def _edit_settings_deps() -> tuple[edit_settings_router.EditSettingsDeps, RecordingEditSettingsDeps]:
    rec = RecordingEditSettingsDeps()
    deps = edit_settings_router.EditSettingsDeps(
        workspace=rec.workspace,
        pending_edits=rec.pending_edits,
        image_registry=rec.registry,
        edit_and_send=rec.edit_and_send,
        fmt_to_aspect=rec.fmt_to_aspect,
        aspect_to_fmt=rec.aspect_to_fmt,
        image_model_meta=rec.image_model_meta,
        edit_confirm_kb=rec.edit_confirm_kb,
        edit_settings_kb=rec.edit_settings_kb,
        default_fmt="land",
        default_image_model="default-model",
    )
    return deps, rec


class EditSettingsRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deps, self.rec = _edit_settings_deps()
        self.router = edit_settings_router.create_router(self.deps)
        self.handler = self.router.callback_query.handlers[0].callback

    def test_creates_es_callback_router(self) -> None:
        self.assertIsInstance(self.router, Router)
        self.assertEqual(self.router.name, "tg-edit-settings")
        self.assertEqual(len(self.router.callback_query.handlers), 1)
        self.assertEqual(self.router.callback_query.handlers[0].callback.__name__, "on_edit_settings")

    def test_cancel_clears_pending_edit_and_deletes_message(self) -> None:
        self.rec.workspaces[42] = {
            "await": "edit_confirm",
            "edit_instruction": "change",
            "ag_variants": [{"prompt": "x"}],
        }
        self.rec.pending_edits[42] = "tok"
        callback = FakeCallback("es:cancel")
        run(self.handler(callback))
        st = self.rec.workspaces[42]
        self.assertIsNone(st["await"])
        self.assertNotIn("edit_instruction", st)
        self.assertNotIn("ag_variants", st)
        self.assertNotIn(42, self.rec.pending_edits)
        self.assertEqual(callback.deletes, 1)

    def test_change_returns_to_edit_prompt(self) -> None:
        self.rec.workspaces[42] = {"edit_fmt": "port", "edit_imodel": "pro-model", "ag_variants": [1]}
        callback = FakeCallback("es:change")
        run(self.handler(callback))
        st = self.rec.workspaces[42]
        self.assertEqual(st["await"], "edit")
        self.assertNotIn("ag_variants", st)
        self.assertTrue(callback.message_answers)
        self.assertEqual(self.rec.calls[-1][0], "edit_settings_kb")

    def test_apply_expired_alerts_when_ref_missing(self) -> None:
        self.rec.workspaces[42] = {"edit_instruction": "change"}
        self.rec.pending_edits[42] = "missing"
        callback = FakeCallback("es:apply")
        run(self.handler(callback))
        self.assertEqual(callback.answers[-1][1], {"show_alert": True})
        self.assertFalse(any(c[0] == "edit_and_send" for c in self.rec.calls))

    def test_apply_success_calls_edit_and_cleans_state(self) -> None:
        ref = SimpleNamespace(user_id=42, aspect_ratio="landscape")
        self.rec.registry["tok"] = ref
        self.rec.pending_edits[42] = "tok"
        self.rec.workspaces[42] = {
            "await": "edit_confirm",
            "edit_instruction": "change it",
            "edit_fmt": "port",
            "edit_imodel": "pro-model",
            "edit_as_gen": True,
            "ag_variants": [1],
        }
        callback = FakeCallback("es:apply")
        run(self.handler(callback))
        edit_call = next(c for c in self.rec.calls if c[0] == "edit_and_send")
        self.assertEqual(edit_call[1], (callback.message, ref, "change it"))
        self.assertEqual(edit_call[2]["actor_id"], 42)
        self.assertEqual(edit_call[2]["aspect_ratio"], "portrait")
        self.assertEqual(edit_call[2]["image_model"], "pro-model")
        self.assertEqual(edit_call[2]["price_action"], "gen")
        st = self.rec.workspaces[42]
        self.assertIsNone(st["await"])
        self.assertNotIn("edit_instruction", st)
        self.assertNotIn("ag_variants", st)
        self.assertNotIn(42, self.rec.pending_edits)

    def test_format_change_updates_confirm_keyboard(self) -> None:
        self.rec.workspaces[42] = {"await": "edit_confirm", "edit_imodel": "default-model"}
        callback = FakeCallback("es:fmt:port")
        run(self.handler(callback))
        self.assertEqual(self.rec.workspaces[42]["edit_fmt"], "port")
        self.assertEqual(self.rec.calls[-1][0], "edit_confirm_kb")
        self.assertTrue(callback.edits)

    def test_valid_model_change_updates_settings_keyboard(self) -> None:
        self.rec.workspaces[42] = {"await": "edit", "edit_fmt": "land"}
        callback = FakeCallback("es:imodel:pro-model")
        run(self.handler(callback))
        self.assertEqual(self.rec.workspaces[42]["edit_imodel"], "pro-model")
        self.assertEqual(self.rec.calls[-1][0], "edit_settings_kb")
        self.assertTrue(callback.edits)

    def test_invalid_model_choice_only_acknowledges(self) -> None:
        self.rec.workspaces[42] = {"await": "edit", "edit_fmt": "land"}
        callback = FakeCallback("es:imodel:nope")
        run(self.handler(callback))
        self.assertNotIn("edit_imodel", self.rec.workspaces[42])
        self.assertTrue(callback.answers)
        self.assertFalse(callback.edits)

    def test_edit_settings_deps_dataclass_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            self.deps.workspace = None  # type: ignore[misc]

    def test_edit_settings_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(edit_settings_router)
        self.assertNotIn("flow_bot", src)


class RecordingImageActionDeps:
    def __init__(self, *, seller: bool = True) -> None:
        self.workspaces: dict[int, dict] = {}
        self.registry: dict[str, object] = {}
        self.pending_edits: dict[int, str] = {}
        self.mix_baskets = defaultdict(list)
        self.seller = seller
        self.calls: list[tuple[str, tuple, dict]] = []

    def workspace(self, user_id: int) -> dict:
        return self.workspaces.setdefault(user_id, {})

    def is_seller(self) -> bool:
        return self.seller

    def aspect_to_fmt(self, aspect: str) -> str:
        self.calls.append(("aspect_to_fmt", (aspect,), {}))
        return "land"

    def edit_settings_kb(self, *args, **kwargs):
        self.calls.append(("edit_settings_kb", args, kwargs))
        return SimpleNamespace(kind="edit-kb")

    def mp_sku_choice_kb(self, *args, **kwargs):
        self.calls.append(("mp_sku_choice_kb", args, kwargs))
        return SimpleNamespace(kind="sku-kb")

    def mp_stamp_message(self, *args, **kwargs):
        self.calls.append(("mp_stamp_message", args, kwargs))

    async def vary_and_send(self, *args, **kwargs):
        self.calls.append(("vary_and_send", args, kwargs))

    async def regen_and_send(self, *args, **kwargs):
        self.calls.append(("regen_and_send", args, kwargs))

    async def enhance_and_send(self, *args, **kwargs):
        self.calls.append(("enhance_and_send", args, kwargs))

    async def real_upscale_and_send(self, *args, **kwargs):
        self.calls.append(("real_upscale_and_send", args, kwargs))

    async def send_original_file(self, *args, **kwargs):
        self.calls.append(("send_original_file", args, kwargs))


def _image_action_deps(*, seller: bool = True) -> tuple[image_action_router.ImageActionDeps, RecordingImageActionDeps]:
    rec = RecordingImageActionDeps(seller=seller)
    deps = image_action_router.ImageActionDeps(
        workspace=rec.workspace,
        image_registry=rec.registry,
        pending_edits=rec.pending_edits,
        mix_baskets=rec.mix_baskets,
        is_seller=rec.is_seller,
        aspect_to_fmt=rec.aspect_to_fmt,
        edit_settings_kb=rec.edit_settings_kb,
        mp_sku_choice_kb=rec.mp_sku_choice_kb,
        mp_stamp_message=rec.mp_stamp_message,
        vary_and_send=rec.vary_and_send,
        regen_and_send=rec.regen_and_send,
        enhance_and_send=rec.enhance_and_send,
        real_upscale_and_send=rec.real_upscale_and_send,
        send_original_file=rec.send_original_file,
        default_image_model="default-model",
        mix_max=2,
    )
    return deps, rec


class ImageActionRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deps, self.rec = _image_action_deps()
        self.router = image_action_router.create_router(self.deps)
        self.handler = self.router.callback_query.handlers[0].callback
        self.ref = SimpleNamespace(
            user_id=42,
            aspect_ratio="landscape",
            prompt="source prompt",
            platform="wb",
            source={"mediaId": "m1"},
        )
        self.rec.registry["tok"] = self.ref

    def _callback(self, action: str, *, user_id: int = 42) -> FakeCallback:
        return FakeCallback(action_callback_data(action, "tok"), user_id=user_id)

    def test_creates_image_action_callback_router(self) -> None:
        self.assertIsInstance(self.router, Router)
        self.assertEqual(self.router.name, "tg-image-action")
        self.assertEqual(len(self.router.callback_query.handlers), 1)
        self.assertEqual(self.router.callback_query.handlers[0].callback.__name__, "on_image_action")

    def test_stale_or_wrong_owner_alerts_without_delegate_call(self) -> None:
        callback = self._callback("edit", user_id=7)
        run(self.handler(callback))
        self.assertEqual(callback.answers[-1][1], {"show_alert": True})
        self.assertEqual(self.rec.calls, [])

    def test_edit_stores_pending_ref_and_opens_settings(self) -> None:
        callback = self._callback("edit")
        run(self.handler(callback))
        self.assertEqual(self.rec.pending_edits[42], "tok")
        self.assertEqual(self.rec.workspaces[42]["await"], "edit")
        self.assertEqual(self.rec.workspaces[42]["edit_fmt"], "land")
        self.assertEqual(self.rec.workspaces[42]["edit_imodel"], "default-model")
        self.assertEqual(self.rec.calls[-1][0], "edit_settings_kb")
        self.assertTrue(callback.message_answers)

    def test_image_operation_actions_delegate_to_injected_helpers(self) -> None:
        for action, expected in (
            ("vary", "vary_and_send"),
            ("regen", "regen_and_send"),
            ("up2x", "enhance_and_send"),
            ("realup", "real_upscale_and_send"),
        ):
            with self.subTest(action=action):
                self.rec.calls.clear()
                callback = self._callback(action)
                run(self.handler(callback))
                self.assertEqual(self.rec.calls[-1][0], expected)
                self.assertEqual(self.rec.calls[-1][1], (callback.message, self.ref))

    def test_skuadd_seller_flow_sets_pending_sku_and_stamps_message(self) -> None:
        callback = self._callback("skuadd")
        callback.message.photo = [SimpleNamespace(file_id="photo-file")]
        run(self.handler(callback))
        st = self.rec.workspaces[42]
        self.assertEqual(st["await"], "mp_sku_name")
        self.assertEqual(st["mp_sku_pending"]["token"], "tok")
        self.assertEqual(st["mp_sku_pending"]["file_id"], "photo-file")
        self.assertEqual([c[0] for c in self.rec.calls[-2:]], ["mp_sku_choice_kb", "mp_stamp_message"])

    def test_skuadd_is_ignored_when_seller_mode_disabled(self) -> None:
        deps, rec = _image_action_deps(seller=False)
        rec.registry["tok"] = self.ref
        handler = image_action_router.create_router(deps).callback_query.handlers[0].callback
        callback = FakeCallback(action_callback_data("skuadd", "tok"))
        run(handler(callback))
        self.assertTrue(callback.answers)
        self.assertEqual(rec.calls, [])

    def test_export_and_download_delegate_to_original_file_sender(self) -> None:
        callback = self._callback("mpexport")
        run(self.handler(callback))
        self.assertEqual(self.rec.calls[-1][0], "send_original_file")
        self.assertEqual(self.rec.calls[-1][2], {"marketplace_export": True})
        self.rec.calls.clear()
        callback = self._callback("download")
        run(self.handler(callback))
        self.assertEqual(self.rec.calls[-1][0], "send_original_file")
        self.assertEqual(self.rec.calls[-1][2], {})

    def test_mix_adds_sources_until_limit(self) -> None:
        self.rec.mix_baskets[42] = [{"old": "source"}]
        callback = self._callback("mix")
        run(self.handler(callback))
        self.assertEqual(self.rec.mix_baskets[42], [{"old": "source"}, {"mediaId": "m1"}])
        self.assertTrue(callback.message_answers)
        callback = self._callback("mix")
        run(self.handler(callback))
        self.assertEqual(callback.answers[-1][1], {"show_alert": True})

    def test_image_action_deps_dataclass_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            self.deps.default_image_model = "x"  # type: ignore[misc]

    def test_image_action_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(image_action_router)
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
