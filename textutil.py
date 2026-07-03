"""Small language/text utilities (Phase 11 core split).

Pure, dependency-free helpers shared by any channel (Telegram now, MAX later).
"""

from __future__ import annotations


def _days_word(n: int) -> str:
    """Russian plural form for 'день/дня/дней'."""
    if 11 <= n % 100 <= 19:
        return "дней"
    rem = n % 10
    if rem == 1:
        return "день"
    if 2 <= rem <= 4:
        return "дня"
    return "дней"


def parse_ids(raw: str) -> set[int]:
    """Parse a set of Telegram IDs from a string (comma/semicolon separated)."""
    return {
        int(x) for x in (raw or "").replace(";", ",").split(",")
        if x.strip().isdigit()
    }


def _short_prompt(text: str, limit: int = 80) -> str:
    """Trim a prompt for captions/status lines, adding an ellipsis if cut.

    Avoids the abrupt «…рыгает мотая головой влево вп» cut — instead the user
    sees «…влево вп…» so it's clear the description continues.
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"
