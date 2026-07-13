from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FlowProviderExtractionTests(unittest.TestCase):
    """PR-2a: the Flow provider now lives in flow_provider/, and flow_bot only
    re-exports it. These are source/import-level checks (no browser, no network).
    """

    def test_provider_classes_are_importable_from_package(self) -> None:
        from flow_provider import FlowHttpClient, SessionKeeper
        from flow_provider.client import (
            FlowHttpClient as C2,
            SessionKeeper as K2,
        )
        from flow_provider.http_client import FlowHttpClient as C3
        from flow_provider.session_keeper import SessionKeeper as K3

        self.assertIs(FlowHttpClient, C2)
        self.assertIs(SessionKeeper, K2)
        self.assertIs(FlowHttpClient, C3)
        self.assertIs(SessionKeeper, K3)

    def test_flow_bot_reexports_the_same_objects(self) -> None:
        import flow_bot
        import flow_provider.client as provider

        # Canonical home is flow_provider; flow_bot must expose the SAME objects.
        self.assertIs(flow_bot.SessionKeeper, provider.SessionKeeper)
        self.assertIs(flow_bot.FlowHttpClient, provider.FlowHttpClient)
        # Config re-exported too (env-derived constant resolved via dotenv).
        self.assertEqual(flow_bot.log.name, "flow_bot")
        self.assertTrue(hasattr(flow_bot, "IDLE_PARK_SEC"))
        self.assertTrue(hasattr(flow_bot, "_effective_proxy_url"))

    def test_provider_classes_have_separate_canonical_modules(self) -> None:
        client_src = (PROJECT_ROOT / "flow_provider" / "client.py").read_text(encoding="utf-8")
        keeper_src = (PROJECT_ROOT / "flow_provider" / "session_keeper.py").read_text(encoding="utf-8")
        http_src = (PROJECT_ROOT / "flow_provider" / "http_client.py").read_text(encoding="utf-8")
        flow_bot_src = (PROJECT_ROOT / "flow_bot.py").read_text(encoding="utf-8")

        self.assertIn("class SessionKeeper:", keeper_src)
        self.assertNotIn("class FlowHttpClient:", keeper_src)
        self.assertIn("class FlowHttpClient:", http_src)
        self.assertNotIn("class SessionKeeper:", http_src)
        self.assertNotIn("class SessionKeeper:", client_src)
        self.assertNotIn("class FlowHttpClient:", client_src)
        self.assertNotIn("class SessionKeeper:", flow_bot_src)
        self.assertNotIn("class FlowHttpClient:", flow_bot_src)
        self.assertIn("from flow_provider import FlowHttpClient, SessionKeeper", flow_bot_src)

    def test_provider_package_does_not_import_aiogram_or_flow_bot(self) -> None:
        for name in (
            "client.py",
            "http_client.py",
            "interfaces.py",
            "request_policy.py",
            "runtime_config.py",
            "session_keeper.py",
            "__init__.py",
        ):
            src = (PROJECT_ROOT / "flow_provider" / name).read_text(encoding="utf-8")
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    mods = {a.name.split(".")[0] for a in node.names}
                    self.assertNotIn("aiogram", mods, name)
                    self.assertNotIn("flow_bot", mods, name)
                elif isinstance(node, ast.ImportFrom):
                    root = (node.module or "").split(".")[0]
                    self.assertNotIn(root, {"aiogram", "flow_bot"}, name)

    def test_keeper_has_no_unreachable_paid_captcha_or_generation_paths(self) -> None:
        keeper = (PROJECT_ROOT / "flow_provider" / "session_keeper.py").read_text(encoding="utf-8")

        solve_start = keeper.index("async def solve_captcha")
        solve_end = keeper.index("async def _ensure_flow_page_loaded", solve_start)
        solve_block = keeper[solve_start:solve_end]
        self.assertIn("return await self._solve_via_browser_js(action)", solve_block)
        for dead_name in (
            "_provider_order",
            "_solve_via_2captcha",
            "_solve_via_capmonster",
            "_proxy_fields_for_capmonster",
            "_get_fresh_captcha",
        ):
            self.assertNotIn(f"def {dead_name}", keeper)

    def test_http_client_depends_on_the_narrow_session_protocol(self) -> None:
        from flow_provider.http_client import FlowHttpClient
        from flow_provider.interfaces import FlowSession
        from flow_provider.session_keeper import SessionKeeper

        self.assertEqual(
            inspect.signature(FlowHttpClient.__init__).parameters["keeper"].annotation,
            "FlowSession",
        )
        required_members = {
            "account_id",
            "api_proxy_url",
            "get_session",
            "solve_captcha",
            "post_json_via_browser",
            "generate_via_browser",
            "_refresh_bearer",
            "_flag_needs_relogin",
        }
        protocol_members = set(FlowSession.__annotations__) | set(FlowSession.__dict__)
        self.assertTrue(required_members <= protocol_members)
        keeper = SessionKeeper(account_id="test-protocol", api_proxy_url=None)
        for member in required_members:
            self.assertTrue(hasattr(keeper, member))


if __name__ == "__main__":
    unittest.main()
