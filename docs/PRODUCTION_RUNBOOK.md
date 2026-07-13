# Production runbook

Статус: обязательный эксплуатационный контур для Telegram consumer, seller и
MAX. Все команды выполняются на VPS из `/opt/geminifree`, если не сказано иное.
Никакие проверки ниже не требуют генерации медиа или расхода 2Captcha/Flow.

## 1. Инварианты перед запуском

- Python 3.11 и существующее виртуальное окружение `/opt/geminifree/.venv`.
- Chrome/Playwright и `xvfb-run` установлены отдельно и уже проверены оператором.
- Репозиторий не содержит tracked-изменений; runtime-файлы остаются ignored.
- `.env` и, если seller включён, `.env.seller` принадлежат сервисному
  пользователю и имеют режим `0600`.
- Consumer использует `CREDITS_SQLITE=1` и свой `METRICS_DB`.
- Seller использует `CREDITS_SQLITE=1` и отдельный `METRICS_DB`, например
  `metrics_seller.db`. Иначе одинаковые Telegram ID объединят два независимых
  кошелька.
- Каждый путь к SQLite/JSON state находится внутри `/opt/geminifree`; его
  родительский каталог существует и доступен сервисному пользователю на запись.
- MAX в production работает только через HTTPS webhook на порту 443, с
  непустым webhook secret, постоянным inbox DB и доверенным CA bundle при
  необходимости.
- Robokassa использует публичный HTTPS base URL; Result URL проксируется на
  consumer-процесс. Секреты никогда не передаются в CLI и не пишутся в журнал.

Проверка без сетевых запросов:

```bash
cd /opt/geminifree
chmod 600 .env
.venv/bin/python tools/production_preflight.py --root . --env-file .env
test ! -r .env.seller || {
  chmod 600 .env.seller
  .venv/bin/python tools/production_preflight.py --root . --env-file .env.seller
}
```

Ошибка preflight блокирует запуск. Не отключать проверку для обхода ошибки.
Первый запуск с `CREDITS_SQLITE=1` идемпотентно импортирует существующий JSON;
исходный JSON сохранить как часть backup до запуска.

## 2. Reverse proxy и сервисы

В существующий TLS server nginx включить содержимое
`deploy/nginx/geminifree-locations.conf.example`. Затем:

```bash
sudo nginx -t
sudo systemctl reload nginx
sudo install -m 0755 deploy/bin/geminifree-bot-run /usr/local/bin/
sudo install -m 0755 deploy/bin/geminifree-seller-bot-run /usr/local/bin/
sudo install -m 0644 deploy/systemd/geminifree-bot.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/geminifree-seller-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable geminifree-bot
```

Seller включать только при готовом `.env.seller`:

```bash
sudo systemctl enable geminifree-seller-bot
```

MAX webhook и Robokassa доступны снаружи только через nginx. Детальный
`/max/health` закрыт от внешней сети и проверяется локально.

## 3. Backup, проверка и retention

SQLite копируется через SQLite backup API, поэтому WAL-состояние попадает в
согласованный снимок. JSON копируется как файл. Manifest содержит размер и
SHA-256 каждого объекта.

```bash
sudo install -d -m 0700 /var/backups/geminifree
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
args=(--env-file .env)
test ! -r .env.seller || args+=(--env-file .env.seller)
.venv/bin/python tools/runtime_backup.py backup \
  --root . --output "/var/backups/geminifree/runtime-${stamp}" "${args[@]}"
.venv/bin/python tools/runtime_backup.py verify \
  "/var/backups/geminifree/runtime-${stamp}"
```

Backup содержит финансовый и пользовательский state, но не `.env`, browser
profiles и медиа. Его следует шифровать и копировать на отдельный носитель по
политике оператора. Browser profiles архивируются отдельно только при
остановленном consumer-сервисе. Минимальная политика: ежедневный backup, 7
дневных + 4 недельных копии, ежемесячная тестовая restore-проверка в отдельный
каталог. Удалять копии только после успешного `verify` более новой копии.

## 4. Deploy

До deploy обязательны:

