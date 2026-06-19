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

import asyncio
import json
import hashlib
import os
import re
import secrets
import tempfile
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Google Flow constants ─────────────────────────────────────────────

# Default image model (the bot's historical hardcode). Friendly ids live in
# IMAGE_MODELS below; this is the raw fallback enum.
IMAGE_MODEL_NAME = "GEM_PIX_2"

# Image model catalog (friendly id -> Google enum + RU label + credit surcharge).
# Verified imageModelName strings from a real batchGenerateImages capture
# (2026-06-09): GEM_PIX_2 = "Nano Banana 2" (default), NARWHAL = "Nano Banana Pro".
# Both cost 0 Google Flow credits; the surcharge is a pure retail upsell.
IMAGE_MODELS: "OrderedDict[str, dict]" = OrderedDict([
    ("nb2",   {"key": "GEM_PIX_2", "label": "Nano Banana 2",  "extra": 0}),
    ("nbpro", {"key": "NARWHAL",   "label": "Nano Banana Pro", "extra": 5}),
])
DEFAULT_IMAGE_MODEL = "nb2"

# Aspect ratios. Enums verified from real batchGenerateImages requests; 4:3 and
# 3:4 confirmed 2026-06-09 (LANDSCAPE_FOUR_THREE / PORTRAIT_THREE_FOUR).
ASPECT_MAP = {
    "landscape": "IMAGE_ASPECT_RATIO_LANDSCAPE",
    "portrait": "IMAGE_ASPECT_RATIO_PORTRAIT",
    "square": "IMAGE_ASPECT_RATIO_SQUARE",
    "landscape_43": "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE",
    "portrait_34": "IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR",
    "16:9": "IMAGE_ASPECT_RATIO_LANDSCAPE",
    "9:16": "IMAGE_ASPECT_RATIO_PORTRAIT",
    "1:1": "IMAGE_ASPECT_RATIO_SQUARE",
    "4:3": "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE",
    "3:4": "IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR",
}


def image_model_meta(model_id: str) -> dict | None:
    """Return the catalog entry for a friendly image-model id, or ``None``."""
    return IMAGE_MODELS.get((model_id or "").lower().strip())


def image_model_key(model_id: str) -> str:
    """Map a friendly image-model id to Google's ``imageModelName`` enum.

    Unknown ids fall back to the default model so generation never breaks.
    """
    meta = image_model_meta(model_id)
    return meta["key"] if meta else IMAGE_MODEL_NAME


def image_model_extra(model_id: str) -> int:
    """Per-image credit surcharge for the chosen model (0 for the default)."""
    meta = image_model_meta(model_id)
    if not meta:
        return 0
    if (model_id or "").lower().strip() == "nbpro":
        base = _price_override("image_nano", PRICE_PER_IMAGE)
        pro = _price_override("image_pro", PRICE_PER_IMAGE + int(meta["extra"]))
        return max(0, int(pro) - int(base))
    return int(meta["extra"])

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


IMAGE_UPLOAD_ENDPOINT = "https://aisandbox-pa.googleapis.com/v1/flow/uploadImage"


def build_upload_image_payload(
    *,
    project_id: str,
    image_bytes: str,
    mime_type: str,
    file_name: str,
) -> dict:
    """Construct the verified ``flow/uploadImage`` request body.

    The web app sends the image as a base64 string in ``imageBytes`` and marks it
    as a visible user upload. No recaptcha token is present in the captured
    request shape.
    """
    return {
        "clientContext": {
            "projectId": project_id,
            "tool": "PINHOLE",
        },
        "imageBytes": image_bytes,
        "isUserUploaded": True,
        "isHidden": False,
        "mimeType": mime_type,
        "fileName": file_name,
    }


def parse_upload_image_response(data: Any) -> dict | None:
    """Return an editable source dict from a ``flow/uploadImage`` response."""
    source = media_source_from_response(data)
    if not source:
        return None
    media = data.get("media") if isinstance(data, dict) else None
    if isinstance(media, dict):
        project_id = media.get("projectId")
        workflow_id = media.get("workflowId")
        if isinstance(project_id, str) and project_id:
            source["_project_id"] = project_id
        if isinstance(workflow_id, str) and workflow_id:
            source["workflowId"] = workflow_id
    return source


def find_media_source(obj: Any) -> dict | None:
    """Backwards-compatible alias returning an editable image source dict."""
    return media_source_from_response(obj)


# Resumable video-upload proxy used by Flow's web app (NOT the image file-input):
#   POST {VIDEO_UPLOAD_START_URL} (action=start)  -> {"sessionUrl", "status"}
#   PUT  {VIDEO_UPLOAD_PUT_URL}  (action=upload, raw bytes)
#       -> {"status":"final","mediaServerId":<uuid>,"workflowServerId":<uuid>,
#           "videoWidth":..,"videoHeight":..}
# Contract verified by tools/capture_video.py --upload-edit (seq 17–18).
VIDEO_UPLOAD_START_URL = "/fx/api/upload-video?action=start"
VIDEO_UPLOAD_PUT_URL = "/fx/api/upload-video?action=upload"


def upload_video_ids_from_response(obj: Any) -> dict | None:
    """Recover ``{mediaId, workflowId?, width?, height?}`` from a video-upload reply.

    Flow's resumable video upload finishes with a body that names the uploaded
    clip via ``mediaServerId`` and its workflow via ``workflowServerId`` — keys
    the image parser does not know (which is why ``upload_image`` returned 0).
    Falls back to the generic image extractor so an unexpected shape still yields
    a media id when one is present. Returns ``None`` if no media id is found.
    """
    media_id: str | None = None
    workflow_id: str | None = None
    width = height = None
    if isinstance(obj, dict):
        for key in ("mediaServerId", "mediaId", "mediaID", "media_id"):
            value = obj.get(key)
            if isinstance(value, str) and _UUID_RE.fullmatch(value):
                media_id = value
                break
        for key in ("workflowServerId", "workflowId", "workflow_id"):
            value = obj.get(key)
            if isinstance(value, str) and _UUID_RE.fullmatch(value):
                workflow_id = value
                break
        w, h = obj.get("videoWidth"), obj.get("videoHeight")
        if isinstance(w, int):
            width = w
        if isinstance(h, int):
            height = h
    if not media_id:
        media_id = extract_media_id(obj)
    if not media_id:
        return None
    out: dict[str, Any] = {"mediaId": media_id}
    if workflow_id:
        out["workflowId"] = workflow_id
    if width is not None:
        out["width"] = width
    if height is not None:
        out["height"] = height
    return out


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
    image_model: str = DEFAULT_IMAGE_MODEL,
) -> dict:
    """Construct the ``flowMedia:batchGenerateImages`` request body.

    ``image_inputs`` is empty for a fresh generation and references a prior
    image (see :func:`build_image_inputs`) when editing. ``image_model`` is a
    friendly id from :data:`IMAGE_MODELS` (resolved to ``imageModelName``).
    """
    inputs = list(image_inputs or [])
    model_name = image_model_key(image_model)
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
                "imageModelName": model_name,
                "imageAspectRatio": aspect_code(aspect),
                "prompt": prompt,
                "imageInputs": [dict(entry) for entry in inputs],
            }
            for i in range(max(1, num_images))
        ],
    }


# Real server-side image upscale — the UI's "Upscaled x2" download. Verified from
# a real ``flow/upsampleImage`` capture (2026-06-09): a SYNCHRONOUS POST that
# returns the upscaled image inline as base64 ``encodedImage`` (no polling, no new
# media id). Costs 0 Google Flow credits like all image ops.
IMAGE_UPSAMPLE_ENDPOINT = "https://aisandbox-pa.googleapis.com/v1/flow/upsampleImage"
IMAGE_UPSAMPLE_RESOLUTION = "UPSAMPLE_IMAGE_RESOLUTION_2K"  # captured "Upscaled x2"


