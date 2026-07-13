# Flow Telegram Bot

## Что это

Telegram-бот генерации картинок и видео поверх гибрида «браузерная сессия +
HTTP»: `SessionKeeper` держит живую Chrome-сессию через Playwright (перехват
свежих Bearer-токенов и reCAPTCHA), а сами запросы генерации уходят напрямую
через aiohttp-клиент. UX — кнопочный: постоянная нижняя клавиатура, инлайн-меню,
визард генерации, пер-картиночные действия (правка / вариации / апскейл),
кредиты и оплата Telegram Stars.

## Требования

- Python 3.11
- Google Chrome (устанавливается через Playwright, бот использует `channel="chrome"`)
- Залогиненный Google-аккаунт в персистентном Chrome-профиле `google_profile/`

## Установка

```bash
pip install -r requirements.txt   # действие с одобрения оператора (см. VALIDATION.md)
playwright install chrome         # отдельный шаг: Playwright ставит сам браузер
cp .env.example .env              # затем заполнить значения (токен, ID админов и т.д.)
```

Минимальный и рекомендуемый `.env` описаны в `docs/ENV_SETUP.md`. Шаблон
`.env.example` шире обычного запуска: он также покрывает multi-account,
captcha fallback, Telegram E2E tester и runtime paths.

## Запуск

```bash
python flow_bot.py
```

Требуется залогиненный Chrome-профиль в `google_profile/` (бот откроет его через
Playwright). Запуск бота — внешнее/статусное действие: не запускать как рутинную
проверку (см. `VALIDATION.md`).

## Несколько Google-аккаунтов (пул)

Бот умеет работать с пулом Flow-аккаунтов: у каждого свой Chrome-профиль и своя
browser-сессия, юзеры закрепляются за аккаунтом (sticky), упавший аккаунт
уходит в кулдаун и роутинг обходит его (failover). Включается одной строкой в
`.env` — код менять не нужно:

```bash
# записи "id=путь_к_профилю" через ';' (разделитель именно '=': в Windows-путях есть ':')
FLOW_ACCOUNTS=main=./google_profile;acc2=./google_profile_acc2
```

Каждый профиль должен быть заранее залогинен в свой Google-аккаунт. Привязки
юзеров хранятся в `flow_accounts_state.json` (gitignored). Админ-команды:
`/admin_accounts` (состояние пула + статистика), `/acc_off <id>` / `/acc_on <id>`
(ручное отключение/включение аккаунта). Без `FLOW_ACCOUNTS` бот работает в
одиночном режиме на `USER_DATA_DIR`, как раньше.

## Реклама по каналам (deep-link атрибуция)

Каждому рекламному каналу даём свою ссылку и считаем, сколько юзеров с него
пришло (first-touch — первый канал «забивает» юзера навсегда):

```
t.me/<bot>?start=seed_<ярлык_канала>
```

Готовую ссылку выдаёт `/admin_channels <ярлык>` (ярлык: латиница/цифры/`_`/`-`,
до 32 символов). Сводку «привлечено / платящие / выручка» по всем каналам —
`/admin_channels` без аргумента. Это не рефералка: кредиты никому не начисляются,
только учёт трафика (таблица `acquisitions` в `metrics.db`). Нужен `BOT_USERNAME`
в `.env` (или бот возьмёт его из `get_me()` на старте).

## Офлайн-проверки (безопасные, без сети и браузера)

```bash
python -m unittest discover -s tests -p "test_*.py"
python -m py_compile flow_bot.py flow_core.py flow_copy.py
```

## Структура проекта

| Путь | Назначение |
| --- | --- |
| `flow_bot.py` | composition root: конфигурирует runtime, зависимости и Telegram-роутеры |
| `flow_provider/session_keeper.py` | живая Playwright-сессия, Bearer/cookies/reCAPTCHA и upload |
| `flow_provider/http_client.py` | прямые Flow HTTP-запросы картинок, видео, правок и апскейла |
| `flow_provider/client.py` | совместимый re-export старого import-path; реализации классов здесь нет |
| `flow_core.py` | чистая логика: payload/парсинг, кредиты и цены, сторы (stdlib-only) |
| `flow_copy.py` | весь русский микрокопирайт UI (кнопки, экраны, ошибки) |
| `metrics.py` | метрики в SQLite (`metrics.db`): латентность, джобы, выручка |
| `prompts_lib.py` | версионированные промпт-файлы из `prompts/` (PromptOps) |
| `tools/` | capture/стресс-утилиты (перехват видео-эндпоинтов и т.п.) |
| `flow_profiler/` | safety-first CLI-профайлер квоты Flow (`flow_quota_profiler.py`) |
| `tests/` | офлайн unittest-сьют (меню, правки, видео, метрики, профайлер) |
| `docs/` | монетизация, видео-UX, рефералка, профайлер и др. |

## Безопасность

- Все секреты — только в `.env` (шаблон: `.env.example`). Никогда не коммитить
  `.env`, `.env_flow`, `api_config.json`, `google_profile/`, `proxylist*.txt`,
  `*.har` — всё это в `.gitignore`.
- Runtime-стейт (`user_credits.json`, `user_projects.json`, `payments.json`,
  `metrics.db`, capture-файлы) тоже gitignored — не коммитить.
- Какие проверки безопасны офлайн, а какие требуют одобрения оператора —
  `VALIDATION.md`. Процесс работы и зоны риска — `AGENTS.md`.
