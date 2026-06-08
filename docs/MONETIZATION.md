# MONETIZATION.md

## Модель монетизации Flow-бота

Обновлено: 2026-06-08.

Этот документ разделяет две разные валюты:

- **Google Flow credits** - себестоимость генерации на стороне Google Flow.
- **Кредиты бота** - розничная внутренняя валюта, которую пользователь покупает через Telegram Stars.

Их нельзя приравнивать 1:1. Цены в `flow_core.py` - это цены в кредитах бота.

## Проверенные внешние вводные

Официальная справка Google Flow на 2026-06-08 указывает:

- без подписки доступно 50 Google Flow credits в день;
- Google AI Plus дает 200 Google Flow credits в месяц;
- Google AI Pro дает 1000 Google Flow credits в месяц;
- Google AI Ultra $100 дает 10000 Google Flow credits в месяц;
- Google AI Ultra $200 дает 25000 Google Flow credits в месяц.

Официальная таблица Google Flow credit costs per generation:

| Модель Google Flow | Google Flow credits за 1 генерацию |
|---|---:|
| Veo 3.1 Lite | 10 |
| Veo 3.1 Fast | 20 |
| Veo 3.1 Quality | 100 |
| Gemini Omni Flash 4s | 15 |
| Gemini Omni Flash 6s | 20 |
| Gemini Omni Flash 8s | 25 |
| Gemini Omni Flash 10s | 30 |
| Gemini Omni Flash video edit | 40 |

Источники:

- Google Flow Help: `https://support.google.com/flow/answer/16526234`
- Google AI Pro benefits: `https://support.google.com/googleone/answer/14534406`
- Gemini subscriptions page: `https://gemini.google/us/subscriptions/`

## Допущения оператора

Базовый сценарий для текущей экономики:

| Параметр | Значение |
|---|---:|
| Google AI Pro через доступный оператору способ | ~500 RUB/year |
| Дополнительный запас на аккаунт/посредника | +100-400 RUB/year |
| Плановая себестоимость одного аккаунта с 1000 credits/month | 600-900 RUB/year |
| Годовая квота аккаунта | 12000 Google Flow credits |
| Себестоимость 1 Google Flow credit | ~0.05-0.075 RUB |

Это очень дешевый сценарий. Если окажется, что подписка за ~500 RUB/year недоступна или дает не 1000 credits/month, а разовый пакет, нужно пересчитать цены сразу.

Консервативный альтернативный сценарий:

| Параметр | Значение |
|---|---:|
| Google AI Pro по публичной цене США | $19.99/month |
| Google Flow credits | 1000/month |
| Себестоимость 1 Google Flow credit | ~$0.02 |

В этом сценарии дешевые цены на видео быстро становятся убыточными. Поэтому текущая сетка ниже рассчитана под операторский cheap-Pro сценарий, а не под публичную цену Google.

## Telegram Stars и цена кредита бота

Текущие пакеты в `flow_core.py`:

| Пакет | Stars | Кредиты бота | Кр/Star |
|---|---:|---:|---:|
| small | 75 | 100 | 1.33 |
| medium | 200 | 290 | 1.45 |
| large | 450 | 700 | 1.56 |
| xl | 900 | 1500 | 1.67 |

При ориентире ~$0.013 net за 1 Star оператор получает примерно:

| Пакет | Net revenue | Net за 1 кредит бота |
|---|---:|---:|
| small | ~$0.975 | ~$0.00975 |
| medium | ~$2.60 | ~$0.00897 |
| large | ~$5.85 | ~$0.00836 |
| xl | ~$11.70 | ~$0.00780 |

Для планирования используем крупные пакеты: **$0.0078-0.0084 за 1 кредит бота**.

## Розничные цены в боте

Изображения оставлены без изменений:

| Действие | Кредиты бота |
|---|---:|
| 1 image generation | 10 |
| edit / myphoto | 10 |
| revary / variations | 20 |
| up2x / realup | 5 |
| original download | 0 |

Видео обновлено: это уже не Google Flow credits, а розничные bot credits.