1. Зелёный GitHub Actions `offline-validation` для точного SHA.
2. Reviewed diff и отсутствие секретов/runtime-state в commit.
3. Для основной ветки — включённая GitHub branch protection: required status
   check `test`, запрет force-push/delete, pull request перед merge. Это настройка
   репозитория, а не код.
4. Отдельно согласованное обновление зависимостей, если изменён
   `requirements.txt`; deploy сам не мутирует venv.

Запуск точного проверенного SHA:

```bash
cd /opt/geminifree
sudo DEPLOY_BRANCH=main DEPLOY_SHA=<green-commit-sha> ./deploy.sh
```

Скрипт:

- отказывается работать с tracked-изменениями;
- проверяет, что SHA принадлежит удалённой ветке;
- до checkout создаёт и проверяет backup из consumer и seller env;
- checkout делает в detached mode по неизменяемому SHA;
- запускает secret audit, compileall и production preflight;
- выполняет compileall/preflight от `SERVICE_USER=bot`, поэтому проверяются
  реальные права сервисного процесса, а не привилегии root;
- перезапускает только установленные сервисы и проверяет `active`;
- при ошибке возвращает прежний code SHA и уже затронутые сервисы.

Runtime-state автоматически не откатывается: старый код может не понимать
частично применённую новую схему, поэтому решение о restore принимает оператор.

## 5. Проверка после deploy

```bash
sudo systemctl is-active geminifree-bot
sudo systemctl is-enabled geminifree-bot
sudo journalctl -u geminifree-bot --since "10 minutes ago" --no-pager
curl --fail --silent --show-error http://127.0.0.1:8081/max/health
```

Если seller включён, те же две systemd-проверки и journal выполнить для
`geminifree-seller-bot`. Для MAX ожидаются HTTP 200, `ready: true`, живые workers
и `dead: 0`. `pending` может кратковременно быть больше нуля. HTTP 503 означает
неподписанный webhook или остановленные workers; `dead > 0` — degraded и требует
разбора журнала/очереди.

Безопасный smoke: `/start`, справка, баланс, навигация по wizard без финального
запуска, открытие страницы пополнения без оплаты. Генерация, реальная оплата,
2Captcha и принудительное обновление browser profile — только после явного
согласования стоимости и риска.

## 6. Rollback и restore

При deploy-ошибке code rollback выполняется автоматически. Для ручного rollback:

```bash
cd /opt/geminifree
sudo systemctl stop geminifree-bot geminifree-seller-bot
git checkout --detach <previous-known-good-sha>
```

State восстанавливать только если подтверждена несовместимость/повреждение и
оба сервиса остановлены:

```bash
.venv/bin/python tools/runtime_backup.py verify <backup-directory>
.venv/bin/python tools/runtime_backup.py restore <backup-directory> \
  --root /opt/geminifree --approve-restore --service-stopped
.venv/bin/python tools/production_preflight.py --root . --env-file .env
sudo systemctl start geminifree-bot
test ! -r .env.seller || sudo systemctl start geminifree-seller-bot
```

Обе restore-опции обязательны. Restore отвергает повреждённый manifest,
несовпавшие хеши, повреждённый SQLite и symlink-выход из root.

## 7. Инцидент и ротация секретов

Application logging is fail-closed: the composition root installs a
process-wide redaction boundary after `logging.basicConfig`. Do not weaken it or
log raw response bodies, signed media URLs, payment/provider identifiers,
browser URLs, cookies, tokens, e-mail addresses, or arbitrary exception text.
`FLOW_BROWSER_API_KEY` belongs only in the protected runtime `.env`; consumer
production preflight rejects a missing value without printing it.

При подозрении на утечку: остановить затронутый ingress, ротировать Telegram/MAX
tokens, Robokassa passwords, webhook secret, captcha key, proxy credentials и
browser session; удалить старую MAX subscription; проверить git history и
journal на значения; затем preflight, restart и health check. Не публиковать
секреты в issue, commit, terminal transcript или HANDOFF.

Критические сигналы: сервис не `active`, MAX health 503, `dead > 0`, повторные
платёжные callback с ошибками settlement, рост refund после delivery failure,
неожиданная смена Flow-account cooldown. В инциденте сначала сохранить verified
backup и журнальный интервал, затем менять state.
