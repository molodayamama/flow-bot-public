from __future__ import annotations

import unittest

import admin_api


class AdminApiValidationTests(unittest.TestCase):
    def test_price_validation_rejects_zero_negative_and_unknown_paid_keys(self) -> None:
        prices, errors = admin_api._validate_prices({
            "image_nano": 0,
            "edit_photo": -1,
            "unknown": 10,
            "veo_lite": 80,
        })

        self.assertEqual(prices, {"veo_lite": 80})
        by_key = {e["key"]: e["error"] for e in errors}
        self.assertEqual(by_key["image_nano"], "zero_paid_price")
        self.assertEqual(by_key["edit_photo"], "negative_price")
        self.assertEqual(by_key["unknown"], "unknown_price_key")

    def test_copy_validation_blocks_lost_placeholders_and_non_string_defaults(self) -> None:
        clean, errors, warnings = admin_api._validate_copy_payload(
            {
                "low_balance": "Need {needed}, have {have}",
                "vid_status_phrases": ["one", "two"],
            },
            {
                "low_balance": "Need credits",
                "vid_status_phrases": "oops",
                "custom": "Цена 10 кр",
            },
            kind="message",
        )

        self.assertEqual(clean, {"custom": "Цена 10 кр"})
        by_key = {e["key"]: e["error"] for e in errors}
        self.assertEqual(by_key["low_balance"], "missing_placeholders")
        self.assertEqual(by_key["vid_status_phrases"], "non_string_default")
        self.assertEqual(warnings, [{"key": "custom", "warning": "hardcoded_price"}])


if __name__ == "__main__":
    unittest.main()
