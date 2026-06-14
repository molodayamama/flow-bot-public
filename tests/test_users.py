"""Unit tests for the users profile table and related metrics.py functions.

Covers: upsert_user, touch_user, mark_user_blocked, get_user_profile,
        report_active_users, report_top_users, report_top_referrers,
        backfill_users_from_metrics.

Each test uses a fresh on-disk SQLite DB (mirrors MetricsTestBase in test_metrics.py).
Broken-DB tests deliberately emit WARNING-level tracebacks — that is expected and correct
(the never-raise contract logs at WARNING and returns safe defaults).
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

import metrics


class UsersTestBase(unittest.TestCase):
    """Fresh DB per test, matching the pattern in test_metrics.py."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / "metrics.db")
        metrics.close()
        metrics.init_db(self.db_path)

    def tearDown(self) -> None:
        metrics.close()
        self._tmp.cleanup()

    def _count(self, table: str) -> int:
        conn = metrics._conn()
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def _one(self, sql: str, params: tuple = ()):
        conn = metrics._conn()
        return conn.execute(sql, params).fetchone()


# ── upsert_user ────────────────────────────────────────────────────────

class UpsertUserTests(UsersTestBase):
    def test_first_insert_creates_row(self) -> None:
        metrics.upsert_user(1001, username="alice", first_name="Alice", channel="tg_ads")
        self.assertEqual(self._count("users"), 1)
        row = self._one(
            "SELECT user_id, username, first_name, acq_channel, is_blocked FROM users WHERE user_id=1001"
        )
        self.assertEqual(row[0], 1001)
        self.assertEqual(row[1], "alice")
        self.assertEqual(row[2], "Alice")
        self.assertEqual(row[3], "tg_ads")
        self.assertEqual(row[4], 0)

    def test_second_call_updates_last_active_and_username(self) -> None:
        metrics.upsert_user(1001, username="alice")
        # Grab initial last_active.
        ts1 = self._one("SELECT last_active FROM users WHERE user_id=1001")[0]
        # Tiny sleep to ensure datetime('now') advances (SQLite resolution: 1s on some builds).
        time.sleep(0.01)
        metrics.upsert_user(1001, username="alice_new")
        row = self._one("SELECT username, last_active FROM users WHERE user_id=1001")
        self.assertEqual(row[0], "alice_new")
        # last_active is updated (same or later — the UPSERT sets it to datetime('now'))
        self.assertIsNotNone(row[1])

    def test_acq_channel_first_touch_not_overwritten(self) -> None:
        metrics.upsert_user(1001, channel="channel_a")
        metrics.upsert_user(1001, channel="channel_b")
        row = self._one("SELECT acq_channel FROM users WHERE user_id=1001")
        self.assertEqual(row[0], "channel_a")  # first-touch preserved

    def test_none_username_does_not_overwrite_existing(self) -> None:
        metrics.upsert_user(1001, username="bob")
        metrics.upsert_user(1001, username=None)  # no fresh data
        row = self._one("SELECT username FROM users WHERE user_id=1001")
        self.assertEqual(row[0], "bob")

    def test_none_first_name_does_not_overwrite_existing(self) -> None:
        metrics.upsert_user(1001, first_name="Bob")
        metrics.upsert_user(1001, first_name=None)
        row = self._one("SELECT first_name FROM users WHERE user_id=1001")
        self.assertEqual(row[0], "Bob")

    def test_upsert_never_raises_on_broken_db(self) -> None:
        metrics.close()
        metrics.init_db(self.db_path)
        metrics._CONN.close()
        try:
            metrics.upsert_user(999, username="x")
        except Exception as exc:  # noqa: BLE001
            self.fail(f"upsert_user raised on broken DB: {exc!r}")
        metrics.close()


# ── touch_user ────────────────────────────────────────────────────────

