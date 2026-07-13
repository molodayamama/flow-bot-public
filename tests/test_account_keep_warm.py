"""Offline tests for request-driven Flow account keep-warm policy."""

from __future__ import annotations

import unittest

from accounts.keep_warm import AccountKeepWarmPolicy, KeepWarmConfig


class _Pool:
    def __init__(self) -> None:
        self.available = {"image-a", "image-b", "video-a"}
        self.video = {"video-a"}

    def is_available(self, account_id: str) -> bool:
        return account_id in self.available

    def is_video_capable(self, account_id: str) -> bool:
        return account_id in self.video


class _Keeper:
    def __init__(self) -> None:
        self.calls: list[tuple[float, str]] = []

    def keep_warm_for(self, seconds: float, role: str) -> None:
        self.calls.append((seconds, role))


class AccountKeepWarmPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 100.0
        self.pool = _Pool()
        self.keepers = {account_id: _Keeper() for account_id in self.pool.available}
        self.policy = AccountKeepWarmPolicy(
            account_pool=self.pool,
            keepers=self.keepers,
            config=KeepWarmConfig(image_accounts=1, video_accounts=1, hold_seconds=300),
            clock=lambda: self.now,
        )

    def test_note_pins_eligible_keeper(self) -> None:
        self.policy.note("video", "video-a")
        self.assertEqual(self.policy.active_targets(), {"image": (), "video": ("video-a",)})
        self.assertEqual(self.keepers["video-a"].calls, [(300.0, "video")])

    def test_role_limit_keeps_latest_target(self) -> None:
        self.policy.note("image", "image-a")
        self.now += 1
        self.policy.note("image", "image-b")
        self.assertEqual(self.policy.active_targets()["image"], ("image-b",))

    def test_expired_and_ineligible_targets_are_pruned(self) -> None:
        self.policy.note("image", "image-a")
        self.now += 301
        self.assertEqual(self.policy.active_targets()["image"], ())
        self.policy.note("video", "image-a")
        self.assertEqual(self.policy.active_targets()["video"], ())
