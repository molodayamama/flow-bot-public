#!/usr/bin/env python3
"""Generate a reviewed Photozhab landing media batch through the local backend.

This is intentionally not a general-purpose provider client.  It talks only to
the consumer's localhost-only ``/internal/generate`` route, reads the shared
token from an operator-supplied env file, bounds every response, and requires an
explicit paid-action acknowledgement.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from dotenv import dotenv_values


LANDING_USER_ID = -2_147_483_000
IMAGE_LIMIT = 25 * 1024 * 1024
VIDEO_LIMIT = 200 * 1024 * 1024
BACKEND_RESPONSE_LIMIT = ((VIDEO_LIMIT + 2) // 3) * 4 + 2 * 1024 * 1024
ALLOWED_MEDIA_HOSTS = (
    "google.com",
    "googleapis.com",
    "googleusercontent.com",
    "flow-content.google",
    "labs.google",
)

IMAGE_JOBS = (
    (
        "showcase-neon",
        "Cinematic editorial portrait of a young adult woman in a rainy neon "
        "city at night, teal and warm coral reflections, natural skin, subtle "
        "film grain, premium fashion campaign, shallow depth of field, vertical "
        "3:4 composition, no text, no logo, no watermark",
    ),
    (
        "showcase-product",
        "Luxury unbranded perfume bottle on rough black volcanic stone, dark "
        "studio, narrow lime rim light and soft coral accent, realistic glass "
        "and condensation, high-end product photography, vertical 3:4 "
        "composition, no text, no logo, no watermark",
    ),
    (
        "showcase-forest",
        "Aerial cinematic view over a snow-covered pine forest at sunrise, "
        "long blue shadows, pale golden mist between trees, documentary realism, "
        "premium film still, vertical 3:4 composition, no text, no logo, no watermark",
    ),
    (
        "showcase-motion",
        "Editorial close portrait of a young adult woman on a dark windswept "
        "coast, gentle smile and hair moving in the wind, soft cyan dawn light, "
        "authentic skin texture, cinematic realism, vertical 3:4 composition, "
        "no text, no logo, no watermark",
    ),
)

VIDEO_JOB = (
    "showcase-video",
    "A cinematic drone glide forward above a snow-covered pine forest at dawn. "
    "Soft golden mist moves between dark blue trees, subtle parallax, smooth "
    "stable camera, realistic natural light, premium film look, no text, no logo, "
    "no watermark.",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-url", default="")
    parser.add_argument("--image-model", default="nb2", choices=("nb2", "nbpro"))
    parser.add_argument("--video-model", default="omni-flash-4s")
    parser.add_argument("--skip-images", action="store_true")
    parser.add_argument("--skip-video", action="store_true")
    parser.add_argument("--approve-external-action", action="store_true")
    return parser


def _is_allowed_media_url(raw: object) -> bool:
    try:
        parsed = urlparse(str(raw or ""))
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return (
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and any(host == suffix or host.endswith("." + suffix) for suffix in ALLOWED_MEDIA_HOSTS)
    )


def _image_extension(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    raise ValueError("unsupported image response")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


async def _post(session: aiohttp.ClientSession, base_url: str, token: str, body: dict) -> dict:
    async with session.post(
        base_url.rstrip("/") + "/internal/generate",
        json=body,
        headers={"X-Internal-Token": token},
    ) as response:
        if response.status != 200:
            raise RuntimeError(f"backend HTTP {response.status}")
        raw = await _read_limited(response, BACKEND_RESPONSE_LIMIT)
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("backend returned invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("error"):
        raise RuntimeError("backend generation failed")
    return payload


async def _read_limited(response: aiohttp.ClientResponse, limit: int) -> bytes:
    declared = int(response.headers.get("Content-Length") or 0)
    if declared > limit:
        raise RuntimeError("response too large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.content.iter_chunked(256 * 1024):
        size += len(chunk)
        if size > limit:
            raise RuntimeError("response too large")
        chunks.append(chunk)
    return b"".join(chunks)


async def _download_image(session: aiohttp.ClientSession, raw_url: object) -> bytes:
    if not _is_allowed_media_url(raw_url):
        raise RuntimeError("backend returned an untrusted media URL")
    async with session.get(str(raw_url), allow_redirects=True) as response:
        if response.status != 200:
            raise RuntimeError(f"media HTTP {response.status}")
        data = await _read_limited(response, IMAGE_LIMIT)
    if not data or len(data) > IMAGE_LIMIT:
        raise RuntimeError("image response empty or too large")
    _image_extension(data)
    return data


def _decode_video(payload: dict) -> bytes:
    videos = payload.get("videos")
    item = videos[0] if isinstance(videos, list) and videos else None
    encoded = item.get("video_b64") if isinstance(item, dict) else None
    if not isinstance(encoded, str) or not encoded:
        raise RuntimeError("backend returned no video")
    if len(encoded) > ((VIDEO_LIMIT + 2) // 3) * 4 + 8:
        raise RuntimeError("video response too large")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RuntimeError("backend returned invalid video") from exc
    if not data or len(data) > VIDEO_LIMIT or b"ftyp" not in data[:64]:
        raise RuntimeError("backend returned an unsupported video")
    return data


async def _run(args: argparse.Namespace) -> list[dict[str, object]]:
    if not args.approve_external_action:
        raise RuntimeError("pass --approve-external-action to allow paid generation")
    config = dotenv_values(args.env_file)
    token = str(config.get("INTERNAL_API_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("INTERNAL_API_TOKEN is not configured")
    host = str(config.get("BACKEND_HOST") or "127.0.0.1")
    port = str(config.get("BACKEND_PORT") or config.get("CONSUMER_WEB_PORT") or "8081")
    base_url = args.base_url.strip() or f"http://{host}:{port}"
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("generation endpoint must be localhost HTTP")

    timeout = aiohttp.ClientTimeout(total=480, connect=10)
    manifest: list[dict[str, object]] = []
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if not args.skip_images:
            for name, prompt in IMAGE_JOBS:
                payload = await _post(session, base_url, token, {
                    "kind": "image",
                    "prompt": prompt,
                    "user_id": LANDING_USER_ID,
                    "aspect_ratio": "portrait_34",
                    "num_images": 1,
                    "image_model": args.image_model,
                })
                items = payload.get("images")
                item = items[0] if isinstance(items, list) and items else None
                data = await _download_image(session, item.get("url") if isinstance(item, dict) else None)
                path = args.output_dir / (name + _image_extension(data))
                _atomic_write(path, data)
                manifest.append({"kind": "image", "file": path.name, "bytes": len(data)})
                print(f"generated image {path.name} ({len(data)} bytes)", flush=True)

        if not args.skip_video:
            name, prompt = VIDEO_JOB
            payload = await _post(session, base_url, token, {
                "kind": "video_text",
                "prompt": prompt,
                "user_id": LANDING_USER_ID,
                "aspect_ratio": "landscape",
                "num_images": 1,
                "image_model": args.image_model,
                "video_model": args.video_model,
            })
            data = _decode_video(payload)
            path = args.output_dir / (name + ".mp4")
            _atomic_write(path, data)
            manifest.append({"kind": "video", "file": path.name, "bytes": len(data)})
            print(f"generated video {path.name} ({len(data)} bytes)", flush=True)

    manifest_path = args.output_dir / "generation-manifest.json"
    _atomic_write(manifest_path, (json.dumps(manifest, indent=2) + "\n").encode())
    return manifest


def main() -> int:
    args = _parser().parse_args()
    try:
        asyncio.run(_run(args))
    except Exception as exc:  # noqa: BLE001 - CLI boundary, deliberately redacted
        print(f"landing media generation failed: {exc}", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
