# Infographic text layer (POC)

`infographic_text.py` renders crisp, readable Russian (Cyrillic) infographic
text as a **template layer over an AI-generated background**, instead of asking
the diffusion model to draw letters (which mangles Cyrillic). This is the core
differentiator for marketplace cards.

## API

```python
render_infographic(
    background_png: bytes,
    title: str,
    bullets: list[str],
    *,
    size: tuple[int, int] | None = None,   # default = background size, else 1080x1440 (3:4)
    accent_hex: str = "#1E88E5",
    layout: str = "left",                  # "left" | "bottom" | "top"
) -> bytes                                 # PNG bytes
```

Pure and deterministic. Never raises on normal inputs: on any failure (including
Pillow not installed) it returns `background_png` unchanged and logs once.

- Fonts: discovers a Cyrillic-capable TrueType on the host (PIL bundled DejaVu,
  then DejaVuSans / Arial / Segoe UI / Tahoma on Windows, DejaVu/Liberation on
  Linux, Arial on macOS). No fonts are downloaded. Falls back to PIL's bitmap
  default if none are found.
- Draws a semi-transparent dark scrim + accent edge, bold wrapped title with an
  accent underline, and bullets with `•` markers. Title and bullets shrink to
  fit; overflow is clamped/ellipsized.

## Status

`INFOGRAPHIC_TEXT_LAYER_ENABLED = False`. Not wired into the bot yet.

## Wiring later (`mp:job:info`)

The `mp:job:info` job in `flow_bot.py` currently only generates the AI
background (seed: "инфографика-карточка товара: крупный товар, место под
заголовок и буллеты"). To ship the text layer:

1. Collect `title` + `bullets` from the user (new ask-step after the photo, or
   parse them from the caption).
2. After the AI background is generated/downloaded, pass its PNG bytes through
   `render_infographic(...)` before sending.
3. Gate the call behind `INFOGRAPHIC_TEXT_LAYER_ENABLED` and let `accent_hex` /
   `layout` come from the user's brand kit / format choice.
4. Tune the background seed to leave a clean empty zone matching the chosen
   `layout` so the scrim lands over low-detail pixels.

## Tests / validation (offline)

```bash
python -m py_compile infographic_text.py tests/test_infographic_text.py
python -m unittest discover -s tests -p "test_infographic_text.py"
```
