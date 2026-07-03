"""Unit tests for product.ideas_hub pure session-state helpers."""

from __future__ import annotations

import unittest

from product.ideas_hub import (
    ideas_clear,
    ideas_has_photo,
    ideas_prompt_with_extra,
    guided_image_fmt,
    guided_video_fmt,
    tp_store_answer,
)


_QUESTIONS = {"tpl1": [{"key": "q0"}, {"key": "q1"}]}


def _tq(tid):
    return _QUESTIONS.get(tid, [])


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


class TpStoreAnswerTests(unittest.TestCase):
    def test_stores_answer_by_question_key_and_advances(self):
        st = {"tp_tpl": "tpl1", "tp_step": 0, "tp_await": "q0"}
        tp_store_answer(st, "hello", template_questions=_tq)
        self.assertEqual(st["tp_answers"], {"q0": "hello"})
        self.assertEqual(st["tp_step"], 1)
        self.assertIsNone(st["tp_await"])

    def test_step_beyond_questions_only_advances(self):
        st = {"tp_tpl": "tpl1", "tp_step": 5}
        tp_store_answer(st, "x", template_questions=_tq)
        self.assertNotIn("tp_answers", st)
        self.assertEqual(st["tp_step"], 6)

    def test_no_template_id_advances_without_storing(self):
        st = {"tp_step": 0}
        tp_store_answer(st, "x", template_questions=_tq)
        self.assertNotIn("tp_answers", st)
        self.assertEqual(st["tp_step"], 1)


if __name__ == "__main__":
    unittest.main()
