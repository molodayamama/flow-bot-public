"""Tests for the SQLite-backed credit store functions in metrics.py and
the CreditStoreSQLite / make_credit_store wrappers in flow_core.py.

Each test uses a fresh temporary DB so there is no cross-test state.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import metrics
from flow_core import CreditStore, CreditStoreSQLite, make_credit_store, STARTER_CREDITS


class CreditsTestBase(unittest.TestCase):
    """Fresh temp DB per test, same pattern as MetricsTestBase in test_metrics.py."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / "metrics.db")
        metrics.close()
        metrics.init_db(self.db_path)

    def tearDown(self) -> None:
        metrics.close()
        self._tmp.cleanup()

    def _row(self, user_id: int):
        conn = metrics._conn()
        row = conn.execute(
            "SELECT balance, granted FROM credits WHERE user_id=?", (user_id,)
        ).fetchone()
        return tuple(row) if row is not None else None

    def _count(self) -> int:
        conn = metrics._conn()
        return conn.execute("SELECT COUNT(*) FROM credits").fetchone()[0]


# ── credits_balance ────────────────────────────────────────────────────


class CreditsBalanceTests(CreditsTestBase):
    def test_first_call_grants_starter_and_sets_granted_flag(self) -> None:
        bal = metrics.credits_balance(42, STARTER_CREDITS)
        self.assertEqual(bal, STARTER_CREDITS)
        row = self._row(42)
        self.assertIsNotNone(row)
        self.assertEqual(row[0], STARTER_CREDITS)
        self.assertEqual(row[1], 1)  # granted flag set

    def test_second_call_does_not_re_grant_starter(self) -> None:
        metrics.credits_balance(42, STARTER_CREDITS)
        bal2 = metrics.credits_balance(42, STARTER_CREDITS)
        self.assertEqual(bal2, STARTER_CREDITS)
        self.assertEqual(self._count(), 1)  # still one row

    def test_starter_grant_is_idempotent_across_calls(self) -> None:
        # Multiple calls must not accumulate starter credits.
        for _ in range(5):
            metrics.credits_balance(7, STARTER_CREDITS)
        row = self._row(7)
        self.assertEqual(row[0], STARTER_CREDITS)

    def test_returns_zero_on_broken_db(self) -> None:
        metrics._CONN.close()
        try:
            result = metrics.credits_balance(99, STARTER_CREDITS)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"credits_balance raised on broken DB: {exc!r}")
        self.assertEqual(result, 0)
        metrics.close()


# ── credits_charge ─────────────────────────────────────────────────────


class CreditsChargeTests(CreditsTestBase):
    def test_charge_deducts_and_returns_true(self) -> None:
        metrics.credits_balance(1, 30)  # balance = 30
        ok = metrics.credits_charge(1, 10, 30)
        self.assertTrue(ok)
        self.assertEqual(self._row(1)[0], 20)

    def test_charge_fails_when_insufficient(self) -> None:
        metrics.credits_balance(1, 10)  # balance = 10
        ok = metrics.credits_charge(1, 20, 10)
        self.assertFalse(ok)
        self.assertEqual(self._row(1)[0], 10)  # unchanged

    def test_charge_zero_is_noop_and_returns_true(self) -> None:
        metrics.credits_balance(1, 30)
        ok = metrics.credits_charge(1, 0, 30)
        self.assertTrue(ok)
        self.assertEqual(self._row(1)[0], 30)

    def test_charge_grants_starter_on_first_call(self) -> None:
        # User never called balance; charge must still grant starter first.
        ok = metrics.credits_charge(5, 10, 30)
        self.assertTrue(ok)
        self.assertEqual(self._row(5)[0], 20)

    def test_charge_returns_false_on_broken_db(self) -> None:
        metrics.credits_balance(1, 30)
        metrics._CONN.close()
        try:
            result = metrics.credits_charge(1, 5, 30)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"credits_charge raised on broken DB: {exc!r}")
        self.assertFalse(result)
        metrics.close()


# ── credits_refund ─────────────────────────────────────────────────────


class CreditsRefundTests(CreditsTestBase):
    def test_refund_adds_back_credits(self) -> None:
        metrics.credits_balance(2, 30)
        metrics.credits_charge(2, 10, 30)  # balance = 20
        metrics.credits_refund(2, 10)       # balance = 30
        self.assertEqual(self._row(2)[0], 30)

    def test_refund_zero_is_noop(self) -> None:
        metrics.credits_balance(2, 30)
        metrics.credits_refund(2, 0)
        self.assertEqual(self._row(2)[0], 30)

    def test_refund_never_raises(self) -> None:
        metrics._CONN.close()
        try:
            metrics.credits_refund(99, 10)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"credits_refund raised: {exc!r}")
        metrics.close()


# ── credits_add ────────────────────────────────────────────────────────


class CreditsAddTests(CreditsTestBase):
    def test_add_tops_up_and_returns_new_balance(self) -> None:
        metrics.credits_balance(3, 30)  # 30
        new_bal = metrics.credits_add(3, 100, 30)
        self.assertEqual(new_bal, 130)
        self.assertEqual(self._row(3)[0], 130)

    def test_add_grants_starter_on_new_user(self) -> None:
        new_bal = metrics.credits_add(4, 50, 30)
        # starter (30) applied first, then +50 = 80
        self.assertEqual(new_bal, 80)

    def test_add_returns_zero_on_broken_db(self) -> None:
        metrics.credits_balance(3, 30)
        metrics._CONN.close()
        try:
            result = metrics.credits_add(3, 50, 30)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"credits_add raised on broken DB: {exc!r}")
        self.assertEqual(result, 0)
        metrics.close()


