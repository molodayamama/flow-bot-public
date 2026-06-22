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
  and photo animation from 50 credits.
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

## Automated Pain Scan 2026-06-19

Read-only scanner: `tools/lead_pain_scan.py`.

Final strict command used:

```text
python tools/lead_pain_scan.py --approve-external-action --days 30 --limit-per-term 80 --max-samples-per-chat 10
```

Final strict lead export run time: 2026-06-18 22:29 UTC / 2026-06-19 local.

Generated local artifacts:

- `lead_scan_runs/pain_leads.csv` - safe review queue with public-message lead
  ids, context, priority, snippets, and links.
- `lead_scan_runs/pain_leads.json` - same queue as structured JSON.
- `lead_scan_runs/pain_dashboard.html` - interactive-looking local dashboard.
- `lead_scan_runs/pain_dashboard.png` - browser screenshot of the dashboard.
- `lead_scan_runs/pain_infographic.svg` - standalone visual summary.

Aggregate result:

- Chats scanned: 10.
- Keyword-matched public messages: 1,328.
- Candidate direct-pain messages: 44.
- Unique candidate pain authors counted in memory: 11.
- Grouped safe lead candidates exported: 11.
- Supply / competitor messages: 1,214.

Category split:

- `product_card`: 44.
- `ai_tool`: 2.
- `product_photo`: 1.

Chat split:

| Chat | Candidate pain msgs | Unique candidate authors | Supply msgs | Read |
|---|---:|---:|---:|---|
| `chat_infographics` | 30 | 5 | 340 | Best immediate listening pool; repeated direct requests for card/infographic help. |
| `wbnahodkychat` | 9 | 1 | 334 | One repeated high-fit infographic request; good for manual review. |
| `MP_partner` | 3 | 3 | 57 | Broader seller questions; useful for helpful non-spam replies. |
| `marketplaces_chat` | 2 | 2 | 35 | Lower volume but includes higher-quality seller requests. |
| `designers_wb_ozon` | 0 | 0 | 295 | Mostly supply/ad inventory; use admin-approved placement, not public replies. |
| `dizainer_wb` | 0 | 0 | 117 | Mostly supply/ad inventory; use admin-approved placement. |
| `wildberries_service` | 0 | 0 | 18 | No strict visual-pain candidates after ad/noise filtering. |
| `xb_prosmm_chat` | 0 | 0 | 15 | Generic AI/SMM signals were filtered out as non-Photozhab fit. |
| `neyroseti_chat` | 0 | 0 | 3 | No fresh direct pain in the 30-day window. |
| `sellery_ozon` | 0 | 0 | 0 | No useful volume in this scan. |

Interpretation:

- Treat 11 grouped candidates as a manual review queue, not a contact list.
- Before replying, inspect the message manually. The scanner favors recall for
  marketplace visual pain, but public chats still contain repeated ads and
  operational WB/Ozon questions that may not fit Photozhab.
- Do not auto-DM or mass-export identities. The script counts unique authors in
  memory only and does not write sender ids, usernames, or contact lists.
- Exported `lead_id` values are public message ids in the form
  `chat_username/message_id`, not Telegram user ids.
- Raw local reports are written under `lead_scan_runs/` and are intentionally
  gitignored.

## Expanded Done-For-You Scan 2026-06-19

The first strict scan (10 chats, 11 leads) was too narrow and over-indexed on
"give me a tool/bot" curiosity. The seller demand we actually want is
done-for-you: "ищу/нужен дизайнера, инфографиста, исполнителя; сделать карточки
под ключ; делегировать". This pass widens sources and re-scores for that intent.

New tooling:

- `tools/lead_chat_discovery.py` - read-only global Telegram search across
  ~35 seed queries. Collects public seller/marketplace/design *megagroups*
  (username + member count). Does not join, does not export member identities.
  Output: `lead_scan_runs/discovered_chats.json` (92 megagroups, 300+ members).
- `tools/render_dashboard_png.py` - renders the local HTML/SVG to PNG via
  Playwright Chromium (offline, local file only).

Classifier changes in `tools/lead_pain_scan.py`:

- New `SERVICE_FIT_HINTS` (done-for-you buyer phrases) plus
  `SELLER_SEEKING_HINTS` to exclude designers/freelancers looking for work.
- New per-lead `lead_type`: `done_for_you` > `advice` > `signal`, used for
  ranking, scoring (+6 for done-for-you), CSV/JSON/HTML/SVG, and KPIs.
- The done-for-you path still rejects supplier self-ads via `SUPPLY_ONLY_HINTS`
  (e.g. "Нужна инфографика? Создаю карточки…", "Я дизайнер, помогу вам"),
  which removed ~500 rhetorical-hook false positives in testing.
