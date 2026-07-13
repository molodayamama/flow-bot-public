"""Static accessibility/security contracts for the browser chat surface."""
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "deploy" / "photozhab"


class WebAppStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (SITE / "app.html").read_text(encoding="utf-8")
        cls.css = (SITE / "app.css").read_text(encoding="utf-8")
        cls.js = (SITE / "app.js").read_text(encoding="utf-8")
        cls.home = (SITE / "index.html").read_text(encoding="utf-8")

    def test_chat_surface_has_four_modes_balance_upload_and_payment(self) -> None:
        for mode in ('data-mode="image"', 'data-mode="edit"', 'data-mode="video"', 'data-mode="animate"'):
            self.assertIn(mode, self.html)
        self.assertIn('id="sidebar-balance"', self.html)
        self.assertIn('id="image-upload"', self.html)
        self.assertIn('id="payment-dialog"', self.html)
        self.assertIn('id="prompt"', self.html)

    def test_app_uses_existing_design_tokens_and_is_responsive(self) -> None:
        self.assertIn('href="/styles.css"', self.html)
        self.assertIn("var(--pz-paper)", self.css)
        self.assertIn("@media (max-width: 820px)", self.css)
        self.assertIn("@media (max-width: 560px)", self.css)
        self.assertIn("prefers-reduced-motion", self.css)

    def test_frontend_calls_only_same_origin_public_api(self) -> None:
        self.assertIn('api("/web/api/session"', self.js)
        self.assertIn('api("/web/api/generate"', self.js)
        self.assertIn('api("/web/api/payment"', self.js)
        self.assertNotIn("INTERNAL_API_TOKEN", self.html + self.js)
        self.assertNotIn("/internal/generate", self.html + self.js)
        self.assertNotIn("innerHTML", self.js)

    def test_generation_is_gated_by_three_account_providers(self) -> None:
        for marker in ('id="login-telegram"', 'id="login-max"', 'id="login-yandex"'):
            self.assertIn(marker, self.html)
        self.assertIn('id="auth-dialog"', self.html)
        self.assertIn('api("/web/api/auth/telegram/start"', self.js)
        self.assertIn('api("/web/api/auth/telegram/complete"', self.js)
        self.assertIn('api("/web/api/auth/max"', self.js)
        self.assertIn('api("/web/api/auth/logout"', self.js)
        self.assertIn("if (!state.session?.authenticated)", self.js)
        self.assertNotIn("Баланс привязан к этому браузеру", self.html)

    def test_app_has_csp_noindex_and_accessible_labels(self) -> None:
        self.assertIn('http-equiv="Content-Security-Policy"', self.html)
        self.assertIn('content="noindex, follow"', self.html)
        self.assertIn('aria-live="polite"', self.html)
        self.assertIn('aria-label="Описание результата"', self.html)

    def test_home_promotes_first_party_generation(self) -> None:
        self.assertIn('href="/app.html">Создать на сайте</a>', self.home)
        self.assertIn("прямо на сайте, в Telegram или MAX", self.home)


if __name__ == "__main__":
    unittest.main()
