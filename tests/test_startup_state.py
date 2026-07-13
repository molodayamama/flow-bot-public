from __future__ import annotations

import unittest

from channels.telegram.startup_state import (
    make_startup_state,
    set_startup_account,
    set_startup_phase,
)


class CountingClock:
    def __init__(self, value: float) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        return self.value


class StartupStateTests(unittest.TestCase):
    def test_factory_builds_initial_snapshot_from_explicit_inputs(self) -> None:
        state = make_startup_state(
            (account_id for account_id in ("main", "sub1")),
            total_accounts=7,
            min_ready=2,
        )

        self.assertEqual(
            state,
            {
                "phase": "init",
                "polling": False,
                "ready_accounts": 0,
                "total_accounts": 7,
                "min_ready": 2,
                "accounts": {
                    "main": {"status": "pending", "ready": False, "updated_at": None},
                    "sub1": {"status": "pending", "ready": False, "updated_at": None},
                },
            },
        )

    def test_factory_accepts_explicit_seller_policy(self) -> None:
        state = make_startup_state(("seller",), total_accounts=1, min_ready=0)

        self.assertEqual(state["total_accounts"], 1)
        self.assertEqual(state["min_ready"], 0)

    def test_factory_accepts_explicit_non_seller_policy(self) -> None:
        state = make_startup_state(("main", "sub1"), total_accounts=2, min_ready=2)

        self.assertEqual(state["total_accounts"], 2)
        self.assertEqual(state["min_ready"], 2)

    def test_set_phase_preserves_order_and_calls_clock_once(self) -> None:
        state = {"phase": "init"}
        clock = CountingClock(123.5)

        set_startup_phase(state, "warming", clock)

        self.assertEqual(state, {"phase": "warming", "updated_at": 123.5})
        self.assertEqual(clock.calls, 1)

    def test_set_phase_extra_can_overwrite_standard_fields(self) -> None:
        state: dict = {}
        clock = CountingClock(123.5)

        set_startup_phase(
            state,
            "warming",
            clock,
            phase="ready",
            updated_at=999.0,
            clock="state-value",
        )

        self.assertEqual(
            state,
            {"phase": "ready", "updated_at": 999.0, "clock": "state-value"},
        )
        self.assertEqual(clock.calls, 1)

    def test_set_account_adds_unknown_account_and_coerces_ready(self) -> None:
        state = {"accounts": {}, "ready_accounts": 0}
        clock = CountingClock(10.0)

        set_startup_account(state, "sub1", "ready", clock, ready="yes")

        self.assertEqual(
            state["accounts"]["sub1"],
            {"status": "ready", "ready": True, "updated_at": 10.0},
        )
        self.assertEqual(state["ready_accounts"], 1)
        self.assertEqual(clock.calls, 1)

    def test_set_account_updates_existing_account_and_error(self) -> None:
        state = {
            "accounts": {
                "main": {
                    "status": "pending",
                    "ready": False,
                    "updated_at": None,
                    "preserved": True,
                }
            }
        }
        clock = CountingClock(20.0)

        set_startup_account(state, "main", "failed", clock, error="TimeoutError")

        self.assertEqual(
            state["accounts"]["main"],
            {
                "status": "failed",
                "ready": False,
                "updated_at": 20.0,
                "preserved": True,
                "error": "TimeoutError",
            },
        )
        self.assertEqual(clock.calls, 1)

    def test_set_account_falsy_error_clears_previous_error(self) -> None:
        for error in (None, ""):
            with self.subTest(error=error):
                state = {
                    "accounts": {
                        "main": {
                            "status": "failed",
                            "ready": False,
                            "updated_at": 1.0,
                            "error": "old",
                        }
                    }
                }

                set_startup_account(state, "main", "running", lambda: 2.0, error=error)

                self.assertNotIn("error", state["accounts"]["main"])

    def test_set_account_recounts_all_accounts_globally(self) -> None:
        state = {
            "accounts": {
                "main": {"ready": True},
                "sub1": {"ready": 1},
                "sub2": {"ready": False},
            },
            "ready_accounts": 99,
        }

        set_startup_account(state, "sub2", "ready", lambda: 30.0, ready=True)

        self.assertEqual(state["ready_accounts"], 3)


if __name__ == "__main__":
    unittest.main()
