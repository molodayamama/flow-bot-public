"""Startup account readiness helpers.

Phase 1 keeps startup orchestration in ``flow_bot.py``. This module exists as
the stable home for the next extraction step without changing runtime behavior.
"""

from .pool import AccountPool

__all__ = ["AccountPool"]
