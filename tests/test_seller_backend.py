from __future__ import annotations

import asyncio
import os
import unittest

from aiohttp import web
from aiohttp.test_utils import TestServer

import seller_backend as sb


class TokenAndSchemaTests(unittest.TestCase):
    def test_token_ok(self) -> None:
        self.assertTrue(sb._token_ok("abc", "abc"))
        self.assertFalse(sb._token_ok("abc", "xyz"))
        self.assertFalse(sb._token_ok("", ""))          # empty expected = disabled
        self.assertFalse(sb._token_ok("abc", ""))

    def test_build_request_schema(self) -> None:
        req = sb.build_request(prompt="кот", num_images="3", aspect_ratio="portrait",
                               image_model="nb2", user_id="7")
        self.assertEqual(req["kind"], "image")
        self.assertEqual(req["num_images"], 3)
        self.assertEqual(req["user_id"], 7)
        self.assertEqual(req["aspect_ratio"], "portrait")

    def test_build_request_i2i_carries_image(self) -> None:
        req = sb.build_request(prompt="карточка", num_images=5, aspect_ratio="portrait",
                               image_model="nb2", user_id=1, kind="i2i", image_b64="QUJD")
        self.assertEqual(req["kind"], "i2i")
        self.assertEqual(req["image_b64"], "QUJD")
        # text image request omits the image field entirely
        img = sb.build_request(prompt="x", num_images=1, aspect_ratio="portrait",
                               image_model="m", user_id=1)
        self.assertNotIn("image_b64", img)

    def test_build_request_video_carries_model_and_image(self) -> None:
        req = sb.build_request(prompt="short product video", num_images=1,
                               aspect_ratio="portrait", image_model="m", user_id=2,
                               kind="video_ingredients", image_b64="QUJD",
                               video_model="veo-lite")
        self.assertEqual(req["kind"], "video_ingredients")
        self.assertEqual(req["image_b64"], "QUJD")
        self.assertEqual(req["video_model"], "veo-lite")

    def test_from_env(self) -> None:
        old = os.environ.get(sb.INTERNAL_TOKEN_ENV)
        try:
            os.environ.pop(sb.INTERNAL_TOKEN_ENV, None)
            self.assertIsNone(sb.BackendClient.from_env())
            os.environ[sb.INTERNAL_TOKEN_ENV] = "secret123"
            client = sb.BackendClient.from_env()
            self.assertIsNotNone(client)
        finally:
            if old is None:
                os.environ.pop(sb.INTERNAL_TOKEN_ENV, None)
            else:
                os.environ[sb.INTERNAL_TOKEN_ENV] = old

    def test_register_requires_token(self) -> None:
        app = web.Application()

        async def cb(body):  # noqa: ANN001
            return {"images": []}

        self.assertFalse(sb.register_internal_routes(app, cb, token=""))
        self.assertTrue(sb.register_internal_routes(app, cb, token="secret123"))


class EndpointRoundtripTests(unittest.TestCase):
    def test_auth_and_roundtrip(self) -> None:
        async def run() -> None:
            app = web.Application()

            async def cb(body):  # noqa: ANN001
                return {"images": [{"url": "http://x/1.png"}], "echo": body}

            self.assertTrue(sb.register_internal_routes(app, cb, token="secret123"))
            server = TestServer(app)
            await server.start_server()
            try:
                base = f"http://127.0.0.1:{server.port}"
                # Wrong token → unauthorized error, no images.
                bad = sb.BackendClient(base, "wrong")
                r = await bad.generate(prompt="hi there", num_images=1,
                                       aspect_ratio="portrait", image_model="m", user_id=1)
                self.assertIn("error", r)
                self.assertNotIn("images", r)
                # Correct token → cb result returned, request echoed.
                ok = sb.BackendClient(base, "secret123")
                r = await ok.generate(prompt="hi there", num_images=2,
                                      aspect_ratio="portrait", image_model="m", user_id=7)
                self.assertEqual(r["images"][0]["url"], "http://x/1.png")
                self.assertEqual(r["echo"]["num_images"], 2)
                self.assertEqual(r["echo"]["user_id"], 7)
            finally:
                await server.close()

        asyncio.run(run())

    def test_backend_unavailable_returns_error(self) -> None:
        async def run() -> None:
            client = sb.BackendClient("http://127.0.0.1:1", "secret123", timeout_sec=1.0)
            r = await client.generate(prompt="hi there", num_images=1,
                                      aspect_ratio="portrait", image_model="m", user_id=1)
            self.assertIn("error", r)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
