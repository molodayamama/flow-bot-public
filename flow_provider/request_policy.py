"""Shared request policy for Google Flow browser and HTTP adapters.

This module contains the small, provider-wide contract that is needed by both
``SessionKeeper`` and ``FlowHttpClient``.  Keeping it independent from either
class lets the two adapters evolve (and eventually move files) without runtime
cross-imports.
"""
from __future__ import annotations

from typing import Any, Mapping


# Image generation uses one action intentionally: an unusual-activity response
# is tied to the account/session, so trying more actions only spends captcha
# balance and time.
RECAPTCHA_ACTIONS = ["IMAGE_GENERATION"]

# Confirmed from the Flow frontend capture (2026-06-20).
VIDEO_RECAPTCHA_ACTION = "VIDEO_GENERATION"
AGENT_RECAPTCHA_ACTION = "CHAT_GENERATION"
AGENT_RECAPTCHA_ACTION_CANDIDATES = [
    "CHAT_GENERATION",
    "CREATIVE_AGENT",
    "FLOW_CREATION_AGENT",
    "CREATION_AGENT",
    "AGENT",
    "IMAGE_GENERATION",
    "VIDEO_GENERATION",
]

# Video reCAPTCHA scores are stochastic, so each retry uses a fresh token.
VIDEO_GEN_MAX_ATTEMPTS = 4
VIDEO_GEN_403_BACKOFF_SEC = 3.0


_CAPTURED_HEADER_MAPPING = {
    "user-agent": "User-Agent",
    "sec-ch-ua": "Sec-Ch-Ua",
    "sec-ch-ua-platform": "Sec-Ch-Ua-Platform",
    "x-browser-channel": "X-Browser-Channel",
    "x-browser-copyright": "X-Browser-Copyright",
    "x-browser-year": "X-Browser-Year",
    "x-browser-validation": "X-Browser-Validation",
    "x-client-data": "X-Client-Data",
}


def build_flow_headers(session: Mapping[str, Any]) -> dict[str, str]:
    """Build the direct Flow API headers from a captured browser session."""
    bearer = session["bearer"]
    extra = session["headers"]

    headers = {
        "Authorization": f"Bearer {bearer}",
        "Content-Type": "text/plain;charset=UTF-8",
        "Accept": "*/*",
        "Accept-Language": "ru,ru-RU;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": "https://labs.google",
        "Referer": "https://labs.google/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "cross-site",
    }

    for source_name, target_name in _CAPTURED_HEADER_MAPPING.items():
        if source_name in extra:
            headers[target_name] = extra[source_name]

    return headers
