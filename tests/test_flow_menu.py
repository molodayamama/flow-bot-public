from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import flow_core
import flow_copy


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PricingTests(unittest.TestCase):
    def test_price_per_image_and_gen_totals(self) -> None:
        self.assertEqual(flow_core.price_gen(1), 10)
        self.assertEqual(flow_core.price_gen(2), 20)
        self.assertEqual(flow_core.price_gen(4), 40)

    def test_action_prices(self) -> None:
        self.assertEqual(flow_core.action_price("gen", 4), 40)
        self.assertEqual(flow_core.action_price("regen", 2), 20)
        self.assertEqual(flow_core.action_price("revary"), 20)   # ~2 images
        self.assertEqual(flow_core.action_price("edit"), 10)
        self.assertEqual(flow_core.action_price("myphoto"), 10)
        self.assertEqual(flow_core.action_price("up2x"), 5)      # quick enhance
        self.assertEqual(flow_core.action_price("realup"), 5)    # true HD upscale
        self.assertEqual(flow_core.action_price("video_prompt_edit"), 20)
        self.assertEqual(flow_core.action_price("dl_raw"), 0)    # free
        self.assertEqual(flow_core.action_price("unknown"), 0)

    def test_upscale_is_half_of_one_image(self) -> None:
        self.assertEqual(flow_core.UPSCALE_PRICE, flow_core.PRICE_PER_IMAGE // 2)

    def test_video_prices_are_bot_retail_prices(self) -> None:
        self.assertEqual(flow_core.video_price("omni-flash-4s"), 20)
        self.assertEqual(flow_core.video_price("omni-flash-6s"), 30)
        self.assertEqual(flow_core.video_price("omni-flash-8s"), 35)
        self.assertEqual(flow_core.video_price("omni-flash-10s"), 45)
        self.assertEqual(flow_core.video_price("veo-lite"), 30)
        self.assertEqual(flow_core.video_price("veo-fast"), 60)
        self.assertEqual(flow_core.video_price("veo-quality"), 290)

    def test_video_reference_mode_surcharges(self) -> None:
        self.assertEqual(flow_core.video_price("veo-fast", mode="ingredients"), 70)
        self.assertEqual(flow_core.video_price("veo-fast", mode="frames"), 80)


class UpscaleCaptureTests(unittest.TestCase):
    """The real upscale learns its request shape once, then replays it."""

    def test_capture_and_replay_round_trip(self) -> None:
        media_id = "aaaaaaaa-1111-2222-3333-444444444444"
        url = f"https://api/v1/projects/OLDPROJ/flowMedia:upscale/{media_id}"
        body = {
            "clientContext": {
                "projectId": "OLDPROJ",
                "sessionId": ";999",
                "recaptchaContext": {"token": "OLDCAPTCHA"},
            },
            "mediaId": media_id,
            "upscaleFactor": 2,
        }
        cap = flow_core.build_request_capture(url, body, {media_id})
        self.assertTrue(cap["resolved"])

        built = flow_core.apply_request_capture(
            cap, media_id="NEWMEDIA", captcha="NEWCAP",
            project_id="NEWPROJ", session_id=";123",
        )
        self.assertIsNotNone(built)
        new_url, new_body = built
        # New values substituted; old ones gone.
        self.assertIn("NEWMEDIA", new_url)
        self.assertIn("NEWPROJ", new_url)
        self.assertNotIn(media_id, new_url)
        self.assertNotIn("OLDPROJ", new_url)
        self.assertEqual(new_body["mediaId"], "NEWMEDIA")
        self.assertEqual(new_body["clientContext"]["projectId"], "NEWPROJ")
        self.assertEqual(new_body["clientContext"]["sessionId"], ";123")
        self.assertEqual(new_body["clientContext"]["recaptchaContext"]["token"], "NEWCAP")
        self.assertEqual(new_body["upscaleFactor"], 2)  # constants preserved

    def test_unresolved_when_no_known_id_matches(self) -> None:
        cap = flow_core.build_request_capture(
            "https://api/v1/projects/P/flowMedia:upscale",
            {"mediaId": "unknown-id"},
            {"some-other-id"},
        )
        self.assertFalse(cap["resolved"])
        self.assertIsNone(
            flow_core.apply_request_capture(
                cap, media_id="x", captcha="y", project_id="z", session_id=";1"
            )
        )

    def test_packs_have_volume_discount_and_one_best(self) -> None:
        rates = [(p["credits"] / p["stars"]) for p in flow_core.STARS_PACKS.values()]
        # Larger packs give more credits per star (monotonic non-decreasing).
        self.assertEqual(rates, sorted(rates))
        best = [pid for pid, p in flow_core.STARS_PACKS.items() if p.get("best")]
        self.assertEqual(len(best), 1)

    def test_pack_label_shows_credits_stars_and_generations(self) -> None:
        label = flow_core.pack_label("large")
        self.assertIn("700", label)            # credits
        self.assertIn("450", label)            # stars
        self.assertIn("70", label)             # generations = 700 / 10
        self.assertIn("ген", label)
        self.assertIn("Выгодно", label)        # best-value marker


class CreditStoreTests(unittest.TestCase):
    def _store(self, tmp: str, starter: int = 50) -> flow_core.CreditStore:
        return flow_core.CreditStore(Path(tmp) / "credits.json", starter=starter)

    def test_starter_granted_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            s = self._store(tmp, starter=50)
            self.assertEqual(s.balance(1), 50)
            # Charging then re-reading must NOT re-grant the starter.
            self.assertTrue(s.charge(1, 30))
            self.assertEqual(s.balance(1), 20)

    def test_starter_persists_across_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "credits.json"
            s1 = flow_core.CreditStore(path, starter=50)
            s1.charge(7, 40)
            self.assertEqual(s1.balance(7), 10)
            s2 = flow_core.CreditStore(path, starter=50)
            self.assertEqual(s2.balance(7), 10)  # not re-granted

    def test_charge_refund_and_affordability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            s = self._store(tmp, starter=20)
            self.assertTrue(s.can_afford(1, 20))
            self.assertFalse(s.can_afford(1, 21))
            self.assertFalse(s.charge(1, 100))   # too expensive, no change
            self.assertEqual(s.balance(1), 20)
            self.assertTrue(s.charge(1, 15))
            self.assertEqual(s.balance(1), 5)
            s.refund(1, 15)
            self.assertEqual(s.balance(1), 20)

    def test_topup_add(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            s = self._store(tmp, starter=0)
            self.assertEqual(s.add(1, 100), 100)
            self.assertEqual(s.add(1, 50), 150)

    def test_zero_charge_is_free_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            s = self._store(tmp, starter=10)
            self.assertTrue(s.charge(1, 0))
            self.assertEqual(s.balance(1), 10)


class CopyTests(unittest.TestCase):
    def test_every_button_key_has_label(self) -> None:
        for key in (
            "gen", "balance", "help", "myphoto", "cnt:1", "cnt:2", "cnt:4",
            "fmt:land", "fmt:port", "fmt:sq", "back", "cancel", "repeat_last",
            "dl_raw", "up2x", "edit", "revary", "regen", "topup",
        ):
            self.assertIn(key, flow_copy.LABELS, key)
            self.assertTrue(flow_copy.label(key))

    def test_messages_format_with_placeholders(self) -> None:
        self.assertIn("42", flow_copy.msg("balance_screen", credits=42, price=10))
        low = flow_copy.msg("low_balance", needed=20, have=5)
        self.assertIn("20", low)
        self.assertIn("5", low)
        cap = flow_copy.msg("result_caption", i=1, n=4)
        self.assertIn("1", cap)
        self.assertIn("4", cap)

    def test_labels_fit_telegram_button_width(self) -> None:
        # Keep labels short and scannable (generous cap incl. emoji).
        for key, value in flow_copy.LABELS.items():
            self.assertLessEqual(len(value), 30, f"{key}: {value!r}")

    def test_missing_key_falls_back_to_key(self) -> None:
        self.assertEqual(flow_copy.label("no_such_key"), "no_such_key")
        self.assertEqual(flow_copy.msg("no_such_key"), "no_such_key")


class BotMenuWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = (PROJECT_ROOT / "flow_bot.py").read_text(encoding="utf-8")

    def test_menu_and_wizard_handlers_present(self) -> None:
        for needle in (
            'F.data.startswith("m:")',     # menu router
            'F.data.startswith("w:")',     # wizard router
            "async def on_menu_action",
            "async def on_wizard_action",
            "def main_menu_kb",
            "def wizard_kb",               # single-screen count+format
            'data == "w:go"',              # one "generate" button
            'st["await"] = "prompt"',       # wizard waits for prompt
        ):
            self.assertIn(needle, self.source, needle)

    def test_single_screen_wizard_has_count_and_format_together(self) -> None:
        start = self.source.index("def wizard_kb")
        end = self.source.index("def reply_menu_kb")
        block = self.source[start:end]
        self.assertIn('callback_data="w:cnt:1"', block)
        self.assertIn('callback_data="w:fmt:land"', block)
        self.assertIn('"w:go"', block)

    def test_persistent_reply_keyboard_present(self) -> None:
        self.assertIn("def reply_menu_kb", self.source)
        self.assertIn("ReplyKeyboardMarkup", self.source)
        self.assertIn("is_persistent=True", self.source)
        # Reply-button taps are handled as plain text before prompt routing.
        self.assertIn('text == L("kb_gen")', self.source)
        self.assertIn('text == L("kb_balance")', self.source)

    def test_admin_grant_restricted(self) -> None:
        self.assertIn('@dp.message(Command("grant"))', self.source)
        self.assertIn("async def cmd_grant", self.source)
        self.assertIn("ADMIN_IDS", self.source)
        self.assertIn("message.from_user.id not in ADMIN_IDS", self.source)
        self.assertIn("credit_store.add(target, amount)", self.source)

    def test_credits_charged_with_refund_on_failure(self) -> None:
        self.assertIn("credit_gate", self.source)
        self.assertIn("class NotEnoughCredits", self.source)
        self.assertIn("credit_store.refund", self.source)
        self.assertIn("charge.ok =", self.source)

    def test_stars_payment_wired(self) -> None:
        self.assertIn("currency=\"XTR\"", self.source)
        self.assertIn("@dp.pre_checkout_query()", self.source)
        self.assertIn("F.successful_payment", self.source)
        self.assertIn("credit_store.add", self.source)

    def test_paid_upscale_and_free_download_distinct(self) -> None:
        # up2x = prompt enhance; realup = true service upscale; download = free file.
        self.assertIn("async def _enhance_and_send", self.source)
        self.assertIn('action="up2x"', self.source)
        self.assertIn("async def _real_upscale_and_send", self.source)
        self.assertIn("async def _send_original_file", self.source)

    def test_real_upscale_wired(self) -> None:
        # The real upscale learns the request from the browser and replays it.
        self.assertIn("def _maybe_capture_upscale", self.source)
        self.assertIn("run_captured_request", self.source)
        self.assertIn("build_request_capture", self.source)
        self.assertIn("apply_request_capture", self.source)
        self.assertIn('b("realup", "realup")', self.source)
        self.assertIn('elif action == "realup"', self.source)

    def test_frames_and_ingredients_have_format_and_count(self) -> None:
        # Frames/Ingredients screens reuse the shared format+count picker rows.
        self.assertIn("def _vid_fmt_count_rows", self.source)
        self.assertIn("def frames_kb(has_start: bool, has_end: bool, vfmt: str, vcount: int, vmodel", self.source)
        self.assertIn("def ingredients_kb(n: int, vfmt: str, vcount: int, vmodel", self.source)
        # Format/count callbacks re-render the active video screen by mode.
        self.assertIn("def _vid_rerender_settings", self.source)
        self.assertIn("await _vid_rerender_settings(msg, user_id=user_id)", self.source)

    def test_model_picker_in_frames_and_ingredients(self) -> None:
        # Veo Lite/Fast/Quality picker available in Frames + Ingredients.
        self.assertIn("def _vid_model_row", self.source)
        self.assertIn('VID_REF_VARIANTS = ("veo-lite", "veo-fast", "veo-quality")', self.source)
        self.assertIn('data.startswith("v:vmod:")', self.source)

    def test_ingredients_generation_enabled(self) -> None:
        # Ingredients now generates (reference-to-video), no longer fail-closed.
        self.assertIn("VIDEO_REFERENCE_ENDPOINT", self.source)
        self.assertIn("is_reference", self.source)
        # done button leads to prompt/generation, not vid_gen_blocked.
        done = self.source.index('if data == "v:ing:done":')
        block = self.source[done:done + 1500]
        self.assertNotIn("vid_gen_blocked", block)
        self.assertIn("_video_generate_and_send", block)

    def test_ingredients_minimum_is_one_photo(self) -> None:
        # Ingredients works from a single photo now (no 2-photo gate).
        ing = self.source.index("def ingredients_kb")
        block = self.source[ing:ing + 400]
        self.assertIn("if n >= 1:", block)
        self.assertIn('if len(photos) < 1:', self.source)

    def test_album_and_caption_support(self) -> None:
        # Grouped photos (album) → first=start, second=end; caption → prompt.
        self.assertIn("async def _handle_album_photos", self.source)
        self.assertIn("message.media_group_id", self.source)
        self.assertIn('st["vfrm_start"] = sources[0]', self.source)
        self.assertIn("vcaption_prompt", self.source)

    def test_frames_next_generates_from_saved_caption(self) -> None:
        start = self.source.index('if data == "v:frm:go":')
        end = self.source.index('if data == "v:frm:clear":')
        block = self.source[start:end]
        self.assertIn('caption = st.pop("vcaption_prompt", None)', block)
        self.assertIn("if caption:", block)
        self.assertIn("_video_generate_and_send(msg, caption, user_id=user_id)", block)
        self.assertIn('st["vawait"] = "vprompt"', block)
        self.assertIn("vid_frm_ask_prompt", block)
        self.assertNotIn("vid_frm_ask_prompt_with_caption", block)
        self.assertIn("vid_frm_ready_next", self.source)

    def test_video_result_edit_and_extend_wiring(self) -> None:
        self.assertIn("def _video_can_extend", self.source)
        self.assertIn('ref.model_id == "veo-lite" and not ref.prompt_edited', self.source)
        self.assertIn('callback_data=f"v:edit:{vtoken}"', self.source)
        self.assertIn('callback_data=f"v:extend:{vtoken}"', self.source)
        self.assertIn('if data.startswith("v:edit:")', self.source)
        self.assertIn('if data.startswith("v:extend:")', self.source)
        self.assertIn('st["vawait"] = "vedit_prompt"', self.source)
        self.assertIn('unit_price_override=action_price("video_prompt_edit")', self.source)
        self.assertIn("vid_extend_unavailable", self.source)

    def test_video_prompt_edit_clears_reference_mode_inputs(self) -> None:
        self.assertIn("def _vid_clear_reference_inputs", self.source)
        start = self.source.index("async def _video_prompt_edit_and_send")
        end = self.source.index("async def _video_repeat_last")
        block = self.source[start:end]
        self.assertIn("_vid_clear_reference_inputs(user_id)", block)
        self.assertIn('st["vmode"] = "text"', block)

    def test_no_backend_or_captcha_words_in_user_messages(self) -> None:
        # User-facing copy must not reveal the backend or mention captcha.
        import flow_copy
        for key, text in flow_copy.MESSAGES.items():
            low = text.lower()
            self.assertNotIn("flow", low, key)
            self.assertNotIn("google", low, key)
            self.assertNotIn("капч", low, key)
            self.assertNotIn("recaptcha", low, key)
        for key, text in flow_copy.LABELS.items():
            self.assertNotIn("flow", text.lower(), key)


class CaptureVideoToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (PROJECT_ROOT / "tools" / "capture_video.py").read_text(encoding="utf-8")

    def test_native_edit_and_extend_capture_modes_are_available(self) -> None:
        self.assertIn('"--edit"', self.source)
        self.assertIn('"--extend"', self.source)
        self.assertIn('Path("tools/video_edit_capture.json")', self.source)
        self.assertIn('Path("tools/video_extend_capture.json")', self.source)
        self.assertIn('"capture_kind"', self.source)

    def test_capture_matching_covers_unknown_native_video_actions(self) -> None:
        self.assertIn("batchAsyncEditVideo", self.source)
        self.assertIn("batchAsyncExtendVideo", self.source)
        self.assertIn("editInstruction", self.source)
        self.assertIn("sourceVideo", self.source)
        self.assertIn("body_l = body_str.lower()", self.source)


class BotImportSmokeTests(unittest.TestCase):
    """Import flow_bot with aiogram installed to catch handler/registration errors.

    Skipped automatically if aiogram (or another heavy dep) is unavailable.
    """

    def test_module_imports_and_registers_handlers(self) -> None:
        import os

        try:
            import aiogram  # noqa: F401
        except Exception:
            self.skipTest("aiogram not installed")

        os.environ.setdefault("TELEGRAM_TOKEN", "123:test")
        # Use a temp credits file so the smoke test never writes the real one.
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["USER_CREDITS_FILE"] = str(Path(tmp) / "credits.json")
            import importlib

            fb = importlib.import_module("flow_bot")
            importlib.reload(fb)

            self.assertGreaterEqual(len(fb.dp.message.handlers), 10)
            self.assertGreaterEqual(len(fb.dp.callback_query.handlers), 3)
            self.assertEqual(len(fb.dp.pre_checkout_query.handlers), 1)
            # Keyboards build without error.
            fb.main_menu_kb()
            fb.wizard_kb(2, "sq")          # single-screen count+format
            fb.reply_menu_kb()             # persistent bottom keyboard
            fb.topup_kb()
            fb._image_keyboard("abcd1234")
            # New user receives the starter grant.
            self.assertEqual(fb.credit_store.balance(987654321), 50)
            # Admin IDs parse from env.
            self.assertIsInstance(fb.ADMIN_IDS, set)


if __name__ == "__main__":
    unittest.main()
