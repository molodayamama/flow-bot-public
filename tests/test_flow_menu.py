from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import flow_core
import flow_copy
import config_store


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
        self.assertEqual(flow_core.action_price("mp_series", 3), 30)
        self.assertEqual(flow_core.action_price("mp_series", 5), 45)
        self.assertEqual(flow_core.action_price("mp_series", 8), 70)
        self.assertEqual(flow_core.action_price("mp_series", 4), 0)
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
        self.assertEqual(flow_core.video_price("omni-flash-4s", mode="ingredients"), 50)
        self.assertEqual(flow_core.video_price("veo-lite", mode="ingredients"), 60)
        self.assertEqual(flow_core.video_price("veo-fast", mode="frames"), 145)

    def test_family_picker_min_prices(self) -> None:
        # Drives the "· от N кр" hint on the video family buttons (no hardcoding).
        variants = tuple(flow_core.VIDEO_MODELS.keys())
        omni = min(flow_core.video_price(m, 1, "text") for m, _ in flow_core.video_models_in_family("omni-flash"))
        veo = min(flow_core.video_price(m, 1, "text") for m, _ in flow_core.video_models_in_family("veo"))
        ing = min(flow_core.video_price(m, 1, "ingredients") for m in variants)
        frm = min(flow_core.video_price(m, 1, "frames") for m in ("veo-lite", "veo-fast", "veo-quality"))
        self.assertEqual((omni, veo, ing, frm), (50, 60, 50, 85))
        self.assertEqual(flow_core.video_animate_min_price(), 50)

    def test_trial_plus_starter_can_animate_one_photo(self) -> None:
        trial = flow_core.STARS_PACKS["trial"]["credits"]
        starter = flow_core.STARTER_CREDITS
        animate_price = flow_core.video_animate_min_price()
        self.assertEqual(animate_price, 50)
        self.assertGreaterEqual(starter + trial, animate_price)

    def test_referral_milestone_tiers(self) -> None:
        # Single highest applicable tier per first payment (no stacking).
        self.assertEqual(flow_core.referral_milestone_bonus(35), 20)   # trial
        self.assertEqual(flow_core.referral_milestone_bonus(75), 20)   # small
        self.assertEqual(flow_core.referral_milestone_bonus(200), 30)  # medium
        self.assertEqual(flow_core.referral_milestone_bonus(450), 50)  # large
        self.assertEqual(flow_core.referral_milestone_bonus(900), 50)  # xl
        self.assertEqual(flow_core.referral_milestone_bonus(0), 0)

    def test_referral_ongoing_is_floored_ten_percent(self) -> None:
        self.assertEqual(flow_core.REFERRAL_ONGOING_PCT, 0.10)
        self.assertEqual(flow_core.referral_ongoing_bonus(300), 30)
        self.assertEqual(flow_core.referral_ongoing_bonus(1500), 150)
        self.assertEqual(flow_core.referral_ongoing_bonus(9), 0)  # floor < 1 → 0

    def test_referral_reward_window_is_three_months(self) -> None:
        self.assertEqual(flow_core.REFERRAL_REWARD_WINDOW_DAYS, 90)


class RuntimeOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_path = config_store._PATH
        self._old_cache = config_store._cache
        self._old_mtime = config_store._mtime
        config_store._PATH = str(Path(self._tmp.name) / "config_override.json")
        config_store._cache = None
        config_store._mtime = -1.0

    def tearDown(self) -> None:
        config_store._PATH = self._old_path
        config_store._cache = self._old_cache
        config_store._mtime = self._old_mtime
        self._tmp.cleanup()

    def test_price_overrides_drive_runtime_pricing_helpers(self) -> None:
        config_store.set_section("prices", {
            "image_nano": 12,
            "image_pro": 19,
            "edit_photo": 21,
            "upscale": 7,
            "veo_lite": 80,
            "ingredients_extra": 20,
            "frames_extra": 30,
            "extend_video": 90,
            "edit_video": 160,
            "seller_series_5": 44,
        })

        self.assertEqual(flow_core.price_gen(2), 24)
        self.assertEqual(flow_core.image_model_extra("nbpro"), 7)
        self.assertEqual(flow_core.action_price("edit"), 21)
        self.assertEqual(flow_core.action_price("realup"), 7)
        self.assertEqual(flow_core.action_price("mp_series", 5), 44)
        self.assertEqual(flow_core.action_price("video_prompt_edit"), 160)
        self.assertEqual(flow_core.video_price("veo-lite", mode="text"), 80)
        self.assertEqual(flow_core.video_price("veo-lite", mode="ingredients"), 100)
        self.assertEqual(flow_core.video_price("veo-lite", mode="frames"), 110)
        self.assertEqual(flow_core.video_extend_price("veo-lite", 1), 90)

    def test_copy_overrides_are_read_at_runtime(self) -> None:
        config_store.set_section("labels", {"gen": "Generate"})
        config_store.set_section("messages", {"low_balance": "Need {needed}, have {have}"})

        self.assertEqual(flow_copy.label("gen"), "Generate")
        self.assertEqual(
            flow_copy.msg("low_balance", needed=10, have=3),
            "Need 10, have 3",
        )

    def test_extend_price_is_fixed(self) -> None:
        self.assertEqual(flow_core.video_extend_price("veo-lite", 1), 60)
        self.assertEqual(flow_core.video_extend_price("veo-lite", 2), 60)
        self.assertEqual(flow_core.video_extend_price("veo-lite", 3), 60)

    def test_extend_price_index_floor_is_one(self) -> None:
        self.assertEqual(flow_core.video_extend_price("veo-lite", 0), 60)

    def test_extend_index_default_on_videoref(self) -> None:
        ref = flow_core.VideoRef(user_id=1, project_id="p", media_id="m")
        self.assertEqual(ref.extend_index, 0)

    def test_robokassa_sha256_signatures(self) -> None:
        shp = {"Shp_user": 7, "Shp_pack": "small"}
        pay_base = "photozhab:97.50:123:pass1" + ":Shp_pack=small:Shp_user=7"
        result_base = "97.50:123:pass2" + ":Shp_pack=small:Shp_user=7"
        self.assertEqual(
            flow_core.robokassa_payment_signature(
                "photozhab", "97.50", 123, "pass1",
                shp_params=shp, algorithm="sha256",
            ),
            hashlib.sha256(pay_base.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            flow_core.robokassa_result_signature(
                "97.50", 123, "pass2", shp_params=shp, algorithm="sha256",
            ),
            hashlib.sha256(result_base.encode("utf-8")).hexdigest(),
        )

    def test_robokassa_amount_uses_fixed_public_grid(self) -> None:
        self.assertEqual(flow_core.robokassa_pack_amount("trial", 1.3), "45.00")
        self.assertEqual(flow_core.robokassa_pack_amount("small", 1.3), "90.00")
        self.assertEqual(flow_core.robokassa_pack_amount("medium", 1.3), "235.00")
        self.assertEqual(flow_core.robokassa_pack_amount("large", 1.3), "530.00")
        self.assertEqual(flow_core.robokassa_pack_amount("xl", 1.3), "1050.00")
        self.assertEqual(flow_core.robokassa_pack_amount("small", 1.3, discount_pct=10), "90.00")
        self.assertEqual(flow_core.robokassa_pack_amount("test", 1.3, discount_pct=10), "1.17")


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
        # Exactly one "best" highlight per ladder (consumer and seller shown apart).
        consumer_best = [p for p in retail if p.get("best")]
        self.assertEqual(len(consumer_best), 1)
        seller = [flow_core.STARS_PACKS[pid] for pid in flow_core.public_pack_ids(seller=True)]
        seller_rates = [(p["credits"] / p["stars"]) for p in seller]
        self.assertEqual(seller_rates, sorted(seller_rates))
        self.assertEqual(len([p for p in seller if p.get("best")]), 1)

    def test_test_pack_is_admin_only_and_one_star(self) -> None:
        self.assertEqual(flow_core.STARS_PACKS["test"]["stars"], 1)
        self.assertTrue(flow_core.STARS_PACKS["test"].get("test"))
        # Hidden from the public ladder, shown only when include_test=True.
        self.assertNotIn("test", flow_core.public_pack_ids())
        self.assertIn("test", flow_core.public_pack_ids(include_test=True))
        self.assertIn("🧪", flow_core.pack_label("test"))

    def test_pack_label_shows_credits_and_generations(self) -> None:
        label = flow_core.pack_label("large")
        self.assertIn("700", label)            # credits
        self.assertIn("70", label)             # generations = 700 / 10
        self.assertIn("ген", label)
        self.assertIn("Выгодно", label)        # best-value marker
        self.assertNotIn("⭐", label)


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
            "pay_stars", "pay_robo",
        ):
            self.assertIn(key, flow_copy.LABELS, key)
            self.assertTrue(flow_copy.label(key))

    def test_messages_format_with_placeholders(self) -> None:
        self.assertIn("42", flow_copy.msg("balance_screen", credits=42, price=10, vprice=50))
        low = flow_copy.msg("low_balance", needed=20, have=5)
        self.assertIn("20", low)
        self.assertIn("5", low)
        cap = flow_copy.msg("result_caption", i=1, n=4)
        self.assertIn("1", cap)
        self.assertIn("4", cap)

    def test_updated_balance_and_video_audio_copy(self) -> None:
        self.assertNotIn("Скачивание оригинала", flow_copy.MESSAGES["balance_screen"])
        self.assertEqual(
            flow_copy.msg("video_audio_filtered"),
            "🔇 Модель не смогла сгенерировать звук для этого видео, поэтому оно не сохранилось. "
            "Кредиты возвращены. Нажмите «Попробовать снова», с 1-2 попыток помогает.",
        )
        self.assertIn("photo_route_choice", flow_copy.MESSAGES)

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

    def test_main_menu_has_animate_under_video_with_source_price(self) -> None:
        start = self.source.index("def main_menu_kb")
        end = self.source.index("# ── Маркетплейс-меню", start)
        block = self.source[start:end]
        video_pos = block.index('"m:vid"')
        animate_pos = block.index('callback_data="m:animate"')
        ideas_pos = block.index('"m:ideas"')
        self.assertLess(video_pos, animate_pos)
        self.assertLess(animate_pos, ideas_pos)
        self.assertIn("video_animate_min_price()", block)

    def test_no_old_animate_75_credit_copy(self) -> None:
        paths = [
            "flow_bot.py",
            "flow_copy.py",
            "deploy/photozhab/index.html",
            "deploy/photozhab/admin.html",
            "docs/VIDEO_UX.md",
            "docs/MONETIZATION.md",
            "docs/SELLER_BOT_PLAN.md",
            "docs/SELLER_BOT_AUDIT_2026-06-21.md",
        ]
        needles = ("Оживить фото · от 75 кр", "оживить фото → видео = от 75 кр", "Veo Lite, 9:16, 1 видео · 75 кр")
        for rel in paths:
            text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
            for needle in needles:
                self.assertNotIn(needle, text, rel)

    def test_single_screen_wizard_has_count_and_format_together(self) -> None:
        start = self.source.index("def wizard_kb")
        end = self.source.index("def reply_menu_kb")
        block = self.source[start:end]
        self.assertIn('"w:cnt:1"', block)
        # Format rows from shared helper; model is now a single toggle button.
        self.assertIn('_fmt_rows(fmt, "w:fmt")', block)
        self.assertIn('_imodel_toggle_btn(imodel, "w:imodel")', block)
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
        self.assertIn('_imodel_toggle_btn(imodel, "es:imodel")', block)

    def test_edit_confirm_kb_keeps_format_and_model_toggles(self) -> None:
        start = self.source.index("def edit_confirm_kb")
        block = self.source[start:start + 700]
        self.assertIn("def edit_confirm_kb(fmt: str, imodel: str)", block)
        self.assertIn('_fmt_rows(fmt, "es:fmt")', block)
        self.assertIn('_imodel_toggle_btn(imodel, "es:imodel")', block)
        self.assertIn('callback_data="es:apply"', block)

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
        start = self.source.index("def reply_menu_kb")
        end = self.source.index("async def _show_help_screen", start)
        block = self.source[start:end]
        # Reply-button taps are handled as plain text before prompt routing.
        self.assertIn('text == L("kb_gen")', self.source)
        self.assertIn("_is_balance_reply_text(text)", self.source)
        self.assertIn("def _balance_reply_label", self.source)
        self.assertIn('B(text=L("kb_gen"))', block)
        self.assertIn('B(text=L("kb_vid"))', block)
        self.assertIn('B(text=L("kb_menu"))', block)
        self.assertIn("B(text=_balance_reply_label(user_id))", block)
        for key in ("ideas", "myphoto", "invite", "help"):
            self.assertNotIn(f'B(text=L("{key}"))', block)
        for key in ("ideas", "myphoto", "invite", "help"):
            self.assertIn(f'text == L("{key}")', self.source)

    def test_public_help_ideas_referral_commands_wired(self) -> None:
        for command in ('Command("help")', 'Command("ideas")', 'Command("referral", "ref")'):
            self.assertIn(command, self.source)
        self.assertIn("async def _show_help_screen", self.source)
        self.assertIn("async def _show_referral_screen", self.source)
        self.assertIn("_show_ideas_root(message, user_id=user_id, edit=False)", self.source)
        for command in ('command="ideas"', 'command="help"', 'command="referral"'):
            self.assertIn(command, self.source)

    def test_start_resets_stale_generation_state(self) -> None:
        start = self.source.index("async def cmd_start")
        block = self.source[start:start + 700]
        self.assertIn("_reset_image_flow(user_id)", block)
        self.assertIn("_vid_clear(user_id)", block)

    def test_robokassa_sbp_topup_wired(self) -> None:
        for needle in (
            "ROBOKASSA_HASH_ALGO",
            "ROBOKASSA_INC_CURR_LABEL",
            'callback_data=f"m:robo:{pid}"',
            'data.startswith("m:robo:")',
            "robokassa_payment_signature(",
            "robokassa_result_signature(",
            "async def robokassa_result",
            'provider="robokassa"',
            "await _start_robokassa_web_server()",
        ):
            self.assertIn(needle, self.source, needle)

    def test_admin_grant_restricted(self) -> None:
        self.assertIn('@dp.message(Command("grant"))', self.source)
        self.assertIn("async def cmd_grant", self.source)
        self.assertIn("ADMIN_IDS", self.source)
        self.assertIn("message.from_user.id not in ADMIN_IDS", self.source)
        self.assertIn("credit_store.add(target, amount)", self.source)

    def test_status_diagnostics_restricted_to_admins(self) -> None:
        start = self.source.index('@dp.message(Command("status"))')
        end = self.source.index('@dp.message(Command("balance"))', start)
        block = self.source[start:end]
        self.assertIn("message.from_user.id not in ADMIN_IDS", block)
        self.assertIn('flow_copy.msg("admin_denied")', block)

        help_start = self.source.index("_HELP_SECTIONS")
        help_end = self.source.index("def _render_admin_help", help_start)
        help_block = self.source[help_start:help_end]
        self.assertGreater(
            help_block.index('("/status"'),
            help_block.index('("/grant'),
        )

    def test_session_keeper_has_locked_shutdown_close(self) -> None:
        start = self.source.index("class SessionKeeper:")
        end = self.source.index("async def _start_locked", start)
        block = self.source[start:end]
        self.assertIn("async def close(self):", block)
        self.assertIn("async with self._lock:", block)
        self.assertIn("await self._close_browser_locked()", block)

    def test_g_credits_lookup_does_not_refresh_bearer(self) -> None:
        start = self.source.index("async def get_g_credits")
        end = self.source.index("async def get_capmonster_balance", start)
        block = self.source[start:end]
        self.assertIn("session = await self._gcredits_session_snapshot()", block)
        self.assertNotIn("await self.get_session()", block)
        self.assertNotIn("await self._refresh_bearer()", block)

    def test_main_cleans_long_lived_resources_on_polling_exit(self) -> None:
        start = self.source.index("async def _main_impl():")
        end = self.source.index("async def main():", start)
        block = self.source[start:end]
        self.assertIn("robokassa_runner = None", block)
        self.assertIn("robokassa_runner = await _start_robokassa_web_server()", block)
        self.assertIn("await ready_event.wait()", block)
        self.assertLess(block.index("await ready_event.wait()"), block.index("await dp.start_polling(bot)"))
        self.assertIn("finally:", block)
        self.assertIn("startup_state[\"polling\"] = False", block)
        self.assertIn("await asyncio.gather(*warmup_tasks, return_exceptions=True)", block)
        self.assertIn("await robokassa_runner.cleanup()", block)
        self.assertIn("for acc_id, kp in keepers.items():", block)
        self.assertIn("await kp.close()", block)

    def test_main_wrapper_cleans_startup_cancellation_resources(self) -> None:
        start = self.source.index("async def main():")
        end = self.source.index('if __name__ == "__main__":', start)
        block = self.source[start:end]
        self.assertIn("await _main_impl()", block)
        self.assertIn("finally:", block)
        self.assertIn("await kp.close()", block)
        self.assertIn("await bot.session.close()", block)

    def test_shutdown_exception_filter_is_narrow(self) -> None:
        start = self.source.index("def _install_shutdown_exception_filter")
        end = self.source.index("async def _main_impl", start)
        block = self.source[start:end]
        self.assertIn('message == "Future exception was never retrieved"', block)
        self.assertIn("Connection closed while reading from the driver", block)
        self.assertIn("loop.default_exception_handler(context)", block)
        self.assertIn("_install_shutdown_exception_filter()", self.source)

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
        # realup button lives under each generated image (restored by operator
        # request) and is wired via action_callback_data / action == "realup".
        self.assertIn('elif action == "realup"', self.source)
        # realup must NOT silently fall back to the prompt enhance anymore.
        real_start = self.source.index("async def _real_upscale_and_send")
        real_block = self.source[real_start:real_start + 900]
        self.assertNotIn("_enhance_and_send", real_block)

    def test_image_keyboard_has_no_mix_button_and_credit_prices(self) -> None:
        start = self.source.index("def _image_keyboard")
        block = self.source[start:start + 1300]
        self.assertNotIn('b("mix"', block)          # «В микс» removed from results
        self.assertNotIn('b("up2x"', block)         # «Чёткость ×2» removed from results
        self.assertNotIn('b("vary"', block)         # Варианты removed from results (UX cleanup)
        # Edit button wired via action_callback_data; repeat via m:repeat
        self.assertIn('action_callback_data("edit", token)', block)
        self.assertIn('"m:repeat"', block)          # Повторить stays
        self.assertIn('· {edit_price} кр', block)   # price tags are credits, not Stars
        # «Улучшить качество» (родной апскейл) восстановлена под картинкой и
        # отправляет апскейленное фото через action == "realup".
        self.assertIn('action_callback_data("realup", token)', block)
        self.assertIn("action_price(\"realup\")", block)

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

    def test_credit_price_tags_on_action_buttons(self) -> None:
        # Video result / model rows show the credit cost in credits.
        self.assertIn('· {edit_price} кр', self.source)
        self.assertIn('· {next_price} кр', self.source)
        self.assertIn("{price} кр", self.source)

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
        # Ingredients supports Omni + Veo; Frames stays Veo-only.
        self.assertIn("def _vid_model_row", self.source)
        self.assertIn('VID_REF_DEFAULT_MODEL = "omni-flash-4s"', self.source)
        self.assertIn("VID_REF_VARIANTS = tuple(VIDEO_MODELS.keys())", self.source)
        self.assertIn('VID_FRAMES_VARIANTS = ("veo-lite", "veo-fast", "veo-quality")', self.source)
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

    def test_myphoto_waiting_text_stays_in_photo_upload_state(self) -> None:
        start = self.source.index("async def handle_plain_text")
        photo_guard = self.source.index('if awaiting == "photo":', start)
        image_fallback = self.source.index('st["pending_prompt"] = text', start)
        self.assertLess(photo_guard, image_fallback)
        block = self.source[photo_guard:photo_guard + 180]
        self.assertIn('flow_copy.msg("ask_photo")', block)
        self.assertIn("return", block)

    def test_marketplace_photo_waiting_text_stays_in_photo_upload_state(self) -> None:
        start = self.source.index("async def handle_plain_text")
        mp_photo_guard = self.source.index('if awaiting == "mp_photo":', start)
        image_fallback = self.source.index('st["pending_prompt"] = text', start)
        self.assertLess(mp_photo_guard, image_fallback)
        block = self.source[mp_photo_guard:mp_photo_guard + 350]
        self.assertIn("_mp_photo_request_text", block)
        self.assertIn("return", block)

    def test_marketplace_series_waiting_text_stays_in_photo_upload_state(self) -> None:
        start = self.source.index("async def handle_plain_text")
        mp_series_guard = self.source.index('if awaiting == "mp_series_photo":', start)
        image_fallback = self.source.index('st["pending_prompt"] = text', start)
        self.assertLess(mp_series_guard, image_fallback)
        block = self.source[mp_series_guard:mp_series_guard + 520]
        self.assertIn("_mp_series_request_text", block)
        self.assertIn("return", block)

    def test_marketplace_sku_name_text_saves_before_image_fallback(self) -> None:
        start = self.source.index("async def handle_plain_text")
        sku_guard = self.source.index('if awaiting == "mp_sku_name":', start)
        image_fallback = self.source.index('st["pending_prompt"] = text', start)
        self.assertLess(sku_guard, image_fallback)
        block = self.source[sku_guard:sku_guard + 450]
        self.assertIn("_save_pending_sku_item", block)
        self.assertIn("return", block)

    def test_marketplace_brandkit_text_saves_before_image_fallback(self) -> None:
        start = self.source.index("async def handle_plain_text")
        brand_guard = self.source.index('if awaiting == "mp_brandkit":', start)
        image_fallback = self.source.index('st["pending_prompt"] = text', start)
        self.assertLess(brand_guard, image_fallback)
        block = self.source[brand_guard:brand_guard + 650]
        self.assertIn("metrics.save_seller_profile", block)
        self.assertIn('"mp_brandkit_saved"', block)
        self.assertIn("return", block)

    def test_seller_result_keyboard_is_gated_to_seller_mode(self) -> None:
        start = self.source.index("async def _send_one_image")
        block = self.source[start:start + 900]
        self.assertIn("IS_SELLER", block)
        self.assertIn("_seller_image_keyboard(token)", block)
        self.assertIn("_image_keyboard(token)", block)

    def test_support_brief_text_runs_before_image_fallback(self) -> None:
        start = self.source.index("async def handle_plain_text")
        support_guard = self.source.index('if st.get("support_await"):', start)
        image_fallback = self.source.index('st["pending_prompt"] = text', start)
        self.assertLess(support_guard, image_fallback)
        block = self.source[support_guard:support_guard + 2200]
        self.assertIn("metrics.create_ticket", block)
        self.assertIn("ticket_text", block)
        self.assertIn("return", block)

    def test_support_photo_does_not_upload_to_flow(self) -> None:
        start = self.source.index("async def handle_photo")
        support_guard = self.source.index('if st.get("support_await"):', start)
        first_upload = self.source.index('flow_copy.msg("uploading_photo")', start)
        self.assertLess(support_guard, first_upload)
        block = self.source[support_guard:support_guard + 320]
        self.assertIn("текстовый бриф", block)
        self.assertIn("return", block)

    def test_captioned_photo_without_mode_asks_image_or_video(self) -> None:
        self.assertIn("pending_photo_routes", self.source)
        self.assertIn('F.data.startswith("pr:")', self.source)
        self.assertIn('"pr:img"', self.source)
        self.assertIn('"pr:vid"', self.source)
        start = self.source.index("async def handle_photo")
        block = self.source[start:start + 13000]
        self.assertIn("await _offer_photo_route_choice(message, user_id=user_id, caption=caption)", block)
        self.assertIn("await _prepare_photo_edit_from_file_id(", block)
        self.assertIn("_prepare_photo_video_from_file_id(", self.source)
        # The old fallback edited immediately when a caption was attached.
        self.assertNotIn("await _edit_and_send(message, ref, caption", block)

    def test_create_image_photo_caption_stops_at_edit_confirm(self) -> None:
        start = self.source.index("async def handle_photo")
        block = self.source[start:start + 4500]
        self.assertIn('st.get("step") in ("prompt_picker", "wizard")', block)
        self.assertIn('or st.get("await") == "prompt"', block)
        self.assertIn('or st.get("pending_prompt")', block)
        self.assertIn('st["await"] = "edit_confirm" if caption else "edit"', self.source)
        self.assertIn("await show_edit_confirm(message, user_id=user_id, edit=False)", self.source)

    def test_edit_and_send_can_use_callback_actor_id(self) -> None:
        start = self.source.index("async def _edit_and_send")
        block = self.source[start:start + 1200]
        self.assertIn("actor_id: int | None = None", block)
        self.assertIn("user_id = actor_id if actor_id is not None else message.from_user.id", block)
        self.assertIn("actor_id=user_id", self.source)

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

    def test_video_result_caption_is_clean_for_sharing(self) -> None:
        # Подпись под готовым видео держим чистой: цены/действия
        # (Изменить · Продлить, omni-hint) живут на кнопках под роликом и
        # не должны попадать в caption — иначе при пересылке видео подпись
        # выглядит мусорно. Доступность Продлить решает клавиатура
        # (_video_can_extend в video_result_kb).
        start = self.source.index('caption = flow_copy.msg("vid_result_caption"')
        block = self.source[start:start + 350]
        self.assertNotIn("vid_result_actions_hint", block)
        self.assertNotIn("vid_omni_no_extend_hint", block)

    def test_video_result_caption_has_referral_link_with_html_parse_mode(self) -> None:
        # Видео-результат должен звать друзей так же, как картинки
        # (_send_result_pairs) — и parse_mode="HTML" обязателен, иначе
        # тег <a href> уйдёт пользователю как голый текст.
        start = self.source.index('caption = flow_copy.msg("vid_result_caption"')
        block = self.source[start:start + 4000]
        self.assertIn("_referral_link(user_id)", block)
        self.assertIn('Создай своё в @', block)
        answer_video_start = block.index("await message.answer_video(")
        answer_video_block = block[answer_video_start:answer_video_start + 300]
        self.assertIn('parse_mode="HTML"', answer_video_block)
        answer_doc_start = block.index("await message.answer_document(")
        answer_doc_block = block[answer_doc_start:answer_doc_start + 300]
        self.assertIn('parse_mode="HTML"', answer_doc_block)

    def test_video_result_caption_escapes_prompt(self) -> None:
        # prompt идёт в HTML-caption — без escape сломает parse_mode="HTML"
        # на промптах с <, >, & (раньше caption слали без parse_mode, поэтому
        # экранирование не требовалось; теперь требуется).
        start = self.source.index('caption = flow_copy.msg("vid_result_caption"')
        line = self.source[start:start + 200]
        self.assertIn("html.escape(_short_prompt(prompt, 60))", line)

    def test_ideas_template_photo_becomes_edit_base(self) -> None:
        # Фото внутри Q&A картиночного шаблона не должно уходить в общий
        # «Изменить моё фото» — оно становится основой, к которой применяется
        # собранный промпт шаблона как правка (запрос оператора).
        self.assertIn("async def _template_photo_received", self.source)
        # handle_photo перехватывает фото для НЕ-видео шаблона.
        self.assertIn(
            'if tp_tpl and prompts_lib.template_target(tp_tpl) != "video":',
            self.source,
        )
        self.assertIn("await _template_photo_received(message, user_id=user_id)", self.source)
        # При завершении шаблона с фото — идём через _edit_and_send, а не show_wizard.
        start = self.source.index("async def _render_template_step")
        block = self.source[start:start + 3000]
        self.assertIn("elif tp_photo:", block)
        self.assertIn("await _edit_and_send(message, ref, prompt", block)
        # Видео-шаблоны фото-основу не используют (upload идёт в видео-визарде).
        self.assertIn("tp_photo для видео не используем", block)

    def test_guided_video_carries_format_and_style(self) -> None:
        # «Подбор по шагам» → видео: выбранный формат (9:16) и стиль должны
        # переноситься в видео-визард, а не сбрасываться на дефолт 16:9.
        # show_video_prompt_input применяет vfmt/vstyle ПОСЛЕ _vid_clear.
        start = self.source.index("async def show_video_prompt_input")
        block = self.source[start:start + 900]
        self.assertIn("vfmt: str | None = None", block)
        self.assertIn("vstyle: str | None = None", block)
        clear_at = block.index("_vid_clear(user_id)")
        apply_at = block.index('st["vfmt"] = vfmt')
        self.assertLess(clear_at, apply_at)  # применяем после очистки
        # Guided-ветка передаёт формат и стиль.
        self.assertIn('gv_fmt = "port" if answers.get("format") in ("story", "avatar") else "land"', self.source)
        self.assertIn("vfmt=gv_fmt, vstyle=gv_style", self.source)

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
        self.assertIn("кр", block)
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

    def test_video_403_refreshes_session_before_next_action(self) -> None:
        start = self.source.index("async def generate_video")
        end = self.source.index("if not solved_any:", start)
        block = self.source[start:end]
        self.assertIn("refreshed_after_403 = False", block)
        self.assertIn("if gen_status == 403:", block)
        self.assertIn("await self.keeper._refresh_bearer()", block)
        self.assertIn("session = await self.keeper.get_session()", block)
        self.assertIn("headers = self._build_headers(session)", block)

    def test_video_403_does_not_auto_browser_fallback_in_production(self) -> None:
        start = self.source.index("async def generate_video")
        block = self.source[start:self.source.index("if gen_status != 200:", start)]
        self.assertIn("for _attempt in range(SessionKeeper.VIDEO_GEN_MAX_ATTEMPTS):", block)
        self.assertNotIn("post_json_via_browser", block)
        self.assertIn('"browser_fallback": browser_fallback_used', block)

    def test_video_browser_fallback_uses_browser_safe_headers(self) -> None:
        helper = self.source[
            self.source.index("def _browser_fetch_headers"):
            self.source.index("def _playwright_proxy_config")
        ]
        self.assertIn('"Authorization"', helper)
        self.assertIn('"Content-Type"', helper)
        self.assertNotIn('"User-Agent"', helper)
        self.assertNotIn('"Origin"', helper)
        self.assertNotIn('"Referer"', helper)
        self.assertNotIn('"Sec-Fetch",', helper)

    def test_video_ab_compares_submit_transports_without_polling(self) -> None:
        start = self.source.index("async def video_transport_ab_test")
        end = self.source.index("async def generate_images", start)
        block = self.source[start:end]
        self.assertIn('"direct_http"', block)
        self.assertIn('"browser_fetch"', block)
        self.assertIn("aiohttp.ClientSession", block)
        self.assertIn("post_json_via_browser", block)
        self.assertIn("VIDEO_GEN_ENDPOINT", block)
        self.assertNotIn("VIDEO_POLL_ENDPOINT", block)
        self.assertNotIn("fetch_video_bytes", block)

    def test_video_401_retries_after_refresh(self) -> None:
        start = self.source.index("async def generate_video")
        end = self.source.index("if not solved_any:", start)
        block = self.source[start:end]
        self.assertIn("refreshed_after_401 = False", block)
        self.assertIn("for _auth_attempt in range(2):", block)
        self.assertIn("if gen_status == 401 and not refreshed_after_401:", block)
        self.assertIn("refreshed_after_401 = True", block)
        self.assertIn("session = await self.keeper.get_session()", block)
        self.assertIn("headers = self._build_headers(session)", block)
        tail = self.source[end:self.source.index("if gen_status == 429:", end)]
        self.assertNotIn("await self.keeper._refresh_bearer()", tail)

    def test_video_account_risk_cools_down_account(self) -> None:
        start = self.source.index("async def generate_video")
        end = self.source.index("if gen_status != 200:", start)
        block = self.source[start:end]
        self.assertIn('"account_risk": "video_auth"', block)
        self.assertIn('"account_risk": "video_recaptcha_403"', block)

        helper = self.source[
            self.source.index("def _mark_video_account_failure"):
            self.source.index("async def _download_ref_image_bytes")
        ]
        # video_auth — кулдаун сразу; video_recaptcha_403 (стохастичный) —
        # через счётчик fail (кулдаун только после серии).
        self.assertIn('risk == "video_auth"', helper)
        self.assertIn("account_pool.mark_cooldown(account_id)", helper)
        self.assertIn("account_pool.mark_failure(account_id)", helper)
        self.assertIn('risk == "video_recaptcha_403"', helper)
        # Провайдерский 429 → аккаунт сразу в кулдаун (а не через счётчик fail).
        self.assertIn("_is_rate_limit_error(result)", helper)
        self.assertIn('"reason": "rate_limited", "op": "video"', helper)

        video = self.source[
            self.source.index("async def _do_video_generate_and_send"):
            self.source.index("async def _video_download")
        ]
        self.assertIn("_mark_video_account_failure(acc_id, result)", video)

    def test_after_result_offers_video_balance_and_menu(self) -> None:
        block = self.source[self.source.index("async def _after_result"):][:700]
        for cb in ('"m:gen"', '"m:vid"', '"m:menu"'):
            self.assertIn(cb, block)
        self.assertIn("_invite_button", block)      # Позвать друга
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
        self.assertIn("effective_model_key=%s", self.source)
        self.assertIn("video_reference_model_key(model_key, aspect)", self.source)
        self.assertIn("video_frames_model_key(model_key)", self.source)

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
        block = self.source[start:start + 3000]
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
        # Подарок приглашённому другу (+15) при join, мимо payments-pipeline.
        self.assertIn("REFERRAL_REFERRED_BONUS", self.source)
        self.assertIn('"referral_referred_bonus"', self.source)
        # «+50 за генерацию» удалено целиком — награда рефереру только на оплате
        # (anti-farm, REFERRAL.md §3). Второго пути быть не должно.
        self.assertNotIn("_maybe_apply_first_referral_generation_reward", self.source)
        self.assertNotIn("grant_first_generation_referral_reward", self.source)
        self.assertNotIn("REFERRAL_FIRST_GENERATION_BONUS", self.source)
        self.assertIn("first_referral_cta", self.source)
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
        self.assertNotIn('"tier": "join"', self.source)
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
        kb = self.source[self.source.index("def _image_keyboard"):][:1200]
        self.assertIn('f"an:img:{token}"', kb)
        self.assertIn('@dp.callback_query(F.data.startswith("an:"))', self.source)
        self.assertIn('data == "m:animate"', self.source)
        self.assertIn("async def show_animate_photo_input", self.source)
        animate_menu = self.source[self.source.index('elif data == "m:animate"'):][:500]
        self.assertIn("show_animate_photo_input", animate_menu)
        self.assertIn('"vanimate_photo"', self.source)
        # Seeds the generated image into the new video wizard as vphoto reference.
        self.assertIn('st["vphoto"] = ref.source', self.source)
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
        self.assertGreaterEqual(self.source.count("_clear_image_flow_keys(st)"), 2)

    def test_video_photo_wait_text_does_not_open_image_wizard(self) -> None:
        start = self.source.index("async def handle_plain_text")
        wait_guard = self.source.index('if st.get("vawait") == "ving_photo":', start)
        start_guard = self.source.index('if st.get("vawait") == "vfrm_start":', start)
        end_guard = self.source.index('if st.get("vawait") == "vfrm_end":', start)
        ing_ready = self.source.index(
            'if st.get("vmode") == "ingredients" and (st.get("ving_photos") or []):',
            start,
        )
        image_fallback = self.source.index('st["pending_prompt"] = text', start)
        # ingredients check must come BEFORE the vawait guards (ingredients-ready text
        # should trigger video generation, not be blocked by the photo-wait guard).
        for guard in (wait_guard, start_guard, end_guard):
            self.assertLess(ing_ready, guard)
            self.assertLess(guard, image_fallback)
        block = self.source[wait_guard:end_guard + 180]
        self.assertIn('flow_copy.msg("vid_ing_send_photo")', block)
        self.assertIn('flow_copy.msg("vid_frm_send_photo_start")', block)
        self.assertIn('flow_copy.msg("vid_frm_send_photo_end")', block)

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
        def _texts(val):
            """Yield all strings from a value (str or list of str)."""
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, str):
                        yield item
            elif isinstance(val, str):
                yield val
        for key, val in flow_copy.MESSAGES.items():
            for text in _texts(val):
                low = text.lower()
                self.assertNotIn("flow", low, key)
                self.assertNotIn("google", low, key)
                self.assertNotIn("капч", low, key)
                self.assertNotIn("recaptcha", low, key)
        for key, text in flow_copy.LABELS.items():
            self.assertNotIn("flow", text.lower(), key)


class LandingStaticContentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = (PROJECT_ROOT / "deploy" / "photozhab" / "index.html").read_text(encoding="utf-8")
        self.privacy = (PROJECT_ROOT / "deploy" / "photozhab" / "privacy.html").read_text(encoding="utf-8")
        self.admin = (PROJECT_ROOT / "deploy" / "photozhab" / "admin.html").read_text(encoding="utf-8")

    def test_hero_promises_honest_bot_points(self) -> None:
        for needle in (
            "30 кредитов на старте",
            "3 картинки бесплатно",
            "цены видны до нажатия",
            "без VPN",
            "без подписок",
            "Veo работает",
        ):
            self.assertIn(needle, self.index)

    def test_trial_package_is_image_only_on_landing(self) -> None:
        self.assertIn("45 кр", self.index)
        self.assertIn("только картинки: примерно 4 изображения", self.index)
        self.assertIn("100 кр", self.index)
        self.assertIn("примерно 10 картинок или 2 коротких видео", self.index)

    def test_privacy_policy_page_and_footer_link_exist(self) -> None:
        self.assertIn('href="/privacy.html"', self.index)
        self.assertIn("Политика конфиденциальности", self.privacy)
        self.assertIn("152-ФЗ", self.privacy)
        for needle in ("telegram_id", "данные платежа", "Robokassa", "ФИО владельца сервиса"):
            self.assertIn(needle, self.privacy)

    def test_admin_accounts_do_not_fall_back_to_demo_rows(self) -> None:
        self.assertNotIn("(await api('GET','/accounts')) || DEMO.accounts", self.admin)
        self.assertIn("Array.isArray(accsRaw) ? accsRaw : null", self.admin)
        self.assertIn("if (!Array.isArray(accs))", self.admin)

    def test_admin_sellers_shows_sku_project_counts(self) -> None:
        self.assertIn('id="s-sku"', self.admin)
        self.assertIn("data.total_sku_projects", self.admin)
        self.assertIn("s.sku_projects", self.admin)

    def test_admin_support_has_done4you_queue_controls(self) -> None:
        self.assertIn('data-sf="done4you"', self.admin)
        self.assertIn('data-sf="in_work"', self.admin)
        self.assertIn('data-sf="done"', self.admin)
        self.assertIn("function _ticketKindBadge", self.admin)
        self.assertIn("setTicketStatus(${t.id},'in_work')", self.admin)
        self.assertIn("setTicketStatus(${t.id},'done')", self.admin)

    def test_admin_has_ad_cac_calculator(self) -> None:
        for needle in (
            'id="ad-segment"',
            'id="ad-channels"',
            'function adRecalc()',
            'function adApplyLiveDefaults',
            'adApplyLiveDefaults({activation, repeat, margin});',
            'CAC green',
            'Flow quota',
        ):
            self.assertIn(needle, self.admin)

    def test_admin_demo_copy_matches_current_bot_copy(self) -> None:
        self.assertNotIn("удобным способом", self.admin)
        self.assertNotIn("referral_first_generation_reward_got", self.admin)


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


