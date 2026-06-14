# UI_PRICE_LABELS.md - current pricing label rules

Last sync: 2026-06-12.

Purpose: every paid action must show its credit cost before the user commits to
it. The executable source of truth is `flow_core.py`; UI builders in
`flow_bot.py` should read pricing from helpers instead of duplicating constants.

## Source Of Truth

Current core constants:

| Constant/helper | Current meaning |
|---|---|
| `PRICE_PER_IMAGE = 10` | base image generation price per image |
| `IMAGE_EDIT_PRICE = 15` | photo edit price |
| `UPSCALE_PRICE = 5` | prompt enhance / real upscale service action |
| `STARTER_CREDITS = 30` | first-start balance, enough for 3 images |
| `VIDEO_MODELS` | video model catalog and base bot-credit prices |
| `VIDEO_INGREDIENTS_SURCHARGE = 15` | reference/ingredients video surcharge |
| `VIDEO_FRAMES_SURCHARGE = 25` | start/end frame video surcharge |
| `VIDEO_PROMPT_EDIT_PRICE = 150` | video prompt edit price |
| `VIDEO_EXTEND_PRICE = 60` | video extend price |

Current video base prices:

| Model | Bot credits |
|---|---:|
| Omni Flash 4s | 50 |
| Omni Flash 6s | 70 |
| Omni Flash 8s | 85 |
| Omni Flash 10s | 100 |
| Veo Lite | 60 |
| Veo Fast | 120 |
| Veo Quality | 450 |

## Image UI

Image wizard:

- Count/format/model selectors are free toggles.
- The wizard body shows the total generation price.
- The generate button does not need a duplicate price suffix if the visible body
  line already shows the exact total.

Image result keyboard:

- Paid buttons should append the dynamic price:
  - edit: `action_price("edit")`;
  - variations: `action_price("revary")`;
  - regenerate: `action_price("regen", count)`;
  - prompt enhance / up2x: `action_price("up2x")`;
  - real upscale: `action_price("realup")`.
- Free staging/navigation buttons should not show `0 кр`.

Balance and top-up:

- Balance copy explains the current image unit price.
- The top-up screen first asks for the payment method.
- Stars pack labels show credits, approximate usage, and the Star price with
  `⭐`, for example `45 кр · только картинки · ~4 карт. · 35⭐`.
- SBP/card pack labels show credits, approximate usage, and the RUB amount.
  These prices apply `ROBOKASSA_CARD_DISCOUNT_PCT` (default 10%) and should be
  presented as the better payment method for the customer.
- The 45-credit trial pack is explicitly image-only because it cannot buy the
  cheapest 50-credit video by itself.
- The value marker `🔥 +N%` is reserved for the three larger packs where the
  credit-per-payment-unit ratio is better than the entry pack.

## Video UI

Family/variant screens:

- Show each variant with its current `video_price(...)`.
- Keep model display names in `VIDEO_MODELS`; do not duplicate them in docs or
  copy tables.

Settings screens:

- Text-video settings show total price for selected model, aspect, and count.
- Ingredients settings include `VIDEO_INGREDIENTS_SURCHARGE`.
- Frames settings include `VIDEO_FRAMES_SURCHARGE`.

Prompt screens:

- The user must see the final price before sending a paid prompt.
- Prompt edit must show `VIDEO_PROMPT_EDIT_PRICE`.
- Extend must show `video_extend_price(model, extend_index)`.

Result keyboard:

- Download is free.
- Edit is shown only when the video reference has the fields required by the
  edit payload.
- Extend is shown only when the video reference is extendable.
- Buttons should avoid stale hardcoded labels; derive prices at render time.

## Safety Rules

- Do not expose provider names, captcha details, account ids, media ids, bearer
  tokens, cookies, or proxy values in user-facing labels.
- Do not charge for navigation, staging photos, opening templates, or failed
  generation.
- If a generation/edit call fails after debit, refund before showing the error.

## Safe Validation

```bash
python -m unittest discover -s tests -p "test_flow_menu.py"
python -m unittest discover -s tests -p "test_flow_video.py"
```
