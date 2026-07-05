"""Seller-side backend client lazy singleton (Phase 11 core split).

The seller bot talks to the consumer process's internal ``/internal/generate``
endpoint via ``seller_backend.BackendClient``. This builds that client lazily
once per process, caches it, and returns ``None`` on init failure so seller
flows can fall back to the "accounts unavailable" message. Extracted out of the
flow_bot composition root; the cache is now instance state instead of a module
global.

Telegram adapter code, not a platform-neutral core module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class BackendClientDeps:
    log: Any
    client_factory: Callable[[], Any]


class BackendClientCache:
    def __init__(self, deps: BackendClientDeps) -> None:
        self._d = deps
        self._cache: Any = None

    def get(self) -> Any:
        """Seller-side client to the consumer's /internal/generate (or None)."""
        if self._cache is None:
            try:
                self._cache = self._d.client_factory()
            except Exception:
                self._d.log.exception("backend client init failed")
                self._cache = None
        return self._cache


def _default_factory() -> Any:
    import seller_backend
    return seller_backend.BackendClient.from_env()


def make_backend_client_cache(log: Any) -> BackendClientCache:
    return BackendClientCache(BackendClientDeps(log=log, client_factory=_default_factory))
