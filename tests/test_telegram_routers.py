from __future__ import annotations

import asyncio
import inspect
import unittest
from collections import defaultdict
from types import SimpleNamespace

from aiogram import Router

from channels.telegram.routers import animate as animate_router
from channels.telegram.routers import agent as agent_router
from channels.telegram.routers import admin_accounts as admin_accounts_router
from channels.telegram.routers import admin_credits as admin_credits_router
from channels.telegram.routers import admin_reports as admin_reports_router
from channels.telegram.routers import admin_status as admin_status_router
from channels.telegram.routers import commands as commands_router
from channels.telegram.routers import edit_settings as edit_settings_router
from channels.telegram.routers import generation_commands as generation_commands_router
from channels.telegram.routers import image_action as image_action_router
from channels.telegram.routers import ideas_flow as ideas_flow_router
from channels.telegram.routers import image_retry as image_retry_router
from channels.telegram.routers import ideas_hub as ideas_hub_router
from channels.telegram.routers import marketplace as marketplace_router
from channels.telegram.routers import onboarding as onboarding_router
from channels.telegram.routers import menu as menu_router
from channels.telegram.routers import payments as payments_router
from channels.telegram.routers import photo_input as photo_input_router
from channels.telegram.routers import photo_route as photo_route_router
from channels.telegram.routers import plain_text as plain_text_router
from channels.telegram.routers import video as video_router
from channels.telegram.routers import video_upload as video_upload_router
from channels.telegram.routers import video_upload_input as video_upload_input_router
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


class FakePreCheckoutQuery:
    def __init__(self, payload: str) -> None:
        self.invoice_payload = payload
        self.answers: list[tuple[tuple, dict]] = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))


class FakePaymentMessage:
    def __init__(self, payment, user_id: int = 42, message_id: int = 77) -> None:
        self.successful_payment = payment
        self.from_user = SimpleNamespace(id=user_id)
        self.message_id = message_id
        self.answers: list[tuple[tuple, dict]] = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))


class RecordingPaymentDeps:
    def __init__(self, tx_status: str = "paid") -> None:
        self.tx_status = tx_status
        self.packs = {"trial": {"credits": 45, "stars": 35}}
        self.order: list[str] = []
        self.tx_calls: list[dict] = []
        self.events: list[tuple[str, dict]] = []
        self.credits_added: list[tuple[int, int]] = []
        self.payments_added: list[tuple] = []
        self.referrals: list[tuple[tuple, dict]] = []
        self.menus: list[tuple[tuple, dict]] = []
        self.warnings: list[tuple[tuple, dict]] = []
        self.errors: list[tuple[tuple, dict]] = []
        self.infos: list[tuple[tuple, dict]] = []
        self.exceptions: list[tuple[tuple, dict]] = []
        self.credit_store = SimpleNamespace(add=self._credit_add)
        self.payment_store = SimpleNamespace(add=self._payment_add)
        self.metrics = SimpleNamespace(
            record_transaction_status=self._record_transaction_status,
            log_event=self._log_event,
        )
        self.log = SimpleNamespace(
            warning=self._warning,
            error=self._error,
            info=self._info,
            exception=self._exception,
        )

    def credit_pack(self, pack_id: str):
        return self.packs.get(pack_id)

    def username(self, message) -> str:
        return f"user{message.from_user.id}"

    def maybe_apply_referral_rewards(self, *args, **kwargs):
        self.order.append("referral")
        self.referrals.append((args, kwargs))

    async def show_main_menu(self, *args, **kwargs):
        self.order.append("menu")
        self.menus.append((args, kwargs))

    def build(self) -> payments_router.PaymentDeps:
        return payments_router.PaymentDeps(
            credit_pack=self.credit_pack,
            stars_to_rub=1.5,
            credit_store=self.credit_store,
            payment_store=self.payment_store,
            metrics=self.metrics,
            log=self.log,
            username=self.username,
            maybe_apply_referral_rewards=self.maybe_apply_referral_rewards,
            show_main_menu=self.show_main_menu,
        )

    def _record_transaction_status(self, **kwargs):
        self.order.append("tx")
        self.tx_calls.append(kwargs)
        return self.tx_status

    def _log_event(self, event: str, **kwargs):
        self.order.append("event")
        self.events.append((event, kwargs))

    def _credit_add(self, user_id: int, credits: int) -> int:
        self.order.append("credit")
        self.credits_added.append((user_id, credits))
        return 145

    def _payment_add(self, *args):
        self.order.append("payment_store")
        self.payments_added.append(args)

    def _warning(self, *args, **kwargs):
        self.warnings.append((args, kwargs))

    def _error(self, *args, **kwargs):
        self.errors.append((args, kwargs))

    def _info(self, *args, **kwargs):
        self.infos.append((args, kwargs))

    def _exception(self, *args, **kwargs):
        self.exceptions.append((args, kwargs))


class PaymentsRouterTests(unittest.TestCase):
    def _router(self, rec: RecordingPaymentDeps | None = None):
        self.rec = rec or RecordingPaymentDeps()
        return payments_router.create_router(self.rec.build())

    def _pre_checkout(self, router):
        return router.pre_checkout_query.handlers[0].callback

    def _successful_payment(self, router):
        return router.message.handlers[0].callback

    def _message(self, payload: str = "credits:trial", charge_id: str = "chg_1"):
        payment = SimpleNamespace(
            invoice_payload=payload,
            telegram_payment_charge_id=charge_id,
            total_amount=35,
        )
        return FakePaymentMessage(payment)

    def test_creates_router_with_payment_handlers(self) -> None:
        router = self._router()
        self.assertIsInstance(router, Router)
        self.assertEqual(router.name, "tg-payments")
        self.assertEqual(len(router.pre_checkout_query.handlers), 1)
        self.assertEqual(len(router.message.handlers), 1)
        self.assertEqual(self._pre_checkout(router).__name__, "on_pre_checkout")
        self.assertEqual(self._successful_payment(router).__name__, "on_successful_payment")

    def test_pre_checkout_accepts_known_pack(self) -> None:
        router = self._router()
        query = FakePreCheckoutQuery("credits:trial")

        run(self._pre_checkout(router)(query))

        self.assertEqual(query.answers, [((), {"ok": True, "error_message": None})])
        self.assertEqual(self.rec.warnings, [])

    def test_pre_checkout_rejects_unknown_pack(self) -> None:
        router = self._router()
        query = FakePreCheckoutQuery("credits:missing")

        run(self._pre_checkout(router)(query))

        self.assertEqual(query.answers[0][1]["ok"], False)
        self.assertIn("Пакет не найден", query.answers[0][1]["error_message"])
        self.assertEqual(len(self.rec.warnings), 1)

    def test_unknown_successful_payment_payload_answers_support_without_crediting(self) -> None:
        router = self._router()
        message = self._message("credits:missing")

        run(self._successful_payment(router)(message))

        self.assertEqual(self.rec.tx_calls, [])
        self.assertEqual(self.rec.credits_added, [])
        self.assertEqual(self.rec.payments_added, [])
        self.assertEqual(len(message.answers), 1)
        self.assertIn("Платёж получен", message.answers[0][0][0])

    def test_duplicate_successful_payment_returns_before_crediting(self) -> None:
        router = self._router(RecordingPaymentDeps(tx_status="duplicate"))
        message = self._message()

        run(self._successful_payment(router)(message))

        self.assertEqual(self.rec.order, ["tx"])
        self.assertEqual(self.rec.credits_added, [])
        self.assertEqual(self.rec.payments_added, [])
        self.assertEqual(self.rec.referrals, [])
        self.assertEqual(self.rec.menus, [])
        self.assertEqual(message.answers, [])

    def test_metrics_error_still_credits_and_continues(self) -> None:
        router = self._router(RecordingPaymentDeps(tx_status="error"))
        message = self._message()

        run(self._successful_payment(router)(message))

        self.assertEqual(self.rec.credits_added, [(42, 45)])
        self.assertEqual(self.rec.payments_added, [(42, "chg_1", 35, 45, "trial")])
        self.assertEqual(len(self.rec.errors), 1)
        self.assertEqual(len(self.rec.referrals), 1)
        self.assertEqual(len(self.rec.menus), 1)
        self.assertEqual(len(message.answers), 1)

    def test_successful_payment_records_before_credit_and_rewards_after(self) -> None:
        router = self._router()
        message = self._message()

        run(self._successful_payment(router)(message))

        self.assertLess(self.rec.order.index("tx"), self.rec.order.index("credit"))
        self.assertLess(self.rec.order.index("credit"), self.rec.order.index("referral"))
        self.assertEqual(self.rec.tx_calls[0]["provider"], "telegram_stars")
        self.assertEqual(self.rec.tx_calls[0]["provider_payment_id"], "chg_1")
        self.assertEqual(self.rec.tx_calls[0]["amount_rub"], 52.5)
        self.assertEqual(self.rec.events[0][0], "payment_success")
        self.assertEqual(self.rec.events[0][1]["payload"]["pack"], "trial")
        self.assertEqual(self.rec.referrals[0][1]["provider_payment_id"], "chg_1")
        self.assertEqual(message.answers[0][1], {})
        self.assertEqual(self.rec.menus[0][1], {"user_id": 42})

    def test_successful_payment_fallback_id_uses_message_id(self) -> None:
        router = self._router()
        message = self._message(charge_id="")

        run(self._successful_payment(router)(message))

        self.assertEqual(
            self.rec.tx_calls[0]["provider_payment_id"],
            "nocharge:42:trial:77",
        )

    def test_deps_dataclass_is_frozen(self) -> None:
        deps = RecordingPaymentDeps().build()
        with self.assertRaises(Exception):
            deps.credit_store = None  # type: ignore[misc]

    def test_router_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(payments_router)
        self.assertNotIn("flow_bot", src)


