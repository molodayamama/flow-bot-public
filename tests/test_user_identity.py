from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import metrics
from core.user_identity import (
    platform_identity,
    telegram_legacy_internal_id,
    uses_telegram_legacy_id,
)


class UserIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / "metrics.db")
        metrics.close()
        metrics.init_db(self.db_path)

    def tearDown(self) -> None:
        metrics.close()
        self._tmp.cleanup()

    def _credit_row(self, internal_user_id: int):
        row = metrics._conn().execute(
            "SELECT balance, granted FROM credits WHERE user_id=?",
            (internal_user_id,),
        ).fetchone()
        return tuple(row) if row else None

    def test_core_identity_normalization(self) -> None:
        identity = platform_identity(" Telegram ", " 42 ")

        self.assertEqual(identity.platform, "telegram")
        self.assertEqual(identity.platform_user_id, "42")
        self.assertTrue(uses_telegram_legacy_id("telegram"))
        self.assertEqual(telegram_legacy_internal_id("42"), 42)

    def test_telegram_identity_uses_legacy_positive_user_id(self) -> None:
        internal_id = metrics.ensure_user_identity("telegram", "42")

        self.assertEqual(internal_id, 42)
        row = metrics.get_user_identity("telegram", "42")
        self.assertIsNotNone(row)
        self.assertEqual(row["internal_user_id"], 42)

    def test_max_identity_gets_separate_negative_namespace(self) -> None:
        telegram_id = metrics.ensure_user_identity("telegram", "42")
        max_id = metrics.ensure_user_identity("max", "42")
        second_max_id = metrics.ensure_user_identity("max", "99")

        self.assertEqual(telegram_id, 42)
        self.assertLess(max_id, 0)
        self.assertLess(second_max_id, 0)
        self.assertNotEqual(max_id, telegram_id)
        self.assertNotEqual(second_max_id, max_id)

    def test_identity_can_be_resolved_from_internal_id(self) -> None:
        internal_id = metrics.ensure_user_identity("max", "42")

        row = metrics.get_identity_by_internal_id(internal_id)

        self.assertIsNotNone(row)
        self.assertEqual(row["platform"], "max")
        self.assertEqual(row["platform_user_id"], "42")
        self.assertIsNone(metrics.get_identity_by_internal_id(-999999))

    def test_identity_credit_balances_are_separate_by_platform(self) -> None:
        telegram_balance = metrics.credits_balance_for_identity("telegram", "42", starter=30)
        max_balance = metrics.credits_balance_for_identity("max", "42", starter=30)
        max_internal_id = metrics.ensure_user_identity("max", "42")

        self.assertEqual(telegram_balance, 30)
        self.assertEqual(max_balance, 30)
        self.assertEqual(self._credit_row(42), (30, 1))
        self.assertEqual(self._credit_row(max_internal_id), (30, 1))
        self.assertNotEqual(max_internal_id, 42)

    def test_identity_charge_refund_and_add(self) -> None:
        self.assertTrue(metrics.credits_charge_for_identity("max", "42", amount=10, starter=30))
        internal_id = metrics.ensure_user_identity("max", "42")
        self.assertEqual(self._credit_row(internal_id), (20, 1))

        metrics.credits_refund_for_identity("max", "42", amount=5)
        self.assertEqual(self._credit_row(internal_id), (25, 1))

        balance = metrics.credits_add_for_identity("max", "42", amount=40, starter=30)
        self.assertEqual(balance, 65)
        self.assertEqual(self._credit_row(internal_id), (65, 1))

    def test_schema_is_idempotent(self) -> None:
        metrics.init_db(self.db_path)
        internal_id = metrics.ensure_user_identity("max", "42")

        self.assertLess(internal_id, 0)


if __name__ == "__main__":
    unittest.main()
