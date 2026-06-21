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


if __name__ == "__main__":
    unittest.main()
