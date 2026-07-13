from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RefactorBaselineTests(unittest.TestCase):
    """Source-level guards for the multi-platform refactor branch.

    These tests intentionally avoid live Telegram/Flow/payment calls. They lock
    the Telegram monolith's current behavior before code is moved behind shared
    services and channel adapters.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.flow_bot = (PROJECT_ROOT / "flow_bot.py").read_text(encoding="utf-8")
        cls.flow_core = (PROJECT_ROOT / "flow_core.py").read_text(encoding="utf-8")
        cls.seller_backend = (PROJECT_ROOT / "seller_backend.py").read_text(encoding="utf-8")
        cls.kb_src = (PROJECT_ROOT / "channels" / "telegram" / "keyboards.py").read_text(encoding="utf-8")
        cls.payments_router = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "payments.py"
        ).read_text(encoding="utf-8")

    def test_consumer_main_menu_routes_are_baselined(self) -> None:
        # main_menu_kb moved to channels/telegram/keyboards.py (Phase 5).
        kb_src = (PROJECT_ROOT / "channels" / "telegram" / "keyboards.py").read_text(encoding="utf-8")
        start = kb_src.index("def main_menu_kb")
        end = kb_src.index("def topup_method_kb", start)
        block = kb_src[start:end]
        for callback_data in ("m:gen", "m:vid", "m:animate", "m:myphoto", "m:ideas", "m:balance"):
            self.assertIn(f'"{callback_data}"', block)
        self.assertIn("video_animate_min_price()", block)
        self.assertIn("show_repeat parameter kept for backward compatibility", block)

    def test_result_actions_use_stable_callback_contract(self) -> None:
        start = self.kb_src.index("def _image_keyboard")
        end = self.kb_src.index("def _seller_image_keyboard", start)
        block = self.kb_src[start:end]
        self.assertIn('action_callback_data("edit", token)', block)
        self.assertIn('callback_data="m:repeat"', block)
        self.assertIn('action_callback_data("realup", token)', block)
        self.assertIn('callback_data=f"an:img:{token}"', block)

        for action in ("edit", "realup", "mpexport", "skuadd"):
            self.assertRegex(self.kb_src, rf'action_callback_data\("{action}", token\)')
        for action in ("regen", "revary", "up2x"):
            self.assertIn(action, self.flow_core)
        self.assertIn("def parse_action_callback", self.flow_core)

    def test_credit_refund_invariant_is_baselined(self) -> None:
        # flow_bot keeps a thin credit_gate wrapper; the async context manager
        # moved to channels.telegram.request_gate (Phase 11) and the charge-on-
        # success / refund-on-failure rule lives in billing/credit_gate.py (PR-7a).
        self.assertIn("def credit_gate(", self.flow_bot)
        request_gate = (
            PROJECT_ROOT / "channels" / "telegram" / "request_gate.py"
        ).read_text(encoding="utf-8")
        self.assertIn("async def credit_gate", request_gate)
        gate = (PROJECT_ROOT / "billing" / "credit_gate.py").read_text(encoding="utf-8")
        self.assertLess(gate.index("store.charge(user_id, price)"), gate.index("yield charge"))
        self.assertIn("finally:", gate)
        self.assertIn("if not charge.ok:", gate)
        self.assertIn("store.refund(user_id, price)", gate)

        # Video generate-and-send moved to channels.telegram.video_flow (Phase 11);
        # deps are injected, hence the ``d.credit_store`` prefix.
        video_flow = (
            PROJECT_ROOT / "channels" / "telegram" / "video_flow.py"
        ).read_text(encoding="utf-8")
        video_start = video_flow.index("async def do_generate_and_send")
        video_block = video_flow[video_start:video_start + 20000]
        self.assertIn("d.credit_store.charge(user_id, total_price)", video_block)
        self.assertIn("d.credit_store.refund(user_id, refund_amt)", video_block)

    def test_payment_and_referral_order_is_baselined(self) -> None:
        start = self.payments_router.index("async def on_successful_payment")
        block = self.payments_router[start:start + 2600]
        tx_at = block.index("deps.metrics.record_transaction_status(")
        add_at = block.index("deps.credit_store.add(user_id")
        referral_at = block.index("deps.maybe_apply_referral_rewards(")
        self.assertLess(tx_at, add_at)
        self.assertLess(add_at, referral_at)
        self.assertIn('provider="telegram_stars"', block)
        self.assertIn("provider_payment_id", block)

    def test_internal_generate_contract_is_baselined(self) -> None:
        self.assertIn('app.router.add_post("/internal/generate", handle_generate)', self.seller_backend)
        self.assertIn('"kind": kind', self.seller_backend)
        self.assertIn('"image_b64"', self.seller_backend)
        self.assertIn('"video_model"', self.seller_backend)

        start = self.flow_bot.index("async def _backend_generate(req")
        block = self.flow_bot[start:start + 700]
        self.assertIn('req.get("kind") == "i2i"', block)
        self.assertIn('req.get("kind") == "video_text"', block)
        self.assertIn('req.get("kind") == "video_ingredients"', block)
        self.assertIn("_backend_generate_images(req)", block)

    def test_max_runtime_entrypoint_is_wired_but_guarded(self) -> None:
        """Phase 11: MAX startup is wired into flow_bot but disabled by default.

        (Supersedes the Phase-0 "no MAX entrypoints yet" guard now that the MAX
        MVP runtime exists.) The wiring must go through the guarded helper, never
        an unconditional start.
        """
        self.assertIn("def _maybe_start_max_bot", self.flow_bot)
        self.assertIn("_maybe_start_max_bot()", self.flow_bot)
        # run_max import + build_runtime moved to channels.telegram.max_bootstrap.
        max_src = (
            PROJECT_ROOT / "channels" / "telegram" / "max_bootstrap.py"
        ).read_text(encoding="utf-8")
        self.assertIn("from channels.max.runtime import run_max", max_src)
        # run_max is a no-op unless MAX_ENABLED=1 (checked in channels/max/client.py),
        # so flow_bot must not force-enable it.
        self.assertNotIn('MAX_ENABLED"] = "1"', self.flow_bot)
