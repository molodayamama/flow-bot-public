from __future__ import annotations

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FlowBotRecoveryStaticTests(unittest.TestCase):
    def test_session_keeper_has_browser_recovery_hooks(self) -> None:
        source = (PROJECT_ROOT / "flow_bot.py").read_text(encoding="utf-8")

        self.assertIn("async def _ensure_browser_locked", source)
        self.assertIn("def _browser_alive", source)
        self.assertIn("def _is_target_closed_error", source)
        self.assertIn("Browser context closed while reading cookies", source)
        self.assertIn("await self._ensure_browser_locked()", source)

    def test_generation_handler_catches_unexpected_client_errors(self) -> None:
        source = (PROJECT_ROOT / "flow_bot.py").read_text(encoding="utf-8")

        self.assertIn('log.exception("Generation failed', source)  # may have extra args
        # Генерация роутится по аккаунту пула (multi-account).
        self.assertIn("result = await _client_for_acc(acc_id).generate_images", source)