class FakePhotoMessage:
    def __init__(
        self,
        user_id: int = 42,
        file_id: str = "photo-1",
        caption: str | None = None,
        media_group_id: str | None = None,
    ) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.photo = [SimpleNamespace(file_id=file_id)]
        self.caption = caption
        self.media_group_id = media_group_id
        self.answers: list[tuple[tuple, dict]] = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))
        return SimpleNamespace(delete=self._delete)

    async def _delete(self):
        return None


class RecordingPhotoInputDeps:
    def __init__(self, *, is_seller: bool = False) -> None:
        self.states: dict[int, dict] = defaultdict(dict)
        self.is_seller_value = is_seller
        self.album_buf: dict[str, list] = {}
        self.album_tasks: dict[str, object] = {}
        self.calls: list[tuple[str, tuple, dict]] = []
        self.pending_edits: dict[int, object] = {}
        self.image_registry = SimpleNamespace(add=self._image_add)

    def build(self) -> photo_input_router.PhotoInputDeps:
        return photo_input_router.PhotoInputDeps(
            workspace=lambda user_id: self.states[user_id],
            album_buffer=lambda: self.album_buf,
            album_tasks=lambda: self.album_tasks,
            create_task=self._create_task,
            flush_album=self._async("flush_album"),
            prepare_photo_edit_from_file_id=self._async("prepare_photo_edit_from_file_id"),
            upload_photo_source_from_message=self._upload_photo_source_from_message,
            show_video_ingredients=self._async("show_video_ingredients"),
            show_video_frames=self._async("show_video_frames"),
            animate_photo_scenario=lambda: SimpleNamespace(
                attach_uploaded_photo=self._async("attach_uploaded_photo")
            ),
            context_factory=lambda message, user_id: ("ctx", message, user_id),
            template_photo_received=self._async("template_photo_received"),
            video_plain_text_ready=lambda st: bool(st.get("video_ready")),
            video_generate_and_send=self._async("video_generate_and_send"),
            mp_video_prompt=self._sync("mp_video_prompt", "video prompt"),
            mp_brand_kit=lambda user_id: "brand",
            mp_niche=lambda user_id: "niche",
            log_event=self._sync("log_event", None),
            seller_video_from_photo=self._async("seller_video_from_photo", True),
            mp_series_counts=(3, 5, 8),
            mp_series_prompt=self._sync("mp_series_prompt", "series prompt"),
            is_seller=lambda: self.is_seller_value,
            mp_confirm_screen=self._mp_confirm_screen,
            mp_stamp_message=self._sync("mp_stamp_message", None),
            upload_image_ref_from_photo_message=self._upload_image_ref_from_photo_message,
            mp_platform_aspect=lambda plat: "portrait_34",
            run_i2i=self._async("run_i2i", True),
            pending_edits=self.pending_edits,
            mp_job_instruction=self._sync("mp_job_instruction", "instruction"),
            mp_platform_fmt=lambda plat: "f34",
            image_registry=self.image_registry,
            edit_and_send=self._async("edit_and_send", True),
            offer_photo_route_choice=self._async("offer_photo_route_choice"),
            default_fmt="land",
            default_image_model="nb2",
            vid_ref_default_model="veo-lite",
        )

    def _create_task(self, coro):
        self.calls.append(("create_task", (coro,), {}))
        coro.close()
        return SimpleNamespace(cancel=lambda: self.calls.append(("cancel_task", (), {})))

    def _async(self, name: str, result=None):
        async def fn(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return result

        return fn

    def _sync(self, name: str, result):
        def fn(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return result

        return fn

    async def _upload_photo_source_from_message(self, *args, **kwargs):
        self.calls.append(("upload_photo_source_from_message", args, kwargs))
        return {"mediaId": "m1"}

    async def _upload_image_ref_from_photo_message(self, *args, **kwargs):
        self.calls.append(("upload_image_ref_from_photo_message", args, kwargs))
        return SimpleNamespace(user_id=kwargs.get("user_id"), source={})

    def _mp_confirm_screen(self, user_id: int):
        self.calls.append(("mp_confirm_screen", (user_id,), {}))
        return "confirm", "kb"

    def _image_add(self, ref):
        self.calls.append(("image_registry.add", (ref,), {}))
        return "tok"


class PhotoInputRouterTests(unittest.TestCase):
    def _router(self, rec: RecordingPhotoInputDeps | None = None):
        self.rec = rec or RecordingPhotoInputDeps()
        return photo_input_router.create_router(self.rec.build())

    def _handler(self, router):
        return router.message.handlers[0].callback

    def test_creates_router_with_photo_handler(self) -> None:
        router = self._router()
        self.assertIsInstance(router, Router)
        self.assertEqual(router.name, "tg-photo-input")
        self.assertEqual(len(router.message.handlers), 1)
        self.assertEqual(self._handler(router).__name__, "handle_photo")

    def test_support_photo_repeats_brief_request_without_upload(self) -> None:
        router = self._router()
        self.rec.states[42]["support_await"] = True
        message = FakePhotoMessage()

        run(self._handler(router)(message))

        self.assertIn("текстовый бриф", message.answers[0][0][0])
        self.assertFalse(any(call[0].startswith("upload_") for call in self.rec.calls))

    def test_album_photo_buffers_and_schedules_flush(self) -> None:
        router = self._router()
        self.rec.states[42]["vawait"] = "ving_photo"
        message = FakePhotoMessage(media_group_id="album-1")

        run(self._handler(router)(message))

        self.assertEqual(self.rec.album_buf["album-1"], [message])
        self.assertIn("album-1", self.rec.album_tasks)
        self.assertEqual(self.rec.calls[0][0], "create_task")

    def test_photo_edit_state_delegates_to_prepare_edit(self) -> None:
        router = self._router()
        self.rec.states[42]["await"] = "photo"
        message = FakePhotoMessage(caption="fix it")

        run(self._handler(router)(message))

        call = self.rec.calls[0]
        self.assertEqual(call[0], "prepare_photo_edit_from_file_id")
        self.assertEqual(call[2]["file_id"], "photo-1")
        self.assertEqual(call[2]["caption"], "fix it")

    def test_video_prompt_waiting_photo_replies_text_only_hint(self) -> None:
        router = self._router()
        self.rec.states[42]["vawait"] = "vprompt"
        message = FakePhotoMessage()

        run(self._handler(router)(message))

        self.assertEqual(len(message.answers), 1)
        self.assertEqual(self.rec.calls, [])

    def test_captioned_photo_without_mode_offers_route_choice(self) -> None:
        router = self._router()
        message = FakePhotoMessage(caption="make a poster")

        run(self._handler(router)(message))

        call = self.rec.calls[0]
        self.assertEqual(call[0], "offer_photo_route_choice")
        self.assertEqual(call[2], {"user_id": 42, "caption": "make a poster"})

    def test_seller_photo_state_shows_confirm_without_upload(self) -> None:
        router = self._router(RecordingPhotoInputDeps(is_seller=True))
        self.rec.states[42].update({"await": "mp_photo", "mp_platform": "wb", "mp_preset": "whitebg"})
        message = FakePhotoMessage(caption="red shoes")

        run(self._handler(router)(message))

        self.assertEqual(self.rec.states[42]["mp_pending_file_id"], "photo-1")
        self.assertIsNone(self.rec.states[42]["await"])
        self.assertEqual(message.answers[0], (("confirm",), {"reply_markup": "kb", "parse_mode": "HTML"}))
        self.assertIn("mp_stamp_message", [call[0] for call in self.rec.calls])
        self.assertNotIn("upload_image_ref_from_photo_message", [call[0] for call in self.rec.calls])

    def test_deps_dataclass_is_frozen(self) -> None:
        deps = RecordingPhotoInputDeps().build()
        with self.assertRaises(Exception):
            deps.default_fmt = "sq"  # type: ignore[misc]

    def test_router_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(photo_input_router)
        self.assertNotIn("flow_bot", src)


class FakeTextMessage:
    def __init__(self, text: str, user_id: int = 42) -> None:
        self.text = text
        self.from_user = SimpleNamespace(id=user_id, first_name="Test")
        self.chat = SimpleNamespace(id=user_id)
        self.photo = None
        self.answers: list[tuple[tuple, dict]] = []
        self.bot = SimpleNamespace(edit_message_text=self._edit_message_text)
        self.edits: list[tuple[tuple, dict]] = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))
        return SimpleNamespace(message_id=99)

    async def _edit_message_text(self, *args, **kwargs):
        self.edits.append((args, kwargs))


