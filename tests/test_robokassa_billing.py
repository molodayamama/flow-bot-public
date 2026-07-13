"""Pure Robokassa callback tests, including external platform identities."""
from __future__ import annotations

import asyncio
import logging
import unittest
from types import SimpleNamespace

from billing.robokassa import (
    RobokassaConfig,
    RobokassaWebDeps,
    handle_result,
    internal_user_id,
    success,
)
from flow_core import robokassa_result_signature


class _Metrics:
    def __init__(self, status="new") -> None:
        self.status = status
        self.transactions = []
        self.events = []

    def record_transaction_status(self, **kwargs):
        self.transactions.append(kwargs)
        return self.status

    def log_event(self, *args, **kwargs):
        self.events.append((args, kwargs))


class RobokassaExternalIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = RobokassaConfig(
            merchant_login="merchant",
            password1="password-1",
            password2="password-2",
            hash_algo="sha256",
            inc_curr_label="",
            test=True,
            pay_url="https://pay.example",
            scope="consumer",
            consumer_result_url="",
            seller_result_url="",
            consumer_bot_username="bot",
            seller_bot_username="seller",
        )

    @staticmethod
    def _request(params):
        return SimpleNamespace(query=params, method="GET")

    def _params(self, *, user="-7", inv_id="9001"):
        shp = {"Shp_bot": "consumer", "Shp_pack": "trial", "Shp_user": user}
        signature = robokassa_result_signature(
            "45.00",
            inv_id,
            self.config.password2,
            shp_params=shp,
            algorithm=self.config.hash_algo,
        )
        return {
            "OutSum": "45.00",
            "InvId": inv_id,
            "SignatureValue": signature,
            **shp,
        }

    def _deps(self, *, status="new"):
        metrics = _Metrics(status)
        added = []
        notified = []

        def add_credits(user_id, credits):
            added.append((user_id, credits))
            return 75

        async def notify(user_id, credits, balance):
            notified.append((user_id, credits, balance))

        async def forward(scope, data):
            raise AssertionError("unexpected forward")

        deps = RobokassaWebDeps(
            config=self.config,
            is_configured=lambda: True,
            credit_pack=lambda pack: {"credits": 45, "stars": 35} if pack == "trial" else None,
            robokassa_pack_amount=lambda pack: "45.00",
            payment_signature=lambda *args, **kwargs: "",
            result_signature=robokassa_result_signature,
            clean_scope=lambda scope: scope,
            add_credits=add_credits,
            metrics=metrics,
            log=logging.getLogger(__name__),
            maybe_apply_referral_rewards=lambda *args, **kwargs: None,
            notify_success=notify,
            forward_result=forward,
        )
        return deps, metrics, added, notified

    def test_signed_negative_identity_is_credited_and_notified(self):
        deps, metrics, added, notified = self._deps()

        response = asyncio.run(handle_result(self._request(self._params()), deps))

        self.assertEqual(response.status, 200)
        self.assertEqual(response.text, "OK9001")
        self.assertEqual(added, [(-7, 45)])
        self.assertEqual(notified, [(-7, 45, 75)])
        self.assertEqual(metrics.transactions[0]["user_id"], -7)

    def test_duplicate_negative_identity_is_not_credited_twice(self):
        deps, _, added, notified = self._deps(status="duplicate")

        response = asyncio.run(handle_result(self._request(self._params()), deps))

        self.assertEqual(response.status, 200)
        self.assertEqual(added, [])
        self.assertEqual(notified, [])

    def test_bad_signature_cannot_reach_signed_identity_parser(self):
        deps, metrics, added, _ = self._deps()
        params = self._params()
        params["SignatureValue"] = "bad"

        response = asyncio.run(handle_result(self._request(params), deps))

        self.assertEqual(response.status, 400)
        self.assertEqual(metrics.transactions, [])
        self.assertEqual(added, [])

    def test_internal_user_id_accepts_signed_sqlite_range_only(self):
        self.assertEqual(internal_user_id("-7"), -7)
        self.assertEqual(internal_user_id("42"), 42)
        self.assertIsNone(internal_user_id("0"))
        self.assertIsNone(internal_user_id("+7"))
        self.assertIsNone(internal_user_id("7.0"))
        self.assertIsNone(internal_user_id(str(2**63)))

    def test_max_success_page_does_not_redirect_to_telegram(self):
        deps, _, _, _ = self._deps()

        response = asyncio.run(success(self._request(self._params()), deps))

        self.assertEqual(response.status, 200)
        self.assertIn("MAX", response.text)
        self.assertNotIn("t.me/", response.text)


if __name__ == "__main__":
    unittest.main()
