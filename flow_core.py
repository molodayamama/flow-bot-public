"""Pure, dependency-free helpers for ``flow_bot.py``.

This module deliberately imports only the Python standard library so it can be
unit-tested without ``aiogram``, ``playwright``, ``aiohttp`` or any network
access (those heavy deps are only installed where the bot actually runs).

It holds the testable core of the Google Flow bot:

- Google Flow ``batchGenerateImages`` payload construction (generation + edit).
- Response parsing (kept byte-for-byte compatible with the previous inline
  ``parse_result`` so image delivery behaviour does not change).
- ``imageInputs`` construction for editing a previously generated image.
- Per-Telegram-user project persistence (each user gets their own Flow project).
- A bounded in-memory registry that maps inline-button tokens back to the
  source image they should edit.

Так каждый Telegram-пользователь получает отдельный project на сайте, а у
каждой выданной картинки есть кнопка «Редактировать», которая ссылается на
конкретное изображение.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Google Flow constants ─────────────────────────────────────────────

IMAGE_MODEL_NAME = "GEM_PIX_2"

ASPECT_MAP = {
    "landscape": "IMAGE_ASPECT_RATIO_LANDSCAPE",
    "portrait": "IMAGE_ASPECT_RATIO_PORTRAIT",
    "square": "IMAGE_ASPECT_RATIO_SQUARE",
    "16:9": "IMAGE_ASPECT_RATIO_LANDSCAPE",
    "9:16": "IMAGE_ASPECT_RATIO_PORTRAIT",
    "1:1": "IMAGE_ASPECT_RATIO_SQUARE",
}

# Сколько изображений можно просить за один запрос (защита от абуза/квоты).
MIN_NUM_IMAGES = 1
MAX_NUM_IMAGES = 8
DEFAULT_NUM_IMAGES = 4


def clamp_num_images(value: object, default: int = DEFAULT_NUM_IMAGES) -> int:
    """Привести запрошенное число картинок к допустимому диапазону."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(MIN_NUM_IMAGES, min(MAX_NUM_IMAGES, n))


# Telegram inline ``callback_data`` is limited to 64 bytes. ``edit:`` + a
# 16-char hex token = 21 bytes, comfortably inside the limit.
EDIT_CALLBACK_PREFIX = "edit:"
# Other per-image inline actions (all token-based, same 64-byte budget).
VARY_CALLBACK_PREFIX = "vary:"   # image-to-image variations, same prompt
REGEN_CALLBACK_PREFIX = "regen:"  # text-to-image again, new seed
MIX_CALLBACK_PREFIX = "mix:"    # add image to the ingredients basket
UPSCALE_CALLBACK_PREFIX = "up:"  # legacy alias (now: free original download)
DOWNLOAD_CALLBACK_PREFIX = "dl:"  # download the original uncompressed file (free)
UP2X_CALLBACK_PREFIX = "u2:"    # paid AI enhance ×2 (image-to-image, prompt-based)
REALUP_CALLBACK_PREFIX = "ru:"  # paid TRUE upscale via the service's own endpoint

ACTION_PREFIXES = {
    "edit": EDIT_CALLBACK_PREFIX,
    "vary": VARY_CALLBACK_PREFIX,
    "regen": REGEN_CALLBACK_PREFIX,
    "mix": MIX_CALLBACK_PREFIX,
    "upscale": UPSCALE_CALLBACK_PREFIX,
    "download": DOWNLOAD_CALLBACK_PREFIX,
    "up2x": UP2X_CALLBACK_PREFIX,
    "realup": REALUP_CALLBACK_PREFIX,
}


def aspect_code(aspect_ratio: str) -> str:
    """Map a friendly aspect-ratio name to Google's enum (defaults landscape)."""
    return ASPECT_MAP.get((aspect_ratio or "").strip().lower(), "IMAGE_ASPECT_RATIO_LANDSCAPE")


# ── response parsing ──────────────────────────────────────────────────


def result_pairs(data: dict) -> list[tuple[str, dict]]:
    """Return ``(display_url, raw_image_dict)`` pairs from a Flow response.

    The ``display_url`` precedence is kept identical to the previous inline
    ``parse_result`` for each response shape, so image delivery is unchanged.
    The raw image dict is preserved so an edit can reference the exact image.
    """
    pairs: list[tuple[str, dict]] = []

    # Формат 1: {"responses": [{"generatedImage": {"mediaStoreUri": ...}}]}
    for resp in data.get("responses", []) or []:
        if not isinstance(resp, dict):
            continue
        img = resp.get("generatedImage") or resp.get("imageOutput") or {}
        if not isinstance(img, dict):
            continue
        url = img.get("mediaStoreUri") or img.get("uri") or img.get("fifeUrl")
        if url:
            pairs.append((url, img))

    # Формат 2: {"media": [{"image": {"generatedImage": {"fifeUrl": ...}}}]}
    for item in data.get("media", []) or []:
        if not isinstance(item, dict):
            continue
        img = (item.get("image", {}) or {}).get("generatedImage", {}) or {}
        if not isinstance(img, dict):
            continue
        url = img.get("fifeUrl") or img.get("mediaStoreUri") or img.get("uri")
        if url:
            pairs.append((url, img))

    return pairs


def parse_result(data: dict) -> list[str]:
    """Image URLs from a Flow response (kept for backward compatibility)."""
    return [url for url, _ in result_pairs(data)]


def edit_source_uri(img: dict) -> str | None:
    """Return the identifier used to reference ``img`` as an edit input.

    Prefers ``mediaStoreUri`` (the stable internal media reference) over the
    public ``fifeUrl`` because the editing endpoint references stored media,
    not a CDN URL.
    """
    if not isinstance(img, dict):
        return None
    return img.get("mediaStoreUri") or img.get("uri") or img.get("fifeUrl")


_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
# A media id sits in the image content URL: ``…/image/<uuid>?…`` (confirmed in
# real generation data: fifeUrl …/image/56999c74-… ↔ mediaId 56999c74-…).
_MEDIA_URL_RE = re.compile(
    r"/(?:image|images|media|asset|assets)/(" + _UUID_RE.pattern + r")",
)
# Keys that may carry the media id, in preference order. ``name`` is included
# because Flow's edit input references the image via a bare-id ``name`` field.
_MEDIA_ID_KEYS = ("mediaId", "mediaID", "media_id", "id", "name")
# Keys that are NOT the media id even though they hold UUIDs.
_NON_MEDIA_ID_KEYS = {"projectId", "sessionId", "workflowId", "userId", "requestId"}