class RecordingPlainTextDeps:
    def __init__(self, *, seller: bool = False) -> None:
        self.states: dict[int, dict] = defaultdict(dict)
        self.calls: list[tuple[str, tuple, dict]] = []
        self.pending_edits: dict[int, object] = {}
        self.pending_photo_routes: dict[int, object] = {}
        self.config = SimpleNamespace(IS_SELLER=seller)
        self.metrics = SimpleNamespace(
            upsert_user=self._sync("upsert_user"),
            log_event=self._sync("log_event"),
            create_ticket=self._sync("create_ticket", 123),
            set_ticket_admin_msg=self._sync("set_ticket_admin_msg"),
            reply_ticket=self._sync("reply_ticket", None),
            redeem_promo=self._sync("redeem_promo", None),
            create_seller_sku_project=self._sync("create_seller_sku_project", True),
            rename_seller_sku_project=self._sync("rename_seller_sku_project", True),
            save_seller_profile=self._sync("save_seller_profile", True),
        )
        self.credit_store = SimpleNamespace(add=self._sync("credit_store.add", 50))
        self.log = SimpleNamespace(warning=self._sync("log.warning"))
        self.image_registry = SimpleNamespace(get=self._sync("image_registry.get", None))
        self.video_registry = {}

    def build(self) -> plain_text_router.PlainTextDeps:
        return plain_text_router.PlainTextDeps(
            workspace=lambda user_id: self.states[user_id],
            metrics=self.metrics,
            username=lambda message: f"user{message.from_user.id}",
            reset_image_flow=self._sync("reset_image_flow"),
            show_prompt_picker=self._async("show_prompt_picker"),
            pending_edits=self.pending_edits,
            pending_photo_routes=self.pending_photo_routes,
            show_main_menu=self._async("show_main_menu"),
            is_balance_reply_text=lambda text: text == "balance",
            show_balance=self._async("show_balance"),
            vid_clear=self._sync("vid_clear"),
            show_ideas_root=self._async("show_ideas_root"),
            show_referral_screen=self._async("show_referral_screen"),
            show_help_screen=self._async("show_help_screen"),
            show_video_prompt_input=self._async("show_video_prompt_input"),
            tp_store_answer=self._sync("tp_store_answer"),
            render_template_step=self._async("render_template_step"),
            render_guided_step=self._async("render_guided_step"),
            video_edit_uploaded=self._async("video_edit_uploaded"),
            video_registry=self.video_registry,
            video_prompt_edit_and_send=self._async("video_prompt_edit_and_send"),
            video_extend_and_send=self._async("video_extend_and_send"),
            video_generate_and_send=self._async("video_generate_and_send"),
            animate_photo_scenario=lambda: SimpleNamespace(
                remember_prompt_until_photo=self._async("remember_prompt_until_photo"),
                generate_from_ready_references=self._async("generate_from_ready_references"),
            ),
            telegram_animate_photo_context=lambda message, user_id: ("ctx", message, user_id),
            show_new_video_wizard=self._async("show_new_video_wizard"),
            video_plain_text_ready=lambda st: bool(st.get("video_ready")),
            admin_ids=[],
            credit_store=self.credit_store,
            send_owner_alert=self._async("send_owner_alert"),
            pending_sku_payload=self._sync("pending_sku_payload", None),
            save_pending_sku_item=self._async("save_pending_sku_item", True),
            log=self.log,
            wizard_text=self._sync("wizard_text", "wizard"),
            default_count=1,
            default_fmt="land",
            default_image_model="nb2",
            fmt_to_aspect=lambda fmt: f"aspect:{fmt}",
            aspect_to_fmt=lambda aspect: f"fmt:{aspect}",
            image_registry=self.image_registry,
            run_i2i=self._async("run_i2i", True),
            show_edit_confirm=self._async("show_edit_confirm"),
            generate_and_send=self._async("generate_and_send"),
            config=self.config,
            show_wizard=self._async("show_wizard"),
        )

    def _sync(self, name: str, result=None):
        def fn(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return result

        return fn

    def _async(self, name: str, result=None):
        async def fn(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return result

        return fn


class PlainTextRouterTests(unittest.TestCase):
    def _router(self, rec: RecordingPlainTextDeps | None = None):
        self.rec = rec or RecordingPlainTextDeps()
        return plain_text_router.create_router(self.rec.build())

    def _handler(self, router):
        return router.message.handlers[0].callback

    def test_creates_router_with_plain_text_handler(self) -> None:
        router = self._router()
        self.assertIsInstance(router, Router)
        self.assertEqual(router.name, "tg-plain-text")
        self.assertEqual(len(router.message.handlers), 1)
        self.assertEqual(self._handler(router).__name__, "handle_plain_text")

    def test_reply_keyboard_generate_opens_prompt_picker(self) -> None:
        router = self._router()
        message = FakeTextMessage(plain_text_router.L("kb_gen"))

        run(self._handler(router)(message))

        self.assertEqual([call[0] for call in self.rec.calls[:3]], [
            "upsert_user",
            "reset_image_flow",
            "show_prompt_picker",
        ])

    def test_awaiting_prompt_delegates_to_image_generation(self) -> None:
        router = self._router()
        self.rec.states[42].update({"await": "prompt", "count": 2, "fmt": "sq", "imodel": "nb2"})
        message = FakeTextMessage("make a poster")

        run(self._handler(router)(message))

        call = self.rec.calls[-1]
        self.assertEqual(call[0], "generate_and_send")
        self.assertEqual(call[1][:2], (message, "make a poster"))
        self.assertEqual(call[2]["num_images"], 2)
        self.assertEqual(call[2]["aspect_ratio"], "aspect:sq")
        self.assertEqual(self.rec.states[42]["await"], None)

    def test_seller_fallback_does_not_open_image_wizard(self) -> None:
        router = self._router(RecordingPlainTextDeps(seller=True))
        message = FakeTextMessage("random text")

        run(self._handler(router)(message))

        self.assertEqual(len(message.answers), 1)
        self.assertNotIn("show_wizard", [call[0] for call in self.rec.calls])
        self.assertNotIn("generate_and_send", [call[0] for call in self.rec.calls])

    def test_deps_dataclass_is_frozen(self) -> None:
        deps = RecordingPlainTextDeps().build()
        with self.assertRaises(Exception):
            deps.default_fmt = "sq"  # type: ignore[misc]

    def test_router_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(plain_text_router)
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


class RecordingMarketplaceMetrics:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))

    def upsert_user(self, *args, **kwargs):
        self._record("upsert_user", *args, **kwargs)

    def log_event(self, *args, **kwargs):
        self._record("log_event", *args, **kwargs)

    def list_seller_sku_projects(self, *args, **kwargs):
        self._record("list_seller_sku_projects", *args, **kwargs)
        return []

    def get_seller_sku_project(self, *args, **kwargs):
        self._record("get_seller_sku_project", *args, **kwargs)
        return {}

    def delete_seller_sku_project(self, *args, **kwargs):
        self._record("delete_seller_sku_project", *args, **kwargs)
        return 1

    def save_seller_profile(self, *args, **kwargs):
        self._record("save_seller_profile", *args, **kwargs)
        return True


class RecordingMarketplaceDeps:
    def __init__(self, *, seller: bool = True) -> None:
        self.workspaces: dict[int, dict] = {}
        self.pending_edits: dict[int, str] = {}
        self.metrics = RecordingMarketplaceMetrics()
        self.seller = seller
        self.stale = False
        self.calls: list[tuple[str, tuple, dict]] = []

    def workspace(self, user_id: int) -> dict:
        return self.workspaces.setdefault(user_id, {})

    def is_seller(self) -> bool:
        return self.seller

    def mp_is_stale_callback(self, *args, **kwargs):
        self.calls.append(("mp_is_stale_callback", args, kwargs))
        return self.stale

    async def mp_reject_stale_callback(self, *args, **kwargs):
        self.calls.append(("mp_reject_stale_callback", args, kwargs))

    def mp_stamp_message(self, *args, **kwargs):
        self.calls.append(("mp_stamp_message", args, kwargs))

    def mp_job_instruction(self, *args, **kwargs):
        self.calls.append(("mp_job_instruction", args, kwargs))
        return "job instruction"

    def mp_series_prompt(self, *args, **kwargs):
        self.calls.append(("mp_series_prompt", args, kwargs))
        return "series prompt"

    async def seller_i2i_from_file_id(self, *args, **kwargs):
        self.calls.append(("seller_i2i_from_file_id", args, kwargs))
        return True

    async def show_sku_projects(self, *args, **kwargs):
        self.calls.append(("show_sku_projects", args, kwargs))

    def pending_sku_payload(self, *args, **kwargs):
        self.calls.append(("pending_sku_payload", args, kwargs))
        return None

    def latest_sku_payload(self, *args, **kwargs):
        self.calls.append(("latest_sku_payload", args, kwargs))
        return None

    async def save_sku_payload(self, *args, **kwargs):
        self.calls.append(("save_sku_payload", args, kwargs))
        return True

    async def save_pending_sku_item(self, *args, **kwargs):
        self.calls.append(("save_pending_sku_item", args, kwargs))

    def reset_image_flow(self, *args, **kwargs):
        self.calls.append(("reset_image_flow", args, kwargs))

    def vid_clear(self, *args, **kwargs):
        self.calls.append(("vid_clear", args, kwargs))

    def clear_image_flow_keys(self, *args, **kwargs):
        self.calls.append(("clear_image_flow_keys", args, kwargs))

    async def show_video_ingredients(self, *args, **kwargs):
        self.calls.append(("show_video_ingredients", args, kwargs))


def _marketplace_deps(*, seller: bool = True) -> tuple[marketplace_router.MarketplaceDeps, RecordingMarketplaceDeps]:
    rec = RecordingMarketplaceDeps(seller=seller)
    deps = marketplace_router.MarketplaceDeps(
        workspace=rec.workspace,
        pending_edits=rec.pending_edits,
        metrics=rec.metrics,
        is_seller=rec.is_seller,
        product_photo_jobs={"whitebg", "info", "model", "cover", "bg"},
        mp_is_stale_callback=rec.mp_is_stale_callback,
        mp_reject_stale_callback=rec.mp_reject_stale_callback,
        mp_stamp_message=rec.mp_stamp_message,
        mp_job_instruction=rec.mp_job_instruction,
        mp_series_prompt=rec.mp_series_prompt,
        seller_i2i_from_file_id=rec.seller_i2i_from_file_id,
        show_sku_projects=rec.show_sku_projects,
        pending_sku_payload=rec.pending_sku_payload,
        latest_sku_payload=rec.latest_sku_payload,
        save_sku_payload=rec.save_sku_payload,
        save_pending_sku_item=rec.save_pending_sku_item,
        reset_image_flow=rec.reset_image_flow,
        vid_clear=rec.vid_clear,
        clear_image_flow_keys=rec.clear_image_flow_keys,
        show_video_ingredients=rec.show_video_ingredients,
    )
    return deps, rec


class MarketplaceRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deps, self.rec = _marketplace_deps()
        self.router = marketplace_router.create_router(self.deps)
        self.handler = self.router.callback_query.handlers[0].callback

    def test_creates_mp_callback_router(self) -> None:
        self.assertIsInstance(self.router, Router)
        self.assertEqual(self.router.name, "tg-marketplace")
        self.assertEqual(len(self.router.callback_query.handlers), 1)
        self.assertEqual(self.router.callback_query.handlers[0].callback.__name__, "on_marketplace_action")

    def test_stale_callback_is_rejected_before_stamp(self) -> None:
        self.rec.stale = True
        callback = FakeCallback("mp:plat:wb")
        run(self.handler(callback))
        self.assertEqual([c[0] for c in self.rec.calls], [
            "mp_is_stale_callback",
            "mp_reject_stale_callback",
        ])

    def test_platform_choice_updates_state_and_jobs_screen(self) -> None:
        callback = FakeCallback("mp:plat:ozon")
        run(self.handler(callback))
        self.assertEqual(self.rec.workspaces[42]["mp_platform"], "ozon")
        self.assertTrue(callback.text_edits)
        self.assertIn(("log_event", ("mp_platform",), {"user_id": 42, "source": "ozon"}), self.rec.metrics.calls)

    def test_product_job_prepares_photo_upload_state(self) -> None:
        self.rec.workspaces[42] = {"mp_platform": "wb"}
        callback = FakeCallback("mp:job:whitebg")
        run(self.handler(callback))
        st = self.rec.workspaces[42]
        self.assertEqual(st["await"], "mp_photo")
        self.assertEqual(st["mp_preset"], "whitebg")
        self.assertEqual(st["edit_imodel"], "nb2")
        self.assertIn("reset_image_flow", [c[0] for c in self.rec.calls])

    def test_non_seller_animate_delegates_to_video_ingredients(self) -> None:
        deps, rec = _marketplace_deps(seller=False)
        handler = marketplace_router.create_router(deps).callback_query.handlers[0].callback
        rec.workspaces[42] = {"mp_platform": "wb"}
        rec.pending_edits[42] = "tok"
        callback = FakeCallback("mp:job:animate")
        run(handler(callback))
        self.assertNotIn(42, rec.pending_edits)
        self.assertEqual(rec.workspaces[42]["vmode"], "ingredients")
        self.assertIn("vid_clear", [c[0] for c in rec.calls])
        self.assertIn("show_video_ingredients", [c[0] for c in rec.calls])

    def test_marketplace_deps_dataclass_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            self.deps.metrics = None  # type: ignore[misc]

    def test_marketplace_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(marketplace_router)
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


class RecordingVideoDeps:
    def __init__(self, *, balance: int = 1000) -> None:
        self.workspaces: dict[int, dict] = {}
        self.balance_value = balance
        self.calls: list[tuple[str, tuple, dict]] = []

    def workspace(self, user_id: int) -> dict:
        return self.workspaces.setdefault(user_id, {})

    def balance(self, user_id: int) -> int:
        self.calls.append(("balance", (user_id,), {}))
        return self.balance_value

    def vid_clear(self, *args, **kwargs):
        self.calls.append(("vid_clear", args, kwargs))

    def nwiz_model(self, st) -> str:
        self.calls.append(("nwiz_model", (st,), {}))
        return "omni-flash-4s"

    def nwiz_price(self, st) -> int:
        self.calls.append(("nwiz_price", (st,), {}))
        return 100

    def _make_async(self, name):
        async def renderer(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None

        return renderer


def _video_deps(*, balance: int = 1000) -> tuple[video_router.VideoDeps, RecordingVideoDeps]:
    rec = RecordingVideoDeps(balance=balance)
    deps = video_router.VideoDeps(
        workspace=rec.workspace,
        vid_clear=rec.vid_clear,
        show_main_menu=rec._make_async("show_main_menu"),
        video_download=rec._make_async("video_download"),
        video_segment_download=rec._make_async("video_segment_download"),
        video_edit_start=rec._make_async("video_edit_start"),
        video_extend_start=rec._make_async("video_extend_start"),
        video_repeat_last=rec._make_async("video_repeat_last"),
        show_new_video_wizard=rec._make_async("show_new_video_wizard"),
        nwiz_text=lambda user_id: "styles text",
        vid_edit=rec._make_async("vid_edit"),
        nwiz_model=rec.nwiz_model,
        nwiz_price=rec.nwiz_price,
        video_generate_and_send=rec._make_async("video_generate_and_send"),
        show_video_settings=rec._make_async("show_video_settings"),
        show_video_variant=rec._make_async("show_video_variant"),
        show_video_ingredients=rec._make_async("show_video_ingredients"),
        show_video_frames=rec._make_async("show_video_frames"),
        show_video_family=rec._make_async("show_video_family"),
        vid_rerender_settings=rec._make_async("vid_rerender_settings"),
        balance=rec.balance,
        vid_frames_default_model="veo-lite",
        vid_code_family={"omni": "omni-flash", "veo": "veo"},
        vid_quickstart_family="omni-flash",
        default_video_count=1,
    )
    return deps, rec


class VideoRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deps, self.rec = _video_deps()
        self.router = video_router.create_router(self.deps)
        self.handler = self.router.callback_query.handlers[0].callback

    def test_creates_v_callback_router(self) -> None:
        self.assertIsInstance(self.router, Router)
        self.assertEqual(self.router.name, "tg-video")
        self.assertEqual(len(self.router.callback_query.handlers), 1)
        self.assertEqual(self.router.callback_query.handlers[0].callback.__name__, "on_video_action")

    def test_busy_generation_blocks_non_download_buttons(self) -> None:
        self.rec.workspaces[42] = {"vstep": "vgenerating"}
        callback = FakeCallback("v:cancel")
        run(self.handler(callback))
        self.assertTrue(callback.answers)
        self.assertEqual(callback.answers[-1][1], {"show_alert": True})
        self.assertEqual(self.rec.calls, [])

    def test_cancel_clears_video_and_shows_main_menu(self) -> None:
        callback = FakeCallback("v:cancel")
        run(self.handler(callback))
        self.assertIn(("vid_clear", (42,), {}), self.rec.calls)
        self.assertEqual(self.rec.calls[-1][0], "show_main_menu")
        self.assertEqual(self.rec.calls[-1][2], {"user_id": 42, "edit": True})

    def test_go_with_low_balance_shows_topup_instead_of_prompt_ask(self) -> None:
        deps, rec = _video_deps(balance=0)
        handler = video_router.create_router(deps).callback_query.handlers[0].callback
        rec.workspaces[42] = {"vmodel": "omni-flash-4s", "vcount": 1}
        callback = FakeCallback("v:go")
        run(handler(callback))
        self.assertEqual([c[0] for c in rec.calls if c[0] not in ("balance",)], ["vid_edit"])
        self.assertNotIn("vawait", rec.workspaces[42])

    def test_go_with_sufficient_balance_asks_for_prompt(self) -> None:
        callback = FakeCallback("v:go")
        self.rec.workspaces[42] = {"vmodel": "omni-flash-4s", "vcount": 1}
        run(self.handler(callback))
        self.assertEqual(self.rec.workspaces[42]["vawait"], "vprompt")
        self.assertEqual(self.rec.workspaces[42]["vstep"], "vprompt")
        self.assertTrue(callback.text_edits)

    def test_family_ingredients_clears_and_shows_ingredients_screen(self) -> None:
        callback = FakeCallback("v:fam:ing")
        run(self.handler(callback))
        st = self.rec.workspaces[42]
        self.assertEqual(st["vmode"], "ingredients")
        self.assertIn("vid_clear", [c[0] for c in self.rec.calls])
        self.assertIn("show_video_ingredients", [c[0] for c in self.rec.calls])

    def test_video_deps_dataclass_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            self.deps.balance = None  # type: ignore[misc]

    def test_video_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(video_router)
        self.assertNotIn("flow_bot", src)


class RecordingGenerationCommandsDeps:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def reset_image_flow(self, *args, **kwargs):
        self.calls.append(("reset_image_flow", args, kwargs))

    def vid_clear(self, *args, **kwargs):
        self.calls.append(("vid_clear", args, kwargs))

    async def show_ideas_root(self, *args, **kwargs):
        self.calls.append(("show_ideas_root", args, kwargs))

    async def generate_and_send(self, *args, **kwargs):
        self.calls.append(("generate_and_send", args, kwargs))

    async def mix_and_send(self, *args, **kwargs):
        self.calls.append(("mix_and_send", args, kwargs))


def _generation_commands_deps() -> tuple[
    generation_commands_router.GenerationCommandsDeps, RecordingGenerationCommandsDeps
]:
    rec = RecordingGenerationCommandsDeps()
    deps = generation_commands_router.GenerationCommandsDeps(
        reset_image_flow=rec.reset_image_flow,
        vid_clear=rec.vid_clear,
        show_ideas_root=rec.show_ideas_root,
        generate_and_send=rec.generate_and_send,
        mix_and_send=rec.mix_and_send,
    )
    return deps, rec


class GenerationCommandsRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deps, self.rec = _generation_commands_deps()
        self.router = generation_commands_router.create_router(self.deps)

    def test_creates_router_with_seven_command_handlers(self) -> None:
        self.assertIsInstance(self.router, Router)
        handlers = self.router.message.handlers
        self.assertEqual(len(handlers), 7)
        self.assertEqual(self.router.callback_query.handlers, [])
        matched = [_handler_commands(h) for h in handlers]
        for expected in ({"ideas"}, {"img"}, {"one"}, {"portrait"}, {"square"}, {"imgn"}, {"mix"}):
            self.assertIn(expected, matched)

    def _call(self, command: str, text: str, user_id: int = 42):
        for handler in self.router.message.handlers:
            if _handler_commands(handler) == {command}:
                message = SimpleNamespace(from_user=SimpleNamespace(id=user_id), text=text)
                run(handler.callback(message))
                return message
        raise AssertionError(f"no handler for {command}")

    def test_ideas_resets_state_and_shows_root(self) -> None:
        self._call("ideas", "/ideas")
        self.assertEqual(
            [c[0] for c in self.rec.calls],
            ["reset_image_flow", "vid_clear", "show_ideas_root"],
        )
        name, args, kwargs = self.rec.calls[-1]
        self.assertEqual(kwargs, {"user_id": 42, "edit": False})

    def test_img_generates_four_images_from_prompt(self) -> None:
        self._call("img", "/img a cat")
        name, args, kwargs = self.rec.calls[-1]
        self.assertEqual(name, "generate_and_send")
        self.assertEqual(args[1], "a cat")
        self.assertEqual(kwargs, {"num_images": 4})

    def test_portrait_and_square_set_aspect_ratio(self) -> None:
        self._call("portrait", "/portrait a dog")
        self.assertEqual(self.rec.calls[-1][2], {"num_images": 2, "aspect_ratio": "portrait"})
        self._call("square", "/square a dog")
        self.assertEqual(self.rec.calls[-1][2], {"num_images": 2, "aspect_ratio": "square"})

    def test_imgn_parses_explicit_count_and_prompt(self) -> None:
        self._call("imgn", "/imgn 3 a fox")
        name, args, kwargs = self.rec.calls[-1]
        self.assertEqual(name, "generate_and_send")
        self.assertEqual(args[1], "a fox")
        self.assertEqual(kwargs, {"num_images": 3})

    def test_mix_delegates_to_mix_and_send(self) -> None:
        self._call("mix", "/mix combine these")
        name, args, kwargs = self.rec.calls[-1]
        self.assertEqual(name, "mix_and_send")
        self.assertEqual(args[1], "combine these")

    def test_generation_commands_deps_dataclass_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            self.deps.generate_and_send = None  # type: ignore[misc]

    def test_generation_commands_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(generation_commands_router)
        self.assertNotIn("flow_bot", src)


class FakeAccountPool:
    def __init__(self, known: set[str] | None = None) -> None:
        self.known = known or {"acc1"}
        self.calls: list[tuple[str, tuple, dict]] = []

    def set_disabled(self, acc_id: str, value: bool) -> bool:
        self.calls.append(("set_disabled", (acc_id, value), {}))
        return acc_id in self.known

    def set_video_allowed(self, acc_id: str, value: bool) -> bool:
        self.calls.append(("set_video_allowed", (acc_id, value), {}))
        return acc_id in self.known

    def account_ids(self):
        return sorted(self.known)


class RecordingAdminAccountsDeps:
    def __init__(self, *, admin: bool = True, owner: bool = True) -> None:
        self.admin = admin
        self.owner = owner
        self.account_pool = FakeAccountPool()
        self.calls: list[tuple[str, tuple, dict]] = []

    def admin_only(self, message) -> bool:
        self.calls.append(("admin_only", (message,), {}))
        return self.admin

    def owner_only(self, message) -> bool:
        self.calls.append(("owner_only", (message,), {}))
        return self.owner

    def log_event(self, *args, **kwargs):
        self.calls.append(("log_event", args, kwargs))

    def render_admin_help(self) -> str:
        self.calls.append(("render_admin_help", (), {}))
        return "help text"


class FakeMessage:
    def __init__(self, text: str, user_id: int = 7) -> None:
        self.text = text
        self.from_user = SimpleNamespace(id=user_id)
        self.answers: list[tuple[tuple, dict]] = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))


