"""Health helpers for the runtime account pool.

The first extraction keeps health behavior on ``AccountPool`` itself for
backward compatibility; this module gives later phases a stable import target.
"""

from .pool import AccountPool

__all__ = ["AccountPool"]
