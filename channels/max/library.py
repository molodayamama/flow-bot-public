"""Identity-aware MAX library/profile persistence over the shared metrics DB.

The MAX handler stays testable with a fake implementation while production uses
the same gallery, prompt-history, support and referral tables as Telegram. All
methods receive the already allocated negative internal identity; no platform
user id is written into legacy Telegram-keyed tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


@runtime_checkable
class MaxUserLibrary(Protocol):
    def record_generation(
        self,
        internal_user_id: int,
        *,
        prompt: str,
        image_urls: Sequence[str] = (),
    ) -> None: ...

    def gallery(self, internal_user_id: int, *, limit: int = 10) -> list[dict]: ...

    def prompts(self, internal_user_id: int, *, limit: int = 10) -> list[str]: ...

    def create_ticket(
        self, internal_user_id: int, *, username: str | None, text: str
    ) -> int: ...

    def tickets(self, internal_user_id: int) -> list[dict]: ...

    def referral_stats(self, internal_user_id: int) -> Mapping[str, int]: ...

    def bind_referrer(
        self, *, referrer_internal_id: int, referred_internal_id: int
    ) -> bool: ...


@dataclass(frozen=True)
class MetricsMaxUserLibrary:
    """Production adapter; imports ``metrics`` lazily for safe module import."""

    platform: str = "max"

    @staticmethod
    def _metrics():
        import metrics

        return metrics

    def record_generation(
        self,
        internal_user_id: int,
        *,
        prompt: str,
        image_urls: Sequence[str] = (),
    ) -> None:
        metrics = self._metrics()
        metrics.save_prompt_history(int(internal_user_id), prompt)
        for url in tuple(image_urls)[:20]:
            value = str(url or "").strip()
            if value.startswith("https://"):
                metrics.save_to_gallery(
                    int(internal_user_id), value, prompt=(prompt or "")[:400]
                )

    def gallery(self, internal_user_id: int, *, limit: int = 10) -> list[dict]:
        return self._metrics().get_gallery(int(internal_user_id), limit=limit)

    def prompts(self, internal_user_id: int, *, limit: int = 10) -> list[str]:
        return self._metrics().get_prompt_history(int(internal_user_id), limit=limit)

    def create_ticket(
        self, internal_user_id: int, *, username: str | None, text: str
    ) -> int:
        return int(
            self._metrics().create_ticket(
                int(internal_user_id), username=username, text=text
            )
            or 0
        )

    def tickets(self, internal_user_id: int) -> list[dict]:
        return self._metrics().get_user_tickets(int(internal_user_id))

    def referral_stats(self, internal_user_id: int) -> Mapping[str, int]:
        return self._metrics().referral_stats(int(internal_user_id))

    def bind_referrer(
        self, *, referrer_internal_id: int, referred_internal_id: int
    ) -> bool:
        referrer = int(referrer_internal_id)
        referred = int(referred_internal_id)
        if referrer >= 0 or referred >= 0 or referrer == referred:
            return False
        metrics = self._metrics()
        identity = metrics.get_identity_by_internal_id(referrer)
        if not identity or identity.get("platform") != self.platform:
            return False
        return bool(
            metrics.record_referral_join(
                referrer_user_id=referrer, referred_user_id=referred
            )
        )
