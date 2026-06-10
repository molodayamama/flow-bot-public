"""Offline unit tests for ``prompts_lib`` (stdlib only, no bot/browser).

Run:
    python -m unittest discover -s tests -p "test_prompts_lib.py"
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import prompts_lib  # noqa: E402


EXPECTED_IDS = [
    "pet_photo_animation",
    "marketplace_white_bg",
    "product_card",
    "ad_banner",
    "ugc_creative",
    "tg_post_cover",
    "story",
    "brand_avatar",
    "product_on_bg",
]


class TemplateCatalogTests(unittest.TestCase):
    def test_template_ids_order_and_count(self) -> None:
        self.assertEqual(prompts_lib.template_ids(), EXPECTED_IDS)
        self.assertEqual(len(prompts_lib.template_ids()), 9)

    def test_get_template_shape(self) -> None:
        for tid in EXPECTED_IDS:
            t = prompts_lib.get_template(tid)
            self.assertIsNotNone(t, tid)
            self.assertEqual(set(t.keys()), {"id", "title", "questions"})
            self.assertEqual(t["id"], tid)
            self.assertTrue(t["title"])
            self.assertIsInstance(t["questions"], list)
            self.assertTrue(t["questions"])
            # No skeleton/body text leaks out of the public descriptor.
            self.assertNotIn("skeleton", t)
            self.assertNotIn("body", t)

    def test_get_template_unknown(self) -> None:
        self.assertIsNone(prompts_lib.get_template("nope"))
        self.assertEqual(prompts_lib.template_questions("nope"), [])

    def test_question_spec_shape(self) -> None:
        for tid in EXPECTED_IDS:
            for q in prompts_lib.template_questions(tid):
                self.assertEqual(
                    set(q.keys()),
                    {"key", "text", "type", "options", "optional"},
                    f"{tid}:{q.get('key')}",
                )
                self.assertIsInstance(q["key"], str)
                self.assertTrue(q["key"])
                self.assertIsInstance(q["text"], str)
                self.assertTrue(q["text"])
                self.assertIn(q["type"], ("choice", "text"))
                self.assertIsInstance(q["optional"], bool)
                if q["type"] == "choice":
                    self.assertTrue(q["options"], f"choice must have options: {tid}:{q['key']}")
                    for o in q["options"]:
                        self.assertEqual(set(o.keys()), {"value", "label"})
                        self.assertTrue(o["value"])
                        self.assertTrue(o["label"])
                else:  # text
                    self.assertEqual(q["options"], [])

    def test_returned_specs_are_copies(self) -> None:
        a = prompts_lib.template_questions("product_card")
        a[0]["text"] = "MUTATED"
        a[0]["options"].append({"value": "x", "label": "y"})
        b = prompts_lib.template_questions("product_card")
        self.assertNotEqual(b[0]["text"], "MUTATED")


class TemplateCompositionTests(unittest.TestCase):
    def test_product_card_full(self) -> None:
        out = prompts_lib.compose_template_prompt(
            "product_card",
            {
                "product": "керамическая кружка",
                "background": "white",
                "audience": "office workers",
                "need_text": "no",
            },
        )
        self.assertIn("керамическая кружка", out)
        self.assertIn("pure white seamless background", out)
        self.assertIn("office workers", out)
        self.assertNotIn("{", out)
        self.assertNotIn("}", out)
        self.assertFalse(out.lstrip().startswith("#"))

    def test_product_card_omits_blank_optional(self) -> None:
        out = prompts_lib.compose_template_prompt(
            "product_card",
            {
                "product": "кожаный кошелёк",
                "background": "luxury",
                "audience": "",  # skipped
                "need_text": "yes",
            },
        )
        self.assertIn("кожаный кошелёк", out)
        self.assertIn("premium luxury setting", out)
        # The optional audience phrasing must be absent when blank.
        self.assertNotIn("aimed at", out)
        # Still a clean prompt, no leftover separators from the dropped slot.
        self.assertNotIn(", ,", out)
        self.assertNotIn("  ", out)
        self.assertNotIn("{", out)

    def test_no_header_lines_in_output(self) -> None:
        out = prompts_lib.compose_template_prompt(
            "product_card",
            {"product": "mug", "background": "white", "need_text": "no"},
        )
        for line in out.splitlines():
            self.assertFalse(line.lstrip().startswith("#"), line)
        # Header-only tokens from the skeleton must not appear.
        self.assertNotIn("purpose:", out)
        self.assertNotIn("example_dialog", out)

    def test_unknown_template_returns_empty(self) -> None:
        self.assertEqual(prompts_lib.compose_template_prompt("does_not_exist", {}), "")

    def test_missing_answers_do_not_raise(self) -> None:
        # No answers at all.
        out = prompts_lib.compose_template_prompt("product_card", {})
        self.assertIsInstance(out, str)
        self.assertTrue(out)
        self.assertNotIn("{", out)

    def test_bad_answers_type_does_not_raise(self) -> None:
        for bad in (None, [], "string", 42):
            out = prompts_lib.compose_template_prompt("ad_banner", bad)  # type: ignore[arg-type]
            self.assertIsInstance(out, str)
            self.assertTrue(out)

    def test_choice_value_mapped_to_phrase_not_raw_value(self) -> None:
        out = prompts_lib.compose_template_prompt(
            "product_on_bg",
            {"product": "флакон духов", "background": "marble"},
        )
        self.assertIn("elegant marble surface", out)
        # Raw enum value should not appear verbatim.
        self.assertNotIn("marble,", out.replace("marble surface", ""))

    def test_unknown_choice_value_falls_back_gracefully(self) -> None:
        out = prompts_lib.compose_template_prompt(
            "product_card",
            {"product": "mug", "background": "weird_value", "need_text": "no"},
        )
        self.assertIsInstance(out, str)
        self.assertTrue(out)
        self.assertNotIn("{", out)

    def test_pet_photo_animation_prompt(self) -> None:
        out = prompts_lib.compose_template_prompt(
            "pet_photo_animation",
            {
                "pet": "рыжий кот",
                "emotion": "cute",
                "motion": "head_tilt",
                "detail": "зелёные глаза и полоски на шерсти",
            },
        )
        self.assertIn("Photo-to-video prompt", out)
        self.assertIn("рыжий кот", out)
        self.assertIn("slowly tilts the head", out)
        self.assertIn("heartwarming cute emotion", out)
        self.assertIn("зелёные глаза", out)
        self.assertNotIn("{", out)

    def test_marketplace_white_bg_prompt(self) -> None:
        out = prompts_lib.compose_template_prompt(
            "marketplace_white_bg",
            {
                "product": "женские кроссовки",
                "platform": "wb",
                "angle": "threequarter",
                "detail": "форму подошвы и логотип",
            },
        )
        self.assertIn("Marketplace white-background product card", out)
        self.assertIn("женские кроссовки", out)
        self.assertIn("Wildberries", out)
        self.assertIn("three-quarter product view", out)
        self.assertIn("#FFFFFF", out)
        self.assertNotIn("{", out)

    def test_all_templates_compose_without_braces(self) -> None:
        for tid in EXPECTED_IDS:
            qs = prompts_lib.template_questions(tid)
            answers = {}
            for q in qs:
                if q["type"] == "choice":
                    answers[q["key"]] = q["options"][0]["value"]
                else:
                    answers[q["key"]] = f"sample {q['key']}"
            out = prompts_lib.compose_template_prompt(tid, answers)
            self.assertTrue(out, tid)
            self.assertNotIn("{", out, tid)
            self.assertNotIn("}", out, tid)
            self.assertFalse(out.lstrip().startswith("#"), tid)


class GuidedPickerTests(unittest.TestCase):
    def test_guided_steps_shape(self) -> None:
        steps = prompts_lib.guided_steps()
        self.assertEqual(len(steps), 3)
        self.assertEqual([s["key"] for s in steps], ["what", "style", "format"])
        for s in steps:
            self.assertEqual(s["type"], "choice")
            self.assertTrue(s["options"])
            for o in s["options"]:
                self.assertEqual(set(o.keys()), {"value", "label"})

    def test_compose_guided_maps_values_to_phrases(self) -> None:
        out = prompts_lib.compose_guided_prompt(
            {"what": "image", "style": "realism", "format": "square"}
        )
        self.assertIn("A striking image", out)
        self.assertIn("photorealistic", out)
        self.assertIn("square 1:1 composition", out)
        self.assertNotIn("{", out)
        self.assertNotIn("}", out)

    def test_compose_guided_video_branch(self) -> None:
        out = prompts_lib.compose_guided_prompt(
            {"what": "video", "style": "cinematic", "format": "banner"}
        )
        self.assertIsInstance(out, str)
        self.assertTrue(out)
        self.assertNotIn("{", out)

    def test_compose_guided_missing_and_bad_input(self) -> None:
        self.assertTrue(prompts_lib.compose_guided_prompt({}))
        for bad in (None, [], "x", 7):
            out = prompts_lib.compose_guided_prompt(bad)  # type: ignore[arg-type]
            self.assertIsInstance(out, str)
            self.assertTrue(out)


class SkeletonHeaderStrippingTests(unittest.TestCase):
    def test_header_block_stripped(self) -> None:
        # The product_card skeleton file carries a "#" header comment block.
        out = prompts_lib.compose_template_prompt(
            "product_card",
            {"product": "mug", "background": "white", "need_text": "no"},
        )
        # Confirm tokens that only live in the header are gone.
        self.assertNotIn("#", out)
        self.assertNotIn("inputs:", out)
        self.assertNotIn("result:", out)

    def test_skeleton_files_exist(self) -> None:
        for tid in EXPECTED_IDS:
            p = prompts_lib.PROMPTS_DIR / "templates" / f"{tid}.txt"
            self.assertTrue(p.is_file(), str(p))
        self.assertTrue((prompts_lib.PROMPTS_DIR / "guided" / "skeleton.txt").is_file())


if __name__ == "__main__":
    unittest.main()
