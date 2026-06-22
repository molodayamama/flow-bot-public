from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import account_onboarding as ao


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


if __name__ == "__main__":
    unittest.main()
