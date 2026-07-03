"""Unit tests for mediautil.image_ext_from_bytes — no flow_bot import."""

from __future__ import annotations

import unittest

from mediautil import image_ext_from_bytes


class ImageExtTests(unittest.TestCase):
    def test_png(self):
        self.assertEqual(image_ext_from_bytes(b"\x89PNG\r\n\x1a\nrest"), "png")

    def test_jpg(self):
        self.assertEqual(image_ext_from_bytes(b"\xff\xd8\xff\xe0rest"), "jpg")

    def test_webp(self):
        self.assertEqual(image_ext_from_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8"), "webp")

    def test_webp_requires_webp_marker(self):
        # RIFF without WEBP at [8:12] is not webp -> fallback
        self.assertEqual(image_ext_from_bytes(b"RIFF\x00\x00\x00\x00AVI rest"), "png")

    def test_unknown_uses_fallback(self):
        self.assertEqual(image_ext_from_bytes(b"garbage"), "png")
        self.assertEqual(image_ext_from_bytes(b"garbage", "jpg"), "jpg")

    def test_empty(self):
        self.assertEqual(image_ext_from_bytes(b"", "webp"), "webp")


if __name__ == "__main__":
    unittest.main()