| Режим | Цена в кредитах бота | Логика |
|---|---:|---|
| Omni Flash 4s | 20 | дешевый вход, но выше Google-cost 15 |
| Omni Flash 6s | 30 | шаг по длине и cost 20 |
| Omni Flash 8s | 35 | базовый длиннее, cost 25 |
| Omni Flash 10s | 45 | самая длинная Omni, cost 30 |
| Veo Lite | 30 | доступный Veo, cost 10 |
| Veo Fast | 60 | основной платный Veo, cost 20 |
| Veo Quality | 290 | премиум; один medium-pack покупает одно Quality-видео |

Доплаты для режимов с референсами:

| Режим | Цена |
|---|---:|
| Ingredients | selected video model + 10 credits |
| Frames | selected video model + 20 credits |

После capture и включения generation UI для Ingredients/Frames должен использовать
`Veo Fast` как дефолт, поэтому стартовые цены:

| Режим | Цена |
|---|---:|
| Ingredients -> Veo Fast | 70 |
| Frames -> Veo Fast | 80 |

Текущее поведение кода: фото собираются и загружаются в Flow, но генерация
Ingredients/Frames остается fail-closed (`vid_gen_blocked`, без списания),
потому что live-проверка 2026-06-08 показала HTTP 400 для угаданных
`startImage`/`endImage` полей на `video:batchAsyncGenerateVideoText`.

## Почему так

1. `Veo Quality = 290 credits` ровно соответствует пакету `medium` на 200 Stars. Это делает premium-цену понятной: купил medium - получил одно Quality-видео.
2. `Veo Fast = 60 credits` оставляет достаточно места между Lite и Quality и не превращает Fast в слишком дешевый заменитель Quality.
3. Omni Flash остается входным продуктом, но больше не продается по ошибочно низким `7/10/12/15`.
4. Ingredients/Frames дороже обычного text-to-video, потому что они используют upload, reference media и имеют больший риск 400/повторов/поддержки.

## Маржинальность

При cheap-Pro сценарии с 600-900 RUB/year за аккаунт и 12000 Google Flow credits/year себестоимость видео очень низкая:

| Модель | Google Flow credits | Cost при 0.075 RUB/G-credit | Bot credits | Net revenue при $0.0078/credit и 90 RUB/USD |
|---|---:|---:|---:|---:|
| Omni Flash 4s | 15 | ~1.13 RUB | 20 | ~14.0 RUB |
| Omni Flash 10s | 30 | ~2.25 RUB | 45 | ~31.6 RUB |
| Veo Lite | 10 | ~0.75 RUB | 30 | ~21.1 RUB |
| Veo Fast | 20 | ~1.50 RUB | 60 | ~42.1 RUB |
| Veo Quality | 100 | ~7.50 RUB | 290 | ~203.6 RUB |

Если подписка окажется не monthly-credits, а разовым 1000-credit пакетом на год, себестоимость станет примерно в 12 раз выше. Даже тогда текущие цены остаются разумными для большинства режимов, но маржа по дешевым видео станет заметно ниже.

## Что проверить live

1. Рабочий reCAPTCHA action для `video:batchAsyncGenerateVideoText`, чтобы убрать перебор и поставить его первым.
2. Реальное списание Google Flow credits по каждой модели после генерации.
3. Реальное списание и API shape для Ingredients/Frames.
4. Доступность подписки Google AI Pro за ~500 RUB/year и факт, что она дает 1000 credits/month.
5. Реальный payout Telegram Stars после Fragment/TON/налогов.

## Правило пересчета

После 20-50 live-генераций подставить фактические числа:

```text
profit_per_generation =
  bot_credits_price * net_usd_per_bot_credit
  - google_flow_credits_spent * cost_usd_per_google_flow_credit
  - retry_failure_allowance
```

Если маржа дешевых видео ниже 40%, поднять Omni и Veo Lite/Fast на 20-30%. `Veo Quality` держать как главный маржинальный продукт и не опускать ниже 250 credits.
