"""Regression net for Telegram callback routing (Phase 6).

The dp-level bare ``@dp.callback_query()`` catch-all was replaced by a precise
image-action filter plus a tail fallback router. These tests feed every
callback_data value the codebase can generate (scraped literals, parametrised
templates with sample values, and all ``flow_core.ACTION_PREFIXES`` action
callbacks) through the real registered handlers' filter chain - in aiogram's
matching order - and assert each one lands on the same handler the old
routing table sent it to. Future callback-router extraction waves must keep
this suite green: it proves user-visible routing never changed.

No handler bodies are executed: only ``HandlerObject.check`` (filters) runs.
"""

from __future__ import annotations

import asyncio
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

import flow_bot
from flow_core import ACTION_PREFIXES, action_callback_data

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Old routing table: dp-level prefix handlers in their registration order,
# then the exact-match retry button, then the image-action catch-all whose
# unknown branch silently acked everything else. Every prefix below has since
# been extracted into its own router module (Phase 6 waves); the table still
# maps prefix -> handler *function name* only, because each extraction kept
# the original handler name unchanged, so `_match_handler` (which resolves by
# `handler.callback.__name__`) keeps working regardless of dp-level vs
# router-level registration. "v:" (on_video_action) was the last dp-level
# group to move, into channels/telegram/routers/video.py.
DP_PREFIX_ORDER = [
    ("mp:", "on_marketplace_action"),
    ("m:", "on_menu_action"),
    ("ob:", "on_onboarding_action"),
    ("pr:", "on_photo_route_choice"),
    ("an:", "on_animate_action"),
    ("ih:", "on_ideas_hub_action"),
    ("tp:", "on_template_action"),
    ("ag:", "on_agent_action"),
    ("gp:", "on_guided_picker_action"),
    ("vu:", "on_video_upload_action"),
    ("es:", "on_edit_settings"),
    ("w:", "on_wizard_action"),
    ("v:", "on_video_action"),
]
FALLBACK_HANDLER = "on_unknown_callback"

_CALLBACK_RE = re.compile(r"callback_data=f?\"([^\"]+)\"|callback_data=f?'([^']+)'")
_SOURCE_FILES = (
    "flow_bot.py",
    str(Path("channels") / "telegram" / "keyboards.py"),
    str(Path("channels") / "telegram" / "routers" / "marketplace.py"),
    str(Path("channels") / "telegram" / "routers" / "video.py"),
    str(Path("channels") / "telegram" / "texts.py"),
    "prompts_lib.py",
)


def _scraped_callback_corpus() -> set[str]:
    """All literal/templated callback_data values generated in the codebase."""
    corpus: set[str] = set()
    for rel in _SOURCE_FILES:
        path = PROJECT_ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for match in _CALLBACK_RE.finditer(text):
            value = match.group(1) or match.group(2)
            # Substitute a sample for every f-string parameter.
            sample = re.sub(r"\{[^}]*\}", "1", value)
            if sample and not sample.startswith("{"):
                corpus.add(sample)
    return corpus


def expected_handler(data: str) -> str:
    """Reference implementation of the pre-change routing decision."""
    if data == "img:retry":
        return "on_img_retry"
    for prefix, handler in DP_PREFIX_ORDER:
        if data.startswith(prefix):
            return handler
    for prefix in ACTION_PREFIXES.values():
        if data.startswith(prefix):
            return "on_image_action"
    return FALLBACK_HANDLER  # old: on_image_action's silent answer() branch


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _match_handler(data: str) -> str | None:
    """Resolve which handler aiogram would run for this callback_data.

    Mirrors aiogram precedence: dp-level observers in registration order
    first, then included routers in inclusion order.
    """
    event = SimpleNamespace(data=data)
    for handler in flow_bot.dp.callback_query.handlers:
        ok, _ = await handler.check(event)
        if ok:
            return handler.callback.__name__
    for router in flow_bot.dp.sub_routers:
        for handler in router.callback_query.handlers:
            ok, _ = await handler.check(event)
            if ok:
                return handler.callback.__name__
    return None


class CallbackRoutingRegressionTests(unittest.TestCase):
    def test_no_bare_dp_level_callback_catch_all_remains(self) -> None:
        for handler in flow_bot.dp.callback_query.handlers:
            self.assertTrue(
                handler.filters,
                f"dp-level callback handler {handler.callback.__name__} has no filter",
            )

    def test_fallback_router_is_included_last(self) -> None:
        names = [router.name for router in flow_bot.dp.sub_routers]
        self.assertIn("tg-callback-fallback", names)
        self.assertEqual(names[-1], "tg-callback-fallback")

    def test_every_generated_callback_routes_as_before(self) -> None:
        corpus = _scraped_callback_corpus()
        self.assertGreater(len(corpus), 80)  # sanity: scrape actually worked
        # Action buttons for every registered action prefix.
        for action in ACTION_PREFIXES:
            corpus.add(action_callback_data(action, "tok123"))
        corpus.add("img:retry")
        for data in sorted(corpus):
            with self.subTest(callback_data=data):
                self.assertEqual(run(_match_handler(data)), expected_handler(data))

    def test_unknown_callbacks_still_get_a_handler(self) -> None:
        # Expired/legacy buttons from older releases: silently acked, never
        # left hanging. The old catch-all did this via its None branch.
        for data in ("zz:whatever", "noop", "legacy:btn:9", "x", ""):
            with self.subTest(callback_data=data):
                self.assertEqual(run(_match_handler(data)), FALLBACK_HANDLER)

    def test_action_callbacks_do_not_collide_with_dp_prefixes(self) -> None:
        # Guards the prefix table: no action callback may be swallowed by a
        # dp-level startswith filter (e.g. "mpe:" vs "mp:", "mix:" vs "m:").
        for action, prefix in ACTION_PREFIXES.items():
            data = f"{prefix}tok123"
            with self.subTest(action=action):
                self.assertEqual(run(_match_handler(data)), "on_image_action")


if __name__ == "__main__":
    unittest.main()
