"""Admin-only Google Flow account onboarding helpers.

This module keeps secret-handling and .env mutation out of the admin route
handlers. It deliberately returns sanitized values only: raw passwords, TOTP
secrets, cookies, tokens, and proxy credentials must never leave this module.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from flow_core import FlowAccount, parse_flow_accounts


FLOW_URL = "https://labs.google/fx/tools/flow"
ACCOUNT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,31}$")


class AccountOnboardingError(ValueError):
    """Validation or persistence error safe to expose by code only."""

    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class AccountEntry:
    account_id: str
    profile_dir: str
    proxy_url: str = ""
    image_capacity: int | None = None
    video_capacity: int | None = None


def validate_account_id(account_id: str) -> str:
    cleaned = (account_id or "").strip()
    if not ACCOUNT_ID_RE.fullmatch(cleaned):
        raise AccountOnboardingError("invalid_account_id")
    return cleaned


def default_profile_dir(account_id: str) -> str:
    return f"./google_profile_{validate_account_id(account_id)}"


def normalize_proxy_url(proxy_url: str | None) -> str:
    raw = (proxy_url or "").strip()
    if not raw:
        return ""
    if raw.lower() in {"off", "none", "direct", "0", "false", "no"}:
        return "direct"
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in {"http", "https", "socks4", "socks5"}:
        raise AccountOnboardingError("invalid_proxy_scheme")
    if not parsed.hostname:
        raise AccountOnboardingError("invalid_proxy_url")
    return raw


def proxy_public_label(proxy_url: str | None) -> str:
    raw = normalize_proxy_url(proxy_url)
    if not raw or raw == "direct":
        return raw or "(none)"
    parsed = urlparse(raw)
    host = parsed.hostname or "?"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}"


def _quote_option_value(value: str) -> str:
    # FLOW_ACCOUNTS uses ;, comma, and | as structural separators. Encode only
    # those rare characters so normal proxy URLs stay readable in .env.
    return quote(value, safe=":/@._~!$&'()*+=%-")


def flow_account_entry(entry: AccountEntry) -> str:
    account_id = validate_account_id(entry.account_id)
    if not (entry.profile_dir or "").strip():
        raise AccountOnboardingError("empty_profile_dir")
    parts = [f"{account_id}={entry.profile_dir.strip()}"]
    proxy = normalize_proxy_url(entry.proxy_url)
    if proxy:
        parts.append(f"proxy={_quote_option_value(proxy)}")
    if entry.image_capacity is not None:
        parts.append(f"image_capacity={max(1, int(entry.image_capacity))}")
    if entry.video_capacity is not None:
        parts.append(f"video_capacity={max(0, int(entry.video_capacity))}")
    return "|".join(parts)


def _env_file_path(env_path: str | os.PathLike[str] | None = None) -> Path:
    raw = env_path or os.getenv("ENV_FILE") or ".env"
    return Path(raw)


def _split_env_assignment(line: str) -> tuple[str, str] | None:
    stripped = line.lstrip()
    prefix = line[: len(line) - len(stripped)]
    export = ""
    rest = stripped
    if rest.startswith("export "):
        export = "export "
        rest = rest[len("export ") :]
    if not rest.startswith("FLOW_ACCOUNTS="):
        return None
    return prefix + export + "FLOW_ACCOUNTS=", rest.partition("=")[2].strip()


def append_flow_account_to_env(
    entry: AccountEntry,
    *,
    env_path: str | os.PathLike[str] | None = None,
) -> dict:
    """Atomically append one account to FLOW_ACCOUNTS in .env.

    The file contains secrets, so this function does not create backup copies or
    return previous values. It returns counts and the exact new account entry.
    """
    account_id = validate_account_id(entry.account_id)
    env_file = _env_file_path(env_path)
    try:
        text = env_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""

    lines = text.splitlines(keepends=True)
    target_idx = -1
    current_value = ""
    prefix = "FLOW_ACCOUNTS="
    for idx, line in enumerate(lines):
        parsed = _split_env_assignment(line.rstrip("\r\n"))
        if parsed is not None:
            prefix, current_value = parsed
            target_idx = idx

    existing = parse_flow_accounts(current_value) if current_value.strip() else []
    if any(acc.id == account_id for acc in existing):
        raise AccountOnboardingError("account_exists")

    new_entry = flow_account_entry(entry)
    new_value = f"{current_value};{new_entry}" if current_value.strip() else new_entry
    newline = "\n"
    if target_idx >= 0 and lines[target_idx].endswith("\r\n"):
        newline = "\r\n"
    new_line = f"{prefix}{new_value}{newline}"
    if target_idx >= 0:
        lines[target_idx] = new_line
    else:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += "\n"
        lines.append(new_line)

    env_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = env_file.with_name(f".{env_file.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text("".join(lines), encoding="utf-8")
        try:
            mode = env_file.stat().st_mode
            os.chmod(tmp, mode)
        except OSError:
            pass
        os.replace(tmp, env_file)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass

    return {
        "account_id": account_id,
        "entry": new_entry,
        "env_path": str(env_file),
        "accounts_count": len(existing) + 1,
    }


def account_from_entry(entry: AccountEntry) -> FlowAccount:
    proxy = normalize_proxy_url(entry.proxy_url)
    return FlowAccount(
        id=validate_account_id(entry.account_id),
        profile_dir=entry.profile_dir.strip(),
        browser_proxy_url=proxy or None,
        api_proxy_url=proxy or None,
        image_capacity=entry.image_capacity,
        video_capacity=entry.video_capacity,
    )


def totp_code(secret: str, *, now: int | None = None, digits: int = 6, period: int = 30) -> str:
    cleaned = re.sub(r"\s+", "", secret or "").upper()
    if not cleaned:
        raise AccountOnboardingError("totp_required")
    padding = "=" * ((8 - len(cleaned) % 8) % 8)
    try:
        key = base64.b32decode(cleaned + padding, casefold=True)
    except Exception as exc:  # noqa: BLE001
        raise AccountOnboardingError("invalid_totp_secret") from exc
    counter = int((now if now is not None else time.time()) // period)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10**digits)).zfill(digits)


def playwright_proxy_config(proxy_url: str | None) -> dict | None:
    proxy = normalize_proxy_url(proxy_url)
    if not proxy or proxy == "direct":
        return None
    parsed = urlparse(proxy)
    server = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        server += f":{parsed.port}"
    config = {"server": server}
    if parsed.username:
        config["username"] = unquote(parsed.username)
    if parsed.password:
        config["password"] = unquote(parsed.password)
    return config


async def _click_next(page, *selectors: str) -> None:
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if await loc.count() > 0:
                await loc.click(timeout=7_000)
                return
        except Exception:
            continue
    try:
        await page.keyboard.press("Enter")
    except Exception:
        pass


async def _fill_first(page, selectors: list[str], value: str, *, timeout_ms: int = 7_000) -> bool:
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if await loc.count() > 0 and await loc.is_visible(timeout=timeout_ms):
                await loc.fill(value, timeout=timeout_ms)
                return True
        except Exception:
            continue
    return False


async def _page_text(page) -> str:
    try:
        return (await page.locator("body").inner_text(timeout=3_000))[:4000]
    except Exception:
        return ""


def _challenge_status(url: str, body_text: str) -> str | None:
    low = f"{url}\n{body_text}".lower()
    if any(token in low for token in ("totp", "authenticator", "verification code", "код подтверждения")):
        return "needs_2fa"
    if any(token in low for token in (
        "challenge",
        "verify it",
        "verify it's you",
        "подтвердите",
        "couldn't verify",
        "captcha",
        "suspicious",
        "unusual",
    )):
        return "needs_challenge"
    return None


async def login_google_flow_profile(
    *,
    email: str,
    password: str,
    totp_secret: str = "",
    profile_dir: str,
    proxy_url: str = "",
    timeout_sec: int = 180,
) -> dict:
    """Create a persistent Chrome profile by signing into Google and opening Flow.

    The function intentionally avoids any image/video generation. Generation
    smoke tests are separate admin actions because they spend provider quota.
    """
    if not email.strip():
        raise AccountOnboardingError("email_required")
    if not password:
        raise AccountOnboardingError("password_required")
    profile_path = Path(profile_dir)
    if profile_path.exists() and any(profile_path.iterdir()):
        raise AccountOnboardingError("profile_exists")
    profile_path.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # noqa: BLE001
        raise AccountOnboardingError("playwright_unavailable") from exc

    deadline_ms = max(30_000, int(timeout_sec) * 1000)
    pw = None
    context = None
    status = "login_failed"
    reason = "timeout"
    try:
        pw = await async_playwright().start()
        launch_kwargs = {
            "user_data_dir": str(profile_path),
            "channel": "chrome",
            "headless": False,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        }
        proxy_cfg = playwright_proxy_config(proxy_url)
        if proxy_cfg:
            launch_kwargs["proxy"] = proxy_cfg
        context = await pw.chromium.launch_persistent_context(**launch_kwargs)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        await page.goto(FLOW_URL, timeout=deadline_ms, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)

        if "accounts.google." in page.url:
            filled_email = await _fill_first(page, [
                'input[type="email"]',
                'input[name="identifier"]',
                '#identifierId',
            ], email)
            if filled_email:
                await _click_next(page, "#identifierNext button", 'button:has-text("Next")', 'button:has-text("Далее")')
                await page.wait_for_timeout(1500)

            filled_password = await _fill_first(page, [
                'input[type="password"]',
                'input[name="Passwd"]',
            ], password)
            if filled_password:
                await _click_next(page, "#passwordNext button", 'button:has-text("Next")', 'button:has-text("Далее")')
                await page.wait_for_timeout(2500)

            body = await _page_text(page)
            challenge = _challenge_status(page.url, body)
            if challenge == "needs_2fa":
                if not totp_secret.strip():
                    return {"ok": False, "status": "needs_2fa", "reason": "totp_required"}
                code = totp_code(totp_secret)
                filled_totp = await _fill_first(page, [
                    'input[name="totpPin"]',
                    'input[type="tel"]',
                    'input[aria-label*="code" i]',
                    'input[aria-label*="код" i]',
                    'input[type="text"]',
                ], code)
                if not filled_totp:
                    return {"ok": False, "status": "needs_challenge", "reason": "totp_field_not_found"}
                await _click_next(page, "#totpNext button", 'button:has-text("Next")', 'button:has-text("Далее")')
                await page.wait_for_timeout(3000)
            elif challenge == "needs_challenge":
                return {"ok": False, "status": "needs_challenge", "reason": "google_challenge"}

        await page.goto(FLOW_URL, timeout=deadline_ms, wait_until="domcontentloaded")
        try:
            await page.wait_for_url("**/fx/tools/flow**", timeout=30_000)
        except Exception:
            pass
        await page.wait_for_timeout(2500)
        body = await _page_text(page)
        if "accounts.google." in page.url:
            challenge = _challenge_status(page.url, body)
            status = challenge or "login_failed"
            reason = "still_on_google_login"
        elif "labs.google" in page.url:
            status = "active"
            reason = "flow_opened"
        else:
            status = "needs_challenge"
            reason = "unexpected_redirect"
        return {
            "ok": status == "active",
            "status": status,
            "reason": reason,
            "final_host": urlparse(page.url).hostname or "",
        }
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        if pw is not None:
            try:
                await pw.stop()
            except Exception:
                pass


async def onboard_google_flow_account(
    *,
    account_id: str,
    email: str,
    password: str,
    totp_secret: str = "",
    proxy_url: str = "",
    env_path: str | os.PathLike[str] | None = None,
    profile_dir: str | None = None,
    timeout_sec: int = 180,
) -> dict:
    account_id = validate_account_id(account_id)
    proxy = normalize_proxy_url(proxy_url)
    profile_dir = profile_dir or default_profile_dir(account_id)
    entry = AccountEntry(account_id=account_id, profile_dir=profile_dir, proxy_url=proxy)

    # Validate duplicate before spending time in Google login.
    env_file = _env_file_path(env_path)
    try:
        text = env_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    for line in text.splitlines():
        parsed = _split_env_assignment(line)
        if parsed:
            existing = parse_flow_accounts(parsed[1]) if parsed[1].strip() else []
            if any(acc.id == account_id for acc in existing):
                raise AccountOnboardingError("account_exists")

    login = await login_google_flow_profile(
        email=email,
        password=password,
        totp_secret=totp_secret,
        profile_dir=profile_dir,
        proxy_url=proxy,
        timeout_sec=timeout_sec,
    )
    result = {
        "ok": False,
        "account_id": account_id,
        "profile_dir": profile_dir,
        "proxy": proxy_public_label(proxy),
        "status": login.get("status", "login_failed"),
        "reason": login.get("reason", "login_failed"),
        "env_updated": False,
        "restart_required": False,
    }
    if not login.get("ok"):
        return result

    env_update = append_flow_account_to_env(entry, env_path=env_file)
    result.update({
        "ok": True,
        "status": "active",
        "reason": "flow_opened",
        "env_updated": True,
        "restart_required": True,
        "accounts_count": env_update["accounts_count"],
    })
    return result
