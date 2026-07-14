"""Static contracts for the production adaptation of Claude Design B v2."""
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "deploy" / "photozhab"


class PhotozhabDesignStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.home = (SITE / "index.html").read_text(encoding="utf-8")
        cls.styles = (SITE / "styles.css").read_text(encoding="utf-8")
        cls.app = (SITE / "app.html").read_text(encoding="utf-8")
        cls.app_css = (SITE / "app.css").read_text(encoding="utf-8")
        cls.app_js = (SITE / "app.js").read_text(encoding="utf-8")
        cls.admin = (SITE / "admin.html").read_text(encoding="utf-8")

    def test_design_export_runtime_and_placeholders_are_not_shipped(self) -> None:
        shipped = self.home + self.app + self.admin
        for marker in ("<x-dc", "<x-import", "<sc-if", "<sc-for", "support.js"):
            self.assertNotIn(marker, shipped)
        self.assertNotIn('href="#" style=', shipped)
        self.assertNotIn('onClick="{{', shipped)

    def test_public_landing_contains_every_b_v2_section(self) -> None:
        for marker in (
            'class="landing-hero"',
            'class="trust-strip"',
            'id="features"',
            'id="showcase-title"',
            'id="steps-title"',
            'id="prices"',
            'id="faq"',
            'class="landing-cta"',
            'class="design-footer"',
        ):
            self.assertIn(marker, self.home)
        self.assertIn("Space+Grotesk", self.styles)
        self.assertIn("--pz-paper:        #0b0d0c", self.styles)
        self.assertIn("--pz-lime:         #d3f36b", self.styles)
        self.assertIn("@media (max-width: 480px)", self.styles)

    def test_generation_app_keeps_real_contracts_under_new_skin(self) -> None:
        for marker in (
            'id="new-chat"',
            'id="chat-history-list"',
            'id="prompt"',
            'id="send-button"',
            'id="auth-dialog"',
            'id="payment-dialog"',
        ):
            self.assertIn(marker, self.app)
        self.assertIn("rememberRequest(prompt)", self.app_js)
        self.assertNotIn("localStorage", self.app_js)
        self.assertIn("Claude Design B v2: generation app", self.app_css)
        self.assertIn("grid-template-columns: 264px", self.app_css)
        self.assertIn("@media (max-width: 820px)", self.app_css)

    def test_admin_uses_b_v2_tokens_without_mocking_live_values(self) -> None:
        for tab in (
            "overview", "accounts", "messages", "labels", "prices", "settings",
            "support", "users", "sellers", "referrals", "analytics", "adcalc",
        ):
            self.assertIn(f'data-tab="{tab}"', self.admin)
        self.assertIn("--pz-paper:       #0b0d0c", self.admin)
        self.assertIn("--pz-lime:        #d3f36b", self.admin)
        self.assertIn("--font-display:", self.admin)
        self.assertIn('id="stat-rev-today">—</div>', self.admin)
        self.assertIn('id="stat-rev-total">—</div>', self.admin)
        self.assertIn("@media(max-width:760px)", self.admin)


if __name__ == "__main__":
    unittest.main()
