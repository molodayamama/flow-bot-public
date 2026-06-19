from __future__ import annotations

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
        self.assertIn("mp:done4you", cb)
        self.assertIn("mp:tips", cb)
        self.assertIn("m:mp", cb)  # back to platforms

    def test_job_seeds_cover_image_jobs(self) -> None:
        for job in ("whitebg", "info", "model", "cover", "bg"):
            self.assertIn(job, flow_bot._MP_JOB_SEED)
            self.assertTrue(flow_bot._MP_JOB_SEED[job])


if __name__ == "__main__":
    unittest.main()
