from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import account_onboarding as ao


class _FakeLocator:
    def __init__(self, *, visible: bool = False, text: str = "", on_click=None, on_fill=None) -> None:
        self.visible = visible
        self.text = text
        self.on_click = on_click
        self.on_fill = on_fill

    @property
    def first(self):
        return self

    async def count(self) -> int:
        return 1 if self.visible else 0

    async def is_visible(self, timeout: int = 0) -> bool:
        return self.visible

    async def scroll_into_view_if_needed(self, timeout: int = 0) -> None:
        return None

    async def click(self, timeout: int = 0) -> None:
        if self.on_click:
            self.on_click()

    async def fill(self, value: str, timeout: int = 0) -> None:
        if self.on_fill:
            self.on_fill(value)

    async def inner_text(self, timeout: int = 0) -> str:
        return self.text


class _FakeFlowPage:
    def __init__(
        self,
        *,
        body_text: str,
        sign_in_visible: bool = False,
        click_opens_google: bool = False,
    ) -> None:
        self.url = ao.FLOW_URL
        self.body_text = body_text
        self.sign_in_visible = sign_in_visible
        self.click_opens_google = click_opens_google

    async def goto(self, url: str, timeout: int = 0, wait_until: str = "") -> None:
        self.url = url

    async def wait_for_url(self, pattern: str, timeout: int = 0) -> None:
        return None

    async def wait_for_timeout(self, timeout: int) -> None:
        return None

    def _click_sign_in(self) -> None:
        if self.click_opens_google:
            self.url = "https://accounts.google.com/signin/v2/identifier"
            self.body_text = "Sign in"

    def get_by_role(self, role: str, name=None):
        return _FakeLocator(visible=self.sign_in_visible, on_click=self._click_sign_in)

    def get_by_text(self, pattern):
        return _FakeLocator(visible=self.sign_in_visible, on_click=self._click_sign_in)

    def locator(self, selector: str):
        if selector == "body":
            return _FakeLocator(visible=True, text=self.body_text)
        return _FakeLocator(visible=self.sign_in_visible, on_click=self._click_sign_in)


class _FakeMouse:
    async def wheel(self, x: int, y: int) -> None:
        return None

    async def click(self, x: int, y: int) -> None:
        return None


class _FakeKeyboard:
    async def press(self, key: str) -> None:
        return None


class _FakeFlowOnboardingPage:
    def __init__(self) -> None:
        self.url = ao.FLOW_URL
        self.step = "create_with_flow"
        self.clicks: list[str] = []
        self.mouse = _FakeMouse()
        self.keyboard = _FakeKeyboard()
        self.viewport_size = {"width": 1280, "height": 800}
        self.scrolled = False

    async def wait_for_load_state(self, state: str, timeout: int = 0) -> None:
        return None

    async def wait_for_timeout(self, timeout: int) -> None:
        return None

    async def evaluate(self, script: str) -> None:
        self.scrolled = True

    def _label(self) -> str:
        return {
            "create_with_flow": "Create with Flow",
            "next": "Next",
            "continue": "Continue",
            "new_project": "New Project",
        }.get(self.step, "")

    def _matches(self, pattern) -> bool:
        label = self._label()
        if not label:
            return False
        if hasattr(pattern, "fullmatch"):
            return bool(pattern.fullmatch(label))
        return str(pattern or "").lower() == label.lower()

    def _click_current(self) -> None:
        label = self._label()
        if label:
            self.clicks.append(label)
        if self.step == "create_with_flow":
            self.step = "next"
        elif self.step == "next":
            self.step = "continue"
        elif self.step == "continue":
            self.step = "new_project"
        elif self.step == "new_project":
            self.step = "project"
            self.url = f"{ao.FLOW_URL}/project/proj-123"

    def get_by_role(self, role: str, name=None):
        return _FakeLocator(visible=role == "button" and self._matches(name), on_click=self._click_current)

    def get_by_text(self, pattern):
        return _FakeLocator(visible=self._matches(pattern), on_click=self._click_current)

    def locator(self, selector: str):
        if selector == "body":
            return _FakeLocator(visible=True, text=self._label())
        if 'button:has-text("' in selector:
            text = selector.split('button:has-text("', 1)[1].split('"', 1)[0]
            return _FakeLocator(visible=text.lower() == self._label().lower(), on_click=self._click_current)
        return _FakeLocator()


