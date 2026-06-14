from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class LiveTelegramProbeSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = (PROJECT_ROOT / "tools" / "live_telegram_probe.py").read_text(encoding="utf-8")

    def test_callback_click_reports_edited_message_snapshot(self) -> None:
        self.assertIn("clicked_message_after = None", self.source)
        self.assertIn('if sent_kind == "click":', self.source)
        self.assertIn('clicked_id = int((click_result or {}).get("message_id") or 0)', self.source)
        self.assertIn("refreshed = await client.get_messages(entity, ids=clicked_id)", self.source)
        self.assertIn("clicked_message_after = _summarize_message(refreshed, args.text_limit)", self.source)
        self.assertIn('"clicked_message_after": clicked_message_after', self.source)


if __name__ == "__main__":
    unittest.main()
