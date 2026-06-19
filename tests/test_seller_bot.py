from __future__ import annotations

import inspect
import unittest

import flow_core
import flow_bot


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

    def test_marketplace_series_photo_runs_i2i_bundle_action(self) -> None:
        source = inspect.getsource(flow_bot.handle_photo)
        self.assertIn('if st.get("await") == "mp_series_photo":', source)
        self.assertIn("_mp_series_prompt(plat, count, caption_text)", source)
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

    def test_seller_image_keyboard_adds_sku_button(self) -> None:
        cb = self._callbacks(flow_bot._seller_image_keyboard("tok123"))
        self.assertIn("sku:tok123", cb)
        normal_cb = self._callbacks(flow_bot._image_keyboard("tok123"))
        self.assertNotIn("sku:tok123", normal_cb)


if __name__ == "__main__":
    unittest.main()
