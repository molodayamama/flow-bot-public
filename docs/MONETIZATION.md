# MONETIZATION.md - current pricing model

Last sync: 2026-06-11.

This document is the business/pricing view. Runtime prices live in
`flow_core.py`; if a value here disagrees with code, fix the doc or the code in
one reviewed change.

## Core Principles

- Bot credits are the user-facing currency.
- Google Flow credits are provider quota and must not be treated as bot credits
  1:1.
- Images are the low-friction entry product because current image operations do
  not consume Google Flow credits.
- Videos are premium because a single Flow account has about 1,000 provider
  credits/month, which is only about 142 Omni Flash 4s videos.
- Telegram Stars introduce payout friction, so retail packs must be priced for
  conversion and quota protection, not just raw compute margin.

## Provider Economics

Current operating assumptions:

| Item | Value |
|---|---:|
| Google One activation | 240 RUB / 12 months |
| Flow quota per account | 1,000 G-credits/month |
| Flow quota per account/year | 12,000 G-credits |
| Activation-only cost | about 0.02 RUB / G-credit |
| First-year blended account/card cost | about 0.053 RUB / G-credit |
| MVP account fleet | up to 5 accounts before real demand proves need |

Proxy/antidetect is not baseline cost. Use direct access first, then add proxy
only if account health or availability data requires it.

## Retail Prices

### Images

Current code baseline:

| Action | Bot credits |
|---|---:|
| Generate 1 image | 10 |
| First-start grant | 30 |
| Prompt enhance / service upscale | 5 |
| Original download | 0 |

Image edit, variation, regeneration, and upscale prices are resolved through
`action_price(...)` in `flow_core.py`. Keep user-facing labels dynamic.

Image format/model expansion is implemented in code, not "future only":

- supported aspect choices include 16:9, 4:3, 1:1, 3:4, 9:16 where the relevant
  flow exposes them;
- model selection includes the current image model choices in `flow_core.py`;
- edit flows can preserve selected format/model where supported.

Because image operations are currently 0 provider credits, they are the safest
acquisition and retention surface.

### Videos

Current video prices in `flow_core.VIDEO_MODELS`:

| Model | Bot credits | Provider credits |
|---|---:|---:|
| Omni Flash 4s | 100 | 7 |
| Omni Flash 6s | 140 | 10 |
| Omni Flash 8s | 170 | 12 |
| Omni Flash 10s | 210 | 15 |
| Veo Lite | 150 | 10 |
| Veo Fast | 300 | 20 |
| Veo Quality | 1200 | 100 |

Reference and edit surcharges:

| Mode | Bot credits |
|---|---:|
| Ingredients / reference-to-video | base video price + 50 |
| Frames / start-end interpolation | base video price + 80 |
| Video prompt edit | 400 |
| Extend step | base video price + 50 * chain depth |

The old cheap-video grid is retired. At 100 bot credits for Omni Flash 4s, one
account's monthly 142-video quota costs users about 14,200 bot credits instead
of being drained by a few low-priced users.

## Packs

Pack labels are built by `pack_label(pid)` and must show:

- bot credits;
- approximate number of image generations;
- Telegram Stars price;
- optional "best value" marker.

Do not promise a fixed number of videos in a pack unless the UI names the exact
model/mode; video prices vary widely.

## Product Positioning

Use images as the mass-market hook:

- marketplace product card on white background;
- profile/avatar/social content;
- quick ad creatives and variations;
- "try it now" low-cost templates.

Use video as a paid upgrade:

- pet/photo animation;
- reference-to-video from a product/person/photo;
- before/after or start/end frame interpolation;
- premium Veo outputs for users who already understand the value.

## Operational Guardrails

- Do not give enough free starter credits for the cheapest video.
- Do not discount videos until account routing, quotas, cooldowns, and refund
  behavior have real metrics.
- Track conversion separately for image users and video users.
- Keep failed paid actions refundable.
- Do not run live quota or generation experiments without explicit approval.

## Metrics To Watch

- activation: `/start` -> first image;
- first paid intent: top-up opened, invoice created, invoice paid;
- video funnel: video menu -> model selected -> prompt sent -> success/fail;
- quota burn per Flow account;
- refund count and refund reason;
- repeat generation within 24 hours;
- template usage and paid conversion by template id.
