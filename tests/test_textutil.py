"""Unit tests for textutil pure helpers."""

from __future__ import annotations

import unittest

from textutil import parse_ids, _days_word, _short_prompt


class ParseIdsTests(unittest.TestCase):
    def test_comma_and_semicolon_separators(self):
        self.assertEqual(parse_ids("111, 222; 333 ,bad"), {111, 222, 333})

    def test_empty_and_none(self):
        self.assertEqual(parse_ids(""), set())
        self.assertEqual(parse_ids(None), set())

    def test_non_digit_ignored(self):
        self.assertEqual(parse_ids("abc, 12x, 7"), {7})

    def test_dedups(self):
        self.assertEqual(parse_ids("5,5,5"), {5})


class DaysWordTests(unittest.TestCase):
    def test_plural_forms(self):
        self.assertEqual(_days_word(1), "день")
        self.assertEqual(_days_word(2), "дня")
        self.assertEqual(_days_word(5), "дней")
        self.assertEqual(_days_word(11), "дней")


class ShortPromptTests(unittest.TestCase):
    def test_short_unchanged(self):
        self.assertEqual(_short_prompt("hi", 80), "hi")

    def test_long_trimmed_with_ellipsis(self):
        out = _short_prompt("x" * 100, 10)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(len(out), 11)


if __name__ == "__main__":
    unittest.main()
