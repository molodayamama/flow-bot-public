# Telegram / MAX consumer interface parity

This document defines product parity between the primary Telegram bot and the
MAX bot. Parity means the same consumer capability and truthful state
transition; callback identifiers and transport-specific presentation may
differ where the platform requires it.

## Parity matrix

| Consumer surface | Telegram | MAX | Contract |
| --- | --- | --- | --- |
| Main menu | Yes | Yes | The seven rows, labels and callbacks are locked by `tests/test_channel_parity.py`. |
| Image generation | Yes | Yes | Prompt, quick ideas, settings, explicit price confirmation, credit charge/refund and media delivery. |
| Video generation | Yes | Yes | Text-to-video, ingredients (1–4 photos) and start/end frames live below the consumer video entry point. |
| Photo routing | Yes | Yes | An arbitrary photo or “Изменить моё фото” offers edit-image or animate-photo routes. |
| Ideas | Yes | Yes | Quick ideas, templates with text/choice steps and a guided constructor end at a paid confirmation screen. |
| Balance and top-up | Yes | Yes | Real balance and signed payment links. Telegram Stars remain platform-specific. |
| Profile | Yes | Yes | Real balance, completed-generation count, gallery, prompt history, support and invitation entry points. |
| Gallery | Yes | Yes | Only successful results belonging to the current platform identity are shown. |
| Prompt history | Yes | Yes | Reuse opens confirmation/settings and never charges on selection. |
| Support | Yes | Yes | Tickets persist in the shared metrics store; admin replies are routed through the stored platform identity. |
| Referrals | Yes | Yes | Share link, validated start payload, statistics and reward notification. |
| Image result actions | Yes | Yes | Edit, exact paid repeat, improve, animate, original link and menu. |
| Video result actions | Yes | Yes | Exact paid repeat, new video, safe original link and menu. |
| Durable wizard state | Yes | Yes | MAX uses its SQLite state store and survives callback/webhook retries. |
| Single-screen navigation | Yes | Yes | MAX edits the inline-keyboard message and sends a fresh screen only when editing is unavailable. |

## Main-menu contract

The MAX root menu intentionally uses the Telegram consumer callbacks so stale
or shared product assumptions do not create a second information architecture:

1. `m:gen` — `🎨 Создать картинку`
2. `m:vid` — `🎬 Создать видео`
3. `m:animate` — `🎬 Оживить фото · от … кр`
4. `m:myphoto` — `🖼 Изменить моё фото`
5. `m:ideas` — `💡 Идеи и шаблоны`
6. `m:balance` — `💳 … кр · Пополнить`
7. `m:profile` and `m:invite` — profile and referral actions in one row

The former MAX `m:video` callback remains accepted for keyboards already sent
before the migration. Advanced provider modes no longer appear on the root
screen.

## Paid-action and replay rules

- Choosing an idea, history item or settings value never spends credits.
- The final confirmation always displays the current server-side price.
- Generation, exact repeat and result transformations use the same shared
  credit gate. Failures refund through the existing service contract.
- Callback acknowledgement happens after the durable action. A failed
  acknowledgement must not replay a completed charge through the webhook
  inbox.
- Result actions keep the exact saved job required for a real repeat; they do
  not reconstruct a cheaper or different request from visible text.

## Identity and data boundaries

- All MAX data is keyed by the shared internal user id obtained from the
  `(platform, platform_user_id)` identity mapping.
- Gallery, history, support and referral queries never accept an arbitrary
  internal id from a button payload.
- A `ref_<id>` deep-link payload binds only when the referenced id belongs to an
  existing MAX identity. Self-referrals and duplicate binding remain rejected
  by the metrics layer.
- Support and referral notifications resolve the stored identity first. A MAX
  internal id is never submitted to Telegram as a chat id.

## Intentional platform differences

- Telegram Stars are Telegram-only. MAX uses the configured signed web payment
  links.
- Telegram administrator controls and the separate seller/marketplace bot are
  operator products, not consumer MAX menu items.
- Telegram video edit/extend currently depends on an account-bound `VideoRef`
  registry. The shared generation boundary cannot safely reproduce it in MAX,
  so MAX offers repeat/new/original instead of non-working edit/extend buttons.

## MAX platform references

- Bot start links and `bot_started.payload`:
  <https://dev.max.ru/docs/chatbots/bots-coding/prepare>
- Callback answers and message updates:
  <https://dev.max.ru/docs-api/methods/POST/answers>
- Message editing:
  <https://dev.max.ru/docs-api/methods/PUT/messages>
- Callback button intents:
  <https://dev.max.ru/docs/chatbots/bots-coding/library/js>

## Offline validation

```bash
python -m pytest -q \
  tests/test_channel_parity.py \
  tests/test_max_mvp.py \
  tests/test_max_channel.py \
  tests/test_channels_base.py \
  tests/test_max_state.py \
  tests/test_max_runtime.py \
  tests/test_max_generation_adapter.py \
  tests/test_max_webhook_route.py \
  tests/test_max_inbox.py \
  tests/test_telegram_routers.py
```

These tests use fakes and temporary SQLite databases. They must not contact
MAX, Google, Robokassa or 2Captcha and must not perform a paid generation.