def _admin_accounts_deps(*, admin: bool = True, owner: bool = True) -> tuple[
    admin_accounts_router.AdminAccountsDeps, RecordingAdminAccountsDeps
]:
    rec = RecordingAdminAccountsDeps(admin=admin, owner=owner)
    deps = admin_accounts_router.AdminAccountsDeps(
        admin_only=rec.admin_only,
        owner_only=rec.owner_only,
        account_pool=rec.account_pool,
        log_event=rec.log_event,
        render_admin_help=rec.render_admin_help,
    )
    return deps, rec


class AdminAccountsRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deps, self.rec = _admin_accounts_deps()
        self.router = admin_accounts_router.create_router(self.deps)

    def test_creates_router_with_five_command_handlers(self) -> None:
        self.assertIsInstance(self.router, Router)
        handlers = self.router.message.handlers
        self.assertEqual(len(handlers), 5)
        self.assertEqual(self.router.callback_query.handlers, [])
        matched = [_handler_commands(h) for h in handlers]
        for expected in ({"acc_off"}, {"acc_on"}, {"acc_vid_off"}, {"acc_vid_on"}, {"admin_help"}):
            self.assertIn(expected, matched)

    def _call(self, command: str, text: str):
        for handler in self.router.message.handlers:
            if _handler_commands(handler) == {command}:
                message = FakeMessage(text)
                run(handler.callback(message))
                return message
        raise AssertionError(f"no handler for {command}")

    def test_non_admin_is_denied_without_pool_mutation(self) -> None:
        deps, rec = _admin_accounts_deps(admin=False)
        router = admin_accounts_router.create_router(deps)
        for handler in router.message.handlers:
            if _handler_commands(handler) == {"acc_off"}:
                message = FakeMessage("/acc_off acc1")
                run(handler.callback(message))
                break
        self.assertEqual(rec.account_pool.calls, [])
        self.assertTrue(message.answers)

    def test_acc_off_disables_known_account_and_logs(self) -> None:
        message = self._call("acc_off", "/acc_off acc1")
        self.assertIn(("set_disabled", ("acc1", True), {}), self.rec.account_pool.calls)
        self.assertEqual(
            [c[0] for c in self.rec.calls if c[0] == "log_event"],
            ["log_event"],
        )
        self.assertIn("acc1", message.answers[-1][0][0])

    def test_acc_on_unknown_account_lists_known_ids(self) -> None:
        message = self._call("acc_on", "/acc_on nope")
        self.assertIn("nope", "".join(str(a) for a in self.rec.account_pool.calls))
        self.assertIn("acc1", message.answers[-1][0][0])

    def test_admin_help_is_owner_gated(self) -> None:
        deps, rec = _admin_accounts_deps(owner=False)
        router = admin_accounts_router.create_router(deps)
        for handler in router.message.handlers:
            if _handler_commands(handler) == {"admin_help"}:
                message = FakeMessage("/admin_help")
                run(handler.callback(message))
                break
        self.assertEqual([c[0] for c in rec.calls if c[0] == "render_admin_help"], [])
        self.assertTrue(message.answers)

    def test_admin_help_owner_renders_help(self) -> None:
        message = self._call("admin_help", "/admin_help")
        self.assertEqual(message.answers[-1][0][0], "help text")
        self.assertIn(("render_admin_help", (), {}), self.rec.calls)

    def test_admin_accounts_deps_dataclass_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            self.deps.account_pool = None  # type: ignore[misc]

    def test_admin_accounts_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(admin_accounts_router)
        self.assertNotIn("flow_bot", src)


class FakeStatusKeeper:
    def __init__(self, rec) -> None:
        self.rec = rec

    async def get_session(self):
        self.rec.calls.append(("get_session", (), {}))
        return {
            "bearer": "token-present",
            "project_id": "proj1",
            "cookies": {"a": "b", "c": "d"},
        }

    async def get_capmonster_balance(self):
        self.rec.calls.append(("get_capmonster_balance", (), {}))
        return "12.3"

    async def get_2captcha_balance(self):
        self.rec.calls.append(("get_2captcha_balance", (), {}))
        return "4.5"


class FakeStatusPool:
    def __init__(self, rec) -> None:
        self.rec = rec

    def status(self):
        self.rec.calls.append(("pool.status", (), {}))
        return [{
            "id": "acc1",
            "disabled": False,
            "cooldown_left": 0,
            "video_allowed": True,
            "active_image_jobs": 1,
            "image_capacity": 2,
            "active_video_jobs": 0,
            "video_capacity": 1,
            "users": 3,
        }]


class FakeStatusMetrics:
    def __init__(self, rec, *, raise_video_health: bool = False) -> None:
        self.rec = rec
        self.raise_video_health = raise_video_health

    def report_video_health(self, windows):
        self.rec.calls.append(("report_video_health", (windows,), {}))
        if self.raise_video_health:
            raise RuntimeError("boom")
        return {
            "windows": {
                "1h": [{
                    "account": "acc1",
                    "success_rate": 0.5,
                    "avg_attempts_before_200": 2.0,
                    "video_attempts": 4,
                    "video_403": 1,
                    "video_success": 2,
                    "video_success_after_retry": 1,
                    "video_final_fail": 1,
                }],
                "24h": [],
            }
        }


class RecordingAdminStatusDeps:
    def __init__(
        self,
        *,
        admin_ids: set[int] | None = None,
        raise_video_health: bool = False,
        capmonster_key: str | None = "cap-key",
        twocaptcha_key: str | None = "two-key",
    ) -> None:
        self.admin_ids = admin_ids if admin_ids is not None else {7}
        self.calls: list[tuple[str, tuple, dict]] = []
        self.keeper = FakeStatusKeeper(self)
        self.account_pool = FakeStatusPool(self)
        self.metrics = FakeStatusMetrics(self, raise_video_health=raise_video_health)
        self.capmonster_key = capmonster_key
        self.twocaptcha_key = twocaptcha_key

    def captcha_provider(self) -> str:
        self.calls.append(("captcha_provider", (), {}))
        return "auto"

    def get_capmonster_key(self) -> str | None:
        self.calls.append(("capmonster_key", (), {}))
        return self.capmonster_key

    def get_twocaptcha_key(self) -> str | None:
        self.calls.append(("twocaptcha_key", (), {}))
        return self.twocaptcha_key

    def now(self) -> float:
        return 1_000.0

    def bearer_timestamp(self) -> float:
        return 700.0


