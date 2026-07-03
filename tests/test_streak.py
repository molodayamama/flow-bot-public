"""Unit tests for product.streak.streak_note (injected update_streak)."""

from __future__ import annotations

import unittest

import flow_copy
from product.streak import streak_note


def _reader(current, is_new_day):
    def _update(_user_id):
        return (current, current, is_new_day)
    return _update


class StreakNoteTests(unittest.TestCase):
    def test_not_new_day_returns_none(self):
        self.assertIsNone(streak_note(1, update_streak=_reader(5, False)))

    def test_day_one_message(self):
        self.assertEqual(streak_note(1, update_streak=_reader(1, True)), flow_copy.msg("streak_day_1"))

    def test_milestones(self):
        for m in (3, 7, 14, 30):
            self.assertEqual(
                streak_note(1, update_streak=_reader(m, True)),
                flow_copy.msg(f"streak_milestone_{m}"),
            )

    def test_ongoing_non_milestone(self):
        note = streak_note(1, update_streak=_reader(5, True))
        self.assertEqual(note, flow_copy.msg("streak_ongoing", n=5, days=_days(5)))

    def test_reader_exception_is_swallowed(self):
        def _boom(_):
            raise RuntimeError("db down")
        self.assertIsNone(streak_note(1, update_streak=_boom))


def _days(n):
    from textutil import _days_word
    return _days_word(n)


if __name__ == "__main__":
    unittest.main()
