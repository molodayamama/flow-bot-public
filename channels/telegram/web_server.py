"""aiohttp web server bootstrap for the Telegram adapter process (Phase 11).

Mounts the admin API, the (consumer-only) internal generation endpoint, the
Robokassa result/success/fail/health routes, and the optional MAX webhook onto
one aiohttp app, then binds it to ``ROBOKASSA_WEB_HOST:ROBOKASSA_WEB_PORT``.
Also re-spawns persisted local ISP-proxies and starts their supervisor loop.

Extracted out of the flow_bot composition root; runtime singletons are injected
via :class:`WebServerDeps` so this module never imports flow_bot. Telegram
adapter code (uses aiohttp), not a platform-neutral core module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aiohttp import web


@dataclass(frozen=True)
class WebServerDeps:
    account_pool: Any
    keepers: dict
    clients: dict
    startup_state: dict
    proxy_supervisor: Any
    is_seller: Callable[[], bool]
    backend_generate: Callable[[dict], Awaitable[dict]]
    register_robokassa_routes: Callable[[web.Application], None]
    maybe_register_max_webhook: Callable[[web.Application], None]
    robokassa_configured: Callable[[], bool]
    web_host: str
    web_port: int
    log: Any


class WebServer:
    def __init__(self, deps: WebServerDeps) -> None:
        self._d = deps

    async def start(self) -> web.AppRunner:
        d = self._d
        import admin_api as _admin_api
        try:
            client_max_size = max(
                1024 * 1024,
                int(os.getenv("WEB_CLIENT_MAX_SIZE", str(32 * 1024 * 1024))),
            )
        except (TypeError, ValueError):
            client_max_size = 32 * 1024 * 1024
        app = web.Application(client_max_size=client_max_size)
        _admin_api.register_admin_routes(
            app, d.account_pool, d.keepers, d.clients,
            startup_state=d.startup_state,
            proxy_supervisor=d.proxy_supervisor,
        )
        if d.proxy_supervisor is not None:
            # Re-spawn persisted local proxies and keep them alive across crashes.
            try:
                await d.proxy_supervisor.ensure_all_running()
                import asyncio
                asyncio.create_task(d.proxy_supervisor.supervise_loop())
            except Exception:
                d.log.warning("local proxy supervisor startup failed", exc_info=True)
        if not d.is_seller():
            # Только consumer (с пулом) отдаёт генерацию для seller-бота (§A).
            try:
                import seller_backend
                seller_backend.register_internal_routes(app, d.backend_generate)
            except Exception:
                d.log.exception("internal generation endpoint registration failed")
        d.register_robokassa_routes(app)
        d.maybe_register_max_webhook(app)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, d.web_host, d.web_port)
        await site.start()
        d.log.info(
            "Web server listening on %s:%s (admin API + robokassa=%s)",
            d.web_host, d.web_port, d.robokassa_configured(),
        )
        return runner

    async def start_robokassa(self) -> web.AppRunner | None:
        return await self.start()
