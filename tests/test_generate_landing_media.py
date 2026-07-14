"""Offline safety contracts for the operator-only landing media generator."""
from __future__ import annotations

import base64
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "generate_landing_media.py"
SPEC = importlib.util.spec_from_file_location("generate_landing_media", MODULE_PATH)
assert SPEC and SPEC.loader
media_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(media_tool)


class GenerateLandingMediaTests(unittest.TestCase):
    def test_only_known_google_media_hosts_are_allowed(self) -> None:
        for url in (
            "https://flow-content.google/image/example",
            "https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=x",
            "https://storage.googleapis.com/example/image.png",
            "https://lh3.googleusercontent.com/example",
        ):
            self.assertTrue(media_tool._is_allowed_media_url(url), url)
        for url in (
            "http://flow-content.google/image/example",
            "https://flow-content.google.evil.test/image/example",
            "http://REDACTED:REDACTED@proxy.example.invalid:8080/example",
            "https://example.test/image.png",
        ):
            self.assertFalse(media_tool._is_allowed_media_url(url), url)

    def test_media_magic_validation_is_fail_closed(self) -> None:
        self.assertEqual(media_tool._image_extension(b"\x89PNG\r\n\x1a\nrest"), ".png")
        self.assertEqual(media_tool._image_extension(b"\xff\xd8\xffrest"), ".jpg")
        self.assertEqual(media_tool._image_extension(b"RIFF1234WEBPrest"), ".webp")
        with self.assertRaises(ValueError):
            media_tool._image_extension(b"not an image")

        fake_mp4 = b"\x00\x00\x00\x18ftypisom" + b"0" * 32
        encoded = base64.b64encode(fake_mp4).decode("ascii")
        self.assertEqual(media_tool._decode_video({"videos": [{"video_b64": encoded}]}), fake_mp4)
        with self.assertRaises(RuntimeError):
            media_tool._decode_video({"videos": [{"video_b64": "invalid!"}]})

    def test_source_requires_explicit_paid_action_and_localhost(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("--approve-external-action", source)
        self.assertIn('parsed.hostname not in {"127.0.0.1", "localhost"}', source)
        self.assertIn("_read_limited", source)
        self.assertNotIn("account_id", source)
        self.assertNotIn("workflow_id", source)
        self.assertNotIn("project_id", source)


if __name__ == "__main__":
    unittest.main()
