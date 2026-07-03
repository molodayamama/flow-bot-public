"""Prompt-improve agent instruction builders (Phase 11 core split).

Pure wrappers that frame a user's draft prompt / edit instruction for the
prompt-improve agent, which returns three ready-to-use variants (text only, no
generation). Channel-neutral, no local imports.

Purpose: keep the exact agent-facing wording versioned in one place per the
PromptOps rule instead of inline in the Telegram adapter.
"""

from __future__ import annotations


def improve_instruction(prompt: str) -> str:
    """Frame a draft prompt so the agent returns 3 distinct, ready-to-use prompt
    variants (different style/mood/detail); text only, no generation."""
    return (
        "Ты — помощник по промптам для генерации видео/изображений. "
        "Улучши промпт ниже и предложи 3 РАЗНЫХ варианта на выбор (разные стиль/"
        "настроение/детали), каждый — законченный готовый промпт. Только текст "
        "вариантов, не запускай генерацию.\n\nИсходный промпт: " + prompt
    )


def edit_instruction(prompt: str) -> str:
    """Frame a photo-edit instruction so the agent returns 3 clearer edit
    instructions (what to change on the photo), not generative scene prompts."""
    return (
        "Ты — помощник по правкам фото. Улучши и уточни инструкцию правки ниже и "
        "предложи 3 варианта (разной детализации/акцента), каждый — законченная "
        "инструкция, ЧТО изменить на фото. Только текст вариантов, без генерации."
        "\n\nИсходная правка: " + prompt
    )
