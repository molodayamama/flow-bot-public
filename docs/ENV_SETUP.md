# ENV_SETUP.md

Практическая инструкция по заполнению `.env` для `flow_bot.py`.

`.env.example` намеренно шире минимального `.env`: он покрывает обычный запуск,
пул Google-аккаунтов, captcha fallback, прокси, Telegram E2E tester и пути к
runtime-файлам. Для старта не нужно заполнять каждую строку.

## Минимальный `.env`

Достаточно для одиночного запуска бота с уже залогиненным Chrome-профилем:

```dotenv
TELEGRAM_TOKEN=123456789:replace_me
OWNER_ID=123456789
USER_DATA_DIR=./google_profile
CAPTCHA_PROVIDER=browser
BOT_USERNAME=your_bot_username
```

Пояснения:

- `TELEGRAM_TOKEN` - токен от BotFather.
- `OWNER_ID` - твой Telegram user id. Владелец автоматически считается админом.
- `USER_DATA_DIR` - папка Chrome-профиля, где Google-аккаунт уже залогинен.
- `CAPTCHA_PROVIDER=browser` - бесплатный основной путь через живую
  браузерную сессию.
- `BOT_USERNAME` - нужен для красивых referral/channel deep links. Если пусто,
  бот попробует получить username через Telegram `get_me()` на старте.

## Рекомендуемый рабочий `.env`

Для текущего проекта удобнее держать чуть больше строк:

```dotenv
TELEGRAM_TOKEN=123456789:replace_me
OWNER_ID=123456789
ADMIN_IDS=123456789

USER_DATA_DIR=./google_profile
FLOW_ACCOUNT_ID=main
PER_USER_PROJECTS=1

PROXY_URL=
BROWSER_PROXY_URL=
API_PROXY_URL=
TG_PROXY_URL=

CAPTCHA_PROVIDER=browser
TWOCAPTCHA_KEY=
CAPMONSTER_KEY=

BOT_USERNAME=your_bot_username
STARS_TO_RUB=1.3
```

`ADMIN_IDS` можно не указывать, если админ только владелец: код делает
`ADMIN_IDS | OWNER_IDS`. Но явная строка снижает путаницу.

## Если нужен платный captcha fallback

`browser` не использует платные ключи. Если хочешь, чтобы бот сначала пробовал
browser token, а затем CapMonster/2Captcha как fallback:

```dotenv
CAPTCHA_PROVIDER=auto
CAPMONSTER_KEY=replace_me
TWOCAPTCHA_KEY=replace_me
```

Оставь ключи пустыми, если не хочешь риска платных решений.

## Прокси

Пустая строка означает "без прокси":

```dotenv
PROXY_URL=
BROWSER_PROXY_URL=
API_PROXY_URL=
```

`off`, `none`, `direct`, `false`, `0` тоже отключают прокси и полезны, если
`PROXY_URL` задан, но конкретный слой надо пустить напрямую:

```dotenv
PROXY_URL=http://REDACTED:REDACTED@proxy.example.invalid:8080
BROWSER_PROXY_URL=off
API_PROXY_URL=off
```

Назначение:

- `PROXY_URL` - общий fallback для браузера/API/captcha.
- `BROWSER_PROXY_URL` - только Playwright Chrome.
- `API_PROXY_URL` - только HTTP-вызовы Flow API.
- `TG_PROXY_URL` - только Telegram Bot API SOCKS proxy.
- `TG_E2E_PROXY_URL` - только Telegram E2E tester, не сам бот.

## Несколько Google-аккаунтов

Каждый аккаунт должен иметь отдельный залогиненный Chrome-профиль:

```dotenv
FLOW_ACCOUNTS=main=./google_profile;acc2=./google_profile_acc2
FLOW_ACCOUNTS_STATE_FILE=flow_accounts_state.json
```

Если `FLOW_ACCOUNTS` не задан, бот работает в одиночном режиме на
`USER_DATA_DIR`.

Per-account proxy можно добавить к нужной записи:

