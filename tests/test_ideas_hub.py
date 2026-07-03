"""Unit tests for product.ideas_hub pure session-state helpers."""

from __future__ import annotations

import unittest

from product.ideas_hub import (
    ideas_clear,
    ideas_has_photo,
    ideas_prompt_with_extra,
    guided_image_fmt,
    guided_video_fmt,
)


class ClearTests(unittest.TestCase):
    def test_clears_template_and_guided_keys_but_keeps_photo(self):
        st = {"tp_step": 1, "gp_answers": {}, "ideas_mode": "root",
              "ideas_photo_file_id": "f", "other": 1}
        ideas_clear(st)
        self.assertNotIn("tp_step", st)
        self.assertNotIn("gp_answers", st)
        self.assertNotIn("ideas_mode", st)
        self.assertEqual(st["ideas_photo_file_id"], "f")  # photo kept
        self.assertEqual(st["other"], 1)

    def test_clear_photo_flag_drops_photo_keys(self):
        st = {"ideas_photo_file_id": "f", "ideas_extra_prompt": "x", "ideas_photo_caption": "c"}
        ideas_clear(st, clear_photo=True)
        self.assertEqual(st, {})


class PhotoTests(unittest.TestCase):
    def test_has_photo(self):
        self.assertTrue(ideas_has_photo({"ideas_photo_file_id": "f"}))
        self.assertFalse(ideas_has_photo({}))
        self.assertFalse(ideas_has_photo({"ideas_photo_file_id": ""}))


class PromptTests(unittest.TestCase):
    def test_no_extra_returns_prompt_unchanged(self):
        self.assertEqual(ideas_prompt_with_extra("a cat", {}), "a cat")

    def test_gp_extra_appended(self):
        out = ideas_prompt_with_extra("a cat", {"gp_extra_prompt": "make it blue"})
        self.assertIn("a cat", out)
        self.assertIn("make it blue", out)

    def test_ideas_extra_used_when_no_gp(self):
        out = ideas_prompt_with_extra("", {"ideas_extra_prompt": "note"})
        self.assertIn("high quality image", out)
        self.assertIn("note", out)


class FormatTests(unittest.TestCase):
    def test_image_fmt(self):
        self.assertEqual(guided_image_fmt({"format": "story"}), "port")
        self.assertEqual(guided_image_fmt({"format": "square"}), "sq")
        self.assertEqual(guided_image_fmt({"format": "avatar"}), "sq")
        self.assertEqual(guided_image_fmt({"format": "post"}), "land")
        self.assertEqual(guided_image_fmt({}), "land")

    def test_video_fmt(self):
        self.assertEqual(guided_video_fmt({"format": "story"}), "port")
        self.assertEqual(guided_video_fmt({"format": "avatar"}), "port")
        self.assertEqual(guided_video_fmt({"format": "wide"}), "land")
        self.assertEqual(guided_video_fmt({}), "land")


if __name__ == "__main__":
    unittest.main()
