"""Tests for channels.telegram.agent_flow."""

from __future__ import annotations

import asyncio
import unittest

from aiogram import types

from channels.telegram.agent_flow import AgentFlow, AgentFlowDeps


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Log:
    def __init__(self):
        self.infos = []
        self.warnings = []

    def info(self, *args):
        self.infos.append(args)

    def warning(self, *args, **kwargs):
        self.warnings.append((args, kwargs))


class _Pool:
    def __init__(self):
        self.ids = ["v1", "v2", "txt"]

    def account_ids(self):
        return list(self.ids)

    def is_video_capable(self, acc):
        return acc in {"v1", "v2"}


class _Client:
    def __init__(self, result, calls):
        self.result = result
        self.calls = calls

    async def improve_prompt(self, instruction, *, project_id):
        self.calls.append((instruction, project_id))
        return self.result


class _Credits:
    def __init__(self, balance=100):
        self.value = balance
        self.charges = []
        self.refunds = []

    def balance(self, user_id):
        return self.value

    def charge(self, user_id, price):
        self.charges.append((user_id, price))

    def refund(self, user_id, price):
        self.refunds.append((user_id, price))


class _Metrics:
    def __init__(self):
        self.events = []

    def log_event(self, *args, **kwargs):
        self.events.append((args, kwargs))


class _Message:
    def __init__(self):
        self.edits = []
        self.answers = []

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


class _Callback:
    def __init__(self):
        self.message = _Message()
        self.answers = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))


def _subject(*, workspace=None, credits=None, clients=None):
    workspace = workspace if workspace is not None else {}
    log = _Log()
    credits = credits or _Credits()
    metrics = _Metrics()
    clients = clients or {}
    calls = []

    async def ensure_user_project(user_id, *, account_id):
        return f"project:{account_id}"

    def client_for_acc(acc):
        return clients.get(acc) or _Client({}, calls)

    deps = AgentFlowDeps(
        workspace=lambda uid: workspace.setdefault(uid, {}),
        ensure_user_project=ensure_user_project,
        account_for=lambda uid: "base",
        account_for_video=lambda uid: "v1",
        account_pool=_Pool(),
        client_for_acc=client_for_acc,
        log=log,
        credit_store=credits,
        metrics=metrics,
        menu_button=lambda text_key, data: types.InlineKeyboardButton(
            text=text_key, callback_data=data
        ),
    )
    return AgentFlow(deps), workspace, credits, metrics, calls


class AgentFlowTests(unittest.TestCase):
    def test_improve_call_tries_video_accounts_until_content(self):
        calls = []
        clients = {
            "v1": _Client({}, calls),
            "v2": _Client({"variants": [{"title": "A", "prompt": "B"}]}, calls),
        }
        flow, workspace, credits, metrics, _ = _subject(clients=clients)
        result = run(flow.improve_call(7, "cat", instruction_fn=lambda p: f"improve:{p}"))
        self.assertEqual(result["variants"][0]["prompt"], "B")
        self.assertEqual(calls, [("improve:cat", "project:base"), ("improve:cat", "project:base")])

    def test_improve_flow_low_balance_does_not_charge(self):
        flow, workspace, credits, metrics, calls = _subject(
            workspace={7: {"prompt": "cat"}}, credits=_Credits(balance=0)
        )
        callback = _Callback()
        edits = []

        async def edit_fn(*args, **kwargs):
            edits.append((args, kwargs))

        run(flow.improve_flow(
            callback,
            user_id=7,
            prompt_key="prompt",
            source="image",
            pick_prefix="ag:pick:",
            keep_data="ag:keep",
            rerender=lambda *a, **k: None,
            edit_fn=edit_fn,
            empty_prompt_msg="empty",
        ))
        self.assertFalse(credits.charges)
        self.assertTrue(edits)

    def test_improve_flow_stores_variants_and_renders_picker(self):
        calls = []
        clients = {"v1": _Client({
            "variants": [
                {"title": "One", "prompt": "Prompt 1"},
                {"title": "Two", "prompt": "Prompt 2"},
            ]
        }, calls)}
        flow, workspace, credits, metrics, _ = _subject(
            workspace={7: {"prompt": "cat"}}, clients=clients
        )
        callback = _Callback()
        edits = []

        async def edit_fn(*args, **kwargs):
            edits.append((args, kwargs))

        run(flow.improve_flow(
            callback,
            user_id=7,
            prompt_key="prompt",
            source="image",
            pick_prefix="ag:pick:",
            keep_data="ag:keep",
            rerender=lambda *a, **k: None,
            edit_fn=edit_fn,
            empty_prompt_msg="empty",
            instruction_fn=lambda p: p,
        ))
        self.assertEqual(workspace[7]["ag_variants"][1]["prompt"], "Prompt 2")
        self.assertTrue(credits.charges)
        self.assertEqual(metrics.events[0][0][0], "prompt_improve")
        self.assertEqual(edits[-1][1]["parse_mode"], "HTML")

    def test_pick_updates_prompt_and_clears_variants(self):
        flow, workspace, credits, metrics, calls = _subject(workspace={7: {
            "prompt": "old",
            "ag_variants": [{"prompt": "new"}],
        }})
        callback = _Callback()
        rerenders = []

        async def rerender(*args, **kwargs):
            rerenders.append((args, kwargs))

        run(flow.pick(callback, user_id=7, idx_str="0", prompt_key="prompt", rerender=rerender))
        self.assertEqual(workspace[7]["prompt"], "new")
        self.assertNotIn("ag_variants", workspace[7])
        self.assertTrue(rerenders)


if __name__ == "__main__":
    unittest.main()
