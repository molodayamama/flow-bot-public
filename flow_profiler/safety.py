from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class SafetySignal:
    kind: str
    severity: str


class SafetyClassifier:
    def classify(self, value: object) -> SafetySignal:
        if isinstance(value, int):
            return self._from_status(value)
        text = _normalize_text(value)
        if "429" in text or "too many requests" in text:
            return SafetySignal("rate_limited", "hard_stop")
        if "401" in text:
            return SafetySignal("auth_required", "hard_stop")
        if "403" in text:
            return SafetySignal("access_denied", "hard_stop")
        if "captcha" in text or "recaptcha" in text:
            return SafetySignal("captcha_required", "hard_stop")
        if "login" in text or "log in" in text or "sign in" in text:
            return SafetySignal("login_required", "hard_stop")
        if "account chooser" in text or "choose an account" in text:
            return SafetySignal("account_chooser", "hard_stop")
        if "auth challenge" in text or "session expired" in text:
            return SafetySignal("auth_challenge", "hard_stop")
        if "human verification" in text or "verify it" in text:
            return SafetySignal("human_verification", "hard_stop")
        if "unusual activity" in text or "suspicious activity" in text:
            return SafetySignal("account_risk", "hard_stop")
        if "account warning" in text or "ban warning" in text:
            return SafetySignal("account_warning", "hard_stop")
        if "quota" in text or "credit" in text or "credits exhausted" in text:
            return SafetySignal("quota_or_credits", "hard_stop")
        if "rate limit" in text or "rate-limit" in text:
            return SafetySignal("rate_limited", "hard_stop")
        if "unavailable generation" in text or "generation unavailable" in text:
            return SafetySignal("generation_unavailable", "hard_stop")
        if "unexpected redirect" in text:
            return SafetySignal("unexpected_redirect", "hard_stop")
        if "browser_launch_failed" in text or "browser launch failed" in text:
            return SafetySignal("browser_launch_failed", "hard_stop")
        if text == "timeout" or "timed out" in text:
            return SafetySignal("timeout", "hard_stop")
        if "repeated timeout" in text:
            return SafetySignal("repeated_timeout", "hard_stop")
        return SafetySignal("unknown", "info")

    def _from_status(self, status_code: int) -> SafetySignal:
        if status_code == 401:
            return SafetySignal("auth_required", "hard_stop")
        if status_code == 403:
            return SafetySignal("access_denied", "hard_stop")
        if status_code == 429:
            return SafetySignal("rate_limited", "hard_stop")
        return SafetySignal("unknown", "info")


def _normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()
