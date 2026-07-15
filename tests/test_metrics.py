from __future__ import annotations

import tempfile
import time
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


class WebAuthenticationStateTests(MetricsTestBase):
    def test_session_binding_uses_provider_identity_and_expires(self) -> None:
        bound = metrics.bind_web_auth_session(
            "browser-session", "telegram", 42, "Alice", now=100, expires_at=200
        )
        self.assertEqual(bound["internal_user_id"], 42)
        self.assertEqual(metrics.get_web_auth_session("browser-session", now=150)["platform"], "telegram")
        self.assertIsNone(metrics.get_web_auth_session("browser-session", now=201))
        self.assertEqual(self._count("web_auth_sessions"), 0)

    def test_telegram_challenge_is_bound_single_use_and_attempt_limited(self) -> None:
        self.assertTrue(metrics.create_web_login_challenge(
            "sid", "challenge-token", now=100, expires_at=200
        ))
        self.assertTrue(metrics.claim_web_login_challenge(
            "challenge-token", "telegram", 55, "Test User", "123456", now=110
        ))
        self.assertFalse(metrics.claim_web_login_challenge(
            "challenge-token", "telegram", 66, "Attacker", "654321", now=111
        ))
        self.assertIsNone(metrics.complete_web_login_challenge("other-sid", "123456", now=120))
        self.assertIsNone(metrics.complete_web_login_challenge("sid", "000000", now=120))
        identity = metrics.complete_web_login_challenge("sid", "123456", now=121)
        self.assertEqual(identity["platform_user_id"], "55")
        self.assertIsNone(metrics.complete_web_login_challenge("sid", "123456", now=122))

    def test_expired_challenge_and_wrong_platform_fail_closed(self) -> None:
        metrics.create_web_login_challenge("sid", "expired-token", now=100, expires_at=101)
        self.assertFalse(metrics.claim_web_login_challenge(
            "expired-token", "telegram", 1, "User", "111111", now=102
        ))
        metrics.create_web_login_challenge("sid", "fresh-token", now=100, expires_at=200)
        self.assertFalse(metrics.claim_web_login_challenge(
            "fresh-token", "max", 1, "User", "111111", now=110
        ))

    def test_oauth_state_is_cookie_bound_and_single_use(self) -> None:
        self.assertTrue(metrics.create_web_oauth_state(
            "sid", "oauth-state", "yandex", "pkce-verifier", now=100, expires_at=200
        ))
        self.assertIsNone(metrics.consume_web_oauth_state(
            "wrong-sid", "oauth-state", "yandex", now=120
        ))
        self.assertEqual(metrics.consume_web_oauth_state(
            "sid", "oauth-state", "yandex", now=120
        ), "pkce-verifier")
        self.assertIsNone(metrics.consume_web_oauth_state(
            "sid", "oauth-state", "yandex", now=121
        ))

    def test_signed_provider_assertion_cannot_be_replayed(self) -> None:
        self.assertTrue(metrics.consume_web_auth_assertion(
            "max", "signed-data", now=100, expires_at=200
        ))
        self.assertFalse(metrics.consume_web_auth_assertion(
            "max", "signed-data", now=101, expires_at=200
        ))
        self.assertFalse(metrics.consume_web_auth_assertion(
            "max", "expired", now=201, expires_at=200
        ))

    def test_yandex_welcome_credit_is_atomic_and_idempotent(self) -> None:
        internal_id = metrics.ensure_user_identity("yandex", "ya-55")
        self.assertLess(internal_id, 0)
        metrics.credits_balance(internal_id, 0)

        first = metrics.grant_identity_welcome_credits("yandex", "ya-55", internal_id, 30)
        second = metrics.grant_identity_welcome_credits("yandex", "ya-55", internal_id, 30)

        self.assertEqual(first, {"granted": True, "balance": 30})
        self.assertEqual(second, {"granted": False, "balance": 30})
        self.assertEqual(metrics.credits_balance(internal_id, 0), 30)
        self.assertEqual(self._count("identity_welcome_grants"), 1)

    def test_external_identities_are_projected_into_shared_users(self) -> None:
        max_id = metrics.ensure_user_identity("max", "max-55")
        yandex_id = metrics.ensure_user_identity("yandex", "ya-55")

        max_profile = metrics.get_user_profile(max_id)
        yandex_profile = metrics.get_user_profile(yandex_id)
        self.assertEqual(max_profile["acq_channel"], "max")
        self.assertEqual(yandex_profile["acq_channel"], "web_yandex")

        metrics.bind_web_auth_session(
            "session-id", "yandex", "ya-55", "Яна",
            now=100, expires_at=200,
        )
        self.assertEqual(metrics.get_user_profile(yandex_id)["first_name"], "Яна")

    def test_web_chat_storage_is_user_scoped_and_ordered(self) -> None:
        owner = metrics.ensure_user_identity("telegram", "42")
        other = metrics.ensure_user_identity("max", "42")
        self.assertTrue(metrics.create_web_chat(owner, "chat-owner-123456", "Дом"))
        self.assertTrue(metrics.append_web_chat_message(
            owner, "chat-owner-123456", role="user", text="сделай дом"
        ))
        self.assertTrue(metrics.append_web_chat_message(
            owner, "chat-owner-123456", role="assistant", text="Готово", media=[{"type":"image","url":"https://media/x"}]
        ))
        self.assertIsNone(metrics.get_web_chat(other, "chat-owner-123456"))
        loaded = metrics.get_web_chat(owner, "chat-owner-123456")
        self.assertEqual(loaded["chat"]["title"], "Дом")
        self.assertEqual([row["role"] for row in loaded["messages"]], ["user", "assistant"])

    def test_init_backfills_historical_identity_projection(self) -> None:
        conn = metrics._conn()
        with conn:
            conn.execute(
                "INSERT INTO user_identities "
                "(platform, platform_user_id, internal_user_id) VALUES ('max','legacy',-99)"
            )
        metrics.close()
        metrics.init_db(self.db_path)

        profile = metrics.get_user_profile(-99)
        self.assertEqual(profile["user_id"], -99)
        self.assertEqual(profile["acq_channel"], "max")

    def test_welcome_credit_rejects_mismatched_identity_owner(self) -> None:
        yandex_id = metrics.ensure_user_identity("yandex", "ya-55")
        other_id = metrics.ensure_user_identity("yandex", "ya-99")

        self.assertIsNone(
            metrics.grant_identity_welcome_credits("yandex", "ya-55", other_id, 30)
        )
        self.assertEqual(metrics.credits_balance(yandex_id, 0), 0)
        self.assertEqual(metrics.credits_balance(other_id, 0), 0)
        self.assertEqual(self._count("identity_welcome_grants"), 0)


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

    def test_has_user_event_one_shot_flag(self) -> None:
        # Used for the one-shot post-first-generation brand-kit nudge.
        self.assertFalse(metrics.has_user_event(42, "brandkit_nudge_shown"))
        metrics.log_event("brandkit_nudge_shown", user_id=42)
        self.assertTrue(metrics.has_user_event(42, "brandkit_nudge_shown"))
        self.assertFalse(metrics.has_user_event(99, "brandkit_nudge_shown"))  # other user
        self.assertFalse(metrics.has_user_event(42, "some_other_event"))

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

    def test_atomic_payment_settlement_is_idempotent(self) -> None:
        first = metrics.record_transaction_and_credit_status(
            provider="robokassa",
            provider_payment_id="robo:max:1",
            user_id=-7,
            package_id="trial",
            amount_rub=45.0,
            credits_issued=45,
            starter_credits=30,
        )
        duplicate = metrics.record_transaction_and_credit_status(
            provider="robokassa",
            provider_payment_id="robo:max:1",
            user_id=-7,
            package_id="trial",
            amount_rub=45.0,
            credits_issued=45,
            starter_credits=30,
        )

        self.assertEqual(first, ("new", 75))
        self.assertEqual(duplicate, ("duplicate", 75))
        self.assertEqual(self._count("transactions"), 1)
        self.assertEqual(
            tuple(self._one("SELECT balance,granted FROM credits WHERE user_id=-7")),
            (75, 1),
        )

    def test_atomic_payment_applies_ungranted_starter_once(self) -> None:
        metrics._conn().execute(
            "INSERT INTO credits(user_id,balance,granted) VALUES(-8,5,0)"
        )
        metrics._conn().commit()

        result = metrics.record_transaction_and_credit_status(
            provider="robokassa",
            provider_payment_id="robo:max:2",
            user_id=-8,
            credits_issued=45,
            starter_credits=30,
        )

        self.assertEqual(result, ("new", 80))
        self.assertEqual(
            tuple(self._one("SELECT balance,granted FROM credits WHERE user_id=-8")),
            (80, 1),
        )

    def test_atomic_payment_rolls_back_transaction_when_credit_fails(self) -> None:
        conn = metrics._conn()
        conn.execute(
            "CREATE TRIGGER fail_credit BEFORE INSERT ON credits "
            "BEGIN SELECT RAISE(ABORT, 'credit failed'); END"
        )
        conn.commit()

        failed = metrics.record_transaction_and_credit_status(
            provider="robokassa",
            provider_payment_id="robo:max:3",
            user_id=-9,
            credits_issued=45,
            starter_credits=30,
        )

        self.assertEqual(failed, ("error", None))
        self.assertEqual(self._count("transactions"), 0)
        conn.execute("DROP TRIGGER fail_credit")
        conn.commit()
        retried = metrics.record_transaction_and_credit_status(
            provider="robokassa",
            provider_payment_id="robo:max:3",
            user_id=-9,
            credits_issued=45,
            starter_credits=30,
        )
        self.assertEqual(retried, ("new", 75))


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

    def test_record_referral_join_is_idempotent(self) -> None:
        # record_referral_join returns True only on the first (new) row — this is
        # what gates the one-time +REFERRAL_REFERRED_BONUS gift to the friend.
        self.assertTrue(metrics.record_referral_join(referrer_user_id=1, referred_user_id=2))
        self.assertFalse(metrics.record_referral_join(referrer_user_id=1, referred_user_id=2))
        self.assertFalse(metrics.record_referral_join(referrer_user_id=2, referred_user_id=2))  # self
        self.assertEqual(self._count("referrals"), 1)

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

    def test_acquisition_syncs_user_channel_snapshot(self) -> None:
        metrics.upsert_user(7, username="seeded")
        self.assertTrue(metrics.record_acquisition(user_id=7, channel="goroskop_new"))
        row = self._one("SELECT acq_channel FROM users WHERE user_id=7")
        self.assertEqual(row[0], "goroskop_new")

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

    def test_report_channels_includes_seed_click_funnel_and_recent_users(self) -> None:
        metrics.upsert_user(1, username="paid")
        metrics.upsert_user(2, username="idle")
        metrics.log_event("channel_seed_created", user_id=777, source="goroskop_new")
        metrics.record_acquisition(user_id=1, channel="goroskop_new")
        metrics.record_acquisition(user_id=2, channel="goroskop_new")
        metrics.log_event("channel_seed_clicked", user_id=1, source="goroskop_new")
        metrics.log_event("channel_seed_new", user_id=1, source="goroskop_new")
        metrics.log_event("menu_clicked", user_id=1, source="m:gen")
        metrics.log_event("channel_seed_clicked", user_id=2, source="goroskop_new")
        metrics.log_event("channel_seed_new", user_id=2, source="goroskop_new")
        metrics.log_event("channel_seed_clicked", user_id=99, source="goroskop_new")
        metrics.log_event("channel_seed_returning", user_id=99, source="goroskop_new")
        metrics.log_flow_job(user_id=1, operation_type="image_generate", status="success")
        metrics.record_transaction(
            provider="telegram", provider_payment_id="chg_seed", user_id=1,
            package_id="small", amount_rub=99.0, stars_amount=75,
            credits_issued=100, status="paid")

        rep = metrics.report_channels()
        item = {c["channel"]: c for c in rep["channels"]}["goroskop_new"]
        self.assertEqual(item["seed_links_created"], 1)
        self.assertEqual(item["seed_clicks"], 3)
        self.assertEqual(item["unique_click_users"], 3)
        self.assertEqual(item["returning_clicks"], 1)
        self.assertEqual(item["users"], 2)
        self.assertEqual(item["started_only"], 1)
        self.assertEqual(item["interacted_users"], 1)
        self.assertEqual(item["requested_users"], 1)
        self.assertEqual(item["generated_users"], 1)
        self.assertEqual(item["paid_users"], 1)
        stages = {u["stage"] for u in item["recent_users"]}
        self.assertIn("started_only", stages)
        self.assertIn("paid", stages)

    def test_report_channels_empty_db(self) -> None:
        rep = metrics.report_channels()
        self.assertEqual(rep, {"total_acquired": 0, "total_seed_clicks": 0, "channels": []})


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
        metrics.upsert_user(7, username="alice")
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

    def test_recent_events_includes_new_user_events(self) -> None:
        metrics.log_event(
            "new_user",
            user_id=77,
            username="seeded",
            source="goroskop_new",
            payload={"seed_channel": "goroskop_new"},
        )

        events = metrics.report_recent_events(10)
        self.assertEqual(events[0]["chip"], "user new")
        self.assertEqual(events[0]["kind"], "new")
        self.assertIn("seed:goroskop_new", events[0]["text"])
        self.assertIn("created_at", events[0])

    def test_recent_events_includes_video_ab_events(self) -> None:
        metrics.log_event(
            "video_ab",
            source="sub1",
            payload={
                "account": "sub1",
                "model": "omni-flash-4s",
                "statuses": {"direct_http": 403, "browser_fetch": 200},
            },
        )

        events = metrics.report_recent_events(10)
        self.assertEqual(events[0]["chip"], "🎬 video_ab")
        self.assertIn("sub1", events[0]["text"])

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
        self.assertEqual(rows[0]["kind"], "support")
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

    def test_done4you_support_queue_filter_and_statuses(self) -> None:
        normal_id = metrics.create_ticket(51, "normal", "regular support")
        done_id = metrics.create_ticket(52, "seller", "🙌 Заявка под ключ\nПлощадка: Wildberries\n\nNeed cards")

        rows = metrics.list_support_tickets("done4you")
        self.assertEqual([r["id"] for r in rows], [done_id])
        self.assertEqual(rows[0]["kind"], "done4you")
        self.assertNotEqual(rows[0]["id"], normal_id)

        self.assertTrue(metrics.set_support_ticket_status(done_id, "in_work"))
        self.assertEqual(metrics.list_support_tickets("in_work")[0]["id"], done_id)
        self.assertTrue(metrics.set_support_ticket_status(done_id, "done"))
        self.assertEqual(metrics.list_support_tickets("done")[0]["status"], "done")
        self.assertFalse(metrics.set_support_ticket_status(done_id, "bad_status"))


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