def build_upsample_payload(
    *,
    media_id: str,
    project_id: str,
    captcha_token: str,
    session_id: str,
    target_resolution: str = IMAGE_UPSAMPLE_RESOLUTION,
) -> dict:
    """Construct the ``flow/upsampleImage`` request body (verified shape)."""
    return {
        "mediaId": media_id,
        "targetResolution": target_resolution,
        "clientContext": {
            "recaptchaContext": {
                "token": captcha_token,
                "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB",
            },
            "projectId": project_id,
            "tool": "PINHOLE",
            "userPaygateTier": "PAYGATE_TIER_ONE",
            "sessionId": session_id,
        },
    }


def parse_upsample_response(data: dict) -> str | None:
    """Return the base64 ``encodedImage`` from an upsampleImage response, or None."""
    enc = data.get("encodedImage") if isinstance(data, dict) else None
    return enc if isinstance(enc, str) and enc else None

# ── video generation ──────────────────────────────────────────────────
# Verified from a real captured request (tools/capture_video.py).
# Endpoint: https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText
# The API is ASYNC — generation returns a batchId; video URLs come via polling.

VIDEO_ENDPOINT = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText"
VIDEO_FRAMES_ENDPOINT = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoStartAndEndImage"
VIDEO_REFERENCE_ENDPOINT = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoReferenceImages"
VIDEO_EDIT_ENDPOINT = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoEditVideo"
VIDEO_EXTEND_ENDPOINT = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoExtendVideo"
VIDEO_POLL_ENDPOINT = "https://aisandbox-pa.googleapis.com/v1/video:batchCheckAsyncVideoGenerationStatus"

# Server-side full-video stitching (the service's REAL "download full" path).
# Verified from a real capture of Flow's download action on an extended video:
#   POST v1:runVideoFxConcatenation { inputVideos:[{mediaGenerationId, length(ns),
#        startTimeOffset, endTimeOffset}, ...] } -> { operation:{operation:{name}} }
#   POST v1:runVideoFxCheckConcatenationStatus { operation:{operation:{name}} } ->
#        { status, encodedVideo(base64 of the full mp4) } when SUCCESSFUL.
# The browser base64-decodes encodedVideo into a blob: URL — no local ffmpeg needed.
VIDEO_CONCAT_ENDPOINT = "https://aisandbox-pa.googleapis.com/v1:runVideoFxConcatenation"
VIDEO_CONCAT_STATUS_ENDPOINT = "https://aisandbox-pa.googleapis.com/v1:runVideoFxCheckConcatenationStatus"

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


# Google Flow's own provider-credit balance endpoint. Confirmed from a live
# capture (2026-06-17): GET with ``Authorization: Bearer <session bearer>``,
# response shape:
#   {"credits": 50, "userPaygateTier": "PAYGATE_TIER_NOT_PAID",
#    "sku": "G1_FREEMIUM", "serviceTier": "SERVICE_TIER_ENTRY",
#    "subscriptionCredits": 50}
CREDITS_ENDPOINT = "https://aisandbox-pa.googleapis.com/v1/credits"
# Public web-client key embedded in Flow's frontend JS bundle — restricted by
# HTTP referrer (labs.google), the same pattern as a Firebase web config key,
# not a per-user secret. Re-verify if Google rotates their frontend build.
FLOW_BROWSER_API_KEY = "REDACTED_CREDENTIAL"


def parse_credits_response(data: dict | None) -> dict | None:
    """Parse Flow's ``/v1/credits`` response into a small summary dict.

    Returns ``None`` for malformed/missing data so callers can treat it as
    "balance unknown" rather than crash. ``is_paid`` is a heuristic (absence
    of "NOT_PAID" in the tier string) since the exact paid-tier constant
    hasn't been observed from a live paid account yet.
    """
    if not isinstance(data, dict) or "credits" not in data:
        return None
    tier = data.get("userPaygateTier")
    return {
        "credits": data.get("credits"),
        "subscription_credits": data.get("subscriptionCredits"),
        "tier": tier,
        "sku": data.get("sku"),
        "service_tier": data.get("serviceTier"),
        "is_paid": "NOT_PAID" not in str(tier or ""),
    }

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


def _veo_tier(model_key: str) -> str:
    """Veo tier (lite/fast/quality) from a friendly model id; default fast."""
    mid = (model_key or "").lower().strip()
    for tier in ("lite", "fast", "quality"):
        if mid.endswith(tier):
            return tier
    return "fast"


def video_frames_model_key(model_key: str) -> str:
    """Resolve a friendly model id to the Frames/interpolation videoModelKey.

    Confirmed from live captures (frames key pattern, owner-confirmed):
    ``veo-lite`` -> ``veo_3_1_interpolation_lite``,
    ``veo-fast`` -> ``veo_3_1_interpolation_fast``,
    ``veo-quality`` -> ``veo_3_1_interpolation_quality``.
    Interpolation keys do NOT encode orientation.
    """
    return f"veo_3_1_interpolation_{_veo_tier(model_key)}"


def video_reference_model_key(model_key: str, aspect: str = "portrait") -> str:
    """Resolve a friendly model id to the Ingredients (r2v) videoModelKey.

    Confirmed from a live Ingredients capture (tools/video_raw_capture.json):
    ``veo-lite`` -> ``veo_3_1_r2v_lite`` — tier suffix ONLY, NO orientation.
    (Orientation is carried separately by ``aspectRatio`` in the request.) The
    ``aspect`` arg is kept for signature stability but no longer affects the key.
    """
    return f"veo_3_1_r2v_{_veo_tier(model_key)}"


def video_edit_model_key() -> str:
    """Google's native video-edit model key, captured from Flow Edit."""
    return "abra_edit"


def video_extend_model_key(model_key: str) -> str:
    """Resolve a friendly model id to the Extend videoModelKey.

    Captured from Flow Extend:
    ``veo-lite`` -> ``veo_3_1_extension_lite``. Other Veo tiers follow the
    same tier suffix pattern used by Frames.
    """
    return f"veo_3_1_extension_{_veo_tier(model_key)}"


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
    ("omni-flash-4s",  {"key": "abra_t2v_4s",  "family": "omni-flash", "duration": 4,  "price": 50,   "confirmed": True}),
    ("omni-flash-6s",  {"key": "abra_t2v_6s",  "family": "omni-flash", "duration": 6,  "price": 70,   "confirmed": False}),
    ("omni-flash-8s",  {"key": "abra_t2v_8s",  "family": "omni-flash", "duration": 8,  "price": 85,   "confirmed": False}),
    ("omni-flash-10s", {"key": "abra_t2v_10s", "family": "omni-flash", "duration": 10, "price": 100,  "confirmed": False}),
    # Veo 3.1 — quality tiers, fixed duration (8s observed for lite).
    ("veo-lite",       {"key": "veo_3_1_t2v_lite",    "family": "veo", "duration": 8, "price": 60,   "confirmed": True}),
    ("veo-fast",       {"key": "veo_3_1_t2v_fast",    "family": "veo", "duration": 8, "price": 120,  "confirmed": False}),
    ("veo-quality",    {"key": "veo_3_1_t2v_quality", "family": "veo", "duration": 8, "price": 450,  "confirmed": False}),
])

