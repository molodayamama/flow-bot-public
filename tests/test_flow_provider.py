from __future__ import annotations

import ast
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

        self.assertIs(FlowHttpClient, C2)
        self.assertIs(SessionKeeper, K2)

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

    def test_provider_classes_defined_in_flow_provider_not_flow_bot(self) -> None:
        client_src = (PROJECT_ROOT / "flow_provider" / "client.py").read_text(encoding="utf-8")
        flow_bot_src = (PROJECT_ROOT / "flow_bot.py").read_text(encoding="utf-8")

        self.assertIn("class SessionKeeper:", client_src)
        self.assertIn("class FlowHttpClient:", client_src)
        # The monolith must no longer DEFINE them (only import/re-export).
        self.assertNotIn("class SessionKeeper:", flow_bot_src)
        self.assertNotIn("class FlowHttpClient:", flow_bot_src)
        self.assertIn("from flow_provider.client import", flow_bot_src)

    def test_provider_package_does_not_import_aiogram_or_flow_bot(self) -> None:
        for name in ("client.py", "runtime_config.py", "__init__.py"):
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


if __name__ == "__main__":
    unittest.main()
