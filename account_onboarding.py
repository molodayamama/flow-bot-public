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
TWO_FA_CODE_RE = re.compile(r"^\d{6,8}$")


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


@dataclass
class PendingGoogleLogin:
    account_id: str
    profile_dir: str
    proxy_url: str
    pw: object
    context: object
    page: object
    created_at: float
    status: str = "pending"
    closed: bool = False

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            await self.context.close()
        except Exception:
            pass
        try:
            await self.pw.stop()
        except Exception:
            pass

    async def submit_2fa_code(self, code: str, *, timeout_sec: int = 90) -> dict:
        if self.closed:
            raise AccountOnboardingError("session_closed")
        cleaned = validate_2fa_code(code)
        deadline_ms = max(30_000, int(timeout_sec) * 1000)
        filled = await _fill_first(self.page, [
            'input[name="totpPin"]',
            'input[type="tel"]',
            'input[aria-label*="code" i]',
            'input[aria-label*="код" i]',
            'input[type="text"]',
        ], cleaned)
        if not filled:
            self.status = "needs_challenge"
            return _login_result(False, "needs_challenge", "two_fa_field_not_found", self.page.url)
        await _click_next(
            self.page,
            "#totpNext button",
            'button:has-text("Next")',
            'button:has-text("Далее")',
        )
        await self.page.wait_for_timeout(3000)
        result = await _open_flow_status(self.page, deadline_ms)
        if result.get("ok"):
            result = await _ensure_flow_project(self.page)
        self.status = str(result.get("status") or "login_failed")
        return result


def validate_account_id(account_id: str) -> str:
    cleaned = (account_id or "").strip()
    if not ACCOUNT_ID_RE.fullmatch(cleaned):
        raise AccountOnboardingError("invalid_account_id")
    return cleaned


def validate_2fa_code(code: str) -> str:
    cleaned = re.sub(r"\s+", "", code or "")
    if not cleaned:
        raise AccountOnboardingError("two_fa_required")
    if not TWO_FA_CODE_RE.fullmatch(cleaned):
        raise AccountOnboardingError("invalid_two_fa_code")
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


def remove_flow_account_from_env(
    account_id: str,
    *,
    env_path: str | os.PathLike[str] | None = None,
) -> dict:
    """Atomically remove one account from FLOW_ACCOUNTS in .env."""
    account_id = validate_account_id(account_id)
    env_file = _env_file_path(env_path)
    try:
        text = env_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise AccountOnboardingError("account_not_found") from None

    lines = text.splitlines(keepends=True)
    target_idx = -1
    current_value = ""
    prefix = "FLOW_ACCOUNTS="
    for idx, line in enumerate(lines):
        parsed = _split_env_assignment(line.rstrip("\r\n"))
        if parsed is not None:
            prefix, current_value = parsed
            target_idx = idx

    if target_idx < 0:
        raise AccountOnboardingError("account_not_found")
    entries = [p for p in current_value.split(";") if p.strip()]
    kept: list[str] = []
    removed: list[str] = []
    for raw in entries:
        entry_id = raw.split("=", 1)[0].strip()
        if entry_id == account_id:
            removed.append(raw)
        else:
            kept.append(raw)
    if not removed:
        raise AccountOnboardingError("account_not_found")

    newline = "\n"
    if lines[target_idx].endswith("\r\n"):
        newline = "\r\n"
    lines[target_idx] = f"{prefix}{';'.join(kept)}{newline}"
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
        "env_path": str(env_file),
        "accounts_count": len(kept),
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


def _assert_account_not_configured(account_id: str, env_path: str | os.PathLike[str] | None = None) -> None:
    account_id = validate_account_id(account_id)
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


def _login_result(ok: bool, status: str, reason: str, url: str = "") -> dict:
    return {
        "ok": ok,
        "status": status,
        "reason": reason,
        "final_host": urlparse(url).hostname or "",
    }


async def _open_flow_status(page, deadline_ms: int) -> dict:
    await page.goto(FLOW_URL, timeout=deadline_ms, wait_until="domcontentloaded")
    try:
        await page.wait_for_url("**/fx/tools/flow**", timeout=min(deadline_ms, 30_000))
    except Exception:
        pass
    await page.wait_for_timeout(2500)
    body = await _page_text(page)
    if "accounts.google." in page.url:
        challenge = _challenge_status(page.url, body)
        return _login_result(False, challenge or "login_failed", "still_on_google_login", page.url)
    if "labs.google" in page.url:
        return _login_result(True, "active", "flow_opened", page.url)
    return _login_result(False, "needs_challenge", "unexpected_redirect", page.url)