def _admin_status_deps(**kwargs) -> tuple[
    admin_status_router.AdminStatusDeps, RecordingAdminStatusDeps
]:
    rec = RecordingAdminStatusDeps(**kwargs)
    deps = admin_status_router.AdminStatusDeps(
        admin_ids=rec.admin_ids,
        keeper=rec.keeper,
        account_pool=rec.account_pool,
        metrics=rec.metrics,
        captcha_provider=rec.captcha_provider,
        capmonster_key=rec.get_capmonster_key,
        twocaptcha_key=rec.get_twocaptcha_key,
        time=rec.now,
        bearer_timestamp=rec.bearer_timestamp,
    )
    return deps, rec


class AdminStatusRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deps, self.rec = _admin_status_deps()
        self.router = admin_status_router.create_router(self.deps)
        self.handler = self.router.message.handlers[0].callback

    def test_creates_single_status_handler(self) -> None:
        self.assertIsInstance(self.router, Router)
        self.assertEqual(self.router.name, "tg-admin-status")
        self.assertEqual(len(self.router.message.handlers), 1)
        self.assertEqual(self.router.callback_query.handlers, [])
        self.assertEqual(_handler_commands(self.router.message.handlers[0]), {"status"})

    def test_non_admin_is_denied_without_diagnostics(self) -> None:
        deps, rec = _admin_status_deps(admin_ids=set())
        router = admin_status_router.create_router(deps)
        message = FakeMessage("/status")
        run(router.message.handlers[0].callback(message))
        self.assertEqual(len(message.answers), 1)
        self.assertEqual(rec.calls, [])

    def test_admin_status_composes_session_pool_and_video_health(self) -> None:
        message = FakeMessage("/status")
        run(self.handler(message))
        text = message.answers[-1][0][0]
        kwargs = message.answers[-1][1]
        self.assertEqual(kwargs, {"parse_mode": "HTML"})
        for needle in ("Bearer", "Project ID", "Cookies", "CapMonster", "2captcha", "acc1", "1h", "403"):
            self.assertIn(needle, text)
        self.assertIn(("get_session", (), {}), self.rec.calls)
        self.assertIn(("pool.status", (), {}), self.rec.calls)
        self.assertIn(("report_video_health", ((1, 24),), {}), self.rec.calls)

    def test_video_health_failure_falls_back_without_failing_status(self) -> None:
        deps, _ = _admin_status_deps(raise_video_health=True)
        router = admin_status_router.create_router(deps)
        message = FakeMessage("/status")
        run(router.message.handlers[0].callback(message))
        text = message.answers[-1][0][0]
        self.assertIn("Bearer", text)
        self.assertIn("Project ID", text)

    def test_admin_status_deps_dataclass_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            self.deps.keeper = None  # type: ignore[misc]

    def test_admin_status_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(admin_status_router)
        self.assertNotIn("flow_bot", src)


