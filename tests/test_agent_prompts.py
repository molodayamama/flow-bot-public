"""Unit tests for product.agent_prompts instruction builders."""

from __future__ import annotations

import unittest

from product.agent_prompts import improve_instruction, edit_instruction


class InstructionTests(unittest.TestCase):
    def test_improve_embeds_prompt_and_asks_three_variants(self):
        out = improve_instruction("кот в шляпе")
        self.assertIn("кот в шляпе", out)
        self.assertIn("3 РАЗНЫХ варианта", out)
        self.assertIn("не запускай генерацию", out)

    def test_edit_embeds_prompt_and_frames_as_edit(self):
        out = edit_instruction("убери фон")
        self.assertIn("убери фон", out)
        self.assertIn("правк", out.lower())
        self.assertIn("без генерации", out)

    def test_empty_prompt_still_builds(self):
        self.assertTrue(improve_instruction("").endswith("Исходный промпт: "))
        self.assertTrue(edit_instruction("").endswith("Исходная правка: "))


if __name__ == "__main__":
    unittest.main()
