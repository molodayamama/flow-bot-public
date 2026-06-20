"""Programmatic infographic text layer for marketplace cards (offline POC).

Why this exists
---------------
Diffusion models mangle Cyrillic letters, so we never let the AI "draw" the
title/bullets. Instead the AI produces a clean background image and this module
overlays crisp, readable Russian text zones (a title plus bullet points) as a
deterministic template layer on top.

Public API
----------
``render_infographic(background_png, title, bullets, *, size, accent_hex,
layout) -> bytes`` is a pure function: deterministic for given inputs, returns
PNG bytes, and never raises on normal inputs. If Pillow is missing or anything
unexpected happens it degrades gracefully and returns the original
``background_png`` unchanged.

This module is intentionally self-contained: it does not import the bot,
flow_core, network, Telegram, or any secrets. Wiring it into the bot
(`mp:job:info`) is a deliberate later step gated by the feature flag below.
"""

from __future__ import annotations

import io
import logging
import os
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Wiring this layer into the bot's mp:job:info flow is a LATER step.
# Keep it False until the render quality is signed off; nothing in flow_bot.py
# should consume this module while the flag is False.
INFOGRAPHIC_TEXT_LAYER_ENABLED = False

# Default output canvas for a 3:4 marketplace card (WB/Ozon-friendly).
_DEFAULT_SIZE: Tuple[int, int] = (1080, 1440)

