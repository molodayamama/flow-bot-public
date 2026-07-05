"""Playwright driver shutdown future filter (Phase 11 core split).

Installs an ``asyncio`` loop exception handler that swallows the noisy
``Future exception was never retrieved`` / ``Connection closed while reading
from the driver`` warnings Playwright emits during process teardown, while
forwarding everything else to the prior handler. Extracted out of the flow_bot
composition root; the loop is fetched at call time so this module never imports
flow_bot.
"""

from __future__ import annotations

import asyncio


def install_shutdown_exception_filter() -> None:
    loop = asyncio.get_running_loop()
    default_handler = loop.get_exception_handler()

    def _handler(loop, context):
        exc = context.get("exception")
        message = context.get("message", "")
        if (
            message == "Future exception was never retrieved"
            and exc is not None
            and "Connection closed while reading from the driver" in str(exc)
        ):
            return
        if default_handler is not None:
            default_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)