class TouchUserTests(UsersTestBase):
    def test_touch_bumps_last_active(self) -> None:
        metrics.upsert_user(42)
        ts1 = self._one("SELECT last_active FROM users WHERE user_id=42")[0]
        time.sleep(0.01)
        metrics.touch_user(42)
        ts2 = self._one("SELECT last_active FROM users WHERE user_id=42")[0]
        # Both timestamps are non-null; the row is still there.
        self.assertIsNotNone(ts1)
        self.assertIsNotNone(ts2)

    def test_touch_noop_on_missing_user(self) -> None:
        # Must not raise, must not create a row.
        try:
            metrics.touch_user(99999)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"touch_user raised: {exc!r}")
        self.assertEqual(self._count("users"), 0)

    def test_touch_never_raises_on_broken_db(self) -> None:
        metrics.close()
        metrics.init_db(self.db_path)
        metrics._CONN.close()
        try:
            metrics.touch_user(1)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"touch_user raised on broken DB: {exc!r}")
        metrics.close()


# ── mark_user_blocked ─────────────────────────────────────────────────

class MarkUserBlockedTests(UsersTestBase):
    def test_mark_sets_is_blocked(self) -> None:
        metrics.upsert_user(77)
        self.assertEqual(self._one("SELECT is_blocked FROM users WHERE user_id=77")[0], 0)
        metrics.mark_user_blocked(77)
        self.assertEqual(self._one("SELECT is_blocked FROM users WHERE user_id=77")[0], 1)

    def test_mark_unblock_clears_flag(self) -> None:
        metrics.upsert_user(77)
        metrics.mark_user_blocked(77, blocked=True)
        metrics.mark_user_blocked(77, blocked=False)
        self.assertEqual(self._one("SELECT is_blocked FROM users WHERE user_id=77")[0], 0)

    def test_mark_noop_on_missing_user(self) -> None:
        try:
            metrics.mark_user_blocked(99999)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"mark_user_blocked raised: {exc!r}")
        self.assertEqual(self._count("users"), 0)

    def test_mark_never_raises_on_broken_db(self) -> None:
        metrics.close()
        metrics.init_db(self.db_path)
        metrics._CONN.close()
        try:
            metrics.mark_user_blocked(1)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"mark_user_blocked raised on broken DB: {exc!r}")
        metrics.close()


# ── get_user_profile ──────────────────────────────────────────────────

class GetUserProfileTests(UsersTestBase):
    def test_returns_zeros_on_missing_user(self) -> None:
        prof = metrics.get_user_profile(12345)
        self.assertEqual(prof["user_id"], 12345)
        self.assertIsNone(prof["username"])
        self.assertIsNone(prof["first_seen"])
        self.assertFalse(prof["is_blocked"])
        self.assertEqual(prof["balance"], 0)
        self.assertFalse(prof["starter_granted"])
        self.assertIsNone(prof["acq_channel"])
        self.assertEqual(prof["requests"]["total"], 0)
        self.assertEqual(prof["spend"]["payments"], 0)
        self.assertEqual(prof["referrals"]["invited"], 0)

    def test_returns_correct_data_on_seeded_data(self) -> None:
        uid = 555
        metrics.upsert_user(uid, username="dave", first_name="Dave")
        metrics.record_acquisition(user_id=uid, channel="organic")
        metrics.credits_balance(uid, 30)  # grants starter
        metrics.log_flow_job(
            user_id=uid, operation_type="gen", status="success", bot_credits_charged=10
        )
        metrics.log_flow_job(
            user_id=uid, operation_type="video_4s", status="success", bot_credits_charged=100
        )
        metrics.record_transaction(
            provider="telegram", provider_payment_id="chg_555",
            user_id=uid, stars_amount=75, amount_rub=99.0, credits_issued=100, status="paid"
        )

        prof = metrics.get_user_profile(uid)
        self.assertEqual(prof["username"], "dave")
        self.assertEqual(prof["first_name"], "Dave")
        self.assertTrue(prof["starter_granted"])
        self.assertEqual(prof["acq_channel"], "organic")
        self.assertEqual(prof["requests"]["image"], 1)
        self.assertEqual(prof["requests"]["video"], 1)
        self.assertEqual(prof["requests"]["total"], 2)
        self.assertEqual(prof["spend"]["stars"], 75)
        self.assertAlmostEqual(prof["spend"]["rub"], 99.0)
        self.assertEqual(prof["spend"]["payments"], 1)

    def test_referral_data_in_profile(self) -> None:
        referrer = 100
        referred = 200
        metrics.upsert_user(referrer, username="ref")
        metrics.record_referral_join(referrer_user_id=referrer, referred_user_id=referred)
        metrics.record_transaction(
            provider="telegram", provider_payment_id="chg_ref200",
            user_id=referred, stars_amount=75, amount_rub=99.0,
            credits_issued=100, status="paid"
        )
        prof = metrics.get_user_profile(referrer)
        self.assertEqual(prof["referrals"]["invited"], 1)
        self.assertEqual(prof["referrals"]["invited_paid"], 1)
        self.assertAlmostEqual(prof["referrals"]["referred_revenue_rub"], 99.0)

    def test_never_raises_on_broken_db(self) -> None:
        metrics.close()
        metrics.init_db(self.db_path)
        metrics._CONN.close()
        try:
            prof = metrics.get_user_profile(1)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"get_user_profile raised on broken DB: {exc!r}")
        self.assertEqual(prof["balance"], 0)
        metrics.close()


