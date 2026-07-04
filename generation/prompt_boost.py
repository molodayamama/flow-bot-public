"""Gemini prompt-improvement helper.

The helper keeps the external HTTP shape outside ``flow_bot`` while taking the
API key, logger, and session factory from the composition root.
"""

from __future__ import annotations

from typing import Any, Callable

import aiohttp


_GEMINI_MODEL_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent"
)


async def boost_prompt_with_gemini(
    prompt: str,
    *,
    api_key: str,
    log: Any,
    session_factory: Callable[..., Any] = aiohttp.ClientSession,
    timeout_factory: Callable[..., Any] = aiohttp.ClientTimeout,
) -> str | None:
    """Expand a short image prompt with Gemini Flash, returning text or None."""
    if not api_key:
        return None
    system = (
        "You improve image generation prompts. The user gave a short prompt; you expand it "
        "with lighting, composition, style, mood, and artistic detail. "
        "Reply ONLY with the improved prompt. No quotes, no explanation, no intro. "
        "Keep the same language as the input. Length: 1-3 sentences max."
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": system}]},
        "generationConfig": {"maxOutputTokens": 200, "temperature": 0.7},
    }
    url = f"{_GEMINI_MODEL_URL}?key={api_key}"
    try:
        async with session_factory(timeout=timeout_factory(total=20)) as sess:
            async with sess.post(url, json=payload) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return (
                    data["candidates"][0]["content"]["parts"][0]["text"].strip()
                ) or None
    except Exception as exc:
        log.warning("Gemini prompt boost failed: %s", exc)
        return None