class SellerReportTests(MetricsTestBase):
    def test_report_sellers_aggregates_only_mp_events(self) -> None:
        metrics.log_event("mp_platform", user_id=1, username="seller1", source="wb")
        metrics.log_event("mp_job", user_id=1, username="seller1", source="wb:whitebg")
        metrics.log_event("mp_job", user_id=1, username="seller1", source="wb:info")
        metrics.log_event("mp_done4you_open", user_id=1, username="seller1", source="seller")
        metrics.log_event("mp_platform", user_id=2, username="seller2", source="ozon")
        # A consumer-only user must not appear as a seller.
        metrics.log_event("image_requested", user_id=3, username="consumer", source="wizard")
        metrics.record_transaction(
            provider="stars",
            provider_payment_id="seller-payment-1",
            user_id=1,
            amount_rub=120.0,
            stars_amount=60,
            status="paid",
        )
        metrics.record_transaction(
            provider="stars",
            provider_payment_id="consumer-payment-1",
            user_id=3,
            amount_rub=999.0,
            stars_amount=500,
            status="paid",
        )

        rep = metrics.report_sellers()
        self.assertEqual(rep["total_sellers"], 2)
        self.assertEqual(rep["total_events"], 5)
        self.assertEqual(rep["total_jobs"], 2)
        self.assertEqual(rep["total_done4you"], 1)
        self.assertEqual(rep["total_paid_count"], 1)
        self.assertEqual(rep["total_revenue_stars"], 60)
        self.assertEqual(rep["total_revenue_rub"], 120.0)
        ids = {s["user_id"] for s in rep["sellers"]}
        self.assertEqual(ids, {1, 2})

        s1 = next(s for s in rep["sellers"] if s["user_id"] == 1)
        self.assertEqual(s1["events"], 4)
        self.assertEqual(s1["jobs"], 2)
        self.assertEqual(s1["done4you"], 1)
        self.assertEqual(s1["username"], "seller1")
        self.assertEqual(s1["paid_count"], 1)
        self.assertEqual(s1["revenue_stars"], 60)
        self.assertEqual(s1["revenue_rub"], 120.0)
        self.assertEqual(s1["sku_projects"], 0)
        self.assertEqual(s1["recent_events"][0]["event_name"], "mp_done4you_open")
        self.assertEqual(s1["recent_events"][0]["source"], "seller")

    def test_seller_sku_projects_group_generated_items(self) -> None:
        self.assertEqual(metrics.save_seller_sku_item(7, "", file_id="file-a"), 0)
        self.assertTrue(metrics.create_seller_sku_project(7, "SKU-0 Empty", platform="ym"))
        self.assertGreater(
            metrics.save_seller_sku_item(
                7, "  SKU-1   Red Shoes  ", file_id="file-a", token="tok-a",
                prompt="first", platform="wb",
            ),
            0,
        )
        self.assertGreater(
            metrics.save_seller_sku_item(
                7, "SKU-1 Red Shoes", file_id="file-b", token="tok-b",
                prompt="second", platform="wb",
            ),
            0,
        )
        self.assertGreater(
            metrics.save_seller_sku_item(
                7, "SKU-2", file_id="file-c", token="tok-c",
                prompt="third", platform="ozon",
            ),
            0,
        )

        projects = metrics.list_seller_sku_projects(7)
        self.assertEqual([p["sku"] for p in projects], ["SKU-2", "SKU-1 Red Shoes", "SKU-0 Empty"])
        self.assertEqual(projects[1]["items"], 2)
        self.assertEqual(projects[1]["latest_file_id"], "file-b")
        self.assertEqual(projects[1]["latest_prompt"], "second")
        self.assertEqual(projects[2]["items"], 0)
        self.assertEqual(projects[2]["platform"], "ym")
        self.assertEqual(metrics.recent_seller_skus(7), ["SKU-2", "SKU-1 Red Shoes", "SKU-0 Empty"])

        self.assertTrue(metrics.rename_seller_sku_project(7, "SKU-0 Empty", "SKU-0 Renamed"))
        renamed = metrics.get_seller_sku_project(7, "SKU-0 Renamed")
        self.assertIsNotNone(renamed)
        self.assertEqual(renamed["items"], 0)
        self.assertGreaterEqual(metrics.delete_seller_sku_project(7, "SKU-0 Renamed"), 1)
        self.assertIsNone(metrics.get_seller_sku_project(7, "SKU-0 Renamed"))

        metrics.log_event("mp_platform", user_id=7, username="seller7", source="wb")
        rep = metrics.report_sellers()
        seller = rep["sellers"][0]
        self.assertEqual(seller["sku_projects"], 2)
        self.assertEqual(rep["total_sku_projects"], 2)

    def test_seller_profile_brand_kit_upsert(self) -> None:
        self.assertEqual(metrics.get_seller_profile(8), {})
        self.assertTrue(metrics.save_seller_profile(8, brand_kit="  black gold  ", niche="shoes"))
        profile = metrics.get_seller_profile(8)
        self.assertEqual(profile["brand_kit"], "black gold")
        self.assertEqual(profile["niche"], "shoes")
        self.assertTrue(metrics.save_seller_profile(8, brand_kit="minimal premium"))
        profile2 = metrics.get_seller_profile(8)
        self.assertEqual(profile2["brand_kit"], "minimal premium")
        self.assertEqual(profile2["niche"], "shoes")
        self.assertTrue(metrics.save_seller_profile(8, niche="electronics"))
        profile3 = metrics.get_seller_profile(8)
        self.assertEqual(profile3["brand_kit"], "minimal premium")
        self.assertEqual(profile3["niche"], "electronics")

    def test_seller_history_pairs_flow_jobs_with_marketplace_source(self) -> None:
        metrics.log_event("mp_job", user_id=70, source="ym:series:3")
        metrics.log_flow_job(
            user_id=70,
            account_id="seller-backend",
            operation_type="mp_series",
            model="nb2",
            status="success",
            bot_credits_charged=30,
        )
        metrics.log_flow_job(
            user_id=70,
            account_id="seller-backend",
            operation_type="video_mp_animate",
            model="veo-lite",
            status="fail",
            error_type="backend_failed",
            refund_amount=75,
        )

        rows = metrics.get_seller_history(70, limit=5)
        self.assertEqual([r["operation_type"] for r in rows], ["video_mp_animate", "mp_series"])
        self.assertEqual(rows[0]["mp_source"], "ym:series:3")
        self.assertEqual(rows[1]["bot_credits_charged"], 30)
        self.assertEqual(rows[0]["refund_amount"], 75)

    def test_report_sellers_empty(self) -> None:
        rep = metrics.report_sellers()
        self.assertEqual(rep["total_sellers"], 0)
        self.assertEqual(rep["total_events"], 0)
        self.assertEqual(rep["total_jobs"], 0)
        self.assertEqual(rep["total_done4you"], 0)
        self.assertEqual(rep["sellers"], [])


