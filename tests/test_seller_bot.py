from __future__ import annotations

import inspect
from pathlib import Path
import unittest

import flow_core
import flow_bot


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SELLER_PACKS = ("s_card", "s_5cards", "s_shop", "s_shopxl")


class SellerPackTests(unittest.TestCase):
    def test_consumer_pack_ids_exclude_seller_and_test(self) -> None:
        ids = flow_core.public_pack_ids()
        self.assertEqual(ids, ["trial", "small", "medium", "large", "xl"])
        for pid in SELLER_PACKS:
            self.assertNotIn(pid, ids)
        self.assertNotIn("test", ids)

    def test_seller_pack_ids(self) -> None:
        ids = flow_core.public_pack_ids(seller=True)
        self.assertEqual(ids, list(SELLER_PACKS))

    def test_test_pack_gated_by_include_test(self) -> None:
        self.assertNotIn("test", flow_core.public_pack_ids())
        self.assertNotIn("test", flow_core.public_pack_ids(seller=True))
        self.assertIn("test", flow_core.public_pack_ids(include_test=True))

    def test_seller_packs_have_credits_stars_and_rub(self) -> None:
        for pid in SELLER_PACKS:
            p = flow_core.pack(pid)
            self.assertIsNotNone(p, pid)
            self.assertGreater(p["credits"], 0)
            self.assertGreater(p["stars"], 0)
            self.assertIn(pid, flow_core.ROBOKASSA_PACK_AMOUNTS_RUB)

    def test_seller_packs_cheaper_per_credit_than_trial(self) -> None:
        # Volume reward: top seller pack must beat the trial rub/credit rate.
        trial_rate = float(flow_core.ROBOKASSA_PACK_AMOUNTS_RUB["trial"]) / flow_core.pack("trial")["credits"]
        shopxl_rate = float(flow_core.ROBOKASSA_PACK_AMOUNTS_RUB["s_shopxl"]) / flow_core.pack("s_shopxl")["credits"]
        self.assertLess(shopxl_rate, trial_rate)

    def test_opt_agency_pack_removed(self) -> None:
        self.assertIsNone(flow_core.pack("s_agency"))


