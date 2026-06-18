from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import metrics


class MetricsTestBase(unittest.TestCase):
    """Each test runs against a fresh on-disk DB in its own temp directory.

    ``metrics`` keeps a single module-global connection, so we always
    ``close()`` it and re-``init_db`` against a brand new file per test to
    guarantee isolation (mirrors the temp-dir-per-test style of the flow tests).
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / "metrics.db")
        metrics.close()
        metrics.init_db(self.db_path)

    def tearDown(self) -> None:
        metrics.close()
        self._tmp.cleanup()

    # ── small query helpers (direct, bypassing the module lock for reads) ──
    def _count(self, table: str) -> int:
        conn = metrics._conn()
        with conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def _one(self, sql: str, params: tuple = ()):  # noqa: ANN001
        conn = metrics._conn()
        return conn.execute(sql, params).fetchone()


class EventTests(MetricsTestBase):
    def test_log_event_inserts_row_and_roundtrips_payload(self) -> None:
        metrics.log_event(
            "image_requested",
            user_id=7,
            username="alice",
            source="wizard",
            payload={"prompt": "кот", "n": 4},
        )
        self.assertEqual(self._count("events"), 1)
        row = self._one(
            "SELECT event_name, user_id, username, source, payload_json FROM events"
        )
        self.assertEqual(row[0], "image_requested")
        self.assertEqual(row[1], 7)
        self.assertEqual(row[2], "alice")
        self.assertEqual(row[3], "wizard")
        # Payload round-trips through payload_json (ensure_ascii=False kept utf-8).
        import json
        self.assertEqual(json.loads(row[4]), {"prompt": "кот", "n": 4})

    def test_events_retention_purges_old_rows_on_init(self) -> None:
        # PII-ретеншн: username в events — персональные данные; init_db чистит
        # строки старше METRICS_EVENTS_RETENTION_DAYS (деф. 90), свежие остаются.
        metrics.log_event("fresh", user_id=1, username="alice")
        conn = metrics._conn()
        with conn:
            conn.execute(
                "INSERT INTO events (event_name, username, created_at) "
                "VALUES ('ancient', 'bob', datetime('now', '-365 day'))"
            )
        self.assertEqual(self._count("events"), 2)
        metrics.close()
        metrics.init_db(self.db_path)  # re-open → purge runs
        self.assertEqual(self._count("events"), 1)
        self.assertEqual(self._one("SELECT event_name FROM events")[0], "fresh")

    def test_log_event_optional_args_are_fine(self) -> None:
        metrics.log_event("user_started")  # no user_id/username/source/payload
        self.assertEqual(self._count("events"), 1)
        row = self._one("SELECT user_id, username, source, payload_json FROM events")
        self.assertEqual(tuple(row), (None, None, None, None))

    def test_log_event_never_raises_on_bad_payload(self) -> None:
        # A non-serializable payload must NOT raise; the event may be stored
        # without its body (payload_json NULL).
        try:
            metrics.log_event("weird", user_id=1, payload={"x": {1, 2, 3}})
        except Exception as exc:  # noqa: BLE001
            self.fail(f"log_event raised on bad payload: {exc!r}")
        row = self._one("SELECT event_name, payload_json FROM events")
        self.assertEqual(row[0], "weird")
        self.assertIsNone(row[1])

    def test_log_event_never_raises_on_closed_db(self) -> None:
        # Even if the DB is closed underneath it, logging must swallow the error.
        metrics.close()
        # Re-init against a path inside a now-deleted dir would still be handled,
        # but the clearest "broken DB" is a closed connection during the call.
        try:
            # Point at a fresh DB so _conn lazily reopens, then break it.
            metrics.init_db(self.db_path)
            metrics._CONN.close()  # simulate a torn-down connection
            metrics.log_event("after_close", user_id=1)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"log_event raised on closed DB: {exc!r}")


class FlowJobTests(MetricsTestBase):
    def test_flow_job_roundtrip_computes_delta(self) -> None:
        metrics.log_flow_job(
            user_id=7,
            account_id="acc-1",
            operation_type="image_generate",
            model="GEM_PIX_2",
            bot_credits_charged=10,
            flow_credits_before=1000,
            flow_credits_after=993,
            duration_ms=1234,
            status="success",
        )
        row = self._one(
            "SELECT flow_credits_before, flow_credits_after, flow_credits_delta, "
            "bot_credits_charged, status FROM flow_jobs"
        )
        self.assertEqual(row[0], 1000)
        self.assertEqual(row[1], 993)
        self.assertEqual(row[2], -7)  # delta = after - before
        self.assertEqual(row[3], 10)
        self.assertEqual(row[4], "success")

        # The same delta surfaces through report_flow.
        rep = metrics.report_flow()
        by_model = {m["model"]: m for m in rep["by_model_credits"]}
        self.assertEqual(by_model["GEM_PIX_2"]["flow_credits_delta_sum"], -7)
        self.assertEqual(rep["success_rate"], 1.0)

    def test_flow_job_delta_null_when_one_side_missing(self) -> None:
        metrics.log_flow_job(operation_type="edit", flow_credits_before=500)
        row = self._one("SELECT flow_credits_delta FROM flow_jobs")
        self.assertIsNone(row[0])

    def test_log_flow_job_never_raises(self) -> None:
        metrics.close()
        metrics.init_db(self.db_path)
        metrics._CONN.close()
        try:
            metrics.log_flow_job(operation_type="image_generate")
        except Exception as exc:  # noqa: BLE001
            self.fail(f"log_flow_job raised on broken DB: {exc!r}")


class TransactionTests(MetricsTestBase):
    def test_record_transaction_is_idempotent(self) -> None:
        first = metrics.record_transaction(
            provider="telegram",
            provider_payment_id="charge_ABC",
            user_id=7,
            package_id="large",
            stars_amount=450,
            credits_issued=700,
        )
        second = metrics.record_transaction(
            provider="telegram",
            provider_payment_id="charge_ABC",  # same id → duplicate webhook
            user_id=7,
            package_id="large",
            stars_amount=450,
            credits_issued=700,
        )
        self.assertTrue(first)   # newly inserted
        self.assertFalse(second)  # already existed, ignored
        # Exactly one row; credits counted once.
        self.assertEqual(self._count("transactions"), 1)
        total = self._one(
            "SELECT COALESCE(SUM(credits_issued),0) FROM transactions"
        )[0]
        self.assertEqual(total, 700)

    def test_record_transaction_distinct_ids_make_two_rows(self) -> None:
        self.assertTrue(metrics.record_transaction(
            provider="telegram", provider_payment_id="chg_1", user_id=1,
            stars_amount=35, credits_issued=45))
        self.assertTrue(metrics.record_transaction(
            provider="telegram", provider_payment_id="chg_2", user_id=1,
            stars_amount=75, credits_issued=100))
        self.assertEqual(self._count("transactions"), 2)

    def test_paid_at_defaults_when_status_paid(self) -> None:
        metrics.record_transaction(
            provider="telegram", provider_payment_id="chg_paid", user_id=1,
            stars_amount=35, credits_issued=45, status="paid")
        row = self._one("SELECT status, paid_at FROM transactions")
        self.assertEqual(row[0], "paid")
        self.assertIsNotNone(row[1])  # auto-stamped

    def test_record_transaction_status_tri_state(self) -> None:
        # 'new' → 'duplicate' on the same id; 'error' on a broken DB. The payment
        # handler credits on 'new', skips on 'duplicate', credits-but-logs on
        # 'error' (paid stars must not be hostage to a metrics outage).
        first = metrics.record_transaction_status(
            provider="telegram", provider_payment_id="chg_tri", user_id=3,
            stars_amount=75, credits_issued=100)
        second = metrics.record_transaction_status(
            provider="telegram", provider_payment_id="chg_tri", user_id=3,
            stars_amount=75, credits_issued=100)
        self.assertEqual(first, "new")
        self.assertEqual(second, "duplicate")
        metrics._CONN.close()  # simulate metrics outage
        broken = metrics.record_transaction_status(
            provider="telegram", provider_payment_id="chg_tri2", user_id=3,
            stars_amount=75, credits_issued=100)
        self.assertEqual(broken, "error")
        metrics.close()  # reset for tearDown


class ReferralTests(MetricsTestBase):
    def test_referral_join_idempotent_on_referred_user(self) -> None:
        self.assertTrue(metrics.record_referral_join(referrer_user_id=1, referred_user_id=2))
        # Same referred user again → ignored, even from a different referrer.
        self.assertFalse(metrics.record_referral_join(referrer_user_id=9, referred_user_id=2))
        self.assertEqual(self._count("referrals"), 1)
        row = self._one("SELECT referrer_user_id, status FROM referrals")
        self.assertEqual(row[0], 1)       # original referrer kept
        self.assertEqual(row[1], "joined")

    def test_self_referral_rejected_without_row(self) -> None:
        self.assertFalse(metrics.record_referral_join(referrer_user_id=5, referred_user_id=5))
        self.assertEqual(self._count("referrals"), 0)

    def test_mark_referral_rewarded(self) -> None:
        metrics.record_referral_join(referrer_user_id=1, referred_user_id=2)
        metrics.mark_referral_rewarded(
            referred_user_id=2, reward_credits=50, first_payment_transaction_id=11)
        row = self._one(
            "SELECT status, reward_credits, first_payment_transaction_id, rewarded_at "
            "FROM referrals WHERE referred_user_id=2"
        )
        self.assertEqual(row[0], "rewarded")
        self.assertEqual(row[1], 50)
        self.assertEqual(row[2], 11)
        self.assertIsNotNone(row[3])

        rep = metrics.report_refs()
        self.assertEqual(rep["total_referrals"], 1)
        self.assertEqual(rep["rewarded"], 1)
        self.assertEqual(rep["total_reward_credits"], 50)

    def test_grant_milestone_if_joined_claims_exactly_once(self) -> None:
        # Атомарный joined→rewarded: второй конкурентный платёж бонус не получает.
        metrics.record_referral_join(referrer_user_id=1, referred_user_id=2)
        first = metrics.grant_milestone_if_joined(referred_user_id=2, reward_credits=50)
        second = metrics.grant_milestone_if_joined(referred_user_id=2, reward_credits=50)
        self.assertTrue(first)
        self.assertFalse(second)
        row = self._one(
            "SELECT status, reward_credits FROM referrals WHERE referred_user_id=2"
        )
        self.assertEqual(row[0], "rewarded")
        self.assertEqual(row[1], 50)

    def test_grant_milestone_without_join_is_noop(self) -> None:
        self.assertFalse(metrics.grant_milestone_if_joined(referred_user_id=99, reward_credits=50))
        self.assertEqual(self._count("referrals"), 0)

    def test_referral_query_helpers(self) -> None:
        metrics.record_referral_join(referrer_user_id=1, referred_user_id=2)
        self.assertEqual(metrics.get_referrer_of(2), 1)
        self.assertIsNone(metrics.get_referrer_of(999))
        self.assertEqual(metrics.referral_status(2), "joined")
        self.assertIsNone(metrics.referral_status(999))
        stats = metrics.referral_stats(1)
        self.assertEqual(stats["invited"], 1)
        self.assertEqual(stats["earned"], 0)

    def test_referral_is_active_respects_window(self) -> None:
        metrics.record_referral_join(referrer_user_id=1, referred_user_id=2)
        # Fresh join → inside any positive window.
        self.assertTrue(metrics.referral_is_active(2, 90))
        # No referral row → not active.
        self.assertFalse(metrics.referral_is_active(999, 90))
        # Backdate the join past the window → attribution expired.
        with metrics._LOCK:
            metrics._conn().execute(
                "UPDATE referrals SET created_at=datetime('now','-100 days') "
                "WHERE referred_user_id=2"
            )
            metrics._conn().commit()
        self.assertFalse(metrics.referral_is_active(2, 90))
        self.assertTrue(metrics.referral_is_active(2, 200))  # wider window still covers it

    def test_ongoing_reward_idempotent_and_capped_lookup(self) -> None:
        # First insert wins; duplicate payment id is ignored (no double reward).
        self.assertTrue(metrics.record_ongoing_reward(1, 2, 29, "charge-A"))
        self.assertFalse(metrics.record_ongoing_reward(1, 2, 29, "charge-A"))
        self.assertEqual(self._count("referral_ongoing_rewards"), 1)
        row = metrics.get_ongoing_reward_by_payment("charge-A")
        self.assertEqual(row["referrer_user_id"], 1)
        self.assertEqual(row["reward_credits"], 29)
        self.assertIsNone(metrics.get_ongoing_reward_by_payment("nope"))
        # Counts toward the referrer's daily total (for cap enforcement).
        self.assertEqual(metrics.get_referral_credits_today(1), 29)

    def test_referral_clawback_reset(self) -> None:
        metrics.record_referral_join(referrer_user_id=1, referred_user_id=2)
        metrics.mark_referral_rewarded(referred_user_id=2, reward_credits=30)
        m = metrics.get_milestone_by_referred(2)
        self.assertEqual(m["status"], "rewarded")
        self.assertEqual(m["reward_credits"], 30)
        metrics.reset_referral_to_joined(2)
        self.assertEqual(metrics.referral_status(2), "joined")


class AcquisitionTests(MetricsTestBase):
    def test_acquisition_first_touch_is_idempotent(self) -> None:
        # Первый канал «забивает» юзера; повторные клики его не перезаписывают.
        self.assertTrue(metrics.record_acquisition(user_id=7, channel="kanal_a"))
        self.assertFalse(metrics.record_acquisition(user_id=7, channel="kanal_b"))
        self.assertEqual(self._count("acquisitions"), 1)
        row = self._one("SELECT channel FROM acquisitions WHERE user_id=7")
        self.assertEqual(row[0], "kanal_a")  # first-touch сохранён

    def test_acquisition_blank_channel_rejected(self) -> None:
        self.assertFalse(metrics.record_acquisition(user_id=1, channel="  "))
        self.assertEqual(self._count("acquisitions"), 0)

    def test_acquisition_never_raises_on_broken_db(self) -> None:
        metrics.close()
        metrics.init_db(self.db_path)
        metrics._CONN.close()
        try:
            ok = metrics.record_acquisition(user_id=1, channel="x")
        except Exception as exc:  # noqa: BLE001
            self.fail(f"record_acquisition raised on broken DB: {exc!r}")
        self.assertFalse(ok)
        metrics.close()

    def test_report_channels_counts_users_paid_and_revenue(self) -> None:
        # Канал A: 2 юзера, один заплатил. Канал B: 1 юзер, без оплат.
        metrics.record_acquisition(user_id=1, channel="A")
        metrics.record_acquisition(user_id=2, channel="A")
        metrics.record_acquisition(user_id=3, channel="B")
        metrics.record_transaction(
            provider="telegram", provider_payment_id="chg_a1", user_id=1,
            package_id="small", amount_rub=99.0, stars_amount=75,
            credits_issued=100, status="paid")
        rep = metrics.report_channels()
        self.assertEqual(rep["total_acquired"], 3)
        by_ch = {c["channel"]: c for c in rep["channels"]}
        self.assertEqual(by_ch["A"]["users"], 2)
        self.assertEqual(by_ch["A"]["paid_users"], 1)
        self.assertEqual(by_ch["A"]["revenue_stars"], 75)
        self.assertEqual(by_ch["A"]["revenue_rub"], 99.0)
        self.assertEqual(by_ch["B"]["users"], 1)
        self.assertEqual(by_ch["B"]["paid_users"], 0)
        self.assertEqual(by_ch["B"]["revenue_stars"], 0)

    def test_report_channels_empty_db(self) -> None:
        rep = metrics.report_channels()
        self.assertEqual(rep, {"total_acquired": 0, "channels": []})


class ReportTodayTests(MetricsTestBase):
    def test_report_today_empty_db_returns_zeros(self) -> None:
        rep = metrics.report_today()
        self.assertEqual(rep["new_users"], 0)
        self.assertEqual(rep["active_users"], 0)
        self.assertEqual(rep["paying_users"], 0)
        self.assertEqual(rep["image_generations"], 0)
        self.assertEqual(rep["video_generations"], 0)
        self.assertEqual(rep["success_rate"], 0.0)
        self.assertEqual(rep["revenue_rub"], 0.0)
        self.assertEqual(rep["revenue_stars"], 0)
        self.assertEqual(rep["credits_charged"], 0)
        self.assertEqual(rep["credits_refunded"], 0)
        self.assertEqual(rep["top_actions"], [])

    def test_report_today_reflects_inserted_activity(self) -> None:
        metrics.log_event("user_started", user_id=7)
        metrics.log_event("image_success", user_id=7)
        metrics.log_flow_job(
            user_id=7, operation_type="image_generate",
            bot_credits_charged=10, status="success")
        metrics.record_transaction(
            provider="telegram", provider_payment_id="chg_today", user_id=7,
            package_id="small", amount_rub=99.0, stars_amount=75,
            credits_issued=100, status="paid")

        rep = metrics.report_today()
        self.assertEqual(rep["new_users"], 1)
        self.assertEqual(rep["active_users"], 1)
        self.assertEqual(rep["paying_users"], 1)
        self.assertEqual(rep["image_generations"], 1)
        self.assertEqual(rep["paying_users"], 1)
        self.assertEqual(rep["revenue_rub"], 99.0)
        self.assertEqual(rep["revenue_stars"], 75)
        self.assertEqual(rep["credits_charged"], 10)
        self.assertEqual(rep["success_rate"], 1.0)
        names = {a["event_name"] for a in rep["top_actions"]}
        self.assertIn("user_started", names)
        self.assertIn("image_success", names)


class ReportResilienceTests(MetricsTestBase):
    def test_all_reports_safe_on_empty_db(self) -> None:
        # None of the reports may raise on a pristine DB.
        self.assertIsInstance(metrics.report_today(), dict)
        self.assertIsInstance(metrics.report_revenue(30), dict)
        self.assertIsInstance(metrics.report_flow(), dict)
        self.assertIsInstance(metrics.report_accounts(), dict)
        self.assertIsInstance(metrics.report_refs(), dict)
        self.assertIsInstance(metrics.report_channels(), dict)
        self.assertIsInstance(metrics.report_errors(7), dict)

    def test_report_accounts_tracks_remaining_and_last_error(self) -> None:
        metrics.log_flow_job(
            account_id="acc-1", operation_type="image_generate",
            flow_credits_before=100, flow_credits_after=90, status="success")
        metrics.log_flow_job(
            account_id="acc-1", operation_type="image_generate",
            status="error", error_type="quota_exceeded")
        rep = metrics.report_accounts()
        accounts = {a["account_id"]: a for a in rep["accounts"]}
        self.assertIn("acc-1", accounts)
        a = accounts["acc-1"]
        self.assertEqual(a["jobs"], 2)
        self.assertEqual(a["success"], 1)
        self.assertEqual(a["fail"], 1)
        self.assertEqual(a["last_error"], "quota_exceeded")
        self.assertEqual(a["credits_remaining"], 90)

    def test_report_errors_lists_recent_failures(self) -> None:
        metrics.log_flow_job(operation_type="video", status="error", error_type="timeout")
        metrics.log_flow_job(operation_type="image", status="success")
        rep = metrics.report_errors(7)
        self.assertEqual(len(rep["recent"]), 1)
        self.assertEqual(rep["recent"][0]["error_type"], "timeout")
        types = {e["error_type"]: e["count"] for e in rep["errors_by_type"]}
        self.assertEqual(types.get("timeout"), 1)

    def test_recent_events_includes_payment_success_events(self) -> None:
        metrics.log_event(
            "payment_success",
            user_id=99,
            username="payer",
            source="stars",
            payload={"credits": 100, "stars": 75},
        )

        events = metrics.report_recent_events(10)
        self.assertEqual(events[0]["chip"], "💳 topup")
        self.assertIn("+100кр", events[0]["text"])

    def test_report_ops_health_summarizes_recent_jobs_and_tickets(self) -> None:
        metrics.log_flow_job(
            user_id=10, account_id="acc1", operation_type="image_generate",
            model="nb2", status="success", bot_credits_charged=10,
        )
        metrics.log_flow_job(
            user_id=11, account_id="acc2", operation_type="video_text",
            model="veo-lite", status="error", error_type="timeout",
        )
        metrics.create_ticket(11, "bob", "video failed")

        rep = metrics.report_ops_health()
        self.assertEqual(rep["jobs_1h"]["total"], 2)
        self.assertEqual(rep["jobs_1h"]["success"], 1)
        self.assertEqual(rep["jobs_1h"]["fail"], 1)
        self.assertEqual(rep["open_tickets"], 1)
        self.assertEqual(rep["critical_errors"][0]["error_type"], "timeout")
        self.assertEqual(rep["last_jobs"][0]["status"], "error")

    def test_support_ticket_reports_include_user_context_and_status_updates(self) -> None:
        metrics.upsert_user(42, username="alice", first_name="Alice", channel="seed")
        metrics.credits_add(42, 100, starter=0)
        metrics.record_transaction(
            provider="telegram", provider_payment_id="chg_support", user_id=42,
            package_id="small", amount_rub=90.0, stars_amount=75,
            credits_issued=100, status="paid",
        )
        metrics.log_flow_job(
            user_id=42, account_id="acc1", operation_type="video_text",
            status="error", error_type="quota", refund_amount=60,
        )
        ticket_id = metrics.create_ticket(42, "alice", "where is my video?")

        rows = metrics.list_support_tickets("open")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], ticket_id)
        self.assertEqual(rows[0]["balance"], 100)
        self.assertEqual(rows[0]["payments_count"], 1)
        self.assertEqual(rows[0]["last_error"], "quota")

        detail = metrics.get_support_ticket_detail(ticket_id)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["user"]["profile"]["user_id"], 42)
        self.assertEqual(detail["user"]["recent_payments"][0]["package_id"], "small")
        self.assertEqual(detail["user"]["recent_jobs"][0]["error_type"], "quota")

        self.assertTrue(metrics.set_support_ticket_status(ticket_id, "closed"))
        self.assertEqual(metrics.list_support_tickets("closed")[0]["status"], "closed")


class LazyInitTests(unittest.TestCase):
    def test_functions_work_without_explicit_init(self) -> None:
        # _conn() lazily inits if the caller forgot. Use an env-driven temp path.
        import os
        with tempfile.TemporaryDirectory() as tmp:
            metrics.close()
            os.environ["METRICS_DB"] = str(Path(tmp) / "lazy.db")
            try:
                # No init_db() call at all.
                metrics.log_event("lazy_event", user_id=1)
                rep = metrics.report_today()
                self.assertEqual(rep["active_users"], 1)
            finally:
                metrics.close()
                os.environ.pop("METRICS_DB", None)


if __name__ == "__main__":
    unittest.main()
