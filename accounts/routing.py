"""Routing helpers for the runtime account pool.

The first extraction keeps routing behavior on ``AccountPool`` itself for
backward compatibility; this module gives later phases a stable import target.
"""

from .pool import AccountPool

__all__ = ["AccountPool"]
