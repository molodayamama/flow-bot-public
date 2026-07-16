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
        self.assertIn('id="model-options"', self.html)
        self.assertIn('id="aspect-options"', self.html)
        self.assertIn('id="count-range"', self.html)

    def test_app_uses_existing_design_tokens_and_is_responsive(self) -> None:
        self.assertIn('href="/styles.css', self.html)
        self.assertIn("var(--pz-paper)", self.css)
        self.assertIn("@media (max-width: 820px)", self.css)
        self.assertIn("@media (max-width: 560px)", self.css)
        self.assertIn("prefers-reduced-motion", self.css)

    def test_frontend_calls_only_same_origin_public_api(self) -> None:
        self.assertIn('api("/web/api/session"', self.js)
        self.assertIn('api("/web/api/generate"', self.js)
        self.assertIn('clipboardData?.files', self.js)
        self.assertIn('chat_id: state.chatId', self.js)
        self.assertIn('async function openChat', self.js)
        self.assertIn('api(`/web/api/chats/${encodeURIComponent(chatId)}`)', self.js)
        self.assertIn('api("/web/api/payment"', self.js)
        self.assertNotIn("INTERNAL_API_TOKEN", self.html + self.js)
        self.assertNotIn("/internal/generate", self.html + self.js)
        self.assertNotIn("innerHTML", self.js)
        self.assertIn('api("/web/api/generate"', self.js)

    def test_generation_is_gated_by_three_account_providers(self) -> None:
        for marker in ('id="login-telegram"', 'id="login-max"', 'id="login-yandex"'):
            self.assertIn(marker, self.html)
        self.assertIn('id="auth-dialog"', self.html)
        self.assertIn('api("/web/api/auth/telegram/start"', self.js)
        self.assertIn('api("/web/api/auth/telegram/complete"', self.js)
        self.assertNotIn('id="telegram-code-form"', self.html)
        self.assertNotIn('id="telegram-code"', self.html)
        self.assertNotIn('telegramCode', self.js)
        self.assertNotIn('telegramForm', self.js)
        self.assertNotIn('renderTelegramCode', self.js)
        self.assertIn('api("/web/api/auth/max"', self.js)
        self.assertIn('api("/web/api/auth/logout"', self.js)
        self.assertIn("if (!state.session?.authenticated)", self.js)
        self.assertIn("бот подтвердит вход без кода", self.html)
        self.assertNotIn("Баланс привязан к этому браузеру", self.html)

    def test_mobile_telegram_login_uses_same_tab_and_restores_pending_polling(self) -> None:
        self.assertIn('matchMedia("(pointer: coarse)")', self.js)
        self.assertIn("window.innerWidth <= 820", self.js)
        self.assertIn("window.location.assign(result.url)", self.js)
        self.assertIn('sessionStorage.setItem(TELEGRAM_PENDING_KEY', self.js)
        self.assertIn("TELEGRAM_PENDING_MAX_AGE", self.js)
        self.assertIn("restoreTelegramPending()", self.js)
        self.assertIn("startTelegramLoginPolling()", self.js)
        self.assertNotIn('sessionStorage.setItem(TELEGRAM_PENDING_KEY, result.url)', self.js)

    def test_unavailable_yandex_login_explains_state(self) -> None:
        self.assertIn('id="login-yandex" href="#" aria-disabled="true"', self.html)
        self.assertIn("handleYandexLogin", self.js)
        self.assertIn("Яндекс ID пока недоступен", self.js)
        self.assertIn("consumeAuthResult", self.js)
        self.assertNotIn(
            '.auth-provider[aria-disabled="true"] { pointer-events: none', self.css
        )

    def test_oauth_return_waits_for_authoritative_session_before_dialog(self) -> None:
        self.assertIn("async function initializeApp()", self.js)
        self.assertIn('await refreshSession({retryAuthenticated: authResult === "success"})', self.js)
        self.assertIn("state.initialized = true", self.js)
        self.assertIn("state.session?.authenticated", self.js)
        self.assertIn("if (state.initialized) refreshSession()", self.js)
        self.assertNotIn(
            "consumeAuthResult();\nif (!elements.authDialog.open) elements.authDialog.showModal();",
            self.js,
        )

    def test_app_has_csp_noindex_and_accessible_labels(self) -> None:
        self.assertIn('http-equiv="Content-Security-Policy"', self.html)
        self.assertIn('content="noindex, follow"', self.html)
        self.assertIn('aria-live="polite"', self.html)
        self.assertIn('aria-label="Описание результата"', self.html)

    def test_archive_three_onboarding_and_backend_prompt_helper_are_real_ui(self) -> None:
        for marker in ('id="onboarding"', 'id="skip-onboarding"', 'id="improve-button"', 'id="improve-variants"'):
            self.assertIn(marker, self.html)
        self.assertIn('api("/web/api/prompt-improve"', self.js)
        self.assertIn("AI-варианты из backend", self.html)
        self.assertNotIn("function improvedPromptVariants()", self.js)
        self.assertNotIn("state.improveRound", self.js)
        self.assertIn("min-height: 54px", self.css)

    def test_video_models_have_their_own_overflow_rail(self) -> None:
        self.assertIn('elements.composer.dataset.mode = mode', self.js)
        self.assertIn('.composer[data-mode="video"] .composer-field--models', self.css)
        self.assertIn("overflow-x: auto", self.css)

    def test_result_actions_download_and_reuse_real_media(self) -> None:
        self.assertIn("media.download_url || media.url", self.js)
        self.assertIn('link.download = media.type === "video"', self.js)
        self.assertIn('useResultAsSource(media, "edit"', self.js)
        self.assertIn('useResultAsSource(media, "animate"', self.js)
        self.assertIn("async function animateReferenceTransfer", self.js)
        self.assertIn("media-flight-clone", self.js)
        self.assertIn("prefers-reduced-motion: reduce", self.css)
        self.assertIn(".media-result-actions", self.css)
        self.assertIn("app.js?v=20260716-a2", self.html)

    def test_low_credit_cta_focus_and_edit_button_contracts(self) -> None:
        self.assertIn('"Пополнить баланс"', self.js)
        self.assertIn("openPayment()", self.js)
        self.assertIn("generation-error-actions", self.js)
        self.assertIn(".composer textarea:focus-visible", self.css)
        self.assertIn("outline: 0", self.css)
        self.assertIn("media-result-action--edit", self.js)
        self.assertIn(".media-result-actions .media-result-action--edit", self.css)
        self.assertIn("white-space: nowrap", self.css)

    def test_home_promotes_first_party_generation(self) -> None:
        self.assertIn('href="/app.html">Создать на сайте</a>', self.home)
        self.assertIn("прямо на сайте, в Telegram или MAX", self.home)


if __name__ == "__main__":
    unittest.main()