class VideoHealthReportTests(MetricsTestBase):
    def test_report_video_health_aggregates(self) -> None:
        metrics.log_event("video_outcome", user_id=1, source="sub1",
                          payload={"ok": False, "attempts": 4, "had_403": True,
                                   "model_key": "abra_t2v_4s", "model_family": "omni-flash",
                                   "endpoint": "text", "mode": "text", "transport": "direct_http",
                                   "unusual_403": True})
        metrics.log_event("video_outcome", user_id=1, source="sub1",
                          payload={"ok": True, "attempts": 3, "had_403": True,
                                   "model_key": "abra_t2v_4s", "model_family": "omni-flash",
                                   "endpoint": "text", "mode": "text", "transport": "direct_http"})
        metrics.log_event("video_outcome", user_id=2, source="sub2",
                          payload={"ok": True, "attempts": 1, "had_403": False,
                                   "model_key": "veo_3_1_t2v_lite", "model_family": "veo",
                                   "endpoint": "text", "mode": "text", "transport": "direct_http"})

        rows = {r["account"]: r for r in metrics.report_video_health((24,))["windows"]["24h"]}
        s1 = rows["sub1"]
        self.assertEqual(s1["model_key"], "abra_t2v_4s")
        self.assertEqual(s1["model_family"], "omni-flash")
        self.assertEqual(s1["endpoint"], "text")
        self.assertEqual(s1["transport"], "direct_http")
        self.assertEqual(s1["video_attempts"], 2)
        self.assertEqual(s1["video_403"], 2)
        self.assertEqual(s1["403_public_error_unusual_activity"], 1)
        self.assertEqual(s1["video_success"], 1)
        self.assertEqual(s1["video_success_after_retry"], 1)
        self.assertEqual(s1["video_final_fail"], 1)
        self.assertEqual(s1["avg_attempts_before_200"], 3.0)
        self.assertEqual(s1["success_rate"], 0.5)
        self.assertEqual(s1["success_rate_24h"], 0.5)

        s2 = rows["sub2"]
        self.assertEqual(s2["video_success"], 1)
        self.assertEqual(s2["video_success_after_retry"], 0)
        self.assertEqual(s2["success_rate"], 1.0)

    def test_report_video_health_includes_video_ab_arms(self) -> None:
        metrics.log_event(
            "video_ab",
            source="sub2",
            payload={
                "account": "sub2",
                "model_key": "veo_3_1_t2v_lite",
                "model_family": "veo",
                "mode": "text",
                "endpoint": "video:batchAsyncGenerateVideoText",
                "arms": [
                    {"transport": "direct_http", "status": 403, "ok": False,
                     "body_preview": "PUBLIC_ERROR_UNUSUAL_ACTIVITY"},
                    {"transport": "browser_fetch", "status": 200, "ok": True},
                ],
            },
        )

        rows = metrics.report_video_health((24,))["windows"]["24h"]
        by_transport = {r["transport"]: r for r in rows}
        self.assertEqual(by_transport["direct_http"]["video_403"], 1)
        self.assertEqual(by_transport["direct_http"]["403_public_error_unusual_activity"], 1)
        self.assertEqual(by_transport["browser_fetch"]["video_success"], 1)
        self.assertEqual(by_transport["browser_fetch"]["avg_attempts_before_200"], 2.0)

    def test_video_account_scores_demote_proxy_failure_and_prefer_success(self) -> None:
        metrics.log_event("proxy_check", source="bad", payload={"account": "bad", "match": False})
        metrics.log_event("video_outcome", source="good",
                          payload={"ok": True, "attempts": 1, "model_family": "veo"})

        scores = metrics.report_video_account_scores(model_family="veo")
        self.assertTrue(scores["bad"]["proxy_failed"])
        self.assertGreater(scores["good"]["score"], scores["bad"]["score"])

    def test_video_account_scores_demote_retry_heavy_below_idle(self) -> None:
        # flaky: a reputation-troubled account that 403s a lot and only succeeds
        # after several captcha retries (high avg_attempts, success-after-retry).
        metrics.log_event("video_outcome", source="flaky",
                          payload={"ok": False, "had_403": True, "model_family": "omni-flash"})
        metrics.log_event("video_outcome", source="flaky",
                          payload={"ok": True, "attempts": 3, "model_family": "omni-flash"})
        # fresh: clean account with a healthy proxy check and no recent video data.
        metrics.log_event("proxy_check", source="fresh", payload={"account": "fresh", "match": True})

        scores = metrics.report_video_account_scores(model_family="omni-flash")
        self.assertIn("fresh", scores)
        self.assertIn("flaky", scores)
        # A clean idle account must outrank a retry-heavy/403-prone one.
        self.assertGreater(scores["fresh"]["score"], scores["flaky"]["score"])
        self.assertGreaterEqual(scores["fresh"]["score"], 50.0)
        self.assertLess(scores["flaky"]["score"], 50.0)
        self.assertEqual(scores["flaky"]["avg_attempts_before_200"], 3.0)

    def test_report_video_health_empty(self) -> None:
        rep = metrics.report_video_health((1, 24))
        self.assertEqual(rep["windows"]["1h"], [])
        self.assertEqual(rep["windows"]["24h"], [])