VIDEO_INGREDIENTS_SURCHARGE = 15
VIDEO_FRAMES_SURCHARGE = 25
VIDEO_PROMPT_EDIT_PRICE = 150

# Operator-set fixed price for one Extend action.
VIDEO_EXTEND_PRICE = 60

# Limits for "how many videos at once".
MIN_NUM_VIDEOS = 1
MAX_NUM_VIDEOS = 4
DEFAULT_NUM_VIDEOS = 1

# Video aspect ratios offered in the UI (square is image-only).
VIDEO_UI_ASPECTS = ("landscape", "portrait")

_VIDEO_PRICE_KEYS = {
    "omni-flash-4s": "omni_4s",
    "omni-flash-6s": "omni_6s",
    "omni-flash-8s": "omni_8s",
    "omni-flash-10s": "omni_10s",
    "veo-lite": "veo_lite",
    "veo-fast": "veo_fast",
    "veo-quality": "veo_quality",
}


def _price_override(key: str, default: int) -> int:
    """Read a runtime price override, falling back to the code default."""
    try:
        import config_store
        return int(config_store.get_price(key, int(default)))
    except Exception:
        return int(default)


def video_model_meta(model_id: str) -> dict | None:
    """Return the catalog entry for a friendly model id, or ``None``."""
    return VIDEO_MODELS.get((model_id or "").lower().strip())


def video_price(model_id: str, num_videos: int = 1, mode: str = "text") -> int:
    """Credits for ``num_videos`` of ``model_id`` (per-video price × count).

    Unknown model ids fall back to the cheapest (omni-flash-4s) price so the
    user is never under-charged for a real generation.
    """
    mid = (model_id or "").lower().strip()
    meta = video_model_meta(mid) or VIDEO_MODELS["omni-flash-4s"]
    base = _price_override(_VIDEO_PRICE_KEYS.get(mid, "omni_4s"), int(meta["price"]))
    surcharge = 0
    if mode == "ingredients":
        surcharge = _price_override("ingredients_extra", VIDEO_INGREDIENTS_SURCHARGE)
    elif mode == "frames":
        surcharge = _price_override("frames_extra", VIDEO_FRAMES_SURCHARGE)
    return (base + surcharge) * clamp_num_videos(num_videos)


