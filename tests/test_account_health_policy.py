"""Unit tests for accounts.health: rate-limit predicate + failure policy."""

from __future__ import annotations

import unittest

from accounts.health import AccountFailurePolicy, is_rate_limit_error


class _FakePool:
    def __init__(self, cooldown=True, failure=True):
        self._cooldown_ret = cooldown
        self._failure_ret = failure
        self.cooldowns = []
        self.failures = []

    def mark_cooldown(self, acc):
        self.cooldowns.append(acc)
        return self._cooldown_ret

    def mark_failure(self, acc):
        self.failures.append(acc)
        return self._failure_ret


class _FakeMetrics:
    def __init__(self):
        self.events = []

    def log_event(self, name, payload=None):
        self.events.append((name, payload))


class _NullLog:
    def warning(self, *a, **k):
        pass


def _policy(pool):
    alerts = []
    metrics = _FakeMetrics()
    pol = AccountFailurePolicy(
        account_pool=pool, log=_NullLog(), metrics=metrics,
        fire_owner_alert=alerts.append,
    )
    return pol, alerts, metrics


class RateLimitTests(unittest.TestCase):
    def test_429_and_phrases_detected(self):
        self.assertTrue(is_rate_limit_error({"error": "HTTP 429 blah"}))
        self.assertTrue(is_rate_limit_error({"error": "Too Many Requests"}))
        self.assertTrue(is_rate_limit_error({"error": "слишком много запросов"}))

    def test_none_and_other_errors_false(self):
        self.assertFalse(is_rate_limit_error(None))
        self.assertFalse(is_rate_limit_error({"error": "boom"}))
        self.assertFalse(is_rate_limit_error({}))


class ImageFailureTests(unittest.TestCase):
    def test_no_account_id_noop(self):
        pool = _FakePool()
        pol, alerts, _ = _policy(pool)
        pol.mark_image_failure(None, {})
        self.assertEqual(pool.cooldowns, [])
        self.assertEqual(alerts, [])

    def test_unusual_activity_cools_and_alerts(self):
        pool = _FakePool()
        pol, alerts, metrics = _policy(pool)
        pol.mark_image_failure("acc1", {"account_risk": "unusual_activity"})
        self.assertEqual(pool.cooldowns, ["acc1"])
        self.assertEqual(pool.failures, [])
        self.assertEqual(len(alerts), 1)
        self.assertIn("unusual_activity", alerts[0])

    def test_rate_limit_cools_without_alert(self):
        pool = _FakePool()
        pol, alerts, metrics = _policy(pool)
        pol.mark_image_failure("acc1", {"error": "429"})
        self.assertEqual(pool.cooldowns, ["acc1"])
        self.assertEqual(alerts, [])
        self.assertEqual(metrics.events[0][1]["reason"], "rate_limited")

    def test_generic_failure_marks_and_alerts_on_threshold(self):
        pool = _FakePool(failure=True)
        pol, alerts, _ = _policy(pool)
        pol.mark_image_failure("acc1", {"error": "boom"})
        self.assertEqual(pool.failures, ["acc1"])
        self.assertEqual(len(alerts), 1)

    def test_generic_failure_below_threshold_no_alert(self):
        pool = _FakePool(failure=False)
        pol, alerts, _ = _policy(pool)
        pol.mark_image_failure("acc1", {"error": "boom"})
        self.assertEqual(alerts, [])


class VideoFailureTests(unittest.TestCase):
    def test_video_auth_cools_and_alerts(self):
        pool = _FakePool()
        pol, alerts, _ = _policy(pool)
        pol.mark_video_failure("v1", {"account_risk": "video_auth"})
        self.assertEqual(pool.cooldowns, ["v1"])
        self.assertIn("video_auth", alerts[0])

    def test_recaptcha_403_counts_as_failure_not_cooldown(self):
        pool = _FakePool(failure=False)
        pol, alerts, metrics = _policy(pool)
        pol.mark_video_failure("v1", {"account_risk": "video_recaptcha_403"})
        self.assertEqual(pool.cooldowns, [])   # single 403 does not cool down
        self.assertEqual(pool.failures, ["v1"])
        self.assertIn(("video_recaptcha_403", {"account": "v1"}), metrics.events)
        self.assertEqual(alerts, [])

    def test_rate_limit_cools_without_alert(self):
        pool = _FakePool()
        pol, alerts, _ = _policy(pool)
        pol.mark_video_failure("v1", {"error": "too many requests"})
        self.assertEqual(pool.cooldowns, ["v1"])
        self.assertEqual(alerts, [])


if __name__ == "__main__":
    unittest.main()
