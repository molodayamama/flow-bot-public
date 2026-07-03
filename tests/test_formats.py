"""Unit tests for the platform-neutral format<->aspect mappers in flow_core.

These moved out of flow_bot.py (Phase 11 core split) so any channel (Telegram
now, MAX later) can convert UI format codes without importing the bot.
"""

from __future__ import annotations

import unittest

from flow_core import fmt_to_aspect, aspect_to_fmt, aspect_to_vfmt


class FmtToAspectTests(unittest.TestCase):
    def test_known_codes(self):
        self.assertEqual(fmt_to_aspect("land"), "landscape")
        self.assertEqual(fmt_to_aspect("port"), "portrait")
        self.assertEqual(fmt_to_aspect("sq"), "square")
        self.assertEqual(fmt_to_aspect("f43"), "landscape_43")
        self.assertEqual(fmt_to_aspect("f34"), "portrait_34")

    def test_unknown_defaults_landscape(self):
        self.assertEqual(fmt_to_aspect("bogus"), "landscape")
        self.assertEqual(fmt_to_aspect(""), "landscape")


class AspectToFmtTests(unittest.TestCase):
    def test_known_aspects(self):
        self.assertEqual(aspect_to_fmt("landscape"), "land")
        self.assertEqual(aspect_to_fmt("portrait"), "port")
        self.assertEqual(aspect_to_fmt("square"), "sq")
        self.assertEqual(aspect_to_fmt("landscape_43"), "f43")
        self.assertEqual(aspect_to_fmt("portrait_34"), "f34")

    def test_unknown_defaults_land(self):
        self.assertEqual(aspect_to_fmt("bogus"), "land")

    def test_round_trip(self):
        for fmt in ("land", "port", "sq", "f43", "f34"):
            self.assertEqual(aspect_to_fmt(fmt_to_aspect(fmt)), fmt)


class AspectToVfmtTests(unittest.TestCase):
    def test_video_supported(self):
        self.assertEqual(aspect_to_vfmt("landscape"), "land")
        self.assertEqual(aspect_to_vfmt("portrait"), "port")

    def test_video_unsupported_defaults_land(self):
        # square/43/34 have no video format -> land
        self.assertEqual(aspect_to_vfmt("square"), "land")
        self.assertEqual(aspect_to_vfmt("landscape_43"), "land")
        self.assertEqual(aspect_to_vfmt("bogus"), "land")


if __name__ == "__main__":
    unittest.main()
