"""Prompt-agent improve/pick flow for Telegram wizards."""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, MutableMapping

from aiogram import types

import flow_copy
from flow_core import action_price
from product.agent_prompts import (
    edit_instruction as agent_edit_instruction,
    improve_instruction as _agent_improve_instruction,
)


@dataclass(frozen=True)
class AgentFlowDeps:
    workspace: Callable[[int], MutableMapping[str, Any]]
    ensure_user_project: Callable[..., Awaitable[str]]
    account_for: Callable[[int], str | None]
    account_for_video: Callable[[int], str | None]
    account_pool: Any
    client_for_acc: Callable[[str], Any]
    log: Any
    credit_store: Any
    metrics: Any
    menu_button: Callable[..., types.InlineKeyboardButton]


class AgentFlow:
    def __init__(self, deps: AgentFlowDeps) -> None:
        self._d = deps

    async def improve_call(
        self, user_id: int, prompt: str, *, instruction_fn=None
    ) -> dict:
        """Improve a prompt via Flow agent, trying video-capable accounts."""
        d = self._d
        instruction_fn = instruction_fn or _agent_improve_instruction
        try:
            project_id = await d.ensure_user_project(user_id, account_id=d.account_for(user_id))
            candidates: list[str] = []
            primary = d.account_for_video(user_id)
            if primary:
                candidates.append(primary)
            for acc in d.account_pool.account_ids():
                if acc not in candidates and d.account_pool.is_video_capable(acc):
                    candidates.append(acc)
            instruction = instruction_fn(prompt)
            res: dict = {}
            for acc_id in candidates[:5]:
                res = await d.client_for_acc(acc_id).improve_prompt(
                    instruction, project_id=project_id
                )
                d.log.info(
                    "✨ improve try acc=%s status=%s err=%s variants=%d",
                    acc_id, (res or {}).get("status"), (res or {}).get("error"),
                    len((res or {}).get("variants") or []),
                )
                if (res or {}).get("variants") or (res or {}).get("single"):
                    break
            return res or {}
        except Exception as exc:  # noqa: BLE001
            d.log.warning("prompt_improve exception: %s", exc.__class__.__name__, exc_info=True)
            return {"error": "exception"}

    def variants_view(
        self, variants: list[dict], *, pick_prefix: str, keep_data: str
    ) -> tuple[str, types.InlineKeyboardMarkup]:
        rows = []
        for i, variant in enumerate(variants):
            label = (variant["title"] or variant["prompt"])[:48]
            rows.append([types.InlineKeyboardButton(
                text=f"{i + 1}. {label}", callback_data=f"{pick_prefix}{i}"
            )])
        rows.append([types.InlineKeyboardButton(
            text="↩️ Оставить мой", callback_data=keep_data
        )])
        body = (
            "✨ <b>Варианты промпта</b> — выбери, какой использовать:\n\n"
            + "\n\n".join(
                f"<b>{i + 1}. {html.escape(variant['title'])}</b>\n"
                f"{html.escape(variant['prompt'][:300])}"
                for i, variant in enumerate(variants)
            )
        )
        return body, types.InlineKeyboardMarkup(inline_keyboard=rows)

    async def improve_flow(
        self,
        callback: types.CallbackQuery,
        *,
        user_id: int,
        prompt_key: str,
        source: str,
        pick_prefix: str,
        keep_data: str,
        rerender,
        edit_fn,
        empty_prompt_msg: str,
        instruction_fn=None,
    ) -> None:
        d = self._d
        msg = callback.message
        st = d.workspace(user_id)
        prompt = (st.get(prompt_key) or "").strip()
        if not prompt:
            await callback.answer(empty_prompt_msg, show_alert=True)
            return
        price = action_price("prompt_improve")
        if d.credit_store.balance(user_id) < price:
            await callback.answer()
            kb_low = types.InlineKeyboardMarkup(inline_keyboard=[
                [d.menu_button("topup", "m:topup")],
                [d.menu_button("menu", "m:menu")],
            ])
            await edit_fn(
                msg,
                flow_copy.msg(
                    "low_balance", needed=price, have=d.credit_store.balance(user_id)
                ),
                kb_low,
                parse_mode="HTML",
            )
            return
        d.credit_store.charge(user_id, price)
        d.metrics.log_event("prompt_improve", user_id=user_id, source=source)
        await callback.answer("✨ Думаю над вариантами…")
        try:
            await msg.edit_text("✨ Подбираю варианты промпта…")
        except Exception:
            pass
        res = await self.improve_call(user_id, prompt, instruction_fn=instruction_fn)
        variants = [v for v in (res.get("variants") or []) if v.get("prompt")][:3]
        single = res.get("single")
        if not variants and not single:
            d.credit_store.refund(user_id, price)
            await rerender(msg, user_id=user_id, edit=True)
            try:
                await msg.answer(
                    "Не получилось улучшить промпт — кредиты вернул. Попробуй ещё раз 🙏"
                )
            except Exception:
                pass
            return
        if not variants and single:
            st[prompt_key] = single
            await rerender(msg, user_id=user_id, edit=True)
            return
        st["ag_variants"] = [
            {"title": v.get("title", ""), "prompt": v.get("prompt", "")}
            for v in variants
        ]
        body, kb = self.variants_view(
            st["ag_variants"], pick_prefix=pick_prefix, keep_data=keep_data
        )
        await edit_fn(msg, body, kb, parse_mode="HTML")

    async def pick(
        self, callback: types.CallbackQuery, *, user_id: int, idx_str: str,
        prompt_key: str, rerender,
    ) -> None:
        st = self._d.workspace(user_id)
        try:
            idx = int(idx_str)
        except (ValueError, TypeError):
            await callback.answer()
            return
        variants = st.get("ag_variants") or []
        if 0 <= idx < len(variants):
            st[prompt_key] = variants[idx].get("prompt") or st.get(prompt_key)
            await callback.answer("Готово ✨")
        else:
            await callback.answer()
        st.pop("ag_variants", None)
        await rerender(callback.message, user_id=user_id, edit=True)