def loads_xssi(text: str) -> Any:
    """Parse a JSON response tolerating Google's anti-XSSI prefix ``)]}'``.

    Returns ``None`` if the text is not JSON.
    """
    if not isinstance(text, str):
        return None
    stripped = text.lstrip()
    for prefix in (")]}'\n", ")]}'", ")]}"):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):]
            break
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None


def _media_url_in(obj: Any) -> str | None:
    """First string anywhere in ``obj`` that looks like an image content URL."""
    if isinstance(obj, str):
        return obj if _MEDIA_URL_RE.search(obj) else None
    if isinstance(obj, dict):
        for value in obj.values():
            found = _media_url_in(value)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _media_url_in(value)
            if found:
                return found
    return None


def id_from_media_url(url: str | None) -> str | None:
    """Public: extract the media UUID from an image content URL, or ``None``."""
    return _id_from_media_url(url)


def _id_from_media_url(url: str | None) -> str | None:
    if not isinstance(url, str):
        return None
    match = _MEDIA_URL_RE.search(url)
    return match.group(1) if match else None


def extract_media_id(obj: Any) -> str | None:
    """Recover an uploaded/generated image's media id from an arbitrary response.

    Strategy (no reliance on one exact field name or host):
    1. A preferred key (``mediaId``/``id``/``name`` …) whose value is a UUID and
       is not a project/session/workflow id.
    2. Otherwise the UUID embedded in an image content URL (``…/image/<uuid>``) —
       the structural invariant confirmed in real Flow data.
    """
    # Pass 1: a UUID-valued media-id key, skipping known non-media id keys.
    found = _find_id_key(obj)
    if found:
        return found
    # Pass 2: derive from a media content URL.
    return _id_from_media_url(_media_url_in(obj))


def _find_id_key(obj: Any) -> str | None:
    if isinstance(obj, dict):
        for key in _MEDIA_ID_KEYS:
            value = obj.get(key)
            if (
                isinstance(value, str)
                and key not in _NON_MEDIA_ID_KEYS
                and _UUID_RE.fullmatch(value)
            ):
                return value
        for k, value in obj.items():
            if k in _NON_MEDIA_ID_KEYS:
                continue
            found = _find_id_key(value)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_id_key(value)
            if found:
                return found
    return None


def media_source_from_response(obj: Any) -> dict | None:
    """Build an editable image source (``{mediaId, fifeUrl?}``) from a response.

    Used to recover the source of an image just uploaded to Flow so it can be
    edited like a generated one. Returns ``None`` if no media id can be found.
    """
    media_id = extract_media_id(obj)
    if not media_id:
        return None
    source: dict[str, str] = {"mediaId": media_id}
    url = _media_url_in(obj)
    if url and url.startswith("http"):
        source["fifeUrl"] = url
    return source


def find_media_source(obj: Any) -> dict | None:
    """Backwards-compatible alias returning an editable image source dict."""
    return media_source_from_response(obj)


def download_url(img: dict) -> str | None:
    """Return the best publicly fetchable URL to download the full-quality file.

    Flow's on-site "upscale" button is really a client-side download of the
    already-generated image, so the bot's equivalent is to fetch the original
    bytes. We prefer the public CDN ``fifeUrl`` (an https URL aiohttp can GET)
    over the internal ``mediaStoreUri``/``uri`` references.
    """
    if not isinstance(img, dict):
        return None
    fife = img.get("fifeUrl")
    if isinstance(fife, str) and fife.startswith("http"):
        return fife
    for key in ("uri", "mediaStoreUri"):
        value = img.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    return fife or img.get("uri") or img.get("mediaStoreUri")


# ── payload construction ──────────────────────────────────────────────


REF_PLACEHOLDER = "__FLOW_IMAGE_REF__"

# Known-good edit-request shape, derived from a REAL intercepted Flow edit
# (not a guess): the live browser sent
#   imageInputs: [ {"imageInputType": "IMAGE_INPUT_TYPE_BASE_IMAGE",
#                   "name": "<image mediaId, a 36-char UUID>"} ]
# ``name`` is the specific image's ``mediaId`` (workflowId is shared across a
# whole batch, so it can't identify one image). Used as the default when no
# runtime-learned capture file has resolved yet, so editing works out of the box;
# self-calibration can still override this if Google changes the contract.
DEFAULT_EDIT_CAPTURE: dict[str, Any] = {
    "version": 1,
    "resolved": True,
    "source": "known-default",
    "ref_path": ["name"],
    "extract": "whole",
    "ref_source_key": "mediaId",
    "template": {
        "imageInputType": "IMAGE_INPUT_TYPE_BASE_IMAGE",
        "name": REF_PLACEHOLDER,
    },
}


def build_image_inputs(source: dict, capture: dict | None = None) -> list[dict]:
    """Build the ``imageInputs`` array that references a prior generated image.

    The exact ``imageInputs`` element shape is NOT guessable and a wrong guess
    is rejected by Google with HTTP 400 (observed:
    ``Unknown name "mediaStoreUri" at 'requests[0].image_inputs[0]'``). The shape
    here is taken from a REAL intercepted edit (see :data:`DEFAULT_EDIT_CAPTURE`).

    Resolution order:
    1. a runtime-learned ``capture`` template (self-calibration), if resolved;
    2. otherwise the known-good :data:`DEFAULT_EDIT_CAPTURE`.
    """
    if not isinstance(source, dict):
        return []
    for candidate in (capture, DEFAULT_EDIT_CAPTURE):
        if candidate:
            built = apply_capture(source, candidate)
            if built:
                return built
    return []


MAX_INGREDIENTS = 4


def build_ingredients_inputs(
    sources: list[dict], capture: dict | None = None, max_inputs: int = MAX_INGREDIENTS
) -> list[dict]:
    """Build a multi-image ``imageInputs`` array ("ingredients").

    Each source becomes one ``imageInputs`` element using the same confirmed
    per-image shape as a single edit (BASE_IMAGE + the image's id). Combining
    several previously generated images into one new prompt is how Flow's
    "ingredients" stay on the contract we already verified — no new endpoint.
    """
    inputs: list[dict] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        built = build_image_inputs(source, capture)
        if built:
            inputs.extend(built)
        if len(inputs) >= max_inputs:
            break
    return inputs[:max_inputs]


