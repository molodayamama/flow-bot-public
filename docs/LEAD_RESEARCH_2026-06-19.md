# Lead Research: Photozhab Telegram Outreach

Date: 2026-06-19.

Scope: first read-only lead map for Photozhab using public web sources and the
existing Telegram E2E user session. No messages were sent, no chats were joined,
no paid services were contacted, and no Google Flow generation was run.

## Working ICP

Primary ICP: Wildberries/Ozon/Yandex Market sellers and the designers,
marketplace managers, and SMM operators who make product cards, infographics,
UGC creatives, and short product videos for them.

Why this ICP is strongest now:

- The existing product already has seller-shaped prompts:
  `marketplace_white_bg`, `product_card`, and `ugc_creative`.
- The public site already has a credible low-barrier offer: 30 starter credits,
  visible action prices, images at 10-15 credits, photo editing at 15 credits,
  and photo animation through Veo Lite at 75 credits.
- Telegram evidence shows active demand and competitor supply around product
  cards, AI photoshoots, infographics, and product videos.

Secondary ICP: SMM/content creators who need quick visual variants, reels, and
short AI video from a photo.

Tertiary ICP: broad AI/neural-network chats. These are useful for pain discovery
and comparison, but weaker for paid conversion because they are crowded with
general-purpose AI tools.

## Telegram Chat Shortlist

Use a separate `seed_` source for every test. Generate links with
`/admin_channels <label>` and keep labels under 32 chars.

