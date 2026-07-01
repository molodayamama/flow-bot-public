from __future__ import annotations

import unittest

from flow_core import referral_milestone_bonus, referral_ongoing_bonus
from referrals.service import ReferralService


class FakeStore:
    def __init__(self, balances=None):
        self._b = dict(balances or {})
        self.added: list[tuple[int, int]] = []
        self.charged: list[tuple[int, int]] = []

    def add(self, user_id, amount):
        self._b[user_id] = self._b.get(user_id, 0) + amount
        self.added.append((user_id, amount))

    def charge(self, user_id, amount):
        self._b[user_id] = self._b.get(user_id, 0) - amount
        self.charged.append((user_id, amount))

    def balance(self, user_id):
        return self._b.get(user_id, 0)


class FakeMetrics:
    def __init__(self, **kw):
        self.referrer = kw.get("referrer")
        self.active = kw.get("active", True)
        self.status = kw.get("status", "joined")
        self.credits_today = kw.get("credits_today", 0)
        self.grant_wins = kw.get("grant_wins", True)
        self.dup_payment = kw.get("dup_payment", None)
        self.record_wins = kw.get("record_wins", True)
        self.ongoing_reward = kw.get("ongoing_reward")
        self.milestone = kw.get("milestone")
        self.events: list[tuple] = []
        self.reset_called: list[int] = []

    def get_referrer_of(self, referred):
        return self.referrer

    def referral_is_active(self, referred, window):
        return self.active

    def referral_status(self, referred):
        return self.status

    def get_referral_credits_today(self, referrer):
        return self.credits_today

    def grant_milestone_if_joined(self, *, referred_user_id, reward_credits):
        return self.grant_wins

    def get_ongoing_reward_by_payment(self, pid):
        return self.dup_payment

    def record_ongoing_reward(self, referrer, referred, amount, pid):
        return self.record_wins

    def get_milestone_by_referred(self, referred):
        return self.milestone

    def reset_referral_to_joined(self, referred):
        self.reset_called.append(referred)

    def log_event(self, name, *, user_id, payload):
        self.events.append((name, user_id, payload))


def _svc(metrics, store, notify_log):
    return ReferralService(
        store=store, metrics=metrics, notify=lambda r, b: notify_log.append((r, b))
    )


def _apply(svc, referred=2, *, stars_paid=200, credits_issued=100, pid="pay-1"):
    svc.apply_payment_rewards(
        referred, stars_paid=stars_paid, credits_issued=credits_issued,
        pack_id="pack", provider_payment_id=pid,
    )


class ApplyRewardTests(unittest.TestCase):
    def test_no_referrer_is_noop(self) -> None:
        store, notified = FakeStore(), []
        _apply(_svc(FakeMetrics(referrer=None), store, notified))
        self.assertEqual(store.added, [])

    def test_self_referral_is_noop(self) -> None:
        store, notified = FakeStore(), []
        _apply(_svc(FakeMetrics(referrer=2), store, notified), referred=2)
        self.assertEqual(store.added, [])

    def test_inactive_binding_is_noop(self) -> None:
        store, notified = FakeStore(), []
        _apply(_svc(FakeMetrics(referrer=1, active=False), store, notified))
        self.assertEqual(store.added, [])

    def test_joined_milestone_pays_when_claim_wins(self) -> None:
        store, notified = FakeStore(), []
        _apply(_svc(FakeMetrics(referrer=1, status="joined", grant_wins=True), store, notified),
               stars_paid=200)
        bonus = referral_milestone_bonus(200)
        self.assertEqual(store.added, [(1, bonus)])
        self.assertEqual(notified, [(1, bonus)])
        self.assertEqual(store.balance(1), bonus)

    def test_joined_milestone_skipped_when_claim_lost(self) -> None:
        store, notified = FakeStore(), []
        _apply(_svc(FakeMetrics(referrer=1, status="joined", grant_wins=False), store, notified))
        self.assertEqual(store.added, [])

    def test_joined_over_daily_cap_skipped(self) -> None:
        store, notified = FakeStore(), []
        _apply(_svc(FakeMetrics(referrer=1, status="joined", credits_today=10_000), store, notified))
        self.assertEqual(store.added, [])

    def test_rewarded_ongoing_pays_once(self) -> None:
        store, notified = FakeStore(), []
        _apply(_svc(FakeMetrics(referrer=1, status="rewarded", record_wins=True), store, notified),
               credits_issued=100)
        ongoing = referral_ongoing_bonus(100)
        if ongoing > 0:
            self.assertEqual(store.added, [(1, ongoing)])
            self.assertEqual(notified, [(1, ongoing)])

    def test_rewarded_duplicate_payment_skipped(self) -> None:
        store, notified = FakeStore(), []
        _apply(_svc(FakeMetrics(referrer=1, status="rewarded", dup_payment={"x": 1}), store, notified))
        self.assertEqual(store.added, [])

    def test_never_raises_into_caller(self) -> None:
        class Boom(FakeMetrics):
            def get_referrer_of(self, referred):
                raise RuntimeError("db down")

        store, notified = FakeStore(), []
        _apply(_svc(Boom(), store, notified))  # must not raise
        self.assertEqual(store.added, [])


class ClawbackTests(unittest.TestCase):
    def test_clawback_ongoing_and_milestone(self) -> None:
        store = FakeStore(balances={1: 100})
        m = FakeMetrics(
            ongoing_reward={"referrer_user_id": 1, "reward_credits": 20},
            milestone={"referrer_user_id": 1, "reward_credits": 50, "status": "rewarded"},
        )
        # get_ongoing_reward_by_payment(charge_id) returns the ongoing dict
        m.dup_payment = {"referrer_user_id": 1, "reward_credits": 20}
        svc = ReferralService(store=store, metrics=m)
        svc.clawback(referred_user_id=2, charge_id="charge-A")

        self.assertEqual(store.charged, [(1, 20), (1, 50)])  # capped at balance where needed
        self.assertEqual(m.reset_called, [2])
        self.assertEqual([e[0] for e in m.events], ["referral_reward_clawback", "referral_reward_clawback"])

    def test_clawback_charge_capped_at_balance(self) -> None:
        store = FakeStore(balances={1: 5})
        m = FakeMetrics(milestone={"referrer_user_id": 1, "reward_credits": 50, "status": "rewarded"})
        m.dup_payment = None  # no ongoing reward
        svc = ReferralService(store=store, metrics=m)
        svc.clawback(referred_user_id=2, charge_id="charge-A")
        self.assertEqual(store.charged, [(1, 5)])  # min(50, balance 5)


if __name__ == "__main__":
    unittest.main()