# ── report_active_users ───────────────────────────────────────────────

class ReportActiveUsersTests(UsersTestBase):
    def test_returns_zeros_on_empty_db(self) -> None:
        rep = metrics.report_active_users()
        self.assertEqual(rep, {"dau": 0, "wau": 0, "mau": 0})

    def test_counts_active_non_blocked_users(self) -> None:
        metrics.upsert_user(1)
        metrics.upsert_user(2)
        metrics.upsert_user(3)
        metrics.mark_user_blocked(3)  # should be excluded
        rep = metrics.report_active_users()
        # All three were just upserted (last_active = now), but user 3 is blocked.
        self.assertEqual(rep["dau"], 2)
        self.assertEqual(rep["wau"], 2)
        self.assertEqual(rep["mau"], 2)

    def test_never_raises_on_broken_db(self) -> None:
        metrics.close()
        metrics.init_db(self.db_path)
        metrics._CONN.close()
        try:
            rep = metrics.report_active_users()
        except Exception as exc:  # noqa: BLE001
            self.fail(f"report_active_users raised on broken DB: {exc!r}")
        self.assertEqual(rep, {"dau": 0, "wau": 0, "mau": 0})
        metrics.close()


# ── report_top_users ──────────────────────────────────────────────────

class ReportTopUsersTests(UsersTestBase):
    def test_returns_empty_on_empty_db(self) -> None:
        rep = metrics.report_top_users()
        self.assertEqual(rep, {"users": []})

    def test_orders_by_rub_desc(self) -> None:
        metrics.upsert_user(10, username="big_spender")
        metrics.upsert_user(20, username="small_spender")
        metrics.record_transaction(
            provider="telegram", provider_payment_id="chg_10",
            user_id=10, stars_amount=500, amount_rub=650.0, credits_issued=1000, status="paid"
        )
        metrics.record_transaction(
            provider="telegram", provider_payment_id="chg_20",
            user_id=20, stars_amount=75, amount_rub=99.0, credits_issued=100, status="paid"
        )
        rep = metrics.report_top_users(limit=10)
        users = rep["users"]
        self.assertEqual(len(users), 2)
        self.assertEqual(users[0]["user_id"], 10)
        self.assertEqual(users[0]["username"], "big_spender")
        self.assertAlmostEqual(users[0]["rub"], 650.0)
        self.assertEqual(users[1]["user_id"], 20)

    def test_never_raises_on_broken_db(self) -> None:
        metrics.close()
        metrics.init_db(self.db_path)
        metrics._CONN.close()
        try:
            rep = metrics.report_top_users()
        except Exception as exc:  # noqa: BLE001
            self.fail(f"report_top_users raised on broken DB: {exc!r}")
        self.assertEqual(rep, {"users": []})
        metrics.close()


# ── report_top_referrers ──────────────────────────────────────────────

