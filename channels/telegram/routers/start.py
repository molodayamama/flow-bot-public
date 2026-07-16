"""Telegram /start command router."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import Any

from aiogram import Router, types
from aiogram.filters import Command

import flow_copy


_WEB_LOGIN_RE = re.compile(r"^web_([A-Za-z0-9_-]{24,64})$")


@dataclass(frozen=True)
class StartDeps:
    """Injected start-command dependencies."""

    metrics: Any
    username: Callable[[types.Message], Any]
    reset_image_flow: Callable[[int], Any]
    vid_clear: Callable[[int], Any]
    credit_store: Any
    send_owner_alert: Callable[[str], Awaitable[Any]]
    referral_param_prefix: str
    referral_referred_bonus: int
    parse_channel_seed: Callable[[str], str | None]
    reply_menu_kb: Callable[[int], types.ReplyKeyboardMarkup]
    config: Any
    workspace: Callable[[int], MutableMapping[str, Any]]
    mp_jobs_kb: Callable[[str], types.InlineKeyboardMarkup]
    mp_stamp_message: Callable[[int, Any], None]
    price_gen: Callable[[int], int]
    onboarding_step1_kb: Callable[[], types.InlineKeyboardMarkup]
    show_main_menu: Callable[..., Awaitable[Any]]


def create_handler(deps: StartDeps) -> Callable[[types.Message], Awaitable[Any]]:
    """Build the /start handler over injected application services."""

    async def cmd_start(message: types.Message):
        metrics = deps.metrics
        _username = deps.username
        _reset_image_flow = deps.reset_image_flow
        _vid_clear = deps.vid_clear
        credit_store = deps.credit_store
        _send_owner_alert = deps.send_owner_alert
        REFERRAL_PARAM_PREFIX = deps.referral_param_prefix
        REFERRAL_REFERRED_BONUS = deps.referral_referred_bonus
        parse_channel_seed = deps.parse_channel_seed
        reply_menu_kb = deps.reply_menu_kb
        _cfg = deps.config
        _ws = deps.workspace
        mp_jobs_kb = deps.mp_jobs_kb
        _mp_stamp_message = deps.mp_stamp_message
        price_gen = deps.price_gen
        _onboarding_step1_kb = deps.onboarding_step1_kb
        show_main_menu = deps.show_main_menu

        user_id = message.from_user.id
        parts = (message.text or "").split(maxsplit=1)
        payload = parts[1].strip() if len(parts) > 1 else ""
        channel = parse_channel_seed(payload)
        is_new = not metrics.user_exists(user_id)
        metrics.upsert_user(
            user_id,
            username=_username(message),
            first_name=getattr(message.from_user, "first_name", None),
            channel=channel if is_new else None,
        )
        _reset_image_flow(user_id)
        _vid_clear(user_id)
        credit_store.balance(user_id)
        metrics.log_event(
            "user_started",
            user_id=user_id,
            username=_username(message),
            source="command",
            payload={"is_new": is_new, "seed_channel": channel},
        )
        if is_new:
            metrics.log_event(
                "new_user",
                user_id=user_id,
                username=_username(message),
                source=channel or "organic",
                payload={"seed_channel": channel},
            )
            uname = _username(message)
            uname_str = f"@{uname}" if uname else f"id {user_id}"
            asyncio.create_task(_send_owner_alert(
                f"\U0001f464 <b>\u041d\u043e\u0432\u044b\u0439 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c</b>\n{uname_str}"
            ))

        web_login = _WEB_LOGIN_RE.fullmatch(payload)
        if web_login and not getattr(message.from_user, "is_bot", False):
            display_name = " ".join(
                part for part in (
                    str(getattr(message.from_user, "first_name", "") or "").strip(),
                    str(getattr(message.from_user, "last_name", "") or "").strip(),
                    f"@{_username(message)}" if _username(message) else "",
                ) if part
            )[:80]
            claimed = metrics.claim_web_login_challenge(
                web_login.group(1),
                "telegram",
                user_id,
                display_name,
            )
            if claimed:
                await message.answer(
                    "Вход на photozhab.ru подтверждён. Вернитесь в браузер — сайт подхватит авторизацию автоматически.",
                    parse_mode="HTML",
                )
            else:
                await message.answer(
                    "Ссылка для входа недействительна или уже использована. "
                    "Вернитесь на сайт и запросите новую."
                )
            return

        _referral_welcome_bonus: int = 0
        if payload.startswith(REFERRAL_PARAM_PREFIX) and not getattr(message.from_user, "is_bot", False):
            raw = payload[len(REFERRAL_PARAM_PREFIX):]
            if raw.isdigit():
                referrer_id = int(raw)
                if referrer_id != user_id and is_new and metrics.record_referral_join(
                    referrer_user_id=referrer_id, referred_user_id=user_id
                ):
                    if REFERRAL_REFERRED_BONUS > 0:
                        credit_store.add(user_id, REFERRAL_REFERRED_BONUS)
                        metrics.log_event(
                            "referral_referred_bonus",
                            user_id=user_id,
                            payload={
                                "referrer": referrer_id,
                                "bonus": REFERRAL_REFERRED_BONUS,
                            },
                        )
                    metrics.log_event(
                        "referral_joined",
                        user_id=user_id,
                        payload={"referrer": referrer_id},
                    )
                    _referral_welcome_bonus = credit_store.balance(user_id)

        if channel and not getattr(message.from_user, "is_bot", False):
            metrics.log_event(
                "channel_seed_clicked",
                user_id=user_id,
                username=_username(message),
                source=channel,
                payload={"channel": channel, "is_new": is_new},
            )
            if is_new and metrics.record_acquisition(user_id=user_id, channel=channel):
                metrics.log_event(
                    "acquired_from_channel",
                    user_id=user_id,
                    username=_username(message),
                    source=channel,
                    payload={"channel": channel},
                )
                metrics.log_event(
                    "channel_seed_new",
                    user_id=user_id,
                    username=_username(message),
                    source=channel,
                    payload={"channel": channel},
                )
            elif not is_new:
                metrics.log_event(
                    "channel_seed_returning",
                    user_id=user_id,
                    username=_username(message),
                    source=channel,
                    payload={"channel": channel},
                )

        await message.answer("\U0001f447", reply_markup=reply_menu_kb(user_id))

        if _cfg.IS_SELLER:
            st = _ws(user_id)
            st.setdefault("mp_platform", "wb")
            sent = await message.answer(
                flow_copy.msg("welcome_seller"),
                reply_markup=mp_jobs_kb(st.get("mp_platform", "wb")),
                parse_mode="HTML",
            )
            _mp_stamp_message(user_id, sent)
            return

        if _referral_welcome_bonus > 0:
            gens = _referral_welcome_bonus // price_gen(1)
            await message.answer(
                flow_copy.msg("referral_welcome", bonus=_referral_welcome_bonus, gens=gens),
                parse_mode="HTML",
            )
            if is_new:
                await message.answer(
                    flow_copy.msg("onboarding_step1"),
                    reply_markup=_onboarding_step1_kb(),
                )
                return
        elif is_new:
            await message.answer(
                flow_copy.msg("onboarding_step1"),
                reply_markup=_onboarding_step1_kb(),
            )
            return

        await message.answer(flow_copy.msg("welcome"), parse_mode="HTML")
        await show_main_menu(message, user_id=user_id)

    return cmd_start


def create_router(
    deps: StartDeps,
    *,
    handler: Callable[[types.Message], Awaitable[Any]] | None = None,
) -> Router:
    """Build the /start Router."""

    router = Router(name="tg-start")
    router.message(Command("start"))(handler or create_handler(deps))
    return router