- `--chats-file` / `--max-chats` flags to feed the discovered megagroup list.

Command used:

```text
python tools/lead_pain_scan.py --approve-external-action \
  --chats-file lead_scan_runs/discovered_chats.json \
  --days 120 --limit-per-term 50 --max-samples-per-chat 6
```

Aggregate result:

- Chats scanned: 92.
- Keyword-matched public messages: 15,472.
- Candidate direct-pain messages: 1,798.
- Unique candidate pain authors counted in memory: 364.
- Grouped safe lead candidates exported: 387.
  - `done_for_you` (wants to delegate the work): 147.
  - `advice` (questions / comparison): 239.
- Supply / competitor messages: 4,121.
- Priority split: high 288, medium 98, low 1.

Top chats by candidate pain: `Postavshchiki_Vayldberriz_OZON_C`,
`wildberriestraderchat`, `OZON_postavshchiki_i_menedzhery`,
`Menedzhery_marketpleysov_Chat`, `postmpchat`, `Postavwiki_na_WildBerries`.

Representative done-for-you lead ids (highest score, manual-review queue):

- `postmpchat/203716` - ищет дизайнера инфографики, нужно разработать обложку
  карточки конкретного товара.
- `proffreelancee_chat_pog/257075` - нужен дизайнер инфографики для обложки.
- `postmpchat/202913` - ищет исполнителя сделать короткое AI-видео по фото.
- `Postavwiki_na_WildBerries/1771403` - ищет дизайнера карточек для Wildberries.
- `wildberriestraderchat/3028811` - ищет дизайнера инфографики для WB/Ozon.
- `mplace_wildberries_ozon_help/2016226` - нужен дизайнер для упаковки новых
  товаров для WB.

Guardrails are unchanged: this is a manual review queue, not a contact list;
`lead_id` is a public `chat_username/message_id`; no sender ids, usernames,
phone numbers, or member lists are exported; outputs stay gitignored.

## Second Pass - Tool-Seeking Segment 2026-06-19

Goal: also surface tool-seekers - people who want a bot / neural net / service to
generate the visual themselves (a direct fit for the Photozhab bot link).

Changes:

- `tools/lead_chat_discovery.py`: added AI / neural-net / SMM seed queries; the
  discovered list grew to 102 public megagroups.
- `tools/lead_pain_scan.py`: new `TOOL_SEEKING_HINTS` and a `tool_seeking`
  `lead_type` (ranked above advice). It requires a genuine asking frame
  ("какой нейросетью?", "посоветуйте бот", "ищу сервис") and rejects bot ads via
  `SUPPLY_ONLY_HINTS` plus a call-to-action guard (an `@handle` / `t.me/` / `http`
  link marks an ad, not a seeker).

Result (102 chats, 120-day window): 737 grouped leads - 292 done_for_you, 441
advice, and only **3 tool_seeking** (all weak: a news post, an off-topic message,
and a voice-not-image request).

Key finding: a tool-seeking buyer ("посоветуйте бота для карточек") is almost
absent in these chats. Instead the chats are saturated with competitor "upload
photo -> finished card" bot ads (SSA Navigator, OblozhkaAI, ABCardo, etc.). So
the demand for a card bot is market-validated, but the supply side is crowded and
tool-seekers do not post requests - they use the advertised bots or hire a
person. Actionable segments stay done_for_you and advice; see
`docs/PRODUCT_RECOMMENDATIONS_2026-06-19.md` for how to route the bot through
those two entries rather than as "yet another bot".

Command used:

```text
python tools/lead_pain_scan.py --approve-external-action \
  --chats-file lead_scan_runs/discovered_chats.json \
  --days 120 --limit-per-term 50 --max-samples-per-chat 6
```

## Validation Notes

Commands and actions performed:

- Loaded local product/site docs and confirmed Photozhab pricing/offer surfaces.
- Used the existing Telethon E2E session in read-only mode to search public
  Telegram entities, messages, and chat descriptions.
- Used public web search for current AI marketplace-card and AI-video tools.
- Ran the read-only Telegram pain scanner across the 10 shortlisted chats.
- Generated safe local lead exports and dashboard/infographic files under
  `lead_scan_runs/`.
- Rendered `pain_dashboard.html` through local Microsoft Edge via Playwright and
  saved `pain_dashboard.png` to verify the visual report.

External actions avoided:

- No Telegram messages sent.
- No chat joins.
- No mass DM or user scraping.
- No raw Telegram sender ids, usernames, or contact lists exported.
- No VPS deploy.
- No Google Flow generation, captcha solve, Robokassa, Stars, or paid API use.
