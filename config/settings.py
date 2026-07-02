"""Application / payment configuration (Phase 5 wave 2).

Env-derived app + monetization config extracted verbatim from flow_bot. Loads
dotenv itself (imported before flow_bot finishes loading) so values are identical
to the monolith. flow_bot re-exports these names.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

ENV_FILE = os.getenv("ENV_FILE", ".env") or ".env"
load_dotenv(ENV_FILE)


try:
    STARS_TO_RUB = float(os.getenv("STARS_TO_RUB", "1.3"))  # ~₽ за 1 Star, best-effort
except (TypeError, ValueError):
    STARS_TO_RUB = 1.3
try:
    ROBOKASSA_CARD_DISCOUNT_PCT = float(os.getenv("ROBOKASSA_CARD_DISCOUNT_PCT", "10"))
except (TypeError, ValueError):
    ROBOKASSA_CARD_DISCOUNT_PCT = 10.0
def _env_any(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value not in (None, ""):
            return value
    return default
ROBOKASSA_MERCHANT_LOGIN = _env_any("ROBOKASSA_MERCHANT_LOGIN", "ROBOKASSA_LOGIN")
ROBOKASSA_TEST = _env_any("ROBOKASSA_TEST", "ROBOKASSA_TEST_MODE", default="0").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
ROBOKASSA_PASSWORD1 = (
    _env_any("ROBOKASSA_TEST_PASSWORD1", "ROBOKASSA_TEST_PASS1")
    if ROBOKASSA_TEST
    else ""
) or _env_any("ROBOKASSA_PASSWORD1", "ROBOKASSA_PASS1", "ROBOKASSA_PASSWORD_1")
ROBOKASSA_PASSWORD2 = (
    _env_any("ROBOKASSA_TEST_PASSWORD2", "ROBOKASSA_TEST_PASS2")
    if ROBOKASSA_TEST
    else ""
) or _env_any("ROBOKASSA_PASSWORD2", "ROBOKASSA_PASS2", "ROBOKASSA_PASSWORD_2")
ROBOKASSA_ENABLED = _env_any(
    "ROBOKASSA_ENABLED",
    default="1" if ROBOKASSA_MERCHANT_LOGIN and ROBOKASSA_PASSWORD1 and ROBOKASSA_PASSWORD2 else "0",
).strip().lower() not in ("0", "false", "no", "off")
ROBOKASSA_PAY_URL = _env_any(
    "ROBOKASSA_PAY_URL",
    default="https://auth.robokassa.ru/Merchant/Index.aspx",
)
ROBOKASSA_PUBLIC_BASE_URL = _env_any(
    "ROBOKASSA_PUBLIC_BASE_URL",
    default="https://pay.photozhab.ru",
).rstrip("/")
ROBOKASSA_INC_CURR_LABEL = _env_any("ROBOKASSA_INC_CURR_LABEL", default="SBP")
ROBOKASSA_WEB_HOST = _env_any("ROBOKASSA_WEB_HOST", default="127.0.0.1")
try:
    ROBOKASSA_WEB_PORT = int(_env_any("ROBOKASSA_WEB_PORT", default="8081"))
except ValueError:
    ROBOKASSA_WEB_PORT = 8081
def _robokassa_clean_scope(value: str) -> str:
    value = (value or "").strip().lower()
    return value if value in ("consumer", "seller") else "consumer"
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
BOT_MODE = (os.getenv("BOT_MODE", "consumer") or "consumer").strip().lower()
IS_SELLER = BOT_MODE == "seller"
ROBOKASSA_SCOPE = _robokassa_clean_scope(os.getenv("ROBOKASSA_SCOPE") or ("seller" if IS_SELLER else "consumer"))
STARS_PAYMENT_ENABLED: bool = True
SBP_PAYMENT_ENABLED: bool = True

# Uploaded-video edit is captured but hidden: the service returns "Oops…" /
# under-loads on user-uploaded video. Whole upload path is preserved; flip to
# True (and re-verify via capture) to bring it back. Hot-patchable via admin,
# so readers must use a live attribute read (config.settings.UPLOAD_VIDEO_EDIT_ENABLED).
UPLOAD_VIDEO_EDIT_ENABLED: bool = False