class QualitativeAnalyticsTests(MetricsTestBase):
    """report_activation_cold / payment_repeat / margin / referral_quality."""

    def _ev_at(self, name: str, user_id: int, offset: str) -> None:
        conn = metrics._conn()
        with conn:
            conn.execute(
                "INSERT INTO events (event_name, user_id, created_at) "
                "VALUES (?, ?, datetime('now', ?))",
                (name, user_id, offset),
            )

    # ── 1 · cold-start activation ───────────────────────────────────────
    def test_activation_cold_by_channel_and_window(self) -> None:
        # u1: started 3d ago, first success within 24h → activated (ads)
        self._ev_at("user_started", 1, "-3 days")
        self._ev_at("image_success", 1, "-3 days")
        metrics.record_acquisition(user_id=1, channel="ads")
        # u2: started 3d ago, never generated → not activated (ads)
        self._ev_at("user_started", 2, "-3 days")
        metrics.record_acquisition(user_id=2, channel="ads")
        # u3: started 3d ago, success only after 24h (≈2d later) → not activated (organic)
        self._ev_at("user_started", 3, "-3 days")
        self._ev_at("video_success", 3, "-1 days")
        metrics.record_acquisition(user_id=3, channel="organic")
        # u4: started 1h ago → window not elapsed → excluded from cohort entirely
        self._ev_at("user_started", 4, "-1 hours")
        self._ev_at("image_success", 4, "-1 hours")

        rep = metrics.report_activation_cold(window_hours=24)
        self.assertEqual(rep["window_hours"], 24)
        self.assertEqual(rep["cohort"], 3)        # u1,u2,u3 (u4 excluded)
        self.assertEqual(rep["activated"], 1)     # only u1
        self.assertAlmostEqual(rep["rate"], round(1 / 3, 3))
        by = {c["channel"]: c for c in rep["by_channel"]}
        self.assertEqual(by["ads"]["cohort"], 2)
        self.assertEqual(by["ads"]["activated"], 1)
        self.assertEqual(by["ads"]["rate"], 0.5)
        self.assertEqual(by["organic"]["cohort"], 1)
        self.assertEqual(by["organic"]["activated"], 0)

    def test_activation_cold_empty(self) -> None:
        rep = metrics.report_activation_cold()
        self.assertEqual(rep["cohort"], 0)
        self.assertEqual(rep["rate"], 0.0)
        self.assertEqual(rep["by_channel"], [])

    # ── 2 · repeat purchase ─────────────────────────────────────────────
    def _pay(self, user_id: int, pid: str, offset: str, rub: float = 100.0) -> None:
        conn = metrics._conn()
        with conn:
            conn.execute(
                "INSERT INTO transactions "
                "(provider, provider_payment_id, user_id, status, amount_rub, "
                " credits_issued, created_at, paid_at) "
                "VALUES ('test', ?, ?, 'paid', ?, 100, datetime('now', ?), datetime('now', ?))",
                (pid, user_id, rub, offset, offset),
            )

    def test_payment_repeat_cohort(self) -> None:
        # u1: first 40d ago, second 10d later (30d ago) → repeated within window
        self._pay(1, "u1a", "-40 days")
        self._pay(1, "u1b", "-30 days")
        # u2: first 40d ago, no second → eligible, not repeated
        self._pay(2, "u2a", "-40 days")
        # u3: first 40d ago, second 35d later (5d ago) → eligible, NOT within 30d
        self._pay(3, "u3a", "-40 days")
        self._pay(3, "u3b", "-5 days")
        # u4: first 5d ago → window not yet elapsed → excluded from eligible
        self._pay(4, "u4a", "-5 days")

        rep = metrics.report_payment_repeat(days=30)
        self.assertEqual(rep["days"], 30)
        self.assertEqual(rep["first_payers"], 4)
        self.assertEqual(rep["eligible_first_payers"], 3)  # u1,u2,u3
        self.assertEqual(rep["repeated"], 1)               # only u1
        self.assertAlmostEqual(rep["repeat_rate"], round(1 / 3, 3))
        self.assertIsNotNone(rep["median_days_to_second"])
        self.assertAlmostEqual(rep["median_days_to_second"], 10.0, delta=0.5)

    def test_payment_repeat_empty(self) -> None:
        rep = metrics.report_payment_repeat()
        self.assertEqual(rep["eligible_first_payers"], 0)
        self.assertEqual(rep["repeat_rate"], 0.0)
        self.assertIsNone(rep["median_days_to_second"])

    # ── 3 · real margin ─────────────────────────────────────────────────
    def test_margin_from_measured_cogs(self) -> None:
        metrics.record_transaction(provider="t", provider_payment_id="m1",
                                   user_id=1, amount_rub=300.0, credits_issued=500)
        metrics.record_transaction(provider="t", provider_payment_id="m2",
                                   user_id=2, amount_rub=300.0, credits_issued=500)
        # Consumption jobs: negative provider delta = COGS in G-credits.
        metrics.log_flow_job(user_id=1, operation_type="video", model="omni-flash-4s",
                             bot_credits_charged=50, flow_credits_before=1000,
                             flow_credits_after=993, status="success")
        metrics.log_flow_job(user_id=2, operation_type="video", model="veo-lite",
                             bot_credits_charged=60, flow_credits_before=993,
                             flow_credits_after=983, status="success")
        # Provider top-up (positive delta) must NOT count as consumption.
        metrics.log_flow_job(user_id=None, operation_type="topup", model="—",
                             bot_credits_charged=0, flow_credits_before=100,
                             flow_credits_after=1100, status="success")

        rep = metrics.report_margin(days=90)
        self.assertEqual(rep["revenue_rub"], 600.0)
        self.assertEqual(rep["credits_sold"], 1000)
        self.assertEqual(rep["paying_users"], 2)
        self.assertEqual(rep["g_credits_consumed"], 17)       # 7 + 10, top-up excluded
        self.assertEqual(rep["bot_credits_charged"], 110)     # 50 + 60
        self.assertEqual(rep["g_credit_cost_rub"], 0.053)
        self.assertEqual(rep["cogs_rub"], round(17 * 0.053, 2))
        self.assertEqual(rep["rub_per_bot_credit"], 0.6)
        self.assertGreater(rep["gross_margin_rub"], 0)
        self.assertGreater(rep["contribution_margin_per_payer_rub"], 0)
        # Override cost feeds straight through to COGS.
        rep2 = metrics.report_margin(days=90, g_credit_cost_rub=1.0)
        self.assertEqual(rep2["cogs_rub"], 17.0)
        self.assertEqual(rep2["gross_margin_rub"], 583.0)

    def test_margin_empty(self) -> None:
        rep = metrics.report_margin()
        self.assertEqual(rep["revenue_rub"], 0.0)
        self.assertEqual(rep["gross_margin_pct"], 0.0)
        self.assertEqual(rep["by_model"], [])

    # ── 5 · referral quality (honest k + share) ─────────────────────────
    def test_referral_quality_k_and_share(self) -> None:
        metrics.record_referral_join(referrer_user_id=1, referred_user_id=2)
        metrics.record_referral_join(referrer_user_id=1, referred_user_id=3)
        metrics.record_referral_join(referrer_user_id=9, referred_user_id=4)
        # Generations (durable signal): u2,u4 referred + u5 non-referred; u3 none.
        for uid in (2, 4, 5):
            metrics.log_flow_job(user_id=uid, operation_type="image", status="success")
        # The "+50 for first generation" writer was removed; the table is kept
        # read-only for historical analytics. Seed one legacy row directly.
        with metrics._LOCK:
            conn = metrics._conn()
            conn.execute(
                "INSERT INTO referral_first_generation_rewards "
                "(referrer_user_id, referred_user_id, reward_credits) VALUES (1, 2, 50)")
            conn.commit()

        rep = metrics.report_referral_quality()
        self.assertEqual(rep["referrers"], 2)            # {1, 9}
        self.assertEqual(rep["invited"], 3)              # {2, 3, 4}
        self.assertEqual(rep["invited_generated"], 2)    # {2, 4}
        self.assertEqual(rep["k"], round(2 / 2, 3))      # 1.0
        self.assertEqual(rep["activated_total"], 3)      # {2, 4, 5}
        self.assertEqual(rep["activated_referred"], 2)   # {2, 4}
        self.assertAlmostEqual(rep["referral_share"], round(2 / 3, 3))
        self.assertEqual(rep["first_generation_rewards"], 1)

    def test_referral_quality_empty(self) -> None:
        rep = metrics.report_referral_quality()
        self.assertEqual(rep["referrers"], 0)
        self.assertEqual(rep["k"], 0.0)
        self.assertEqual(rep["referral_share"], 0.0)


if __name__ == "__main__":
    unittest.main()
