"""AdminContext: the runtime services the /api/admin/* handlers depend on.

Phase 11 replaces the scattered `admin_api` module globals (`_pool`, `_keepers`,
`_video_clients`, `_startup_state`, `_proxy_sup`) with a single explicit context
object, injected by `register_admin_routes`. Mutable by design so the routes
registrar (and tests) can populate it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AdminContext:
    pool: Any = None
    keepers: Any = None
    video_clients: Any = None
    startup_state: Any = None
    proxy_sup: Any = None
