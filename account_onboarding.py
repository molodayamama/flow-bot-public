"""Admin-only Google Flow account onboarding helpers.

This module keeps secret-handling and .env mutation out of the admin route
handlers. It deliberately returns sanitized values only: raw passwords, TOTP
secrets, cookies, tokens, and proxy credentials must never leave this module.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from flow_core import FlowAccount, parse_flow_accounts


log = logging.getLogger(__name__)

FLOW_URL = "https://labs.google/fx/tools/flow"
GOOGLE_LOGIN_URL = "https://accounts.google.com/"
ACCOUNT_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,31}$")
TWO_FA_CODE_RE = re.compile(r"^\d{6,8}$")
FLOW_PROJECT_CTA_RE = re.compile(
    r"^\s*(new project|create project|new flow|create with flow)\s*$",
    re.I,
)
FLOW_NEW_PROJECT_RE = re.compile(r"^\s*new project\s*$", re.I)
FLOW_CREATE_WITH_FLOW_RE = re.compile(r"^\s*create with flow\s*$", re.I)
FLOW_SIGN_IN_RE = re.compile(
    r"^\s*(sign in|sign in with google|log in|log in with google|войти|войти через google)\s*$",
    re.I,
)
GOOGLE_USE_ANOTHER_ACCOUNT_RE = re.compile(
    r"^\s*(use another account|add account|другой аккаунт|использовать другой аккаунт)\s*$",
    re.I,
)
FLOW_SIGNED_OUT_TEXT_RE = re.compile(
    r"(sign[\s-]*in|log[\s-]*in)(?:\s+(?:with|to)\s+google)?|"
    r"(?:choose|use)\s+an?\s+account\s+to\s+continue|"
    r"войд(?:ите|и)(?:\s+через\s+google)?|"
    r"аккаунт(?: google)?,?\s+чтобы продолжить",
    re.I,
)


# Live onboarding progress, keyed by account id, polled by the admin panel.
# Values are secret-free (stage names, step numbers, human labels, hosts).
_PROGRESS: dict[str, dict] = {}
_PROGRESS_TTL_SEC = 900
_TOTAL_STEPS = 7

# Happy-path steps that advance the "N of 7" counter.
_STEP_LABELS: dict[str, tuple[int, str]] = {
    "login_start": (1, "Открываю профиль Chrome"),
    "browser_launch": (2, "Запускаю браузер"),
    "page_loaded": (3, "Открываю Google / Flow"),
    "email_filled": (4, "Ввожу Google-логин"),
    "password_filled": (5, "Ввожу пароль"),
    "flow_opened": (6, "Flow открыт"),
    "project_ready": (7, "Проект готов"),
}

# Branch / informational stages: keep the current step, show their own label.
_BRANCH_LABELS: dict[str, str] = {
    "needs_2fa": "Google запросил 2FA-код",
    "needs_challenge": "Google просит доп. проверку",
    "needs_project": "Flow открыт, но проект не создан — нужен ручной разбор",
    "sign_in_clicked": "Flow просит вход — открываю Google login",
    "2fa_submit": "Отправляю 2FA-код",
    "2fa_field_not_found": "Поле 2FA не найдено",
    "recheck": "Перепроверяю вход",
    "env_written": "Записываю аккаунт в .env",
    "profile_exists": "Папка профиля уже существует",
}

# Final-status labels for *_result stages.
_STATUS_LABELS: dict[str, str] = {
    "active": "Готово — Flow и проект открыты",
    "needs_2fa": "Нужен 2FA-код",
    "needs_challenge": "Нужна доп. проверка Google",
    "needs_project": "Проект не создан — нужен ручной разбор",
    "login_failed": "Логин не завершился",
}


def _record_progress(account_id: str, stage: str, extra: dict) -> None:
    if not account_id:
        return
    if stage == "login_start":
        _PROGRESS.pop(account_id, None)  # fresh run resets the counter
    prev = _PROGRESS.get(account_id) or {}
    step = int(prev.get("step", 0))
    if stage in _STEP_LABELS:
        step, label = _STEP_LABELS[stage]
    elif stage in _BRANCH_LABELS:
        label = _BRANCH_LABELS[stage]
    elif stage.endswith("_result"):
        status = str(extra.get("status") or "")
        label = _STATUS_LABELS.get(status, status or "Готово")
    else:
        label = stage
    status = str(extra.get("status") or prev.get("status") or "")
    done = stage.endswith("_result") or stage in {
        "needs_2fa", "needs_challenge", "needs_project", "env_written", "profile_exists",
    }
    _PROGRESS[account_id] = {
        "account_id": account_id,
        "step": step,
        "total": _TOTAL_STEPS,
        "label": label,
        "stage": stage,
        "status": status,
        "done": bool(done),
        "updated_at": time.time(),
    }


def get_progress(account_id: str) -> dict:
    """Return the latest secret-free progress snapshot for an account id."""
    now = time.time()
    # Opportunistic TTL prune so the dict can't grow unbounded.
    for aid in [k for k, v in _PROGRESS.items()
                if now - float(v.get("updated_at", 0)) > _PROGRESS_TTL_SEC]:
        _PROGRESS.pop(aid, None)
    item = _PROGRESS.get(account_id)
    return dict(item) if isinstance(item, dict) else {}


def _stage(account_id: str, stage: str, **extra) -> None:
    """Emit a single onboarding-stage line to the bot log AND the live
    progress store polled by the admin panel.

    Secrets never reach here: callers pass only stage names, booleans, status
    codes, and URL *hosts* — never passwords, codes, cookies, or full URLs.
    """
    suffix = ""
    if extra:
        suffix = " " + " ".join(f"{k}={v}" for k, v in extra.items())
    log.info("onboard[%s] %s%s", account_id, stage, suffix)
    try:
        _record_progress(account_id, stage, extra)
    except Exception:  # progress is best-effort, never break login
        log.debug("progress record failed", exc_info=True)


def _host(url: str) -> str:
    try:
        return urlparse(url or "").hostname or ""
    except Exception:
        return ""


def _onboarding_display_env() -> dict | None:
    """Launch the onboarding browser on a VNC-visible X display when configured.

    Operators pass Google "verify it's you" challenges by watching this browser
    over VNC. The bot itself runs under a throwaway ``xvfb-run`` display the
    operator cannot see, so onboarding overrides ``DISPLAY`` (default ``:99``,
    which the VNC stack is attached to). Set ``ONBOARDING_DISPLAY`` empty to keep
    the inherited display.
    """
    display = os.getenv("ONBOARDING_DISPLAY", ":99").strip()
    if not display:
        return None
    return {**os.environ, "DISPLAY": display}


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
        _stage(self.account_id, "2fa_submit", host=_host(self.page.url))
        filled = await _fill_first(self.page, [
            'input[name="totpPin"]',
            'input[type="tel"]',
            'input[aria-label*="code" i]',
            'input[aria-label*="код" i]',
            'input[type="text"]',
        ], cleaned)
        if not filled:
            self.status = "needs_challenge"
            _stage(self.account_id, "2fa_field_not_found", host=_host(self.page.url))
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
            _stage(self.account_id, "flow_opened", host=_host(self.page.url))
            result = await _ensure_flow_project(self.page)
            if result.get("ok"):
                _stage(self.account_id, "project_ready", host=_host(self.page.url))
        self.status = str(result.get("status") or "login_failed")
        _stage(self.account_id, "2fa_result", status=self.status, reason=result.get("reason"))
        return result

    async def recheck(self, *, timeout_sec: int = 90) -> dict:
        """Re-evaluate login after the operator finished a manual Google
        challenge in the VNC-visible browser. Re-opens Flow and ensures a
        project, then updates ``status`` so ``complete`` can finalize.
        """
        if self.closed:
            raise AccountOnboardingError("session_closed")
        deadline_ms = max(30_000, int(timeout_sec) * 1000)
        _stage(self.account_id, "recheck", host=_host(self.page.url))
        result = await _open_flow_status(self.page, deadline_ms)
        if result.get("ok"):
            _stage(self.account_id, "flow_opened", host=_host(self.page.url))
            result = await _ensure_flow_project(self.page)
            if result.get("ok"):
                _stage(self.account_id, "project_ready", host=_host(self.page.url))
        self.status = str(result.get("status") or "login_failed")
        _stage(self.account_id, "recheck_result", status=self.status, reason=result.get("reason"))
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


def _flow_text_looks_signed_out(body_text: str) -> bool:
    return bool(FLOW_SIGNED_OUT_TEXT_RE.search(body_text or ""))


def _flow_sign_in_candidates(page) -> list[tuple[str, object]]:
    return [
        ("sign_in_button", lambda: page.get_by_role("button", name=FLOW_SIGN_IN_RE)),
        ("sign_in_link", lambda: page.get_by_role("link", name=FLOW_SIGN_IN_RE)),
        ("sign_in_text", lambda: page.get_by_text(FLOW_SIGN_IN_RE)),
        ("sign_in_aria", lambda: page.locator('[aria-label*="sign in" i]')),
        ("login_aria", lambda: page.locator('[aria-label*="log in" i]')),
        ("google_accounts_link", lambda: page.locator('a[href*="accounts.google."]')),
        ("sign_in_button_text", lambda: page.locator('button:has-text("Sign in")')),
        ("login_button_text", lambda: page.locator('button:has-text("Log in")')),
    ]


async def _has_flow_sign_in_cta(page) -> bool:
    for _label, getter in _flow_sign_in_candidates(page):
        try:
            loc = getter().first
            if await loc.count() > 0 and await loc.is_visible(timeout=500):
                return True
        except Exception:
            continue
    return False


async def _flow_page_is_signed_out(page, body_text: str | None = None) -> bool:
    if "labs.google" not in (page.url or ""):
        return False
    if await _has_flow_sign_in_cta(page):
        return True
    body = body_text if body_text is not None else await _page_text(page)
    return _flow_text_looks_signed_out(body)


async def _click_flow_sign_in(page) -> bool:
    for _label, getter in _flow_sign_in_candidates(page):
        try:
            loc = getter().first
            if await loc.count() > 0 and await loc.is_visible(timeout=1_000):
                try:
                    await loc.scroll_into_view_if_needed(timeout=2_000)
                except Exception:
                    pass
                await loc.click(timeout=5_000)
                await page.wait_for_timeout(1500)
                try:
                    await page.wait_for_url("**accounts.google.**", timeout=15_000)
                except Exception:
                    pass
                return True
        except Exception:
            continue
    return False


def _google_use_another_account_candidates(page) -> list[tuple[str, object]]:
    return [
        ("use_another_account_button", lambda: page.get_by_role("button", name=GOOGLE_USE_ANOTHER_ACCOUNT_RE)),
        ("use_another_account_link", lambda: page.get_by_role("link", name=GOOGLE_USE_ANOTHER_ACCOUNT_RE)),
        ("use_another_account_text", lambda: page.get_by_text(GOOGLE_USE_ANOTHER_ACCOUNT_RE)),
        ("use_another_account_data", lambda: page.locator('[data-identifier=""]')),
    ]


async def _click_google_use_another_account(page) -> bool:
    for _label, getter in _google_use_another_account_candidates(page):
        try:
            loc = getter().first
            if await loc.count() > 0 and await loc.is_visible(timeout=1_000):
                try:
                    await loc.scroll_into_view_if_needed(timeout=2_000)
                except Exception:
                    pass
                await loc.click(timeout=5_000)
                await page.wait_for_timeout(1500)
                return True
        except Exception:
            continue
    return False


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


async def _submit_google_login_if_needed(
    page,
    *,
    account_id: str,
    email: str,
    password: str,
    totp_secret: str = "",
    deadline_ms: int = 90_000,
) -> dict | None:
    if "labs.google" in (page.url or "") and await _flow_page_is_signed_out(page):
        clicked = await _click_flow_sign_in(page)
        _stage(account_id, "sign_in_clicked", ok=bool(clicked), host=_host(page.url))

    if "accounts.google." not in (page.url or ""):
        return None

    deadline = time.time() + max(30_000, int(deadline_ms)) / 1000
    clicked_account_chooser = False
    while time.time() < deadline:
        if "accounts.google." not in (page.url or ""):
            return None

        filled_email = await _fill_first(page, [
            'input[type="email"]',
            'input[name="identifier"]',
            '#identifierId',
        ], email, timeout_ms=2_000)
        if filled_email:
            _stage(account_id, "email_filled", ok=True)
            await _click_next(page, "#identifierNext button", 'button:has-text("Next")', 'button:has-text("Далее")')
            await page.wait_for_timeout(1800)
            continue

        if not clicked_account_chooser and await _click_google_use_another_account(page):
            clicked_account_chooser = True
            _stage(account_id, "email_filled", ok=False, chooser=True)
            continue

        # Google password verification URLs often contain /challenge/pwd and
        # page text like "verify it's you"; if a password field is visible,
        # this is still the normal password step, not a manual challenge.
        filled_password = await _fill_first(page, [
            'input[type="password"]',
            'input[name="Passwd"]',
        ], password, timeout_ms=2_000)
        if filled_password:
            _stage(account_id, "password_filled", ok=True)
            await _click_next(page, "#passwordNext button", 'button:has-text("Next")', 'button:has-text("Далее")')
            await page.wait_for_timeout(2500)
            continue

        body = await _page_text(page)
        challenge = _challenge_status(page.url, body)
        if challenge == "needs_2fa":
            if totp_secret.strip():
                try:
                    code = totp_code(totp_secret)
                except AccountOnboardingError as exc:
                    _stage(account_id, "needs_2fa", host=_host(page.url))
                    return _login_result(False, "needs_2fa", exc.code, page.url)
                filled_totp = await _fill_first(page, [
                    'input[name="totpPin"]',
                    'input[type="tel"]',
                    'input[aria-label*="code" i]',
                    'input[aria-label*="код" i]',
                    'input[type="text"]',
                ], code, timeout_ms=2_000)
                _stage(account_id, "2fa_submit", host=_host(page.url), auto=True, ok=bool(filled_totp))
                if not filled_totp:
                    _stage(account_id, "2fa_field_not_found", host=_host(page.url))
                    return _login_result(False, "needs_challenge", "totp_field_not_found", page.url)
                await _click_next(
                    page,
                    "#totpNext button",
                    'button:has-text("Next")',
                    'button:has-text("Далее")',
                )
                await page.wait_for_timeout(3000)
                continue
            _stage(account_id, "needs_2fa", host=_host(page.url))
            return _login_result(False, "needs_2fa", "two_fa_code_required", page.url)
        if challenge == "needs_challenge":
            _stage(account_id, "needs_challenge", host=_host(page.url))
            return _login_result(False, "needs_challenge", "google_challenge", page.url)

        await page.wait_for_timeout(1000)

    if "accounts.google." not in (page.url or ""):
        return None
    body = await _page_text(page)
    challenge = _challenge_status(page.url, body)
    if challenge == "needs_2fa":
        _stage(account_id, "needs_2fa", host=_host(page.url))
        return _login_result(False, "needs_2fa", "two_fa_code_required", page.url)
    if challenge == "needs_challenge":
        _stage(account_id, "needs_challenge", host=_host(page.url))
        return _login_result(False, "needs_challenge", "google_challenge", page.url)
    _stage(account_id, "needs_challenge", host=_host(page.url))
    return _login_result(False, "needs_challenge", "google_login_form_not_ready", page.url)


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
        log.info("onboard flow-status: still on google host=%s challenge=%s", _host(page.url), challenge)
        return _login_result(False, challenge or "login_failed", "still_on_google_login", page.url)
    if "labs.google" in page.url:
        if await _flow_page_is_signed_out(page, body):
            clicked = await _click_flow_sign_in(page)
            body = await _page_text(page)
            if "accounts.google." in page.url:
                challenge = _challenge_status(page.url, body)
                log.info(
                    "onboard flow-status: flow signed out, opened google host=%s challenge=%s",
                    _host(page.url), challenge,
                )
                return _login_result(
                    False,
                    challenge or "needs_challenge",
                    "flow_signed_out_google_login",
                    page.url,
                )
            log.info("onboard flow-status: flow signed out host=%s clicked=%s", _host(page.url), clicked)
            return _login_result(False, "needs_challenge", "flow_signed_out", page.url)
        log.info("onboard flow-status: flow opened host=%s", _host(page.url))
        return _login_result(True, "active", "flow_opened", page.url)
    log.info("onboard flow-status: unexpected redirect host=%s", _host(page.url))
    return _login_result(False, "needs_challenge", "unexpected_redirect", page.url)


def _project_id_from_url(url: str) -> str | None:
    for marker in ("/project/", "/projects/"):
        if marker in (url or ""):
            pid = url.split(marker, 1)[-1].split("?", 1)[0].split("/", 1)[0]
            if pid:
                return pid
    return None


def _flow_project_cta_candidates(page) -> list[tuple[str, object]]:
    # Keep exact Flow CTAs before broad icon fallbacks. Plain "Create" is
    # intentionally excluded: Google account pages use it for "Create account".
    return [
        ("new_project_button", lambda: page.get_by_role("button", name=FLOW_NEW_PROJECT_RE)),
        ("new_project_link", lambda: page.get_by_role("link", name=FLOW_NEW_PROJECT_RE)),
        ("new_project_text", lambda: page.get_by_text(FLOW_NEW_PROJECT_RE)),
        ("create_with_flow_button", lambda: page.get_by_role("button", name=FLOW_CREATE_WITH_FLOW_RE)),
        ("create_with_flow_link", lambda: page.get_by_role("link", name=FLOW_CREATE_WITH_FLOW_RE)),
        ("create_with_flow_text", lambda: page.get_by_text(FLOW_CREATE_WITH_FLOW_RE)),
        ("project_cta_button", lambda: page.get_by_role("button", name=FLOW_PROJECT_CTA_RE)),
        ("project_cta_link", lambda: page.get_by_role("link", name=FLOW_PROJECT_CTA_RE)),
        ("project_cta_text", lambda: page.get_by_text(FLOW_PROJECT_CTA_RE)),
        ("new_project_aria", lambda: page.locator('[aria-label*="new project" i]')),
        ("create_project_aria", lambda: page.locator('[aria-label*="create project" i]')),
        ("create_with_flow_aria", lambda: page.locator('[aria-label*="create with flow" i]')),
        ("new_project_button_text", lambda: page.locator('button:has-text("New project")')),
        ("create_project_button_text", lambda: page.locator('button:has-text("Create project")')),
        ("create_with_flow_button_text", lambda: page.locator('button:has-text("Create with Flow")')),
        ("new_flow_button_text", lambda: page.locator('button:has-text("New flow")')),
        ("new_icon_button", lambda: page.locator('button[aria-label*="new" i]')),
        ("plus_button", lambda: page.locator('button:has-text("+")')),
    ]


async def _wait_for_project_url(page, timeout_ms: int) -> str | None:
    deadline = time.time() + max(timeout_ms, 0) / 1000
    while time.time() < deadline:
        pid = _project_id_from_url(page.url)
        if pid:
            return pid
        remaining_ms = int((deadline - time.time()) * 1000)
        await page.wait_for_timeout(max(100, min(500, remaining_ms)))
    return _project_id_from_url(page.url)


async def _ensure_flow_project(page) -> dict:
    if _project_id_from_url(page.url):
        log.info("onboard project: already open host=%s", _host(page.url))
        return _login_result(True, "active", "project_opened", page.url)
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass

    clicked_any = False
    for label, getter in _flow_project_cta_candidates(page):
        try:
            loc = getter().first
            if await loc.count() > 0 and await loc.is_visible(timeout=1_000):
                try:
                    await loc.scroll_into_view_if_needed(timeout=2_000)
                except Exception:
                    pass
                await loc.click(timeout=5_000)
                clicked_any = True
                log.info("onboard project: clicked cta=%s host=%s", label, _host(page.url))
                if await _wait_for_project_url(page, 12_000):
                    log.info("onboard project: created host=%s", _host(page.url))
                    return _login_result(True, "active", "project_created", page.url)
                if "accounts.google." in page.url:
                    body = await _page_text(page)
                    challenge = _challenge_status(page.url, body)
                    log.info(
                        "onboard project: redirected to google host=%s challenge=%s",
                        _host(page.url), challenge,
                    )
                    return _login_result(
                        False,
                        challenge or "needs_challenge",
                        "project_creation_google_redirect",
                        page.url,
                    )
                if "labs.google" not in page.url:
                    log.info("onboard project: unexpected redirect host=%s", _host(page.url))
                    return _login_result(False, "needs_challenge", "project_creation_redirect", page.url)
        except Exception:
            continue
    if not clicked_any:
        log.info("onboard project: 'new project' button not found host=%s", _host(page.url))
        return _login_result(False, "needs_project", "new_project_button_not_found", page.url)
    if await _wait_for_project_url(page, 10_000):
        log.info("onboard project: created host=%s", _host(page.url))
        return _login_result(True, "active", "project_created", page.url)
    log.info("onboard project: not created host=%s", _host(page.url))
    return _login_result(False, "needs_project", "project_not_created", page.url)


async def start_google_flow_login(
    *,
    account_id: str,
    email: str,
    password: str,
    totp_secret: str = "",
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
        _stage(account_id, "profile_exists", profile=os.path.basename(str(profile_path)))
        raise AccountOnboardingError("profile_exists")
    profile_path.mkdir(parents=True, exist_ok=True)
    _stage(
        account_id,
        "login_start",
        profile=os.path.basename(str(profile_path)),
        proxy=proxy_public_label(proxy_url),
        existing_profile=allow_existing_profile,
    )

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
        display_env = _onboarding_display_env()
        if display_env is not None:
            launch_kwargs["env"] = display_env
            _stage(account_id, "browser_launch", display=display_env.get("DISPLAY"))
        else:
            _stage(account_id, "browser_launch", display="(inherited)")
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

        await page.goto(GOOGLE_LOGIN_URL, timeout=deadline_ms, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        _stage(account_id, "page_loaded", host=_host(page.url))
        login_result = await _submit_google_login_if_needed(
            page,
            account_id=account_id,
            email=email,
            password=password,
            totp_secret=totp_secret,
            deadline_ms=deadline_ms,
        )
        if login_result is not None:
            session.status = str(login_result.get("status") or "login_failed")
            return session, login_result

        result = await _open_flow_status(page, deadline_ms)
        if result.get("ok"):
            _stage(account_id, "flow_opened", host=_host(page.url))
            result = await _ensure_flow_project(page)
            if result.get("ok"):
                _stage(account_id, "project_ready", host=_host(page.url))
        session.status = str(result.get("status") or "login_failed")
        _stage(account_id, "login_result", status=session.status, reason=result.get("reason"))
        # Active means done. needs_2fa/needs_challenge/needs_project all keep the
        # browser alive so the operator can finish via a 2FA code, or a manual
        # VNC step (challenge / project creation) followed by «Проверить снова».
        if session.status in {"active", "needs_2fa", "needs_challenge", "needs_project"}:
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
    _stage(session.account_id, "env_written", accounts_count=env_update["accounts_count"])
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