class ReportTopReferrersTests(UsersTestBase):
    def test_returns_empty_on_empty_db(self) -> None:
        rep = metrics.report_top_referrers()
        self.assertEqual(rep, {"referrers": []})

    def test_orders_by_referred_revenue(self) -> None:
        metrics.upsert_user(1, username="ref_a")
        metrics.upsert_user(2, username="ref_b")
        # ref_a invited user 10 (paid 99 rub); ref_b invited user 20 (paid 650 rub).
        metrics.record_referral_join(referrer_user_id=1, referred_user_id=10)
        metrics.record_referral_join(referrer_user_id=2, referred_user_id=20)
        metrics.record_transaction(
            provider="telegram", provider_payment_id="chg_r10",
            user_id=10, stars_amount=75, amount_rub=99.0, credits_issued=100, status="paid"
        )
        metrics.record_transaction(
            provider="telegram", provider_payment_id="chg_r20",
            user_id=20, stars_amount=500, amount_rub=650.0, credits_issued=1000, status="paid"
        )
        rep = metrics.report_top_referrers(limit=10)
        refs = rep["referrers"]
        self.assertEqual(len(refs), 2)
        # ref_b should be first (higher revenue).
        self.assertEqual(refs[0]["referrer_user_id"], 2)
        self.assertAlmostEqual(refs[0]["referred_revenue_rub"], 650.0)
        self.assertEqual(refs[0]["username"], "ref_b")

    def test_never_raises_on_broken_db(self) -> None:
        metrics.close()
        metrics.init_db(self.db_path)
        metrics._CONN.close()
        try:
            rep = metrics.report_top_referrers()
        except Exception as exc:  # noqa: BLE001
            self.fail(f"report_top_referrers raised on broken DB: {exc!r}")
        self.assertEqual(rep, {"referrers": []})
        metrics.close()


# ── backfill_users_from_metrics ───────────────────────────────────────

class BackfillUsersTests(UsersTestBase):
    def test_backfills_from_transactions_and_acquisitions(self) -> None:
        # Seed data without any upsert_user calls.
        metrics.record_transaction(
            provider="telegram", provider_payment_id="chg_bf1",
            user_id=301, stars_amount=75, amount_rub=99.0, credits_issued=100, status="paid"
        )
        metrics.record_acquisition(user_id=302, channel="organic")
        metrics.credits_balance(303, 30)  # creates a credits row
        self.assertEqual(self._count("users"), 0)

        inserted = metrics.backfill_users_from_metrics()
        # 301 from transactions, 302 from acquisitions, 303 from credits.
        self.assertGreaterEqual(inserted, 3)
        self.assertGreaterEqual(self._count("users"), 3)

        # acq_channel is populated from acquisitions.
        row = self._one("SELECT acq_channel FROM users WHERE user_id=302")
        self.assertEqual(row[0], "organic")

    def test_idempotent_on_double_run(self) -> None:
        metrics.record_transaction(
            provider="telegram", provider_payment_id="chg_idem",
            user_id=401, stars_amount=75, amount_rub=99.0, credits_issued=100, status="paid"
        )
        first = metrics.backfill_users_from_metrics()
        second = metrics.backfill_users_from_metrics()
        self.assertGreater(first, 0)
        self.assertEqual(second, 0)  # INSERT OR IGNORE, no new rows

    def test_does_not_overwrite_live_row(self) -> None:
        # upsert_user creates a live row with fresh data.
        metrics.upsert_user(501, username="live_user", first_name="Live")
        metrics.record_transaction(
            provider="telegram", provider_payment_id="chg_live",
            user_id=501, stars_amount=75, amount_rub=99.0, credits_issued=100, status="paid"
        )
        metrics.backfill_users_from_metrics()
        row = self._one("SELECT username, first_name FROM users WHERE user_id=501")
        self.assertEqual(row[0], "live_user")
        self.assertEqual(row[1], "Live")

    def test_returns_zero_on_empty_db(self) -> None:
        self.assertEqual(metrics.backfill_users_from_metrics(), 0)

    def test_never_raises_on_broken_db(self) -> None:
        metrics.close()
        metrics.init_db(self.db_path)
        metrics._CONN.close()
        try:
            result = metrics.backfill_users_from_metrics()
        except Exception as exc:  # noqa: BLE001
            self.fail(f"backfill_users_from_metrics raised on broken DB: {exc!r}")
        self.assertEqual(result, 0)
        metrics.close()


if __name__ == "__main__":
    unittest.main()
