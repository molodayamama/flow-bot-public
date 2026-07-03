"""Regression guard for the Phase 11 core split.

Each extracted core/domain module must be importable WITHOUT pulling in the
Telegram monolith (flow_bot). This locks in the "core + adapters" boundary:
any accidental core->flow_bot import (a cycle / layering violation) fails here.

Imports run in a fresh subprocess per module so a module imported earlier by
another test cannot mask a real dependency via a polluted sys.modules.
"""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Modules that make up the platform-neutral core / domain layer.
CORE_MODULES = [
    "textutil",
    "mediautil",
    "flow_core",
    "accounts.routing",
    "accounts.projects",
    "accounts.health",
    "product.marketplace",
    "product.marketplace_prompts",
    "product.video_reference",
    "product.streak",
    "product.ideas_hub",
    "config.video",
    "storage.session_state",
    "billing.credit_gate",
    "billing.pricing",
    "referrals.service",
    "core.user_identity",
    "channels.base",
]


class CoreIndependenceTests(unittest.TestCase):
    def _import_isolated(self, module: str) -> str:
        """Import `module` in a fresh interpreter; return 'yes'/'no' for flow_bot."""
        code = (
            "import sys, importlib;"
            f"importlib.import_module({module!r});"
            "print('yes' if 'flow_bot' in sys.modules else 'no')"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            proc.returncode, 0,
            msg=f"importing {module} failed:\n{proc.stderr}",
        )
        return proc.stdout.strip().splitlines()[-1]

    def test_core_modules_do_not_import_flow_bot(self):
        offenders = []
        for module in CORE_MODULES:
            if self._import_isolated(module) != "no":
                offenders.append(module)
        self.assertEqual(
            offenders, [],
            msg=f"core modules that transitively import flow_bot: {offenders}",
        )

    def test_core_module_sources_have_no_flow_bot_import(self):
        # Doc references ("moved out of flow_bot") are fine; an import statement
        # is not. Catch `import flow_bot` / `from flow_bot import ...`.
        pattern = re.compile(r"^\s*(?:import\s+flow_bot|from\s+flow_bot\b)", re.M)
        offenders = []
        for module in CORE_MODULES:
            path = PROJECT_ROOT / (module.replace(".", "/") + ".py")
            if pattern.search(path.read_text(encoding="utf-8")):
                offenders.append(module)
        self.assertEqual(
            offenders, [],
            msg=f"core module sources import flow_bot: {offenders}",
        )


if __name__ == "__main__":
    unittest.main()
