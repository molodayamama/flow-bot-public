"""Regression net for Telegram MESSAGE routing (Phase 6, wave F).

Counterpart of test_callback_routing.py for the message observer chain.
Built when handle_video_upload (``F.video | F.document``) moved from the
dp level into channels/telegram/routers/video_upload_input.py: aiogram
matches dp-level observers before included routers, so the move changes the
matching position of the video/document filter relative to everything else.

The grid below resolves REAL aiogram ``Message`` objects through the real
registered handlers' filter chains in aiogram's precedence order (dp-level
observers in registration order, then sub_routers in inclusion order) and
pins the full routing table:

- All non-video routing is unchanged: text commands, plain text, photos and
  payments land exactly where they did (their filters are disjoint from
  ``F.video | F.document``).
- Captionless and plain-captioned videos/documents land on the extracted
  handler, as before.
- A video captioned with a DP-LEVEL command (``/start``) still hits that
  command: dp-level precedes routers, unchanged.
- A video captioned with a ROUTER-owned command (``/menu``) now hits the
  command handler because command routers are included before the
  video-input router. This is the one intentional delta vs. the pre-move
  layout (where the dp-level video handler swallowed it silently) and it
  RESTORES the original monolith behaviour, where all command handlers were
  registered before handle_video_upload.

No handler bodies are executed: only ``HandlerObject.check`` (filters) runs.
"""

from __future__ import annotations

import asyncio
import datetime
import unittest
from types import SimpleNamespace

from aiogram.types import Chat, Document, Message, PhotoSize, User, Video

import flow_bot

# Protected dp-level message handlers that must NOT be extracted (payments,
# media catch-alls, /start deep-links). cmd_status stays by design too
# (keeper-internals closure, see HANDOFF session 23).
EXPECTED_DP_LEVEL = [
    "cmd_start",
    "cmd_status",
    "on_successful_payment",
    "handle_photo",
    "handle_plain_text",
]


def _message(**kwargs) -> Message:
    base = dict(
        message_id=1,
        date=datetime.datetime(2026, 7, 3, 12, 0, 0),
        chat=Chat(id=1, type="private"),
        from_user=User(id=7, is_bot=False, first_name="t"),
    )
    base.update(kwargs)
    return Message(**base)


_VIDEO = Video(file_id="f", file_unique_id="u", width=10, height=10, duration=3)
_DOCUMENT = Document(file_id="f2", file_unique_id="u2")
_PHOTO = [PhotoSize(file_id="p", file_unique_id="pu", width=10, height=10)]

# The pinned routing table: description -> (Message kwargs, expected handler).
ROUTING_TABLE = {
    "text /start": (dict(text="/start"), "dp:cmd_start"),
    "text /status": (dict(text="/status"), "dp:cmd_status"),
    "text /menu": (dict(text="/menu"), "tg-public-commands:cmd_menu"),
    "text /img": (dict(text="/img cat"), "tg-generation-commands:cmd_img"),
    "text /grant": (dict(text="/grant 1 10"), "tg-admin-credits:cmd_grant"),
    "text /admin_today": (dict(text="/admin_today"), "tg-admin-reports:cmd_admin_today"),
    "text /acc_off": (dict(text="/acc_off a1"), "tg-admin-accounts:cmd_acc_off"),
    "plain text": (dict(text="hello"), "dp:handle_plain_text"),
    "photo": (dict(photo=_PHOTO), "dp:handle_photo"),
    "photo with command caption": (dict(photo=_PHOTO, caption="/menu"), "dp:handle_photo"),
    "video no caption": (dict(video=_VIDEO), "tg-video-upload-input:handle_video_upload"),
    "video plain caption": (
        dict(video=_VIDEO, caption="make it fly"),
        "tg-video-upload-input:handle_video_upload",
    ),
    "document no caption": (dict(document=_DOCUMENT), "tg-video-upload-input:handle_video_upload"),
    "document plain caption": (
        dict(document=_DOCUMENT, caption="edit this"),
        "tg-video-upload-input:handle_video_upload",
    ),
    # dp-level commands read captions and dp-level precedes routers: unchanged.
    "video with dp-command caption": (dict(video=_VIDEO, caption="/start"), "dp:cmd_start"),
    # Router-owned commands win over the video-input router (included later).
    # Intentional delta vs the dp-level video handler layout; restores the
    # original monolith order where commands preceded handle_video_upload.
    "video with router-command caption": (
        dict(video=_VIDEO, caption="/menu"),
        "tg-public-commands:cmd_menu",
    ),
}


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _match_handler(message: Message) -> str | None:
    """Resolve which message handler aiogram would run, without executing it."""
    fake_bot = SimpleNamespace()
    for handler in flow_bot.dp.message.handlers:
        ok, _ = await handler.check(message, bot=fake_bot)
        if ok:
            return f"dp:{handler.callback.__name__}"
    for router in flow_bot.dp.sub_routers:
        for handler in router.message.handlers:
            ok, _ = await handler.check(message, bot=fake_bot)
            if ok:
                return f"{router.name}:{handler.callback.__name__}"
    return None


class MessageRoutingRegressionTests(unittest.TestCase):
    def test_dp_level_message_handlers_are_exactly_the_protected_set(self) -> None:
        names = [h.callback.__name__ for h in flow_bot.dp.message.handlers]
        self.assertEqual(names, EXPECTED_DP_LEVEL)

    def test_video_input_router_is_included_after_command_routers(self) -> None:
        names = [router.name for router in flow_bot.dp.sub_routers]
        self.assertIn("tg-video-upload-input", names)
        video_idx = names.index("tg-video-upload-input")
        for command_router in (
            "tg-public-commands", "tg-generation-commands",
            "tg-admin-accounts", "tg-admin-reports", "tg-admin-credits",
        ):
            self.assertLess(names.index(command_router), video_idx, command_router)
        # And still above the tail callback fallback.
        self.assertEqual(names[-1], "tg-callback-fallback")

    def test_message_routing_table_is_stable(self) -> None:
        for description, (kwargs, expected) in ROUTING_TABLE.items():
            with self.subTest(case=description):
                self.assertEqual(run(_match_handler(_message(**kwargs))), expected)

    def test_video_document_filter_is_disjoint_from_other_dp_catchalls(self) -> None:
        # Photos, plain text, and command texts never reach the video-input
        # router; videos/documents never reach photo/plain-text handlers.
        photo_result = run(_match_handler(_message(photo=_PHOTO)))
        text_result = run(_match_handler(_message(text="hello")))
        video_result = run(_match_handler(_message(video=_VIDEO)))
        document_result = run(_match_handler(_message(document=_DOCUMENT)))
        self.assertNotIn("video-upload-input", photo_result)
        self.assertNotIn("video-upload-input", text_result)
        self.assertNotIn("handle_photo", video_result)
        self.assertNotIn("handle_plain_text", video_result)
        self.assertNotIn("handle_photo", document_result)
        self.assertNotIn("handle_plain_text", document_result)


if __name__ == "__main__":
    unittest.main()