# Candidate Cyrillic-capable TrueType fonts. We do NOT download fonts; we only
# locate ones already present on the host. Each entry is (regular, bold) and we
# probe them in order. Bold may be None -> we fall back to the regular face.
_FONT_CANDIDATES: Tuple[Tuple[str, Optional[str]], ...] = (
    # Pillow historically bundled DejaVuSans under PIL/fonts; check there first.
    ("__pil_dejavu__", "__pil_dejavu_bold__"),
    # Windows
    (r"C:\Windows\Fonts\DejaVuSans.ttf", r"C:\Windows\Fonts\DejaVuSans-Bold.ttf"),
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
    (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
    (r"C:\Windows\Fonts\tahoma.ttf", r"C:\Windows\Fonts\tahomabd.ttf"),
    # Linux (DejaVu is the most common Cyrillic-capable default).
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    (
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ),
    (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ),
    # macOS
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", None),
)

# Module-level flag to ensure we only emit the noisy Pillow warning once.
_pillow_warned = False


def _pil_font_dir() -> Optional[str]:
    """Return PIL's bundled fonts dir if it exists (older Pillow versions)."""
    try:
        import PIL  # noqa: WPS433 (local import keeps module importable w/o PIL)

        candidate = os.path.join(os.path.dirname(PIL.__file__), "fonts")
        return candidate if os.path.isdir(candidate) else None
    except Exception:  # pragma: no cover - defensive
        return None


def _resolve_font_paths() -> Tuple[Optional[str], Optional[str]]:
    """Find a (regular, bold) Cyrillic-capable TrueType pair on this host.

    Returns ``(None, None)`` if nothing usable is found, in which case callers
    fall back to PIL's bitmap default font (which still covers Cyrillic, just
    without size control).
    """
    pil_dir = _pil_font_dir()
    for regular, bold in _FONT_CANDIDATES:
        reg_path = regular
        bold_path = bold
        if regular == "__pil_dejavu__":
            if not pil_dir:
                continue
            reg_path = os.path.join(pil_dir, "DejaVuSans.ttf")
            bold_path = os.path.join(pil_dir, "DejaVuSans-Bold.ttf")
        if not reg_path or not os.path.isfile(reg_path):
            continue
        if not bold_path or not os.path.isfile(bold_path):
            bold_path = reg_path  # fall back to regular face for "bold"
        return reg_path, bold_path
    return None, None


def _hex_to_rgb(
    value: str, default: Tuple[int, int, int] = (30, 136, 229)
) -> Tuple[int, int, int]:
    """Parse ``#RRGGBB`` (or ``#RGB``) to an RGB tuple; tolerant of junk."""
    try:
        s = value.strip().lstrip("#")
        if len(s) == 3:
            s = "".join(ch * 2 for ch in s)
        if len(s) != 6:
            return default
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except Exception:
        return default


def _load_truetype(path: Optional[str], size: int):
    """Load a TrueType font at ``size``, or PIL's default if unavailable."""
    from PIL import ImageFont

    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _text_width(draw, text: str, font) -> int:
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return int(bbox[2] - bbox[0])
    except Exception:
        try:
            return int(draw.textlength(text, font=font))
        except Exception:
            return len(text) * 8


def _line_height(draw, font) -> int:
    try:
        bbox = draw.textbbox((0, 0), "Ёй| ", font=font)
        return int(bbox[3] - bbox[1])
    except Exception:
        return getattr(font, "size", 16)


def _wrap_text(draw, text: str, font, max_width: int) -> List[str]:
    """Greedy word-wrap; also hard-splits single words wider than the box."""
    words = (text or "").split()
    if not words:
        return []
    lines: List[str] = []
    current = ""
    for word in words:
        trial = word if not current else current + " " + word
        if _text_width(draw, trial, font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
        # Hard-split a single over-long token.
        while _text_width(draw, current, font) > max_width and len(current) > 1:
            cut = len(current)
            while cut > 1 and _text_width(draw, current[:cut], font) > max_width:
                cut -= 1
            lines.append(current[:cut])
            current = current[cut:]
    if current:
        lines.append(current)
    return lines


def _ellipsize(draw, text: str, font, max_width: int) -> str:
    """Trim ``text`` to fit ``max_width`` with a trailing ellipsis."""
    if _text_width(draw, text, font) <= max_width:
        return text
    ell = "…"
    cut = len(text)
    while cut > 0 and _text_width(draw, text[:cut] + ell, font) > max_width:
        cut -= 1
    return (text[:cut].rstrip() + ell) if cut > 0 else ell


def render_infographic(
    background_png: bytes,
    title: str,
    bullets: List[str],
    *,
    size: Optional[Tuple[int, int]] = None,
    accent_hex: str = "#1E88E5",
    layout: str = "left",
) -> bytes:
    """Overlay a clean Russian title + bullets on an AI-generated background.

    Parameters
    ----------
    background_png:
        Raw PNG bytes of the AI-generated background.
    title:
        Headline text (Cyrillic supported). May be empty.
    bullets:
        List of short bullet strings (Cyrillic supported). May be empty.
    size:
        Output ``(width, height)``. Defaults to the background's own size, or
        ``1080x1440`` (3:4 marketplace) if the background can't be read.
    accent_hex:
        Accent colour ``#RRGGBB`` used for the title underline and bullet
        markers. Tolerant of malformed values.
    layout:
        Panel placement: ``"left"`` (vertical scrim on the left),
        ``"bottom"`` or ``"top"`` (horizontal scrim band).

    Returns
    -------
    bytes
        PNG bytes of the composited card. On any failure (incl. Pillow not
        installed) returns ``background_png`` unchanged.
    """
    global _pillow_warned

    if not isinstance(background_png, (bytes, bytearray)) or not background_png:
        return background_png if isinstance(background_png, bytes) else b""

    try:
        from PIL import Image, ImageDraw  # noqa: WPS433
    except Exception:
        if not _pillow_warned:
            logger.warning(
                "Pillow is not installed; infographic_text returns the raw "
                "background. Run `pip install Pillow` to enable the text layer."
            )
            _pillow_warned = True
        return bytes(background_png)

    try:
        return _render(
            Image,
            ImageDraw,
            bytes(background_png),
            title or "",
            list(bullets or []),
            size=size,
            accent_hex=accent_hex,
            layout=layout,
        )
    except Exception:
        logger.exception("render_infographic failed; returning raw background")
        return bytes(background_png)


def _render(
    Image,
    ImageDraw,
    background_png: bytes,
    title: str,
    bullets: List[str],
    *,
    size: Optional[Tuple[int, int]],
    accent_hex: str,
    layout: str,
) -> bytes:
    # --- Load + size the canvas -------------------------------------------
    try:
        bg = Image.open(io.BytesIO(background_png)).convert("RGBA")
        natural_size = bg.size
    except Exception:
        bg = None
        natural_size = None

    if size is not None:
        out_w, out_h = int(size[0]), int(size[1])
    elif natural_size is not None:
        out_w, out_h = natural_size
    else:
        out_w, out_h = _DEFAULT_SIZE
    out_w = max(64, out_w)
    out_h = max(64, out_h)

    if bg is not None:
        if bg.size != (out_w, out_h):
            bg = bg.resize((out_w, out_h))
        canvas = bg
    else:
        canvas = Image.new("RGBA", (out_w, out_h), (240, 240, 240, 255))

    accent = _hex_to_rgb(accent_hex)
    reg_path, bold_path = _resolve_font_paths()

    # --- Panel geometry ----------------------------------------------------
    layout = layout if layout in {"left", "bottom", "top"} else "left"
    margin = max(24, out_w // 24)

    if layout == "left":
        panel_w = int(out_w * 0.42)
        panel = (0, 0, panel_w, out_h)
        text_x0 = margin
        text_x1 = panel_w - margin
        text_y0 = margin
        text_y1 = out_h - margin
    elif layout == "top":
        panel_h = int(out_h * 0.34)
        panel = (0, 0, out_w, panel_h)
        text_x0 = margin
        text_x1 = out_w - margin
        text_y0 = margin
        text_y1 = panel_h - margin
    else:  # bottom
        panel_h = int(out_h * 0.34)
        panel = (0, out_h - panel_h, out_w, out_h)
        text_x0 = margin
        text_x1 = out_w - margin
        text_y0 = out_h - panel_h + margin
        text_y1 = out_h - margin

    text_w = max(32, text_x1 - text_x0)
    text_h = max(32, text_y1 - text_y0)

    # --- Draw the scrim ----------------------------------------------------
    scrim = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(scrim)
    sdraw.rectangle(panel, fill=(15, 18, 24, 165))
    # Accent edge along the inner side of the panel for a marketplace feel.
    edge = max(4, out_w // 180)
    if layout == "left":
        sdraw.rectangle((panel[2] - edge, 0, panel[2], out_h), fill=accent + (255,))
    elif layout == "top":
        sdraw.rectangle((0, panel[3] - edge, out_w, panel[3]), fill=accent + (255,))
    else:
        sdraw.rectangle((0, panel[1], out_w, panel[1] + edge), fill=accent + (255,))
    canvas = Image.alpha_composite(canvas, scrim)

    draw = ImageDraw.Draw(canvas)

    # --- Fit the title (shrink-to-fit on width AND height) ----------------
    # Title gets up to ~45% of the text column height; bullets get the rest.
    title_budget_h = int(text_h * (0.45 if bullets else 0.9))
    title_lines: List[str] = []
    title_font = None
    title_lh = 0
    if title.strip():
        title_size = max(20, int(out_h * 0.055))
        min_title = max(16, int(out_h * 0.028))
        while title_size >= min_title:
            title_font = _load_truetype(bold_path, title_size)
            title_lines = _wrap_text(draw, title, title_font, text_w)
            title_lh = int(_line_height(draw, title_font) * 1.18)
            if title_lh * max(1, len(title_lines)) <= title_budget_h:
                break
            title_size -= 2
        # If still overflowing at min size, clamp the number of lines.
        max_title_lines = max(1, title_budget_h // max(1, title_lh))
        if len(title_lines) > max_title_lines:
            title_lines = title_lines[:max_title_lines]
            if title_lines:
                title_lines[-1] = _ellipsize(
                    draw, title_lines[-1], title_font, text_w
                )

    # --- Fit the bullets ---------------------------------------------------
    bullets = [b for b in (bullets or []) if isinstance(b, str) and b.strip()]
    bullet_block_h = text_h - (title_lh * len(title_lines))
    bullet_block_h -= margin if title_lines else 0
    bullet_block_h = max(0, bullet_block_h)
    bullet_lines_per: List[List[str]] = []
    bullet_font = None
    bullet_lh = 0
    marker_w = 0
    if bullets and bullet_block_h > 0:
        bullet_size = max(16, int(out_h * 0.030))
        min_bullet = max(12, int(out_h * 0.018))
        while bullet_size >= min_bullet:
            bullet_font = _load_truetype(reg_path, bullet_size)
            bullet_lh = int(_line_height(draw, bullet_font) * 1.25)
            marker_w = _text_width(draw, "•  ", bullet_font)
            bullet_lines_per = [
                _wrap_text(draw, b, bullet_font, max(16, text_w - marker_w))
                for b in bullets
            ]
            total = sum(max(1, len(ls)) for ls in bullet_lines_per) * bullet_lh
            if total <= bullet_block_h:
                break
            bullet_size -= 2

    # --- Paint title -------------------------------------------------------
    y = text_y0
    for line in title_lines:
        draw.text((text_x0, y), line, font=title_font, fill=(255, 255, 255, 255))
        y += title_lh

    if title_lines:
        # Accent underline beneath the title block.
        uw = min(text_w, int(text_w * 0.55))
        uy = y + max(4, margin // 3)
        draw.rectangle(
            (text_x0, uy, text_x0 + uw, uy + max(3, out_h // 360)),
            fill=accent + (255,),
        )
        y = uy + margin

    # --- Paint bullets -----------------------------------------------------
    for lines in bullet_lines_per:
        if not lines:
            continue
        if y + bullet_lh > text_y1:
            break
        draw.text((text_x0, y), "•", font=bullet_font, fill=accent + (255,))
        for line in lines:
            if y + bullet_lh > text_y1:
                break
            draw.text(
                (text_x0 + marker_w, y),
                line,
                font=bullet_font,
                fill=(238, 240, 244, 255),
            )
            y += bullet_lh

    # --- Encode ------------------------------------------------------------
    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="PNG")
    return out.getvalue()
