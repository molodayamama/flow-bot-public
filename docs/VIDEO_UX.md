# VIDEO_UX.md - current video UX truth

Last sync: 2026-06-11.

This file describes the current video UX in `flow_bot.py`. The executable
source of truth is still `flow_bot.py` plus pricing/model helpers in
`flow_core.py` and copy in `flow_copy.py`.

## Entry Points

- Main menu callback: `m:vid`.
- Reply keyboard label: `kb_vid`.
- Video callback prefix: `v:`.
- Video wizard state lives in `wizard_state[uid]` under `v*` keys.
- The upload-to-edit entry is hidden while
  `flow_bot.UPLOAD_VIDEO_EDIT_ENABLED = False`.

Do not reintroduce older "generation blocked" assumptions for Ingredients or
Frames: both modes are implemented. Live/paid checks still require operator
approval.

## Current Modes

### Text To Video

Flow:

1. Family picker: Omni Flash or Veo.
2. Model picker: duration/tier.
3. Settings: aspect, count, price.
4. Prompt input.
5. Generate, poll, download, send results.

Endpoint:

- `video:batchAsyncGenerateVideoText`
- poll with `video:batchCheckAsyncVideoGenerationStatus`
- download via `labs.google/fx/api/trpc/media.getMediaUrlRedirect`

`VIDEO_GENERATION` is the first reCAPTCHA action tried for live generation; the
code keeps fallback actions for provider drift.

### Ingredients / Reference To Video

Status: captured and enabled.

Use cases:

- photos plus prompt to video;
- generated image -> "animate" flow;
- one to four reference images.

Endpoint:

- `video:batchAsyncGenerateVideoReferenceImages`
- payload field: `referenceImages: [{mediaId, imageUsageType:
  "IMAGE_USAGE_TYPE_ASSET"}]`
- model key pattern: `veo_3_1_r2v_{tier}_{orientation}`

Defaults:

- `VID_REF_DEFAULT_MODEL = "veo-lite"`
- surcharge: `VIDEO_INGREDIENTS_SURCHARGE = 15`

Implementation note: only a subset of tier/orientation combinations has been
live-confirmed. Unconfirmed model keys are pattern-inferred in `flow_core.py`
and should be verified with capture/live tests only after explicit approval.

### Frames / Start-End Video

Status: captured and enabled.

Use case: start frame + end frame + prompt -> interpolation video.

Endpoint:

- `video:batchAsyncGenerateVideoStartAndEndImage`
- `startImage` / `endImage` carry `mediaId` without `fe_id_` prefix plus
  crop coordinates.
- model key pattern: `veo_3_1_interpolation_{tier}`

Defaults:

- `VID_FRAMES_DEFAULT_MODEL = "veo-lite"`
- surcharge: `VIDEO_FRAMES_SURCHARGE = 25`

### Prompt Edit

Status: implemented for generated videos with a usable `workflow_id`.

Endpoint:

- video edit endpoint built in `flow_core.build_video_edit_payload`

Price:

- `VIDEO_PROMPT_EDIT_PRICE = 250`

### Extend

Status: implemented.

Behavior:

- each step extends from the current video reference;
- `VideoRef.extend_index` tracks chain depth;
- price grows by `VIDEO_EXTEND_STEP` each time;
- provider-side concat/stitch is used for full chained output when available;
- segment-only fallback remains available.

Formula:

```text
video_extend_price(model, n) = video_price(model, 1, "text") + 20 * n
```

### Uploaded Video Edit

Status: captured but disabled in UI.

The upload contract has been captured via:

- `/fx/api/upload-video?action=start`
- `/fx/api/upload-video?action=upload`

The upload parser can extract `mediaServerId` and `workflowServerId`. This is no
longer blocked by a missing workflow-id contract, but the feature remains hidden
because provider behavior is still unstable. Keep it disabled until a dedicated
live verification task approves the risk.

## Pricing

All user-facing video prices must come from `flow_core.py`.

Current video grid:

| Model | Bot credits |
|---|---:|
| Omni Flash 4s | 50 |
| Omni Flash 6s | 70 |
| Omni Flash 8s | 85 |
| Omni Flash 10s | 100 |
| Veo Lite | 75 |
| Veo Fast | 160 |
| Veo Quality | 600 |

Surcharges:

| Mode | Extra bot credits |
|---|---:|
| Ingredients / reference-to-video | +15 |
| Frames / start-end interpolation | +25 |
| Prompt edit | 250 total |
| Extend | base text-video price + 20 per chain step |

Rationale: entry video must fit the first-purchase ladder while still protecting
scarce Flow quota. If monthly Flow quota load factor stays above 70% for a full
month, raise video prices by 25-50%.

Current reference-to-video variants are Veo-only because the captured
`video_reference_model_key(...)` contract builds `veo_3_1_r2v_*` keys. Do not
offer Omni-priced reference videos until that endpoint/key is verified.

## UI Rules

- Never hardcode prices in copy when a helper exists.
- Variant buttons and settings screens must show the current price before the
  user confirms generation.
- Edit/extend result buttons must show prices or make the progressive price
  clear in the prompt screen.
- Keep user-facing copy provider-neutral: no "Google", "Flow", "captcha", or
  internal endpoint names in bot messages.
- Failed generation/edit paths must refund credits before showing an error.

## Safe Validation

Offline checks:

```bash
python -m unittest discover -s tests -p "test_flow_video.py"
python -m unittest discover -s tests -p "test_flow_menu.py"
python -m py_compile flow_bot.py flow_core.py flow_copy.py
```

External checks not allowed without explicit approval:

- running `python flow_bot.py`;
- running capture scripts against live Flow;
- spending 2Captcha balance;
- generating real media.
