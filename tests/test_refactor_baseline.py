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

    def test_consumer_main_menu_routes_are_baselined(self) -> None:
        start = self.flow_bot.index("def main_menu_kb")
        end = self.flow_bot.index("# ── Маркетплейс-меню", start)
        block = self.flow_bot[start:end]
        for callback_data in ("m:gen", "m:vid", "m:animate", "m:myphoto", "m:ideas", "m:balance"):
            self.assertIn(f'"{callback_data}"', block)
        self.assertIn("video_animate_min_price()", block)
        self.assertIn("show_repeat parameter kept for backward compatibility", block)

    def test_result_actions_use_stable_callback_contract(self) -> None:
        start = self.flow_bot.index("def _image_keyboard")
        end = self.flow_bot.index("def _seller_image_keyboard", start)
        block = self.flow_bot[start:end]
        self.assertIn('action_callback_data("edit", token)', block)
        self.assertIn('callback_data="m:repeat"', block)
        self.assertIn('action_callback_data("realup", token)', block)
        self.assertIn('callback_data=f"an:img:{token}"', block)

        for action in ("edit", "realup", "mpexport", "skuadd"):
            self.assertRegex(self.flow_bot, rf'action_callback_data\("{action}", token\)')
        for action in ("regen", "revary", "up2x"):
            self.assertIn(action, self.flow_core)
        self.assertIn("def parse_action_callback", self.flow_core)

    def test_credit_refund_invariant_is_baselined(self) -> None:
        start = self.flow_bot.index("async def credit_gate")
        end = self.flow_bot.index("def _project_key", start)
        block = self.flow_bot[start:end]
        self.assertLess(block.index("credit_store.charge(user_id, price)"), block.index("yield charge"))
        self.assertIn("finally:", block)
        self.assertIn("if not charge.ok:", block)
        self.assertIn("credit_store.refund(user_id, price)", block)

        video_start = self.flow_bot.index("async def _do_video_generate_and_send")
        video_block = self.flow_bot[video_start:video_start + 7000]
        self.assertIn("credit_store.charge(user_id, total_price)", video_block)
        self.assertIn("credit_store.refund(user_id, refund_amt)", video_block)

    def test_payment_and_referral_order_is_baselined(self) -> None:
        start = self.flow_bot.index("async def on_successful_payment")
        end = self.flow_bot.index("def _robokassa_provider_payment_id", start)
        block = self.flow_bot[start:end]
        tx_at = block.index("metrics.record_transaction_status(")
        add_at = block.index("credit_store.add(user_id")
        referral_at = block.index("_maybe_apply_referral_rewards(")
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
        block = self.flow_bot[start:start + 450]
        self.assertIn('req.get("kind") == "i2i"', block)
        self.assertIn('req.get("kind") == "video_ingredients"', block)
        self.assertIn("_backend_generate_images(req)", block)

    def test_no_refactor_branch_runtime_entrypoints_yet(self) -> None:
        """Phase 0 should not introduce MAX/runtime entrypoints."""
        self.assertNotIn("MAX_ENABLED", self.flow_bot)
        self.assertIsNone(re.search(r"channels[./\\\\]max", self.flow_bot))
