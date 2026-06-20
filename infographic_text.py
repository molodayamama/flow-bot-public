"""Programmatic infographic text layer for marketplace cards (offline POC).

Why this exists
---------------
Diffusion models mangle Cyrillic letters, so we never let the AI "draw" the
title/bullets. Instead the AI produces a clean background image and this module
overlays crisp, readable Russian text zones (a title plus benefit points) as a
deterministic template layer on top.

Design goal: look like a real marketplace product card, not a flat slide — soft
gradient scrim, a shadowed headline with an accent bar, benefit rows with accent
check discs, and an optional corner badge.

Public API
----------
``render_infographic(background_png, title, bullets, *, size, accent_hex,
layout, badge) -> bytes`` is a pure function: deterministic for given inputs,
returns PNG bytes, and never raises on normal inputs. If Pillow is missing or
anything unexpected happens it degrades gracefully and returns the original
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

_pillow_warned = False


def _pil_font_dir() -> Optional[str]:
    try:
        import PIL  # noqa: WPS433

        candidate = os.path.join(os.path.dirname(PIL.__file__), "fonts")
        return candidate if os.path.isdir(candidate) else None
    except Exception:  # pragma: no cover - defensive
        return None


def _resolve_font_paths() -> Tuple[Optional[str], Optional[str]]:
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
            bold_path = reg_path
        return reg_path, bold_path
    return None, None


def _hex_to_rgb(
    value: str, default: Tuple[int, int, int] = (30, 136, 229)
) -> Tuple[int, int, int]:
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
    if _text_width(draw, text, font) <= max_width:
        return text
    ell = "…"
    cut = len(text)
    while cut > 0 and _text_width(draw, text[:cut] + ell, font) > max_width:
        cut -= 1
    return (text[:cut].rstrip() + ell) if cut > 0 else ell


def _gradient_band(Image, full_size, band, direction, color, max_alpha):
    """Return a full-canvas RGBA scrim with a soft alpha gradient over ``band``.

    ``direction``: "bottom"|"top" (vertical) or "left" (horizontal). The scrim is
    darkest toward the edge the text sits against, fading to transparent — far
    more premium than a flat panel."""
    out_w, out_h = full_size
    bx0, by0, bx1, by1 = band
    bw = max(1, bx1 - bx0)
    bh = max(1, by1 - by0)
    scrim = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))

    if direction == "left":
        grad = Image.new("L", (bw, 1))
        for x in range(bw):
            # opaque at the left edge, fading right
            grad.putpixel((x, 0), int(max_alpha * (1.0 - x / bw)))
        grad = grad.resize((bw, bh))
    else:
        grad = Image.new("L", (1, bh))
        for y in range(bh):
            t = y / bh
            a = t if direction == "bottom" else (1.0 - t)
            grad.putpixel((0, y), int(max_alpha * a))
        grad = grad.resize((bw, bh))

    panel = Image.new("RGBA", (bw, bh), color + (255,))
    panel.putalpha(grad)
    scrim.paste(panel, (bx0, by0), panel)
    return scrim


def render_infographic(
    background_png: bytes,
    title: str,
    bullets: List[str],
    *,
    size: Optional[Tuple[int, int]] = None,
    accent_hex: str = "#1E88E5",
    layout: str = "bottom",
    badge: Optional[str] = None,
) -> bytes:
    """Overlay a clean Russian title + benefits on an AI-generated background.

    Parameters
    ----------
    background_png:
        Raw image bytes of the AI-generated background (PNG/JPEG both fine).
    title:
        Headline text (Cyrillic supported). May be empty.
    bullets:
        Short benefit strings (Cyrillic supported). May be empty.
    size:
        Output ``(width, height)``. Defaults to the background's own size, or
        ``1080x1440`` (3:4 marketplace) if it can't be read.
    accent_hex:
        Accent colour ``#RRGGBB`` for the title bar, check discs and badge.
    layout:
        ``"bottom"`` (default), ``"top"`` or ``"left"`` text zone.
    badge:
        Optional short corner sticker (e.g. ``"−40%"`` or ``"ХИТ"``). ``None`` to
        omit.

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
            badge=(badge or None),
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
    badge: Optional[str],
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
        canvas = Image.new("RGBA", (out_w, out_h), (235, 238, 242, 255))

    accent = _hex_to_rgb(accent_hex)
    reg_path, bold_path = _resolve_font_paths()
    layout = layout if layout in {"left", "bottom", "top"} else "bottom"
    margin = max(34, out_w // 16)

    # --- Text-zone geometry + gradient scrim ------------------------------
    if layout == "left":
        zone_w = int(out_w * 0.50)
        band = (0, 0, zone_w, out_h)
        scrim = _gradient_band(Image, (out_w, out_h), band, "left", (10, 12, 18), 232)
        tx0, tx1 = margin, zone_w - margin
        ty0, ty1 = margin, out_h - margin
    else:
        band_h = int(out_h * 0.46)
        if layout == "top":
            band = (0, 0, out_w, band_h)
            scrim = _gradient_band(Image, (out_w, out_h), band, "top", (10, 12, 18), 232)
            ty0, ty1 = margin, band_h - margin
        else:
            band = (0, out_h - band_h, out_w, out_h)
            scrim = _gradient_band(Image, (out_w, out_h), band, "bottom", (10, 12, 18), 232)
            ty0, ty1 = out_h - band_h + margin, out_h - margin
        tx0, tx1 = margin, out_w - margin

    canvas = Image.alpha_composite(canvas, scrim)
    draw = ImageDraw.Draw(canvas)
    text_w = max(48, tx1 - tx0)

    def _shadow_text(x, y, s, font, fill):
        off = max(1, out_h // 700)
        draw.text((x + off, y + off), s, font=font, fill=(0, 0, 0, 170))
        draw.text((x, y), s, font=font, fill=fill)

    # --- Optional corner badge --------------------------------------------
    if badge:
        bfont = _load_truetype(bold_path, max(22, int(out_h * 0.030)))
        bw = _text_width(draw, badge, bfont)
        bh = _line_height(draw, bfont)
        pad = max(12, out_w // 70)
        bx1_ = out_w - margin
        bx0_ = bx1_ - (bw + pad * 2)
        by0_ = margin
        by1_ = by0_ + bh + pad
        try:
            draw.rounded_rectangle((bx0_, by0_, bx1_, by1_), radius=max(10, bh // 3),
                                   fill=accent + (255,))
        except Exception:
            draw.rectangle((bx0_, by0_, bx1_, by1_), fill=accent + (255,))
        draw.text((bx0_ + pad, by0_ + pad // 2), badge, font=bfont, fill=(255, 255, 255, 255))

    # --- Title (bold, shadow, accent bar) ---------------------------------
    title_budget_h = int((ty1 - ty0) * (0.42 if bullets else 0.85))
    title_lines: List[str] = []
    title_font = None
    title_lh = 0
    if title.strip():
        title_size = max(26, int(out_h * 0.062))
        min_title = max(20, int(out_h * 0.034))
        while title_size >= min_title:
            title_font = _load_truetype(bold_path, title_size)
            title_lines = _wrap_text(draw, title, title_font, text_w)
            title_lh = int(_line_height(draw, title_font) * 1.16)
            if title_lh * max(1, len(title_lines)) <= title_budget_h:
                break
            title_size -= 2
        max_lines = max(1, title_budget_h // max(1, title_lh))
        if len(title_lines) > max_lines:
            title_lines = title_lines[:max_lines]
            title_lines[-1] = _ellipsize(draw, title_lines[-1], title_font, text_w)

    y = ty0
    if title_lines:
        bar_h = max(6, out_h // 150)
        try:
            draw.rounded_rectangle((tx0, y, tx0 + int(text_w * 0.16), y + bar_h),
                                   radius=bar_h // 2, fill=accent + (255,))
        except Exception:
            draw.rectangle((tx0, y, tx0 + int(text_w * 0.16), y + bar_h), fill=accent + (255,))
        y += bar_h + max(16, margin // 2)
        for line in title_lines:
            _shadow_text(tx0, y, line, title_font, (255, 255, 255, 255))
            y += title_lh
        y += max(18, margin // 2)

    # --- Benefit rows: accent check disc + white text ---------------------
    bullets = [b for b in (bullets or []) if isinstance(b, str) and b.strip()]
    if bullets and y < ty1:
        bullet_size = max(20, int(out_h * 0.032))
        min_bullet = max(15, int(out_h * 0.020))
        disc_r = 0
        gap = 0
        bullet_font = None
        bullet_lh = 0
        wrapped: List[List[str]] = []
        while bullet_size >= min_bullet:
            bullet_font = _load_truetype(reg_path, bullet_size)
            bullet_lh = int(_line_height(draw, bullet_font) * 1.30)
            disc_r = max(8, bullet_lh // 3)
            gap = disc_r * 2 + max(10, disc_r)
            wrapped = [_wrap_text(draw, b, bullet_font, max(24, text_w - gap)) for b in bullets]
            total = sum(max(1, len(ls)) for ls in wrapped) * bullet_lh + len(wrapped) * (bullet_lh // 4)
            if y + total <= ty1:
                break
            bullet_size -= 2

        for lines in wrapped:
            if not lines or y + bullet_lh > ty1:
                break
            # accent check disc aligned to the first line
            cy = y + bullet_lh // 2
            cx = tx0 + disc_r
            draw.ellipse((cx - disc_r, cy - disc_r, cx + disc_r, cy + disc_r), fill=accent + (255,))
            cw = max(2, disc_r // 3)
            draw.line(
                [(cx - disc_r * 0.45, cy + disc_r * 0.02),
                 (cx - disc_r * 0.08, cy + disc_r * 0.42),
                 (cx + disc_r * 0.5, cy - disc_r * 0.40)],
                fill=(255, 255, 255, 255), width=cw, joint="curve",
            )
            tx = tx0 + gap
            for line in lines:
                if y + bullet_lh > ty1:
                    break
                _shadow_text(tx, y, line, bullet_font, (238, 241, 246, 255))
                y += bullet_lh
            y += bullet_lh // 4

    # --- Encode ------------------------------------------------------------
    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="PNG")
    return out.getvalue()