class _FakeGoogleLoginPage:
    def __init__(self, *, phase: str = "email") -> None:
        self.url = (
            "https://accounts.google.com/v3/signin/challenge/pwd"
            if phase == "password_verify"
            else "https://accounts.google.com/"
        )
        self.phase = phase
        self.filled_email = ""
        self.filled_password = ""
        self.filled_totp = ""

    async def wait_for_timeout(self, timeout: int) -> None:
        return None

    def _body_text(self) -> str:
        if self.phase == "password_verify":
            return "To continue, first verify it's you"
        if self.phase == "totp":
            return "Enter a verification code from your authenticator app"
        return "Sign in"

    def _email_next(self) -> None:
        if self.filled_email:
            self.phase = "password"

    def _password_next(self) -> None:
        if self.filled_password:
            self.url = "https://myaccount.google.com/"

    def _totp_next(self) -> None:
        if self.filled_totp:
            self.url = "https://myaccount.google.com/"

    def locator(self, selector: str):
        if selector == "body":
            return _FakeLocator(visible=True, text=self._body_text())
        if selector in {'input[type="email"]', 'input[name="identifier"]', "#identifierId"}:
            return _FakeLocator(
                visible=self.phase == "email",
                on_fill=lambda value: setattr(self, "filled_email", value),
            )
        if selector in {'input[type="password"]', 'input[name="Passwd"]'}:
            return _FakeLocator(
                visible=self.phase in {"password", "password_verify"},
                on_fill=lambda value: setattr(self, "filled_password", value),
            )
        if selector in {'input[name="totpPin"]', 'input[type="tel"]'}:
            return _FakeLocator(
                visible=self.phase == "totp",
                on_fill=lambda value: setattr(self, "filled_totp", value),
            )
        if selector == "#identifierNext button":
            return _FakeLocator(visible=self.phase == "email", on_click=self._email_next)
        if selector == "#passwordNext button":
            return _FakeLocator(visible=self.phase in {"password", "password_verify"}, on_click=self._password_next)
        if selector == "#totpNext button":
            return _FakeLocator(visible=self.phase == "totp", on_click=self._totp_next)
        return _FakeLocator()

    def get_by_role(self, role: str, name=None):
        return _FakeLocator()

    def get_by_text(self, pattern):
        return _FakeLocator()