def apply_capture(source: dict, capture: dict) -> list[dict]:
    """Fill a captured ``imageInputs`` template with ``source``'s reference value.

    Supports two extraction recipes learned at capture time:

    - ``whole``: the request referenced the image by a value the bot stores
      verbatim (e.g. the full ``mediaStoreUri``).
    - ``substring``: the request referenced a bare id that is embedded inside one
      of the bot's stored values (observed: a 36-char id sitting inside the
      longer ``mediaStoreUri``). The recorded ``prefix``/``suffix`` locate it.

    Returns ``[]`` if the capture is unresolved or the id can't be derived.
    """
    template = capture.get("template")
    ref_path = capture.get("ref_path")
    if template is None or not ref_path:
        return []

    value = _extract_ref_value(source, capture)
    if not value:
        return []

    entry = _deep_copy(template)
    try:
        set_path(entry, ref_path, value)
    except (KeyError, IndexError, TypeError, ValueError):
        return []
    return [entry]


def _extract_ref_value(source: dict, capture: dict) -> str | None:
    """Derive the image-reference string for ``source`` per the capture recipe."""
    extract = capture.get("extract", "whole")
    ref_key = capture.get("ref_source_key")

    candidates: list[str] = []
    if ref_key and isinstance(source.get(ref_key), str):
        candidates.append(source[ref_key])
    # Fall back to any stored string value if the recorded key is missing.
    for value in source.values():
        if isinstance(value, str) and value and value not in candidates:
            candidates.append(value)

    if extract == "substring":
        prefix = capture.get("prefix", "")
        suffix = capture.get("suffix", "")
        for value in candidates:
            extracted = _slice_between(value, prefix, suffix)
            if extracted:
                return extracted
        return None

    # extract == "whole"
    if ref_key and isinstance(source.get(ref_key), str) and source[ref_key]:
        return source[ref_key]
    return edit_source_uri(source)


def _slice_between(value: str, prefix: str, suffix: str) -> str | None:
    """Return the text in ``value`` between ``prefix`` and ``suffix`` (or ``None``)."""
    start = 0
    if prefix:
        idx = value.find(prefix)
        if idx < 0:
            return None
        start = idx + len(prefix)
    if suffix:
        end = value.find(suffix, start)
        if end < 0:
            return None
    else:
        end = len(value)
    middle = value[start:end]
    return middle or None


# ── nested path helpers (for captured templates) ──────────────────────


def get_path(node: Any, path: list) -> Any:
    cur = node
    for key in path:
        cur = cur[int(key)] if isinstance(cur, list) else cur[key]
    return cur