def video_extend_price(model_id: str, extend_index: int) -> int:
    """Credits for extending a video."""
    return _price_override("extend_video", VIDEO_EXTEND_PRICE)


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
    max_inputs: int = 4,
) -> list[dict]:
    """Build Flow asset refs for video Ingredients (reference-to-video).

    Verified from a live ``--ingredients`` capture: each ref is
    ``{"mediaId": <uploaded asset id>, "imageUsageType": "IMAGE_USAGE_TYPE_ASSET"}``
    (the bare ``mediaId``, not ``mediaGenerationId``; ``fe_id_`` prefix stripped).
    """
    refs: list[dict] = []
    for source in sources or []:
        media_id = _video_frame_media_id(source)
        if media_id:
            refs.append({"mediaId": media_id, "imageUsageType": "IMAGE_USAGE_TYPE_ASSET"})
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

    Three shapes, all verified from live captures (tools/capture_video.py):
      * text-to-video — ``video_model_key`` (``abra_t2v_*`` / ``veo_3_1_t2v_*``);
      * Frames (``--frames``) — ``startImage``/``endImage`` (mediaId + crop),
        ``veo_3_1_interpolation_*`` key;
      * Ingredients (``--ingredients``) — ``referenceImages`` (mediaId +
        imageUsageType), aspect-encoded ``veo_3_1_r2v_*`` key.
    ``batch_id`` must be a fresh UUID4 for each request (generated by the caller).
    The API is async: the response contains a batchId, not video URLs directly.
    """
    if start_image or end_image:
        video_key = video_frames_model_key(model_key)
    elif reference_images:
        video_key = video_reference_model_key(model_key, aspect)
    else:
        video_key = video_model_key(model_key)
    request = {
        "aspectRatio": video_aspect_code(aspect),
        "textInput": {
            "structuredPrompt": {
                "parts": [{"text": prompt}],
            },
        },
        "videoModelKey": video_key,
        "seed": secrets.randbelow(9000) + 1000,
        "metadata": {},
    }
    if start_image:
        request["startImage"] = dict(start_image)
    if end_image:
        request["endImage"] = dict(end_image)
    if reference_images:
        request["referenceImages"] = [dict(r) for r in reference_images]
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


VIDEO_EDIT_FPS = 30  # web app: endFrameIndex = round(duration * 30); 4s clip → 120


def video_edit_end_frame(duration_s: float | None, default: int = 240) -> int:
    """endFrameIndex for a video edit: clip duration × 30 fps.

    The web-app bundle computes ``Math.round(duration * 30)`` from the clip's
    real duration. An endFrameIndex past the clip's end makes the edit job go
    ACTIVE → FAILED server-side, so the hardcoded 240 default is only safe for
    8s+ clips; pass the real duration whenever it is known.
    """
    if isinstance(duration_s, (int, float)) and duration_s > 0:
        return max(1, round(duration_s * VIDEO_EDIT_FPS))
    return default


_DURATION_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)s")


def video_duration_from_poll_item(item: Any) -> float | None:
    """Clip duration in seconds from a ``batchCheckAsyncVideoGenerationStatus`` item.

    Verified shape (upload-edit capture): ``video.videoOffset.endOffset`` and
    ``video.dimensions.length`` are both ``"4s"``-style strings.
    """
    if not isinstance(item, dict):
        return None
    video = item.get("video")
    if not isinstance(video, dict):
        return None
    for outer, inner in (("videoOffset", "endOffset"), ("dimensions", "length")):
        node = video.get(outer)
        value = node.get(inner) if isinstance(node, dict) else None
        if isinstance(value, str):
            m = _DURATION_RE.fullmatch(value.strip())
            if m:
                return float(m.group(1))
    return None


def build_video_edit_payload(
    *,
    prompt: str,
    project_id: str | None,
    captcha_token: str,
    aspect: str,
    session_id: str,
    batch_id: str,
    source_media_id: str,
    source_workflow_id: str,
    end_frame_index: int = 240,
) -> dict:
    """Construct the native Flow video Edit request body.

    Shape captured by ``tools/capture_video.py --edit``:
    endpoint ``video:batchAsyncGenerateVideoEditVideo``, model ``abra_edit``,
    source video under ``videoInput.mediaId``, and original workflow under
    ``metadata.workflowId``. ``end_frame_index`` must not exceed the clip's
    real frame count — see :func:`video_edit_end_frame`.
    """
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
        "requests": [
            {
                "aspectRatio": video_aspect_code(aspect),
                "textInput": {
                    "structuredPrompt": {
                        "parts": [{"text": prompt}],
                    },
                },
                "videoModelKey": video_edit_model_key(),
                "seed": secrets.randbelow(90000) + 10000,
                "metadata": {"workflowId": source_workflow_id},
                "videoInput": {
                    "mediaId": source_media_id,
                    "startFrameIndex": 0,
                    "endFrameIndex": end_frame_index,
                },
            }
        ],
    }


def build_video_extend_payload(
    *,
    prompt: str,
    project_id: str | None,
    captcha_token: str,
    aspect: str,
    model_key: str,
    session_id: str,
    batch_id: str,
    source_media_id: str,
    scene_id: str,
    position: int = 1,
) -> dict:
    """Construct the native Flow video Extend request body.

    Shape captured by ``tools/capture_video.py --extend``:
    endpoint ``video:batchAsyncGenerateVideoExtendVideo``, source video under
    ``videoInput.mediaId``, and scene linkage in both request metadata and
    ``mediaGenerationContext.sceneContext``.
    """
    scene_context = {"sceneId": scene_id, "position": position}
    return {
        "mediaGenerationContext": {
            "batchId": batch_id,
            "audioFailurePreference": "BLOCK_SILENCED_VIDEOS",
            "sceneContext": scene_context,
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
        "requests": [
            {
                "aspectRatio": video_aspect_code(aspect),
                "textInput": {
                    "structuredPrompt": {
                        "parts": [{"text": prompt}],
                    },
                },
                "videoModelKey": video_extend_model_key(model_key),
                "seed": secrets.randbelow(90000) + 10000,
                "metadata": {"sceneId": scene_id},
                "videoInput": {"mediaId": source_media_id},
            }
        ],
        "useV2ModelConfig": True,
    }


def flow_scene_create_url(project_id: str) -> str:
    """Endpoint used by Flow to create a scene from existing workflow ids."""
    from urllib.parse import quote
    return f"https://aisandbox-pa.googleapis.com/v1/flow/projects/{quote(project_id, safe='')}/scenes"


def flow_scene_workflows_url(scene_id: str, project_id: str) -> str:
    """Endpoint used by Flow to read workflows attached to a prepared scene."""
    from urllib.parse import quote, urlencode
    scene = quote(scene_id, safe="")
    query = urlencode({"sceneId": scene_id, "projectId": project_id})
    return f"https://aisandbox-pa.googleapis.com/v1/flow/scene/{scene}/workflows?{query}"


# ── full-video concatenation (server-side stitch) ──────────────────────

# Poll cadence for runVideoFxCheckConcatenationStatus. The real job completed
# within a few seconds; keep the ceiling generous for long timelines.
VIDEO_CONCAT_POLL_INTERVAL = 3.0
VIDEO_CONCAT_POLL_MAX = 40  # ~120s ceiling


def _duration_to_seconds(value: str | None) -> float:
    """Parse a Google duration string like ``"3.500s"`` / ``"8s"`` into seconds."""
    if not value:
        return 0.0
    try:
        return float(str(value).rstrip("s") or 0)
    except ValueError:
        return 0.0


def _seconds_to_offset(seconds: float) -> str:
    """Format seconds as a trimmed offset string, e.g. 3.5 -> ``"3.5s"``."""
    return f"{seconds:g}s"


def parse_scene_segments(data: dict) -> list[dict]:
    """Return ordered timeline segments from a scene-workflows response.

    Each entry: ``{"position", "media_id", "total", "start", "end"}``.
    Sorted by ``sceneWorkflowMetadata.position`` (missing position sorts as 0,
    matching the real concat request order). Verified from a real capture.
    """
    segments: list[dict] = []
    for item in data.get("sceneWorkflows", []) or []:
        if not isinstance(item, dict):
            continue
        meta = (item.get("workflow") or {}).get("metadata") or {}
        media_id = meta.get("primaryMediaId")
        if not isinstance(media_id, str) or not media_id:
            continue
        swm = item.get("sceneWorkflowMetadata") or {}
        try:
            position = int(swm.get("position", 0) or 0)
        except (TypeError, ValueError):
            position = 0
        total = swm.get("totalDuration") or "0s"
        segments.append({
            "position": position,
            "media_id": media_id,
            "total": total,
            "start": swm.get("startTime") or "0s",
            "end": swm.get("endTime") or total,
        })
    segments.sort(key=lambda s: s["position"])
    return segments


def build_concat_payload(segments: list[dict]) -> dict:
    """Build the ``runVideoFxConcatenation`` request body from ordered segments.

    ``length`` is the segment's full duration in nanoseconds; the offsets trim
    each segment. Verified field shapes from a real capture.
    """
    inputs = []
    for seg in segments:
        total_sec = _duration_to_seconds(seg.get("total"))
        inputs.append({
            "mediaGenerationId": seg["media_id"],
            "length": str(int(round(total_sec * 1_000_000_000))),
            "startTimeOffset": _seconds_to_offset(_duration_to_seconds(seg.get("start"))),
            "endTimeOffset": _seconds_to_offset(_duration_to_seconds(seg.get("end"))),
        })
    return {"inputVideos": inputs}


def parse_concat_operation_name(data: dict) -> str | None:
    """Extract the long-running operation name from a concat-start response."""
    name = ((data.get("operation") or {}).get("operation") or {}).get("name")
    return name if isinstance(name, str) and name else None


def build_concat_status_payload(operation_name: str) -> dict:
    """Build the ``runVideoFxCheckConcatenationStatus`` request body."""
    return {"operation": {"operation": {"name": operation_name}}}


def parse_concat_status(data: dict) -> tuple[str, str | None]:
    """Return ``(status, encoded_video_b64)`` from a concat-status response.

    ``encoded_video_b64`` is present (base64 of the full mp4) only when the
    status is SUCCESSFUL.
    """
    status = data.get("status", "") or ""
    encoded = data.get("encodedVideo")
    if not isinstance(encoded, str):
        encoded = None
    return status, encoded


def parse_video_scene_id(data: dict) -> str | None:
    """Extract ``sceneId`` from Flow scene-create / scene-workflows responses."""
    scene = data.get("scene")
    if isinstance(scene, dict):
        scene_id = scene.get("sceneId")
        if isinstance(scene_id, str) and scene_id:
            return scene_id
    for item in data.get("sceneWorkflows", []) or []:
        if not isinstance(item, dict):
            continue
        scene_id = item.get("sceneId")
        if isinstance(scene_id, str) and scene_id:
            return scene_id
    return None


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
            result = {"media_id": media_id, "project_id": project_id}
            workflow_id = item.get("workflowId")
            if isinstance(workflow_id, str) and workflow_id:
                result["workflow_id"] = workflow_id
            scene_id = item.get("sceneId")
            if isinstance(scene_id, str) and scene_id:
                result["scene_id"] = scene_id
            return result
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
    account_id: str | None = None  # аккаунт пула, где живёт project/media


@dataclass(frozen=True)
class VideoRef:
    """Everything needed to re-fetch a specific delivered video (download button).

    Stored in an :class:`ImageRegistry` instance (the registry doesn't type-check
    its contents) keyed by an inline-button token, validated per user at use time.
    """

    user_id: int
    project_id: str | None
    media_id: str
    source_media_id: str | None = None
    prompt: str = ""
    model_id: str = ""
    aspect_ratio: str = "landscape"
    mode: str = "text"
    prompt_edited: bool = False
    workflow_id: str | None = None
    scene_id: str | None = None
    extend_index: int = 0  # how many extends produced this clip (base video = 0)
    duration_s: float | None = None  # real clip length (uploads); drives edit endFrameIndex
    account_id: str | None = None  # аккаунт пула, где живёт project/media


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


# ── account pool (multi-account routing; см. docs/MONETIZATION.md §12) ─


@dataclass(frozen=True)
class FlowAccount:
    """Один Google-аккаунт пула: id + путь к его Chrome-профилю.

    Опциональные поля ёмкости переопределяют глобальные дефолты пула:
    ``image_capacity`` — максимум параллельных image-джобов на аккаунт;
    ``video_capacity`` — максимум параллельных video-джобов на аккаунт.
    None = использовать дефолт AccountPool.
    """

    id: str
    profile_dir: str
    browser_proxy_url: str | None = None
    api_proxy_url: str | None = None
    image_capacity: int | None = None
    video_capacity: int | None = None


def parse_flow_accounts(
    raw: str | None,
    *,
    default_id: str = "default",
    default_dir: str = "./google_profile",
) -> list[FlowAccount]:
    """Разобрать env ``FLOW_ACCOUNTS`` в список аккаунтов.

    Формат: записи через ``;`` или ``,``, каждая — ``id=путь_к_chrome_профилю``
    (разделитель именно ``=``: в Windows-путях есть ``:``). Запись без ``=`` —
    просто путь, id генерится ``accN``. Пустая/отсутствующая переменная — один
    аккаунт ``default_id``/``default_dir`` (поведение одиночного бота).
    Дубль id — выигрывает первая запись.
    """
    accounts: list[FlowAccount] = []
    seen: set[str] = set()
    for chunk in re.split(r"[;,]", raw or ""):
        entry = chunk.strip()
        if not entry:
            continue
        base, *option_parts = [part.strip() for part in entry.split("|")]
        if "=" in base:
            acc_id, _, path = base.partition("=")
            acc_id, path = acc_id.strip(), path.strip()
        else:
            acc_id, path = "", base
        if not path:
            continue
        if not acc_id:
            acc_id = f"acc{len(accounts) + 1}"
        if acc_id in seen:
            continue
        browser_proxy_url: str | None = None
        api_proxy_url: str | None = None
        image_capacity: int | None = None
        video_capacity: int | None = None
        for option in option_parts:
            if not option or "=" not in option:
                continue
            key, _, value = option.partition("=")
            key = key.strip().lower().replace("-", "_")
            value = value.strip()
            if not value:
                continue
            if key == "proxy":
                browser_proxy_url = value
                api_proxy_url = value
            elif key in {"browser_proxy", "browser_proxy_url"}:
                browser_proxy_url = value
            elif key in {"api_proxy", "api_proxy_url"}:
                api_proxy_url = value
            elif key == "image_capacity":
                try:
                    image_capacity = max(1, int(value))
                except ValueError:
                    pass
            elif key == "video_capacity":
                try:
                    video_capacity = max(0, int(value))
                except ValueError:
                    pass
        seen.add(acc_id)
        accounts.append(
            FlowAccount(
                id=acc_id,
                profile_dir=path,
                browser_proxy_url=browser_proxy_url,
                api_proxy_url=api_proxy_url,
                image_capacity=image_capacity,
                video_capacity=video_capacity,
            )
        )
    if not accounts:
        accounts.append(FlowAccount(id=default_id, profile_dir=default_dir))
    return accounts


class AccountPool:
    """Sticky-роутинг юзеров по аккаунтам + health/cooldown/failover.

    Контракт (docs/MONETIZATION.md §12): у каждого аккаунта есть статус, роутер
    выбирает здоровый, упавший уходит в кулдаун, аккаунт можно отключить
    вручную. Привязка user→account персистится (atomic JSON, как остальные
    сторы) — проекты юзера живут на «его» аккаунте. Health — в памяти:
    после рестарта все аккаунты считаются здоровыми (cooldown заново).

    Чистый класс: часы инжектируются (``clock``), I/O — только собственный
    JSON-стор. Один аккаунт в пуле никогда не блокируется кулдауном (падения
    единственного аккаунта почти наверняка системные: лучше попытаться, чем
    молча отказывать всем).
    """

    def __init__(
        self,
        accounts: list[FlowAccount],
        store_path: str | Path | None = None,
        *,
        max_failures: int = 3,
        cooldown_sec: float = 600.0,
        default_image_capacity: int = 2,
        default_video_capacity: int = 1,
        clock=time.monotonic,
    ) -> None:
        if not accounts:
            raise ValueError("AccountPool needs at least one account")
        self._accounts: "OrderedDict[str, FlowAccount]" = OrderedDict(
            (a.id, a) for a in accounts
        )
        self._max_failures = max(1, int(max_failures))
        self._cooldown_sec = float(cooldown_sec)
        self._default_image_capacity = max(1, int(default_image_capacity))
        self._default_video_capacity = max(0, int(default_video_capacity))
        self._clock = clock
        self._path = Path(store_path) if store_path else None
        self._assign: dict[str, str] = {}
        self._health: dict[str, dict] = {
            a.id: {"fails": 0, "cooldown_until": 0.0, "disabled": False,
                   "video_allowed": True}
            for a in accounts
        }
        # Capacity tracking (runtime-only; resets on restart).
        # Semaphores are created lazily on first use so unit-tests don't need a
        # running event loop when constructing AccountPool.
        self._image_sems: dict[str, asyncio.Semaphore] = {}
        self._video_sems: dict[str, asyncio.Semaphore] = {}
        self._active_image: dict[str, int] = {a.id: 0 for a in accounts}
        self._active_video: dict[str, int] = {a.id: 0 for a in accounts}
        self._load()

    # ── состав пула ────────────────────────────────────────────────────

    def account_ids(self) -> list[str]:
        return list(self._accounts)

    def get(self, account_id: str) -> FlowAccount | None:
        return self._accounts.get(account_id)

    def __len__(self) -> int:
        return len(self._accounts)

    # ── health ─────────────────────────────────────────────────────────

    def is_available(self, account_id: str) -> bool:
        h = self._health.get(account_id)
        if h is None:
            return False
        if h["disabled"]:
            return False
        return self._clock() >= h["cooldown_until"]

    def mark_success(self, account_id: str) -> None:
        h = self._health.get(account_id)
        if h is not None:
            h["fails"] = 0
            h["cooldown_until"] = 0.0

    def mark_failure(self, account_id: str) -> bool:
        """Учесть сбой; вернуть True, если аккаунт ушёл в кулдаун."""
        h = self._health.get(account_id)
        if h is None:
            return False
        h["fails"] += 1
        if h["fails"] >= self._max_failures:
            h["cooldown_until"] = self._clock() + self._cooldown_sec
            h["fails"] = 0
            return True
        return False

    def mark_cooldown(self, account_id: str) -> bool:
        """Force a bounded runtime cooldown for a strong account-health signal."""
        h = self._health.get(account_id)
        if h is None:
            return False
        h["fails"] = 0
        h["cooldown_until"] = self._clock() + self._cooldown_sec
        return True

    def reset_failures(self, account_id: str) -> bool:
        """Clear failure counter and cooldown; keep disabled state intact.

        Returns True if account id is known, False otherwise.
        """
        h = self._health.get(account_id)
        if h is None:
            return False
        h["fails"] = 0
        h["cooldown_until"] = 0.0
        return True

    def set_disabled(self, account_id: str, disabled: bool) -> bool:
        """Ручное отключение/включение аккаунта; True если id известен."""
        h = self._health.get(account_id)
        if h is None:
            return False
        h["disabled"] = bool(disabled)
        if not disabled:
            h["fails"] = 0
            h["cooldown_until"] = 0.0
        self._save()
        return True

    def set_video_allowed(self, account_id: str, allowed: bool) -> bool:
        """Разрешить/запретить видео на аккаунте; True если id известен.

        Персистируется в state-файле — переживает рестарт.
        """
        h = self._health.get(account_id)
        if h is None:
            return False
        h["video_allowed"] = bool(allowed)
        self._save()
        return True

    def is_video_capable(self, account_id: str) -> bool:
        """True если аккаунт доступен (не в кулдауне/disabled) и может видео."""
        if not self.is_available(account_id):
            return False
        h = self._health.get(account_id)
        return bool(h and h.get("video_allowed", True))

    def is_image_only(self, account_id: str) -> bool:
        h = self._health.get(account_id)
        return bool(h and not h.get("video_allowed", True))

    # ── capacity control ───────────────────────────────────────────────

    def _image_cap(self, account_id: str) -> int:
        acc = self._accounts.get(account_id)
        cap = acc.image_capacity if (acc and acc.image_capacity is not None) else self._default_image_capacity
        return max(1, cap)

    def _video_cap(self, account_id: str) -> int:
        acc = self._accounts.get(account_id)
        cap = acc.video_capacity if (acc and acc.video_capacity is not None) else self._default_video_capacity
        return max(0, cap)

    def _image_sem(self, account_id: str) -> asyncio.Semaphore:
        """Lazy-init semaphore for image slots on this account."""
        if account_id not in self._image_sems:
            self._image_sems[account_id] = asyncio.Semaphore(self._image_cap(account_id))
        return self._image_sems[account_id]

    def _video_sem(self, account_id: str) -> asyncio.Semaphore:
        """Lazy-init semaphore for video slots on this account."""
        if account_id not in self._video_sems:
            cap = self._video_cap(account_id)
            self._video_sems[account_id] = asyncio.Semaphore(max(1, cap))
        return self._video_sems[account_id]

    def has_image_capacity(self, account_id: str) -> bool:
        """Non-blocking check: True if an image slot is available right now."""
        sem = self._image_sems.get(account_id)
        if sem is None:
            return True  # not yet created → semaphore hasn't been exhausted
        return sem._value > 0  # CPython internal; stable since 3.10

    def has_video_capacity(self, account_id: str) -> bool:
        """Non-blocking check: True if a video slot is available right now."""
        sem = self._video_sems.get(account_id)
        if sem is None:
            return True
        return sem._value > 0

    @asynccontextmanager
    async def image_slot(self, account_id: str):
        """Acquire an image job slot (blocks if account is at capacity).

        Always releases in ``finally`` — safe across exceptions, timeouts, and
        failover ``continue``/``return`` paths.
        """
        sem = self._image_sem(account_id)
        async with sem:
            self._active_image[account_id] = self._active_image.get(account_id, 0) + 1
            try:
                yield
            finally:
                self._active_image[account_id] = max(
                    0, self._active_image.get(account_id, 1) - 1
                )

    @asynccontextmanager
    async def video_slot(self, account_id: str):
        """Acquire a video job slot (blocks if account is at capacity).

        Always releases in ``finally``.
        """
        sem = self._video_sem(account_id)
        async with sem:
            self._active_video[account_id] = self._active_video.get(account_id, 0) + 1
            try:
                yield
            finally:
                self._active_video[account_id] = max(
                    0, self._active_video.get(account_id, 1) - 1
                )

    # ── роутинг ────────────────────────────────────────────────────────

    def assigned_to(self, user_id: int | str) -> str | None:
        return self._assign.get(str(user_id))

    def pick_for(self, user_id: int | str) -> str | None:
        """Аккаунт для джобы юзера (sticky) или None, если весь пул недоступен.

        Один аккаунт в пуле возвращается всегда (см. docstring класса).
        Sticky-привязка переезжает на наименее загруженный живой аккаунт,
        только когда «свой» недоступен (failover; проект пересоздаётся там).
        """
        key = str(user_id)
        if len(self._accounts) == 1:
            only = next(iter(self._accounts))
            if self._assign.get(key) != only:
                self._assign[key] = only
                self._save()
            return only
        sticky = self._assign.get(key)
        if sticky and self.is_available(sticky):
            return sticky
        candidates = [aid for aid in self._accounts if self.is_available(aid)]
        if not candidates:
            return None
        loads: dict[str, int] = {aid: 0 for aid in self._accounts}
        for assigned in self._assign.values():
            if assigned in loads:
                loads[assigned] += 1
        best = min(candidates, key=lambda aid: loads[aid])
        self._assign[key] = best
        self._save()
        return best

    def pick_for_image(
        self,
        user_id: int | str,
        *,
        prefer_image_only: bool = False,
        exclude: set[str] | None = None,
    ) -> str | None:
        """Pick an account for image work.

        Normal image generation keeps the regular sticky route. Upload-based
        image editing can prefer accounts marked ``video_allowed=False`` so paid
        video-capable accounts keep more quota for video jobs.
        """
        excluded = set(exclude or set())
        if not prefer_image_only and not excluded:
            return self.pick_for(user_id)
        key = str(user_id)
        sticky = self._assign.get(key)
        if not prefer_image_only and sticky and sticky not in excluded and self.is_available(sticky):
            return sticky

        candidates = [
            aid for aid in self._accounts
            if aid not in excluded and self.is_available(aid) and self.is_image_only(aid)
        ]
        if not candidates:
            candidates = [
                aid for aid in self._accounts
                if aid not in excluded and self.is_available(aid)
            ]
        if not candidates:
            return None
        if sticky in candidates:
            return sticky
        loads: dict[str, int] = {aid: 0 for aid in self._accounts}
        for assigned in self._assign.values():
            if assigned in loads:
                loads[assigned] += 1
        best = min(candidates, key=lambda aid: loads[aid])
        self._assign[key] = best
        self._save()
        return best

    def pick_for_video(self, user_id: int | str) -> str | None:
        """Аккаунт для видео-джобы — только среди video_capable.

        Не изменяет sticky-привязку юзера (та остаётся для картинок).
        Если sticky-аккаунт юзера может видео — используем его (consistency).
        Иначе — наименее загруженный video-capable аккаунт без записи в assign.
        """
        key = str(user_id)
        sticky = self._assign.get(key)
        if sticky and self.is_video_capable(sticky):
            return sticky
        candidates = [aid for aid in self._accounts if self.is_video_capable(aid)]
        if not candidates:
            return None
        loads: dict[str, int] = {aid: 0 for aid in self._accounts}
        for assigned in self._assign.values():
            if assigned in loads:
                loads[assigned] += 1
        return min(candidates, key=lambda aid: loads[aid])

    def status(self) -> list[dict]:
        """Срез состояния пула для админ-отчёта (без секретов)."""
        now = self._clock()
        loads: dict[str, int] = {aid: 0 for aid in self._accounts}
        for assigned in self._assign.values():
            if assigned in loads:
                loads[assigned] += 1
        out = []
        for aid in self._accounts:
            h = self._health[aid]
            out.append({
                "id": aid,
                "disabled": h["disabled"],
                "video_allowed": h.get("video_allowed", True),
                "cooldown_left": max(0, int(h["cooldown_until"] - now)),
                "fails": h["fails"],
                "users": loads[aid],
                "active_image_jobs": self._active_image.get(aid, 0),
                "active_video_jobs": self._active_video.get(aid, 0),
                "image_capacity": self._image_cap(aid),
                "video_capacity": self._video_cap(aid),
            })
        return out

    # ── персистентность привязок ───────────────────────────────────────

    def _load(self) -> None:
        if self._path is None:
            return
        try:
            parsed = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(parsed, dict):
            assign = parsed.get("assignments", {})
            if isinstance(assign, dict):
                # Привязки к выбывшим из конфига аккаунтам отбрасываем — юзер
                # просто получит новый аккаунт (и новый проект) при следующей джобе.
                self._assign = {
                    str(k): str(v) for k, v in assign.items()
                    if str(v) in self._accounts
                }
            # Восстанавливаем флаги video_allowed (персистируем только False-записи)
            video_cfg = parsed.get("video_allowed", {})
            if isinstance(video_cfg, dict):
                for acc_id, allowed in video_cfg.items():
                    h = self._health.get(str(acc_id))
                    if h is not None:
                        h["video_allowed"] = bool(allowed)
            # Восстанавливаем ручные отключения (disabled=True)
            disabled_cfg = parsed.get("disabled", [])
            if isinstance(disabled_cfg, list):
                for acc_id in disabled_cfg:
                    h = self._health.get(str(acc_id))
                    if h is not None:
                        h["disabled"] = True

    def _save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Сохраняем только аккаунты у которых video_allowed=False (остальные — дефолт True)
        video_cfg = {
            aid: h["video_allowed"]
            for aid, h in self._health.items()
            if not h.get("video_allowed", True)
        }
        # Сохраняем список вручную отключённых аккаунтов (disabled=True)
        disabled_list = [
            aid for aid, h in self._health.items()
            if h.get("disabled", False)
        ]
        payload: dict = {"assignments": self._assign}
        if video_cfg:
            payload["video_allowed"] = video_cfg
        if disabled_list:
            payload["disabled"] = disabled_list
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


# ── credits & pricing (monetization-strategist model) ─────────────────

PRICE_PER_IMAGE = 10        # 1 generated image = 10 credits
IMAGE_EDIT_PRICE = 15       # edit uploaded/generated photo
UPSCALE_PRICE = 5          # +0.5x of one image, rounded
SELLER_SERIES_PRICES = {3: 30, 5: 45, 8: 70}
STARTER_CREDITS = 30        # one-time grant on first /start (balanced: 3 free images)
LOW_BALANCE_THRESHOLD = 20  # nudge to top up below this


def price_gen(num_images: int) -> int:
    """Credits for generating ``num_images`` images (10 each)."""
    return max(1, int(num_images)) * _price_override("image_nano", PRICE_PER_IMAGE)


def action_price(action: str, num_images: int = 1) -> int:
    """Credits charged for an action.

    - ``gen``/``regen``: per-image (count chosen in the wizard).
    - ``revary``: image-to-image, ~2 images → priced as 2 images.
    - ``edit``/``myphoto``: photo edit price.
    - ``mp_series``: seller marketplace slide-series bundle.
    - ``up2x`` / ``realup``: premium add-on, +0.5x of one image.
    - ``dl_raw``: free.
    """
    if action in ("gen", "regen"):
        return price_gen(num_images)
    if action == "revary":
        return price_gen(2)
    if action in ("edit", "myphoto"):
        return _price_override("edit_photo", IMAGE_EDIT_PRICE)
    if action == "mp_series":
        try:
            count = int(num_images)
        except (TypeError, ValueError):
            return 0
        price = SELLER_SERIES_PRICES.get(count)
        if price is None:
            return 0
        return _price_override(f"seller_series_{count}", price)
    if action in ("up2x", "realup"):
        return _price_override("upscale", UPSCALE_PRICE)
    if action == "video_prompt_edit":
        return _price_override("edit_video", VIDEO_PROMPT_EDIT_PRICE)
    if action == "dl_raw":
        return 0
    return 0


# Telegram Stars top-up packs (id -> stars/credits/best-value flag).
# ``trial`` is a low-barrier first-purchase pack; ``test`` is an admin-only
# 1-star pack for verifying the Stars payment pipeline end-to-end (hidden from
# normal users via ``test: True``).
STARS_PACKS = {
    "trial":  {"stars": 35,  "credits": 45,   "best": False},
    "small":  {"stars": 75,  "credits": 100,  "best": False},
    "medium": {"stars": 200, "credits": 290,  "best": False},
    "large":  {"stars": 450, "credits": 700,  "best": True},
    "xl":     {"stars": 900, "credits": 1500, "best": False},
    "test":   {"stars": 1,   "credits": 10,   "best": False, "test": True},
    # Seller packs (shown only when BOT_MODE=seller). Sized around a card =
    # ~6 slides (60 кр); see docs/SELLER_BOT_PLAN.md §7.
    "s_card":   {"stars": 45,  "credits": 60,   "best": False, "seller": True},
    "s_5cards": {"stars": 205, "credits": 300,  "best": False, "seller": True},
    "s_shop":   {"stars": 450, "credits": 700,  "best": True,  "seller": True},
    "s_shopxl": {"stars": 900, "credits": 1500, "best": False, "seller": True},
}


def pack(pack_id: str) -> dict | None:
    return STARS_PACKS.get(pack_id)


def public_pack_ids(include_test: bool = False, seller: bool = False) -> list[str]:
    """Pack ids for the top-up menu.

    ``seller`` selects the seller-bot pack set; otherwise the consumer set is
    returned. The admin-only ``test`` pack is included only with ``include_test``.
    """
    out = []
    for pid, p in STARS_PACKS.items():
        if p.get("test") and not include_test:
            continue
        if p.get("test"):
            out.append(pid)
            continue
        if bool(p.get("seller")) != bool(seller):
            continue
        out.append(pid)
    return out


def pack_label(pack_id: str) -> str:
    """Pack button label, e.g. ``700 кр · ~70 ген 🔥 Выгодно``.

    Shows how many generations the credits buy (1 ген = PRICE_PER_IMAGE кр).
    """
    p = STARS_PACKS.get(pack_id)
    if not p:
        return pack_id
    gens = p["credits"] // PRICE_PER_IMAGE
    if p.get("test"):
        return f"🧪 Тест · {p['credits']} кр"
    text = f"{p['credits']} кр · ~{gens} ген"
    if p.get("best"):
        text += " 🔥 Выгодно"
    return text


def robokassa_hash(data: str, algorithm: str = "md5") -> str:
    """Return a Robokassa-compatible hex digest for ``data``."""
    algo = (algorithm or "md5").strip().lower().replace("-", "")
    if algo not in {"md5", "sha1", "sha256", "sha384", "sha512"}:
        raise ValueError(f"unsupported Robokassa hash algorithm: {algorithm!r}")
    h = hashlib.new(algo)
    h.update(data.encode("utf-8"))
    return h.hexdigest()


def robokassa_shp_suffix(shp_params: dict[str, object] | None = None) -> str:
    """Serialize Robokassa ``Shp_*`` params in the required sorted order."""
    if not shp_params:
        return ""
    parts = []
    for key in sorted(shp_params):
        if key.startswith("Shp_"):
            parts.append(f"{key}={shp_params[key]}")
    return "".join(f":{part}" for part in parts)


def robokassa_payment_signature(
    merchant_login: str,
    out_sum: str,
    inv_id: str | int,
    password1: str,
    *,
    shp_params: dict[str, object] | None = None,
    receipt: str | None = None,
    algorithm: str = "md5",
) -> str:
    """SignatureValue for redirecting the user to Robokassa payment."""
    parts = [merchant_login, out_sum, str(inv_id)]
    if receipt:
        parts.append(receipt)
    parts.append(password1)
    base = ":".join(parts) + robokassa_shp_suffix(shp_params)
    return robokassa_hash(base, algorithm)


def robokassa_result_signature(
    out_sum: str,
    inv_id: str | int,
    password2: str,
    *,
    shp_params: dict[str, object] | None = None,
    algorithm: str = "md5",
) -> str:
    """Expected SignatureValue for Robokassa ResultURL callbacks."""
    base = f"{out_sum}:{inv_id}:{password2}" + robokassa_shp_suffix(shp_params)
    return robokassa_hash(base, algorithm)


ROBOKASSA_PACK_AMOUNTS_RUB = {
    "trial": "45.00",
    "small": "90.00",
    "medium": "235.00",
    "large": "530.00",
    "xl": "1050.00",
    # Seller packs (docs/SELLER_BOT_PLAN.md §7).
    "s_card": "55.00",
    "s_5cards": "240.00",
    "s_shop": "520.00",
    "s_shopxl": "1020.00",
}


def robokassa_pack_amount(pack_id: str, rub_per_star: float, discount_pct: float = 0.0) -> str:
    """RUB amount for a Robokassa top-up.

    Public packs use a fixed rounded card/SBP price grid. Admin or future packs
    still fall back to Stars-derived pricing so test payments keep working.
    """
    p = pack(pack_id)
    if not p:
        raise ValueError(f"unknown pack: {pack_id!r}")
    fixed = ROBOKASSA_PACK_AMOUNTS_RUB.get(pack_id)
    if fixed:
        return fixed
    discount = max(0.0, min(float(discount_pct or 0.0), 95.0))
    amount = float(p["stars"]) * float(rub_per_star) * (1.0 - discount / 100.0)
    return f"{amount:.2f}"


# ── Referral program (economics in docs/REFERRAL.md) ──────────────────
# Mutually-exclusive first-payment tiers (only the highest applicable fires),
# then an ongoing % of every later top-up. State lives in metrics.db.
REFERRAL_PARAM_PREFIX = "ref_"
REFERRAL_TIER1_BONUS = 20      # credits — any first purchase below TIER2 stars
REFERRAL_TIER2_STARS = 200     # >100₽ ≈ medium pack
REFERRAL_TIER2_BONUS = 30
REFERRAL_TIER3_STARS = 450     # >500₽ ≈ large pack
REFERRAL_TIER3_BONUS = 50
REFERRAL_ONGOING_PCT = 0.05    # fraction of credits_issued (floor), every later top-up
REFERRAL_DAILY_CAP_CREDITS = 500  # max referral credits to one referrer per day
# Привязка реферала действует ограниченное время: спустя столько дней с момента
# приглашения рефереру больше ничего не начисляется (ни разовый бонус, ни %).
# Гасит само-рефералку «два аккаунта = вечная скидка» и держит экономику в плюсе.
REFERRAL_REWARD_WINDOW_DAYS = 90  # ≈ 3 месяца


def referral_milestone_bonus(stars_paid: int) -> int:
    """First-payment milestone bonus for the referrer (single highest tier)."""
    stars = max(0, int(stars_paid or 0))
    if stars >= REFERRAL_TIER3_STARS:
        return REFERRAL_TIER3_BONUS
    if stars >= REFERRAL_TIER2_STARS:
        return REFERRAL_TIER2_BONUS
    if stars > 0:
        return REFERRAL_TIER1_BONUS
    return 0


def referral_ongoing_bonus(credits_issued: int) -> int:
    """Ongoing referral reward = floor(REFERRAL_ONGOING_PCT * credits_issued)."""
    return int(max(0, int(credits_issued or 0)) * REFERRAL_ONGOING_PCT)


def referral_window_cutoff_arg(window_days: int = REFERRAL_REWARD_WINDOW_DAYS) -> str:
    """SQLite datetime modifier marking the start of the active referral window."""
    return f"-{max(0, int(window_days))} days"


# ── Channel attribution (рекламные deep-link'и) ───────────────────────
# Каждому каналу — своя ссылка t.me/bot?start=seed_<канал>. По ней считаем,
# откуда пришёл юзер (first-touch). Это НЕ рефералка: денег никому не начисляет,
# только атрибуция трафика. Слаг — ярлык канала, который выбирает оператор.
CHANNEL_PARAM_PREFIX = "seed_"
_CHANNEL_SEED_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


def parse_channel_seed(payload: str) -> str | None:
    """Достать ярлык канала из start-пэйлоада ``seed_<канал>`` (или None).

    Telegram разрешает в start-параметре только ``[A-Za-z0-9_-]`` (≤64). Слаг
    канонизируем в нижний регистр (чтобы «Kanal» и «kanal» считались одним
    каналом) и валидируем — мусор/пустой/слишком длинный → None (не атрибутируем).
    """
    if not payload or not payload.startswith(CHANNEL_PARAM_PREFIX):
        return None
    slug = payload[len(CHANNEL_PARAM_PREFIX):].strip().lower()
    return slug if _CHANNEL_SEED_RE.match(slug) else None


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


class CreditStoreSQLite:
    """SQLite-backed credit store with the same public API as :class:`CreditStore`.

    Delegates to the ``credits_*`` functions in :mod:`metrics`.  The ``path``
    parameter is accepted but ignored (kept only so callers can swap stores
    without changing their constructor call).
    """

    def __init__(self, path, starter: int = STARTER_CREDITS) -> None:  # noqa: ANN001
        # ``path`` kept for signature compatibility; metrics module owns the path.
        self._starter = int(starter)

    def balance(self, user_id: int | str) -> int:
        """Balance, granting the one-time starter bonus on first access."""
        import metrics as _m
        return _m.credits_balance(int(user_id), self._starter)

    def can_afford(self, user_id: int | str, amount: int) -> bool:
        return self.balance(user_id) >= int(amount)

    def charge(self, user_id: int | str, amount: int) -> bool:
        """Deduct ``amount`` if affordable; return whether it succeeded."""
        import metrics as _m
        return _m.credits_charge(int(user_id), int(amount), self._starter)

    def refund(self, user_id: int | str, amount: int) -> None:
        import metrics as _m
        _m.credits_refund(int(user_id), int(amount))

    def add(self, user_id: int | str, amount: int) -> int:
        """Top up (purchase) and return the new balance."""
        import metrics as _m
        return _m.credits_add(int(user_id), int(amount), self._starter)


def make_credit_store(path, starter: int = STARTER_CREDITS, *, use_sqlite: bool | None = None):  # noqa: ANN001
    """Factory: return a :class:`CreditStoreSQLite` or :class:`CreditStore`.

    ``use_sqlite`` defaults to the ``CREDITS_SQLITE`` env var (``"1"`` → True).
    Passing it explicitly overrides the env var (useful in tests).
    """
    import os as _os
    if use_sqlite is None:
        use_sqlite = _os.getenv("CREDITS_SQLITE", "0").strip() == "1"
    if use_sqlite:
        return CreditStoreSQLite(path, starter)
    return CreditStore(path, starter)


class PaymentStore:
    """Persisted log of Telegram Stars payments, for refunds (atomic JSON).

    Each record keeps the ``telegram_payment_charge_id`` needed by
    ``refundStarPayment`` plus the credited amount, so an admin refund can both
    return the stars and claw back the granted credits.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._records: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            parsed = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(parsed, dict):
            recs = parsed.get("payments", [])
            if isinstance(recs, list):
                self._records = [r for r in recs if isinstance(r, dict)]

    def add(self, user_id: int, charge_id: str, stars: int, credits: int, pack_id: str) -> dict:
        # Идемпотентность: редоставленный Telegram-апдейт несёт тот же charge_id —
        # второй записи не создаём (иначе /refund мог бы вернуть звёзды дважды).
        existing = self.find_by_charge(charge_id)
        if existing is not None:
            return existing
        rec = {
            "user_id": int(user_id),
            "charge_id": str(charge_id),
            "stars": int(stars),
            "credits": int(credits),
            "pack": str(pack_id),
            "refunded": False,
        }
        self._records.append(rec)
        self._save()
        return rec

    def last_for_user(self, user_id: int) -> dict | None:
        """Most recent non-refunded payment by ``user_id`` (or None)."""
        for rec in reversed(self._records):
            if rec.get("user_id") == int(user_id) and not rec.get("refunded"):
                return rec
        return None

    def find_by_charge(self, charge_id: str) -> dict | None:
        for rec in self._records:
            if rec.get("charge_id") == str(charge_id):
                return rec
        return None

    def mark_refunded(self, charge_id: str) -> dict | None:
        rec = self.find_by_charge(charge_id)
        if rec is not None:
            rec["refunded"] = True
            self._save()
        return rec

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"payments": self._records}
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

