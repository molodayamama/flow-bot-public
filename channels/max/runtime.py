"""MAX MVP runtime startup (glue).

Builds a `MaxBotClient` from env, wires it into `MaxMvpBot` with an injected
generation service (and optional wallet), and runs the long-polling loop. The
generation service is injected by the caller (the app owns generation), so this
module stays free of any flow_bot import and is fully testable with fakes.

Nothing runs unless `MAX_ENABLED=1` and `MAX_BOT_TOKEN` are set, so importing or
calling this in the offline test suite is a safe no-op by default.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Awaitable, Callable, Mapping

from channels.max.client import MaxBotClient, max_config_from_env, validate_max_config
from channels.max.handler import GenerationService, MaxMvpBot, Wallet
from channels.max.polling import run_polling

log = logging.getLogger(__name__)


def build_max_bot(
    service: GenerationService,
    *,
    env: Mapping[str, str] | None = None,
    wallet: Wallet | None = None,
    client: MaxBotClient | None = None,
) -> tuple[MaxBotClient, MaxMvpBot] | None:
    """Build (client, MaxMvpBot) from env, or None when MAX is disabled.

    Raises ValueError when enabled but the bot token is missing.
    """
    config = max_config_from_env(env)
    if not config.enabled:
        log.info("MAX disabled (MAX_ENABLED not set); skipping MAX bot startup")
        return None
    validate_max_config(config)
    client = client or MaxBotClient(
        token=config.bot_token, base_url=config.api_base_url, ca_bundle=config.ca_bundle
    )
    bot = MaxMvpBot(platform=client, service=service, wallet=wallet)
    return client, bot


async def run_max(
    service: GenerationService,
    *,
    env: Mapping[str, str] | None = None,
    wallet: Wallet | None = None,
    client: MaxBotClient | None = None,
    should_stop: Callable[[], bool] | None = None,
    idle_delay: float = 1.0,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> Any:
    """Start the MAX polling runtime; a no-op (returns None) when MAX is disabled."""
    built = build_max_bot(service, env=env, wallet=wallet, client=client)
    if built is None:
        return None
    max_client, bot = built
    log.info("MAX bot starting long-polling loop")
    kwargs: dict[str, Any] = {"should_stop": should_stop, "idle_delay": idle_delay}
    if sleep is not None:
        kwargs["sleep"] = sleep
    try:
        return await run_polling(max_client, bot, **kwargs)
    finally:
        close = getattr(max_client, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result
