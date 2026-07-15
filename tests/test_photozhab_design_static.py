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
        cls.landing_css = (SITE / "landing.css").read_text(encoding="utf-8")
        cls.landing_js = (SITE / "landing.js").read_text(encoding="utf-8")
        cls.hero_js = (SITE / "hero-experiment.js").read_text(encoding="utf-8")
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
            'class="pz-hero"',
            'class="pz-trust"',
            'id="features"',
            'id="showcase-title"',
            'id="steps-title"',
            'id="prices"',
            'class="pz-section pz-messengers"',
            'id="faq"',
            'class="pz-cta"',
            'class="pz-footer"',
        ):
            self.assertIn(marker, self.home)
        self.assertIn("Space+Grotesk", self.styles)
        self.assertIn("--pz-paper:        #0b0d0c", self.styles)
        self.assertIn("--pz-lime:         #d3f36b", self.styles)
        self.assertIn("@media (max-width: 480px)", self.landing_css)

    def test_landing_skin_is_isolated_from_legacy_public_components(self) -> None:
        self.assertIn('href="/landing.css?v=20260715-b4"', self.home)
        self.assertIn('src="/landing.js?v=20260715-a1"', self.home)
        self.assertNotIn("Claude Design B v2: public landing", self.styles)
        self.assertNotIn('class="price-row', self.home)
        self.assertNotIn('class="showcase-', self.home)
        self.assertIn(".pz-prices__row--best", self.landing_css)
        self.assertIn("grid-template-columns: 130px minmax(0, 1fr) auto 110px", self.landing_css)
        self.assertIn("white-space: nowrap", self.landing_css)

    def test_landing_uses_real_generated_images_and_video(self) -> None:
        asset_dir = SITE / "assets" / "showcase"
        expected = {
            "neon.webp": 50_000,
            "product.webp": 30_000,
            "motion.webp": 50_000,
            "forest.webp": 100_000,
            "forest-video-poster.webp": 50_000,
            "forest-video.mp4": 500_000,
        }
        for name, minimum in expected.items():
            path = asset_dir / name
            self.assertTrue(path.is_file(), name)
            self.assertGreater(path.stat().st_size, minimum, name)
            self.assertIn(f'/assets/showcase/{name}', self.home)
        for name in expected:
            data = (asset_dir / name).read_bytes()[:16]
            if name.endswith(".webp"):
                self.assertEqual(data[:4], b"RIFF", name)
                self.assertEqual(data[8:12], b"WEBP", name)
            else:
                self.assertIn(b"ftyp", data, name)
        self.assertIn("data-showcase-video muted autoplay loop playsinline", self.home)
        self.assertIn("prefers-reduced-motion: reduce", self.landing_css)
        self.assertIn("reducedMotion.matches", self.landing_js)
        self.assertIn("IntersectionObserver", self.landing_js)

    def test_final_archive_hero_and_messenger_composition_are_complete(self) -> None:
        self.assertIn("Идея → картинка<br>за 1 минуту", self.home)
        self.assertNotIn("за 9 секунд", self.home)
        self.assertNotIn("~12 сек", self.home)
        for marker in (
            'class="pz-demo"',
            '/assets/showcase/neon.webp',
            'id="messengers-title"',
            'class="pz-phone"',
            '/assets/showcase/motion.webp',
            'class="pz-sticky-cta"',
        ):
            self.assertIn(marker, self.home)
        self.assertIn("@keyframes pz-demo-image", self.landing_css)
        self.assertIn(".pz-messengers__grid", self.landing_css)

    def test_landing_runs_a_stable_five_way_hero_experiment(self) -> None:
        asset_dir = SITE / "assets" / "heroes"
        expected = {
            "hero-a-creator.webp", "hero-b-frog.webp", "hero-c-botanical.webp",
            "hero-d-product.webp", "hero-e-cinematic.webp",
        }
        self.assertIn('src="/hero-experiment.js?v=20260714-a1"', self.home)
        self.assertIn('class="pz-hero__image" role="img"', self.home)
        self.assertNotIn('class="pz-hero__image" src=', self.home)
        for name in expected:
            path = asset_dir / name
            self.assertTrue(path.is_file(), name)
            self.assertGreater(path.stat().st_size, 80_000, name)
            self.assertEqual(path.read_bytes()[:4], b"RIFF", name)
            self.assertIn(f'/assets/heroes/{name}', self.hero_js + self.landing_css)
        self.assertIn('const variants = Object.freeze(["a", "b", "c", "d", "e"])', self.hero_js)
        self.assertIn("Max-Age=7776000", self.hero_js)
        self.assertIn('fetch("/web/api/experiment"', self.landing_js)
        self.assertNotIn("localStorage", self.hero_js + self.landing_js)

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
        self.assertIn("rememberRequest(prompt,", self.app_js)
        self.assertIn('ONBOARDING_KEY_PREFIX = "photozhabOnboardingV1"', self.app_js)
        self.assertNotIn("localStorage.setItem(\"gallery", self.app_js)
        self.assertIn("Claude Design B v2: generation app", self.app_css)
        self.assertIn("grid-template-columns: 280px", self.app_css)
        self.assertIn("@media (max-width: 820px)", self.app_css)
        for marker in ('id="model-options"', 'id="aspect-options"', 'id="count-range"', 'class="send-button-label"'):
            self.assertIn(marker, self.app)
        for marker in ('id="open-gallery"', 'id="gallery-view"', 'id="gallery-grid"', 'id="payment-submit"'):
            self.assertIn(marker, self.app)
        self.assertIn("syncModelButtons", self.app_js)
        self.assertIn("syncAspectButtons", self.app_js)
        self.assertIn("renderGallery", self.app_js)
        self.assertIn("state.gallery.unshift", self.app_js)
        self.assertIn("selectPack", self.app_js)
        self.assertIn('if (state.mode === "image") setMode("edit")', self.app_js)
        self.assertIn("width: min(820px", self.app_css)
        self.assertIn(".generation-progress-card", self.app_css)
        for marker in ('id="improve-button"', 'id="improve-panel"', 'id="onboarding"', 'id="skip-onboarding"'):
            self.assertIn(marker, self.app)
        self.assertIn('composer.dataset.mode = mode', self.app_js)
        self.assertIn('.composer[data-mode="video"] .composer-field--models', self.app_css)
        self.assertIn('.onboarding-topline', self.app_css)
        self.assertIn('class="count-slider__ticks"', self.app)
        self.assertIn('get_prompt_history', (ROOT / "channels" / "web" / "app.py").read_text(encoding="utf-8"))

    def test_secondary_pages_share_archive_three_chrome(self) -> None:
        for name in (
            "generaciya-kartinok.html", "generaciya-video.html", "nano-banana.html",
            "max-bot.html", "oferta.html", "privacy.html", "consent.html",
        ):
            source = (SITE / name).read_text(encoding="utf-8")
            self.assertIn('class="topbar-cta" href="/app.html"', source, name)
            self.assertIn('<svg width="18" height="18" viewBox="0 0 32 32">', source, name)
            self.assertIn('styles.css?v=20260715-b4', source, name)
        self.assertIn("Design archive 3 — shared chrome", self.styles)

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
        self.assertIn('<span class="sb-icon">▣</span> Обзор', self.admin)
        self.assertNotIn('<span class="sb-icon">📊</span>', self.admin)
        self.assertIn("@media(max-width:760px)", self.admin)
        for marker in ('id="user-detail-overlay"', 'openUserDetail(', 'renderUserDetail(', 'role="button" tabindex="0"'):
            self.assertIn(marker, self.admin)


if __name__ == "__main__":
    unittest.main()
