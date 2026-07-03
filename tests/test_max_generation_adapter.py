"""Unit tests for BackendGenerationService — no network, fake backend."""

from __future__ import annotations

import asyncio
import unittest

from channels.max.generation_adapter import BackendGenerationService
from channels.max.handler import GenerationService


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Deps:
    default_image_model = "nb2"


class AdapterTests(unittest.TestCase):
    def _service(self, backend):
        return BackendGenerationService(generate_images=backend, deps=_Deps())

    def test_satisfies_generation_service_protocol(self):
        svc = self._service(lambda deps, req: None)
        self.assertIsInstance(svc, GenerationService)

    def test_create_image_maps_request_and_returns_result(self):
        seen = {}

        async def backend(deps, req):
            seen["deps"] = deps
            seen["req"] = req
            return {"images": [{"url": "http://u/i.png"}]}

        svc = self._service(backend)
        out = run(svc.create_image(internal_user_id=42, prompt="a cat"))
        self.assertEqual(out, {"images": [{"url": "http://u/i.png"}]})
        self.assertEqual(seen["req"]["prompt"], "a cat")
        self.assertEqual(seen["req"]["user_id"], 42)
        self.assertEqual(seen["req"]["num_images"], 1)
        self.assertEqual(seen["req"]["aspect_ratio"], "portrait")
        self.assertIs(seen["deps"], svc._deps)

    def test_create_image_propagates_backend_error(self):
        async def backend(deps, req):
            return {"error": "accounts_unavailable"}

        out = run(self._service(backend).create_image(internal_user_id=1, prompt="x"))
        self.assertEqual(out, {"error": "accounts_unavailable"})

    def test_custom_default_aspect(self):
        captured = {}

        async def backend(deps, req):
            captured["aspect"] = req["aspect_ratio"]
            return {"images": []}

        svc = BackendGenerationService(
            generate_images=backend, deps=_Deps(), default_aspect="landscape"
        )
        run(svc.create_image(internal_user_id=1, prompt="x"))
        self.assertEqual(captured["aspect"], "landscape")

    def test_edit_photo_without_bridge_returns_graceful_error(self):
        out = run(self._service(lambda d, r: None).edit_photo(
            internal_user_id=1, prompt="x", photo_file_id="http://u/p.png"
        ))
        self.assertIn("error", out)
        self.assertTrue(out["error"])

    def test_animate_photo_without_bridge_returns_graceful_error(self):
        out = run(self._service(lambda d, r: None).animate_photo(
            internal_user_id=1, prompt="x", photo_file_id="http://u/p.png"
        ))
        self.assertIn("error", out)


class PhotoBridgeTests(unittest.TestCase):
    def _bridged(self, *, i2i=None, video=None, downloaded=b"IMG"):
        seen = {}

        async def _dl(url):
            seen["url"] = url
            return downloaded

        async def _i2i(deps, req):
            seen["i2i_req"] = req
            return {"images": [{"url": "http://u/e.png"}]}

        async def _video(deps, req):
            seen["video_req"] = req
            return {"videos": [{"url": "http://u/v.mp4"}]}

        svc = BackendGenerationService(
            generate_images=lambda d, r: None,
            deps=_Deps(),
            generate_i2i=i2i or _i2i,
            generate_video_ingredients=video or _video,
            download_bytes=_dl,
        )
        return svc, seen

    def test_edit_photo_downloads_and_calls_i2i_with_b64(self):
        import base64
        svc, seen = self._bridged(downloaded=b"RAWBYTES")
        out = run(svc.edit_photo(
            internal_user_id=9, prompt="brighten", photo_file_id="https://cdn/p.png"
        ))
        self.assertEqual(out, {"images": [{"url": "http://u/e.png"}]})
        self.assertEqual(seen["url"], "https://cdn/p.png")
        self.assertEqual(seen["i2i_req"]["image_b64"], base64.b64encode(b"RAWBYTES").decode())
        self.assertEqual(seen["i2i_req"]["prompt"], "brighten")
        self.assertEqual(seen["i2i_req"]["user_id"], 9)

    def test_animate_photo_downloads_and_calls_video(self):
        svc, seen = self._bridged()
        out = run(svc.animate_photo(
            internal_user_id=3, prompt="spin", photo_file_id="https://cdn/p.png"
        ))
        self.assertEqual(out, {"videos": [{"url": "http://u/v.mp4"}]})
        self.assertIn("image_b64", seen["video_req"])

    def test_non_url_photo_ref_is_graceful_error(self):
        # a token/file-id ref (not http) can't be downloaded here
        svc, seen = self._bridged()
        out = run(svc.edit_photo(internal_user_id=1, prompt="x", photo_file_id="token123"))
        self.assertIn("error", out)
        self.assertNotIn("url", seen)  # download never attempted

    def test_empty_download_is_graceful_error(self):
        svc, seen = self._bridged(downloaded=b"")
        out = run(svc.edit_photo(internal_user_id=1, prompt="x", photo_file_id="https://cdn/p.png"))
        self.assertIn("error", out)


if __name__ == "__main__":
    unittest.main()