class SellerMenuTests(unittest.TestCase):
    def setUp(self) -> None:
        self._was_seller = flow_bot.IS_SELLER

    def tearDown(self) -> None:
        flow_bot.IS_SELLER = self._was_seller

    def _callbacks(self, kb) -> list[str]:
        return [b.callback_data for row in kb.inline_keyboard for b in row]

    def test_marketplace_button_only_in_seller_mode(self) -> None:
        flow_bot.IS_SELLER = True
        self.assertIn("m:mp", self._callbacks(flow_bot.main_menu_kb()))
        flow_bot.IS_SELLER = False
        self.assertNotIn("m:mp", self._callbacks(flow_bot.main_menu_kb()))

    def test_platform_keyboard(self) -> None:
        cb = self._callbacks(flow_bot.mp_root_kb())
        self.assertEqual(
            [c for c in cb if c.startswith("mp:plat:")],
            ["mp:plat:wb", "mp:plat:ozon", "mp:plat:ym"],
        )

    def test_jobs_keyboard_has_core_jobs(self) -> None:
        cb = self._callbacks(flow_bot.mp_jobs_kb("wb"))
        for job in ("whitebg", "info", "model", "cover", "bg", "animate"):
            self.assertIn(f"mp:job:{job}", cb)
        self.assertIn("mp:series", cb)
        self.assertIn("mp:brandkit", cb)
        self.assertIn("mp:niche", cb)
        self.assertIn("mp:projects", cb)
        self.assertIn("mp:done4you", cb)
        self.assertIn("mp:tips", cb)
        self.assertIn("m:mp", cb)  # back to platforms

    def test_series_keyboard_has_bundle_prices(self) -> None:
        kb = flow_bot.mp_series_kb("wb")
        cb = self._callbacks(kb)
        self.assertEqual(
            [c for c in cb if c.startswith("mp:series:")],
            ["mp:series:3", "mp:series:5", "mp:series:8"],
        )
        text = "\n".join(b.text for row in kb.inline_keyboard for b in row)
        self.assertIn("30 кр", text)
        self.assertIn("45 кр", text)
        self.assertIn("70 кр", text)

    def test_job_seeds_cover_image_jobs(self) -> None:
        for job in ("whitebg", "info", "model", "cover", "bg"):
            self.assertIn(job, flow_bot._MP_JOB_SEED)
            self.assertTrue(flow_bot._MP_JOB_SEED[job])

    def test_marketplace_image_jobs_wait_for_product_photo(self) -> None:
        source = inspect.getsource(flow_bot.on_marketplace_action)
        self.assertIn('if job not in _MP_PRODUCT_PHOTO_JOBS:', source)
        self.assertIn('st["await"] = "mp_photo"', source)
        self.assertIn('st["edit_fmt"] = "f34"', source)
        self.assertIn("_mp_photo_request_text(plat, job)", source)
        self.assertNotIn("await show_wizard(msg", source)

    def test_marketplace_instruction_uses_uploaded_photo(self) -> None:
        prompt = flow_bot._mp_job_instruction("whitebg", "wb", "красные ботинки")
        self.assertIn("загруженное фото", prompt)
        self.assertIn("Wildberries", prompt)
        self.assertIn("красные ботинки", prompt)

    def test_marketplace_series_waits_for_product_photo(self) -> None:
        source = inspect.getsource(flow_bot.on_marketplace_action)
        self.assertIn('if data == "mp:series":', source)
        self.assertIn('if data.startswith("mp:series:"):', source)
        self.assertIn('st["await"] = "mp_series_photo"', source)
        self.assertIn('st["mp_series_count"] = count', source)
        self.assertIn("_mp_series_request_text(plat, count)", source)
        self.assertIn('"mp_job"', source)

    def test_marketplace_series_prompt_uses_uploaded_photo(self) -> None:
        prompt = flow_bot._mp_series_prompt("ozon", 5, "зелёная бутылка")
        self.assertIn("5", prompt)
        self.assertIn("Ozon", prompt)
        self.assertIn("загруженное фото", prompt)
        self.assertIn("зелёная бутылка", prompt)

    def test_marketplace_prompts_include_brand_kit_when_present(self) -> None:
        prompt = flow_bot._mp_job_instruction("cover", "wb", brand_kit="чёрный и золото")
        self.assertIn("Бренд-кит продавца", prompt)
        self.assertIn("чёрный и золото", prompt)
        series_prompt = flow_bot._mp_series_prompt("wb", 3, brand_kit="минимализм")
        self.assertIn("Бренд-кит продавца", series_prompt)
        self.assertIn("минимализм", series_prompt)

    def test_marketplace_prompts_include_niche_when_present(self) -> None:
        prompt = flow_bot._mp_job_instruction("model", "wb", niche="clothes")
        self.assertIn("Ниша товара", prompt)
        self.assertIn("Одежда", prompt)
        self.assertIn("посадку", prompt)
        series_prompt = flow_bot._mp_series_prompt("wb", 3, niche="electronics")
        self.assertIn("Ниша товара", series_prompt)
        self.assertIn("Электроника", series_prompt)

    def test_marketplace_series_photo_runs_i2i_bundle_action(self) -> None:
        source = inspect.getsource(flow_bot.handle_photo)
        self.assertIn('if st.get("await") == "mp_series_photo":', source)
        self.assertIn("niche=_mp_niche(user_id)", source)
        self.assertIn('num_images=count', source)
        self.assertIn('action="mp_series"', source)

    def test_done4you_sets_support_brief_state(self) -> None:
        source = inspect.getsource(flow_bot.on_marketplace_action)
        self.assertIn('if data == "mp:done4you":', source)
        self.assertIn('st["support_await"] = True', source)
        self.assertIn('st["support_kind"] = "mp_done4you"', source)
        self.assertIn('"mp_done4you_open"', source)

    def test_done4you_brief_is_tagged_ticket(self) -> None:
        source = inspect.getsource(flow_bot.handle_plain_text)
        self.assertIn('support_kind = st.pop("support_kind", "support")', source)
        self.assertIn('if support_kind == "mp_done4you":', source)
        self.assertIn("Заявка под ключ", source)
        self.assertIn('"mp_done4you_submitted"', source)

    def test_sku_projects_menu_and_result_action(self) -> None:
        source = inspect.getsource(flow_bot.on_marketplace_action)
        self.assertIn('if data == "mp:projects":', source)
        self.assertIn("_show_sku_projects", source)
        action_source = inspect.getsource(flow_bot.on_image_action)
        self.assertIn('elif action == "skuadd":', action_source)
        self.assertIn('st["mp_sku_pending"]', action_source)
        self.assertIn("_mp_sku_choice_kb(user_id)", action_source)

    def test_seller_image_keyboard_adds_export_and_sku_buttons(self) -> None:
        cb = self._callbacks(flow_bot._seller_image_keyboard("tok123"))
        self.assertIn("mpe:tok123", cb)
        self.assertIn("sku:tok123", cb)
        normal_cb = self._callbacks(flow_bot._image_keyboard("tok123"))
        self.assertNotIn("mpe:tok123", normal_cb)
        self.assertNotIn("sku:tok123", normal_cb)

    def test_seller_marketplace_export_action_uses_document_helper(self) -> None:
        action_source = inspect.getsource(flow_bot.on_image_action)
        self.assertIn('elif action == "mpexport":', action_source)
        self.assertIn("marketplace_export=True", action_source)
        ref = flow_core.ImageRef(
            user_id=1,
            project_id="p",
            source={"mediaId": "mediaabcdef123456"},
            platform="wb",
        )
        filename = flow_bot._marketplace_export_filename(ref, b"\x89PNG\r\n\x1a\nrest")
        self.assertTrue(filename.startswith("photozhab_wildberries_3x4_"))
        self.assertTrue(filename.endswith(".png"))
        self.assertIn("Wildberries", flow_bot._marketplace_export_caption(ref))

    def test_brandkit_sets_profile_state(self) -> None:
        source = inspect.getsource(flow_bot.on_marketplace_action)
        self.assertIn('if data == "mp:brandkit":', source)
        self.assertIn('st["await"] = "mp_brandkit"', source)
        self.assertIn("_mp_brandkit_text(user_id)", source)

    def test_niche_presets_save_profile(self) -> None:
        cb = self._callbacks(flow_bot._mp_niche_kb())
        self.assertEqual(
            [c for c in cb if c.startswith("mp:niche:")],
            ["mp:niche:clothes", "mp:niche:beauty", "mp:niche:electronics", "mp:niche:kids", "mp:niche:food"],
        )
        source = inspect.getsource(flow_bot.on_marketplace_action)
        self.assertIn('if data == "mp:niche":', source)
        self.assertIn('if data.startswith("mp:niche:"):', source)
        self.assertIn("metrics.save_seller_profile(user_id, niche=niche_id)", source)
        self.assertIn('"mp_niche_saved"', source)

    def test_seller_service_templates_are_wired_for_separate_env(self) -> None:
        root = PROJECT_ROOT
        flow_source = (root / "flow_bot.py").read_text(encoding="utf-8")
        deploy_script = (root / "deploy.sh").read_text(encoding="utf-8")
        seller_unit = (root / "deploy/systemd/geminifree-seller-bot.service").read_text(encoding="utf-8")
        seller_runner = (root / "deploy/bin/geminifree-seller-bot-run").read_text(encoding="utf-8")
        seller_env = (root / "deploy/examples/seller.env.example").read_text(encoding="utf-8")
        gitignore = (root / ".gitignore").read_text(encoding="utf-8")

        self.assertIn('ENV_FILE = os.getenv("ENV_FILE", ".env") or ".env"', flow_source)
        self.assertIn("load_dotenv(ENV_FILE)", flow_source)
        self.assertIn("geminifree-seller-bot", deploy_script)
        self.assertIn("Skipping $unit (unit is not installed)", deploy_script)
        self.assertIn("Skipping $unit (missing $required_env)", deploy_script)
        self.assertIn("restart_if_installed geminifree-seller-bot /opt/geminifree/.env.seller", deploy_script)
        self.assertIn("ENV_FILE=/opt/geminifree/.env.seller", seller_unit)
        self.assertIn("/usr/local/bin/geminifree-seller-bot-run", seller_unit)
        self.assertIn("/opt/geminifree/.env.seller", seller_runner)
        self.assertIn("BOT_MODE=seller", seller_env)
        self.assertIn("USER_CREDITS_FILE=user_credits_seller.json", seller_env)
        self.assertIn("ROBOKASSA_WEB_PORT=8082", seller_env)
        self.assertIn("SBP_PAYMENT_ENABLED=0", seller_env)
        self.assertIn("user_credits_*.json", gitignore)


if __name__ == "__main__":
    unittest.main()
