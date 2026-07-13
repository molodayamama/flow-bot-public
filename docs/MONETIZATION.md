# MONETIZATION.md - current pricing model

Last sync: 2026-07-13.

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
  credits/month, while the current planning fleet of five accounts is still only
  about 5,000 provider credits/month.
- Telegram Stars introduce payout friction, so retail packs must be priced for
  conversion and quota protection, not just raw compute margin.

## Provider Economics

Current operating assumptions:

| Item | Value |
|---|---:|
| Google One activation | 240 RUB / 12 months |
| Flow quota per account | 1,000 G-credits/month |
| Flow quota per account/year | 12,000 G-credits |
| Planning quota at 5 accounts | 5,000 G-credits/month |
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
| Nano Banana 2 image | 10 |
| Nano Banana Pro image | 15 |
| Edit photo | 15 |
| First-start grant | 30 |
| Prompt enhance / service upscale | 5 |
| Original download | 0 |

Channel acquisition policy:

- Telegram and MAX receive the one-time 30-credit first-start grant.
- The anonymous first-party website starts at 0 credits and uses Robokassa
  card/SBP top-up. A browser cookie can be cleared at will, so granting starter
  credit there would create an unlimited reset abuse path.
- Website prices are calculated only on the server from the same `flow_core`
  helpers as the bots; JavaScript display values are never accepted for charge.
- A web balance belongs to its signed browser session. Until account login is
  implemented, clearing that cookie loses access to the corresponding balance.

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
| Omni Flash 4s | 50 | 7 |
| Omni Flash 6s | 70 | 10 |
| Omni Flash 8s | 85 | 12 |
| Omni Flash 10s | 100 | 15 |
| Veo Lite | 60 | 10 |
| Veo Fast | 120 | 20 |
| Veo Quality | 450 | 100 |

Reference and edit surcharges:

| Mode | Bot credits |
|---|---:|
| Ingredients / reference-to-video | base video price + 0 |
| Frames / start-end interpolation | base video price + 25 |
| Photo animation via Omni Flash 4s | 50 |
| Video prompt edit | 150 |
| Extend video | 60 |

The video grid is intentionally between two bad extremes:

- the retired cheap grid underpriced scarce quota;
- the previous premium grid protected quota but pushed entry video above the
  first-purchase threshold.

With blended net revenue near 0.65 RUB per bot credit, the current grid keeps
images cheap, makes Veo Lite accessible, and leaves Veo Quality as a premium
anchor. Reference-to-video now supports both engines via the ⚡ Быстро (Omni) /
💎 Качество (Veo) toggle on the create-video screen, so photo animation starts at
50 credits (Omni `abra_r2v_*`) with Veo Lite reference at 60. The Omni
reference-to-video key is captured and enabled. Video Extend
remains Veo-only (Omni results do not offer "Продлить").

## Packs

Generic pack labels are built by `pack_label(pid)` and must show:

- bot credits;
- approximate number of image generations;
- optional "best value" marker.

Top-up pack labels are context-specific in `flow_bot.py`. The top-up screen
first separates payment methods ("Оплата через Stars" and
"СБП/Карта · выгоднее"). Stars pack labels show the Star price with `⭐`.
Robokassa/SBP pack labels show the fixed rounded RUB amount, the same usage hint,
and make card/SBP visibly better for the customer.

Robokassa/SBP public prices use a fixed rounded grid from
`ROBOKASSA_PACK_AMOUNTS_RUB` so customer-facing prices do not show kopeks:

| Pack | Credits | Card/SBP amount |
|---|---:|---:|
| trial | 45 | 45 ₽ |
| small | 100 | 90 ₽ |
| medium | 290 | 235 ₽ |
| large | 700 | 530 ₽ |
| xl | 1500 | 1050 ₽ |

The `trial` pack must be labelled as image-only in customer-facing package
lists. It has 45 credits, so it cannot buy the cheapest standalone video
(50 credits) by itself. The launch video-entry path is:

`STARTER_CREDITS` 30 + `trial` 45 = 75 credits = one photo animation via
Veo Lite (`video_price("veo-lite", mode="ingredients")`).

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
- If monthly Flow quota load factor stays above 70% for a full month, raise
  video prices by 25-50% before adding promotional discounts.
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
