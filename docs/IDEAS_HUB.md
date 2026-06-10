# IDEAS_HUB.md - current template and growth ideas

Last sync: 2026-06-11.

This file describes ideas that are either implemented in the bot's Ideas Hub or
ready for the next product/growth iteration. Runtime template data lives in
`prompts_lib.py` and `prompts/templates/*.txt`.

## Current Implementation

The Ideas Hub is implemented as a guided prompt/template layer:

- template catalog: `prompts_lib.py`;
- template bodies: `prompts/templates/*.txt`;
- Telegram UI: `flow_bot.py`;
- tests: `tests/test_prompts_lib.py`, `tests/test_flow_menu.py`.

Current tests expect 9 templates. If templates are added or removed, update
`EXPECTED_IDS` in `tests/test_prompts_lib.py` and the menu tests in the same
change.

## Newly Added Killer Features

### Pet Photo Animation

Template id: `pet_photo_animation`

User promise:

- turn a pet photo into an emotional short-video prompt;
- preserve the pet's real appearance;
- offer simple emotion/motion choices.

Why it matters:

- strong mass-market "wow" effect;
- easy to demonstrate in Reels/TikTok/Shorts;
- high sharing potential because the output is personal.

Current scope:

- implemented as a ready prompt template;
- best used with photo/reference-to-video or generated-image animate flows;
- not yet a dedicated one-click "upload pet photo -> animate" vertical.

Next product step:

- add a direct button in Ideas Hub that routes users into reference-to-video with
  one photo, `VID_REF_DEFAULT_MODEL`, and this template's prompt flow.

### Marketplace White Background

Template id: `marketplace_white_bg`

User promise:

- create a clean marketplace product card on a white background in seconds;
- preserve the product;
- fit Wildberries/Ozon-style seller needs.

Why it matters:

- clear business pain for marketplace sellers;
- easy value comparison against manual photo cleanup and photographers;
- strong lead magnet for seller chats and short-form demos.

Current scope:

- implemented as a prompt template;
- usable for image generation/editing now.

Next product step:

- add before/after demo assets and a seller-focused onboarding path:
  "upload product photo -> clean white card -> generate 4 variants".

## Video Feature Status

Do not use older "blocked until capture" notes:

- text-to-video is implemented;
- Ingredients/reference-to-video is captured and enabled;
- Frames/start-end interpolation is captured and enabled;
- generated-video edit and extend are implemented;
- uploaded-video edit has a captured upload contract but remains hidden behind
  `UPLOAD_VIDEO_EDIT_ENABLED = False` because provider behavior needs a separate
  live verification task.

## Growth Ideas To Connect With Templates

Prioritize demos that produce a visible before/after in under 10 seconds:

- pet photo -> emotional short video;
- product photo -> white marketplace card;
- one product -> 4 ad creatives;
- selfie/photo -> avatar or profile image;
- generated image -> animated clip.

Suggested CTA:

- "send photo to the bot";
- "get 3 free image tries";
- "video is premium, images are cheap".

Avoid generic "AI image generator" positioning. Lead with the concrete job:

- "Оживи фото питомца";
- "Карточка товара на белом фоне";
- "4 креатива для объявления";
- "Фото профиля в одном стиле".

## Backlog

Product:

- direct pet-animation route from Ideas Hub into reference-to-video;
- direct seller-product route into image edit with marketplace defaults;
- template-specific analytics in `metrics.py`;
- template-specific conversion report in admin metrics;
- demo gallery from approved generated examples.

Marketing:

- 10-15 organic short videos made manually before buying any posting farm;
- separate seller-facing and pet-owner-facing content batches;
- TGStat research for relevant communities, but no mass unsolicited DM spam from
  the bot account;
- opt-in lead capture through public posts, useful comments, and manual
  outreach where allowed.

Validation:

```bash
python -m unittest discover -s tests -p "test_prompts_lib.py"
python -m unittest discover -s tests -p "test_flow_menu.py"
```