| Priority | Chat | Members observed | Fit | Rules / route | Suggested seed |
|---|---:|---:|---|---|---|
| 1 | [Дизайнеры Селлеры WB OZON Инфографика Карточки](https://t.me/chat_infographics) | 2,388 | Best first public test: the chat description says designers may post services for free for now. | Public helpful post or case post is plausible. Avoid spammy repetition. | `tg_chat_infographics` |
| 1 | [Дизайнеры Wildberries \| Инфографика](https://t.me/designers_wb_ozon) | 40,199 | Strong marketplace visual audience, many AI/video/card posts. | Description says placement is through a tariff bot/admin route. Use approved placement. | `tg_paid_designerswb` |
| 1 | [Дизайнеры Wildberries и Ozon \| Инфографика](https://t.me/dizainer_wb) | 12,127 | Similar to above, high fit for product cards and AI photoshoots. | Description says placement is through a tariff bot/admin route. Use approved placement. | `tg_paid_dizainerwb` |
| 1 | [Wildberries поставщики \| Чат Инфографика](https://t.me/wbnahodkychat) | 22,622 | Fresh direct demand: one recent message asks for someone who does infographics. | Description points to paid/pinned placement and admin route. Use approved placement. | `tg_paid_wbnahodky` |
| 2 | [Маркетплейсы ЧАТ поставщиков](https://t.me/marketplaces_chat) | 67,109 | Large seller chat; found a direct recent request for a strong infographic designer for 12 cards. | Ad without approval is a permanent ban per description. Use admin route; public answers only when genuinely useful and allowed. | `tg_admin_marketplace` |
| 2 | [Маркетплейсы \| чат селлеров вб/озон](https://t.me/MP_partner) | 43,433 | Broad seller Q&A; good for demand listening and admin post tests. | Services mentions are forbidden without admin approval. | `tg_admin_mppartner` |
| 2 | [Маркетплейсы \| Чат поставщиков](https://t.me/sellery_ozon) | 3,705 | Smaller but direct Ozon/WB seller Q&A; has historical requests for card edits/designers. | Description says ad placement without agreement is forbidden. | `tg_admin_selleryozon` |
| 2 | [Wildberries - селлеры, чат](https://t.me/wildberries_service) | 7,513 | Q&A-only seller audience; useful for listening and admin placement. | Unauthorized ads/self-promo/spam are banned. | `tg_admin_wbservice` |
| 3 | [PRO Нейросети и SMM \| ЧАТ](https://t.me/xb_prosmm_chat) | 832 | Good SMM/neural-network fit; less direct seller-buying intent. | Use for pain discovery and only relevant, non-spam replies. | `tg_smm_xb` |
| 3 | [НЕЙРОСЕТИ - Чат (ИИ)](https://t.me/neyroseti_chat) | 367 | Small, but one exact pain appeared: asking for a bot/site to animate a photo without hassle. | Description says any ad attempt is a ban. Do not post links unless admin approves or user explicitly opts in. | `tg_answer_neyroseti` |

## Evidence Signals

Representative public-message evidence from the read-only Telegram search:

- `marketplaces_chat`: a 2026-06-09 message asks for a strong marketplace
  infographic designer to make 12 WB/Ozon cards:
  https://t.me/marketplaces_chat/990385
- `wbnahodkychat`: a 2026-06-18 message asks for someone doing infographics:
  https://t.me/wbnahodkychat/1033103
- `designers_wb_ozon`: repeated 2026-06 posts advertise AI video for
  marketplace cards, including animated covers and 5-10 second AI video:
  https://t.me/designers_wb_ozon/63639
- `MP_partner`: a 2026-06-16 message asks whether minimalist or bright product
  card design works better:
  https://t.me/MP_partner/70072
- `neyroseti_chat`: a 2026-02-11 message asks for a bot or site to animate a
  photo "without hassle" using Kling/Veo-like tools:
  https://t.me/neyroseti_chat/1394

Pattern observed: the marketplace chats contain more seller intent, but many
messages are supply-side self-promo. The best route is not mass posting; it is
either an admin-approved case post or a genuinely useful reply to a specific
question.

## Competitor / Alternative Map

Direct marketplace-visual competitors:

| Tool | Positioning observed | Implication for Photozhab |
|---|---|---|
| [SellerDen AI](https://sellerden.ai/) | Upload product photo, then AI creates photos, video, infographic, and SEO description. | Strong direct seller competitor. Do not compete as a full seller platform; compete as fast Telegram pay-as-you-go visual generation. |
| [Neiro Card AI](https://neiro-card.ai/) | AI card generator for WB/Ozon/Yandex Market: background removal, AI infographics, product images, templates. Shows free tier and 1,490 rub/month plan. | Photozhab can undercut subscription friction with small paid actions and a free starter. |
| [Fabula AI](https://fabula-ai.com/) | AI cards/infographics for marketplaces, including AI photoshoot framing. | Same seller language; useful for copy inspiration. |
| [MPCard.AI](https://www.mpcard.ai/) | Marketplace cards with infographics and AI backgrounds. | Same visual outcome; emphasize Telegram speed and no heavy editor. |
| [Magvi](https://magvi.ai/) | Free online constructor, background removal/generation, infographic for marketplaces. | Competes on free utility; Photozhab should lead with video/photo animation and no setup. |
| [Sellermoon AI chat](https://sellermoon.ru/tools/ai-chat) | Marketplace AI chat for text, image, and video; observed 49 rub/15 days access. | Competes on seller workflow; Photozhab can avoid subscription and sell per result. |

Broad AI-bot competitors:

- [SyntX AI](https://syntx.ai/ru/) bundles 90+ AI models in Telegram and web.
  Its Telegram bot page says it includes Seedance, Kling, VEO, Midjourney, Nano
  Banana Pro and more: https://t.me/syntxaibot
- [YES AI forum](https://t.me/yes_ai_chat) is a 6k+ member AI forum tied to
  `@yes_ai_bot`, focused on Midjourney, Stable Diffusion and ChatGPT.
- [Chat AI Veo page](https://chataibot.ru/veo/) positions Veo access in
  Russian without VPN through site/Telegram/extension.

Competitors seen inside marketplace chats:

- ABCardo: "card for WB/Ozon in 5 minutes + A/B test" messaging.
- SSA Navigator bot: "AI marketer for WB/Ozon/Yandex Market" with AI visual,
  SEO/text and Rich-content messaging.
- InfoSellTGBot: "AI studio for infographics and neurophotoshoots" messaging.
- MatrixAi Bot: Telegram AI image tooling with upscale and image functions.

## Positioning To Test

Do not sell Photozhab as "another AI bot". Test these outcome-led angles:

1. "Сделать быстрый черновик карточки товара / белый фон / UGC-визуал прямо в Telegram."
2. "Оживить фото товара в короткое видео для карточки или соцсетей."
3. "Проверить 2-3 визуальные гипотезы до оплаты дизайнера."
4. "Veo/видео без VPN и подписок, цена видна до запуска."
5. "Не подписка: 30 кредитов бесплатно, дальше пополнение маленькими пакетами."

For sellers, avoid promising "полностью заменит дизайнера". Safer promise:
"быстро сделать вариант, проверить идею, подготовить референс/черновик".

## Outreach Drafts

Public answer when someone asks for product-card help:

```text
Если задача сейчас быстро проверить визуал до дизайнера, можно сначала сделать
черновик: фото товара -> чистый фон / UGC-кадр / короткое видео из фото.
Я тестирую Telegram-бота под такие AI-картинки и видео, там видно цену до
запуска и есть бесплатные стартовые кредиты. Если актуально, могу скинуть ссылку
или сделать один пример по вашему фото.
```

Admin placement pitch:

```text
Привет. Хочу аккуратно протестировать полезный пост для вашей аудитории селлеров:
как из обычного фото товара быстро получить AI-вариант карточки / UGC-фото /
короткое видео в Telegram без VPN.

Не массовая реклама: готов сделать 1-2 примера под товары участников и оформить
как кейс "до/после". Оплата/бартер по вашим правилам. Подскажите, какой формат
размещения у вас допустим?
```

Reply for AI chat when someone asks "where to animate a photo":

```text
Для фото->видео обычно смотрят Kling/Veo-подобные сервисы. Главный фильтр:
чтобы не требовали VPN/подписку до результата и чтобы цена была понятна до
запуска. Я тестирую Telegram-бота с таким сценарием; если можно тут делиться
ссылками или хотите в личку, скину.
```

## First Test Plan

1. Generate deep links:
   - `/admin_channels tg_chat_infographics`
   - `/admin_channels tg_paid_designerswb`
   - `/admin_channels tg_paid_wbnahodky`
   - `/admin_channels tg_admin_marketplace`
   - `/admin_channels tg_answer_neyroseti`
2. Prepare three demo assets:
   - Product photo -> clean marketplace card.
   - Product photo -> UGC-style image.
   - Product photo -> short video/photo animation.
3. Run one free/allowed public test in `chat_infographics`.
4. Ask admins for paid or approved case-post terms in the larger seller chats.
5. Monitor the exact evidence-message threads above for follow-up opportunities.
6. Score by starts, first generation, second generation, payment, and complaints,
   not by message count or views.

Stop conditions:

- Any complaint, deletion, warning, or admin objection.
- Starts without first generation.
- First generation without second generation or payment after enough attempts.

## Validation Notes

Commands and actions performed:

- Loaded local product/site docs and confirmed Photozhab pricing/offer surfaces.
- Used the existing Telethon E2E session in read-only mode to search public
  Telegram entities, messages, and chat descriptions.
- Used public web search for current AI marketplace-card and AI-video tools.

External actions avoided:

- No Telegram messages sent.
- No chat joins.
- No mass DM or user scraping.
- No VPS deploy.
- No Google Flow generation, captcha solve, Robokassa, Stars, or paid API use.
