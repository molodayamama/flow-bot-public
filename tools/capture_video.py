"""Capture Google Flow video API requests.

Default mode aborts the first video-like POST before Google accepts the job, so
credits should not be spent. Use ``--no-abort`` only when you intentionally want
the request to go through and capture polling/download behavior.

Common usage:

    python tools/capture_video.py
    python tools/capture_video.py --frames
    python tools/capture_video.py --ingredients
    python tools/capture_video.py --edit
    python tools/capture_video.py --upload-edit
    python tools/capture_video.py --extend
    python tools/capture_video.py --no-abort --timeout 300

Frames capture workflow:

    1. Stop flow_bot.py; this script needs exclusive access to google_profile/.
    2. Run: python tools/capture_video.py --frames
    3. In the opened browser, use Flow's Frames / first-last-frame flow manually.
    4. Upload start/end frames, enter a short prompt, click Generate.
    5. The script aborts the first video-like POST and writes a sanitized JSON
       capture to tools/video_frames_capture.json.

The output intentionally excludes headers, cookies, bearer tokens, and raw
browser profile data. It stores only sanitized URLs, request bodies, and response
bodies for the API calls seen by Playwright routing.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

FLOW_URL = "https://labs.google/fx/tools/flow"
API_HOST = "aisandbox-pa.googleapis.com"
USER_DATA_DIR = os.getenv("USER_DATA_DIR", "./google_profile")

# Hook grecaptcha.enterprise.execute to record the EXACT action string the Flow
# frontend uses. The reCAPTCHA action is baked into the token (not the request
# body), so it can't be read from the POST — wrapping execute() is the only way
# to learn the action the video endpoint expects (the 403 cause). Installed via
# add_init_script so it runs before page scripts; grecaptcha may load late, so we
# poll until it appears.
GRECAPTCHA_HOOK_JS = r"""
(() => {
  window.__grecaptcha_actions = window.__grecaptcha_actions || [];
  const record = (o) => { try { window.__grecaptcha_actions.push(o); } catch (e) {} };
  const wrap = (ns) => {
    if (!ns || ns.__actionWrapped || typeof ns.execute !== 'function') return;
    const orig = ns.execute;
    ns.execute = function (a, b) {
      try {
        let action = null, sitekey = null;
        if (a && typeof a === 'object') { action = a.action; sitekey = a.sitekey; }
        else { sitekey = a; if (b && typeof b === 'object') action = b.action; }
        record({ action: action || null, sitekey: sitekey || null,
                 ts: Date.now(), url: location.href });
      } catch (e) {}
      return orig.apply(this, arguments);
    };
    ns.__actionWrapped = true;
  };
  const iv = setInterval(() => {
    try {
      if (window.grecaptcha) {
        wrap(window.grecaptcha);
        if (window.grecaptcha.enterprise) wrap(window.grecaptcha.enterprise);
      }
    } catch (e) {}
  }, 250);
  setTimeout(() => clearInterval(iv), 600000);
})();
"""
if not Path(USER_DATA_DIR).is_absolute():
    USER_DATA_DIR = str(PROJECT_ROOT / USER_DATA_DIR)

VIDEO_URL_KEYWORDS = (
    "batchAsyncGenerateVideoText",
    "batchAsyncGenerateVideoStartAndEndImage",
    "batchAsyncGenerateVideoReferenceImages",
    "batchAsyncEditVideo",
    "batchAsyncGenerateVideoEdit",
    "batchAsyncExtendVideo",
    "editVideo",
    "extendVideo",
    "batchGenerateVideos",
    "GenerateVideo",
    "generateVideo",
)

# URL names can drift; Frames may use a different endpoint. Body signals are the
# real safety net for abort mode.
VIDEO_BODY_KEYWORDS = (
    "videoModelKey",
    "videoModel",
    "startImage",
    "endImage",
    "startImageMediaId",
    "endImageMediaId",
    "referenceImage",
    "referenceImages",
    "referenceImageMediaIds",
    "IMAGE_USAGE_TYPE_ASSET",
    "videoInput",
    "videoInputs",
    "sourceVideo",
    "referenceVideo",
    "generatedVideo",
    "editInstruction",
    "editPrompt",
    "extend",
    "extension",
)

SECRET_QUERY_KEYS = {
    "key",
    "token",
    "access_token",
    "authorization",
}

SECRET_BODY_KEYS = {
    "authorization",
    "bearer",
    "cookie",
    "cookies",
    "captcha",
    "captchaToken",
    "recaptcha",
    "recaptchaToken",
    "token",
}

DONE_SIGNALS = (
    "MEDIA_GENERATION_STATUS_SUCCESSFUL",
    "MEDIA_GENERATION_STATUS_FAILED",
    '"status":"DONE"',
    '"status": "DONE"',
    '"done":true',
    '"done": true',
    "SUCCEEDED",
)
MP4_RE = re.compile(r"https?://[^\s\"']+\.mp4")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_profile_lock() -> None:
    base = Path(USER_DATA_DIR)
    for lock_file in (base / "lockfile", base / "SingletonLock"):
        if not lock_file.exists():
            continue
        try:
            with open(lock_file, "r+b"):
                pass
        except OSError:
            print(
                f"\nChrome profile is locked: {base}\n"
                "Stop flow_bot.py / Chrome that uses this profile, then retry.\n",
                flush=True,
            )
            raise SystemExit(1)


def sanitize_url(url: str) -> str:
    """Redact API keys and token-like query params while keeping endpoint shape."""
    try:
        parts = urlsplit(url)
        query = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key.lower() in SECRET_QUERY_KEYS:
                query.append((key, f"<REDACTED len={len(value)}>"))
            else:
                query.append((key, value))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    except Exception:
        return url


def strip_xssi(text: str) -> str:
    stripped = text.lstrip()
    for prefix in (")]}'\n", ")]}'", ")]}"):
        if stripped.startswith(prefix):
            return stripped[len(prefix):]
    return text


def redact(obj):
    """Recursively redact token-like values while preserving payload structure."""
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            key_text = str(key)
            key_l = key_text.lower()
            if key_l in {k.lower() for k in SECRET_BODY_KEYS}:
                if isinstance(value, str):
                    out[key] = f"<REDACTED len={len(value)}>"
                else:
                    out[key] = "<REDACTED>"
            else:
                out[key] = redact(value)
        return out
    if isinstance(obj, list):
        return [redact(value) for value in obj]
    if isinstance(obj, str) and len(obj) > 1200 and re.search(r"[A-Za-z0-9_-]{80,}", obj):
        return f"<REDACTED long-string len={len(obj)}>"
    return obj


def parse_body(text: str):
    if not text:
        return ""
    try:
        return redact(json.loads(strip_xssi(text)))
    except (json.JSONDecodeError, ValueError):
        return {"raw": text[:1000]}


def parse_request_body(body_str: str):
    if not body_str:
        return ""
    try:
        return redact(json.loads(body_str))
    except (json.JSONDecodeError, ValueError):
        return {"raw": body_str[:500]}


def request_post_data_text(req) -> str:
    """Return request POST data as text without crashing on binary upload bodies."""
    try:
        value = req.post_data
        if value:
            return value
    except UnicodeDecodeError:
        pass
    except Exception:
        pass

    try:
        value = req.post_data_buffer
    except Exception:
        return ""
    if not value:
        return ""
    try:
        return bytes(value).decode("utf-8")
    except UnicodeDecodeError:
        return bytes(value).decode("utf-8", errors="replace")
    except Exception:
        return ""


def is_video_like_request(url: str, method: str, body_str: str) -> bool:
    if method.upper() != "POST":
        return False
    if "batchLogFrontendEvents" in url:
        return False
    url_l = url.lower()
    body_l = body_str.lower()
    if any(keyword.lower() in url_l for keyword in VIDEO_URL_KEYWORDS):
        return True
    return any(keyword.lower() in body_l for keyword in VIDEO_BODY_KEYWORDS)


def capture_kind_from_args(args: argparse.Namespace) -> str:
    if args.frames:
        return "frames"
    if args.ingredients:
        return "ingredients"
    if getattr(args, "upload_edit", False):
        return "upload-edit"
    if args.edit:
        return "edit"
    if args.extend:
        return "extend"
    return "text-video"


def default_output(args: argparse.Namespace) -> Path:
    if args.output is not None:
        return args.output
    kind = capture_kind_from_args(args)
    if kind == "frames":
        return Path("tools/video_frames_capture.json")
    if kind == "ingredients":
        return Path("tools/video_ingredients_capture.json")
    if kind == "edit":
        return Path("tools/video_edit_capture.json")
    if kind == "upload-edit":
        return Path("tools/video_upload_edit_capture.json")
    if kind == "extend":
        return Path("tools/video_extend_capture.json")
    if args.no_abort:
        return Path("tools/video_flow_capture.json")
    return Path("tools/video_raw_capture.json")


def default_timeout(args: argparse.Namespace) -> int:
    if args.timeout is not None:
        return args.timeout
    if args.no_abort:
        return 300
    if capture_kind_from_args(args) != "text-video":
        return 300
    return 180


async def run(
    output_path: Path,
    timeout_sec: int,
    *,
    no_abort: bool,
    frames: bool,
    ingredients: bool = False,
    edit: bool = False,
    upload_edit: bool = False,
    extend: bool = False,
) -> int:
    from playwright.async_api import async_playwright

    check_profile_lock()

    if no_abort:
        print(
            "\nWARNING: --no-abort lets the generation request through and can spend Flow credits.\n",
            flush=True,
        )

    seq = 0
    traffic: list[dict] = []
    seen_posts: list[str] = []
    generation_request: dict = {}
    captured: dict = {}
    started_event = asyncio.Event()
    done_event = asyncio.Event()

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            channel="chrome",
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        # Record the reCAPTCHA action(s) the Flow frontend uses for video.
        await context.add_init_script(GRECAPTCHA_HOOK_JS)

        async def handle_route(route):
            nonlocal seq

            req = route.request
            url = req.url
            method = req.method.upper()
            body_str = request_post_data_text(req)

            # Upload of a user video may go to a DIFFERENT host (resumable/signed
            # upload URL), so also record upload-like traffic on any host — its
            # response carries the mediaId/workflowId we need for video-edit.
            upload_like = (
                method in ("POST", "PUT", "PATCH")
                and "upload" in url.lower()
                and "batchlog" not in url.lower()
            )
            if API_HOST not in url and not upload_like:
                await route.continue_()
                return

            if method == "POST":
                seen_posts.append(sanitize_url(url))

            print(f"  [API {method}] {sanitize_url(url)}", flush=True)

            is_video_like = is_video_like_request(url, method, body_str)

            if is_video_like and not no_abort:
                seq += 1
                generation_request.update({
                    "url": url,
                    "body": body_str,
                    "captured_at": utc_now(),
                })
                fake_response = {"error": "ABORTED_BY_CAPTURE_SCRIPT"}
                traffic.append({
                    "seq": seq,
                    "method": method,
                    "url": sanitize_url(url),
                    "status": 499,
                    "request_body": parse_request_body(body_str),
                    "response_body": fake_response,
                })
                print(
                    "\nCaptured video-like POST and fulfilled locally before provider acceptance:\n"
                    f"  {sanitize_url(url)}\n",
                    flush=True,
                )
                await route.fulfill(
                    status=499,
                    content_type="application/json",
                    body=json.dumps(fake_response),
                )
                started_event.set()
                done_event.set()
                return

            try:
                response = await route.fetch()
            except Exception as exc:
                print(f"    route.fetch failed: {exc!r}; continuing request", flush=True)
                try:
                    await route.continue_()
                except Exception:
                    pass
                return

            try:
                response_text = await response.text()
            except Exception:
                response_text = ""

            seq += 1
            entry = {
                "seq": seq,
                "method": method,
                "url": sanitize_url(url),
                "status": response.status,
                "request_body": parse_request_body(body_str),
                "response_body": parse_body(response_text),
            }
            traffic.append(entry)

            if is_video_like:
                generation_request.update({
                    "url": url,
                    "body": body_str,
                    "captured_at": utc_now(),
                })
                print(
                    f"\nVideo-like POST passed through (--no-abort): {sanitize_url(url)} -> {response.status}\n",
                    flush=True,
                )
                started_event.set()

            if started_event.is_set() and response_text:
                if any(signal in response_text for signal in DONE_SIGNALS) or MP4_RE.search(response_text):
                    done_event.set()

            try:
                await route.fulfill(response=response)
            except Exception:
                pass

        await context.route("**/*", handle_route)
        page = await context.new_page()

        non_api_urls: list[str] = []

        def on_any_request(req):
            url = req.url
            if API_HOST in url:
                return
            if any(skip in url for skip in (".png", ".jpg", ".ico", ".woff", ".css", ".wasm")):
                return
            if any(signal in url for signal in ("video", "media", ".mp4", "fife", "storage.google")):
                non_api_urls.append(sanitize_url(url))

        page.on("request", on_any_request)

        await page.goto(FLOW_URL, timeout=60_000, wait_until="domcontentloaded")

        if frames:
            instructions = (
                "Frames capture:\n"
                "  1. Open your Flow project.\n"
                "  2. Choose Frames / first-last-frame video flow.\n"
                "  3. Upload start frame and end frame.\n"
                "  4. Enter a short prompt and click Generate.\n"
                "  5. Default mode aborts the video-like POST before spending credits.\n"
            )
        elif ingredients:
            instructions = (
                "Ingredients capture (photo[s] + text -> video):\n"
                "  1. Open your Flow project.\n"
                "  2. Choose the Ingredients / reference-images video flow.\n"
                "  3. Upload one or more reference photos.\n"
                "  4. Enter a short prompt and click Generate.\n"
                "  5. Default mode aborts the video-like POST before spending credits.\n"
                "  Goal: capture the endpoint URL + how reference images are passed.\n"
            )
        elif upload_edit:
            instructions = (
                "User-uploaded video Edit capture (THIS is the bot's failing flow):\n"
                "  1. Open your Flow project.\n"
                "  2. Upload your OWN video file from disk (not a generated one).\n"
                "     -> Watch the console: the UPLOAD POST/PUT is logged here even if\n"
                "        it goes to a different host (resumable/signed upload URL).\n"
                "  3. Wait until Flow finishes ingesting the uploaded video.\n"
                "  4. Use Flow's native Edit action on that uploaded video.\n"
                "  5. Enter a short edit instruction and submit it.\n"
                "  6. Default mode aborts the final video-like generate POST (no credits);\n"
                "     the upload + its mediaId/workflowId response are still captured.\n"
                "  Goal: capture (a) the upload endpoint + response (does it return a\n"
                "        mediaId AND a workflowId?) and (b) how Edit references that\n"
                "        uploaded source.\n"
            )
        elif edit:
            instructions = (
                "Native video Edit capture:\n"
                "  1. Open your Flow project and find a generated video.\n"
                "  2. Use Flow's native Edit action for that video.\n"
                "  3. Enter a short edit instruction and submit it.\n"
                "  4. Default mode aborts the video-like POST before spending credits.\n"
                "  Goal: capture the endpoint URL + how the source video and edit text are passed.\n"
            )
        elif extend:
            instructions = (
                "Native video Extend capture:\n"
                "  1. Open your Flow project and find a generated video that can be extended.\n"
                "  2. Use Flow's native Extend / Continue action.\n"
                "  3. Submit the extension request.\n"
                "  4. Default mode aborts the video-like POST before spending credits.\n"
                "  Goal: capture the endpoint URL + how the source video is referenced.\n"
            )
        else:
            instructions = (
                "Text video capture:\n"
                "  1. Open your Flow project.\n"
                "  2. Switch to Video mode, choose model/settings.\n"
                "  3. Enter a short prompt and click Generate.\n"
                "  4. Default mode aborts; --no-abort captures the full async flow.\n"
            )

        mode = "NO-ABORT (can spend credits)" if no_abort else "ABORT (safe default)"
        print(
            f"\n{'=' * 70}\n"
            f"Mode: {mode}\n"
            f"Output: {output_path}\n"
            f"{instructions}"
            f"{'=' * 70}\n"
            f"Waiting up to {timeout_sec}s...\n",
            flush=True,
        )

        try:
            await asyncio.wait_for(done_event.wait(), timeout=timeout_sec)
            if no_abort:
                await asyncio.sleep(5)
                try:
                    video_srcs = await page.evaluate(
                        """() => Array.from(document.querySelectorAll('video'))
                            .map(v => v.src || v.currentSrc)
                            .filter(Boolean)"""
                    )
                    if video_srcs:
                        captured["video_src_from_page"] = [sanitize_url(src) for src in video_srcs]
                except Exception as exc:
                    print(f"Could not read video.src from page: {exc!r}", flush=True)
        except asyncio.TimeoutError:
            print(f"\nTimed out after {timeout_sec}s.", flush=True)
            if not started_event.is_set():
                if seen_posts:
                    print("POSTs seen, but none matched video-like signals:", flush=True)
                    for post_url in dict.fromkeys(seen_posts):
                        print(f"  {post_url}", flush=True)
                else:
                    print("No API POSTs seen. Did you click Generate?", flush=True)
        finally:
            try:
                acts = await page.evaluate("() => window.__grecaptcha_actions || []")
                if acts:
                    captured["grecaptcha_actions"] = acts
            except Exception:
                pass
            await context.close()

    if not traffic and not generation_request:
        print("\nNothing useful captured.", flush=True)
        return 1

    out: dict = {
        "mode": "no-abort" if no_abort else "abort",
        "capture_kind": (
            "frames"
            if frames
            else (
                "ingredients"
                if ingredients
                else (
                    "upload-edit"
                    if upload_edit
                    else ("edit" if edit else ("extend" if extend else "text-video"))
                )
            )
        ),
        "captured_at": utc_now(),
        "generation_request": None,
        "api_traffic": traffic,
    }

    if generation_request:
        out["generation_request"] = {
            "url": sanitize_url(generation_request.get("url") or ""),
            "body": parse_request_body(generation_request.get("body", "")),
        }

    if captured.get("grecaptcha_actions"):
        out["grecaptcha_actions"] = captured["grecaptcha_actions"]

    if captured.get("video_src_from_page"):
        out["video_src_from_page"] = captured["video_src_from_page"]

    if non_api_urls:
        out["non_api_media_urls"] = list(dict.fromkeys(non_api_urls))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nSaved sanitized capture -> {output_path}", flush=True)
    print(f"API calls captured: {len(traffic)}", flush=True)
    if out["generation_request"]:
        print(f"Generation-like request: {out['generation_request']['url']}", flush=True)
    if out.get("grecaptcha_actions"):
        seen_actions = list(dict.fromkeys(
            a.get("action") for a in out["grecaptcha_actions"] if a.get("action")
        ))
        print(f"reCAPTCHA actions observed: {seen_actions or '(none)'}", flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture sanitized Google Flow video API traffic.")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--frames",
        action="store_true",
        help="Manual Frames capture mode; default output is tools/video_frames_capture.json.",
    )
    mode_group.add_argument(
        "--ingredients",
        action="store_true",
        help="Manual Ingredients (photos+text) capture; default output is tools/video_ingredients_capture.json.",
    )
    mode_group.add_argument(
        "--edit",
        action="store_true",
        help="Manual native video Edit capture; default output is tools/video_edit_capture.json.",
    )
    mode_group.add_argument(
        "--upload-edit",
        dest="upload_edit",
        action="store_true",
        help=(
            "Upload your OWN video then Edit it; captures the upload endpoint + "
            "mediaId/workflowId. Default output tools/video_upload_edit_capture.json."
        ),
    )
    mode_group.add_argument(
        "--extend",
        action="store_true",
        help="Manual native video Extend capture; default output is tools/video_extend_capture.json.",
    )
    parser.add_argument(
        "--no-abort",
        action="store_true",
        help="Let video-like POSTs through. This can spend Google Flow credits.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON path.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Seconds to wait for the manual action.",
    )
    return parser


if __name__ == "__main__":
    parsed = build_parser().parse_args()
    raise SystemExit(
        asyncio.run(
            run(
                default_output(parsed),
                default_timeout(parsed),
                no_abort=parsed.no_abort,
                frames=parsed.frames,
                ingredients=parsed.ingredients,
                edit=parsed.edit,
                upload_edit=parsed.upload_edit,
                extend=parsed.extend,
            )
        )
    )