```dotenv
FLOW_ACCOUNTS=main=./google_profile;acc2=./google_profile_acc2|proxy=http://127.0.0.1:8118
```

`proxy=` применяется и к Playwright Chrome, и к HTTP-вызовам Flow API. Если
нужно развести их, используйте `browser_proxy=` и `api_proxy=`. Значения
`off`/`none`/`direct` отключают прокси для конкретной части.

## Telegram E2E tester

Эти строки не нужны для работы бота. Они нужны только для
`telegram_bot_tester.py`:

```dotenv
TG_API_ID=123456
TG_API_HASH=replace_me
TG_PHONE=+10000000000
TG_E2E_PROXY_URL=
```

Внешние режимы tester всегда требуют `--approve-external-action`.

## Robokassa / SBP

Keep Telegram Stars enabled; Robokassa is an additional top-up path. Do not
commit real passwords.

```dotenv
ROBOKASSA_ENABLED=1
ROBOKASSA_MERCHANT_LOGIN=photozhab
ROBOKASSA_HASH_ALGO=sha256
ROBOKASSA_PASSWORD1=replace_me
ROBOKASSA_PASSWORD2=replace_me
ROBOKASSA_TEST_PASSWORD1=replace_me
ROBOKASSA_TEST_PASSWORD2=replace_me
ROBOKASSA_TEST=1
ROBOKASSA_INC_CURR_LABEL=SBP
ROBOKASSA_PUBLIC_BASE_URL=https://pay.photozhab.ru
ROBOKASSA_WEB_HOST=127.0.0.1
ROBOKASSA_WEB_PORT=8081
ROBOKASSA_SCOPE=consumer
ROBOKASSA_CONSUMER_RESULT_URL=http://127.0.0.1:8081/robokassa/result
ROBOKASSA_SELLER_RESULT_URL=http://127.0.0.1:8082/robokassa/result
ROBOKASSA_CONSUMER_BOT_USERNAME=photozhab_bot
ROBOKASSA_SELLER_BOT_USERNAME=photozhab_wb_bot
```

Seller Card/SBP uses the same merchant credentials, but `BOT_MODE=seller`,
`ROBOKASSA_WEB_PORT=8082`, and `ROBOKASSA_SCOPE=seller`. New invoices include a
signed `Shp_bot` value (`consumer` or `seller`). If Robokassa sends the ResultURL
to the wrong local process, that process forwards the callback to the configured
localhost result URL for the target scope before credits are issued. Old invoices
without `Shp_bot` remain consumer-only for retry compatibility.

## TGStat

The admin advertising calculator can fetch Telegram channel reach via TGStat.
Keep the real token only in production `.env` files.

```dotenv
TGSTAT_API_TOKEN=replace_me
TGSTAT_CACHE_TTL_SEC=21600
```

Robokassa cabinet URLs:

```text
Result URL:  https://pay.photozhab.ru/robokassa/result  POST
Success URL: https://pay.photozhab.ru/robokassa/success GET
Fail URL:    https://pay.photozhab.ru/robokassa/fail    GET
```

VPS nginx must proxy `/robokassa/` on `pay.photozhab.ru` to the bot callback
server, usually `http://127.0.0.1:8081`.

## Runtime paths

Эти переменные обычно не надо задавать: у них есть безопасные default-значения,
а файлы добавлены в `.gitignore`.

```dotenv
USER_PROJECTS_FILE=user_projects.json
USER_CREDITS_FILE=user_credits.json
PAYMENTS_FILE=payments.json
METRICS_DB=metrics.db
EDIT_CAPTURE_FILE=edit_capture.json
UPLOAD_CAPTURE_FILE=upload_capture.json
UPSCALE_CAPTURE_FILE=upscale_capture.json
```

Меняй их только если сознательно переносишь runtime state.

## Что не делать

- Не коммить `.env`.
- Не вставлять реальные токены в `.env.example` или docs.
- Не запускать `login.py` для "проверки": он пересоздаёт `google_profile/`.
- Не копировать inline-комментарии после секретов, если значение может содержать
  `#`; безопаснее держать комментарии отдельной строкой.
