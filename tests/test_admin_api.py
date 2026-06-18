from __future__ import annotations

import json
import asyncio
import unittest

import admin_api


class _FakePool:
    def __init__(self, accounts):
        self._accounts = accounts

    def status(self):
        return self._accounts


class _FakeKeeper:
    def __init__(self, credits_dict=None, raises=False, delay=0.0):
        self._credits = credits_dict
        self._raises = raises
        self._delay = delay

    async def get_g_credits(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raises:
            raise RuntimeError("boom")
        return self._credits


class AccountsEndpointGCreditsTests(unittest.IsolatedAsyncioTestCase):
    """handle_accounts_get() attaches a live g_credits field per account."""

    def setUp(self):
        self.addCleanup(self._reset_globals)

    def _reset_globals(self):
        admin_api._pool = None
        admin_api._keepers = None
        admin_api.GCREDITS_LOOKUP_TIMEOUT_SEC = 3.0

    async def test_g_credits_attached_when_keepers_present(self):
        pool = _FakePool([
            {"id": "a1", "disabled": False, "cooldown_left": 0, "fails": 0},
            {"id": "a2", "disabled": False, "cooldown_left": 0, "fails": 0},
        ])
        keepers = {
            "a1": _FakeKeeper({"credits": 50, "is_paid": False}),
            "a2": _FakeKeeper(raises=True),
        }
        admin_api._pool = pool
        admin_api._keepers = keepers

        resp = await admin_api.handle_accounts_get(None)
        body = json.loads(resp.body)
        by_id = {a["id"]: a for a in body}
        self.assertEqual(by_id["a1"]["g_credits"], {"credits": 50, "is_paid": False})
        self.assertIsNone(by_id["a2"]["g_credits"])  # exception -> None, not a crash

    async def test_g_credits_absent_without_keepers(self):
        pool = _FakePool([{"id": "a1", "disabled": False, "cooldown_left": 0, "fails": 0}])
        admin_api._pool = pool
        admin_api._keepers = None

        resp = await admin_api.handle_accounts_get(None)
        body = json.loads(resp.body)
        self.assertIsNone(body[0]["g_credits"])

    async def test_slow_g_credits_does_not_block_accounts_response(self):
        pool = _FakePool([
            {"id": "a1", "disabled": False, "cooldown_left": 0, "fails": 0},
        ])
        admin_api._pool = pool
        admin_api._keepers = {"a1": _FakeKeeper({"credits": 50}, delay=0.05)}
        admin_api.GCREDITS_LOOKUP_TIMEOUT_SEC = 0.001

        resp = await admin_api.handle_accounts_get(None)
        body = json.loads(resp.body)
        self.assertEqual(body[0]["id"], "a1")
        self.assertIsNone(body[0]["g_credits"])


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

    def test_overrides_only_keeps_diffs_and_custom_keys(self) -> None:
        defaults = {"a": "code-A", "b": "code-B", "c": "code-C"}
        clean = {
            "a": "code-A",      # unchanged → dropped (falls back to code)
            "b": "edited-B",    # changed → kept
            "custom": "extra",  # not in defaults → kept
        }
        self.assertEqual(
            admin_api._overrides_only(defaults, clean),
            {"b": "edited-B", "custom": "extra"},
        )

    def test_overrides_only_empty_when_all_match_code(self) -> None:
        defaults = {"a": "x", "b": "y"}
        self.assertEqual(admin_api._overrides_only(defaults, {"a": "x", "b": "y"}), {})


if __name__ == "__main__":
    unittest.main()