class RobokassaWebhookTests(unittest.TestCase):
    def _load_bot(self):
        import importlib
        import os

        try:
            import aiogram  # noqa: F401
        except Exception:
            self.skipTest("aiogram not installed")

        os.environ.setdefault("TELEGRAM_TOKEN", "123:test")
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        os.environ["USER_CREDITS_FILE"] = str(Path(self.tmpdir.name) / "credits.json")
        fb = importlib.import_module("flow_bot")
        return importlib.reload(fb)

    @staticmethod
    def _request(params: dict[str, str]):
        return SimpleNamespace(query=params, method="GET")

    @staticmethod
    def _signed_result_params(
        fb, *, inv_id: str = "777", pack: str = "trial", user: str = "123",
        bot_scope: str | None = None,
    ) -> dict[str, str]:
        out_sum = fb._robokassa_pack_amount(pack)
        shp = {"Shp_pack": pack, "Shp_user": user}
        if bot_scope is not None:
            shp["Shp_bot"] = bot_scope
        sig = flow_core.robokassa_result_signature(
            out_sum,
            inv_id,
            fb.ROBOKASSA_PASSWORD2,
            shp_params=shp,
            algorithm=fb.ROBOKASSA_HASH_ALGO,
        )
        return {
            "OutSum": out_sum,
            "InvId": inv_id,
            "SignatureValue": sig,
            **shp,
        }

    def _configure(self, fb):
        fb.ROBOKASSA_ENABLED = True
        fb.ROBOKASSA_MERCHANT_LOGIN = "photozhab"
        fb.ROBOKASSA_PASSWORD1 = "pass1"
        fb.ROBOKASSA_PASSWORD2 = "pass2"
        fb.ROBOKASSA_HASH_ALGO = "sha256"
        fb.STARS_TO_RUB = 1.3
        fb.ROBOKASSA_CARD_DISCOUNT_PCT = 10
        fb.ROBOKASSA_SCOPE = "consumer"
        fb.ROBOKASSA_CONSUMER_RESULT_URL = "http://127.0.0.1:8081/robokassa/result"
        fb.ROBOKASSA_SELLER_RESULT_URL = "http://127.0.0.1:8082/robokassa/result"

        events: list[tuple[str, int, str, dict | None]] = []
        tx_calls: list[dict] = []
        credits_added: list[tuple[int, int]] = []

        class FakeCreditStore:
            def add(self, user_id, credits):
                credits_added.append((int(user_id), int(credits)))
                return sum(c for u, c in credits_added if u == int(user_id))

        def record_transaction_status(**kwargs):
            tx_calls.append(kwargs)
            return "duplicate" if len(tx_calls) > 1 else "new"

        def log_event(event_type, user_id=None, source="", payload=None, username=None):
            events.append((event_type, int(user_id or 0), source, payload))

        async def notify(user_id, credits, balance):
            events.append(("notify", int(user_id), "robokassa", {"credits": credits, "balance": balance}))

        def referral(*args, **kwargs):
            events.append(("referral_checked", int(args[0]) if args else 0, "robokassa", kwargs))

        fb.credit_store = FakeCreditStore()
        fb.metrics = SimpleNamespace(
            record_transaction_status=record_transaction_status,
            log_event=log_event,
            mark_user_blocked=lambda user_id: None,
        )
        fb._notify_robokassa_success = notify
        fb._maybe_apply_referral_rewards = referral
        return SimpleNamespace(events=events, tx_calls=tx_calls, credits_added=credits_added)

    def test_robokassa_payment_url_carries_fiscal_receipt(self) -> None:
        import json as _json
        from urllib.parse import parse_qs, quote, urlsplit

        fb = self._load_bot()
        self._configure(fb)

        url = fb._robokassa_payment_url(123, "xl", 555)
        qs = parse_qs(urlsplit(url).query)

        # Номенклатура присутствует — иначе Robokassa жалуется на пустой чек.
        self.assertIn("Receipt", qs)
        receipt = _json.loads(qs["Receipt"][0])
        item = receipt["items"][0]
        out_sum = fb._robokassa_pack_amount("xl")
        self.assertEqual(round(item["sum"], 2), round(float(out_sum), 2))
        self.assertEqual(item["tax"], "none")          # самозанятый: без НДС
        self.assertNotIn("sno", receipt)               # режим СМЗ держит Robokassa
        self.assertIn("кредит", item["name"].lower())  # есть название позиции

        # Подпись считается по СЫРОМУ JSON, а в ссылку он идёт rawurlencode'нутым.
        raw_json = fb._robokassa_receipt_json("xl", out_sum, fb.credit_pack("xl")["credits"])
        self.assertIn("Receipt=" + quote(raw_json, safe=""), url)
        expected_sig = flow_core.robokassa_payment_signature(
            fb.ROBOKASSA_MERCHANT_LOGIN, out_sum, 555, fb.ROBOKASSA_PASSWORD1,
            shp_params={"Shp_bot": "consumer", "Shp_pack": "xl", "Shp_user": 123},
            receipt=raw_json, algorithm=fb.ROBOKASSA_HASH_ALGO,
        )
        self.assertEqual(qs["Shp_bot"][0], "consumer")
        self.assertEqual(qs["SignatureValue"][0], expected_sig)

    def test_robokassa_webhook_is_idempotent_by_inv_id(self) -> None:
        fb = self._load_bot()
        state = self._configure(fb)
        params = self._signed_result_params(fb, inv_id="9001", pack="trial", user="123")

        first = asyncio.run(fb.robokassa_result(self._request(params)))
        second = asyncio.run(fb.robokassa_result(self._request(params)))

        self.assertEqual(first.status, 200)
        self.assertEqual(second.status, 200)
        self.assertEqual(first.text, "OK9001")
        self.assertEqual(second.text, "OK9001")
        self.assertEqual(state.credits_added, [(123, 45)])
        self.assertEqual([c["provider_payment_id"] for c in state.tx_calls], ["robokassa:9001", "robokassa:9001"])

    def test_robokassa_scoped_invoice_uses_scoped_payment_id(self) -> None:
        fb = self._load_bot()
        state = self._configure(fb)
        params = self._signed_result_params(
            fb, inv_id="9011", pack="trial", user="123", bot_scope="consumer",
        )

        response = asyncio.run(fb.robokassa_result(self._request(params)))

        self.assertEqual(response.status, 200)
        self.assertEqual(response.text, "OK9011")
        self.assertEqual(state.credits_added, [(123, 45)])
        self.assertEqual(state.tx_calls[0]["provider_payment_id"], "robokassa:consumer:9011")

    def test_robokassa_seller_callback_forwards_to_seller_process(self) -> None:
        fb = self._load_bot()
        state = self._configure(fb)
        params = self._signed_result_params(
            fb, inv_id="9012", pack="s_card", user="123", bot_scope="seller",
        )
        forwards: list[tuple[str, dict[str, str]]] = []

        async def fake_forward(scope: str, data: dict[str, str]):
            forwards.append((scope, dict(data)))
            return fb.web.Response(text="OK9012")

        fb._robokassa_forward_result = fake_forward

        response = asyncio.run(fb.robokassa_result(self._request(params)))

        self.assertEqual(response.status, 200)
        self.assertEqual(response.text, "OK9012")
        self.assertEqual(forwards, [("seller", params)])
        self.assertEqual(state.credits_added, [])
        self.assertEqual(state.tx_calls, [])

    def test_robokassa_seller_process_accepts_seller_scope(self) -> None:
        fb = self._load_bot()
        state = self._configure(fb)
        fb.ROBOKASSA_SCOPE = "seller"
        params = self._signed_result_params(
            fb, inv_id="9013", pack="s_card", user="456", bot_scope="seller",
        )

        response = asyncio.run(fb.robokassa_result(self._request(params)))

        self.assertEqual(response.status, 200)
        self.assertEqual(response.text, "OK9013")
        self.assertEqual(state.credits_added, [(456, 60)])
        self.assertEqual(state.tx_calls[0]["provider_payment_id"], "robokassa:seller:9013")

    def test_robokassa_webhook_credits_without_success_redirect(self) -> None:
        fb = self._load_bot()
        state = self._configure(fb)
        params = self._signed_result_params(fb, inv_id="9002", pack="trial", user="456")

        response = asyncio.run(fb.robokassa_result(self._request(params)))

        self.assertEqual(response.status, 200)
        self.assertEqual(response.text, "OK9002")
        self.assertEqual(state.credits_added, [(456, 45)])
        self.assertEqual(state.tx_calls[0]["user_id"], 456)
        self.assertEqual(state.tx_calls[0]["package_id"], "trial")

    def test_robokassa_unmatched_payment_is_logged_for_manual_reconcile(self) -> None:
        fb = self._load_bot()
        state = self._configure(fb)
        params = self._signed_result_params(fb, inv_id="9003", pack="trial", user="not-a-user")

        response = asyncio.run(fb.robokassa_result(self._request(params)))

        self.assertEqual(response.status, 400)
        self.assertEqual(response.text, "bad order")
        self.assertEqual(state.credits_added, [])
        unmatched = [e for e in state.events if e[0] == "robokassa_unmatched_payment"]
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(unmatched[0][3]["inv_id"], "9003")
        self.assertEqual(unmatched[0][3]["reason"], "bad_order")


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
            # Модель — одна кнопка-тогл: показывает текущую, cb_data → следующая.
            model_buttons = [
                b
                for row in fb.wizard_kb(1, "sq", "nb2").inline_keyboard
                for b in row
                if (b.callback_data or "").startswith("w:imodel:")
            ]
            self.assertEqual(len(model_buttons), 1, "должна быть ровно одна кнопка модели")
            self.assertIn("Nano Banana 2", model_buttons[0].text)
            # callback_data ведёт на следующую модель (nbpro)
            self.assertEqual(model_buttons[0].callback_data, "w:imodel:nbpro")
            fb.reply_menu_kb()             # persistent bottom keyboard
            methods = fb.topup_kb()
            method_texts = [b.text for row in methods.inline_keyboard for b in row]
            self.assertIn("Оплата через Stars", method_texts)
            self.assertIn("СБП/Карта · выгоднее", method_texts)
            stars_rows = fb.topup_stars_kb().inline_keyboard  # public Stars packs (no test pack)
            stars_texts = [b.text for row in stars_rows for b in row]
            self.assertIn("45 кр · ≈4 карточек · 35⭐", stars_texts)
            self.assertTrue(any("1500 кр" in text and "≈150 карточек" in text and "900⭐" in text for text in stars_texts))
            admin_stars_texts = [b.text for row in fb.topup_stars_kb(is_admin=True).inline_keyboard for b in row]
            self.assertFalse(any("Тест" in text for text in admin_stars_texts))
            old_test_flag = fb.TOPUP_TEST_PACKS_ENABLED
            fb.TOPUP_TEST_PACKS_ENABLED = True
            try:
                flagged_admin_texts = [b.text for row in fb.topup_stars_kb(is_admin=True).inline_keyboard for b in row]
                flagged_user_texts = [b.text for row in fb.topup_stars_kb(is_admin=False).inline_keyboard for b in row]
                self.assertTrue(any("Тест" in text for text in flagged_admin_texts))
                self.assertFalse(any("Тест" in text for text in flagged_user_texts))
            finally:
                fb.TOPUP_TEST_PACKS_ENABLED = old_test_flag
            robo_texts = [b.text for row in fb.topup_robo_kb().inline_keyboard for b in row]
            self.assertIn("45 кр · ≈4 карточек · 45 ₽", robo_texts)
            self.assertTrue(any("100 кр" in text and "≈2 видео" in text and "90 ₽" in text for text in robo_texts))
            self.assertTrue(any("1500 кр" in text and "≈150 карточек" in text and "1050 ₽" in text for text in robo_texts))
            self.assertFalse(any("🔥" in text or "+%" in text for text in stars_texts + robo_texts))
            self.assertIn("картинка от", fb._topup_copy("topup_screen"))
            self.assertIn("видео от", fb._topup_copy("topup_robo_screen"))
            fb._image_keyboard("abcd1234")
            fb.video_family_kb()           # family buttons now carry "· от N кр"
            fb.ingredients_kb(1, "land", 1, "veo-fast", has_caption=True)
            main_rows = fb.main_menu_kb().inline_keyboard
            animate_main = next(
                b for row in main_rows for b in row if (b.callback_data or "") == "m:animate"
            )
            self.assertIn("50 кр", animate_main.text)
            # Family buttons show a min-price hint.
            fam_first = fb.video_family_kb().inline_keyboard[0][0].text
            self.assertIn("кр", fam_first)
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
                self.assertIn("кр", upload_btn.text)
            finally:
                fb.UPLOAD_VIDEO_EDIT_ENABLED = False
            # "Оживить фото" under a generated image (an:img:) now carries a price too.
            img_rows = fb._image_keyboard("abcd1234").inline_keyboard
            animate_btn = next(
                b for row in img_rows for b in row
                if (b.callback_data or "").startswith("an:img:")
            )
            self.assertIn("кр", animate_btn.text)
            self.assertIn("50 кр", animate_btn.text)
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

    def test_video_engine_toggle_decouples_model_from_photo(self) -> None:
        import os

        try:
            import aiogram  # noqa: F401
        except Exception:
            self.skipTest("aiogram not installed")
        os.environ.setdefault("TELEGRAM_TOKEN", "123:test")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["USER_CREDITS_FILE"] = str(Path(tmp) / "credits.json")
            import importlib
            fb = importlib.import_module("flow_bot")
            importlib.reload(fb)
            self._assert_engine_toggle(fb)

    def _assert_engine_toggle(self, fb) -> None:
        # Default engine is ⚡ Быстро (Omni), even with a photo attached — the
        # old "photo => Veo" coupling is gone.
        self.assertEqual(fb._nwiz_engine({}), "omni")
        st_photo = {"vphoto": {"media_id": "m"}, "vdur": 4}
        self.assertEqual(fb._nwiz_model(st_photo), "omni-flash-4s")
        # Choosing 💎 Качество switches to Veo, with or without a photo.
        self.assertEqual(fb._nwiz_model({"vphoto": {"media_id": "m"}, "vengine": "veo"}), "veo-lite")
        self.assertEqual(fb._nwiz_model({"vengine": "veo"}), "veo-lite")
        # Omni duration still drives the Omni model.
        self.assertEqual(fb._nwiz_model({"vengine": "omni", "vdur": 6}), "omni-flash-6s")
        # An Omni-animated (r2v) result is NOT extendable; only Veo is.
        VR = fb.VideoRef
        omni_r2v = VR(user_id=1, project_id="p", media_id="m", workflow_id="w",
                      model_id="omni-flash-6s", mode="ingredients")
        self.assertFalse(fb._video_can_extend(omni_r2v))
        # Keyboard exposes the engine toggle; duration row only for Быстро.
        fb.wizard_state[424242].update({"vprompt": "hi", "vfmt": "land", "vdur": 4})
        datas = [b.callback_data for row in fb._nwiz_kb(424242).inline_keyboard for b in row]
        self.assertIn("v:neng:omni", datas)
        self.assertIn("v:neng:veo", datas)
        self.assertIn("v:ndur:4", datas)
        # AI prompt-improve button appears once a draft prompt exists; costs 5.
        self.assertIn("ag:vimprove", datas)
        import flow_core as _fc
        self.assertEqual(_fc.action_price("prompt_improve"), 5)
        # The image wizard exposes the same agent improve button (ag:improve).
        img_datas = [b.callback_data for row in fb.wizard_kb(1, "land", show_improve=True).inline_keyboard for b in row]
        self.assertIn("ag:improve", img_datas)
        self.assertNotIn("ag:improve", [b.callback_data for row in fb.wizard_kb(1, "land").inline_keyboard for b in row])
        # Edit-my-photo confirm screen exposes agent improve + apply.
        ec = [b.callback_data for row in fb.edit_confirm_kb("land", "nb2").inline_keyboard for b in row]
        self.assertIn("ag:eimprove", ec)
        self.assertIn("es:apply", ec)
        fb.wizard_state[424242]["vengine"] = "veo"
        datas_veo = [b.callback_data for row in fb._nwiz_kb(424242).inline_keyboard for b in row]
        self.assertIn("v:neng:veo", datas_veo)
        self.assertNotIn("v:ndur:4", datas_veo)
        # 💎 Качество exposes the Veo level picker Lite/Fast/Quality.
        self.assertIn("v:nqual:lite", datas_veo)
        self.assertIn("v:nqual:fast", datas_veo)
        self.assertIn("v:nqual:quality", datas_veo)
        # Selecting Fast/Quality drives the corresponding Veo model.
        self.assertEqual(fb._nwiz_model({"vengine": "veo", "vquality": "fast"}), "veo-fast")
        self.assertEqual(fb._nwiz_model({"vengine": "veo", "vquality": "quality"}), "veo-quality")


if __name__ == "__main__":
    unittest.main()
