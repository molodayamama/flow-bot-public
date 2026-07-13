"""MAX Bot bootstrap for the Telegram adapter process (Phase 11 core split).

Builds the (config, client, service) tuple shared by the MAX long-polling and
webhook intake paths, and offers two no-op-on-failure entry points:

* :meth:`MaxBootstrap.maybe_start_polling` — launch MAX long-polling as a
  background ``asyncio`` task (poll mode).
* :meth:`MaxBootstrap.maybe_register_webhook` — mount the MAX webhook route on
  the aiohttp app (webhook mode).

Both are no-ops when MAX is disabled, in the wrong mode, or missing a secret,
and never crash Telegram startup. The generation service reuses the same
backend + account pool as Telegram; this is the glue that wires it up.

Extracted out of the flow_bot composition root; runtime singletons are injected
via :class:`MaxBootstrapDeps` so this module never imports flow_bot. Telegram
adapter code (uses asyncio/aiohttp), not a platform-neutral core module.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from generation import backend_service


@dataclass(frozen=True)
class MaxBootstrapDeps:
    backend_generation_deps: Callable[[], Any]
    log: Any


class MaxBootstrap:
    def __init__(self, deps: MaxBootstrapDeps) -> None:
        self._d = deps

    def build_runtime(self):
        """Build (config, client, service) for MAX, or None when disabled/failed.

        Shared by the polling and webhook intake paths; the generation service
        uses the same backend + account pool as Telegram, and photo edit/animate
        download the incoming MAX photo and feed it to i2i/video.
        """
        from channels.base import PlatformFile
        from channels.max.client import (
            MaxBotClient,
            max_config_from_env,
            validate_max_config,
        )
        from channels.max.generation_adapter import BackendGenerationService

        config = max_config_from_env()
        if not config.enabled:
            return None
        validate_max_config(config, production=config.mode == "webhook")
        client = MaxBotClient(
            token=config.bot_token,
            base_url=config.api_base_url,
            ca_bundle=config.ca_bundle,
        )

        async def _download(url: str) -> bytes:
            return await client.get_file_bytes(PlatformFile(file_id="", url=url))

        service = BackendGenerationService(
            generate_images=backend_service.generate_images,
            generate_i2i=backend_service.generate_i2i,
            generate_video_ingredients=backend_service.generate_video_ingredients,
            download_bytes=_download,
            deps=self._d.backend_generation_deps(),
        )
        return config, client, service

    def maybe_start_polling(self) -> None:
        """Start MAX long-polling as a background task (poll mode, MAX_ENABLED=1).

        No-op (never crashes Telegram startup) when MAX is disabled, in webhook
        mode, or on any startup error. Webhook mode is mounted on the web server
        instead.
        """
        try:
            built = self.build_runtime()
            if built is None:
                return
            config, client, service = built
            if config.mode == "webhook":
                return  # webhook mode is registered on the web app, not polled
            from channels.max.runtime import run_max

            asyncio.create_task(run_max(service, client=client))
        except Exception:
            self._d.log.exception("MAX bot startup failed")

    def maybe_register_webhook(self, app) -> None:
        """Mount the MAX webhook route on the web app when MAX_MODE=webhook.

        No-op when MAX is disabled, not in webhook mode, or missing a webhook
        secret.
        """
        try:
            built = self.build_runtime()
            if built is None:
                return
            config, client, service = built
            if config.mode != "webhook":
                return
            if not config.webhook_secret:
                self._d.log.warning("MAX webhook mode requires MAX_WEBHOOK_SECRET; skipping")
                return
            from channels.max.handler import MaxMvpBot
            from channels.max.inbox import MaxWebhookInbox
            from channels.max.webhook_route import (
                register_max_health,
                register_max_subscription_lifecycle,
                register_max_webhook,
            )

            bot = MaxMvpBot(platform=client, service=service)
            inbox = MaxWebhookInbox(config.inbox_db)
            path = urlparse(config.webhook_url).path or "/max/webhook"
            register_max_webhook(
                app,
                dispatch=bot.handle,
                secret=config.webhook_secret,
                path=path,
                inbox=inbox,
            )
            register_max_health(app, inbox=inbox)
            register_max_subscription_lifecycle(
                app,
                client=client,
                webhook_url=config.webhook_url,
                secret=config.webhook_secret,
                inbox=inbox,
                dispatch=bot.handle,
                worker_count=config.inbox_workers,
            )
            self._d.log.info("MAX webhook route registered")
        except Exception:
            self._d.log.exception("MAX webhook registration failed")
            raise
