from __future__ import annotations

import unittest
from pathlib import Path

from flow_provider.client import FlowHttpClient, SessionKeeper
from flow_provider.request_policy import (
    AGENT_RECAPTCHA_ACTION,
    AGENT_RECAPTCHA_ACTION_CANDIDATES,
    RECAPTCHA_ACTIONS,
    VIDEO_GEN_403_BACKOFF_SEC,
    VIDEO_GEN_MAX_ATTEMPTS,
    VIDEO_RECAPTCHA_ACTION,
    build_flow_headers,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FlowProviderRequestPolicyTests(unittest.TestCase):
    def test_build_flow_headers_preserves_the_existing_contract(self) -> None:
        captured_headers = {
            "user-agent": "test-browser",
            "sec-ch-ua": '"Test";v="1"',
            "x-browser-validation": "test-validation",
            "ignored-header": "must-not-leak",
        }

        headers = build_flow_headers({
            "bearer": "test-bearer",
            "headers": captured_headers,
        })

        self.assertEqual(headers, {
            "Authorization": "Bearer test-bearer",
            "Content-Type": "text/plain;charset=UTF-8",
            "Accept": "*/*",
            "Accept-Language": "ru,ru-RU;q=0.9,en-US;q=0.8,en;q=0.7",
            "Origin": "https://labs.google",
            "Referer": "https://labs.google/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
            "User-Agent": "test-browser",
            "Sec-Ch-Ua": '"Test";v="1"',
            "X-Browser-Validation": "test-validation",
        })
        self.assertEqual(captured_headers["ignored-header"], "must-not-leak")

    def test_legacy_class_policy_attributes_remain_available(self) -> None:
        self.assertIs(SessionKeeper.RECAPTCHA_ACTIONS, RECAPTCHA_ACTIONS)
        self.assertIs(
            SessionKeeper.AGENT_RECAPTCHA_ACTION_CANDIDATES,
            AGENT_RECAPTCHA_ACTION_CANDIDATES,
        )
        self.assertEqual(SessionKeeper.VIDEO_RECAPTCHA_ACTION, VIDEO_RECAPTCHA_ACTION)
        self.assertEqual(SessionKeeper.AGENT_RECAPTCHA_ACTION, AGENT_RECAPTCHA_ACTION)
        self.assertEqual(SessionKeeper.VIDEO_GEN_MAX_ATTEMPTS, VIDEO_GEN_MAX_ATTEMPTS)
        self.assertEqual(SessionKeeper.VIDEO_GEN_403_BACKOFF_SEC, VIDEO_GEN_403_BACKOFF_SEC)

    def test_http_client_compatibility_method_delegates_to_shared_builder(self) -> None:
        client = FlowHttpClient(object())
        session = {"bearer": "test-bearer", "headers": {}}

        self.assertEqual(client._build_headers(session), build_flow_headers(session))

    def test_provider_classes_have_no_runtime_cross_references(self) -> None:
        source = (PROJECT_ROOT / "flow_provider" / "client.py").read_text(encoding="utf-8")
        keeper_block = source[source.index("class SessionKeeper:"):source.index("class FlowHttpClient:")]
        http_block = source[source.index("class FlowHttpClient:"):]

        self.assertNotIn("FlowHttpClient(", keeper_block)
        self.assertNotIn("SessionKeeper.", http_block)


if __name__ == "__main__":
    unittest.main()