# ── credits_migrate_from_json ──────────────────────────────────────────


class CreditsMigrateTests(CreditsTestBase):
    def _write_json(self, data: dict) -> str:
        path = str(Path(self._tmp.name) / "user_credits.json")
        Path(path).write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_migrates_balances_and_granted(self) -> None:
        path = self._write_json({
            "balances": {"10": 50, "20": 75},
            "granted": ["10", "20"],
        })
        count = metrics.credits_migrate_from_json(path)
        self.assertEqual(count, 2)
        self.assertEqual(self._row(10), (50, 1))
        self.assertEqual(self._row(20), (75, 1))

    def test_granted_flag_false_for_ungranted_user(self) -> None:
        path = self._write_json({
            "balances": {"10": 50, "20": 75},
            "granted": ["10"],
        })
        metrics.credits_migrate_from_json(path)
        self.assertEqual(self._row(10)[1], 1)
        self.assertEqual(self._row(20)[1], 0)

    def test_idempotent_second_call_skips_existing_rows(self) -> None:
        path = self._write_json({"balances": {"10": 50}, "granted": ["10"]})
        first = metrics.credits_migrate_from_json(path)
        second = metrics.credits_migrate_from_json(path)
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)  # INSERT OR IGNORE, no new rows
        self.assertEqual(self._count(), 1)

    def test_missing_file_returns_zero(self) -> None:
        count = metrics.credits_migrate_from_json("/nonexistent/path.json")
        self.assertEqual(count, 0)

    def test_empty_json_returns_zero(self) -> None:
        path = str(Path(self._tmp.name) / "empty.json")
        Path(path).write_text("{}", encoding="utf-8")
        count = metrics.credits_migrate_from_json(path)
        self.assertEqual(count, 0)

    def test_invalid_json_returns_zero(self) -> None:
        path = str(Path(self._tmp.name) / "bad.json")
        Path(path).write_text("not json!", encoding="utf-8")
        count = metrics.credits_migrate_from_json(path)
        self.assertEqual(count, 0)


# ── CreditStoreSQLite ──────────────────────────────────────────────────


class CreditStoreSQLiteTests(CreditsTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.store = CreditStoreSQLite(path="ignored.json", starter=30)

    def test_balance_grants_starter_once(self) -> None:
        bal = self.store.balance(100)
        self.assertEqual(bal, 30)
        bal2 = self.store.balance(100)
        self.assertEqual(bal2, 30)

    def test_can_afford(self) -> None:
        self.assertTrue(self.store.can_afford(100, 30))
        self.assertFalse(self.store.can_afford(100, 31))

    def test_charge_and_refund_cycle(self) -> None:
        self.store.balance(100)         # ensure starter applied (balance=30)
        self.assertTrue(self.store.charge(100, 10))
        self.assertEqual(self.store.balance(100), 20)
        self.store.refund(100, 10)
        self.assertEqual(self.store.balance(100), 30)

    def test_charge_fails_when_broke(self) -> None:
        self.store.balance(100)  # 30
        self.assertFalse(self.store.charge(100, 31))

    def test_add_returns_new_balance(self) -> None:
        new_bal = self.store.add(100, 70)
        self.assertEqual(new_bal, 100)  # 30 starter + 70

    def test_path_param_ignored(self) -> None:
        # Changing the path arg must not change behaviour.
        s = CreditStoreSQLite(path="some/other/path.json", starter=30)
        bal = s.balance(200)
        self.assertEqual(bal, 30)


# ── make_credit_store factory ──────────────────────────────────────────


class MakeCreditStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._json_path = str(Path(self._tmp.name) / "credits.json")
        # Close any existing metrics connection; tests that use SQLite store
        # need a fresh DB.
        metrics.close()
        os.environ.pop("CREDITS_SQLITE", None)

    def tearDown(self) -> None:
        metrics.close()
        os.environ.pop("CREDITS_SQLITE", None)
        self._tmp.cleanup()

    def test_default_returns_json_store(self) -> None:
        store = make_credit_store(self._json_path)
        self.assertIsInstance(store, CreditStore)

    def test_env_one_returns_sqlite_store(self) -> None:
        os.environ["CREDITS_SQLITE"] = "1"
        store = make_credit_store(self._json_path)
        self.assertIsInstance(store, CreditStoreSQLite)

    def test_explicit_true_returns_sqlite(self) -> None:
        store = make_credit_store(self._json_path, use_sqlite=True)
        self.assertIsInstance(store, CreditStoreSQLite)

    def test_explicit_false_returns_json(self) -> None:
        os.environ["CREDITS_SQLITE"] = "1"  # env says SQLite, but explicit wins
        store = make_credit_store(self._json_path, use_sqlite=False)
        self.assertIsInstance(store, CreditStore)

    def test_sqlite_store_works_end_to_end(self) -> None:
        db_path = str(Path(self._tmp.name) / "m.db")
        metrics.init_db(db_path)
        store = make_credit_store(self._json_path, use_sqlite=True)
        bal = store.balance(1)
        self.assertEqual(bal, STARTER_CREDITS)
        self.assertTrue(store.charge(1, 10))
        self.assertEqual(store.balance(1), STARTER_CREDITS - 10)
        store.refund(1, 5)
        self.assertEqual(store.balance(1), STARTER_CREDITS - 5)
        new_bal = store.add(1, 100)
        self.assertEqual(new_bal, STARTER_CREDITS - 5 + 100)


if __name__ == "__main__":
    unittest.main()