def _project_id_from_url(url: str) -> str | None:
    for marker in ("/project/", "/projects/"):
        if marker in (url or ""):
            pid = url.split(marker, 1)[-1].split("?", 1)[0].split("/", 1)[0]
            if pid:
                return pid
    return None


async def _ensure_flow_project(page) -> dict:
    if _project_id_from_url(page.url):
        return _login_result(True, "active", "project_opened", page.url)
    candidates = [
        lambda: page.get_by_role("button", name=re.compile(r"new flow", re.I)),
        lambda: page.get_by_role("link", name=re.compile(r"new flow", re.I)),
        lambda: page.get_by_text(re.compile(r"^\s*new flow\s*$", re.I)),
        lambda: page.get_by_role("button", name=re.compile(r"new project|create", re.I)),
        lambda: page.locator('[aria-label*="new" i]'),
        lambda: page.locator('button:has-text("+")'),
    ]
    clicked = False
    for getter in candidates:
        try:
            loc = getter().first
            if await loc.count() > 0 and await loc.is_visible(timeout=1_000):
                await loc.click(timeout=5_000)
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        return _login_result(False, "needs_project", "new_flow_button_not_found", page.url)
    try:
        await page.wait_for_url("**/project/**", timeout=20_000)
    except Exception:
        pass
    await page.wait_for_timeout(1500)
    if _project_id_from_url(page.url):
        return _login_result(True, "active", "project_created", page.url)
    return _login_result(False, "needs_project", "project_not_created", page.url)


async def start_google_flow_login(
    *,
    account_id: str,
    email: str,
    password: str,
    profile_dir: str,
    proxy_url: str = "",
    env_path: str | os.PathLike[str] | None = None,
    timeout_sec: int = 180,
    allow_existing_profile: bool = False,
    skip_account_exists: bool = False,
) -> tuple[PendingGoogleLogin | None, dict]:
    """Start Google login and keep the browser alive for a one-time 2FA code.

    Returns ``(session, result)``. The session is present only when the login is
    waiting for 2FA or has already opened Flow and can be finalized. Password is
    used only to fill the page and is not stored.
    """
    account_id = validate_account_id(account_id)
    if not email.strip():
        raise AccountOnboardingError("email_required")
    if not password:
        raise AccountOnboardingError("password_required")
    if not skip_account_exists:
        _assert_account_not_configured(account_id, env_path)
    profile_path = Path(profile_dir)
    if not allow_existing_profile and profile_path.exists() and any(profile_path.iterdir()):
        raise AccountOnboardingError("profile_exists")
    profile_path.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # noqa: BLE001
        raise AccountOnboardingError("playwright_unavailable") from exc

    deadline_ms = max(30_000, int(timeout_sec) * 1000)
    pw = None
    context = None
    session: PendingGoogleLogin | None = None
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
        session = PendingGoogleLogin(
            account_id=account_id,
            profile_dir=str(profile_path),
            proxy_url=proxy_url,
            pw=pw,
            context=context,
            page=page,
            created_at=time.time(),
        )
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
                session.status = "needs_2fa"
                return session, _login_result(False, "needs_2fa", "two_fa_code_required", page.url)
            if challenge == "needs_challenge":
                result = _login_result(False, "needs_challenge", "google_challenge", page.url)
                await session.close()
                return None, result

        result = await _open_flow_status(page, deadline_ms)
        if result.get("ok"):
            result = await _ensure_flow_project(page)
        session.status = str(result.get("status") or "login_failed")
        if session.status == "active":
            return session, result
        await session.close()
        return None, result
    except Exception:
        if session is not None:
            await session.close()
        elif context is not None:
            try:
                await context.close()
            except Exception:
                pass
            if pw is not None:
                try:
                    await pw.stop()
                except Exception:
                    pass
        raise


def complete_google_flow_login(
    session: PendingGoogleLogin,
    *,
    env_path: str | os.PathLike[str] | None = None,
) -> dict:
    if session.closed:
        raise AccountOnboardingError("session_closed")
    if session.status != "active":
        raise AccountOnboardingError("login_not_active")
    entry = AccountEntry(
        account_id=session.account_id,
        profile_dir=session.profile_dir,
        proxy_url=session.proxy_url,
    )
    env_update = append_flow_account_to_env(entry, env_path=env_path)
    return {
        "ok": True,
        "account_id": session.account_id,
        "profile_dir": session.profile_dir,
        "proxy": proxy_public_label(session.proxy_url),
        "status": "active",
        "reason": "flow_opened",
        "env_updated": True,
        "restart_required": True,
        "accounts_count": env_update["accounts_count"],
    }


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
