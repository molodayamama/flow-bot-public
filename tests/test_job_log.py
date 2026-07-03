"""Unit tests for product.job_log: timing/tag helpers + ImageJobLogger."""

from __future__ import annotations

import time
import unittest

from product.job_log import ms_since, seller_acc_tag, ImageJobLogger


class HelperTests(unittest.TestCase):
    def test_ms_since_nonnegative(self):
        t = time.monotonic()
        self.assertGreaterEqual(ms_since(t), 0)

    def test_seller_acc_tag(self):
        self.assertEqual(seller_acc_tag("sub5"), "sub5-sell")
        self.assertEqual(seller_acc_tag("  sub5  "), "sub5-sell")
        self.assertIsNone(seller_acc_tag(""))
        self.assertIsNone(seller_acc_tag(None))


class _FakeMetrics:
    def __init__(self):
        self.rows = []

    def log_flow_job(self, **kw):
        self.rows.append(kw)


class _FakePool:
    def __init__(self, assigned=None):
        self._assigned = assigned

    def assigned_to(self, _user_id):
        return self._assigned


class LoggerTests(unittest.TestCase):
    def _logger(self, pool):
        m = _FakeMetrics()
        return ImageJobLogger(metrics=m, account_pool=pool, flow_account_id="default"), m

    def test_explicit_account_id_wins(self):
        lg, m = self._logger(_FakePool(assigned="poolacc"))
        lg.log(1, "gen", "narwhal", time.monotonic(), ok=True, charged=10, account_id="sub5-sell")
        self.assertEqual(m.rows[0]["account_id"], "sub5-sell")
        self.assertEqual(m.rows[0]["operation_type"], "image")
        self.assertEqual(m.rows[0]["status"], "success")

    def test_falls_back_to_assigned_then_default(self):
        lg, m = self._logger(_FakePool(assigned="poolacc"))
        lg.log(1, "edit", "narwhal", time.monotonic(), ok=False)
        self.assertEqual(m.rows[0]["account_id"], "poolacc")
        self.assertEqual(m.rows[0]["operation_type"], "edit")
        self.assertEqual(m.rows[0]["status"], "fail")
        self.assertEqual(m.rows[0]["error_type"], "gen_failed")

    def test_default_account_when_unassigned(self):
        lg, m = self._logger(_FakePool(assigned=None))
        lg.log(1, "gen", "narwhal", time.monotonic(), ok=True)
        self.assertEqual(m.rows[0]["account_id"], "default")

    def test_unknown_action_maps_to_image(self):
        lg, m = self._logger(_FakePool())
        lg.log(1, "weird", "narwhal", time.monotonic(), ok=True)
        self.assertEqual(m.rows[0]["operation_type"], "image")


if __name__ == "__main__":
    unittest.main()
