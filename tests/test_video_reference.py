"""Unit tests for product.video_reference pure resolvers."""

from __future__ import annotations

import unittest

from product import video_reference as vr


class SourceFieldTests(unittest.TestCase):
    def test_account_and_project_id_extracted(self):
        src = {"_account_id": "acc1", "_project_id": "p1"}
        self.assertEqual(vr.source_account_id(src), "acc1")
        self.assertEqual(vr.source_project_id(src), "p1")

    def test_missing_or_bad_values_none(self):
        for bad in (None, {}, {"_account_id": ""}, {"_account_id": 5}, "nope"):
            self.assertIsNone(vr.source_account_id(bad))
            self.assertIsNone(vr.source_project_id(bad))


class SourcesListTests(unittest.TestCase):
    def test_ingredients_filters_non_dicts(self):
        st = {"ving_photos": [{"a": 1}, None, "x", {"b": 2}]}
        self.assertEqual(vr.video_reference_sources(st, "ingredients"), [{"a": 1}, {"b": 2}])

    def test_frames_start_end(self):
        st = {"vfrm_start": {"s": 1}, "vfrm_end": {"e": 2}}
        self.assertEqual(vr.video_reference_sources(st, "frames"), [{"s": 1}, {"e": 2}])

    def test_frames_start_only(self):
        st = {"vfrm_start": {"s": 1}, "vfrm_end": None}
        self.assertEqual(vr.video_reference_sources(st, "frames"), [{"s": 1}])

    def test_unknown_mode_empty(self):
        self.assertEqual(vr.video_reference_sources({"ving_photos": [{"a": 1}]}, "other"), [])


class AccountIdTests(unittest.TestCase):
    def test_single_usable_account_returned(self):
        st = {"ving_photos": [{"_account_id": "a"}, {"_account_id": "a"}]}
        self.assertEqual(
            vr.video_reference_account_id(st, "ingredients", is_reference_usable=lambda _: True),
            "a",
        )

    def test_single_but_unusable_returns_none(self):
        st = {"ving_photos": [{"_account_id": "a"}]}
        self.assertIsNone(
            vr.video_reference_account_id(st, "ingredients", is_reference_usable=lambda _: False)
        )

    def test_mixed_accounts_returns_none(self):
        st = {"ving_photos": [{"_account_id": "a"}, {"_account_id": "b"}]}
        called = []
        self.assertIsNone(
            vr.video_reference_account_id(
                st, "ingredients", is_reference_usable=lambda x: called.append(x) or True
            )
        )
        self.assertEqual(called, [])  # health check skipped when ambiguous


class ProjectIdTests(unittest.TestCase):
    def test_single_project_returned(self):
        st = {"vfrm_start": {"_project_id": "p"}, "vfrm_end": {"_project_id": "p"}}
        self.assertEqual(vr.video_reference_project_id(st, "frames"), "p")

    def test_mixed_projects_none(self):
        st = {"vfrm_start": {"_project_id": "p"}, "vfrm_end": {"_project_id": "q"}}
        self.assertIsNone(vr.video_reference_project_id(st, "frames"))


if __name__ == "__main__":
    unittest.main()
