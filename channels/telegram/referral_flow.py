"""Referral Telegram glue for links, CTAs, and reward notifications."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import quote

from aiogram import types
from aiogram.exceptions import TelegramForbiddenError

import flow_copy


@dataclass(frozen=True)
class ReferralFlowDeps:
    bot_username: Callable[[], str | None]
    referral_param_prefix: str
    referred_bonus: int
    referral_service: Any
    metrics: Any
    bot: Any
    copy: Any = flow_copy
    create_task: Callable[[Awaitable[Any]], Any] = asyncio.create_task


class ReferralFlow:
    def __init__(self, deps: ReferralFlowDeps) -> None:
        self._d = deps

    def link(self, user_id: int) -> str:
        """Personal referral deep-link, falling back to raw /start payload."""
        username = self._d.bot_username()
        payload = f"{self._d.referral_param_prefix}{user_id}"
        if username:
            return f"https://t.me/{username}?start={payload}"
        return payload

    def invite_button(self, user_id: int) -> types.InlineKeyboardButton:
        link = self.link(user_id)
        share = (
            "https://t.me/share/url?url=" + quote(link, safe="")
            + "&text=" + quote(self._d.copy.msg("invite_share_text"), safe="")
        )
        return types.InlineKeyboardButton(
            text=self._d.copy.label("invite_friend"),
            url=share,
        )

    def maybe_apply_rewards(
        self,
        referred_user_id: int,
        *,
        stars_paid: int,
        credits_issued: int,
        pack_id: str,
        provider_payment_id: str,
    ) -> None:
        self._d.referral_service.apply_payment_rewards(
            referred_user_id,
            stars_paid=stars_paid,
            credits_issued=credits_issued,
            pack_id=pack_id,
            provider_payment_id=provider_payment_id,
        )

    def first_cta_text(self, user_id: int) -> str | None:
        try:
            if int(self._d.metrics.referral_stats(user_id).get("invited") or 0) > 0:
                return None
        except Exception:
            return None
        return self._d.copy.msg("first_referral_cta", referred=self._d.referred_bonus)

    async def post_generation_hooks(
        self,
        message: types.Message,
        user_id: int,
        *,
        send_cta: bool = True,
    ) -> None:
        if not send_cta:
            return
        text = self.first_cta_text(user_id)
        if not text:
            return
        try:
            await message.answer(
                text,
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [self.invite_button(user_id)],
                ]),
            )
        except Exception:
            pass

    def clawback_rewards(self, referred_user_id: int, charge_id: str) -> None:
        self._d.referral_service.clawback(referred_user_id, charge_id)

    def notify_referrer(
        self,
        referrer_id: int,
        bonus: int,
        *,
        message_key: str = "referral_reward_got",
    ) -> None:
        async def _send() -> None:
            try:
                await self._d.bot.send_message(
                    referrer_id,
                    self._d.copy.msg(message_key, bonus=bonus),
                    parse_mode="HTML",
                )
            except TelegramForbiddenError:
                self._d.metrics.mark_user_blocked(referrer_id)
            except Exception:
                pass

        coro = _send()
        try:
            self._d.create_task(coro)
        except Exception:
            try:
                coro.close()
            except Exception:
                pass