class RecordingReportsMetrics:
    """Fake metrics facade: records report calls, returns canned shapes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def report_today(self):
        self.calls.append(("report_today", (), {}))
        return {
            "new_users": 3, "active_users": 9, "paying_users": 2,
            "image_generations": 14, "video_generations": 4,
            "success_rate": 0.93, "revenue_rub": 1234.0, "revenue_stars": 700,
            "credits_charged": 210, "credits_refunded": 10,
            "top_actions": [{"event_name": "image_requested", "count": 14}],
        }

    def report_revenue(self, days):
        self.calls.append(("report_revenue", (days,), {}))
        return {
            "revenue_rub": 5000.0, "revenue_stars": 2800,
            "transactions_count": 12, "paying_users": 8,
            "by_package": [{"package_id": "pack100", "count": 5, "rub": 900.0, "stars": 500}],
            "by_day": [{"day": "2026-07-01", "rub": 450.0, "count": 3}],
        }

    def report_flow(self):
        self.calls.append(("report_flow", (), {}))
        return {
            "success_rate": 0.88,
            "by_operation": [{"operation_type": "image", "count": 10, "success": 9, "fail": 1, "avg_duration_ms": 900}],
            "by_model_credits": [{"model": "nb2", "jobs": 10, "flow_credits_delta_sum": 0}],
            "errors_by_type": [{"error_type": "http_500", "count": 1}],
        }

    def report_accounts(self):
        self.calls.append(("report_accounts", (), {}))
        return {
            "accounts": [{
                "account_id": "acc1", "jobs": 5, "success": 4, "fail": 1,
                "credits_remaining": 900, "last_error": None,
            }],
        }

    def report_refs(self):
        self.calls.append(("report_refs", (), {}))
        return {
            "total_referrals": 6, "joined": 5, "rewarded": 4,
            "total_reward_credits": 120,
            "top_referrers": [{"referrer_user_id": 42, "count": 3}],
        }

    def report_channels(self):
        self.calls.append(("report_channels", (), {}))
        return {
            "total_acquired": 11,
            "channels": [{
                "channel": "tg_ads", "users": 11, "paid_users": 2,
                "revenue_stars": 550, "revenue_rub": 990.0,
            }],
        }

    def report_errors(self, days):
        self.calls.append(("report_errors", (days,), {}))
        return {
            "errors_by_type": [{"error_type": "http_403", "count": 2}],
            "recent": [{
                "created_at": "2026-07-02", "operation_type": "video",
                "model": "veo-lite", "error_type": "http_403",
            }],
        }

    def report_cohort_retention(self):
        self.calls.append(("report_cohort_retention", (), {}))
        return [{
            "period": 1, "cohort_date": "2026-06-25",
            "cohort_size": 10, "retained": 4, "rate": 0.4,
        }]


class FakeReportsKeeper:
    def __init__(self, rec) -> None:
        self.rec = rec

    async def get_g_credits(self):
        self.rec.keeper_calls.append("get_g_credits")
        return {"credits": 950, "is_paid": False}


class FakeReportsPool:
    def __init__(self, rec) -> None:
        self.rec = rec

    def account_ids(self):
        self.rec.pool_calls.append("account_ids")
        return ["acc1"]

    def status(self):
        self.rec.pool_calls.append("status")
        return [{
            "id": "acc1", "disabled": False, "cooldown_left": 0,
            "video_allowed": True, "users": 2, "fails": 0,
            "active_image_jobs": 0, "image_capacity": 2,
            "active_video_jobs": 0, "video_capacity": 1,
        }]


class RecordingAdminReportsDeps:
    def __init__(self, *, admin: bool = True) -> None:
        self.admin = admin
        self.metrics = RecordingReportsMetrics()
        self.pool_calls: list[str] = []
        self.keeper_calls: list[str] = []
        self.account_pool = FakeReportsPool(self)
        self.gate_calls: list[int] = []

    def admin_only(self, message) -> bool:
        self.gate_calls.append(message.from_user.id)
        return self.admin

    def keeper_for_acc(self, acc_id):
        return FakeReportsKeeper(self)

    def bot_username(self) -> str:
        return "test_bot"


def _admin_reports_deps(*, admin: bool = True) -> tuple[
    admin_reports_router.AdminReportsDeps, RecordingAdminReportsDeps
]:
    rec = RecordingAdminReportsDeps(admin=admin)
    deps = admin_reports_router.AdminReportsDeps(
        admin_only=rec.admin_only,
        metrics=rec.metrics,
        account_pool=rec.account_pool,
        keeper_for_acc=rec.keeper_for_acc,
        bot_username=rec.bot_username,
    )
    return deps, rec


_ADMIN_REPORT_COMMANDS = (
    "admin_today", "admin_revenue", "admin_flow", "admin_accounts",
    "admin_refs", "admin_channels", "admin_errors", "admin_cohort",
)


class AdminReportsRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.deps, self.rec = _admin_reports_deps()
        self.router = admin_reports_router.create_router(self.deps)

    def _call(self, router, command: str, text: str):
        for handler in router.message.handlers:
            if _handler_commands(handler) == {command}:
                message = FakeMessage(text)
                run(handler.callback(message))
                return message
        raise AssertionError(f"no handler for {command}")

    def test_creates_router_with_eight_report_handlers(self) -> None:
        self.assertIsInstance(self.router, Router)
        handlers = self.router.message.handlers
        self.assertEqual(len(handlers), 8)
        self.assertEqual(self.router.callback_query.handlers, [])
        matched = [_handler_commands(h) for h in handlers]
        for expected in _ADMIN_REPORT_COMMANDS:
            self.assertIn({expected}, matched)

    def test_every_command_denies_non_admin_without_side_effects(self) -> None:
        # Security-sensitive gate: a non-admin must get the denial text and
        # NO metrics/pool/keeper call may happen for any of the 8 commands.
        for command in _ADMIN_REPORT_COMMANDS:
            with self.subTest(command=command):
                deps, rec = _admin_reports_deps(admin=False)
                router = admin_reports_router.create_router(deps)
                message = self._call(router, command, f"/{command}")
                self.assertEqual(len(message.answers), 1)
                self.assertEqual(rec.gate_calls, [7])  # gate consulted once
                self.assertEqual(rec.metrics.calls, [])
                self.assertEqual(rec.pool_calls, [])
                self.assertEqual(rec.keeper_calls, [])

    def test_every_command_calls_its_report_for_admin(self) -> None:
        expected_report = {
            "admin_today": "report_today",
            "admin_revenue": "report_revenue",
            "admin_flow": "report_flow",
            "admin_accounts": "report_accounts",
            "admin_refs": "report_refs",
            "admin_channels": "report_channels",
            "admin_errors": "report_errors",
            "admin_cohort": "report_cohort_retention",
        }
        for command, report in expected_report.items():
            with self.subTest(command=command):
                deps, rec = _admin_reports_deps()
                router = admin_reports_router.create_router(deps)
                message = self._call(router, command, f"/{command}")
                self.assertIn(report, [c[0] for c in rec.metrics.calls])
                self.assertTrue(message.answers)

    def test_admin_channels_with_slug_builds_deep_link(self) -> None:
        message = self._call(self.router, "admin_channels", "/admin_channels my_channel")
        text = message.answers[-1][0][0]
        self.assertIn("https://t.me/test_bot?start=", text)
        self.assertIn("my_channel", text)
        # Link mode must not read the channels report.
        self.assertEqual(self.rec.metrics.calls, [])

    def test_admin_accounts_gathers_g_credits_per_pool_account(self) -> None:
        message = self._call(self.router, "admin_accounts", "/admin_accounts")
        self.assertIn("account_ids", self.rec.pool_calls)
        self.assertIn("status", self.rec.pool_calls)
        self.assertEqual(self.rec.keeper_calls, ["get_g_credits"])
        self.assertIn("acc1", message.answers[-1][0][0])

    def test_admin_reports_deps_dataclass_is_frozen(self) -> None:
        with self.assertRaises(Exception):
            self.deps.metrics = None  # type: ignore[misc]

    def test_admin_reports_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(admin_reports_router)
        self.assertNotIn("flow_bot", src)


class FakeCreditStore:
    """In-memory credit store recording every mutation."""

    def __init__(self, balances: dict[int, int] | None = None) -> None:
        self.balances: dict[int, int] = dict(balances or {})
        self.calls: list[tuple[str, tuple, dict]] = []

    def add(self, user_id: int, amount: int) -> int:
        self.calls.append(("add", (user_id, amount), {}))
        self.balances[user_id] = self.balances.get(user_id, 0) + amount
        return self.balances[user_id]

    def balance(self, user_id: int) -> int:
        self.calls.append(("balance", (user_id,), {}))
        return self.balances.get(user_id, 0)

    def charge(self, user_id: int, amount: int) -> int:
        self.calls.append(("charge", (user_id, amount), {}))
        self.balances[user_id] = self.balances.get(user_id, 0) - amount
        return self.balances[user_id]


class FakePaymentStore:
    def __init__(self, records: list[dict] | None = None) -> None:
        self.records = records or []
        self.calls: list[tuple[str, tuple, dict]] = []

    def find_by_charge(self, charge_id: str):
        self.calls.append(("find_by_charge", (charge_id,), {}))
        for rec in self.records:
            if rec["charge_id"] == charge_id:
                return rec
        return None

    def last_for_user(self, user_id: int):
        self.calls.append(("last_for_user", (user_id,), {}))
        for rec in reversed(self.records):
            if rec["user_id"] == user_id:
                return rec
        return None

    def mark_refunded(self, charge_id: str):
        self.calls.append(("mark_refunded", (charge_id,), {}))
        for rec in self.records:
            if rec["charge_id"] == charge_id:
                rec["refunded"] = True


class FakeCreditsMetrics:
    def __init__(self, *, promo_credits: int | None = None) -> None:
        self.promo_credits = promo_credits
        self.calls: list[tuple[str, tuple, dict]] = []

    def redeem_promo(self, code: str, user_id: int):
        self.calls.append(("redeem_promo", (code, user_id), {}))
        return self.promo_credits

    def log_event(self, *args, **kwargs):
        self.calls.append(("log_event", args, kwargs))

    def create_promo_code(self, *args, **kwargs):
        self.calls.append(("create_promo_code", args, kwargs))
        return True

    def mark_user_blocked(self, user_id: int):
        self.calls.append(("mark_user_blocked", (user_id,), {}))


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    async def send_message(self, *args, **kwargs):
        self.calls.append(("send_message", args, kwargs))

    async def refund_star_payment(self, *args, **kwargs):
        self.calls.append(("refund_star_payment", args, kwargs))


class RecordingAdminCreditsDeps:
    def __init__(
        self,
        *,
        admin_ids: set[int] | None = None,
        owner_ids: set[int] | None = None,
        promo_credits: int | None = None,
        payments: list[dict] | None = None,
        balances: dict[int, int] | None = None,
    ) -> None:
        self.admin_ids = admin_ids if admin_ids is not None else {7}
        self.owner_ids = owner_ids if owner_ids is not None else {7}
        self.metrics = FakeCreditsMetrics(promo_credits=promo_credits)
        self.credit_store = FakeCreditStore(balances)
        self.payment_store = FakePaymentStore(payments)
        self.bot = FakeBot()
        self.workspaces: dict[int, dict] = {}
        self.calls: list[tuple[str, tuple, dict]] = []
        self.log = SimpleNamespace(
            info=lambda *a, **k: self.calls.append(("log.info", a, k)),
            exception=lambda *a, **k: self.calls.append(("log.exception", a, k)),
        )

    def workspace(self, user_id: int) -> dict:
        return self.workspaces.setdefault(user_id, {})

    def username(self, message) -> str | None:
        return "tester"

    async def send_owner_alert(self, text: str):
        self.calls.append(("send_owner_alert", (text,), {}))

    def clawback_referral_rewards(self, *args, **kwargs):
        self.calls.append(("clawback_referral_rewards", args, kwargs))


def _admin_credits_deps(**kwargs) -> tuple[
    admin_credits_router.AdminCreditsDeps, RecordingAdminCreditsDeps
]:
    rec = RecordingAdminCreditsDeps(**kwargs)
    deps = admin_credits_router.AdminCreditsDeps(
        workspace=rec.workspace,
        admin_ids=rec.admin_ids,
        owner_ids=rec.owner_ids,
        metrics=rec.metrics,
        credit_store=rec.credit_store,
        payment_store=rec.payment_store,
        bot=rec.bot,
        username=rec.username,
        send_owner_alert=rec.send_owner_alert,
        clawback_referral_rewards=rec.clawback_referral_rewards,
        log=rec.log,
    )
    return deps, rec


class AdminCreditsRouterTests(unittest.TestCase):
    def _call(self, router, command: str, text: str):
        for handler in router.message.handlers:
            if _handler_commands(handler) == {command}:
                message = FakeMessage(text)
                run(handler.callback(message))
                return message
        raise AssertionError(f"no handler for {command}")

    def test_creates_router_with_four_command_handlers(self) -> None:
        deps, _ = _admin_credits_deps()
        router = admin_credits_router.create_router(deps)
        self.assertIsInstance(router, Router)
        handlers = router.message.handlers
        self.assertEqual(len(handlers), 4)
        self.assertEqual(router.callback_query.handlers, [])
        matched = [_handler_commands(h) for h in handlers]
        for expected in ({"promo"}, {"addpromo"}, {"grant"}, {"refund"}):
            self.assertIn(expected, matched)

    def test_grant_and_refund_deny_non_admin_without_credit_mutation(self) -> None:
        for command, text in (("grant", "/grant 55 100"), ("refund", "/refund 55")):
            with self.subTest(command=command):
                deps, rec = _admin_credits_deps(admin_ids=set(), owner_ids=set())
                router = admin_credits_router.create_router(deps)
                message = self._call(router, command, text)
                self.assertEqual(len(message.answers), 1)
                self.assertEqual(rec.credit_store.calls, [])
                self.assertEqual(rec.payment_store.calls, [])
                self.assertEqual(rec.bot.calls, [])
                self.assertEqual(rec.metrics.calls, [])

    def test_addpromo_denies_non_owner_without_promo_creation(self) -> None:
        deps, rec = _admin_credits_deps(owner_ids=set())
        router = admin_credits_router.create_router(deps)
        message = self._call(router, "addpromo", "/addpromo CODE 50")
        self.assertEqual(len(message.answers), 1)
        self.assertEqual(rec.metrics.calls, [])

    def test_grant_bad_syntax_is_rejected_without_mutation(self) -> None:
        for text in ("/grant", "/grant abc 10", "/grant 55 xyz", "/grant 55 0", "/grant 55 -5"):
            with self.subTest(text=text):
                deps, rec = _admin_credits_deps()
                router = admin_credits_router.create_router(deps)
                message = self._call(router, "grant", text)
                self.assertEqual(len(message.answers), 1)
                self.assertEqual(rec.credit_store.calls, [])
                self.assertEqual(rec.bot.calls, [])

    def test_grant_adds_credits_and_notifies_target(self) -> None:
        deps, rec = _admin_credits_deps()
        router = admin_credits_router.create_router(deps)
        message = self._call(router, "grant", "/grant 55 100")
        self.assertIn(("add", (55, 100), {}), rec.credit_store.calls)
        self.assertEqual(rec.credit_store.balances[55], 100)
        # Target is not the admin -> gets a Telegram notification.
        self.assertEqual(rec.bot.calls[-1][0], "send_message")
        self.assertEqual(rec.bot.calls[-1][1][0], 55)
        self.assertTrue(message.answers)

    def test_refund_returns_stars_charges_credits_and_claws_back(self) -> None:
        payments = [{
            "user_id": 55, "charge_id": "chg_1", "credits": 100,
            "stars": 50, "refunded": False,
        }]
        deps, rec = _admin_credits_deps(payments=payments, balances={55: 60})
        router = admin_credits_router.create_router(deps)
        message = self._call(router, "refund", "/refund 55")
        self.assertIn(("refund_star_payment", (), {
            "user_id": 55, "telegram_payment_charge_id": "chg_1",
        }), rec.bot.calls)
        self.assertIn(("mark_refunded", ("chg_1",), {}), rec.payment_store.calls)
        # Clawback never goes below zero: balance 60 < credited 100 -> take 60.
        self.assertIn(("charge", (55, 60), {}), rec.credit_store.calls)
        self.assertIn(("clawback_referral_rewards", (55, "chg_1"), {}), rec.calls)
        self.assertIn(
            "credits_refunded",
            [c[1][0] for c in rec.metrics.calls if c[0] == "log_event"],
        )
        self.assertTrue(message.answers)

    def test_refund_already_refunded_is_a_noop(self) -> None:
        payments = [{
            "user_id": 55, "charge_id": "chg_1", "credits": 100,
            "stars": 50, "refunded": True,
        }]
        deps, rec = _admin_credits_deps(payments=payments, balances={55: 60})
        router = admin_credits_router.create_router(deps)
        message = self._call(router, "refund", "/refund 55")
        self.assertEqual(len(message.answers), 1)
        self.assertEqual(rec.bot.calls, [])
        self.assertEqual(rec.credit_store.calls, [])

    def test_promo_unknown_code_gives_no_credits(self) -> None:
        deps, rec = _admin_credits_deps(promo_credits=None)
        router = admin_credits_router.create_router(deps)
        message = self._call(router, "promo", "/promo NOPE")
        self.assertIn(("redeem_promo", ("NOPE", 7), {}), rec.metrics.calls)
        self.assertEqual(rec.credit_store.calls, [])
        self.assertEqual(len(message.answers), 1)

    def test_promo_valid_code_credits_and_logs(self) -> None:
        deps, rec = _admin_credits_deps(promo_credits=30)
        router = admin_credits_router.create_router(deps)
        message = self._call(router, "promo", "/promo WELCOME")
        self.assertIn(("add", (7, 30), {}), rec.credit_store.calls)
        self.assertIn(
            "promo_redeemed",
            [c[1][0] for c in rec.metrics.calls if c[0] == "log_event"],
        )
        self.assertTrue(message.answers)

    def test_promo_without_code_sets_await_state(self) -> None:
        deps, rec = _admin_credits_deps()
        router = admin_credits_router.create_router(deps)
        self._call(router, "promo", "/promo")
        self.assertEqual(rec.workspaces[7]["await"], "promo")
        self.assertEqual(rec.credit_store.calls, [])

    def test_admin_credits_deps_dataclass_is_frozen(self) -> None:
        deps, _ = _admin_credits_deps()
        with self.assertRaises(Exception):
            deps.credit_store = None  # type: ignore[misc]

    def test_admin_credits_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(admin_credits_router)
        self.assertNotIn("flow_bot", src)


class FakeStatusMessage:
    def __init__(self, rec) -> None:
        self.rec = rec

    async def edit_text(self, *args, **kwargs):
        self.rec.calls.append(("status.edit_text", args, kwargs))

    async def delete(self):
        self.rec.calls.append(("status.delete", (), {}))


class FakeUploadMessage:
    def __init__(self, rec, *, video=None, document=None, caption=None, user_id: int = 7) -> None:
        self.rec = rec
        self.video = video
        self.document = document
        self.caption = caption
        self.from_user = SimpleNamespace(id=user_id)
        self.answers: list[tuple[tuple, dict]] = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))
        return FakeStatusMessage(self.rec)


class FakeUploadKeeper:
    def __init__(self, rec) -> None:
        self.rec = rec

    async def upload_video(self, data, **kwargs):
        self.rec.calls.append(("upload_video", (len(data),), kwargs))
        return dict(self.rec.upload_result) if self.rec.upload_result else self.rec.upload_result


class FakeUploadClient:
    def __init__(self, rec) -> None:
        self.rec = rec

    async def wait_video_ready(self, media_id, project_id):
        self.rec.calls.append(("wait_video_ready", (media_id, project_id), {}))
        return self.rec.ready_item


class RecordingVideoUploadInputDeps:
    def __init__(
        self,
        *,
        enabled: bool = True,
        acc_id: str | None = "acc1",
        upload_result: dict | None = None,
        ready_item: dict | None = None,
    ) -> None:
        self.enabled = enabled
        self.acc_id = acc_id
        self.upload_result = {"mediaId": "m1"} if upload_result is None else upload_result
        self.ready_item = {"duration_seconds": 4} if ready_item is None else ready_item
        self.workspaces: dict[int, dict] = {}
        self.calls: list[tuple[str, tuple, dict]] = []
        self.bot = SimpleNamespace(download=self._download)
        self.log = SimpleNamespace(
            exception=lambda *a, **k: self.calls.append(("log.exception", a, k)),
            warning=lambda *a, **k: self.calls.append(("log.warning", a, k)),
        )
        self.metrics = SimpleNamespace(
            log_event=lambda *a, **k: self.calls.append(("log_event", a, k)),
        )

    async def _download(self, file_id):
        self.calls.append(("bot.download", (file_id,), {}))
        return b"clip-bytes"

    def workspace(self, user_id: int) -> dict:
        return self.workspaces.setdefault(user_id, {})

    def upload_video_edit_enabled(self) -> bool:
        return self.enabled

    def account_for_video(self, user_id: int):
        self.calls.append(("account_for_video", (user_id,), {}))
        return self.acc_id

    async def ensure_user_project(self, user_id: int, *, account_id=None):
        self.calls.append(("ensure_user_project", (user_id,), {"account_id": account_id}))
        return "proj1"

    def keeper_for_acc(self, acc_id):
        return FakeUploadKeeper(self)

    def client_for_acc(self, acc_id):
        return FakeUploadClient(self)

    async def video_edit_uploaded(self, *args, **kwargs):
        self.calls.append(("video_edit_uploaded", args, kwargs))


def _video_upload_input_deps(**kwargs) -> tuple[
    video_upload_input_router.VideoUploadInputDeps, RecordingVideoUploadInputDeps
]:
    rec = RecordingVideoUploadInputDeps(**kwargs)
    deps = video_upload_input_router.VideoUploadInputDeps(
        workspace=rec.workspace,
        upload_video_edit_enabled=rec.upload_video_edit_enabled,
        bot=rec.bot,
        log=rec.log,
        metrics=rec.metrics,
        account_for_video=rec.account_for_video,
        ensure_user_project=rec.ensure_user_project,
        keeper_for_acc=rec.keeper_for_acc,
        client_for_acc=rec.client_for_acc,
        video_edit_uploaded=rec.video_edit_uploaded,
    )
    return deps, rec


def _fake_video(duration: int = 3):
    return SimpleNamespace(file_id="vf1", mime_type="video/mp4", duration=duration)


class VideoUploadInputRouterTests(unittest.TestCase):
    def _handler(self, deps):
        router = video_upload_input_router.create_router(deps)
        return router.message.handlers[0].callback

    def test_creates_message_input_router(self) -> None:
        deps, _ = _video_upload_input_deps()
        router = video_upload_input_router.create_router(deps)
        self.assertIsInstance(router, Router)
        self.assertEqual(router.name, "tg-video-upload-input")
        self.assertEqual(len(router.message.handlers), 1)
        self.assertEqual(router.callback_query.handlers, [])
        self.assertEqual(router.message.handlers[0].callback.__name__, "handle_video_upload")

    def test_disabled_feature_ignores_video_silently(self) -> None:
        deps, rec = _video_upload_input_deps(enabled=False)
        rec.workspaces[7] = {"vawait": "vu_video"}
        message = FakeUploadMessage(rec, video=_fake_video())
        run(self._handler(deps)(message))
        self.assertEqual(message.answers, [])
        self.assertEqual(rec.calls, [])

    def test_wrong_await_state_ignores_video_silently(self) -> None:
        deps, rec = _video_upload_input_deps(enabled=True)
        rec.workspaces[7] = {"vawait": None}
        message = FakeUploadMessage(rec, video=_fake_video())
        run(self._handler(deps)(message))
        self.assertEqual(message.answers, [])
        self.assertEqual(rec.calls, [])

    def test_non_video_document_is_rejected_without_download(self) -> None:
        deps, rec = _video_upload_input_deps()
        rec.workspaces[7] = {"vawait": "vu_video"}
        doc = SimpleNamespace(file_id="df1", mime_type="image/png", duration=0)
        message = FakeUploadMessage(rec, document=doc)
        run(self._handler(deps)(message))
        self.assertEqual(len(message.answers), 1)
        self.assertNotIn("bot.download", [c[0] for c in rec.calls])

    def test_video_with_caption_uploads_and_starts_edit(self) -> None:
        deps, rec = _video_upload_input_deps()
        rec.workspaces[7] = {"vawait": "vu_video"}
        message = FakeUploadMessage(rec, video=_fake_video(), caption="make it fly")
        run(self._handler(deps)(message))
        names = [c[0] for c in rec.calls]
        for step in ("bot.download", "account_for_video", "ensure_user_project",
                     "upload_video", "wait_video_ready", "log_event", "video_edit_uploaded"):
            self.assertIn(step, names, step)
        st = rec.workspaces[7]
        self.assertEqual(st["vu_source"]["mediaId"], "m1")
        self.assertEqual(st["vu_source"]["_account_id"], "acc1")
        self.assertIsNone(st["vawait"])
        # Caption became the edit prompt directly.
        edit_call = [c for c in rec.calls if c[0] == "video_edit_uploaded"][-1]
        self.assertEqual(edit_call[1][1], "make it fly")
        self.assertEqual(edit_call[2], {"user_id": 7})

    def test_video_without_caption_asks_for_edit_prompt(self) -> None:
        deps, rec = _video_upload_input_deps()
        rec.workspaces[7] = {"vawait": "vu_video"}
        message = FakeUploadMessage(rec, video=_fake_video())
        run(self._handler(deps)(message))
        self.assertEqual(rec.workspaces[7]["vawait"], "vu_edit_prompt")
        self.assertNotIn("video_edit_uploaded", [c[0] for c in rec.calls])
        # status message + ask-prompt reply
        self.assertEqual(len(message.answers), 2)

    def test_no_available_account_reports_and_stops(self) -> None:
        deps, rec = _video_upload_input_deps(acc_id=None)
        rec.workspaces[7] = {"vawait": "vu_video"}
        message = FakeUploadMessage(rec, video=_fake_video())
        run(self._handler(deps)(message))
        names = [c[0] for c in rec.calls]
        self.assertIn("status.edit_text", names)
        self.assertNotIn("upload_video", names)

    def test_video_upload_input_deps_dataclass_is_frozen(self) -> None:
        deps, _ = _video_upload_input_deps()
        with self.assertRaises(Exception):
            deps.bot = None  # type: ignore[misc]

    def test_video_upload_input_module_does_not_import_flow_bot(self) -> None:
        src = inspect.getsource(video_upload_input_router)
        self.assertNotIn("flow_bot", src)


if __name__ == "__main__":
    unittest.main()