def set_path(node: Any, path: list, value: Any) -> Any:
    cur = node
    for key in path[:-1]:
        cur = cur[int(key)] if isinstance(cur, list) else cur[key]
    last = path[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value
    return node


def find_ref_path(node: Any, targets: set, prefix: tuple = ()) -> tuple[list, str] | None:
    """Find the path to the first string leaf whose value is in ``targets``.

    Returns ``(path, matched_value)`` or ``None``. Used to locate which field in
    a real captured ``imageInputs`` element holds the image reference, by
    matching against identifiers of images the bot recently delivered.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            found = find_ref_path(value, targets, prefix + (key,))
            if found:
                return found
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found = find_ref_path(value, targets, prefix + (index,))
            if found:
                return found
    elif isinstance(node, str) and node in targets:
        return list(prefix), node
    return None


def describe_schema(node: Any, _depth: int = 0, max_depth: int = 8) -> Any:
    """Values-free structural description of ``node`` (safe to log).

    Strings collapse to ``str[len=N]`` so no secret/value content is emitted.
    """
    if _depth >= max_depth:
        return "…"
    if isinstance(node, dict):
        return {str(key): describe_schema(value, _depth + 1, max_depth) for key, value in node.items()}
    if isinstance(node, list):
        sample = describe_schema(node[0], _depth + 1, max_depth) if node else None
        return {"list[len=%d]" % len(node): sample}
    if isinstance(node, bool):
        return "bool"
    if isinstance(node, str):
        return "str[len=%d]" % len(node)
    if isinstance(node, int):
        return "int"
    if isinstance(node, float):
        return "float"
    if node is None:
        return "null"
    return type(node).__name__


def _deep_copy(node: Any) -> Any:
    if isinstance(node, dict):
        return {key: _deep_copy(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_deep_copy(value) for value in node]
    return node


# ── edit-shape capture persistence ────────────────────────────────────


def load_edit_capture(path: str | Path) -> dict | None:
    """Load a previously captured edit-request shape, or ``None``."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def save_edit_capture(path: str | Path, capture: dict) -> None:
    """Atomically persist a captured edit-request shape."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(capture, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


_MIN_REF_LEN = 12  # avoid matching short enum/category constants by accident


def build_capture_from_inputs(image_inputs: list, recent_sources: list[dict]) -> dict:
    """Build a capture template from a real ``imageInputs`` array.

    ``recent_sources`` are raw generated-image dicts the bot recently delivered.
    We locate which field in ``image_inputs[0]`` references the image by matching
    its string leaves against the stored identifiers — first as an exact value,
    then (the observed case) as a bare id embedded *inside* a stored value such
    as ``mediaStoreUri``. The matched slot is replaced with
    :data:`REF_PLACEHOLDER`; the recipe to reproduce the reference for future
    images (``extract``/``prefix``/``suffix``/``ref_source_key``) is recorded.

    Always returns a dict with a values-free ``schema``; ``resolved`` is True
    only when a reference slot was located.
    """
    entry = image_inputs[0] if image_inputs else {}
    schema = describe_schema(image_inputs)

    # Map every known identifier string -> the source key it came from.
    value_to_key: dict[str, str] = {}
    for src in recent_sources:
        if not isinstance(src, dict):
            continue
        for key, value in src.items():
            if isinstance(value, str) and value:
                value_to_key.setdefault(value, key)

    leaves = list(_string_leaves(entry))

    # Pass 1: exact match — the request used a value the bot stores verbatim.
    for path, leaf in leaves:
        if leaf in value_to_key:
            return _resolved_capture(
                entry, path, schema,
                extract="whole", ref_source_key=value_to_key[leaf],
            )

    # Pass 2: substring — the request used a bare id embedded in a stored value.
    for path, leaf in leaves:
        if len(leaf) < _MIN_REF_LEN:
            continue
        for stored_value, key in value_to_key.items():
            idx = stored_value.find(leaf)
            if idx < 0:
                continue
            prefix = stored_value[:idx]
            suffix = stored_value[idx + len(leaf):]
            return _resolved_capture(
                entry, path, schema,
                extract="substring", ref_source_key=key,
                prefix=prefix, suffix=suffix,
            )

    return {"version": 1, "resolved": False, "schema": schema}


def _resolved_capture(entry: dict, ref_path: list, schema: Any, **recipe: Any) -> dict:
    template = _deep_copy(entry)
    set_path(template, ref_path, REF_PLACEHOLDER)
    return {
        "version": 1,
        "resolved": True,
        "ref_path": ref_path,
        "template": template,
        "schema": schema,
        **recipe,
    }


def _string_leaves(node: Any, prefix: tuple = ()):
    """Yield ``(path, value)`` for every string leaf in ``node``."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _string_leaves(value, prefix + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _string_leaves(value, prefix + (index,))
    elif isinstance(node, str):
        yield list(prefix), node


def build_generation_payload(
    *,
    prompt: str,
    project_id: str | None,
    captcha_token: str,
    aspect: str,
    num_images: int,
    seed: int,
    session_id: str,
    image_inputs: list[dict] | None = None,
) -> dict:
    """Construct the ``flowMedia:batchGenerateImages`` request body.

    ``image_inputs`` is empty for a fresh generation and references a prior
    image (see :func:`build_image_inputs`) when editing.
    """
    inputs = list(image_inputs or [])
    return {
        "clientContext": {
            "recaptchaContext": {
                "token": captcha_token,
                "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB",
            },
            "sessionId": session_id,
            "projectId": project_id,
            "tool": "PINHOLE",
        },
        "requests": [
            {
                "seed": seed + i,
                "imageModelName": IMAGE_MODEL_NAME,
                "imageAspectRatio": aspect_code(aspect),
                "prompt": prompt,
                "imageInputs": [dict(entry) for entry in inputs],
            }
            for i in range(max(1, num_images))
        ],
    }


# ── video generation ──────────────────────────────────────────────────
# Verified from a real captured request (tools/capture_video.py).
# Endpoint: https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText
# The API is ASYNC — generation returns a batchId; video URLs come via polling.

VIDEO_ENDPOINT = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText"
VIDEO_FRAMES_ENDPOINT = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoStartAndEndImage"
VIDEO_POLL_ENDPOINT = "https://aisandbox-pa.googleapis.com/v1/video:batchCheckAsyncVideoGenerationStatus"

# Video download: the front-end (labs.google, NOT the API host) resolves a media
# id to the actual file via a tRPC redirect endpoint. Confirmed from the page's
# <video> src: https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=<media_id>
# A GET here (with the browser's labs.google cookies) 302-redirects to the real
# media file. Used for both video download and, by the same pattern, any media id.
VIDEO_MEDIA_REDIRECT_BASE = "https://labs.google/fx/api/trpc/media.getMediaUrlRedirect"


def video_media_redirect_url(media_id: str) -> str:
    """Build the labs.google redirect URL that resolves a media id to its file.

    Confirmed from the real page <video> src. A GET (with labs.google cookies)
    redirects to the actual media (video/gif/upscaled) bytes.
    """
    from urllib.parse import quote
    return f"{VIDEO_MEDIA_REDIRECT_BASE}?name={quote(media_id, safe='')}"

# Polling interval and timeout (seconds).
VIDEO_POLL_INTERVAL = 5.0
VIDEO_POLL_TIMEOUT  = 300

# Status constants (confirmed from real capture).
VIDEO_STATUS_SCHEDULED  = "MEDIA_GENERATION_STATUS_SCHEDULED"
VIDEO_STATUS_ACTIVE     = "MEDIA_GENERATION_STATUS_ACTIVE"
VIDEO_STATUS_SUCCESSFUL = "MEDIA_GENERATION_STATUS_SUCCESSFUL"
VIDEO_STATUS_FAILED     = "MEDIA_GENERATION_STATUS_FAILED"
VIDEO_TERMINAL_STATUSES = {VIDEO_STATUS_SUCCESSFUL, VIDEO_STATUS_FAILED}

# Friendly key → Google's internal videoModelKey.
# Confirmed from real captures:
#   omni-flash-4s → abra_t2v_4s    (capture_video.py, abort mode)
#   veo-lite      → veo_3_1_t2v_lite  (capture_video.py, no-abort mode)
# Remaining keys are pattern-inferred — run capture_video.py for each to confirm.
VIDEO_MODEL_KEYS: dict[str, str] = {
    "omni-flash-4s":   "abra_t2v_4s",        # confirmed
    "omni-flash-6s":   "abra_t2v_6s",        # pattern-inferred
    "omni-flash-8s":   "abra_t2v_8s",        # pattern-inferred
    "omni-flash-10s":  "abra_t2v_10s",       # pattern-inferred
    "veo-lite":        "veo_3_1_t2v_lite",   # confirmed
    "veo-fast":        "veo_3_1_t2v_fast",   # UNVERIFIED — run capture_video.py
    "veo-quality":     "veo_3_1_t2v_quality",# UNVERIFIED
}

VIDEO_ASPECT_MAP: dict[str, str] = {
    # Enum values confirmed from captured payload (VIDEO_ASPECT_RATIO_PORTRAIT seen).
    "landscape": "VIDEO_ASPECT_RATIO_LANDSCAPE",
    "portrait":  "VIDEO_ASPECT_RATIO_PORTRAIT",
    "square":    "VIDEO_ASPECT_RATIO_SQUARE",
    "16:9":      "VIDEO_ASPECT_RATIO_LANDSCAPE",
    "9:16":      "VIDEO_ASPECT_RATIO_PORTRAIT",
    "1:1":       "VIDEO_ASPECT_RATIO_SQUARE",
}


def video_model_key(model_key: str) -> str:
    """Resolve a friendly model id to Google's videoModelKey (default: omni-flash-4s).

    The catalog (``VIDEO_MODELS``) is the source of truth; ``VIDEO_MODEL_KEYS``
    is kept as a flat back-compat alias map.
    """
    mid = (model_key or "").lower().strip()
    meta = VIDEO_MODELS.get(mid)
    if meta:
        return meta["key"]
    return VIDEO_MODEL_KEYS.get(mid, VIDEO_MODELS["omni-flash-4s"]["key"])


def video_frames_model_key(model_key: str) -> str:
    """Resolve a friendly model id to the Frames/interpolation videoModelKey.

    Confirmed from a live Frames capture:
    ``veo-lite`` -> ``veo_3_1_interpolation_lite``.
    Other Veo tiers follow the same observed naming pattern but still need live
    confirmation before exposing a picker.
    """
    base = video_model_key(model_key)
    if base.startswith("veo_3_1_t2v_"):
        return base.replace("veo_3_1_t2v_", "veo_3_1_interpolation_", 1)
    return "veo_3_1_interpolation_lite"


def video_aspect_code(aspect_ratio: str) -> str:
    """Map a friendly aspect-ratio name to Google's video enum (default: landscape)."""
    return VIDEO_ASPECT_MAP.get(
        (aspect_ratio or "").strip().lower(),
        VIDEO_ASPECT_MAP["landscape"],
    )


# ── video model catalog + pricing ─────────────────────────────────────
# One ordered catalog drives BOTH the UI (labels/menu) and pricing, so a model
# can never appear without a price or vice-versa. Prices are operator-set
# (rationale in docs/MONETIZATION.md); 100 credits = $1. ``key`` is the verified
# or pattern-inferred Google videoModelKey (see VIDEO_MODEL_KEYS notes above).
# Video supports only 16:9 / 9:16 (no square), per the live UI.
#
# ``family`` groups the two-tier picker: choose family → choose variant.
VIDEO_MODELS: "OrderedDict[str, dict]" = OrderedDict([
    # Omni Flash — fast/cheap, duration is the variant axis.
    ("omni-flash-4s",  {"key": "abra_t2v_4s",  "family": "omni-flash", "duration": 4,  "price": 20,  "confirmed": True}),
    ("omni-flash-6s",  {"key": "abra_t2v_6s",  "family": "omni-flash", "duration": 6,  "price": 30,  "confirmed": False}),
    ("omni-flash-8s",  {"key": "abra_t2v_8s",  "family": "omni-flash", "duration": 8,  "price": 35,  "confirmed": False}),
    ("omni-flash-10s", {"key": "abra_t2v_10s", "family": "omni-flash", "duration": 10, "price": 45,  "confirmed": False}),
    # Veo 3.1 — quality tiers, fixed duration (8s observed for lite).
    ("veo-lite",       {"key": "veo_3_1_t2v_lite",    "family": "veo", "duration": 8, "price": 30,  "confirmed": True}),
    ("veo-fast",       {"key": "veo_3_1_t2v_fast",    "family": "veo", "duration": 8, "price": 60,  "confirmed": False}),
    ("veo-quality",    {"key": "veo_3_1_t2v_quality", "family": "veo", "duration": 8, "price": 290, "confirmed": False}),
])

VIDEO_INGREDIENTS_SURCHARGE = 10
VIDEO_FRAMES_SURCHARGE = 20

# Limits for "how many videos at once".
MIN_NUM_VIDEOS = 1
MAX_NUM_VIDEOS = 4
DEFAULT_NUM_VIDEOS = 1

# Video aspect ratios offered in the UI (square is image-only).
VIDEO_UI_ASPECTS = ("landscape", "portrait")


def video_model_meta(model_id: str) -> dict | None:
    """Return the catalog entry for a friendly model id, or ``None``."""
    return VIDEO_MODELS.get((model_id or "").lower().strip())


def video_price(model_id: str, num_videos: int = 1, mode: str = "text") -> int:
    """Credits for ``num_videos`` of ``model_id`` (per-video price × count).

    Unknown model ids fall back to the cheapest (omni-flash-4s) price so the
    user is never under-charged for a real generation.
    """
    meta = video_model_meta(model_id) or VIDEO_MODELS["omni-flash-4s"]
    surcharge = 0
    if mode == "ingredients":
        surcharge = VIDEO_INGREDIENTS_SURCHARGE
    elif mode == "frames":
        surcharge = VIDEO_FRAMES_SURCHARGE
    return (meta["price"] + surcharge) * clamp_num_videos(num_videos)


def clamp_num_videos(value: object, default: int = DEFAULT_NUM_VIDEOS) -> int:
    """Clamp a requested video count to the allowed range."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(MIN_NUM_VIDEOS, min(MAX_NUM_VIDEOS, n))


def video_models_in_family(family: str) -> list[tuple[str, dict]]:
    """Ordered ``(model_id, meta)`` pairs for a family (e.g. 'omni-flash', 'veo')."""
    return [(mid, m) for mid, m in VIDEO_MODELS.items() if m["family"] == family]


def video_families() -> list[str]:
    """Distinct families in catalog order (for the first picker screen)."""
    seen: list[str] = []
    for m in VIDEO_MODELS.values():
        if m["family"] not in seen:
            seen.append(m["family"])
    return seen


def _media_generation_id(source: dict | None) -> str | None:
    """Return the Flow mediaGenerationId used for video image references."""
    if not isinstance(source, dict):
        return None
    value = source.get("mediaGenerationId")
    if isinstance(value, str) and value:
        return value
    return None


def _video_frame_media_id(source: dict | None) -> str | None:
    """Return the mediaId required by the Frames endpoint."""
    if not isinstance(source, dict):
        return None
    for key in ("mediaId", "mediaID", "media_id", "id", "name"):
        value = source.get(key)
        if isinstance(value, str) and value:
            if value.startswith("fe_id_"):
                return value.removeprefix("fe_id_")
            return value
    value = source.get("mediaGenerationId")
    if isinstance(value, str) and value.startswith("fe_id_"):
        return value.removeprefix("fe_id_")
    return None


def build_video_reference_images(
    sources: list[dict] | None,
    max_inputs: int = 3,
) -> list[dict]:
    """Build candidate Flow asset refs for video Ingredients/R2V.

    Flow video references use the uploaded asset's ``mediaGenerationId`` rather
    than the shorter per-image ``mediaId`` used by image editing. The exact
    video endpoint field names are still uncaptured.
    """
    refs: list[dict] = []
    for source in sources or []:
        media_generation_id = _media_generation_id(source)
        if media_generation_id:
            refs.append({"mediaGenerationId": media_generation_id})
        if len(refs) >= max_inputs:
            break
    return refs


# The Frames endpoint expects cropCoordinates per frame (confirmed from the live
# batchAsyncGenerateVideoStartAndEndImage capture). The bot has no crop UI, so we
# default to the full frame; a source dict may carry its own "cropCoordinates".
FULL_FRAME_CROP = {"top": 0, "left": 0, "bottom": 1, "right": 1}


def _frame_crop(source: dict | None) -> dict:
    """Return cropCoordinates for a frame source (default: the full frame)."""
    if isinstance(source, dict):
        crop = source.get("cropCoordinates")
        if isinstance(crop, dict) and all(k in crop for k in ("top", "left", "bottom", "right")):
            return {k: crop[k] for k in ("top", "left", "bottom", "right")}
    return dict(FULL_FRAME_CROP)


def build_video_frame_images(
    start_source: dict | None,
    end_source: dict | None = None,
) -> tuple[dict | None, dict | None]:
    """Build start/end refs for the captured Frames endpoint.

    Each ref carries ``mediaId`` + ``cropCoordinates`` to match the live
    ``batchAsyncGenerateVideoStartAndEndImage`` request shape exactly.
    """
    start_id = _video_frame_media_id(start_source)
    end_id = _video_frame_media_id(end_source)
    start = {"mediaId": start_id, "cropCoordinates": _frame_crop(start_source)} if start_id else None
    end = {"mediaId": end_id, "cropCoordinates": _frame_crop(end_source)} if end_id else None
    return start, end


def build_video_payload(
    *,
    prompt: str,
    project_id: str | None,
    captcha_token: str,
    aspect: str,
    model_key: str,
    session_id: str,
    batch_id: str,
    reference_images: list[dict] | None = None,
    start_image: dict | None = None,
    end_image: dict | None = None,
) -> dict:
    """Construct a video generation request body.

    Text-to-video shape is verified from tools/capture_video.py. Frames shape is
    verified from a 2026-06-08 ``--frames`` capture and uses ``startImage`` /
    ``endImage`` with uploaded asset ``mediaId`` values.
    ``batch_id`` must be a fresh UUID4 for each request (generated by the caller).
    The API is async: the response contains a batchId, not video URLs directly.
    Ingredients/reference image arguments are intentionally not serialized until
    that separate endpoint shape is captured.
    """
    request = {
        "aspectRatio": video_aspect_code(aspect),
        "textInput": {
            "structuredPrompt": {
                "parts": [{"text": prompt}],
            },
        },
        "videoModelKey": video_frames_model_key(model_key) if (start_image or end_image) else video_model_key(model_key),
        "seed": secrets.randbelow(9000) + 1000,
        "metadata": {},
    }
    if start_image:
        request["startImage"] = dict(start_image)
    if end_image:
        request["endImage"] = dict(end_image)
    return {
        "mediaGenerationContext": {
            "batchId": batch_id,
            "audioFailurePreference": "BLOCK_SILENCED_VIDEOS",
        },
        "clientContext": {
            "projectId": project_id,
            "tool": "PINHOLE",
            "userPaygateTier": "PAYGATE_TIER_ONE",
            "sessionId": session_id,
            "recaptchaContext": {
                "token": captcha_token,
                "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB",
            },
        },
        "requests": [request],
        "useV2ModelConfig": True,
    }


def build_video_poll_payload(media_id: str, project_id: str) -> dict:
    """Construct the ``video:batchCheckAsyncVideoGenerationStatus`` request body.

    Verified from a real captured polling request.
    """
    return {"media": [{"name": media_id, "projectId": project_id}]}


def parse_video_gen_response(data: dict) -> dict | None:
    """Extract media_id and project_id from a ``batchAsyncGenerateVideoText`` response.

    Returns ``{"media_id": str, "project_id": str}`` or ``None`` if not found.
    Shape confirmed from real capture: top-level ``media`` list with ``name`` +
    ``projectId`` fields.
    """
    for item in data.get("media", []) or []:
        if not isinstance(item, dict):
            continue
        media_id   = item.get("name")
        project_id = item.get("projectId")
        if media_id and project_id:
            return {"media_id": media_id, "project_id": project_id}
    return None


def check_video_poll_status(data: dict) -> tuple[str, dict | None]:
    """Return ``(status_string, media_item)`` from a polling response.

    ``status_string`` is one of the ``VIDEO_STATUS_*`` constants.
    Returns ``("", None)`` if the status field is absent or the response is empty.
    Shape confirmed: ``media[0].mediaMetadata.mediaStatus.mediaGenerationStatus``.
    """
    for item in data.get("media", []) or []:
        if not isinstance(item, dict):
            continue
        status = (
            item.get("mediaMetadata", {})
                .get("mediaStatus", {})
                .get("mediaGenerationStatus", "")
        )
        if status:
            return status, item
    return "", None


def parse_video_batch_id(data: dict) -> str | None:
    """Extract the batchId from a batchAsyncGenerateVideoText response.

    The API is async — the initial response confirms the batch was accepted.
    Poll a separate status endpoint with this batchId to get video URLs.
    Exact response shape is UNVERIFIED — update after capturing a real response.
    """
    # Try common patterns for async job IDs
    for key in ("batchId", "operationId", "name", "id"):
        val = data.get(key)
        if isinstance(val, str) and val:
            return val
    # Some APIs nest it
    op = data.get("operation") or data.get("response") or {}
    if isinstance(op, dict):
        for key in ("batchId", "name", "id"):
            val = op.get(key)
            if isinstance(val, str) and val:
                return val
    return None


def parse_video_result(data: dict) -> list[str]:
    """Extract video URLs from a video generation response or poll result.

    Tries several plausible field-name variants. Returns empty list if the
    response format is unknown — capture a real poll response to verify.
    """
    urls: list[str] = []

    # Variant: {"responses": [{"generatedVideo": {"uri": ...}}]}
    for resp in data.get("responses", []) or []:
        if not isinstance(resp, dict):
            continue
        vid = resp.get("generatedVideo") or resp.get("videoOutput") or {}
        if isinstance(vid, dict):
            url = vid.get("uri") or vid.get("mediaStoreUri") or vid.get("fifeUrl")
            if url:
                urls.append(url)

    # Variant: {"media": [{"video": {"generatedVideo": {"uri": ...}}}]}
    for item in data.get("media", []) or []:
        if not isinstance(item, dict):
            continue
        vid = (item.get("video", {}) or {}).get("generatedVideo", {}) or {}
        if isinstance(vid, dict):
            url = vid.get("uri") or vid.get("fifeUrl") or vid.get("mediaStoreUri")
            if url:
                urls.append(url)

    return urls


# ── real-upscale request capture / replay ─────────────────────────────
# Flow's native upscale endpoint/shape is unknown, so (like the edit shape) we
# learn it once from a real request intercepted in the live browser, store a
# template with the media id / captcha / project / session swapped for
# placeholders, then replay it for any image. Nothing is guessed.

UPSCALE_REF = "__UP_MEDIA__"
UPSCALE_CAPTCHA = "__UP_CAPTCHA__"
UPSCALE_PROJECT = "__UP_PROJECT__"
UPSCALE_SESSION = "__UP_SESSION__"


def build_request_capture(url: str, body: Any, known_ids: set) -> dict:
    """Templatize a real upscale request so the bot can replay it for any image.

    Matches the referenced media id against ``known_ids`` (ids of recently
    delivered images); also placeholders the recaptcha token, projectId and
    sessionId. ``resolved`` is True only when the media id was located, so we
    never store a template we can't safely re-target.
    """
    schema = describe_schema(body)
    if not isinstance(body, (dict, list)):
        return {"resolved": False, "schema": schema}
    template = _deep_copy(body)
    matched = _placeholder_value(template, set(known_ids), UPSCALE_REF)
    _placeholder_keys(template)
    return {
        "resolved": bool(matched),
        "url": _templatize_url(url, set(known_ids)),
        "body": template,
        "schema": schema,
    }


def apply_request_capture(
    capture: dict,
    *,
    media_id: str,
    captcha: str,
    project_id: str,
    session_id: str,
) -> tuple[str, Any] | None:
    """Fill a captured upscale template → ``(url, body)`` ready to POST, else None."""
    if not capture or not capture.get("resolved"):
        return None
    mapping = {
        UPSCALE_REF: media_id or "",
        UPSCALE_CAPTCHA: captcha or "",
        UPSCALE_PROJECT: project_id or "",
        UPSCALE_SESSION: session_id or "",
    }
    url = capture.get("url") or ""
    for placeholder, value in mapping.items():
        url = url.replace(placeholder, value)
    body = _deep_copy(capture.get("body"))
    _fill_placeholders(body, mapping)
    return url, body


def _placeholder_value(node: Any, targets: set, placeholder: str) -> bool:
    """Replace any string leaf whose value is in ``targets`` with ``placeholder``."""
    found = False
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if isinstance(value, str) and value in targets:
                node[key] = placeholder
                found = True
            elif _placeholder_value(value, targets, placeholder):
                found = True
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if isinstance(value, str) and value in targets:
                node[index] = placeholder
                found = True
            elif _placeholder_value(value, targets, placeholder):
                found = True
    return found


def _placeholder_keys(node: Any) -> None:
    """Swap auth/project/session VALUES for placeholders, identified by key name."""
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if isinstance(value, str):
                if key == "token":
                    node[key] = UPSCALE_CAPTCHA
                elif key == "projectId":
                    node[key] = UPSCALE_PROJECT
                elif key == "sessionId":
                    node[key] = UPSCALE_SESSION
            else:
                _placeholder_keys(value)
    elif isinstance(node, list):
        for value in node:
            _placeholder_keys(value)


def _fill_placeholders(node: Any, mapping: dict) -> None:
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if isinstance(value, str) and value in mapping:
                node[key] = mapping[value]
            else:
                _fill_placeholders(value, mapping)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if isinstance(value, str) and value in mapping:
                node[index] = mapping[value]
            else:
                _fill_placeholders(value, mapping)


def _templatize_url(url: str, ids: set) -> str:
    out = re.sub(r"/projects/[^/]+", "/projects/" + UPSCALE_PROJECT, url or "")
    for media_id in ids:
        if media_id and media_id in out:
            out = out.replace(media_id, UPSCALE_REF)
    return out


# ── inline-button tokens ──────────────────────────────────────────────


def new_token() -> str:
    """Short, unguessable token for an inline button (16 hex chars)."""
    return secrets.token_hex(8)


def action_callback_data(action: str, token: str) -> str:
    """Build callback data ``<prefix><token>`` for a per-image action."""
    prefix = ACTION_PREFIXES.get(action)
    if prefix is None:
        raise ValueError(f"unknown action: {action}")
    return f"{prefix}{token}"


def parse_action_callback(data: str) -> tuple[str, str] | None:
    """Return ``(action, token)`` from callback data, or ``None`` if unknown."""
    if not isinstance(data, str):
        return None
    for action, prefix in ACTION_PREFIXES.items():
        if data.startswith(prefix):
            token = data[len(prefix):].strip()
            return (action, token) if token else None
    return None


def edit_callback_data(token: str) -> str:
    return f"{EDIT_CALLBACK_PREFIX}{token}"


def parse_edit_callback(data: str) -> str | None:
    """Return the token from ``edit:<token>`` callback data, else ``None``."""
    if not isinstance(data, str) or not data.startswith(EDIT_CALLBACK_PREFIX):
        return None
    token = data[len(EDIT_CALLBACK_PREFIX):].strip()
    return token or None


# ── edit registry ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class ImageRef:
    """Everything needed to edit a specific delivered image."""

    user_id: int
    project_id: str | None
    source: dict = field(default_factory=dict)
    prompt: str = ""
    aspect_ratio: str = "landscape"


@dataclass(frozen=True)
class VideoRef:
    """Everything needed to re-fetch a specific delivered video (download button).

    Stored in an :class:`ImageRegistry` instance (the registry doesn't type-check
    its contents) keyed by an inline-button token, validated per user at use time.
    """

    user_id: int
    project_id: str | None
    media_id: str
    prompt: str = ""
    model_id: str = ""
    aspect_ratio: str = "landscape"


class ImageRegistry:
    """Bounded token -> :class:`ImageRef` map for inline edit buttons.

    In-memory only: buttons from before a restart simply expire, which the bot
    reports gracefully. Bounded so a long-running process cannot grow without
    limit. Keyed per token (and validated per user at use time) so one user can
    never edit another user's image.
    """

    def __init__(self, max_entries: int = 5000) -> None:
        self._items: "OrderedDict[str, ImageRef]" = OrderedDict()
        self._max = max(1, int(max_entries))

    def add(self, ref: ImageRef) -> str:
        token = new_token()
        # Vanishingly unlikely collision, but keep tokens unique.
        while token in self._items:
            token = new_token()
        self._items[token] = ref
        while len(self._items) > self._max:
            self._items.popitem(last=False)
        return token

    def get(self, token: str) -> ImageRef | None:
        return self._items.get(token)

    def __len__(self) -> int:
        return len(self._items)


# ── per-user project persistence ──────────────────────────────────────


class UserProjectStore:
    """Persisted ``telegram_user_id -> flow_project_id`` map.

    Stored as JSON so each user keeps their own Flow project across restarts.
    Writes are atomic (temp file + ``os.replace``) so a crash mid-write cannot
    corrupt the map. Single-threaded asyncio access; no locking needed.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._data: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return
        if isinstance(parsed, dict):
            self._data = {
                str(key): str(value)
                for key, value in parsed.items()
                if value not in (None, "")
            }

    def get(self, user_id: int | str) -> str | None:
        return self._data.get(str(user_id))

    def set(self, user_id: int | str, project_id: str) -> None:
        if not project_id:
            return
        self._data[str(user_id)] = str(project_id)
        self._save()

    def remove(self, user_id: int | str) -> None:
        if str(user_id) in self._data:
            del self._data[str(user_id)]
            self._save()

    def as_dict(self) -> dict[str, str]:
        return dict(self._data)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._data, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self._path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise


# ── credits & pricing (monetization-strategist model) ─────────────────

PRICE_PER_IMAGE = 10        # 1 generated image = 10 credits
UPSCALE_PRICE = 5          # +0.5x of one image, rounded
STARTER_CREDITS = 50        # one-time grant on first /start
LOW_BALANCE_THRESHOLD = 20  # nudge to top up below this


def price_gen(num_images: int) -> int:
    """Credits for generating ``num_images`` images (10 each)."""
    return max(1, int(num_images)) * PRICE_PER_IMAGE


def action_price(action: str, num_images: int = 1) -> int:
    """Credits charged for an action.

    - ``gen``/``regen``: per-image (count chosen in the wizard).
    - ``revary``: image-to-image, ~2 images → priced as 2 images.
    - ``edit``/``myphoto``: 1 image.
    - ``up2x`` / ``realup``: premium add-on, +0.5x of one image.
    - ``dl_raw``: free.
    """
    if action in ("gen", "regen"):
        return price_gen(num_images)
    if action == "revary":
        return price_gen(2)
    if action in ("edit", "myphoto"):
        return price_gen(1)
    if action in ("up2x", "realup"):
        return UPSCALE_PRICE
    if action == "dl_raw":
        return 0
    return 0


# Telegram Stars top-up packs (id -> stars/credits/best-value flag).
STARS_PACKS = {
    "small": {"stars": 75, "credits": 100, "best": False},
    "medium": {"stars": 200, "credits": 290, "best": False},
    "large": {"stars": 450, "credits": 700, "best": True},
    "xl": {"stars": 900, "credits": 1500, "best": False},
}


def pack(pack_id: str) -> dict | None:
    return STARS_PACKS.get(pack_id)


def pack_label(pack_id: str) -> str:
    """Pack button label, e.g. ``700 кр · ~70 ген · 450⭐ 🔥 Выгодно``.

    Shows how many generations the credits buy (1 ген = PRICE_PER_IMAGE кр).
    """
    p = STARS_PACKS.get(pack_id)
    if not p:
        return pack_id
    gens = p["credits"] // PRICE_PER_IMAGE
    text = f"{p['credits']} кр · ~{gens} ген · {p['stars']}⭐"
    if p.get("best"):
        text += " 🔥 Выгодно"
    return text


class CreditStore:
    """Persisted per-user credit balances (atomic JSON, like UserProjectStore).

    Tracks a ``granted`` set so the one-time starter bonus is never re-granted.
    Single-threaded asyncio access; no locking needed.
    """

    def __init__(self, path: str | Path, starter: int = STARTER_CREDITS) -> None:
        self._path = Path(path)
        self._starter = int(starter)
        self._bal: dict[str, int] = {}
        self._granted: set[str] = set()
        self._load()

    def _load(self) -> None:
        try:
            parsed = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(parsed, dict):
            bal = parsed.get("balances", {})
            if isinstance(bal, dict):
                for key, value in bal.items():
                    try:
                        self._bal[str(key)] = int(value)
                    except (TypeError, ValueError):
                        continue
            granted = parsed.get("granted", [])
            if isinstance(granted, list):
                self._granted = {str(g) for g in granted}

    def balance(self, user_id: int | str) -> int:
        """Balance, granting the one-time starter bonus on first access."""
        key = str(user_id)
        if key not in self._granted:
            self._granted.add(key)
            self._bal[key] = self._bal.get(key, 0) + self._starter
            self._save()
        return self._bal.get(key, 0)

    def can_afford(self, user_id: int | str, amount: int) -> bool:
        return self.balance(user_id) >= int(amount)

    def charge(self, user_id: int | str, amount: int) -> bool:
        """Deduct ``amount`` if affordable; return whether it succeeded."""
        amount = int(amount)
        if amount <= 0:
            return True
        key = str(user_id)
        if self.balance(user_id) < amount:
            return False
        self._bal[key] -= amount
        self._save()
        return True

    def refund(self, user_id: int | str, amount: int) -> None:
        amount = int(amount)
        if amount <= 0:
            return
        key = str(user_id)
        self._bal[key] = self._bal.get(key, 0) + amount
        self._save()

    def add(self, user_id: int | str, amount: int) -> int:
        """Top up (purchase) and return the new balance."""
        key = str(user_id)
        self.balance(user_id)  # ensure starter applied
        self._bal[key] = self._bal.get(key, 0) + int(amount)
        self._save()
        return self._bal[key]

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"balances": self._bal, "granted": sorted(self._granted)}
        fd, tmp_name = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self._path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

