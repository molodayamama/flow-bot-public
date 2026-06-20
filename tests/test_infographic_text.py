"""Offline tests for the infographic text-layer POC (infographic_text.py).

These run without network/Telegram/Google. If Pillow is unavailable the whole
case is skipped (the module is designed to degrade gracefully in that case).
"""

import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import infographic_text  # noqa: E402

try:
    from PIL import Image  # noqa: WPS433

    _HAVE_PIL = True
except Exception:  # pragma: no cover
    _HAVE_PIL = False


def _solid_png(size=(600, 800), color=(200, 80, 60)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _decode_size(png_bytes: bytes):
    return Image.open(io.BytesIO(png_bytes)).size


_TITLE = "Тёплый плед из микрофибры"
_BULLETS = [
    "Размер 200×220 см, не электризуется",
    "Гипоаллергенный наполнитель",
    "Машинная стирка при 30°C",
]


@unittest.skipUnless(_HAVE_PIL, "Pillow not installed")
class RenderInfographicTest(unittest.TestCase):
    def test_basic_render_returns_png_of_expected_size(self) -> None:
        bg = _solid_png((600, 800))
        out = infographic_text.render_infographic(bg, _TITLE, _BULLETS)
        self.assertIsInstance(out, bytes)
        self.assertGreater(len(out), 0)
        self.assertTrue(out.startswith(b"\x89PNG\r\n\x1a\n"), "not a PNG")
        # No explicit size -> output matches background size.
        self.assertEqual(_decode_size(out), (600, 800))
        # And it actually changed the pixels (text layer was drawn).
        self.assertNotEqual(out, bg)

    def test_explicit_size_is_respected(self) -> None:
        bg = _solid_png((600, 800))
        out = infographic_text.render_infographic(
            bg, _TITLE, _BULLETS, size=(1080, 1440)
        )
        self.assertEqual(_decode_size(out), (1080, 1440))

    def test_all_layouts(self) -> None:
        bg = _solid_png((600, 800))
        for layout in ("left", "bottom", "top"):
            out = infographic_text.render_infographic(
                bg, _TITLE, _BULLETS, layout=layout
            )
            self.assertEqual(_decode_size(out), (600, 800), layout)

    def test_empty_bullets_does_not_raise(self) -> None:
        bg = _solid_png()
        out = infographic_text.render_infographic(bg, _TITLE, [])
        self.assertTrue(out.startswith(b"\x89PNG"))

    def test_empty_title_and_bullets(self) -> None:
        bg = _solid_png()
        out = infographic_text.render_infographic(bg, "", [])
        self.assertTrue(out.startswith(b"\x89PNG"))

    def test_very_long_title_is_handled(self) -> None:
        bg = _solid_png((600, 800))
        long_title = "Очень длинный заголовок товара " * 12
        out = infographic_text.render_infographic(bg, long_title, _BULLETS)
        self.assertEqual(_decode_size(out), (600, 800))

    def test_unbreakable_long_word_is_handled(self) -> None:
        bg = _solid_png((400, 500))
        word = "А" * 300  # single token wider than any panel
        out = infographic_text.render_infographic(bg, word, [word])
        self.assertEqual(_decode_size(out), (400, 500))

    def test_many_bullets_overflow_is_clamped(self) -> None:
        bg = _solid_png((600, 800))
        bullets = [f"Пункт номер {i} с описанием" for i in range(40)]
        out = infographic_text.render_infographic(bg, _TITLE, bullets)
        self.assertEqual(_decode_size(out), (600, 800))

    def test_malformed_accent_hex_falls_back(self) -> None:
        bg = _solid_png()
        out = infographic_text.render_infographic(
            bg, _TITLE, _BULLETS, accent_hex="not-a-color"
        )
        self.assertTrue(out.startswith(b"\x89PNG"))

    def test_unknown_layout_falls_back(self) -> None:
        bg = _solid_png((600, 800))
        out = infographic_text.render_infographic(
            bg, _TITLE, _BULLETS, layout="diagonal"
        )
        self.assertEqual(_decode_size(out), (600, 800))

    def test_odd_inputs_return_original_or_png(self) -> None:
        # Empty / non-bytes background -> returns input safely, never raises.
        self.assertEqual(infographic_text.render_infographic(b"", _TITLE, _BULLETS), b"")
        self.assertEqual(
            infographic_text.render_infographic(None, _TITLE, _BULLETS), b""
        )
        # Garbage (non-PNG) bytes: must not raise; falls back to a synthetic
        # canvas at the requested size.
        out = infographic_text.render_infographic(
            b"not an image", _TITLE, _BULLETS, size=(300, 400)
        )
        self.assertTrue(out.startswith(b"\x89PNG"))
        self.assertEqual(_decode_size(out), (300, 400))

    def test_none_bullets_does_not_raise(self) -> None:
        bg = _solid_png()
        out = infographic_text.render_infographic(bg, _TITLE, None)  # type: ignore[arg-type]
        self.assertTrue(out.startswith(b"\x89PNG"))

    def test_determinism(self) -> None:
        bg = _solid_png((600, 800))
        a = infographic_text.render_infographic(bg, _TITLE, _BULLETS)
        b = infographic_text.render_infographic(bg, _TITLE, _BULLETS)
        self.assertEqual(a, b)

    def test_feature_flag_default_off(self) -> None:
        self.assertFalse(infographic_text.INFOGRAPHIC_TEXT_LAYER_ENABLED)


class GracefulDegradationTest(unittest.TestCase):
    """These run regardless of Pillow availability."""

    def test_non_bytes_input_returns_empty_bytes(self) -> None:
        self.assertEqual(infographic_text.render_infographic(None, "t", []), b"")

    def test_font_resolution_does_not_raise(self) -> None:
        # Should return a tuple even on a host without the expected fonts.
        reg, bold = infographic_text._resolve_font_paths()
        self.assertTrue(reg is None or isinstance(reg, str))
        self.assertTrue(bold is None or isinstance(bold, str))


if __name__ == "__main__":
    unittest.main()
