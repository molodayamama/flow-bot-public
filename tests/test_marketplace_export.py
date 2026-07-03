"""Unit tests for marketplace export filename/caption pure helpers."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from product.marketplace import (
    marketplace_export_filename,
    marketplace_export_caption,
)


def _ref(**kw):
    base = {"platform": "wb", "aspect_ratio": "portrait_34", "source": {"mediaId": "123456789XYZ"}}
    base.update(kw)
    return SimpleNamespace(**base)


class FilenameTests(unittest.TestCase):
    def test_full_slug_and_media_suffix(self):
        name = marketplace_export_filename(_ref(), "png")
        self.assertEqual(name, "photozhab_wildberries_3x4_123456789XYZ.png")

    def test_unknown_platform_and_aspect_fall_back(self):
        name = marketplace_export_filename(_ref(platform="tiktok", aspect_ratio=""), "webp")
        self.assertEqual(name, "photozhab_marketplace_card_123456789XYZ.webp")

    def test_missing_source_uses_image_placeholder(self):
        name = marketplace_export_filename(_ref(source=None), "jpg")
        self.assertEqual(name, "photozhab_wildberries_3x4_image.jpg")

    def test_ext_is_caller_supplied(self):
        self.assertTrue(marketplace_export_filename(_ref(), "gif").endswith(".gif"))


class CaptionTests(unittest.TestCase):
    def test_known_platform_named(self):
        self.assertIn("Wildberries", marketplace_export_caption(_ref()))

    def test_unknown_platform_generic(self):
        self.assertIn("маркетплейса", marketplace_export_caption(_ref(platform="x")))


if __name__ == "__main__":
    unittest.main()
