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
        self.assertEqual(flow_core.action_price("edit"), 15)
        self.assertEqual(flow_core.action_price("myphoto"), 15)
        self.assertEqual(flow_core.action_price("up2x"), 5)      # quick enhance
        self.assertEqual(flow_core.action_price("realup"), 5)    # true HD upscale
        self.assertEqual(flow_core.action_price("video_prompt_edit"), 150)
        self.assertEqual(flow_core.action_price("dl_raw"), 0)    # free
        self.assertEqual(flow_core.action_price("unknown"), 0)

    def test_upscale_is_half_of_one_image(self) -> None:
        self.assertEqual(flow_core.UPSCALE_PRICE, flow_core.PRICE_PER_IMAGE // 2)

    def test_video_prices_are_bot_retail_prices(self) -> None:
        self.assertEqual(flow_core.video_price("omni-flash-4s"), 50)
        self.assertEqual(flow_core.video_price("omni-flash-6s"), 70)
        self.assertEqual(flow_core.video_price("omni-flash-8s"), 85)
        self.assertEqual(flow_core.video_price("omni-flash-10s"), 100)
        self.assertEqual(flow_core.video_price("veo-lite"), 60)
        self.assertEqual(flow_core.video_price("veo-fast"), 120)
        self.assertEqual(flow_core.video_price("veo-quality"), 450)

    def test_video_reference_mode_surcharges(self) -> None:
        self.assertEqual(flow_core.video_price("veo-lite", mode="ingredients"), 75)
        self.assertEqual(flow_core.video_price("veo-fast", mode="frames"), 145)

    def test_family_picker_min_prices(self) -> None:
        # Drives the "· от N⭐" hint on the video family buttons (no hardcoding).
        variants = ("veo-lite", "veo-fast", "veo-quality")
        omni = min(flow_core.video_price(m, 1, "text") for m, _ in flow_core.video_models_in_family("omni-flash"))
        veo = min(flow_core.video_price(m, 1, "text") for m, _ in flow_core.video_models_in_family("veo"))
        ing = min(flow_core.video_price(m, 1, "ingredients") for m in variants)
        frm = min(flow_core.video_price(m, 1, "frames") for m in variants)
        self.assertEqual((omni, veo, ing, frm), (50, 60, 75, 85))

    def test_referral_milestone_tiers(self) -> None:
        # Single highest applicable tier per first payment (no stacking).
        self.assertEqual(flow_core.referral_milestone_bonus(35), 20)   # trial
        self.assertEqual(flow_core.referral_milestone_bonus(75), 20)   # small
        self.assertEqual(flow_core.referral_milestone_bonus(200), 30)  # medium
        self.assertEqual(flow_core.referral_milestone_bonus(450), 50)  # large
        self.assertEqual(flow_core.referral_milestone_bonus(900), 50)  # xl
        self.assertEqual(flow_core.referral_milestone_bonus(0), 0)

    def test_referral_ongoing_is_floored_ten_percent(self) -> None:
        self.assertEqual(flow_core.referral_ongoing_bonus(290), 29)
        self.assertEqual(flow_core.referral_ongoing_bonus(1500), 150)
        self.assertEqual(flow_core.referral_ongoing_bonus(5), 0)  # floor < 1 → 0

    def test_extend_price_is_fixed(self) -> None:
        self.assertEqual(flow_core.video_extend_price("veo-lite", 1), 60)
        self.assertEqual(flow_core.video_extend_price("veo-lite", 2), 60)
        self.assertEqual(flow_core.video_extend_price("veo-lite", 3), 60)

    def test_extend_price_index_floor_is_one(self) -> None:
        self.assertEqual(flow_core.video_extend_price("veo-lite", 0), 60)

    def test_extend_index_default_on_videoref(self) -> None:
        ref = flow_core.VideoRef(user_id=1, project_id="p", media_id="m")
        self.assertEqual(ref.extend_index, 0)


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
        # Retail ladder only (the admin-only 1-star "test" pack is excluded).
        retail = [flow_core.STARS_PACKS[pid] for pid in flow_core.public_pack_ids()]
        rates = [(p["credits"] / p["stars"]) for p in retail]
        # Larger packs give more credits per star (monotonic non-decreasing).
        self.assertEqual(rates, sorted(rates))
        best = [pid for pid, p in flow_core.STARS_PACKS.items() if p.get("best")]
        self.assertEqual(len(best), 1)

    def test_test_pack_is_admin_only_and_one_star(self) -> None:
        self.assertEqual(flow_core.STARS_PACKS["test"]["stars"], 1)
        self.assertTrue(flow_core.STARS_PACKS["test"].get("test"))
        # Hidden from the public ladder, shown only when include_test=True.
        self.assertNotIn("test", flow_core.public_pack_ids())
        self.assertIn("test", flow_core.public_pack_ids(include_test=True))
        self.assertIn("🧪", flow_core.pack_label("test"))

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


class PaymentStoreTests(unittest.TestCase):
    def test_records_lookup_and_refund_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "payments.json"
            ps = flow_core.PaymentStore(path)
            ps.add(7, "chg_A", 35, 45, "trial")
            ps.add(7, "chg_B", 1, 10, "test")
            # last_for_user returns the most recent non-refunded payment.
            last = ps.last_for_user(7)
            self.assertEqual(last["charge_id"], "chg_B")
            self.assertEqual(ps.find_by_charge("chg_A")["stars"], 35)
            self.assertIsNone(ps.last_for_user(999))
            # Marking refunded hides it from last_for_user.
            ps.mark_refunded("chg_B")
            self.assertTrue(ps.find_by_charge("chg_B")["refunded"])
            self.assertEqual(ps.last_for_user(7)["charge_id"], "chg_A")

    def test_persists_across_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "payments.json"
            flow_core.PaymentStore(path).add(5, "chg_X", 75, 100, "small")
            reloaded = flow_core.PaymentStore(path)
            self.assertEqual(reloaded.find_by_charge("chg_X")["credits"], 100)

    def test_add_is_idempotent_on_charge_id(self) -> None:
        # Редоставка апдейта несёт тот же charge_id — второй записи нет, иначе
        # /refund мог бы вернуть звёзды по «двойнику» второй раз.
        with tempfile.TemporaryDirectory() as tmp:
            ps = flow_core.PaymentStore(Path(tmp) / "payments.json")
            first = ps.add(7, "chg_DUP", 75, 100, "small")
            again = ps.add(7, "chg_DUP", 75, 100, "small")
            self.assertIs(first, again)
            self.assertEqual(len(ps._records), 1)


class ChannelSeedTests(unittest.TestCase):
    def test_parses_and_lowercases_valid_slug(self) -> None:
        self.assertEqual(flow_core.parse_channel_seed("seed_MyChannel"), "mychannel")
        self.assertEqual(flow_core.parse_channel_seed("seed_my_channel-1"), "my_channel-1")

    def test_rejects_wrong_prefix_or_garbage(self) -> None:
        self.assertIsNone(flow_core.parse_channel_seed(""))
        self.assertIsNone(flow_core.parse_channel_seed("ref_123"))
        self.assertIsNone(flow_core.parse_channel_seed("seed_"))          # пустой слаг
        self.assertIsNone(flow_core.parse_channel_seed("seed_bad slug"))  # пробел
        self.assertIsNone(flow_core.parse_channel_seed("seed_" + "x" * 33))  # длинный

    def test_prefix_constant(self) -> None:
        self.assertEqual(flow_core.CHANNEL_PARAM_PREFIX, "seed_")


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

    # ── Phase 1 copy ───────────────────────────────────────────────────
    def test_back_labels_unified_to_nazad(self) -> None:
        self.assertEqual(flow_copy.label("vid_back:fam"), "← Назад")
        self.assertEqual(flow_copy.label("vid_back:model"), "← Назад")

    def test_ingredients_caption_copy_present(self) -> None:
        self.assertEqual(flow_copy.label("vid_ing_done_ready"), "✅ Готово — на генерацию")
        self.assertIn("vid_ing_ready_with_caption", flow_copy.MESSAGES)
        self.assertIn("{prompt}", flow_copy.MESSAGES["vid_ing_ready_with_caption"])

    def test_price_screens_bold_the_price(self) -> None:
        for key in (
            "wizard_screen", "balance_screen", "low_balance", "vid_settings_screen",
            "vid_ing_screen", "vid_frm_screen", "after_image_screen",
        ):
            self.assertIn("<b>", flow_copy.MESSAGES[key], key)

    def test_after_image_screen_copy(self) -> None:
        msg = flow_copy.msg("after_image_screen", credits=42)
        self.assertIn("42", msg)
        self.assertIn("<b>", flow_copy.MESSAGES["after_image_screen"])


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
        self.assertIn('"w:cnt:1"', block)
        # Format + model rows come from shared helpers (also reused by edit).
        self.assertIn('_fmt_rows(fmt, "w:fmt")', block)
        self.assertIn('_imodel_row(imodel, "w:imodel")', block)
        self.assertIn('"w:go"', block)

    def test_fmt_rows_cover_five_formats(self) -> None:
        start = self.source.index("def _fmt_rows")
        block = self.source[start:start + 700]
        for code in ("land", "f43", "sq", "f34", "port"):
            self.assertIn(f'"{code}"', block)
        self.assertIn('f"{prefix}:{code}"', block)

    def test_edit_settings_kb_offers_format_and_model(self) -> None:
        start = self.source.index("def edit_settings_kb")
        block = self.source[start:start + 500]
        self.assertIn('_fmt_rows(fmt, "es:fmt")', block)
        self.assertIn('_imodel_row(imodel, "es:imodel", base_price=action_price("edit"))', block)

    def test_edit_settings_prefix_does_not_collide_with_edit_button(self) -> None:
        # The image "Изменить" button uses the "edit:" callback prefix; the edit
        # settings picker must use "es:" so its handler never hijacks it.
        self.assertIn('F.data.startswith("es:")', self.source)
        self.assertFalse("edit:".startswith("es:"))  # the actual guarantee
        self.assertNotIn('startswith("e:")', self.source)  # no over-broad filter

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
        # The real upscale uses the verified flow/upsampleImage contract (sync POST
        # returning base64 encodedImage), NOT a prompt-based image-to-image enhance.
        self.assertIn("async def upsample_image", self.source)
        self.assertIn("result = await _client_for_acc(ref.account_id).upsample_image", self.source)
        self.assertIn("build_upsample_payload", self.source)
        self.assertIn("parse_upsample_response", self.source)
        self.assertIn('b("realup", "realup")', self.source)
        self.assertIn('elif action == "realup"', self.source)
        # realup must NOT silently fall back to the prompt enhance anymore.
        real_start = self.source.index("async def _real_upscale_and_send")
        real_block = self.source[real_start:real_start + 900]
        self.assertNotIn("_enhance_and_send", real_block)

    def test_image_keyboard_has_no_mix_button_and_star_prices(self) -> None:
        start = self.source.index("def _image_keyboard")
        block = self.source[start:start + 900]
        self.assertNotIn('b("mix"', block)          # «В микс» removed from results
        self.assertNotIn('b("up2x"', block)         # «Чёткость ×2» removed from results
        self.assertIn('b("realup", "realup")', block)  # HD-upscale button kept
        self.assertIn('· {price}⭐', block)          # price tags carry the star emoji

    def test_realup_label_is_improve_quality(self) -> None:
        import flow_copy
        self.assertEqual(flow_copy.label("realup"), "🔍 Улучшить качество")

    def test_selected_option_is_colored_green(self) -> None:
        # The chosen wizard option turns the BUTTON green natively via the Bot API
        # 9.4 `style="success"` field (not a text marker). (Live build asserted in
        # the smoke test, which sets up a temp credits file before importing.)
        self.assertIn('SELECT_STYLE = "success"', self.source)
        self.assertIn("def _sel_btn", self.source)
        helper_start = self.source.index("def _sel_btn")
        helper = self.source[helper_start:helper_start + 900]
        self.assertIn('kwargs["style"] = SELECT_STYLE', helper)
        # The old text-prefix selector must be gone everywhere.
        self.assertNotIn("text=_sel(", self.source)

    def test_wizard_swallows_not_modified_no_duplicate_panel(self) -> None:
        # Re-tapping an already-selected wizard option must NOT post a second
        # panel: the "message is not modified" edit error is swallowed.
        self.assertIn("async def _edit_or_answer", self.source)
        helper_start = self.source.index("async def _edit_or_answer")
        helper = self.source[helper_start:helper_start + 1100]
        self.assertIn('"not modified" in str(exc).lower()', helper)
        self.assertIn("await _edit_or_answer(message, text, kb", self.source)

    def test_star_price_tags_on_action_buttons(self) -> None:
        # Video result / model rows show the credit cost with a star emoji.
        self.assertIn('· {edit_price}⭐', self.source)
        self.assertIn('· {next_price}⭐', self.source)
        self.assertIn("{price}⭐", self.source)

    def test_stale_photo_edit_cleared_on_navigation(self) -> None:
        # Regression: photo upload sets pending_edits; navigating to generate/menu
        # must clear it so the next prompt is NOT applied as an edit of that photo.
        self.assertIn("def _reset_image_flow", self.source)
        reset_start = self.source.index("def _reset_image_flow")
        self.assertIn("pending_edits.pop(user_id, None)", self.source[reset_start:reset_start + 600])
        # The dangerous unconditional "old path" edit fallback is gone.
        self.assertNotIn("Старый путь (на случай pending_edits", self.source)
        self.assertIn("_reset_image_flow(user_id)", self.source)

    def test_frames_and_ingredients_have_format_and_count(self) -> None:
        # Frames/Ingredients screens reuse the shared format+count picker rows.
        self.assertIn("def _vid_fmt_count_rows", self.source)
        self.assertIn("def frames_kb(has_start: bool, has_end: bool, vfmt: str, vcount: int, vmodel", self.source)
        self.assertIn("def ingredients_kb(", self.source)
        # Format/count callbacks re-render the active video screen by mode.
        self.assertIn("def _vid_rerender_settings", self.source)
        self.assertIn("await _vid_rerender_settings(msg, user_id=user_id)", self.source)

    def test_model_picker_in_frames_and_ingredients(self) -> None:
        # Veo Lite/Fast/Quality picker available in Frames + Ingredients.
        self.assertIn("def _vid_model_row", self.source)
        self.assertIn('VID_REF_DEFAULT_MODEL = "veo-lite"', self.source)
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

    def test_video_settings_plain_text_runs_video_before_image_fallback(self) -> None:
        self.assertIn("def _video_plain_text_ready", self.source)
        start = self.source.index("async def handle_plain_text")
        video_branch = self.source.index("if _video_plain_text_ready(st):", start)
        image_fallback = self.source.index('st["pending_prompt"] = text', start)
        awaiting_image_prompt = self.source.index('awaiting = st.get("await")', start)
        self.assertLess(video_branch, awaiting_image_prompt)
        self.assertLess(video_branch, image_fallback)
        block = self.source[video_branch:video_branch + 250]
        self.assertIn("_video_generate_and_send(message, text, user_id=user_id)", block)
        self.assertIn("return", block)

    def test_video_plain_text_ready_is_narrow(self) -> None:
        start = self.source.index("def _video_plain_text_ready")
        end = self.source.index("def video_family_kb", start)
        block = self.source[start:end]
        self.assertIn('st.get("vawait")', block)
        self.assertIn('st.get("vstep") != "vsettings"', block)
        self.assertIn('st.get("vmode", "text") != "text"', block)
        self.assertIn("video_model_meta(model_id)", block)
        self.assertIn("vfmt not in _VID_FMT_TO_ASPECT", block)
        self.assertIn("clamp_num_videos(vcount) == vcount", block)

    def test_omni_video_result_warns_extend_unavailable(self) -> None:
        self.assertIn("vid_omni_no_extend_hint", flow_copy.MESSAGES)
        start = self.source.index('caption = flow_copy.msg("vid_result_caption"')
        block = self.source[start:start + 350]
        self.assertIn('meta.get("family") == "omni-flash"', block)
        self.assertIn("vid_omni_no_extend_hint", block)

    def test_video_result_edit_and_extend_wiring(self) -> None:
        self.assertIn("def _video_can_edit", self.source)
        self.assertIn("def _video_can_extend", self.source)
        self.assertIn("ref.workflow_id", self.source)
        self.assertIn('str(ref.model_id).startswith("veo-")', self.source)
        self.assertIn('VIDEO_EXTEND_MODEL = "veo-lite"', self.source)
        self.assertIn("not ref.prompt_edited", self.source)
        self.assertIn('callback_data=f"v:edit:{vtoken}"', self.source)
        self.assertIn('callback_data=f"v:extend:{vtoken}"', self.source)
        self.assertIn('if data.startswith("v:edit:")', self.source)
        self.assertIn('if data.startswith("v:extend:")', self.source)
        self.assertIn('st["vawait"] = "vedit_prompt"', self.source)
        self.assertIn('st["vawait"] = "vextend_prompt"', self.source)
        self.assertIn('unit_price_override=action_price("video_prompt_edit")', self.source)
        self.assertIn('video_operation="edit"', self.source)
        self.assertIn('video_operation="extend"', self.source)
        self.assertIn("prepare_video_extend_scene", self.source)

    # ── Phase 1 upgrades ───────────────────────────────────────────────
    def test_ingredients_and_frames_have_back_to_family(self) -> None:
        ing = self.source[self.source.index("def ingredients_kb"):][:900]
        frm = self.source[self.source.index("def frames_kb"):][:900]
        self.assertIn('"v:back:fam"', ing)
        self.assertIn('"v:back:fam"', frm)

    def test_family_picker_shows_min_prices(self) -> None:
        self.assertIn("def _vid_family_min_price", self.source)
        block = self.source[self.source.index("def video_family_kb"):][:600]
        self.assertIn("от ", block)
        self.assertIn("⭐", block)
        self.assertIn("_vid_family_min_price", block)

    def test_ingredients_done_label_is_dynamic(self) -> None:
        block = self.source[self.source.index("def ingredients_kb"):][:600]
        self.assertIn("has_caption", block)
        self.assertIn("vid_ing_done_ready", block)
        # screen shows the pending caption like frames mode does
        self.assertIn("vid_ing_ready_with_caption", self.source)

    def test_video_retry_rehydrates_from_snapshot(self) -> None:
        # The retry button must re-run the SAME request, not report "expired".
        self.assertIn('st["vretry"]', self.source)
        self.assertIn('snap = st.get("vretry")', self.source)
        clear = self.source[self.source.index("def _vid_clear"):][:400]
        self.assertIn('"vretry"', clear)  # snapshot survives the finally-clear

    def test_after_result_offers_video_balance_and_menu(self) -> None:
        block = self.source[self.source.index("async def _after_result"):][:700]
        for cb in ('"m:repeat"', '"m:gen"', '"m:vid"', '"m:balance"', '"m:menu"'):
            self.assertIn(cb, block)
        self.assertIn("after_image_screen", block)
        self.assertIn("credit_store.balance", block)

    def test_price_screens_use_html_and_escape_user_text(self) -> None:
        self.assertIn("import html", self.source)
        self.assertIn('parse_mode="HTML"', self.source)
        # every echoed user prompt on an HTML screen is escaped
        self.assertIn("html.escape(pending", self.source)
        self.assertIn("html.escape(caption", self.source)

    def test_ingredients_diagnostic_logging_present(self) -> None:
        # Temporary capture-driven logging to diagnose the фото+текст gen failure.
        self.assertIn("🎬 r2v req", self.source)

    def test_metrics_wired_into_flow(self) -> None:
        # Metrics import + init + key events + idempotent transaction recording.
        self.assertIn("import metrics", self.source)
        self.assertIn("metrics.init_db(", self.source)
        for ev in (
            '"user_started"', '"image_requested"', '"image_success"', '"image_failed"',
            '"variations_requested"', '"upscale_requested"', '"image_edit_requested"',
            '"video_requested"', '"video_success"', '"video_failed"',
            '"credits_charged"', '"credits_refunded"', '"topup_opened"',
            '"payment_success"', '"wizard_started"', '"wizard_completed"',
        ):
            self.assertIn(ev, self.source, ev)
        # Идемпотентность ДО зачисления: дубль доставки successful_payment не
        # зачисляет кредиты второй раз; сбой метрик-БД оплату не блокирует.
        self.assertIn("metrics.record_transaction_status(", self.source)
        start = self.source.index("async def on_successful_payment")
        handler = self.source[start:start + 2600]
        self.assertLess(
            handler.index("metrics.record_transaction_status("),
            handler.index("credit_store.add(user_id"),
        )
        self.assertIn('if tx_status == "duplicate":', handler)
        self.assertIn('if tx_status == "error":', handler)
        # Fallback-ключ дедупа различает редоставку и новую покупку (message_id).
        self.assertIn("message.message_id", handler)
        self.assertIn("metrics.log_flow_job(", self.source)

    def test_admin_metrics_commands_registered(self) -> None:
        for cmd in (
            "admin_today", "admin_revenue", "admin_flow",
            "admin_accounts", "admin_refs", "admin_channels", "admin_errors",
        ):
            self.assertIn(f'Command("{cmd}")', self.source, cmd)
        # All admin-gated (read-only for users).
        self.assertIn("def _admin_only", self.source)
        self.assertIn("metrics.report_today()", self.source)
        self.assertIn("metrics.report_accounts()", self.source)

    def test_channel_attribution_wired(self) -> None:
        # /start seed_<канал> → first-touch атрибуция в metrics.acquisitions.
        self.assertIn("CHANNEL_PARAM_PREFIX", self.source)
        self.assertIn("parse_channel_seed(payload)", self.source)
        self.assertIn("metrics.record_acquisition(", self.source)
        self.assertIn('"acquired_from_channel"', self.source)
        # Атрибуция стоит внутри cmd_start (рядом с рефералкой), не где попало.
        start = self.source.index("async def cmd_start")
        block = self.source[start:start + 1600]
        self.assertIn("channel = parse_channel_seed(payload)", block)
        self.assertIn("metrics.record_acquisition(user_id=user_id, channel=channel)", block)
        # Админ-отчёт по каналам читает report_channels и умеет выдавать ссылку.
        self.assertIn("metrics.report_channels()", self.source)
        self.assertIn("?start={CHANNEL_PARAM_PREFIX}{slug}", self.source)

    def test_admin_help_is_owner_gated(self) -> None:
        # /admin_help — справочник команд, доступен ТОЛЬКО владельцам (OWNER_ID).
        self.assertIn('Command("admin_help")', self.source)
        self.assertIn("def _owner_only", self.source)
        self.assertIn("message.from_user.id in OWNER_IDS", self.source)
        start = self.source.index("async def cmd_admin_help")
        block = self.source[start:start + 400]
        self.assertIn("if not _owner_only(message):", block)
        self.assertNotIn("_admin_only(message)", block)  # не путать админ/владелец
        # Справочник перечисляет и пользовательские, и админские команды.
        self.assertIn("_HELP_SECTIONS", self.source)
        for cmd in ("/grant", "/refund", "/admin_channels", "/img", "/admin_help"):
            self.assertIn(cmd, self.source, cmd)

    def test_referral_wired(self) -> None:
        # Deep-link join, payment reward, menu entry, invite buttons, clawback.
        self.assertIn("REFERRAL_PARAM_PREFIX", self.source)
        self.assertIn("metrics.record_referral_join(", self.source)
        self.assertIn('"referral_joined"', self.source)
        self.assertIn("_maybe_apply_referral_rewards(", self.source)
        self.assertIn('"referral_reward_paid"', self.source)
        self.assertIn('data == "m:invite"', self.source)
        self.assertIn("_invite_button(", self.source)
        self.assertIn("_clawback_referral_rewards(", self.source)
        # Reward must be applied only after a recorded (idempotent) payment.
        self.assertIn("referral_milestone_bonus(", self.source)
        self.assertIn("referral_ongoing_bonus(", self.source)
        # Milestone выдаётся через атомарный клейм joined→rewarded (без TOCTOU):
        # начисление кредитов реферу — только при выигранном UPDATE.
        self.assertIn("metrics.grant_milestone_if_joined(", self.source)
        start = self.source.index("def _maybe_apply_referral_rewards")
        block = self.source[start:start + 2200]
        self.assertLess(
            block.index("metrics.grant_milestone_if_joined("),
            block.index("credit_store.add(referrer_id, bonus)"),
        )

    def test_video_generation_holds_user_slot(self) -> None:
        # Видео — через тот же per-user замок, что и картинки: иначе гонка
        # «проверь баланс — потом спиши» между параллельными видео и картинкой.
        start = self.source.index("async def _video_generate_and_send")
        end = self.source.index("async def _do_video_generate_and_send", start)
        wrapper = self.source[start:end]
        self.assertIn("async with user_slot(user_id, message):", wrapper)
        self.assertIn("await _do_video_generate_and_send(", wrapper)
        self.assertIn("except RateLimited:", wrapper)

    def test_pre_checkout_validates_payload(self) -> None:
        # Последний рубеж перед списанием звёзд: чужой/битый payload не одобряем.
        start = self.source.index("async def on_pre_checkout")
        block = self.source[start:start + 700]
        self.assertIn("invoice_payload", block)
        self.assertIn('parts[0] == "credits"', block)
        self.assertIn("credit_pack(parts[1]) is not None", block)
        self.assertIn("ok=ok", block)
        self.assertNotIn("answer(ok=True)", block)

    def test_animate_image_to_video_wired(self) -> None:
        # "Оживить фото" button under images + main-menu entry → r2v pipeline.
        kb = self.source[self.source.index("def _image_keyboard"):][:900]
        self.assertIn('f"an:img:{token}"', kb)
        self.assertIn('@dp.callback_query(F.data.startswith("an:"))', self.source)
        self.assertIn('data == "m:animate"', self.source)
        # Seeds the generated image as the single r2v reference, then reuses the
        # existing ingredients flow.
        self.assertIn('st["ving_photos"] = [ref.source]', self.source)
        self.assertIn('st["vmode"] = "ingredients"', self.source)
        # The animate prefix handler is registered before the catch-all image one.
        self.assertLess(
            self.source.index('startswith("an:")'),
            self.source.index("async def on_image_action"),
        )

    def test_ingredients_chat_prompt_starts_video(self) -> None:
        # «Оживить фото»: фото уже выбрано → текст из чата запускает ВИДЕО, а не
        # картинки. Ветки (ingredients/frames) стоят ДО image-фолбэка.
        start = self.source.index("async def handle_plain_text")
        ing_branch = self.source.index(
            'if st.get("vmode") == "ingredients" and (st.get("ving_photos") or []):', start
        )
        frm_branch = self.source.index(
            'if st.get("vmode") == "frames" and st.get("vfrm_start") and st.get("vfrm_end"):',
            start,
        )
        image_fallback = self.source.index('st["pending_prompt"] = text', start)
        awaiting_image_prompt = self.source.index('awaiting = st.get("await")', start)
        wizard_image = self.source.index('if st.get("step") == "wizard":', start)
        for branch in (ing_branch, frm_branch):
            self.assertLess(branch, awaiting_image_prompt)
            self.assertLess(branch, wizard_image)
            self.assertLess(branch, image_fallback)
        block = self.source[ing_branch:frm_branch + 260]
        self.assertIn("_video_generate_and_send(message, text, user_id=user_id)", block)
        # Вход в «Оживить фото» чистит залипший image-визард (await/step), иначе он
        # перехватил бы промпт. Помощник зовётся из m:animate и an:img.
        self.assertIn("def _clear_image_flow_keys", self.source)
        self.assertEqual(self.source.count("_clear_image_flow_keys(st)"), 2)

    def test_ideas_hub_wired(self) -> None:
        # Menu entry + hub root + both branches (templates Q&A, guided picker).
        self.assertIn("import prompts_lib", self.source)
        self.assertIn('data == "m:ideas"', self.source)
        self.assertIn('@dp.callback_query(F.data.startswith("ih:"))', self.source)
        self.assertIn('@dp.callback_query(F.data.startswith("tp:"))', self.source)
        self.assertIn('@dp.callback_query(F.data.startswith("gp:"))', self.source)
        self.assertIn("prompts_lib.compose_template_prompt(", self.source)
        self.assertIn("prompts_lib.compose_guided_prompt(", self.source)
        # Composed prompt feeds the existing wizard via pending_prompt.
        self.assertIn('st["pending_prompt"] = prompt', self.source)
        self.assertIn('"template_opened"', self.source)
        self.assertIn('"template_used"', self.source)
        # Free-text Q&A answers are captured in the text handler.
        self.assertIn('st.get("tp_await") == "text"', self.source)
        # New prefixes register before the catch-all image handler.
        for pfx in ('startswith("ih:")', 'startswith("tp:")', 'startswith("gp:")'):
            self.assertLess(self.source.index(pfx),
                            self.source.index("async def on_image_action"), pfx)

    def test_prompts_lib_templates_complete(self) -> None:
        import prompts_lib
        self.assertEqual(len(prompts_lib.template_ids()), 9)
        # Composing never leaks placeholders or header-comment lines.
        p = prompts_lib.compose_template_prompt(
            "product_card",
            {"product": "кружка", "background": "white", "need_text": "no"},
        )
        self.assertNotIn("{", p)
        self.assertNotIn("#", p)
        self.assertEqual(prompts_lib.compose_template_prompt("nope", {}), "")

    def test_video_upload_edit_wired(self) -> None:
        # Загрузка/правка СВОЕГО видео временно отключена флагом: сервис отдаёт
        # «Oops…» / недогруз. Реализация сохранена целиком, но спрятана за
        # UPLOAD_VIDEO_EDIT_ENABLED (вернуть фичу = поставить True).
        self.assertIn("UPLOAD_VIDEO_EDIT_ENABLED = False", self.source)
        self.assertIn("if UPLOAD_VIDEO_EDIT_ENABLED else []", self.source)   # кнопка в семействе
        self.assertIn("if not UPLOAD_VIDEO_EDIT_ENABLED:", self.source)      # колбэк vu:start
        self.assertIn("vid_upload_disabled", flow_copy.MESSAGES)
        # handle_video_upload игнорирует видео, пока фича выключена.
        self.assertIn(
            "if not UPLOAD_VIDEO_EDIT_ENABLED or st.get(\"vawait\") != \"vu_video\":",
            self.source,
        )
        # Реализация (на случай возврата фичи) на месте: колбэк + хендлер + правка.
        self.assertIn('"vu:start"', self.source)
        self.assertIn('@dp.callback_query(F.data.startswith("vu:"))', self.source)
        self.assertIn("@dp.message(F.video | F.document)", self.source)
        self.assertIn("_account_for_video(user_id)", self.source)
        self.assertIn("_keeper_for_acc(acc_id).upload_video(", self.source)
        self.assertIn("async def _video_edit_uploaded", self.source)
        # The upload proxy contract (from the Flow web-app bundle): the PUT must
        # carry the resumable session URL + chunk headers, else it 400s.
        self.assertIn("'X-Upload-Session-Url': sessionUrl", self.source)
        self.assertIn("'X-Upload-Offset': String(offset)", self.source)
        self.assertIn("'upload, finalize'", self.source)
        self.assertIn("'X-Upload-Content-Length': String(bytes.length)", self.source)
        # No image-file-input fallback for video: that input only accepts images
        # and pops "Unsupported image format" in the service UI.
        upload_video_block = self.source[
            self.source.index("async def upload_video"):
            self.source.index("async def _page_media_ids")
        ]
        self.assertNotIn("upload_image(", upload_video_block)
        # Uploaded-video edit is marked prompt_edited=True → extend stays blocked.
        block = self.source[
            self.source.index("async def _video_edit_uploaded"):
            self.source.index("async def on_edit_settings")
        ]
        self.assertIn("prompt_edited=True", block)
        self.assertIn('video_operation="edit"', block)
        # Orientation comes from the uploaded clip's real dimensions.
        self.assertIn("_VID_FMT_TO_ASPECT[fmt]", block)
        # The upload handler waits for server-side transcode before allowing the
        # edit (otherwise the edit job FAILs), and a caption sent together with
        # the video starts the edit immediately instead of re-asking.
        upload_handler = self.source[
            self.source.index("async def handle_video_upload"):
            self.source.index("async def _video_edit_uploaded")
        ]
        self.assertIn("_client_for_acc(acc_id).wait_video_ready(", upload_handler)
        self.assertIn("message.caption", upload_handler)
        self.assertIn("_video_edit_uploaded(message, caption, user_id=user_id)", upload_handler)
        self.assertIn("async def wait_video_ready", self.source)

    def test_video_prompt_edit_clears_reference_mode_inputs(self) -> None:
        self.assertIn("def _vid_clear_reference_inputs", self.source)
        start = self.source.index("async def _video_prompt_edit_and_send")
        end = self.source.index("async def _video_repeat_last")
        block = self.source[start:end]
        self.assertIn("_vid_clear_reference_inputs(user_id)", block)
        self.assertIn('st["vmode"] = "edit"', block)
        self.assertIn('source_video=ref', block)
        self.assertNotIn("_video_prompt_edit_prompt(ref, instruction)", block)

    def test_video_extend_prepares_scene_before_generation(self) -> None:
        start = self.source.index("async def _video_extend_and_send")
        end = self.source.index("async def _video_repeat_last")
        block = self.source[start:end]
        self.assertIn("_client_for_acc(ref.account_id).prepare_video_extend_scene", block)
        self.assertIn("if not scene_id:", block)
        self.assertNotIn("ref.scene_id =", block)
        self.assertIn('st["vmode"] = "extend"', block)
        self.assertIn('video_operation="extend"', block)
        self.assertIn("source_scene_id=scene_id", block)

    def test_video_extend_delivery_uses_service_concat(self) -> None:
        # No local ffmpeg merge anywhere.
        self.assertNotIn("async def _concat_video_bytes", self.source)
        self.assertNotIn("asyncio.create_subprocess_exec", self.source)
        self.assertNotIn("segment_media_id", self.source)
        # Default extend delivery = the service's server-side stitched full video.
        self.assertIn("async def _video_delivery_bytes", self.source)
        self.assertIn("async def fetch_full_extended_video", self.source)
        self.assertIn("full_bytes = await _client_for_acc(ref.account_id).fetch_full_extended_video", self.source)
        self.assertIn('if ref.mode == "extend" and ref.scene_id and ref.project_id', self.source)
        # The new fragment button downloads the extend result media_id itself.
        self.assertIn("async def _video_segment_download", self.source)
        self.assertIn("v:dl_seg:", self.source)

    def test_video_extend_result_keeps_source_media_id(self) -> None:
        start = self.source.index("vref = VideoRef(")
        block = self.source[start:start + 550]
        self.assertIn('source_media_id=source_video.media_id if video_operation == "extend"', block)
        self.assertIn("delivery_bytes, merged_video = await _video_delivery_bytes", self.source)
        self.assertIn("BufferedInputFile(delivery_bytes, filename)", self.source)

    def test_video_download_uses_delivery_helper(self) -> None:
        start = self.source.index("async def _video_download")
        end = self.source.index("async def _video_edit_start", start)
        block = self.source[start:end]
        self.assertIn("video_bytes, merged_video = await _video_delivery_bytes(ref)", block)
        self.assertIn("'_full' if merged_video else ''", block)

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

    def test_upload_edit_capture_mode_available(self) -> None:
        # Editing a USER-uploaded video is the bot's failing flow; the tool needs a
        # dedicated mode that also records the upload (which may hit a different host).
        self.assertIn('"--upload-edit"', self.source)
        self.assertIn('Path("tools/video_upload_edit_capture.json")', self.source)
        self.assertIn("upload-edit", self.source)
        # Upload of a user video can go to a non-API host (resumable/signed URL):
        # the route handler must record upload-like traffic on any host.
        self.assertIn("upload_like", self.source)
        self.assertIn('"upload" in url.lower()', self.source)


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
            fb.wizard_kb(4, "f43", "nbpro")  # new format + model picker
            fb.edit_settings_kb("f34", "nbpro")  # edit-flow format/model picker
            model_buttons = [
                b.text
                for row in fb.wizard_kb(1, "sq", "nb2").inline_keyboard
                for b in row
                if (b.callback_data or "").startswith("w:imodel:")
            ]
            self.assertIn("Nano Banana 2 · 10 кр", model_buttons)
            self.assertIn("Nano Banana Pro · 15 кр", model_buttons)
            fb.reply_menu_kb()             # persistent bottom keyboard
            fb.topup_kb()                  # public packs (no test pack)
            fb.topup_kb(is_admin=True)     # includes the 1-star test pack
            fb._image_keyboard("abcd1234")
            fb.video_family_kb()           # family buttons now carry "· от N⭐"
            fb.ingredients_kb(1, "land", 1, "veo-fast", has_caption=True)
            # Family buttons show a min-price hint.
            fam_first = fb.video_family_kb().inline_keyboard[0][0].text
            self.assertIn("⭐", fam_first)
            # "Изменить своё видео" (upload→edit) временно отключено флагом:
            # пока UPLOAD_VIDEO_EDIT_ENABLED=False, кнопки vu:start в семействе нет.
            self.assertFalse(fb.UPLOAD_VIDEO_EDIT_ENABLED)
            fam_rows = fb.video_family_kb().inline_keyboard
            self.assertFalse(
                any((b.callback_data or "") == "vu:start" for row in fam_rows for b in row)
            )
            # Возврат флага возвращает кнопку (с ценой) — проводка сохранена, лишь скрыта.
            fb.UPLOAD_VIDEO_EDIT_ENABLED = True
            try:
                rows_on = fb.video_family_kb().inline_keyboard
                upload_btn = next(
                    b for row in rows_on for b in row if (b.callback_data or "") == "vu:start"
                )
                self.assertIn("⭐", upload_btn.text)
            finally:
                fb.UPLOAD_VIDEO_EDIT_ENABLED = False
            # "Оживить фото" under a generated image (an:img:) now carries a price too.
            img_rows = fb._image_keyboard("abcd1234").inline_keyboard
            animate_btn = next(
                b for row in img_rows for b in row
                if (b.callback_data or "").startswith("an:img:")
            )
            self.assertIn("⭐", animate_btn.text)
            # Extend gating: ANY veo-family source (lite/fast/quality) is
            # extendable — the extension itself runs on veo-lite. Omni is not.
            VR = fb.VideoRef
            lite = VR(user_id=1, project_id="p", media_id="m", workflow_id="w",
                      model_id="veo-lite", mode="ingredients")
            fast = VR(user_id=1, project_id="p", media_id="m", workflow_id="w",
                      model_id="veo-fast", mode="ingredients")
            omni = VR(user_id=1, project_id="p", media_id="m", workflow_id="w",
                      model_id="omni-flash-4s", mode="text")
            self.assertTrue(fb._video_can_extend(lite))
            self.assertTrue(fb._video_can_extend(fast))
            self.assertFalse(fb._video_can_extend(omni))
            # Bot API 9.4 green-button: selected option carries style=success in
            # the outgoing JSON; the unselected one omits it (graceful on old apps).
            chosen = fb._sel_btn("X", True, "w:cnt:1").model_dump(exclude_none=True)
            plain = fb._sel_btn("Y", False, "w:cnt:2").model_dump(exclude_none=True)
            self.assertEqual(chosen.get("style"), "success")
            self.assertNotIn("style", plain)
            # New user receives the starter grant.
            self.assertEqual(fb.credit_store.balance(987654321), 30)
            # Admin IDs parse from env.
            self.assertIsInstance(fb.ADMIN_IDS, set)
            # Multiple OWNER_ID values parse, and owners are admins.
            self.assertEqual(fb._parse_ids("111, 222; 333 ,bad"), {111, 222, 333})
            self.assertEqual(fb._parse_ids(""), set())


if __name__ == "__main__":
    unittest.main()
