from __future__ import annotations

import ast
import asyncio
import unittest
from pathlib import Path

from generation import backend_service
from generation.contracts import (
    GenerateEditRequest,
    GenerateImageRequest,
    GenerateResult,
    GenerateVideoRequest,
    result_from_backend,
)
from generation.edit_service import EditService
from generation.image_service import ImageService
from generation.video_service import VideoService


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class RecordingBackend:
    """Fake backend callable: records the (deps, req) it was given, returns canned."""

    def __init__(self, raw):
        self.raw = raw
        self.calls: list[tuple] = []

    async def __call__(self, deps, req):
        self.calls.append((deps, req))
        return self.raw


DEPS = object()  # opaque; the fake backend never inspects it


class ContractMappingTests(unittest.TestCase):
    def test_image_request_maps_and_omits_none_model(self) -> None:
        req = GenerateImageRequest(user_id=7, prompt="cat", num_images=3, aspect_ratio="landscape")
        self.assertEqual(
            req.as_backend_dict(),
            {"prompt": "cat", "num_images": 3, "aspect_ratio": "landscape", "user_id": 7},
        )

    def test_image_request_includes_model_when_set(self) -> None:
        req = GenerateImageRequest(user_id=1, prompt="x", image_model="GEM_PIX_2")
        self.assertEqual(req.as_backend_dict()["image_model"], "GEM_PIX_2")

    def test_edit_request_carries_image_b64(self) -> None:
        req = GenerateEditRequest(user_id=1, prompt="brighten", image_b64="QUJD")
        self.assertEqual(req.as_backend_dict()["image_b64"], "QUJD")

    def test_video_request_shape(self) -> None:
        req = GenerateVideoRequest(user_id=1, prompt="wave", image_b64="QUJD", video_model="omni")
        d = req.as_backend_dict()
        self.assertEqual(d["image_b64"], "QUJD")
        self.assertEqual(d["video_model"], "omni")
        self.assertNotIn("num_images", d)

    def test_result_from_backend_success(self) -> None:
        res = result_from_backend(
            {"images": [{"url": "u1", "img": "b1"}, {"url": "u2"}], "account_id": "a1", "project_id": "p1"}
        )
        self.assertTrue(res.ok)
        self.assertEqual(res.media_count, 2)
        self.assertEqual(res.images[0].url, "u1")
        self.assertEqual(res.account_id, "a1")
        self.assertIsNone(res.error)

    def test_result_from_backend_skips_urlless_images(self) -> None:
        res = result_from_backend({"images": [{"img": "b"}, {"url": "ok"}]})
        self.assertEqual(res.media_count, 1)

    def test_result_from_backend_error_retryable(self) -> None:
        res = result_from_backend({"error": "accounts_unavailable"})
        self.assertFalse(res.ok)
        self.assertTrue(res.retry_possible)

    def test_result_from_backend_error_terminal(self) -> None:
        res = result_from_backend({"error": "prompt rejected"})
        self.assertFalse(res.ok)
        self.assertFalse(res.retry_possible)

    def test_result_from_backend_none(self) -> None:
        res = result_from_backend(None)
        self.assertFalse(res.ok)
        self.assertEqual(res.error, "no_result")

    def test_as_backend_dict_round_trips(self) -> None:
        raw = {"images": [{"url": "u", "img": "b"}], "account_id": "a", "project_id": "p"}
        self.assertEqual(result_from_backend(raw).as_backend_dict(), raw)

    def test_video_result_round_trips(self) -> None:
        raw = {
            "videos": [{
                "video_b64": "AAA", "media_id": "m", "model_id": "omni",
                "aspect_ratio": "portrait", "workflow_id": "w", "scene_id": "s",
            }],
            "account_id": "a", "project_id": "p",
        }
        self.assertEqual(result_from_backend(raw).as_backend_dict(), raw)


class ServiceTests(unittest.TestCase):
    def test_image_service_calls_backend_with_mapped_request(self) -> None:
        backend = RecordingBackend({"images": [{"url": "u1"}], "account_id": "a1", "project_id": "p1"})
        svc = ImageService(DEPS, backend=backend)

        res = run(svc.generate(GenerateImageRequest(user_id=42, prompt="dog", num_images=2)))

        self.assertEqual(len(backend.calls), 1)
        deps_used, req_sent = backend.calls[0]
        self.assertIs(deps_used, DEPS)
        self.assertEqual(req_sent["user_id"], 42)
        self.assertEqual(req_sent["num_images"], 2)
        self.assertTrue(res.ok)
        self.assertEqual(res.images[0].url, "u1")
        self.assertEqual(res.account_id, "a1")

    def test_edit_service_passes_image_and_reports_error(self) -> None:
        backend = RecordingBackend({"error": "generation failed"})
        svc = EditService(DEPS, backend=backend)

        res = run(svc.generate(GenerateEditRequest(user_id=1, prompt="fix", image_b64="QUJD")))

        self.assertEqual(backend.calls[0][1]["image_b64"], "QUJD")
        self.assertFalse(res.ok)
        self.assertTrue(res.retry_possible)

    def test_video_service_parses_videos(self) -> None:
        backend = RecordingBackend({"videos": [{"video_b64": "AAA", "media_id": "m"}], "account_id": "a"})
        svc = VideoService(DEPS, backend=backend)

        res = run(svc.generate(GenerateVideoRequest(user_id=1, prompt="wave", image_b64="QUJD")))

        self.assertTrue(res.ok)
        self.assertEqual(res.videos[0].video_b64, "AAA")
        self.assertEqual(res.videos[0].media_id, "m")

    def test_video_animate_is_generate_alias(self) -> None:
        backend = RecordingBackend({"videos": [{"video_b64": "AAA"}]})
        svc = VideoService(DEPS, backend=backend)

        res = run(svc.animate(GenerateVideoRequest(user_id=1, prompt="p", image_b64="QUJD")))
        self.assertTrue(res.ok)

    def test_default_backends_reuse_backend_service(self) -> None:
        # Services must reuse the existing core, not reimplement it.
        self.assertIs(ImageService(DEPS)._backend, backend_service.generate_images)
        self.assertIs(EditService(DEPS)._backend, backend_service.generate_i2i)
        self.assertIs(VideoService(DEPS)._backend, backend_service.generate_video_ingredients)


class PurityTests(unittest.TestCase):
    def test_generation_modules_are_channel_free(self) -> None:
        for name in ("contracts.py", "image_service.py", "edit_service.py", "video_service.py"):
            src = (PROJECT_ROOT / "generation" / name).read_text(encoding="utf-8")
            tree = ast.parse(src)
            for node in ast.walk(tree):
                mods = set()
                if isinstance(node, ast.Import):
                    mods = {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    mods = {(node.module or "").split(".")[0]}
                self.assertNotIn("aiogram", mods, name)
                self.assertNotIn("flow_bot", mods, name)


if __name__ == "__main__":
    unittest.main()