class AccountOnboardingHelperTests(unittest.TestCase):
    def test_totp_code_matches_rfc_vector(self) -> None:
        # RFC 6238 test secret for SHA1; at t=59 the 8-digit code is 94287082.
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        self.assertEqual(ao.totp_code(secret, now=59, digits=8), "94287082")

    def test_validate_2fa_code_accepts_one_time_code_only(self) -> None:
        self.assertEqual(ao.validate_2fa_code(" 123 456 "), "123456")
        with self.assertRaises(ao.AccountOnboardingError) as ctx:
            ao.validate_2fa_code("JBSWY3DPEHPK3PXP")
        self.assertEqual(ctx.exception.code, "invalid_two_fa_code")

    def test_progress_tracks_steps_and_resets_on_new_run(self) -> None:
        ao._PROGRESS.pop("subX", None)
        ao._stage("subX", "login_start")
        ao._stage("subX", "password_filled")
        p = ao.get_progress("subX")
        self.assertEqual(p["step"], 5)
        self.assertEqual(p["total"], 7)
        self.assertIn("парол", p["label"].lower())
        # A branch stage keeps the step but updates the label + done flag.
        ao._stage("subX", "needs_project")
        p = ao.get_progress("subX")
        self.assertEqual(p["step"], 5)
        self.assertTrue(p["done"])
        self.assertIn("проект", p["label"].lower())
        # A fresh login_start resets the counter.
        ao._stage("subX", "login_start")
        self.assertEqual(ao.get_progress("subX")["step"], 1)
        ao._PROGRESS.pop("subX", None)

    def test_get_progress_unknown_id_is_empty(self) -> None:
        self.assertEqual(ao.get_progress("nope-zzz"), {})

    def test_project_cta_matches_flow_actions_only(self) -> None:
        self.assertIsNotNone(ao.FLOW_PROJECT_CTA_RE.fullmatch("New project"))
        self.assertIsNotNone(ao.FLOW_PROJECT_CTA_RE.fullmatch("Create project"))
        self.assertIsNotNone(ao.FLOW_PROJECT_CTA_RE.fullmatch("New flow"))
        self.assertIsNotNone(ao.FLOW_CREATE_WITH_FLOW_RE.fullmatch("Create with Flow"))
        self.assertIsNone(ao.FLOW_PROJECT_CTA_RE.fullmatch("Create with Flow"))
        self.assertIsNone(ao.FLOW_PROJECT_CTA_RE.fullmatch("Create"))
        self.assertIsNone(ao.FLOW_PROJECT_CTA_RE.fullmatch("Create account"))

    def test_flow_signed_out_text_requires_login_phrase(self) -> None:
        self.assertTrue(ao._flow_text_looks_signed_out("Sign in with Google to continue"))
        self.assertTrue(ao._flow_text_looks_signed_out("Choose an account to continue"))
        self.assertFalse(ao._flow_text_looks_signed_out("New project\nManage your Google Account"))

    def test_project_cta_candidates_prioritize_new_project(self) -> None:
        labels = [label for label, _getter in ao._flow_project_cta_candidates(object())]
        self.assertEqual(labels[:3], [
            "new_project_button",
            "new_project_link",
            "new_project_text",
        ])
        self.assertIn("project_cta_button", labels)
        self.assertNotIn("create_with_flow_button", labels)
        self.assertNotIn("create_button", labels)

    def test_flow_setup_candidates_include_first_run_steps(self) -> None:
        labels = [label for label, _getter in ao._flow_setup_step_candidates(object())]
        self.assertIn("create_with_flow_button", labels)
        self.assertIn("flow_next_button", labels)
        self.assertIn("flow_continue_button", labels)

    def test_project_id_from_flow_project_urls(self) -> None:
        self.assertEqual(
            ao._project_id_from_url("https://labs.google/fx/tools/flow/project/proj-1"),
            "proj-1",
        )
        self.assertEqual(
            ao._project_id_from_url("https://api.example/v1/projects/proj-2/media"),
            "proj-2",
        )
        self.assertIsNone(ao._project_id_from_url("https://labs.google/fx/tools/flow"))

    def test_proxy_public_label_strips_credentials(self) -> None:
        proxy = "http://user:" + "pass@10.0.0.1:8118"
        self.assertEqual(
            ao.proxy_public_label(proxy),
            "http://10.0.0.1:8118",
        )

    def test_flow_account_entry_quotes_separators(self) -> None:
        proxy = "http://user:p" + "|ass@10.0.0.1:8118"
        entry = ao.flow_account_entry(ao.AccountEntry(
            account_id="sub7",
            profile_dir="./google_profile_sub7",
            proxy_url=proxy,
        ))

        self.assertIn("sub7=./google_profile_sub7", entry)
        self.assertIn("proxy=http://user:p%7C" + "ass@10.0.0.1:8118", entry)

    def test_append_flow_account_to_env_updates_existing_value_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "OTHER_SETTING=placeholder\n"
                "FLOW_ACCOUNTS=main=./google_profile\n",
                encoding="utf-8",
            )

            proxy = "http://user:" + "pass@10.0.0.1:8118"
            result = ao.append_flow_account_to_env(
                ao.AccountEntry(
                    account_id="sub7",
                    profile_dir="./google_profile_sub7",
                    proxy_url=proxy,
                ),
                env_path=env_path,
            )

            text = env_path.read_text(encoding="utf-8")
            self.assertEqual(result["accounts_count"], 2)
            self.assertIn("FLOW_ACCOUNTS=main=./google_profile;sub7=./google_profile_sub7", text)
            self.assertIn("proxy=" + proxy, text)
            self.assertNotIn(".tmp.", "".join(p.name for p in Path(tmp).iterdir()))

    def test_append_flow_account_to_env_rejects_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("FLOW_ACCOUNTS=sub7=./google_profile_sub7\n", encoding="utf-8")

            with self.assertRaises(ao.AccountOnboardingError) as ctx:
                ao.append_flow_account_to_env(
                    ao.AccountEntry(account_id="sub7", profile_dir="./other"),
                    env_path=env_path,
                )

            self.assertEqual(ctx.exception.code, "account_exists")

    def test_remove_flow_account_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "FLOW_ACCOUNTS=main=./google_profile;sub7=./google_profile_sub7|proxy=http://127.0.0.1:8126\n",
                encoding="utf-8",
            )

            result = ao.remove_flow_account_from_env("sub7", env_path=env_path)

            self.assertEqual(result["accounts_count"], 1)
            text = env_path.read_text(encoding="utf-8")
            self.assertIn("main=./google_profile", text)
            self.assertNotIn("sub7=", text)


class AccountOnboardingFlowStatusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        ao._PROGRESS.pop("subT", None)

    async def asyncTearDown(self) -> None:
        ao._PROGRESS.pop("subT", None)

    async def test_open_flow_status_does_not_treat_signed_out_flow_as_active(self) -> None:
        page = _FakeFlowPage(
            body_text="Sign in with Google",
            sign_in_visible=True,
            click_opens_google=True,
        )

        result = await ao._open_flow_status(page, 1000)

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "needs_challenge")
        self.assertEqual(result["reason"], "flow_signed_out_google_login")
        self.assertEqual(result["final_host"], "accounts.google.com")

    async def test_open_flow_status_treats_logged_in_flow_as_active(self) -> None:
        page = _FakeFlowPage(body_text="New project\nCreate project")

        result = await ao._open_flow_status(page, 1000)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["reason"], "flow_opened")

    async def test_ensure_flow_project_runs_first_run_setup_steps(self) -> None:
        page = _FakeFlowOnboardingPage()

        result = await ao._ensure_flow_project(page)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["reason"], "project_created")
        self.assertEqual(result["final_host"], "labs.google")
        self.assertTrue(page.scrolled)
        self.assertEqual(page.clicks, ["Create with Flow", "Next", "Continue", "New Project"])

    async def test_submit_google_login_fills_direct_accounts_page(self) -> None:
        page = _FakeGoogleLoginPage()

        result = await ao._submit_google_login_if_needed(
            page,
            account_id="subT",
            email="account@example.com",
            password="secret-password",
            deadline_ms=30_000,
        )

        self.assertIsNone(result)
        self.assertEqual(page.url, "https://myaccount.google.com/")
        self.assertEqual(page.filled_email, "account@example.com")
        self.assertEqual(page.filled_password, "secret-password")

    async def test_submit_google_login_keeps_2fa_session_without_totp_secret(self) -> None:
        page = _FakeGoogleLoginPage(phase="totp")

        result = await ao._submit_google_login_if_needed(
            page,
            account_id="subT",
            email="account@example.com",
            password="secret-password",
            deadline_ms=30_000,
        )

        self.assertIsNotNone(result)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "needs_2fa")
        self.assertEqual(result["reason"], "two_fa_code_required")

    async def test_submit_google_login_fills_password_before_challenge_classification(self) -> None:
        page = _FakeGoogleLoginPage(phase="password_verify")

        result = await ao._submit_google_login_if_needed(
            page,
            account_id="subT",
            email="account@example.com",
            password="secret-password",
            deadline_ms=30_000,
        )

        self.assertIsNone(result)
        self.assertEqual(page.url, "https://myaccount.google.com/")
        self.assertEqual(page.filled_password, "secret-password")

    async def test_submit_google_login_can_auto_submit_totp_secret(self) -> None:
        page = _FakeGoogleLoginPage(phase="totp")

        result = await ao._submit_google_login_if_needed(
            page,
            account_id="subT",
            email="account@example.com",
            password="secret-password",
            totp_secret="GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
            deadline_ms=30_000,
        )

        self.assertIsNone(result)
        self.assertEqual(page.url, "https://myaccount.google.com/")
        self.assertRegex(page.filled_totp, r"^\d{6}$")


if __name__ == "__main__":
    unittest.main()
