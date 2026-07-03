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

_PR2A_PROVIDER_SOURCE = (
    (PROJECT_ROOT / "flow_provider" / "client.py").read_text(encoding="utf-8")
    + "\n"
    + (PROJECT_ROOT / "flow_provider" / "runtime_config.py").read_text(encoding="utf-8")
)


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
        # Video/edit keyboard builders moved to channels/telegram/keyboards.py
        # (Phase 5). Scrapes of those defs read kb_source instead of flow_bot.
        self.kb_source = (
            PROJECT_ROOT / "channels" / "telegram" / "keyboards.py"
        ).read_text(encoding="utf-8")
        # Public command handlers (/menu /help /referral /balance) moved to
        # channels/telegram/routers/commands.py (Phase 6). Scrapes of those
        # handlers read commands_router_source instead of flow_bot.
        self.commands_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "commands.py"
        ).read_text(encoding="utf-8")
        # /start deep-link handling moved to its own first-included router.
        self.start_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "start.py"
        ).read_text(encoding="utf-8")
        # Telegram Stars pre-checkout and successful-payment handlers moved to
        # their own router (Phase 6, wave G).
        self.payments_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "payments.py"
        ).read_text(encoding="utf-8")
        # Photo input (F.photo message handler) moved to its own router
        # (Phase 6, wave H).
        self.photo_input_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "photo_input.py"
        ).read_text(encoding="utf-8")
        # Photo-route callback handler (pr:) moved to its own router (Phase 6).
        self.photo_route_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "photo_route.py"
        ).read_text(encoding="utf-8")
        self.ideas_hub_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "ideas_hub.py"
        ).read_text(encoding="utf-8")
        self.ideas_flow_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "ideas_flow.py"
        ).read_text(encoding="utf-8")
        self.video_upload_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "video_upload.py"
        ).read_text(encoding="utf-8")
        self.edit_settings_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "edit_settings.py"
        ).read_text(encoding="utf-8")
        self.animate_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "animate.py"
        ).read_text(encoding="utf-8")
        self.wizard_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "wizard.py"
        ).read_text(encoding="utf-8")
        self.menu_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "menu.py"
        ).read_text(encoding="utf-8")
        self.image_action_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "image_action.py"
        ).read_text(encoding="utf-8")
        # Video wizard callback handler (v:) moved to its own router (Phase 6).
        self.video_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "video.py"
        ).read_text(encoding="utf-8")
        # /ideas + image slash commands moved to their own router (Phase 6).
        self.generation_commands_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "generation_commands.py"
        ).read_text(encoding="utf-8")
        # /acc_off /acc_on /acc_vid_off /acc_vid_on /admin_help moved to their
        # own router (Phase 6); _render_admin_help/_HELP_SECTIONS stay in
        # flow_bot.py and are injected into the router.
        self.admin_accounts_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "admin_accounts.py"
        ).read_text(encoding="utf-8")
        # /status moved to its own admin diagnostics router (Phase 6).
        self.admin_status_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "admin_status.py"
        ).read_text(encoding="utf-8")
        # /admin_today .. /admin_cohort read-only reports moved to their own
        # router (Phase 6).
        self.admin_reports_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "admin_reports.py"
        ).read_text(encoding="utf-8")
        # /promo /addpromo /grant /refund (credit-mutating) moved to their own
        # router (Phase 6, wave E).
        self.admin_credits_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "admin_credits.py"
        ).read_text(encoding="utf-8")
        # handle_video_upload (F.video | F.document message input) moved to its
        # own router (Phase 6, wave F); distinct from video_upload.py which
        # owns the vu: callbacks.
        self.video_upload_input_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "video_upload_input.py"
        ).read_text(encoding="utf-8")
        # Plain text catch-all moved to its own router (Phase 6, wave I).
        self.plain_text_router_source = (
            PROJECT_ROOT / "channels" / "telegram" / "routers" / "plain_text.py"
        ).read_text(encoding="utf-8")
        # Robokassa URL/webhook logic moved to billing/robokassa.py; flow_bot
        # keeps compatibility wrappers and web-server registration.
        self.robokassa_source = (
            PROJECT_ROOT / "billing" / "robokassa.py"
        ).read_text(encoding="utf-8")
        # Profile/gallery/history/support renderers moved to screens.py.
        self.screens_source = (
            PROJECT_ROOT / "channels" / "telegram" / "screens.py"
        ).read_text(encoding="utf-8")

    def test_menu_and_wizard_handlers_present(self) -> None:
        self.assertIn('F.data.startswith("m:")', self.menu_router_source)  # menu router
        self.assertIn("async def on_menu_action", self.menu_router_source)
        self.assertIn('F.data.startswith("w:")', self.wizard_router_source)  # wizard router
        self.assertIn("async def on_wizard_action", self.wizard_router_source)
        self.assertIn('data == "w:go"', self.wizard_router_source)
        self.assertIn('st["await"] = "prompt"', self.wizard_router_source)
        self.assertIn("def wizard_kb", self.kb_source)   # single-screen count+format

    def test_main_menu_has_animate_under_video_with_source_price(self) -> None:
        # main_menu_kb moved to channels/telegram/keyboards.py (Phase 5).
        kb_src = (PROJECT_ROOT / "channels" / "telegram" / "keyboards.py").read_text(encoding="utf-8")
        start = kb_src.index("def main_menu_kb")
        end = kb_src.index("def topup_method_kb", start)
        block = kb_src[start:end]
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
        start = self.kb_source.index("def wizard_kb")
        end = self.kb_source.index("def edit_settings_kb")
        block = self.kb_source[start:end]
        self.assertIn('"w:cnt:1"', block)
        # Format rows from shared helper; model is now a single toggle button.
        self.assertIn('_fmt_rows(fmt, "w:fmt")', block)
        self.assertIn('_imodel_toggle_btn(imodel, "w:imodel")', block)
        self.assertIn('"w:go"', block)

    def test_fmt_rows_cover_five_formats(self) -> None:
        start = self.kb_source.index("def _fmt_rows")
        block = self.kb_source[start:start + 700]
        for code in ("land", "f43", "sq", "f34", "port"):
            self.assertIn(f'"{code}"', block)
        self.assertIn('f"{prefix}:{code}"', block)

    def test_edit_settings_kb_offers_format_and_model(self) -> None:
        start = self.kb_source.index("def edit_settings_kb")
        block = self.kb_source[start:start + 500]
        self.assertIn('_fmt_rows(fmt, "es:fmt")', block)
        self.assertIn('_imodel_toggle_btn(imodel, "es:imodel")', block)

    def test_edit_confirm_kb_keeps_format_and_model_toggles(self) -> None:
        start = self.kb_source.index("def edit_confirm_kb")
        block = self.kb_source[start:start + 1200]
        self.assertIn("def edit_confirm_kb(fmt: str, imodel: str", block)
        self.assertIn('_fmt_rows(fmt, "es:fmt")', block)
        self.assertIn('_imodel_toggle_btn(imodel, "es:imodel")', block)
        self.assertIn('callback_data="es:apply"', block)

    def test_photo_reference_in_create_is_priced_as_generation(self) -> None:
        """Фото-референс из «Создать картинку» = тариф генерации (10/15), не правки.

        edit_confirm_kb(as_generation=True) должен показывать цену генерации, а
        обычная правка — цену правки; генерация дешевле.
        """
        import flow_bot as fb
        import flow_core as fc
        model = fb.DEFAULT_IMAGE_MODEL

        def apply_label(kb):
            for row in kb.inline_keyboard:
                for b in row:
                    if b.callback_data == "es:apply":
                        return b.text
            return ""

        edit_lbl = apply_label(fb.edit_confirm_kb("land", model))
        gen_lbl = apply_label(fb.edit_confirm_kb("land", model, as_generation=True))
        extra = fc.image_model_extra(model)
        self.assertIn(str(fc.action_price("edit") + extra), edit_lbl)
        self.assertIn(str(fc.price_gen(1) + extra), gen_lbl)
        self.assertLess(fc.price_gen(1), fc.action_price("edit"))
        # pr:img и инлайн «Создать картинку» помечают фото как генерацию.
        self.assertIn("as_generation=True", self.photo_input_router_source)
        self.assertIn('price_action="gen" if st.get("edit_as_gen") else "edit"', self.edit_settings_router_source)

    def test_edit_settings_prefix_does_not_collide_with_edit_button(self) -> None:
        # The image "Изменить" button uses the "edit:" callback prefix; the edit
        # settings picker must use "es:" so its handler never hijacks it.
        self.assertIn('F.data.startswith("es:")', self.edit_settings_router_source)
        self.assertFalse("edit:".startswith("es:"))  # the actual guarantee
        self.assertNotIn('startswith("e:")', self.edit_settings_router_source)  # no over-broad filter

    def test_persistent_reply_keyboard_present(self) -> None:
        self.assertIn("def reply_menu_kb", self.kb_source)
        self.assertIn("ReplyKeyboardMarkup", self.kb_source)
        self.assertIn("is_persistent=True", self.kb_source)
        start = self.kb_source.index("def reply_menu_kb")
        end = self.kb_source.index("# --- Marketplace", start)
        block = self.kb_source[start:end]
        # Reply-button taps are handled as plain text before prompt routing.
        self.assertIn('text == L("kb_gen")', self.plain_text_router_source)
        self.assertIn("_is_balance_reply_text(text)", self.plain_text_router_source)
        self.assertIn("def _balance_reply_label", self.kb_source)
        self.assertIn("balance_fn=credit_store.balance", self.source)
        self.assertIn('B(text=L("kb_gen"))', block)
        self.assertIn('B(text=L("kb_vid"))', block)
        self.assertIn('B(text=L("kb_menu"))', block)
        self.assertIn("B(text=_balance_reply_label(user_id, balance_fn=balance_fn))", block)
        for key in ("ideas", "myphoto", "invite", "help"):
            self.assertNotIn(f'B(text=L("{key}"))', block)
        for key in ("ideas", "myphoto", "invite", "help"):
            self.assertIn(f'text == L("{key}")', self.plain_text_router_source)

    def test_reply_menu_keyboard_uses_injected_balance_and_seller_mode(self) -> None:
        from channels.telegram import keyboards as kb

        balance_label = flow_copy.label("kb_balance")
        consumer = kb.reply_menu_kb(42, balance_fn=lambda user_id: 17, is_seller=False)
        consumer_rows = [[button.text for button in row] for row in consumer.keyboard]
        self.assertEqual(consumer_rows[0], [flow_copy.label("kb_gen"), flow_copy.label("kb_vid")])
        self.assertEqual(consumer_rows[1], [flow_copy.label("kb_menu"), f"{balance_label} · 17кр"])

        seller = kb.reply_menu_kb(42, balance_fn=lambda user_id: 23, is_seller=True)
        seller_rows = [[button.text for button in row] for row in seller.keyboard]
        self.assertEqual(seller_rows, [[flow_copy.label("kb_menu"), f"{balance_label} · 23кр"]])

    def test_public_help_ideas_referral_commands_wired(self) -> None:
        # /help and /referral handlers live in the extracted commands router;
        # /ideas moved to the generation-commands router (Phase 6), its
        # renderer (_show_ideas_root) stays defined in flow_bot.
        for command in ('Command("help")', 'Command("referral", "ref")'):
            self.assertIn(command, self.commands_router_source)
        self.assertIn('Command("ideas")', self.generation_commands_router_source)
        self.assertIn("async def _show_help_screen", self.source)
        self.assertIn("async def _show_referral_screen", self.source)
        self.assertIn("_show_ideas_root(message, user_id=user_id, edit=False)", self.plain_text_router_source)
        for command in ('command="ideas"', 'command="help"', 'command="referral"'):
            self.assertIn(command, self.source)

    def test_public_commands_router_wired_with_injected_renderers(self) -> None:
        # The router is included on dp with flow_bot renderers injected.
        self.assertIn("dp.include_router(", self.source)
        self.assertIn("tg_commands_router.create_router(", self.source)
        for needle in (
            "show_main_menu=show_main_menu",
            "show_help_screen=_show_help_screen",
            "show_referral_screen=_show_referral_screen",
            "show_balance=show_balance",
        ):
            self.assertIn(needle, self.source)
        for command in ('Command("menu")', 'Command("balance")'):
            self.assertIn(command, self.commands_router_source)

    def test_start_resets_stale_generation_state(self) -> None:
        self.assertIn("tg_start_router.create_router(", self.source)
        self.assertIn("cmd_start = tg_start_router.create_handler", self.source)
        start = self.start_router_source.index("async def cmd_start")
        block = self.start_router_source[start:start + 1200]
        self.assertIn("_reset_image_flow(user_id)", block)
        self.assertIn("_vid_clear(user_id)", block)

    def test_robokassa_sbp_topup_wired(self) -> None:
        for needle in (
            "ROBOKASSA_HASH_ALGO",
            "ROBOKASSA_INC_CURR_LABEL",
            "async def robokassa_result",
            "await _start_robokassa_web_server()",
        ):
            self.assertIn(needle, self.source, needle)
        for needle in (
            "payment_signature(",
            "result_signature(",
            'provider="robokassa"',
            "def register_routes(",
        ):
            self.assertIn(needle, self.robokassa_source, needle)
        self.assertIn("_register_robokassa_routes(app)", self.source)
        self.assertNotIn("flow_bot", self.robokassa_source)
        self.assertIn('data.startswith("m:robo:")', self.menu_router_source)
        self.assertIn('callback_data=f"m:robo:{pid}"', self.kb_source)

    def test_profile_screens_moved_to_telegram_screens_module(self) -> None:
        self.assertIn("from channels.telegram import screens as tg_screens", self.source)
        self.assertIn("tg_screens.ProfileScreensDeps(", self.source)
        for name in (
            "async def show_gallery",
            "async def show_prompt_history",
            "async def show_support_menu",
            "async def show_profile_screen",
            "async def show_my_tickets",
        ):
            self.assertIn(name, self.screens_source, name)
        self.assertIn("seller_history_text=_seller_history_text", self.source)
        self.assertNotIn("flow_bot", self.screens_source)

    def test_public_screens_moved_to_telegram_screens_module(self) -> None:
        self.assertIn("tg_screens.PublicScreensDeps(", self.source)
        for name in (
            "async def show_referral_screen",
            "async def show_help_screen",
            "async def show_main_menu",
            "async def show_balance",
        ):
            self.assertIn(name, self.screens_source, name)
        for needle in (
            "referral_link=_referral_link",
            "invite_button=_invite_button",
            "reply_menu_kb=reply_menu_kb",
        ):
            self.assertIn(needle, self.source)
        self.assertNotIn("flow_bot", self.screens_source)

    def test_image_wizard_screens_moved_to_telegram_screens_module(self) -> None:
        self.assertIn("tg_screens.ImageWizardScreensDeps(", self.source)
        for name in (
            "async def show_edit_confirm",
            "def wizard_text",
            "async def show_wizard",
            "def prompt_picker_text",
            "async def show_prompt_picker",
        ):
            self.assertIn(name, self.screens_source, name)
        for needle in (
            "workspace=_ws",
            "edit_or_answer=_edit_or_answer",
            "quick_ideas=tuple(_QUICK_IDEAS)",
            "default_image_model=DEFAULT_IMAGE_MODEL",
        ):
            self.assertIn(needle, self.source)
        self.assertNotIn("flow_bot", self.screens_source)

    def test_image_wizard_screens_use_injected_state(self) -> None:
        from channels.telegram import screens as tg_screens

        state = {
            7: {
                "pending_prompt": "<cat>&",
                "edit_instruction": "<fix>&",
                "edit_as_gen": True,
            }
        }
        events = []

        class CreditStore:
            def balance(self, user_id):
                return 99

        class Message:
            async def answer(self, text, reply_markup=None, parse_mode=None):
                events.append(("answer", text, reply_markup, parse_mode))
                return SimpleNamespace(message_id=707)

        async def edit_or_answer(*args, **kwargs):
            events.append(("edit_or_answer", args, kwargs))

        def log_event(*args, **kwargs):
            events.append(("log_event", args, kwargs))

        deps = tg_screens.ImageWizardScreensDeps(
            workspace=lambda user_id: state[user_id],
            credit_store=CreditStore(),
            log_event=log_event,
            edit_or_answer=edit_or_answer,
            quick_ideas=("first <idea>", "second", "third", "fourth"),
            default_count=1,
            default_fmt="land",
            default_image_model="nb2",
            fmt_names={"land": "16:9"},
        )

        text = tg_screens.wizard_text(7, deps=deps)
        self.assertIn("&lt;cat&gt;&amp;", text)
        self.assertIn("99", text)

        asyncio.run(tg_screens.show_wizard(Message(), user_id=7, edit=True, deps=deps))
        self.assertEqual(state[7]["step"], "wizard")
        self.assertEqual(events[-1][0], "edit_or_answer")
        self.assertEqual(events[-1][2].get("parse_mode"), "HTML")

        asyncio.run(tg_screens.show_edit_confirm(Message(), user_id=7, edit=False, deps=deps))
        self.assertEqual(events[-1][0], "answer")
        self.assertIn("&lt;fix&gt;&amp;", events[-1][1])
        self.assertEqual(events[-1][3], "HTML")

        state[7].pop("ideas_pool", None)
        asyncio.run(tg_screens.show_prompt_picker(Message(), user_id=7, edit=False, deps=deps))
        self.assertEqual(state[7]["step"], "prompt_picker")
        self.assertIn("ideas_pool", state[7])
        self.assertEqual(state[7]["picker_msg_id"], 707)
        self.assertTrue(any(event[0] == "log_event" for event in events))

    def test_video_reference_screens_moved_to_telegram_screens_module(self) -> None:
        self.assertIn("tg_screens.VideoReferenceScreensDeps(", self.source)
        for name in (
            "async def show_video_ingredients",
            "async def show_video_frames",
        ):
            self.assertIn(name, self.screens_source, name)
        for needle in (
            "wizard_state=wizard_state",
            "credit_store=credit_store",
            "vid_edit=_vid_edit",
            "vid_ref_default_model=VID_REF_DEFAULT_MODEL",
            "vid_frames_default_model=VID_FRAMES_DEFAULT_MODEL",
        ):
            self.assertIn(needle, self.source)
        self.assertNotIn("flow_bot", self.screens_source)

    def test_video_settings_screens_moved_to_telegram_screens_module(self) -> None:
        self.assertIn("tg_screens.VideoSettingsScreensDeps(", self.source)
        for name in (
            "def video_settings_text",
            "async def show_video_family",
            "async def show_video_variant",
            "async def show_video_settings",
        ):
            self.assertIn(name, self.screens_source, name)
        for needle in (
            "vid_clear=_vid_clear",
            "aspect_to_vfmt=_aspect_to_vfmt",
            "vid_edit=_vid_edit",
            "vid_default_count=VID_DEFAULT_COUNT",
        ):
            self.assertIn(needle, self.source)
        self.assertNotIn("flow_bot", self.screens_source)

    def test_video_settings_screens_use_injected_state(self) -> None:
        from channels.telegram import screens as tg_screens

        state = {
            7: {
                "vlast": {"aspect": "portrait", "count": 2},
                "vretry": {"prompt": "old"},
            }
        }
        events = []

        class CreditStore:
            def balance(self, user_id):
                return 42

        class Message:
            async def answer(self, text, reply_markup=None, parse_mode=None):
                events.append(("answer", text, reply_markup, parse_mode))
                return SimpleNamespace(message_id=7001)

        def vid_clear(user_id):
            st = state[user_id]
            keep = {k: st.get(k) for k in ("vlast", "vretry") if k in st}
            st.clear()
            st.update(keep)

        async def vid_edit(*args, **kwargs):
            events.append(("edit", args, kwargs))

        deps = tg_screens.VideoSettingsScreensDeps(
            wizard_state=state,
            credit_store=CreditStore(),
            vid_clear=vid_clear,
            aspect_to_vfmt=lambda aspect: {"landscape": "land", "portrait": "port"}.get(aspect, "land"),
            vid_edit=vid_edit,
            vid_default_fmt="land",
            vid_default_count=1,
            vid_fmt_names={"land": "16:9", "port": "9:16"},
        )

        asyncio.run(tg_screens.show_video_family(Message(), user_id=7, edit=False, deps=deps))
        self.assertEqual(state[7]["vstep"], "vfam")
        self.assertEqual(state[7]["vfmt"], "port")
        self.assertEqual(state[7]["vcount"], 2)
        self.assertNotIn("vretry", state[7])
        self.assertEqual(state[7]["vmsg_id"], 7001)

        state[7]["vfamily"] = "veo"
        asyncio.run(tg_screens.show_video_variant(Message(), user_id=7, deps=deps))
        self.assertEqual(state[7]["vstep"], "vmodel")
        self.assertEqual(events[-1][0], "edit")

        state[7]["vmodel"] = "veo-lite"
        asyncio.run(tg_screens.show_video_settings(Message(), user_id=7, deps=deps))
        self.assertEqual(state[7]["vstep"], "vsettings")
        self.assertEqual(events[-1][2].get("parse_mode"), "HTML")

    def test_new_video_wizard_screens_moved_to_telegram_screens_module(self) -> None:
        self.assertIn("tg_screens.NewVideoWizardScreensDeps(", self.source)
        for name in (
            "def new_video_wizard_text",
            "def new_video_wizard_kb",
            "async def show_video_prompt_input",
            "async def show_new_video_wizard",
        ):
            self.assertIn(name, self.screens_source, name)
        for needle in (
            "vid_clear=_vid_clear",
            "nwiz_engine=_nwiz_engine",
            "nwiz_model=_nwiz_model",
            "nwiz_price=_nwiz_price",
            "vid_omni_durations=tuple(_VID_OMNI_DURATIONS)",
        ):
            self.assertIn(needle, self.source)
        self.assertIn("return tg_screens.new_video_wizard_text", self.source)
        self.assertIn("return tg_screens.new_video_wizard_kb", self.source)
        self.assertNotIn("flow_bot", self.screens_source)

    def test_new_video_wizard_screens_use_injected_state(self) -> None:
        from channels.telegram import screens as tg_screens

        state = {
            7: {
                "vprompt": "<move>&",
                "vfmt": "land",
                "vdur": 4,
                "vstyle": "cine",
            }
        }
        events = []

        class CreditStore:
            def balance(self, user_id):
                return 55

        class Message:
            async def answer(self, text, reply_markup=None, parse_mode=None):
                events.append(("answer", text, reply_markup, parse_mode))
                return SimpleNamespace(message_id=808)

        def vid_clear(user_id):
            events.append(("clear", dict(state[user_id])))
            for key in list(state[user_id]):
                if key.startswith("v"):
                    state[user_id].pop(key, None)

        async def vid_edit(*args, **kwargs):
            events.append(("edit", args, kwargs))

        def nwiz_engine(st):
            return st.get("vengine") or "omni"

        def nwiz_model(st):
            return "veo-lite" if nwiz_engine(st) == "veo" else "omni-flash-4s"

        def nwiz_price(st):
            return 12 if st.get("vphoto") else 7

        deps = tg_screens.NewVideoWizardScreensDeps(
            wizard_state=state,
            credit_store=CreditStore(),
            vid_clear=vid_clear,
            vid_edit=vid_edit,
            nwiz_engine=nwiz_engine,
            nwiz_model=nwiz_model,
            nwiz_price=nwiz_price,
            vid_default_fmt="land",
            vid_fmt_names={"land": "16:9", "port": "9:16"},
            vid_styles={"": ("Никакой", ""), "cine": ("Кино", " cinematic")},
            vid_omni_durations=(4, 6),
            vid_veo_quality_cycle=("lite", "fast", "quality"),
            vid_veo_quality_names={"lite": "Lite", "fast": "Fast", "quality": "Quality"},
        )

        text = tg_screens.new_video_wizard_text(7, deps=deps)
        self.assertIn("&lt;move&gt;&amp;", text)
        self.assertIn("Кино", text)
        self.assertIn("55", text)

        kb = tg_screens.new_video_wizard_kb(7, deps=deps)
        datas = [b.callback_data for row in kb.inline_keyboard for b in row]
        self.assertIn("v:neng:omni", datas)
        self.assertIn("v:ndur:4", datas)
        self.assertIn("ag:vimprove", datas)

        asyncio.run(tg_screens.show_video_prompt_input(
            Message(), user_id=7, edit=True, deps=deps, vfmt="port", vstyle="cine"
        ))
        self.assertEqual(events[0][0], "clear")
        self.assertEqual(state[7]["vstep"], "vprompt_input")
        self.assertEqual(state[7]["vmode"], "text")
        self.assertEqual(state[7]["vfmt"], "port")
        self.assertEqual(state[7]["vstyle"], "cine")
        self.assertEqual(events[-1][0], "edit")
        self.assertEqual(events[-1][2].get("parse_mode"), "HTML")

        state[7].update({
            "vprompt": "clip",
            "vphoto": {"media_id": "m"},
            "vengine": "veo",
        })
        asyncio.run(tg_screens.show_new_video_wizard(
            Message(), user_id=7, edit=False, deps=deps
        ))
        self.assertEqual(state[7]["vstep"], "vnewwiz")
        self.assertIsNone(state[7]["vawait"])
        self.assertEqual(state[7]["vmodel"], "veo-lite")
        self.assertEqual(state[7]["vmode"], "ingredients")
        self.assertEqual(state[7]["vmsg_id"], 808)
        datas = [b.callback_data for row in events[-1][2].inline_keyboard for b in row]
        self.assertIn("v:nremove_photo", datas)
        self.assertIn("v:nqual:fast", datas)

    def test_marketplace_screens_moved_to_telegram_screens_module(self) -> None:
        self.assertIn("tg_screens.MarketplaceScreensDeps(", self.source)
        for name in (
            "def mp_confirm_screen",
            "def mp_sku_projects",
            "def mp_sku_projects_text",
            "def mp_sku_projects_kb",
            "async def show_sku_projects",
        ):
            self.assertIn(name, self.screens_source, name)
        for needle in (
            "workspace=_ws",
            "credit_store=credit_store",
            "brand_kit=_mp_brand_kit",
            "niche_label=_mp_niche_label",
            "stamp_message=_mp_stamp_message",
        ):
            self.assertIn(needle, self.source)
        self.assertNotIn("flow_bot", self.screens_source)

    def test_admin_grant_restricted(self) -> None:
        # /grant moved to the admin_credits router (Phase 6); the admin gate
        # is preserved bit-exact via injected deps.admin_ids.
        self.assertIn('@router.message(Command("grant"))', self.admin_credits_router_source)
        self.assertIn("async def cmd_grant", self.admin_credits_router_source)
        self.assertIn("admin_ids=ADMIN_IDS", self.source)  # wiring injects the real set
        self.assertIn("message.from_user.id not in deps.admin_ids", self.admin_credits_router_source)
        self.assertIn("deps.credit_store.add(target, amount)", self.admin_credits_router_source)

    def test_status_diagnostics_restricted_to_admins(self) -> None:
        self.assertIn('@router.message(Command("status"))', self.admin_status_router_source)
        self.assertIn("async def cmd_status", self.admin_status_router_source)
        self.assertIn("message.from_user.id not in deps.admin_ids", self.admin_status_router_source)
        self.assertIn('flow_copy.msg("admin_denied")', self.admin_status_router_source)
        for needle in (
            "admin_ids=ADMIN_IDS",
            "keeper=keeper",
            "account_pool=account_pool",
            "metrics=metrics",
            "bearer_timestamp=lambda: keeper._bearer_ts",
        ):
            self.assertIn(needle, self.source)

        help_start = self.source.index("_HELP_SECTIONS")
        help_end = self.source.index("def _render_admin_help", help_start)
        help_block = self.source[help_start:help_end]
        self.assertGreater(
            help_block.index('("/status"'),
            help_block.index('("/grant'),
        )

    def test_session_keeper_has_locked_shutdown_close(self) -> None:
        self.source = _PR2A_PROVIDER_SOURCE + "\n" + self.source
        start = self.source.index("class SessionKeeper:")
        end = self.source.index("async def _start_locked", start)
        block = self.source[start:end]
        self.assertIn("async def close(self):", block)
        self.assertIn("async with self._lock:", block)
        self.assertIn("await self._close_browser_locked()", block)

    def test_g_credits_lookup_does_not_refresh_bearer(self) -> None:
        self.source = _PR2A_PROVIDER_SOURCE + "\n" + self.source
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
        # flow_bot wires the gate and callers flag success; the charge/refund
        # rule itself lives in billing/credit_gate.py (PR-7a).
        self.assertIn("credit_gate", self.source)
        self.assertIn("charge.ok =", self.source)
        billing_src = (PROJECT_ROOT / "billing" / "credit_gate.py").read_text(encoding="utf-8")
        self.assertIn("class NotEnoughCredits", billing_src)
        self.assertIn("store.refund", billing_src)
        self.assertIn("if not charge.ok:", billing_src)

    def test_stars_payment_wired(self) -> None:
        self.assertIn("currency=\"XTR\"", self.source)
        self.assertIn("@router.pre_checkout_query()", self.payments_router_source)
        self.assertIn("F.successful_payment", self.payments_router_source)
        self.assertIn("deps.credit_store.add", self.payments_router_source)
        self.assertIn("tg_payments_router.create_router(", self.source)
        self.assertIn("credit_store=credit_store", self.source)
        self.assertIn("payment_store=payment_store", self.source)

    def test_paid_upscale_and_free_download_distinct(self) -> None:
        # up2x = prompt enhance; realup = true service upscale; download = free file.
        self.assertIn("async def _enhance_and_send", self.source)
        self.assertIn('action="up2x"', self.source)
        self.assertIn("async def _real_upscale_and_send", self.source)
        self.assertIn("async def _send_original_file", self.source)

    def test_real_upscale_wired(self) -> None:
        self.source = _PR2A_PROVIDER_SOURCE + "\n" + self.source
        # The real upscale uses the verified flow/upsampleImage contract (sync POST
        # returning base64 encodedImage), NOT a prompt-based image-to-image enhance.
        self.assertIn("async def upsample_image", self.source)
        self.assertIn("result = await _client_for_acc(ref.account_id).upsample_image", self.source)
        self.assertIn("build_upsample_payload", self.source)
        self.assertIn("parse_upsample_response", self.source)
        # realup button lives under each generated image (restored by operator
        # request) and is wired via action_callback_data / action == "realup".
        self.assertIn('elif action == "realup"', self.image_action_router_source)
        # realup must NOT silently fall back to the prompt enhance anymore.
        real_start = self.source.index("async def _real_upscale_and_send")
        real_block = self.source[real_start:real_start + 900]
        self.assertNotIn("_enhance_and_send", real_block)

    def test_image_keyboard_has_no_mix_button_and_credit_prices(self) -> None:
        start = self.kb_source.index("def _image_keyboard")
        block = self.kb_source[start:start + 1300]
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
        self.assertIn('SELECT_STYLE = "success"', (PROJECT_ROOT / "config" / "video.py").read_text(encoding="utf-8"))
        self.assertIn("def _sel_btn", self.kb_source)
        helper_start = self.kb_source.index("def _sel_btn")
        helper = self.kb_source[helper_start:helper_start + 900]
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
        self.assertIn('· {edit_price} кр', self.kb_source)
        self.assertIn('· {next_price} кр', self.kb_source)
        self.assertIn("{price} кр", self.kb_source)

    def test_stale_photo_edit_cleared_on_navigation(self) -> None:
        # Regression: photo upload sets pending_edits; navigating to generate/menu
        # must clear it so the next prompt is NOT applied as an edit of that photo.
        # _reset_image_flow moved to storage/session_state.py as reset_image_flow (Phase 11).
        ss = (PROJECT_ROOT / "storage" / "session_state.py").read_text(encoding="utf-8")
        self.assertIn("def reset_image_flow", ss)
        reset_start = ss.index("def reset_image_flow")
        self.assertIn("pending_edits.pop(user_id, None)", ss[reset_start:reset_start + 600])
        # The dangerous unconditional "old path" edit fallback is gone.
        self.assertNotIn("Старый путь (на случай pending_edits", self.source)
        self.assertIn("_reset_image_flow(user_id)", self.source + "\n" + self.start_router_source)

    def test_frames_and_ingredients_have_format_and_count(self) -> None:
        # Frames/Ingredients screens reuse the shared format+count picker rows.
        self.assertIn("def _vid_fmt_count_rows", self.kb_source)
        self.assertIn("def frames_kb(has_start: bool, has_end: bool, vfmt: str, vcount: int, vmodel", self.kb_source)
        self.assertIn("def ingredients_kb(", self.kb_source)
        # Format/count callbacks re-render the active video screen by mode.
        self.assertIn("def _vid_rerender_settings", self.source)
        # dispatch to _vid_rerender_settings lives in the video router (Phase 6).
        self.assertIn("await deps.vid_rerender_settings(msg, user_id=user_id)", self.video_router_source)

    def test_model_picker_in_frames_and_ingredients(self) -> None:
        # Ingredients supports Omni + Veo; Frames stays Veo-only.
        self.assertIn("def _vid_model_row", self.kb_source)
        _vid_cfg = (PROJECT_ROOT / "config" / "video.py").read_text(encoding="utf-8")
        self.assertIn('VID_REF_DEFAULT_MODEL = "omni-flash-4s"', _vid_cfg)
        self.assertIn("VID_REF_VARIANTS = tuple(VIDEO_MODELS.keys())", _vid_cfg)
        self.assertIn('VID_FRAMES_VARIANTS = ("veo-lite", "veo-fast", "veo-quality")', _vid_cfg)
        # v:vmod: dispatch lives in the video router (Phase 6).
        self.assertIn('data.startswith("v:vmod:")', self.video_router_source)

    def test_ingredients_generation_enabled(self) -> None:
        # Ingredients now generates (reference-to-video), no longer fail-closed.
        self.assertIn("VIDEO_REFERENCE_ENDPOINT", self.source)
        self.assertIn("is_reference", self.source)
        # done button leads to prompt/generation, not vid_gen_blocked. v:ing:done
        # dispatch lives in the video router (Phase 6).
        done = self.video_router_source.index('if data == "v:ing:done":')
        block = self.video_router_source[done:done + 1500]
        self.assertNotIn("vid_gen_blocked", block)
        self.assertIn("deps.video_generate_and_send", block)

    def test_ingredients_minimum_is_one_photo(self) -> None:
        # Ingredients works from a single photo now (no 2-photo gate).
        ing = self.kb_source.index("def ingredients_kb")
        block = self.kb_source[ing:ing + 400]
        self.assertIn("if n >= 1:", block)
        self.assertIn('if len(photos) < 1:', self.video_router_source)

    def test_album_and_caption_support(self) -> None:
        # Grouped photos (album) → first=start, second=end; caption → prompt.
        self.assertIn("async def _handle_album_photos", self.source)
        self.assertIn("message.media_group_id", self.photo_input_router_source)
        self.assertIn('st["vfrm_start"] = sources[0]', self.source)
        self.assertIn("vcaption_prompt", self.source)

    def test_frames_next_generates_from_saved_caption(self) -> None:
        # v:frm:go / v:frm:clear dispatch lives in the video router (Phase 6).
        start = self.video_router_source.index('if data == "v:frm:go":')
        end = self.video_router_source.index('if data == "v:frm:clear":')
        block = self.video_router_source[start:end]
        self.assertIn('caption = st.pop("vcaption_prompt", None)', block)
        self.assertIn("if caption:", block)
        self.assertIn("deps.video_generate_and_send(msg, caption, user_id=user_id)", block)
        self.assertIn('st["vawait"] = "vprompt"', block)
        self.assertIn("vid_frm_ask_prompt", block)
        self.assertNotIn("vid_frm_ask_prompt_with_caption", block)
        self.assertIn("vid_frm_ready_next", self.screens_source)

    def test_video_settings_plain_text_runs_video_before_image_fallback(self) -> None:
        # _video_plain_text_ready moved to config.video (Phase 11).
        config_video_source = (PROJECT_ROOT / "config" / "video.py").read_text(encoding="utf-8")
        self.assertIn("def video_plain_text_ready", config_video_source)
        source = self.plain_text_router_source
        start = source.index("async def handle_plain_text")
        video_branch = source.index("if _video_plain_text_ready(st):", start)
        image_fallback = source.index('st["pending_prompt"] = text', start)
        awaiting_image_prompt = source.index('awaiting = st.get("await")', start)
        self.assertLess(video_branch, awaiting_image_prompt)
        self.assertLess(video_branch, image_fallback)
        block = source[video_branch:video_branch + 250]
        self.assertIn("_video_generate_and_send(message, text, user_id=user_id)", block)
        self.assertIn("return", block)

    def test_myphoto_waiting_text_stays_in_photo_upload_state(self) -> None:
        source = self.plain_text_router_source
        start = source.index("async def handle_plain_text")
        photo_guard = source.index('if awaiting == "photo":', start)
        image_fallback = source.index('st["pending_prompt"] = text', start)
        self.assertLess(photo_guard, image_fallback)
        block = source[photo_guard:photo_guard + 180]
        self.assertIn('flow_copy.msg("ask_photo")', block)
        self.assertIn("return", block)

    def test_marketplace_photo_waiting_text_stays_in_photo_upload_state(self) -> None:
        source = self.plain_text_router_source
        start = source.index("async def handle_plain_text")
        mp_photo_guard = source.index('if awaiting == "mp_photo":', start)
        image_fallback = source.index('st["pending_prompt"] = text', start)
        self.assertLess(mp_photo_guard, image_fallback)
        block = source[mp_photo_guard:mp_photo_guard + 350]
        self.assertIn("_mp_photo_request_text", block)
        self.assertIn("return", block)

    def test_marketplace_series_waiting_text_stays_in_photo_upload_state(self) -> None:
        source = self.plain_text_router_source
        start = source.index("async def handle_plain_text")
        mp_series_guard = source.index('if awaiting == "mp_series_photo":', start)
        image_fallback = source.index('st["pending_prompt"] = text', start)
        self.assertLess(mp_series_guard, image_fallback)
        block = source[mp_series_guard:mp_series_guard + 520]
        self.assertIn("_mp_series_request_text", block)
        self.assertIn("return", block)

    def test_marketplace_sku_name_text_saves_before_image_fallback(self) -> None:
        source = self.plain_text_router_source
        start = source.index("async def handle_plain_text")
        sku_guard = source.index('if awaiting == "mp_sku_name":', start)
        image_fallback = source.index('st["pending_prompt"] = text', start)
        self.assertLess(sku_guard, image_fallback)
        block = source[sku_guard:sku_guard + 450]
        self.assertIn("_save_pending_sku_item", block)
        self.assertIn("return", block)

    def test_marketplace_brandkit_text_saves_before_image_fallback(self) -> None:
        source = self.plain_text_router_source
        start = source.index("async def handle_plain_text")
        brand_guard = source.index('if awaiting == "mp_brandkit":', start)
        image_fallback = source.index('st["pending_prompt"] = text', start)
        self.assertLess(brand_guard, image_fallback)
        block = source[brand_guard:brand_guard + 650]
        self.assertIn("metrics.save_seller_profile", block)
        self.assertIn('"mp_brandkit_saved"', block)
        self.assertIn("return", block)

    def test_seller_result_keyboard_is_gated_to_seller_mode(self) -> None:
        # send_one_image moved to channels.telegram.image_delivery (Phase 11).
        source = (PROJECT_ROOT / "channels" / "telegram" / "image_delivery.py").read_text(encoding="utf-8")
        start = source.index("async def send_one_image")
        block = source[start:start + 1200]
        self.assertIn("is_seller()", block)
        self.assertIn("d.seller_image_keyboard(token)", block)
        self.assertIn("d.image_keyboard(token)", block)

    def test_support_brief_text_runs_before_image_fallback(self) -> None:
        source = self.plain_text_router_source
        start = source.index("async def handle_plain_text")
        support_guard = source.index('if st.get("support_await"):', start)
        image_fallback = source.index('st["pending_prompt"] = text', start)
        self.assertLess(support_guard, image_fallback)
        block = source[support_guard:support_guard + 2200]
        self.assertIn("metrics.create_ticket", block)
        self.assertIn("ticket_text", block)
        self.assertIn("return", block)

    def test_support_photo_does_not_upload_to_flow(self) -> None:
        start = self.photo_input_router_source.index("async def handle_photo")
        support_guard = self.photo_input_router_source.index('if st.get("support_await"):', start)
        first_upload = self.photo_input_router_source.index('flow_copy.msg("uploading_photo")', start)
        self.assertLess(support_guard, first_upload)
        block = self.photo_input_router_source[support_guard:support_guard + 320]
        self.assertIn("текстовый бриф", block)
        self.assertIn("return", block)

    def test_captioned_photo_without_mode_asks_image_or_video(self) -> None:
        self.assertIn("pending_photo_routes", self.source)
        self.assertIn('F.data.startswith("pr:")', self.photo_route_source)
        self.assertIn('"pr:img"', self.photo_route_source)
        self.assertIn('"pr:vid"', self.photo_route_source)
        start = self.photo_input_router_source.index("async def handle_photo")
        block = self.photo_input_router_source[start:start + 13000]
        self.assertIn("await deps.offer_photo_route_choice(message, user_id=user_id, caption=caption)", block)
        self.assertIn("await deps.prepare_photo_edit_from_file_id(", self.photo_route_source)
        self.assertIn("_prepare_photo_video_from_file_id(", self.source)
        # The old fallback edited immediately when a caption was attached.
        self.assertNotIn("await _edit_and_send(message, ref, caption", block)

    def test_create_image_photo_caption_stops_at_edit_confirm(self) -> None:
        start = self.photo_input_router_source.index("async def handle_photo")
        block = self.photo_input_router_source[start:start + 4500]
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
        # Moved to config.video (Phase 11); body unchanged.
        source = (PROJECT_ROOT / "config" / "video.py").read_text(encoding="utf-8")
        start = source.index("def video_plain_text_ready")
        block = source[start:]
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
        # Photo-key set moved to product.ideas_hub (Phase 11); flow_bot imports it.
        self.assertIn("_IDEAS_PHOTO_KEYS", self.source)
        ideas_hub_source = (PROJECT_ROOT / "product" / "ideas_hub.py").read_text(encoding="utf-8")
        self.assertIn(
            '_IDEAS_PHOTO_KEYS = ("ideas_photo_file_id", "ideas_photo_caption", "ideas_extra_prompt")',
            ideas_hub_source,
        )
        # handle_photo перехватывает фото внутри любой ветки «Идей».
        self.assertIn('if st.get("tp_tpl") or "gp_step" in st or st.get("ideas_mode") in (', self.photo_input_router_source)
        for mode in ('"root"', '"templates"', '"guided"'):
            self.assertIn(mode, self.photo_input_router_source)
        self.assertIn("await deps.template_photo_received(message, user_id=user_id)", self.photo_input_router_source)
        receive = self.source[
            self.source.index("async def _template_photo_received"):
            self.source.index("async def _render_guided_step")
        ]
        self.assertIn('st["ideas_photo_file_id"] = message.photo[-1].file_id', receive)
        self.assertNotIn("upload_image(", receive)
        # При завершении шаблона с фото — идём в штатные экраны настроек image/video.
        start = self.source.index("async def _render_template_step")
        block = self.source[start:start + 3000]
        self.assertIn('ideas_photo_file_id = st.get("ideas_photo_file_id")', block)
        self.assertIn("await _prepare_photo_video_from_file_id(", block)
        self.assertIn("elif ideas_photo_file_id:", block)
        self.assertIn("await _prepare_photo_edit_from_file_id(", block)

    def test_guided_video_carries_format_and_style(self) -> None:
        # «Подбор по шагам» → видео: выбранный формат (9:16) и стиль должны
        # переноситься в видео-визард, а не сбрасываться на дефолт 16:9.
        # show_video_prompt_input применяет vfmt/vstyle ПОСЛЕ vid_clear.
        start = self.screens_source.index("async def show_video_prompt_input")
        block = self.screens_source[start:start + 900]
        self.assertIn("vfmt: str | None = None", block)
        self.assertIn("vstyle: str | None = None", block)
        clear_at = block.index("deps.vid_clear(user_id)")
        apply_at = block.index('st["vfmt"] = vfmt')
        self.assertLess(clear_at, apply_at)  # применяем после очистки
        # Guided-ветка передаёт формат и стиль.
        self.assertIn("gv_fmt = _guided_video_fmt(answers)", self.source)
        self.assertIn("image_fmt = _guided_image_fmt(answers)", self.source)
        self.assertIn("vfmt=gv_fmt", self.source)
        self.assertIn("vstyle=gv_style", self.source)

    def test_video_result_edit_and_extend_wiring(self) -> None:
        self.assertIn("def _video_can_edit", self.kb_source)
        self.assertIn("def _video_can_extend", self.kb_source)
        self.assertIn("ref.workflow_id", self.kb_source)
        self.assertIn('str(ref.model_id).startswith("veo-")', self.kb_source)
        self.assertIn('VIDEO_EXTEND_MODEL = "veo-lite"', (PROJECT_ROOT / "config" / "video.py").read_text(encoding="utf-8"))
        self.assertIn("not ref.prompt_edited", self.kb_source)
        self.assertIn('callback_data=f"v:edit:{vtoken}"', self.kb_source)
        self.assertIn('callback_data=f"v:extend:{vtoken}"', self.kb_source)
        # v:edit:/v:extend: dispatch lives in the video callback router (Phase 6).
        self.assertIn('if data.startswith("v:edit:")', self.video_router_source)
        self.assertIn('if data.startswith("v:extend:")', self.video_router_source)
        self.assertIn('st["vawait"] = "vedit_prompt"', self.source)
        self.assertIn('st["vawait"] = "vextend_prompt"', self.source)
        self.assertIn('if st.get("vawait") == "vedit_prompt":', self.plain_text_router_source)
        self.assertIn('if st.get("vawait") == "vextend_prompt":', self.plain_text_router_source)
        self.assertIn('unit_price_override=action_price("video_prompt_edit")', self.source)
        self.assertIn('video_operation="edit"', self.source)
        self.assertIn('video_operation="extend"', self.source)
        self.assertIn("prepare_video_extend_scene", self.source)

    # ── Phase 1 upgrades ───────────────────────────────────────────────
    def test_ingredients_and_frames_have_back_to_family(self) -> None:
        ing = self.kb_source[self.kb_source.index("def ingredients_kb"):][:900]
        frm = self.kb_source[self.kb_source.index("def frames_kb"):][:900]
        self.assertIn('"v:back:fam"', ing)
        self.assertIn('"v:back:fam"', frm)

    def test_family_picker_shows_min_prices(self) -> None:
        self.assertIn("def _vid_family_min_price", self.kb_source)
        block = self.kb_source[self.kb_source.index("def video_family_kb"):][:600]
        self.assertIn("от ", block)
        self.assertIn("кр", block)
        self.assertIn("_vid_family_min_price", block)

    def test_ingredients_done_label_is_dynamic(self) -> None:
        block = self.kb_source[self.kb_source.index("def ingredients_kb"):][:600]
        self.assertIn("has_caption", block)
        self.assertIn("vid_ing_done_ready", block)
        # screen shows the pending caption like frames mode does
        self.assertIn("vid_ing_ready_with_caption", self.screens_source)

    def test_video_reference_screens_use_injected_state_and_escape_caption(self) -> None:
        from channels.telegram import screens as tg_screens

        events = []

        class CreditStore:
            def balance(self, user_id):
                return 777

        class Message:
            async def answer(self, text, reply_markup=None, parse_mode=None):
                events.append(("answer", text, reply_markup, parse_mode))
                return SimpleNamespace(message_id=9001)

        async def vid_edit(*args, **kwargs):
            events.append(("edit", args, kwargs))

        deps = tg_screens.VideoReferenceScreensDeps(
            wizard_state={
                7: {
                    "ving_photos": [{"mediaId": "m1"}],
                    "vcaption_prompt": "<tag>&",
                }
            },
            credit_store=CreditStore(),
            vid_edit=vid_edit,
            vid_default_fmt="land",
            vid_default_count=1,
            vid_ref_default_model="omni-flash-4s",
            vid_frames_default_model="veo-lite",
            vid_fmt_names={"land": "16:9", "port": "9:16"},
        )

        asyncio.run(tg_screens.show_video_ingredients(Message(), user_id=7, deps=deps))
        st = deps.wizard_state[7]
        self.assertEqual(st["vstep"], "ving")
        self.assertEqual(st["vawait"], "ving_photo")
        self.assertEqual(st["vmsg_id"], 9001)
        self.assertEqual(events[-1][3], "HTML")
        self.assertIn("&lt;tag&gt;&amp;", events[-1][1])

        events.clear()
        deps.wizard_state[7] = {
            "vfrm_start": {"mediaId": "s1"},
            "vfrm_end": {"mediaId": "e1"},
            "vcaption_prompt": "<go>&",
        }
        asyncio.run(tg_screens.show_video_frames(Message(), user_id=7, deps=deps))
        st = deps.wizard_state[7]
        self.assertEqual(st["vstep"], "vfrm")
        self.assertIsNone(st["vawait"])
        self.assertEqual(st["vmsg_id"], 9001)
        self.assertEqual(events[-1][3], "HTML")
        self.assertIn("&lt;go&gt;&amp;", events[-1][1])

    def test_video_retry_rehydrates_from_snapshot(self) -> None:
        # The retry button must re-run the SAME request, not report "expired".
        self.assertIn('st["vretry"]', self.source)
        # v:retry / v:retrynew snapshot restore lives in the video router (Phase 6).
        self.assertIn('snap = st.get("vretry")', self.video_router_source)
        # _vid_clear moved to storage/session_state.py (Phase 11 core split).
        ss = (PROJECT_ROOT / "storage" / "session_state.py").read_text(encoding="utf-8")
        clear = ss[ss.index("def _vid_clear"):][:400]
        self.assertIn('"vretry"', clear)  # snapshot survives the finally-clear

    def test_moderation_block_offers_change_prompt_retry(self) -> None:
        # На модерации повтор того же промпта бессмыслен → отдельная кнопка
        # «Изменить промпт и снова» (v:retrynew), которая ждёт новый промпт и
        # генерит с сохранёнными фото/настройками. Прочие сбои — обычный v:retry.
        self.assertIn('if error_type == "danger_filter"', self.source)
        self.assertIn('_menu_button("vid_retry_edit", "v:retrynew")', self.source)
        # v:retrynew dispatch/state-set moved to the video router (Phase 6).
        self.assertIn('if data == "v:retrynew":', self.video_router_source)
        self.assertIn('st["vawait"] = "vretry_prompt"', self.video_router_source)
        self.assertIn('if st.get("vawait") == "vretry_prompt":', self.plain_text_router_source)
        self.assertIn("vid_retry_edit", (PROJECT_ROOT / "flow_copy.py").read_text(encoding="utf-8"))

    def test_moderation_fail_excluded_from_account_stats(self) -> None:
        # Модерация (кривой промпт) — не вина аккаунта: video_outcome (routing
        # score) и flow_jobs (admin per-account fail) НЕ пишутся при danger_filter.
        # Продуктовый video_failed и возврат кредитов остаются.
        start = self.source.index("async def _fail_retry")
        block = self.source[start:start + 2200]
        self.assertIn('content_moderation = error_type == "danger_filter"', block)
        # both account-attributing writes are guarded by `if not content_moderation:`
        self.assertEqual(block.count("if not content_moderation:"), 2)
        # the two guarded writes are the routing score and the per-account ledger
        om = block.index('"video_outcome"')
        fj = block.index("metrics.log_flow_job(")
        for pos in (om, fj):
            guard = block.rfind("if not content_moderation:", 0, pos)
            self.assertNotEqual(guard, -1)
        # product-level failure event stays unconditional
        self.assertIn('metrics.log_event("video_failed"', block)

    def test_video_403_refreshes_session_before_next_action(self) -> None:
        self.source = _PR2A_PROVIDER_SOURCE + "\n" + self.source
        start = self.source.index("async def generate_video")
        end = self.source.index("if not solved_any:", start)
        block = self.source[start:end]
        self.assertIn("refreshed_after_403 = False", block)
        self.assertIn("if gen_status == 403:", block)
        self.assertIn("await self.keeper._refresh_bearer()", block)
        self.assertIn("session = await self.keeper.get_session()", block)
        self.assertIn("headers = self._build_headers(session)", block)

    def test_video_403_does_not_auto_browser_fallback_in_production(self) -> None:
        self.source = _PR2A_PROVIDER_SOURCE + "\n" + self.source
        start = self.source.index("async def generate_video")
        block = self.source[start:self.source.index("if gen_status != 200:", start)]
        self.assertIn("for _attempt in range(SessionKeeper.VIDEO_GEN_MAX_ATTEMPTS):", block)
        self.assertNotIn("post_json_via_browser", block)
        self.assertIn('"browser_fallback": browser_fallback_used', block)

    def test_video_browser_fallback_uses_browser_safe_headers(self) -> None:
        self.source = _PR2A_PROVIDER_SOURCE + "\n" + self.source
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
        self.source = _PR2A_PROVIDER_SOURCE + "\n" + self.source
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
        self.source = _PR2A_PROVIDER_SOURCE + "\n" + self.source
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
        self.source = _PR2A_PROVIDER_SOURCE + "\n" + self.source
        start = self.source.index("async def generate_video")
        end = self.source.index("if gen_status != 200:", start)
        block = self.source[start:end]
        self.assertIn('"account_risk": "video_auth"', block)
        self.assertIn('"account_risk": "video_recaptcha_403"', block)

        # Failure/cooldown policy moved to accounts.health (Phase 11).
        health_source = (PROJECT_ROOT / "accounts" / "health.py").read_text(encoding="utf-8")
        helper = health_source[health_source.index("def mark_video_failure"):]
        # video_auth — кулдаун сразу; video_recaptcha_403 (стохастичный) —
        # через счётчик fail (кулдаун только после серии).
        self.assertIn('risk == "video_auth"', helper)
        self.assertIn("self._pool.mark_cooldown(account_id)", helper)
        self.assertIn("self._pool.mark_failure(account_id)", helper)
        self.assertIn('risk == "video_recaptcha_403"', helper)
        # Провайдерский 429 → аккаунт сразу в кулдаун (а не через счётчик fail).
        self.assertIn("is_rate_limit_error(result)", helper)
        self.assertIn('"reason": "rate_limited", "op": "video"', helper)

        video = self.source[
            self.source.index("async def _do_video_generate_and_send"):
            self.source.index("async def _video_download")
        ]
        self.assertIn("_mark_video_account_failure(acc_id, result)", video)

    def test_after_result_offers_video_balance_and_menu(self) -> None:
        # after_result moved to channels.telegram.image_delivery (Phase 11).
        source = (PROJECT_ROOT / "channels" / "telegram" / "image_delivery.py").read_text(encoding="utf-8")
        block = source[source.index("async def after_result"):][:700]
        for cb in ('"m:gen"', '"m:vid"', '"m:menu"'):
            self.assertIn(cb, block)
        self.assertIn("invite_button", block)      # Позвать друга
        self.assertIn("after_image_screen", block)
        self.assertIn("credit_store.balance", block)

    def test_price_screens_use_html_and_escape_user_text(self) -> None:
        self.assertIn("import html", self.source)
        self.assertIn('parse_mode="HTML"', self.source)
        # every echoed user prompt on an HTML screen is escaped
        self.assertIn("html.escape(pending", self.source + "\n" + self.screens_source)
        self.assertIn("html.escape(caption", self.source + "\n" + self.screens_source)

    def test_ingredients_diagnostic_logging_present(self) -> None:
        self.source = _PR2A_PROVIDER_SOURCE + "\n" + self.source
        # Temporary capture-driven logging to diagnose the фото+текст gen failure.
        self.assertIn("🎬 r2v req", self.source)
        self.assertIn("effective_model_key=%s", self.source)
        self.assertIn("video_reference_model_key(model_key, aspect)", self.source)
        self.assertIn("video_frames_model_key(model_key)", self.source)

    def test_metrics_wired_into_flow(self) -> None:
        # Metrics import + init + key events + idempotent transaction recording.
        self.assertIn("import metrics", self.source)
        self.assertIn("metrics.init_db(", self.source)
        metrics_sources = (
            self.source
            + "\n"
            + self.menu_router_source
            + "\n"
            + self.payments_router_source
            + "\n"
            + self.start_router_source
            + "\n"
            + self.screens_source
        )
        for ev in (
            '"user_started"', '"image_requested"', '"image_success"', '"image_failed"',
            '"variations_requested"', '"upscale_requested"', '"image_edit_requested"',
            '"video_requested"', '"video_success"', '"video_failed"',
            '"credits_charged"', '"credits_refunded"', '"topup_opened"',
            '"payment_success"', '"wizard_started"', '"wizard_completed"',
        ):
            self.assertIn(ev, metrics_sources, ev)
        # Идемпотентность ДО зачисления: дубль доставки successful_payment не
        # зачисляет кредиты второй раз; сбой метрик-БД оплату не блокирует.
        self.assertIn("deps.metrics.record_transaction_status(", self.payments_router_source)
        start = self.payments_router_source.index("async def on_successful_payment")
        handler = self.payments_router_source[start:start + 2600]
        self.assertLess(
            handler.index("deps.metrics.record_transaction_status("),
            handler.index("deps.credit_store.add(user_id"),
        )
        self.assertIn('if tx_status == "duplicate":', handler)
        self.assertIn('if tx_status == "error":', handler)
        # Fallback-ключ дедупа различает редоставку и новую покупку (message_id).
        self.assertIn("message.message_id", handler)
        self.assertIn("metrics.log_flow_job(", self.source)

    def test_admin_metrics_commands_registered(self) -> None:
        # The report commands live in the admin_reports router (Phase 6).
        for cmd in (
            "admin_today", "admin_revenue", "admin_flow",
            "admin_accounts", "admin_refs", "admin_channels", "admin_errors",
        ):
            self.assertIn(f'Command("{cmd}")', self.admin_reports_router_source, cmd)
        # All admin-gated (read-only for users); the gate stays in flow_bot
        # and is injected into the router.
        self.assertIn("def _admin_only", self.source)
        self.assertIn("deps.metrics.report_today()", self.admin_reports_router_source)
        self.assertIn("deps.metrics.report_accounts()", self.admin_reports_router_source)

    def test_channel_attribution_wired(self) -> None:
        # /start seed_<канал> → first-touch атрибуция в metrics.acquisitions.
        self.assertIn("CHANNEL_PARAM_PREFIX", self.source)
        self.assertIn("parse_channel_seed=parse_channel_seed", self.source)
        self.assertIn("parse_channel_seed(payload)", self.start_router_source)
        self.assertIn("metrics.record_acquisition(", self.start_router_source)
        self.assertIn('"acquired_from_channel"', self.start_router_source)
        # Атрибуция стоит внутри cmd_start (рядом с рефералкой), не где попало.
        start = self.start_router_source.index("async def cmd_start")
        block = self.start_router_source[start:start + 4500]
        self.assertIn("channel = parse_channel_seed(payload)", block)
        self.assertIn("metrics.record_acquisition(user_id=user_id, channel=channel)", block)
        # Админ-отчёт по каналам читает report_channels и умеет выдавать ссылку
        # (живёт в admin_reports роутере, Phase 6).
        self.assertIn("deps.metrics.report_channels()", self.admin_reports_router_source)
        self.assertIn("?start={CHANNEL_PARAM_PREFIX}{slug}", self.admin_reports_router_source)

    def test_admin_help_is_owner_gated(self) -> None:
        # /admin_help — справочник команд, доступен ТОЛЬКО владельцам (OWNER_ID).
        # The handler itself moved to the admin_accounts router (Phase 6).
        self.assertIn('Command("admin_help")', self.admin_accounts_router_source)
        self.assertIn("def _owner_only", self.source)
        self.assertIn("message.from_user.id in OWNER_IDS", self.source)
        start = self.admin_accounts_router_source.index("async def cmd_admin_help")
        block = self.admin_accounts_router_source[start:start + 400]
        self.assertIn("if not deps.owner_only(message):", block)
        self.assertNotIn("deps.admin_only(message)", block)  # не путать админ/владелец
        # Справочник перечисляет и пользовательские, и админские команды.
        self.assertIn("_HELP_SECTIONS", self.source)
        for cmd in ("/grant", "/refund", "/admin_channels", "/img", "/admin_help"):
            self.assertIn(cmd, self.source, cmd)

    def test_referral_wired(self) -> None:
        # Deep-link join, payment reward, menu entry, invite buttons, clawback.
        self.assertIn("REFERRAL_PARAM_PREFIX", self.source)
        self.assertIn("metrics.record_referral_join(", self.start_router_source)
        self.assertIn('"referral_joined"', self.start_router_source)
        self.assertIn("_maybe_apply_referral_rewards(", self.source)
        self.assertIn("deps.maybe_apply_referral_rewards(", self.payments_router_source)
        # Подарок приглашённому другу (+15) при join, мимо payments-pipeline.
        self.assertIn("REFERRAL_REFERRED_BONUS", self.source)
        self.assertIn('"referral_referred_bonus"', self.start_router_source)
        # «+50 за генерацию» удалено целиком — награда рефереру только на оплате
        # (anti-farm, REFERRAL.md §3). Второго пути быть не должно.
        self.assertNotIn("_maybe_apply_first_referral_generation_reward", self.source)
        self.assertNotIn("grant_first_generation_referral_reward", self.source)
        self.assertNotIn("REFERRAL_FIRST_GENERATION_BONUS", self.source)
        self.assertIn("first_referral_cta", self.source)
        # Reward orchestration moved to referrals/service.py (Phase 9).
        svc = (PROJECT_ROOT / "referrals" / "service.py").read_text(encoding="utf-8")
        self.assertIn('"referral_reward_paid"', svc)
        self.assertIn('data == "m:invite"', self.menu_router_source)
        self.assertIn("deps.show_referral_screen", self.menu_router_source)
        self.assertIn("_invite_button(", self.source)
        self.assertIn("_clawback_referral_rewards(", self.source)
        # Reward must be applied only after a recorded (idempotent) payment.
        self.assertIn("referral_milestone_bonus(", svc)
        self.assertIn("referral_ongoing_bonus(", svc)
        # Milestone выдаётся через атомарный клейм joined→rewarded (без TOCTOU):
        # начисление кредитов реферу — только при выигранном UPDATE.
        self.assertIn("grant_milestone_if_joined(", svc)
        self.assertNotIn('"tier": "join"', self.source)
        start = svc.index("def apply_payment_rewards")
        block = svc[start:start + 2200]
        self.assertLess(
            block.index("grant_milestone_if_joined("),
            block.index("self._store.add(referrer_id, bonus)"),
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
        start = self.payments_router_source.index("async def on_pre_checkout")
        block = self.payments_router_source[start:start + 700]
        self.assertIn("invoice_payload", block)
        self.assertIn('parts[0] == "credits"', block)
        self.assertIn("deps.credit_pack(parts[1]) is not None", block)
        self.assertIn("ok=ok", block)
        self.assertNotIn("answer(ok=True)", block)

    def test_animate_image_to_video_wired(self) -> None:
        # "Оживить фото" button under images + main-menu entry → r2v pipeline.
        kb = self.kb_source[self.kb_source.index("def _image_keyboard"):][:1200]
        self.assertIn('f"an:img:{token}"', kb)
        self.assertIn('@router.callback_query(F.data.startswith("an:"))', self.animate_router_source)
        self.assertIn('data == "m:animate"', self.menu_router_source)
        self.assertIn("async def show_animate_photo_input", self.source)
        animate_menu = self.menu_router_source[
            self.menu_router_source.index('elif data == "m:animate"'):
        ][:500]
        self.assertIn("deps.show_animate_photo_input", animate_menu)
        self.assertIn('"vanimate_photo"', self.plain_text_router_source)
        # Seeds the generated image into the new scenario layer as a vphoto
        # reference, carrying the image's account/project so r2v doesn't 404 on
        # another acc.
        self.assertIn("start_from_generated_image", self.animate_router_source)
        self.assertIn("source=ref.source if isinstance(ref.source, dict) else {}", self.animate_router_source)
        self.assertIn("account_id=ref.account_id", self.animate_router_source)
        self.assertIn("project_id=ref.project_id", self.animate_router_source)
        # "✏️ Изменить" (v:nchange) must NOT call show_video_prompt_input, which
        # does _vid_clear and would drop the attached photo — it must keep state
        # and wait for a new prompt via vnchange instead.
        # v:nchange dispatch lives in the video router (Phase 6).
        nchange = self.video_router_source[self.video_router_source.index('if data == "v:nchange":'):][:700]
        self.assertNotIn("show_video_prompt_input", nchange)
        self.assertIn('st["vawait"] = "vnchange"', nchange)
        # The vnchange text handler re-renders the wizard (keeps vphoto/vmode).
        self.assertIn(
            'if st.get("vstep") == "vnewwiz" and st.get("vawait") == "vnchange":',
            self.plain_text_router_source,
        )
        self.assertIn('startswith("an:")', self.animate_router_source)

    def test_ingredients_chat_prompt_starts_video(self) -> None:
        # «Оживить фото»: фото уже выбрано → текст из чата запускает ВИДЕО, а не
        # картинки. Ветки (ingredients/frames) стоят ДО image-фолбэка.
        source = self.plain_text_router_source
        start = source.index("async def handle_plain_text")
        ing_branch = source.index(
            'if st.get("vmode") == "ingredients" and (st.get("ving_photos") or []):', start
        )
        frm_branch = source.index(
            'if st.get("vmode") == "frames" and st.get("vfrm_start") and st.get("vfrm_end"):',
            start,
        )
        image_fallback = source.index('st["pending_prompt"] = text', start)
        awaiting_image_prompt = source.index('awaiting = st.get("await")', start)
        wizard_image = source.index('if st.get("step") == "wizard":', start)
        for branch in (ing_branch, frm_branch):
            self.assertLess(branch, awaiting_image_prompt)
            self.assertLess(branch, wizard_image)
            self.assertLess(branch, image_fallback)
        block = source[ing_branch:frm_branch + 260]
        self.assertIn("_video_generate_and_send(message, text, user_id=user_id)", block)
        # Вход в «Оживить фото» чистит залипший image-визард (await/step), иначе он
        # перехватил бы промпт. Помощник зовётся из m:animate и an:img.
        # _clear_image_flow_keys moved to storage/session_state.py (Phase 11).
        ss = (PROJECT_ROOT / "storage" / "session_state.py").read_text(encoding="utf-8")
        self.assertIn("def _clear_image_flow_keys", ss)
        self.assertGreaterEqual(self.source.count("_clear_image_flow_keys(st)"), 2)

    def test_video_photo_wait_text_does_not_open_image_wizard(self) -> None:
        source = self.plain_text_router_source
        start = source.index("async def handle_plain_text")
        wait_guard = source.index('if st.get("vawait") == "ving_photo":', start)
        start_guard = source.index('if st.get("vawait") == "vfrm_start":', start)
        end_guard = source.index('if st.get("vawait") == "vfrm_end":', start)
        ing_ready = source.index(
            'if st.get("vmode") == "ingredients" and (st.get("ving_photos") or []):',
            start,
        )
        image_fallback = source.index('st["pending_prompt"] = text', start)
        # ingredients check must come BEFORE the vawait guards (ingredients-ready text
        # should trigger video generation, not be blocked by the photo-wait guard).
        for guard in (wait_guard, start_guard, end_guard):
            self.assertLess(ing_ready, guard)
            self.assertLess(guard, image_fallback)
        block = source[wait_guard:end_guard + 180]
        self.assertIn('flow_copy.msg("vid_ing_send_photo")', block)
        self.assertIn('flow_copy.msg("vid_frm_send_photo_start")', block)
        self.assertIn('flow_copy.msg("vid_frm_send_photo_end")', block)

    def test_ideas_hub_wired(self) -> None:
        # Menu entry + hub root + both branches (templates Q&A, guided picker).
        self.assertIn("import prompts_lib", self.source)
        self.assertIn('data == "m:ideas"', self.menu_router_source)
        self.assertIn("deps.show_ideas_root", self.menu_router_source)
        self.assertIn('@router.callback_query(F.data.startswith("ih:"))', self.ideas_hub_router_source)
        self.assertIn('@router.callback_query(F.data.startswith("tp:"))', self.ideas_flow_router_source)
        self.assertIn('@router.callback_query(F.data.startswith("gp:"))', self.ideas_flow_router_source)
        self.assertIn("prompts_lib.compose_template_prompt(", self.source)
        self.assertIn("prompts_lib.compose_guided_prompt(", self.source)
        # Composed prompt feeds the existing wizard via pending_prompt.
        self.assertIn('st["pending_prompt"] = prompt', self.source)
        self.assertIn('"template_opened"', self.ideas_flow_router_source)
        self.assertIn('"template_used"', self.source)
        # Free-text Q&A answers are captured in the text handler.
        self.assertIn('st.get("tp_await") == "text"', self.plain_text_router_source)
        self.assertIn('flow_copy.msg("ideas_text_attached_template")', self.plain_text_router_source)
        self.assertIn('flow_copy.msg("ideas_text_attached_guided")', self.plain_text_router_source)
        self.assertIn('flow_copy.msg("ideas_text_attached_root")', self.plain_text_router_source)
        self.assertIn('flow_copy.msg("ideas_choice_hint")', self.source)
        self.assertIn('flow_copy.msg("ideas_guided_hint")', self.source)
        self.assertIn("_ideas_prompt_with_extra(prompt, st)", self.source)
        self.assertIn("await _prepare_photo_edit_from_file_id(", self.source)
        self.assertIn("await _prepare_photo_video_from_file_id(", self.source)
        for key in (
            "ideas_choice_hint",
            "ideas_guided_hint",
            "ideas_photo_attached_guided",
            "ideas_text_attached_guided",
        ):
            self.assertIn(key, flow_copy.MESSAGES)
        # New prefixes register before the catch-all image handler.
        for pfx in ('startswith("tp:")', 'startswith("gp:")'):
            self.assertIn(pfx, self.ideas_flow_router_source)
        self.assertIn('startswith("ih:")', self.ideas_hub_router_source)

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
        self.source = _PR2A_PROVIDER_SOURCE + "\n" + self.source
        # Загрузка/правка СВОЕГО видео временно отключена флагом: сервис отдаёт
        # «Oops…» / недогруз. Реализация сохранена целиком, но спрятана за
        # UPLOAD_VIDEO_EDIT_ENABLED (вернуть фичу = поставить True). Флаг живёт в
        # config/settings.py (live-config, Phase 5); читатели используют _cfg.
        _settings_src = (PROJECT_ROOT / "config" / "settings.py").read_text(encoding="utf-8")
        self.assertIn("UPLOAD_VIDEO_EDIT_ENABLED: bool = False", _settings_src)
        self.assertIn("if _cfg.UPLOAD_VIDEO_EDIT_ENABLED else []", self.kb_source)   # кнопка в семействе
        self.assertIn("if not deps.upload_video_edit_enabled():", self.video_upload_router_source)  # колбэк vu:start
        self.assertIn("vid_upload_disabled", flow_copy.MESSAGES)
        # handle_video_upload игнорирует видео, пока фича выключена. Хендлер
        # переехал в message-input роутер (Phase 6, wave F).
        self.assertIn(
            "if not deps.upload_video_edit_enabled() or st.get(\"vawait\") != \"vu_video\":",
            self.video_upload_input_router_source,
        )
        # Реализация (на случай возврата фичи) на месте: колбэк + хендлер + правка.
        self.assertIn('"vu:start"', self.video_upload_router_source)
        self.assertIn('@router.callback_query(F.data.startswith("vu:"))', self.video_upload_router_source)
        self.assertIn("@router.message(F.video | F.document)", self.video_upload_input_router_source)
        self.assertIn("deps.account_for_video(user_id)", self.video_upload_input_router_source)
        self.assertIn("deps.keeper_for_acc(acc_id).upload_video(", self.video_upload_input_router_source)
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
        # on_video_action (v: callback handler) moved to
        # channels/telegram/routers/video.py (Phase 6); the next function in
        # flow_bot.py after _video_edit_uploaded is now _aspect_to_fmt.
        block = self.source[
            self.source.index("async def _video_edit_uploaded"):
            self.source.index("def _aspect_to_fmt")
        ]
        self.assertIn("prompt_edited=True", block)
        self.assertIn('video_operation="edit"', block)
        # Orientation comes from the uploaded clip's real dimensions.
        self.assertIn("_VID_FMT_TO_ASPECT[fmt]", block)
        # The upload handler waits for server-side transcode before allowing the
        # edit (otherwise the edit job FAILs), and a caption sent together with
        # the video starts the edit immediately instead of re-asking. The
        # handler lives in the video_upload_input router (Phase 6, wave F).
        upload_handler = self.video_upload_input_router_source[
            self.video_upload_input_router_source.index("async def handle_video_upload"):
        ]
        self.assertIn("deps.client_for_acc(acc_id).wait_video_ready(", upload_handler)
        self.assertIn("message.caption", upload_handler)
        self.assertIn("deps.video_edit_uploaded(message, caption, user_id=user_id)", upload_handler)
        self.assertIn("async def wait_video_ready", self.source)

    def test_video_prompt_edit_clears_reference_mode_inputs(self) -> None:
        # _vid_clear_reference_inputs moved to storage/session_state.py (Phase 11).
        self.assertIn(
            "def _vid_clear_reference_inputs",
            (PROJECT_ROOT / "storage" / "session_state.py").read_text(encoding="utf-8"),
        )
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
        self.source = _PR2A_PROVIDER_SOURCE + "\n" + self.source
        # No local ffmpeg merge anywhere.
        self.assertNotIn("async def _concat_video_bytes", self.source)
        self.assertNotIn("asyncio.create_subprocess_exec", self.source)
        self.assertNotIn("segment_media_id", self.source)
        # Default extend delivery = the service's server-side stitched full video.
        # Byte-fetch logic moved to product.video_delivery (Phase 11).
        delivery_source = (PROJECT_ROOT / "product" / "video_delivery.py").read_text(encoding="utf-8")
        self.assertIn("async def video_delivery_bytes", delivery_source)
        self.assertIn("async def fetch_full_extended_video", self.source)
        self.assertIn("full_bytes = await client_for_acc(ref.account_id).fetch_full_extended_video", delivery_source)
        self.assertIn('if ref.mode == "extend" and ref.scene_id and ref.project_id', delivery_source)
        # The new fragment button downloads the extend result media_id itself.
        self.assertIn("async def _video_segment_download", self.source)
        # v:dl_seg: dispatch lives in the video callback router (Phase 6).
        self.assertIn("v:dl_seg:", self.video_router_source)

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
            'data-tab="adcalc"',
            'id="tab-adcalc"',
            'id="ad-segment"',
            'id="ad-channels"',
            'id="ad-tgstat-url"',
            '/telemetr/channel?url=',
            'function adFetchChannel',
            'function loadAdCalc',
            'function adRecalc()',
            'function adApplyLiveDefaults',
            'adApplyLiveDefaults({repeat, margin});',
            'class="form-help"',
            'Фрикция %',
            'CAC green',
            'Flow quota',
        ):
            self.assertIn(needle, self.admin)
        analytics = self.admin[
            self.admin.index('id="tab-analytics"'):
            self.admin.index('id="tab-adcalc"')
        ]
        self.assertNotIn("CAC-калькулятор", analytics)

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

        old_credit_store = fb.credit_store
        old_metrics = fb.metrics
        old_notify = fb._notify_robokassa_success
        old_referral = fb._maybe_apply_referral_rewards
        self.addCleanup(setattr, fb, "credit_store", old_credit_store)
        self.addCleanup(setattr, fb, "metrics", old_metrics)
        self.addCleanup(setattr, fb, "_notify_robokassa_success", old_notify)
        self.addCleanup(setattr, fb, "_maybe_apply_referral_rewards", old_referral)

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

            # Message handlers live both on dp and on extracted routers
            # (Phase 6); count them together, same as callbacks below.
            message_handler_count = len(fb.dp.message.handlers) + sum(
                len(router.message.handlers) for router in fb.dp.sub_routers
            )
            self.assertGreaterEqual(message_handler_count, 10)
            callback_handler_count = len(fb.dp.callback_query.handlers) + sum(
                len(router.callback_query.handlers) for router in fb.dp.sub_routers
            )
            self.assertGreaterEqual(callback_handler_count, 12)
            router_names = [router.name for router in fb.dp.sub_routers]
            self.assertIn("tg-start", router_names)
            self.assertIn("tg-image-action", router_names)
            self.assertIn("tg-payments", router_names)
            pre_checkout_count = len(fb.dp.pre_checkout_query.handlers) + sum(
                len(router.pre_checkout_query.handlers) for router in fb.dp.sub_routers
            )
            self.assertEqual(pre_checkout_count, 1)
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
            import config.settings as _cfg
            old_test_flag = _cfg.TOPUP_TEST_PACKS_ENABLED
            _cfg.TOPUP_TEST_PACKS_ENABLED = True
            try:
                flagged_admin_texts = [b.text for row in fb.topup_stars_kb(is_admin=True).inline_keyboard for b in row]
                flagged_user_texts = [b.text for row in fb.topup_stars_kb(is_admin=False).inline_keyboard for b in row]
                self.assertTrue(any("Тест" in text for text in flagged_admin_texts))
                self.assertFalse(any("Тест" in text for text in flagged_user_texts))
            finally:
                _cfg.TOPUP_TEST_PACKS_ENABLED = old_test_flag
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
            # Флаг живёт в config.settings (live-config); читатель video_family_kb
            # берёт его через _cfg, поэтому тумблим именно config.settings.
            import config.settings as _cfg
            self.assertFalse(_cfg.UPLOAD_VIDEO_EDIT_ENABLED)
            fam_rows = fb.video_family_kb().inline_keyboard
            self.assertFalse(
                any((b.callback_data or "") == "vu:start" for row in fam_rows for b in row)
            )
            # Возврат флага возвращает кнопку (с ценой) — проводка сохранена, лишь скрыта.
            _cfg.UPLOAD_VIDEO_EDIT_ENABLED = True
            try:
                rows_on = fb.video_family_kb().inline_keyboard
                upload_btn = next(
                    b for row in rows_on for b in row if (b.callback_data or "") == "vu:start"
                )
                self.assertIn("кр", upload_btn.text)
            finally:
                _cfg.UPLOAD_VIDEO_EDIT_ENABLED = False
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
