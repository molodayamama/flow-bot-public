# VALIDATION.md

Validation must be chosen by risk level. Prefer offline checks first. Network,
paid, account-mutating, or browser-profile-mutating checks require explicit
task approval.

## Security and log-boundary checks

Run before every production-oriented commit:

```bash
python tools/check_tracked_secrets.py
python -m unittest discover -s tests -p "test_tracked_secret_audit.py"
python -m unittest discover -s tests -p "test_logging_redaction.py"
python -m unittest discover -s tests -p "test_production_preflight.py"
python -m unittest discover -s tests -p "test_task_supervisor.py"
```

The log test covers formatted arguments, exception tracebacks, handlers created
after startup, and source guards against provider bodies/identifiers. Consumer
production preflight requires `FLOW_BROWSER_API_KEY` from the runtime env; it
must never be copied into source, templates, test fixtures, logs, or HANDOFF.
The task-supervisor test locks ownership and deterministic shutdown for every
composition-level long-running loop; aiohttp continues to own webhook workers.

## Public landing and SEO checks

Run the static contracts whenever public landing, legal, crawl or deploy-copy
assets change:

```bash
python -m pytest tests/test_landing_seo.py tests/test_deployment_assets.py tests/test_flow_menu.py::LandingStaticContentTests -q
bash -n deploy.sh
```

The SEO contract checks unique title, description, H1 and canonical metadata;
parseable JSON-LD; descriptive internal links; real sitemap targets; restrictive
robots directives for admin/API surfaces; and `noindex` on the admin page.
Also serve `deploy/photozhab/` locally and inspect both a desktop viewport and a
390px-class mobile viewport for horizontal overflow and usable primary actions.
For the Claude Design B v2 adaptation, additionally inspect all three supplied
surfaces: the home hero plus a lower landing section, the anonymous generation
app and auth dialog, and the admin overview. The exported `.dc.html` files are
design references only: production assets must not contain `x-dc`, `x-import`,
`sc-if`, `sc-for`, `support.js`, template event handlers or mock live values.
Run `tests/test_photozhab_design_static.py` and confirm Space Grotesk, the
`#0b0d0c`/`#d3f36b` B v2 tokens, all landing sections, the four generation
modes, session-only (non-`localStorage`) history, all twelve admin tabs and
760px/820px responsive contracts. When changing versioned CSS/JS query strings,
reload the page and confirm the browser actually loaded the new URL before
judging the render.

The final B v2 archive additionally requires the animated generation demo,
Telegram/MAX continuation cards, the frog CTA and the mobile sticky CTA. All
media slots must resolve to tracked first-party image/video assets. The public
speed promise is `за 1 минуту`; stale nine/twelve-second image claims are a
release blocker. Keep the five-way hero assignment intact and ensure the demo
animation becomes static under `prefers-reduced-motion`.

For generated landing media, run the additional offline gate:

```bash
python -m pytest -q tests/test_photozhab_design_static.py tests/test_generate_landing_media.py
node --check deploy/photozhab/landing.js
python -m py_compile tools/generate_landing_media.py
ffprobe -v error -show_entries format=duration,size -show_entries stream=codec_name,width,height,pix_fmt deploy/photozhab/assets/showcase/forest-video.mp4
```

The tracked media contract verifies WebP/MP4 signatures, meaningful minimum
sizes, exact public references, a muted inline video, reduced-motion handling
and isolation from legacy `.price-row`/showcase selectors. A live content batch
is paid/stateful and must only be run after explicit operator approval with
`tools/generate_landing_media.py --approve-external-action`; the helper must use
the localhost internal endpoint, a dedicated non-customer identity, bounded
responses and a Google-media hostname allowlist. Do not commit its generated
manifest or provider URLs/identifiers. Inspect every image and a decoded video
frame before publishing, and strip audio plus add MP4 fast-start for autoplay.

For the five-way landing hero experiment, verify every tracked file under
`deploy/photozhab/assets/heroes/` is a 1536x960 WebP, remains reasonably small,
and has the intended dark copy-safe area. `hero-experiment.js` must choose only
`a` through `e`, persist only the non-identifying `pz_hero_variant` cookie, and
preload only the assigned asset. `landing.js` may submit only `exposure` and
`cta` to the same-origin `/web/api/experiment` endpoint. The endpoint must
strictly allowlist the experiment/variant/event tuple, reject bodies over 512
bytes and cross-origin requests, rate-limit by opaque session, and store no
user id or arbitrary client payload. Run:

```bash
node --check deploy/photozhab/hero-experiment.js
node --check deploy/photozhab/landing.js
python -m pytest -q tests/test_photozhab_design_static.py tests/test_web_app.py
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,pix_fmt deploy/photozhab/assets/heroes/*.webp
```

Browser QA must confirm that the 90-day cookie keeps one variant stable across
reloads, a different allowlisted cookie selects the corresponding asset, and
the network loads only that hero. Aggregate conversion is measured from
`landing_hero_exposure` and `landing_hero_cta` grouped by payload variant.

When changing per-account gost upstreams, take and hash-verify a protected
backup of the affected unit and credential files first. Test supplied upstreams
before mutation, keep credentials in root-only `EnvironmentFile` files (0600),
preserve the local port/account mapping, then verify each local proxy's egress
against the expected host. Sanitized output may contain only account labels,
host endpoints, status, match flags and latency; never print or commit proxy
usernames, passwords or full URLs. Obsolete proxy services must be stopped and
disabled rather than left restarting against known-dead upstreams.

When a public asset directory gains nested paths, verify `copy_static()` with
`tests/test_deployment_assets.py` and `bash -n deploy.sh`. Deployment must create
every source directory with mode 0755 and install every nested file with mode
0644 using null-delimited `find` output; a one-level `assets/*` glob is not safe
because `install -m 0644` fails when the glob expands to a directory.

After an immutable-SHA deploy, request the public home, each sitemap URL,
`/robots.txt` and `/sitemap.xml`; all public crawl targets must return HTTP 200.
Search-engine indexing, snippet selection and ranking are external outcomes and
must not be reported as testable deployment guarantees.

## First-party web app checks

The browser app is a paid/stateful surface. Run its focused contract first:

```bash
python -m pytest tests/test_web_app.py tests/test_web_app_static.py tests/test_generation_services.py tests/test_robokassa_billing.py tests/test_production_preflight.py tests/test_deployment_assets.py -q
python -m py_compile channels/web/app.py generation/backend_service.py billing/robokassa.py channels/telegram/web_server.py flow_bot.py tools/production_preflight.py
```

Required assertions include: anonymous GET/invalid requests do not allocate a
SQLite identity; mutation Origin is exact; cookies are signed and HttpOnly;
client price/user/model-key fields are ignored; upload and output media are
bounded and validated; only one generation per session runs; failures refund;
successful output charges once; MP4 URLs are session-bound and expire; payment
packs are allowlisted and signed with `Shp_channel=web`; production starter
credit is zero and Secure-cookie cannot be disabled. Generation, payment and
media must return 401/404 for an anonymous cookie. Authentication checks must
cover Telegram's cookie-bound one-time challenge + six-digit confirmation,
MAX Mini App HMAC/duplicate-key/auth-date/replay rejection, Yandex OAuth
state-cookie binding + PKCE S256, session rotation and logout invalidation.
The Claude-reference composer additionally keeps the existing native model,
aspect and count values as the backend source of truth while exposing expanded
button/range controls. Verify model/format/count synchronization, request-price
updates, image-upload transition into edit mode, the 820px desktop composer and
the horizontal-scroll mobile control rail without substituting screenshot mock
balance, account or history values.

The final B v2 app also requires a six-slot visual Telegram code field backed by
one real accessible numeric input, an explicit pack-selection then payment step,
and generation loading/error/result actions. `Мои работы` is intentionally a
current-tab gallery: it may render only media returned by successful
`/web/api/generate` responses, must not use `localStorage`, and must not claim
server or cross-device history. Download/open/reuse actions must operate on the
real response media and fail with a user-visible message when a source cannot be
fetched.

For browser QA, serve `deploy/photozhab/` locally and inspect desktop plus a
390x844 viewport. Confirm the non-dismissible account gate, Telegram code form,
MAX/Yandex provider states, signed-in account/logout surface, all four modes,
mobile mode selection, file preview, composer/send visibility, payment dialog,
no console syntax error and no horizontal overflow. A plain static server
cannot satisfy `/web/api/session`; the resulting handled connection toast is
expected in static-only QA.

For mobile Telegram login, the 820px/coarse-pointer branch must use same-tab
navigation to the server-issued `t.me` universal link and persist only a numeric
pending timestamp in `sessionStorage`; never persist the challenge URL/token or
six-digit code. Browser return/back must restore the code form for at most 11
minutes. Desktop may retain the named popup. When Yandex credentials are absent,
its control must remain fail-closed but tappable enough to explain that setup is
pending; it must not silently ignore the user or navigate to a non-working OAuth
route. OAuth callback `auth` query markers must be shown once and removed from
the address bar.

OAuth return initialization must never open the auth dialog before the first
authoritative `/web/api/session` response. An `auth=success` query marker is
display-only and cannot authenticate the browser; after a short bounded retry,
the dialog stays closed only when the server returns `authenticated=true`.
Anonymous initialization must still open the dialog, API failure must leave
generation controls disabled, and pageshow/visibility refresh must run only
after initial session loading completes.

After green CI, back up the protected VPS env and nginx config, set a random
32+ character `WEB_SESSION_SECRET`, enable the consumer-only web app, keep
`WEB_STARTER_CREDITS=0`, add the `/web/api/` reverse proxy to the
`photozhab.ru` TLS host, validate nginx, deploy an immutable SHA and check:

- public `/app.html`, `/app.css`, `/app.js` return 200;
- same-origin `/web/api/session` returns no-store JSON, anonymous auth state,
  zero balance and a Secure/HttpOnly/SameSite cookie without creating a credit
  identity or paid provider work;
- a cross-origin generation POST is rejected before identity/backend work;
- anonymous generation/payment is rejected; a Telegram code from the real bot
  creates a rotated authenticated session whose balance matches that Telegram
  user; never print the code/cookie/id during the smoke;
- a public pack produces an HTTPS Robokassa URL containing signed web channel
  metadata (do not complete a real charge during smoke unless requested);
- one owner-authorized generation deducts the server-displayed price only on a
  valid result and returns displayable image/MP4 media.

Never output the session secret, cookie, signed payment URL, prompt/upload,
provider response, account/project/media ids or internal negative user id.

## MAX transport smoke

The harness is offline by default and documents each external side effect:

```bash
python -m unittest discover -s tests -p "test_max_smoke.py"
python tools/max_smoke.py --mode plan --env-file .env
```

Production preflight also validates MAX TLS trust without contacting the
provider. For the default `platform-api2.max.ru` endpoint it requires the
pinned Russian Trusted Root CA fingerprint either in `MAX_CA_BUNDLE` or in the
system trust store; a merely existing or parseable unrelated PEM is not enough.
The current pinned DER SHA-256 is documented in
`tools/production_preflight.py`. After obtaining the certificate from
`https://www.gosuslugi.ru/crt`, validate with:

```bash
python tools/production_preflight.py --root . --env-file .env
python tools/max_smoke.py --mode subscription --env-file .env --approve-external-action
```

MAX media transport must reject non-HTTPS inbound URLs, enforce the 50 MiB
incoming-image limit, and send generated video through the documented
download -> `/uploads` -> token -> `/messages` sequence. Upload URLs are
accepted only on the provider hosts named in the current official MAX upload
contract.

After explicit operator approval, run `subscription`, then `message`, then
`media` as documented in `docs/MAX_SMOKE.md`. The last two modes send visible
content to the configured operator-owned MAX target; none of the modes print
configuration values or response bodies.

## MAX consumer and generation parity

Run the declarative Telegram/MAX menu contract, consumer flows, durable state,
shared facade and backend contracts together:

```bash
python -m pytest -q tests/test_channel_parity.py tests/test_max_mvp.py tests/test_max_channel.py tests/test_channels_base.py tests/test_max_state.py tests/test_max_runtime.py tests/test_max_generation_adapter.py tests/test_max_webhook_route.py tests/test_max_inbox.py tests/test_telegram_routers.py tests/test_generation_facade.py tests/test_generation_services.py
```

Required consumer assertions include the exact seven-row Telegram/MAX root
menu, legacy callback acceptance, edit-with-send-fallback navigation, arbitrary
photo routing, quick ideas/templates/guided construction, explicit priced
confirmation before every paid action, profile/gallery/history/support,
validated referral deep links, identity-routed notifications and real result
actions including an exact paid repeat. Gallery/history fixtures may contain
only results belonging to the current MAX identity.

Generation assertions include migration from the legacy action-only SQLite
schema, corrupt-state recovery, allowlisted image/video model and aspect
callbacks, image count capped at four, server-side prices, text-to-video,
Ingredients capped at four photos, Frames requiring exactly two photos,
insufficient-balance rejection, refund on provider/delivery failure and
callback acknowledgement failure not replaying a completed business action.
All channel/adapter/backend tests use temporary databases, fake downloads and
fake provider calls; they must not contact MAX, Google, Robokassa or 2Captcha.
The maintained feature boundary and intentional platform-only exclusions are
documented in `docs/CHANNEL_PARITY.md`.

After an immutable-SHA production deploy, an unpaid menu smoke may open each
flow and change every setting, then restart the service and confirm one pending
selection survives. Do not submit a prompt/photo during that smoke. A real
image, text-video, Ingredients or Frames check spends quota and credits and
requires the operator's explicit approval. The official MAX keyboard currently
allows up to seven callback buttons per row; keep every generated row within
that limit and keep `MAX_API_BASE_URL` on `platform-api2.max.ru` before the
provider's announced 2026-07-19 endpoint cutover.

When an approved live/stateful check fails, record a sanitized entry in
`docs/LIVE_TEST_FAILURES.md`. Include Telegram input/output, Flow account label,
Google HTTP status/body snippet, user-facing text, reproduction steps, severity,
and next fix owner. Never paste secret values, raw HAR bodies, bearer/cookie
material, proxy credentials, Telegram `file_id` values, or private user content.
When a follow-up repair path is ready, record the sanitized recommendation in
`docs/LIVE_TEST_FIXES.md` and reference the matching `LF-000` failure id.

## Validation Categories

### Offline/Safe

Use for documentation, packaging, and pure refactors.

- Inspect file list: `rg --files`
- Check git status if git exists: `git status --short`
- Python syntax check, assumption:
  `python -m py_compile flow_bot.py flow_core.py flow_copy.py metrics.py prompts_lib.py find_recaptcha_params.py live_test.py login.py test_recaptcha_params.py test_tokens.py telegram_bot_tester.py flow_quota_profiler.py`
- Full offline unit suite, assumption:
  `python -m unittest discover -s tests -p "test_*.py"`
- Search for accidental secret patterns before commit without printing matched
  lines, assumption:
  `rg -l "Bearer |TELEGRAM_TOKEN=|TWOCAPTCHA|session-token|ya29\\.|socks5h?://|http://[^\\s]+:[^\\s]+@"`

Notes:

- `python -m py_compile` is an assumption because Python version is not pinned.
- In a clean worktree without local `.env`, the full offline unit suite may need
  an explicitly fake Telegram token, for example:
  `$env:TELEGRAM_TOKEN = '123456789:REDACTED'; python -m unittest discover -s tests -p "test_*.py"`.
- Secret scans must not print secret values in command output or final reports.
  Use file-only output such as `rg -l` / `--files-with-matches`, or a dedicated
  redaction tool. Do not use `rg -n`, `-o`, `-C`, `-A`, or `-B` for secret
  scans in this repository.

### Local Browser/Stateful

Requires explicit approval.

- `python login.py`
- Any script that opens persistent Chrome profile via Playwright.
- Admin account onboarding from `/admin.html`:
  `POST /api/admin/accounts/onboard/start` with `confirm_login=true` opens
  Chrome, creates a persistent profile, and logs in to Google until Flow opens or
  Google asks for a one-time 2FA code; `POST /api/admin/accounts/onboard/2fa`
  submits that code into the open browser session; `POST
  /api/admin/accounts/onboard/complete` with `confirm_add=true` updates `.env`
  `FLOW_ACCOUNTS` for new accounts. Relogin mode reuses an existing profile and
  stops/restarts that account's runtime keeper. Account deletion
  `POST /api/admin/accounts/<id>/delete` with `confirm_delete=true` removes the
  account from `.env`/runtime but leaves the profile directory on disk.

Risks:

- Can mutate or delete `google_profile/`.
- Can open a visible browser.
- Can affect logged-in Google account/session state.
- Can create or mutate `google_profile_*` directories and `.env`.

### Network/External API

Requires explicit approval.

- `python live_test.py`
- `python test_tokens.py`
- `python test_recaptcha_params.py`
- `python flow_bot.py`
- `python telegram_bot_tester.py --mode telegram-login-request --approve-external-action`
- `python telegram_bot_tester.py --mode telegram-login-complete --approve-external-action`
- `python telegram_bot_tester.py --mode telegram-smoke --approve-external-action`
- `python telegram_bot_tester.py --mode telegram-generation --prompt "simple safe landscape test" --approve-external-action`
- `python telegram_bot_tester.py --mode telegram-ramp --prompt "simple safe landscape test" --max-steps 3 --delay-sec 90 --approve-external-action`
- Admin video transport A/B diagnostic, approval required and spends quota/captcha:
  `curl -sS -X POST http://127.0.0.1:<admin-port>/api/admin/video-ab -H "Content-Type: application/json" -d '{"confirm_spend":true,"account":"<account_id>","model":"omni-flash-4s","aspect":"landscape","prompt":"simple cinematic shot of a calm sunrise over a lake","pause_sec":4}'`
- Admin account image smoke, approval required and may spend captcha/quota:
  `curl -sS -X POST http://127.0.0.1:<admin-port>/api/admin/accounts/<account_id>/test-image -H "Content-Type: application/json" -d '{"confirm_spend":true}'`
- Admin account video smoke, approval required and sends one real video submit:
  `curl -sS -X POST http://127.0.0.1:<admin-port>/api/admin/accounts/<account_id>/test-video -H "Content-Type: application/json" -d '{"confirm_spend":true}'`

Risks:

- Calls Telegram, Google, 2Captcha, proxy services, or IP APIs.
- May spend API/captcha balance.
- May generate media.
- May trigger provider rate limits or account security checks.
- May create or update Telegram `.session` files.

## Commands Found In Repository

Found in comments/docstrings:

- `pip install aiogram playwright aiohttp aiohttp-socks python-dotenv`
- `playwright install chromium`
- `python test_tokens.py`

Found as executable entry points:

- `python flow_bot.py`
- `python login.py`
- `python find_recaptcha_params.py`
- `python live_test.py`
- `python test_recaptcha_params.py`
- `python test_tokens.py`
- `python telegram_bot_tester.py`

Assumptions:

- `pip install -r requirements.txt`
- `python -m py_compile ...`
- `python -m py_compile telegram_bot_tester.py tg_e2e/config.py tg_e2e/runner.py tg_e2e/telethon_client.py`

(`node bot.js` / `node --check bot.js` / npm install удалены из списка:
`bot.js` — legacy, удалён из проекта 2026-06-10.)

## Validation By Change Type

Documentation-only:

- Confirm changed markdown files exist.
- Check that docs do not include secret values.
- Optional safe file-only scan:
  `rg -l "ya29\\.|session-token|TELEGRAM_TOKEN=|TWOCAPTCHA_API_KEY=|BEARER_TOKEN=" *.md`

Secret hygiene:

- Check `.gitignore`.
- Check templates contain placeholders only.
- Run safe file-only secret scan against files intended for commit.
- Do not delete or rotate real secrets unless explicitly requested.

Server migration/cutover:

- Requires explicit operator approval before starting live bot services.
- Pull a local backup before changing the destination host; do not print env,
  cookie, bearer, HAR, proxy credential, or browser-profile contents.
- Verify copied archives with `sha256sum -c` before extraction.
- Verify SQLite state with `sqlite3 metrics.db 'PRAGMA integrity_check;'`.
- Verify nginx with `nginx -t` and public HTTPS/admin responses.
- If nginx uses `auth_basic_user_file`, verify the referenced htpasswd file
  exists on the destination and is readable by nginx; do not print its contents.
- Verify local proxy/listener exposure with `ss -ltnp`; VNC ports must not be
  exposed publicly unless the operator explicitly asks for interactive VNC.
- Verify the intended deploy-managed service only:
  `systemctl is-active geminifree-bot`. Seller is outside the current deploy
  helper and should be checked separately when seller work resumes.
- Verify startup journal reaches Telegram polling and has no fresh
  `Traceback`, `ERROR`, `CRITICAL`, `exception`, or restart loop.
- Do not run media generation, paid/captcha diagnostics, Google re-login, or
  `login.py` unless explicitly approved.

NL-only deployment:

- The project no longer ships FI/NL DNS failover tooling. `photozhab.ru` and
  `pay.photozhab.ru` are expected to run on NL (`192.0.2.10`) only.
- Verify the intended NL deploy-managed service:
  `systemctl is-active geminifree-bot`. Seller is outside the current deploy
  helper and should be checked separately when seller work resumes.
- Verify no old failover timer is installed or active:
  `systemctl is-enabled geminifree-failover.timer` should be absent/disabled,
  and `systemctl is-active geminifree-failover.timer` should be inactive/failed.
- Verify DNS points at NL before relying on public traffic:
  `Resolve-DnsName photozhab.ru -Type A` and
  `Resolve-DnsName pay.photozhab.ru -Type A`.

Dependency manifests:

- `requirements.txt` is the current Python manifest.
- Installing dependencies or Playwright browsers is not routine validation; run
  it only when the operator approves environment setup.

Python code change:

- `python -m py_compile <changed .py files>` - assumption.
- Add targeted offline tests before external validation where possible.
- Avoid running Telegram/Google scripts unless explicitly approved.

MAX webhook/runtime change:

- Syntax check, assumption: `python -m py_compile channels/max/*.py
  channels/telegram/max_bootstrap.py`.
- Focused offline suite, assumption: `python -m unittest discover -s tests -p
  "test_max_*.py"`.
- Run the full offline suite before merge because MAX shares generation,
  billing, and aiohttp composition surfaces with Telegram.
- Inbox tests must use temporary SQLite files and confirm duplicate suppression,
  cross-worker lease exclusion, FIFO for one state-owning user, parallel claims
  for different users, retry/dead-letter state, retention, and redaction of
  exception text.
- Wizard-state tests must use temporary SQLite files and confirm persistence
  across store/bot recreation, TTL cleanup, explicit clear, successful-delivery
  cleanup, and refund plus state retention when media delivery raises.
- MAX Robokassa tests must remain provider-free and confirm invoice links carry
  the negative internal identity inside the signed `Shp_user`, callback parsing
  accepts only non-zero signed 64-bit integers after signature verification,
  duplicate callbacks do not re-credit, credits land in the metrics SQLite
  namespace, and MAX identities never fall through to Telegram notification or
  a Telegram success-page link.
- Atomic external-payment tests must prove transaction-row and credit-row
  idempotency in one temporary metrics SQLite database. Inject a credit-write
  failure and verify the transaction row is rolled back, the callback reports a
  retryable error, and the identical payment succeeds after the fault is removed.
- Transport tests must prove that `POST /messages` is not retried on ambiguous
  failures, rate limiting stays below 30 rps, `Retry-After` is bounded, unknown
  provider bodies/codes are redacted, and owned sessions close on shutdown.
- `/max/health` is a local readiness check only: test subscription state,
  worker-task state, inbox counts, and dead-letter degradation with fakes. It
  must not call the MAX API.
- Inbound fixtures must use the official nested shape: message text/id/media in
  `message.body`, chat in `message.recipient.chat_id`, sender in
  `message.sender`, callback identity in top-level `chat_id`/`message_id` plus
  `callback.callback_id`, and start events with `update_type=bot_started`.
- Do not register a live subscription, send a MAX message, or call the MAX API
  without explicit approval; those actions mutate external state.

Admin UI/API change:

- Syntax check, assumption:
  `python -m py_compile admin_api.py metrics.py flow_core.py flow_copy.py flow_bot.py account_onboarding.py`
- Static admin JavaScript parse check, assumption:
  `node -e "const fs=require('fs'); const html=fs.readFileSync('deploy/photozhab/admin.html','utf8'); const scripts=[...html.matchAll(/<script>([\\s\\S]*?)<\\/script>/g)].map(m=>m[1]); for (const s of scripts) new Function(s); console.log('admin.html scripts parse OK');"`
- Seller admin panel changes should include targeted metrics/admin handler tests:
  `python -m unittest discover -s tests -p "test_metrics.py"` and
  `python -m unittest discover -s tests -p "test_admin_api.py"`.
- Account onboarding changes should include:
  `python -m unittest discover -s tests -p "test_account_onboarding.py"`,
  `python -m unittest discover -s tests -p "test_admin_api.py"`, and
  `python -m unittest discover -s tests -p "test_flow_accounts.py"`.
- Offline tests, assumption:
  `python -m unittest discover -s tests -p "test_*.py"`
- Startup/video routing health changes should also include targeted routing and
  static wiring tests, assumption:
  `python -m unittest discover -s tests -p "test_flow_accounts.py"` and
  `python -m unittest discover -s tests -p "test_flow_menu.py"`.
- In a dirty worktree, run whitespace checks scoped to intended files rather
  than treating unrelated local edits as part of the patch:
  `git diff --check -- <intended files>`.
- Optional local static/offline visual check: serve `deploy/photozhab/` on a
  temporary localhost port and open `/admin.html`; with no admin API present,
  the page must show explicit API unavailable state and no fake production
  data.
- Do not run Flow, Telegram, captcha, Robokassa, browser-profile, or payment
  checks unless the operator explicitly approves that external/stateful action.

Flow quota profiler RL-001A:

- Syntax check, assumption:
  `python -m py_compile flow_quota_profiler.py flow_profiler/config.py flow_profiler/planner.py flow_profiler/runner.py flow_profiler/writers.py flow_profiler/report.py flow_profiler/safety.py flow_profiler/lock.py`
- Offline tests, assumption:
  `python -m unittest discover -s tests -p "test_flow_profiler_*.py"`
- Safe file-only secret scan, assumption:
  `rg -l "ya29\\.|session-token|TELEGRAM_TOKEN=|TWOCAPTCHA|BEARER_TOKEN=|socks5h?://|http://[^\\s]+:[^\\s]+@" flow_profiler flow_quota_profiler.py tests docs`
- Manual offline dry-run, assumption:
  `python flow_quota_profiler.py --mode dry-run --prompt-id test-001 --prompt "simple safe prompt"`
- Do not run browser, Telegram, Google Flow, captcha, proxy, or profile checks
  for RL-001A.

Flow quota profiler RL-001B:

- Syntax check, assumption:
  `python -m py_compile flow_quota_profiler.py flow_profiler/config.py flow_profiler/planner.py flow_profiler/runner.py flow_profiler/writers.py flow_profiler/report.py flow_profiler/safety.py flow_profiler/lock.py flow_profiler/browser_single.py`
- Offline tests, assumption:
  `python -m unittest discover -s tests -p "test_flow_profiler_*.py"`
- Safe file-only secret scan, assumption:
  `rg -l "ya29\\.|session-token|TELEGRAM_TOKEN=|TWOCAPTCHA|BEARER_TOKEN=|socks5h?://|http://[^\\s]+:[^\\s]+@" flow_profiler flow_quota_profiler.py tests docs`
- Manual browser smoke, approval required:
  `python flow_quota_profiler.py --mode single --prompt-id smoke-001 --prompt "simple safe landscape test" --user-data-dir ./google_profile --approve-external-action`
- Run the manual browser smoke only after offline validation passes and the
  operator accepts the profile/account/quota risk.

Flow quota profiler RL-001C:

- Syntax check, assumption:
  `python -m py_compile flow_quota_profiler.py flow_profiler/config.py flow_profiler/planner.py flow_profiler/runner.py flow_profiler/writers.py flow_profiler/report.py flow_profiler/safety.py flow_profiler/lock.py flow_profiler/browser_single.py flow_profiler/browser_check.py`
- Offline tests, assumption:
  `python -m unittest discover -s tests -p "test_flow_profiler_*.py"`
- Safe file-only secret scan, assumption:
  `rg -l "ya29\\.|session-token|TELEGRAM_TOKEN=|TWOCAPTCHA|BEARER_TOKEN=|socks5h?://|http://[^\\s]+:[^\\s]+@" flow_profiler flow_quota_profiler.py tests docs`
- Manual temp-profile diagnostic, approval required:
  `python flow_quota_profiler.py --mode browser-check --approve-external-action`
- Manual existing-profile diagnostic, approval required and only after the
  temp-profile check passes:
  `python flow_quota_profiler.py --mode browser-check --user-data-dir ./google_profile --approve-external-action`

Manual RL-001C interpretation:

- Temp profile fails: likely missing Playwright browser install or OS/browser
  launch issue.
- Temp profile passes and existing profile fails: likely profile lock,
  incompatible/corrupted profile, or existing Chrome process conflict.
- Both pass: a prior `single` failure is likely in headed persistent launch
  options or later Google Flow UI flow.

Telegram E2E tester:

- Syntax check, assumption:
  `python -m py_compile telegram_bot_tester.py tg_e2e/config.py tg_e2e/env_file.py tg_e2e/runner.py tg_e2e/telethon_client.py`
- Offline tests, assumption:
  `python -m unittest discover -s tests -p "test_tg_e2e.py"`
- Dry-run CLI, assumption:
  `python telegram_bot_tester.py --mode dry-run`
- Safe file-only secret scan, assumption:
  `rg -l "ya29\\.|session-token|TELEGRAM_TOKEN=|TWOCAPTCHA|BEARER_TOKEN=|TG_API_HASH=|TG_PHONE=|TG_E2E_PROXY_URL=|tg://proxy\\?server=.*secret=|socks5h?://|http://[^\\s]+:[^\\s]+@" telegram_bot_tester.py tg_e2e tests docs`
- Manual Telegram smoke, approval required:
  `python telegram_bot_tester.py --mode telegram-login --approve-external-action`
- Non-interactive Telegram login request, approval required:
  `python telegram_bot_tester.py --mode telegram-login-request --approve-external-action`
- Non-interactive Telegram login completion, approval required:
  `python telegram_bot_tester.py --mode telegram-login-complete --approve-external-action`
- Manual Telegram smoke after login, approval required:
  `python telegram_bot_tester.py --mode telegram-smoke --approve-external-action`
- Manual single generation, approval required and only after smoke passes:
  `python telegram_bot_tester.py --mode telegram-generation --prompt "simple safe landscape test" --approve-external-action`
- Manual ramp, approval required and only after single generation passes:
  `python telegram_bot_tester.py --mode telegram-ramp --prompt "simple safe landscape test" --max-steps 3 --delay-sec 90 --approve-external-action`

Approved ad-hoc live probe helper:

- Text command/message:
  `python tools/live_telegram_probe.py --env-file .env --bot-username <bot> --text "/admin_help"`
- Photo upload:
  `python tools/live_telegram_probe.py --env-file .env --bot-username <bot> --photo kotenok.jpg --caption "short safe test"`
- Recent safe summaries:
  `python tools/live_telegram_probe.py --env-file .env --bot-username <bot> --recent 10`
- Inline callback by data:
  `python tools/live_telegram_probe.py --env-file .env --bot-username <bot> --click-data m:vid`

Approved live video-wizard driver:

- `tools/live_video_probe.py` drives the real prompt-first video wizard end to
  end and waits for the delivered video. It requires `--approve-external-action`
  because it makes the bot call Google Flow and spends real credits/quota.
- Text-to-video (Omni), one job:
  `python tools/live_video_probe.py --approve-external-action --model omni-flash-4s --prompt "a calm cinematic sunrise over a quiet lake"`
- Photo-to-video (Veo r2v), one job:
  `python tools/live_video_probe.py --approve-external-action --model veo-lite --photo kotenok.jpg --prompt "the kitten slowly turns its head"`
- Allowed models: `omni-flash-4s|6s|8s|10s` (text) and `veo-lite|veo-fast`
  (need `--photo`). The probe refuses `veo-quality` and never cycles Veo quality
  through `quality`.
- It prints a sanitized JSON summary only (status, `has_video`, timings, bot
  text previews); it never prints Telegram `file_id` values or secrets.
- The bot routes a job to the acting user's assigned account. To exercise a
  different account, an admin can `/acc_vid_off <id>` the sticky account first
  and `/acc_vid_on <id>` afterwards; do not leave toggles changed.

Run one Telethon probe at a time when reusing the same `.session` file.
Concurrent probes can fail in the harness with
`sqlite3.OperationalError: database is locked`; this is not evidence of a bot
failure unless reproduced with sequential clients. The probe prints message
summaries only; do not paste Telegram `file_id` values, raw logs, secrets, or
private prompts into failure notes.

After inline callback clicks, inspect `clicked_message_after` to read the
current edited wizard message. Telegram often edits the existing message instead
of sending a new one, so the probe's `messages` array only contains fresh bot
messages created after the click. Use `--recent` as a fallback when manually
auditing older chat state.

Telegram E2E external modes contact Telegram and may cause the bot to contact
Google Flow, mutate bot cooldown state, spend quota, trigger captcha/rate-limit
signals, or create/update `.sessions/` files. Never run them as routine
validation or CI.

Telegram lead pain scanner:

- Syntax check, assumption:
  `python -m py_compile tools\lead_pain_scan.py tools\lead_chat_discovery.py tools\render_dashboard_png.py`
- CLI help smoke, assumption:
  `python tools\lead_pain_scan.py --help`
- Manual read-only chat discovery, approval required (no joins, no identity export):
  `python tools\lead_chat_discovery.py --approve-external-action --min-participants 300`
- Manual read-only strict scan (legacy narrow shortlist), approval required:
  `python tools\lead_pain_scan.py --approve-external-action --days 30 --limit-per-term 80 --max-samples-per-chat 10`
- Manual read-only done-for-you scan over discovered megagroups, approval required:
  `python tools\lead_pain_scan.py --approve-external-action --chats-file lead_scan_runs\discovered_chats.json --days 120 --limit-per-term 50 --max-samples-per-chat 6`
- Optional local visual check for generated dashboard:
  open `lead_scan_runs/pain_dashboard.html`, or render the HTML/SVG to PNG with
  `python tools\render_dashboard_png.py` (Playwright Chromium, offline local file).

Risks:

- Calls Telegram through the existing E2E user session and may update the local
  `.session` database.
- Reads public chat messages only; it must not send messages, join chats,
  export sender ids/usernames, scrape member lists, or create contact lists.
- Writes generated CSV/JSON/Markdown/HTML/SVG/PNG reports under
  `lead_scan_runs/`; keep that directory gitignored and do not commit raw scan
  output unless deliberately sanitized.

Per-user projects, image editing, menu UX, and credits (flow_bot):

- Syntax check, assumption:
  `python -m py_compile flow_core.py flow_bot.py flow_copy.py`
- Offline tests, assumption:
  `python -m unittest discover -s tests -p "test_flow_edit.py"`,
  `python -m unittest discover -s tests -p "test_flow_menu.py"`
  (menu/credits/Stars + an aiogram import-smoke that registers handlers and
  builds keyboards; the smoke writes credits to a temp file, never the real
  `user_credits.json`), and the recovery guard
  `python -m unittest discover -s tests -p "test_flow_bot_recovery.py"`
- Full offline suite, assumption:
  `python -m unittest discover -s tests -p "test_*.py"`
- Safe file-only secret scan, assumption:
  `rg -l "ya29\\.|session-token|TELEGRAM_TOKEN=|TWOCAPTCHA|BEARER_TOKEN=|socks5h?://|http://[^\\s]+:[^\\s]+@" flow_core.py flow_bot.py flow_copy.py tests/test_flow_edit.py tests/test_flow_menu.py`
  - `flow_bot.py` is expected to match on the `TWOCAPTCHA` env-name and the
    `socks5h://` proxy-protocol identifier only; no secret values are printed
    by `-l`. Other files are clean.
  - Telegram Stars payments (`send_invoice`/`pre_checkout`/`successful_payment`)
    and the visual menu require a live Telegram run to validate end-to-end; do
    not run as routine validation. `user_credits.json` is runtime state, gitignored.
- Manual end-to-end, approval required (operator must restart the running
  `flow_bot.py` first so the new code is live):
  - `python telegram_bot_tester.py --mode telegram-generation --prompt "simple safe landscape test" --approve-external-action`
    then, in the real Telegram chat, tap "✏️ Редактировать" under a returned
    image and send a short edit instruction.
- The `imageInputs` edit payload shape is learned at runtime, not guessed (a
  guess returned `HTTP 400 Unknown name "mediaStoreUri" at
  'requests[0].image_inputs[0]'`). To calibrate: with the bot running, open
  labs.google Flow in the bot's Chrome window and edit one freshly generated
  image; `SessionKeeper._maybe_capture_edit` writes `edit_capture.json` and logs
  a values-free schema (`🔬 EDIT imageInputs schema: …`). After that, the
  Telegram "✏️ Редактировать" button works. The interceptor only reads/logs
  schema (no secret values) and is fully wrapped in try/except so it can never
  break generation.
- `edit_capture.json` is runtime state (gitignored). It contains the edit
  template with the image reference replaced by `__FLOW_IMAGE_REF__`; recaptcha
  and bearer live in `clientContext`, not in `imageInputs`, so they are not
  stored there.

Image features beyond video (all five):

- Implemented on the confirmed `batchGenerateImages` contract (no new endpoint):
  variations (`vary:`), regenerate (`regen:`), square aspect (`/square`),
  flexible count (`/imgn N`), and ingredients/`/mix` (multiple `imageInputs`).
  These need only the existing edit calibration to be in effect.
- ⬇️ Оригинал (full-quality download): the Flow site's "upscale" is a
  client-side file download, not a server endpoint, so the bot fetches the
  image's public `fifeUrl` and sends it as a Telegram document (uncompressed,
  full resolution) via `_send_original_file`. No endpoint is called; nothing to
  calibrate. Works for any delivered image that has a fetchable URL.
- Offline checks (assumption): `python -m py_compile flow_core.py flow_bot.py`;
  `python -m unittest discover -s tests -p "test_flow_edit.py"`; full suite
  `python -m unittest discover -s tests -p "test_*.py"`.
- `SQUARE` aspect enum is added by naming pattern; if Google rejects it the
  request returns HTTP 400 and only `/square` is affected. 4:3 / 3:4 are not
  wired because their enums are unknown.
- Per-user project creation drives the live browser once per new user. It only
  runs inside the bot process (which owns `google_profile/`); do not launch a
  second browser against that profile from a separate tool while the bot runs.

Node code change:

- Неактуально: `bot.js` (единственный Node-код) — legacy, удалён из проекта
  (2026-06-10).

Google Flow API/client change:

- Offline tests for payload construction and response parsing.
- Manual/network test only with approval.
- Record whether bearer/cookies/captcha/proxy were used, without values.

Video Ingredients/Frames (Flow API):

- Offline checks, assumption:
  `python -m py_compile flow_core.py flow_bot.py flow_copy.py`
  and `python -m unittest discover -s tests -p "test_flow_video.py"`.
- Product/UX offline checks for frame prompt flow, video result actions, and
  credit wiring:
  `python -m py_compile flow_core.py flow_copy.py flow_bot.py tests\test_flow_edit.py tests\test_flow_menu.py tests\test_flow_video.py`,
  `python -m unittest discover -s tests -p "test_flow_edit.py"`,
  `python -m unittest discover -s tests -p "test_flow_menu.py"`,
  `python -m unittest discover -s tests -p "test_flow_video.py"`,
  `python -m unittest discover -s tests -p "test_*.py"`.
- Manual Frames capture helper:
  `python tools/capture_video.py --frames`
  opens the persistent Flow browser profile, records sanitized API traffic to
  `tools/video_frames_capture.json`, and aborts the first video-like POST by
  default. Do not add `--no-abort` unless credit spend is explicitly accepted.
- Manual native video capture helpers:
  `python tools/capture_video.py --edit` writes `tools/video_edit_capture.json`;
  `python tools/capture_video.py --extend` writes
  `tools/video_extend_capture.json`. Both are abort-by-default endpoint discovery
  modes for Flow's own video Edit/Extend UI actions.
- `build_video_reference_images` and `build_video_frame_images` must use the
  uploaded Flow asset `mediaId` values, stripping any `fe_id_` prefix. Do not use
  Telegram `file_id` values.
- Live 2026-06-08 evidence in `HANDOFF.md`: text-to-video, Frames, and
  Ingredients generated successfully through the bot. Keep using the captured
  endpoint/request-shape helpers instead of guessing new video endpoints.
- Live 2026-06-14 evidence in `HANDOFF.md`: video result download, native Edit,
  Ingredients from `kotenok.jpg`, Frames from two `kotenok.jpg` uploads, Extend,
  and "download only new segment" all completed through Telegram and Google
  Flow. The r2v/frames request log should show `effective_model_key`, not the
  source UI model id.
- Live 2026-06-15 evidence in `HANDOFF.md`: after the LF-002/LF-008 follow-up
  deploy, Ingredients from `kotenok.jpg` and Frames from two `kotenok.jpg`
  uploads both completed through the Telegram bot on the VPS. Provider logs
  showed Reference and Frames HTTP 200, polling reached
  `MEDIA_GENERATION_STATUS_SUCCESSFUL`, and Telegram delivery had `has_video`.
- Native Video Edit uses the captured
  `video:batchAsyncGenerateVideoEditVideo` endpoint. It requires a stored source
  `mediaId` and `workflowId`; stale or incomplete video refs must fail closed
  without falling back to text-to-video.
- Native Video Extend uses the captured
  `video:batchAsyncGenerateVideoExtendVideo` endpoint after preparing a Flow
  scene from the source `workflowId`. The bot must not charge for Extend until a
  usable `sceneId` exists; stale or incomplete video refs remain no-charge
  unavailable.
- Extend delivery uses the provider's full stitched video path: scene workflows
  -> `v1:runVideoFxConcatenation` -> `v1:runVideoFxCheckConcatenationStatus`.
  If stitching fails, delivery falls back to the generated continuation segment
  so the paid result is not lost. Validate concat payload/status helpers offline;
  live Telegram validation is still approval-required.
- `VideoRef` is frozen. Extend must pass any prepared `sceneId` as
  `source_scene_id` into generation and must not mutate the stored registry ref.
- Offline native Edit/Extend checks:
  `python -m py_compile flow_core.py flow_bot.py tests\test_flow_video.py tests\test_flow_menu.py`,
  `python -m unittest discover -s tests -p "test_flow_video.py"`,
  `python -m unittest discover -s tests -p "test_flow_menu.py"`,
  `python -m unittest discover -s tests -p "test_flow_edit.py"`.
- Recent UX/state-only changes do not require new provider capture: Frames
  caption-on-Next behavior, image edit 429 recovery, video download, and retry
  buttons. Optional captures are still useful for unverified Veo tier/orientation
  model-key combinations if a live request fails.
- Video settings plain-text prompt UX is state-only. Validate offline that the
  plain-text branch runs before image fallback and is gated to
  `vstep == "vsettings"`, text mode, valid `vmodel`, `vfmt`, and `vcount`:
  `python -m py_compile flow_bot.py flow_copy.py tests\test_flow_menu.py`,
  `python -m unittest discover -s tests -p "test_flow_menu.py"`, and
  `python -m unittest discover -s tests -p "test_flow_video.py"`.
- Live validation requires approval because Telegram photo upload opens the
  persistent browser profile, contacts Telegram/Google Flow, may solve captcha,
  and may spend Google Flow credits. The bot must refund user credits on
  generation failure.

Telegram behavior change:

- Unit-test command parsing if extracted.
- Manual bot test only with approval and test chat/account.

Admin/server command change:

- Reviewer approval required.
- Verify authorization logic offline.
- Do not execute real shell/admin actions as validation.
- For the repo-root `admin_help` wrapper, safe validation is:
  `./admin_help` on the VPS. It must print command names and service-log hints
  only; it must not read or print runtime state or secrets.

Live video 401/403 routing checks:

- Requires explicit approval because it contacts Telegram, Google Flow, captcha
  providers, and may spend Flow credits.
- If a text-to-video request fails with repeated provider 403 on one account,
  inspect `/admin_accounts` from the admin chat and consider quarantining only
  the failing video account with `/acc_vid_off <id>`.
- Verified 2026-06-14: disabling video for `main` and `sub1` routed the next
  Omni 4s text-to-video request to `sub2`, which returned HTTP 200, completed
  provider polling, downloaded the video, and delivered it in Telegram.
- The code should refresh bearer and retry once on a video POST 401. Validate
  the source-level guard with `test_video_401_retries_after_refresh` and keep
  the retry bounded.

Live Telegram probe:

- `tools/live_telegram_probe.py` callback checks should collect messages after
  the current latest chat message id before clicking, not after the clicked
  historical message id. This avoids mixing stale messages into callback
  summaries when testing old result buttons.
- Long-running image callbacks can post an interim status and then the final
  media result tens of seconds later. Use `--idle-sec 35` for live generative
  image callbacks such as variations, edits, and regen; keep the default for
  quick menu/admin checks.
- Safe validation for the probe fix: `python -m py_compile
  tools\live_telegram_probe.py`, then click a no-charge callback such as a
  menu/help button and confirm `clicked_message_after` contains the edited
  message while only fresh bot replies appear in `messages`.
- Verified 2026-06-14 on the VPS: `/menu` then `m:help` by explicit
  `--click-message-id` returned the edited help message in
  `clicked_message_after` and left `messages` empty.
- When the chat has many old inline menus, pass `--click-message-id` for menu
  callbacks. A plain `--click-data` may click an older message with the same
  callback data, which is useful for result-button regression tests but noisy
  for current-menu UX checks.

Live admin/account controls:

- Approved 2026-06-14 checks covered both the shell helper `./admin_help` and
  Telegram owner/admin commands. The shell helper prints command names and
  service-log hints only; account toggles must be run in Telegram.
- `/admin_help`, `/admin_accounts`, and `/admin_errors` returned sanitized
  owner/admin responses in Telegram.
- `/status` is an admin-only diagnostic command. It may show backend/session
  health markers, project id presence/value, cookie count, and captcha balances
  to admins, so it must keep the `ADMIN_IDS` gate and live validation notes must
  not paste raw diagnostic values.
- `telegram_bot_tester.py --mode telegram-smoke` is the public smoke path and
  should use `/start` plus `/menu`, not `/status`.
- Verified 2026-06-14 on the VPS: updated `telegram-smoke` produced
  `telegram-start` and `telegram-menu`, both `success`, with no stop signals.
- `/acc_off sub3` disabled an account with no assigned users, `/acc_on sub3`
  restored it, `/acc_vid_off sub3` made it image-only, and `/acc_vid_on sub3`
  restored video capability. A final `/admin_accounts` check confirmed `sub3`
  was active with video+image again.
- Do not leave account toggles changed after validation unless the goal is
  explicit quarantine and the decision is recorded in `docs/LIVE_TEST_FAILURES.md`
  or `HANDOFF.md`.

Live no-payment UI checks:

- Approved 2026-06-14 checks covered menu/help/ideas/invite/balance/top-up
  navigation without completing payment or generation.
- Help, ideas/templates, guided prompt selection, referral invite, balance, and
  top-up provider/package menus rendered in Telegram. The payment checks stopped
  at package lists and did not create or pay invoices.
- Negative image command checks `/img`, `/one`, `/portrait`, `/square`, and
  `/imgn 2` without prompts returned the shared "describe a little more" copy.
  Recent service logs showed only handled Telegram updates and no Flow HTTP
  activity for those validation failures.
- Video wizard checks covered Omni, Veo, Ingredients, and Frames entry states;
  model/format/count changes updated labels and prices without starting
  generation.
- Negative video prompt check covered `/menu` -> `m:vid` -> Omni 4s -> `v:go`
  -> short text `x`. The bot returned the shared video prompt-too-short copy,
  and recent service logs showed only handled Telegram updates with no Flow
  video HTTP activity.
- Video Ingredients/Frames photo-wait states must not fall through to the image
  prompt wizard on plain text. Verified 2026-06-14 after deploy: text while
  Ingredients waited for a photo repeated the Ingredients photo request; text
  while Frames waited for the start photo repeated the start-frame request.
  Recent service logs showed handled Telegram updates only for these checks.
- Image wizard checks reached the image settings screen with count, aspect,
  model, balance, and price controls. The next untested step is prompt entry or
  `w:go`, which starts a paid/provider generation path.

Live image result actions:

- Approved 2026-06-14 checks covered Telegram photo+caption edit using
  `kotenok.jpg`, original download, real upscale, variations, result edit, and
  result regen. All successful generative callbacks returned Flow HTTP 200.
- Result-button regen must match its displayed one-image price: `_regen_and_send`
  should call `_generate_and_send(..., num_images=1, action="regen")`. Validate
  with `python -m unittest discover -s tests -p "test_flow_edit.py"` plus a live
  `/one` -> `Заново · 10 кр` check when external validation is approved.

Live my-photo entry state:

- The "edit my photo" entry sets `await == "photo"`. Plain text in that state
  must not become an image prompt or open the image wizard; it should repeat the
  photo request message and keep waiting for an upload.
- Safe offline validation: `python -m py_compile flow_bot.py
  tests\test_flow_menu.py` and `python -m unittest discover -s tests -p
  "test_flow_menu.py"`.
- Approved live validation: open `m:myphoto`, send plain text instead of a
  photo, confirm the bot asks for a photo again, and inspect recent service logs
  to confirm there is no Flow HTTP request for that text.

Live my-photo edit 403/failover classification:

- Image edit/i2i calls run `generate_images(..., allow_browser_fallback=False)`.
  If every recaptcha action returns HTTP 403, the result must be treated as the
  existing temporary rate-limit/account failure, not generic `gen_failed`.
- Safe offline validation: `python -m py_compile flow_bot.py
  tests\test_flow_edit.py` and `python -m unittest discover -s tests -p
  "test_flow_edit.py"`.
- Approved live validation: open `m:myphoto`, upload `kotenok.jpg`, send a safe
  edit prompt, and inspect recent service logs for either successful media or
  rate-limit failover. If both the first account and failover account hit
  `PUBLIC_ERROR_UNUSUAL_ACTIVITY`, balance should remain unchanged and the bot
  should show temporary edit-limit copy rather than generic generation failure.
- Repeated provider unusual-activity 403s are an account-health finding, not
  proof that the classification fix failed.
- After `LFX-010`, no-browser-fallback image 403s that include the provider
  unusual-activity signal should return a symbolic `account_risk:
  unusual_activity` marker and immediately cool down that account through
  `AccountPool.mark_cooldown()`. Safe offline validation: `python -m py_compile
  flow_core.py flow_bot.py tests\test_flow_accounts.py tests\test_flow_edit.py`,
  `python -m unittest discover -s tests -p "test_flow_accounts.py"`, and
  `python -m unittest discover -s tests -p "test_flow_edit.py"`.
- Approved 2026-06-15 LF-008 provider-backed retry confirmed the no-charge
  temporary edit-limit path and `/admin_accounts` cooldown state when tested
  image accounts returned provider unusual-activity 403s. A future healthy-image
  account check can still confirm successful media delivery.

Video account-risk cooldown:

- After `LFX-014`, final video 401 after bounded refresh and all-action video
  403 should return symbolic `account_risk` markers and immediately put the
  account into bounded runtime cooldown through `_mark_video_account_failure()`.
- Safe offline validation: `python -m py_compile flow_bot.py
  tests\test_flow_menu.py` and `python -m unittest discover -s tests -p
  "test_flow_menu.py"`.
- Live recurrence validation is opportunistic: do not force provider failures.
  If one occurs during normal video testing, inspect sanitized `/admin_accounts`
  output and recent journal markers to confirm the account moved into cooldown
  and the next video job routes elsewhere.

Service restart logs:

- During approved VPS restarts on 2026-06-14, `journalctl` showed ignored
  `RuntimeError: Event loop is closed` tracebacks from subprocess transport
  cleanup after SIGINT. The service restarted and handled updates normally, but
  this remains a low-priority cleanup validation target for future shutdown
  work.
- After `LFX-013` on 2026-06-15, validate shutdown cleanup by deploying
  `flow_bot.py`, running a remote syntax check, restarting `geminifree-bot`,
  waiting for the new process to reach polling, then restarting once more. In
  the fresh journal interval for the second restart, `Event` markers for
  `Event loop is closed` and `Future` markers for the known Playwright shutdown
  future should both be zero, and `systemctl is-active geminifree-bot` should
  return `active`.

Robokassa consumer/seller callback routing:

- New Robokassa invoices must include signed `Shp_bot=consumer|seller`.
  Callback handling verifies the Robokassa signature first, then forwards a
  callback that landed on the wrong local process to
  `ROBOKASSA_CONSUMER_RESULT_URL` or `ROBOKASSA_SELLER_RESULT_URL` before any
  credits are issued.
- Old invoices without `Shp_bot` remain consumer-only and keep the legacy
  provider payment id `robokassa:{InvId}` so Robokassa retries cannot double
  credit previously handled payments.
- Safe offline validation: `python -m py_compile flow_bot.py
  tests\test_flow_menu.py tests\test_seller_bot.py`,
  `python -m unittest discover -s tests -p "test_flow_menu.py"`,
  `python -m unittest discover -s tests -p "test_seller_bot.py"`, and full
  `python -m unittest discover -s tests`.
- Approved VPS smoke: after deploy, send a signed non-money Robokassa callback
  with `Shp_bot=seller` to the consumer port and an invalid user id. Expected:
  consumer forwards to seller, seller returns `bad order`, no credits are issued,
  and logs contain no Traceback/ERROR. Do not run a real Robokassa charge as a
  smoke test.

Seller Telegram marketplace video:

- Full chat validation requires an authorized user Telegram session because Bot
  API cannot send messages to the bot as a user. The local Telethon e2e session
  can be used for this when external actions and Flow quota spend are approved.
- Approved live check: from the user session, open `@photozhab_wb_bot`, use the
  marketplace animate path, upload a product photo, and wait for a returned
  video/document. This spends Flow quota and seller credits.

## Evidence Template

Use this in Verifier handoff:

```md
## Validation Evidence

Task:
Changed files:
Offline checks:
Network/stateful checks:
Checks skipped:
Result:
Residual risk:
```

## GitHub offline merge gate

- Reproduce the runner without relying on ignored local `.env` values:
  `TELEGRAM_TOKEN=123456789:TEST ROBOKASSA_ENABLED=0 python -m unittest
  discover -s tests -p "test_*.py"` (POSIX syntax; set the same variables with
  `$env:` in PowerShell).
- The placeholder is import-only. Tests must not contact Telegram, Robokassa,
  MAX, Google Flow, captcha, or any other paid/stateful endpoint.
- After every workflow repair, push the focused commit and watch the resulting
  `offline-validation` GitHub Actions run until its final conclusion is success.

## Production operations (offline-safe)

- Validate config names and paths without printing values or contacting any
  provider: `python tools/production_preflight.py --root . --env-file .env`.
  A non-zero exit is a deploy blocker. On POSIX, the env file must be mode 0600
  or stricter. Run separately for `.env.seller` when present.
- Validate shell assets: `bash -n deploy.sh deploy/bin/geminifree-bot-run
  deploy/bin/geminifree-seller-bot-run`.
- Create a consistent state backup using every configured consumer/seller path:
  `python tools/runtime_backup.py backup --root . --env-file .env
  --env-file .env.seller --output <outside-repo-directory>`, omitting the second
  env file when seller is absent. Paths are deduplicated after canonical
  resolution, so absolute and root-relative references to the same state file
  produce exactly one manifest record.
- Verify before relying on a backup: `python tools/runtime_backup.py verify
  <backup-directory>`. This checks hashes and SQLite integrity without restoring.
- Restore is destructive and must only run with services stopped and explicit
  operator approval: `python tools/runtime_backup.py restore <backup-directory>
  --root /opt/geminifree --approve-restore --service-stopped`.
- Safe focused suites: `test_production_preflight.py`, `test_runtime_backup.py`,
  `test_deployment_assets.py`, `test_ci_workflow.py`, and `test_seller_bot.py`.
- Post-deploy service/MAX checks and rollback procedure are authoritative in
  `docs/PRODUCTION_RUNBOOK.md`. A real payment, media generation, captcha solve,
  profile login, or MAX provider call is never part of an offline deploy check.

## Current Bootstrap Validation

For the documentation bootstrap task:

- Files expected: `AGENTS.md`, `AI_WORKBENCH.md`, `PROJECT_PLAN.md`,
  `TASKS.md`, `VALIDATION.md`.
- No business logic files should be modified.
- No network/stateful tests are required.
- Secrets must be described by category/name only, not copied as values.

## Keep-Warm Request Pinning

- Account keep-warm must not rotate accounts on a background timer. It should
  pin only accounts that were actually used by image/video upload or generation
  paths, for `KEEP_WARM_AFTER_REQUEST_SEC` seconds (default 1800).
- Safe offline validation:
  - `python -m py_compile flow_bot.py`
  - `$env:TELEGRAM_TOKEN='123456789:REDACTED'; python -m unittest discover -s tests -p test_keeper_parking.py`
  - `$env:TELEGRAM_TOKEN='123456789:REDACTED'; python -m unittest discover -s tests -p test_flow_accounts.py`
  - `git diff --check`
- Live validation after deploy: journal should show `keep-warm pinned after
  request: <role>:<account> for 1800 sec` only after real bot/backend requests,
  and should not show periodic `keep-warm active: video:..., image:...` wake
  cycles when there are no requests.

## Hermetic pytest and merged account/media routing

- Default pytest discovery is intentionally constrained by `pytest.ini` to the
  tracked `tests/` directory. This prevents ignored/local operator scripts whose
  names end in `_test.py` from being imported by a normal `pytest` run.
- Canonical full offline gate: `python -m pytest -q`. It must not read server
  env files or contact Telegram, MAX, Google Flow, captcha, payment or VPS hosts.
- Video failover validation must cover failed-account exclusion, immediate
  cooldown for unusual-activity risk, reference-media-not-found classification,
  and re-upload of account-bound ingredients/frames before retry.
- Telegram albums are one logical request: sort by message id, take the first
  non-empty caption, cap at four photos, and upload every photo to the same
  account/project. Image route choice retains all file ids and multi-reference
  edit failover moves the complete group.
- Seed attribution must compute `is_new` before user UPSERT, persist first-touch
  channel only for a new user, and log click/new/returning events separately.

## MAX generated-video delivery

- Backend video results may contain `videos[].video_b64` rather than a public
  URL. The MAX adapter must decode it only after an encoded-length bound, enforce
  the decoded 250 MiB cap, upload it as `video/mp4`, and send the upload token.
- Invalid or oversized generated video is a terminal result error: refund the
  reserved credits, clear the pending action, show the safe failure copy, and do
  not raise into the durable webhook inbox (which would regenerate on retry).
- Safe focused gate: `python -m pytest -q tests/test_generation_facade.py
  tests/test_max_mvp.py tests/test_max_client.py`.
- Callback acknowledgement happens after the callback's business action and is
  therefore best-effort. A stale/rejected acknowledgement must not escape into
  the durable inbox, which would replay the already completed action.
- Ingredients/Reference Veo model keys encode both tier and orientation:
  `veo_3_1_r2v_{tier}_{portrait|landscape}`. A key without the orientation was
  observed live to reach the correct endpoint but return provider HTTP 500.

## 2026-07-15 — archive 3 web/runtime wave

- Full offline gate: `python -m unittest discover -s tests -p 'test_*.py' -q`
  — **1545 tests OK**. This includes the Yandex OAuth callback/grant retry,
  atomic welcome-credit ledger, archive-3 static/accessibility contracts,
  SEO pages, and production preflight.
- JavaScript syntax: `node --check deploy/photozhab/app.js` — passed.
- Production preflight: `.venv/bin/python tools/production_preflight.py
  --root . --env-file .env` — passed.
- Deployment: runtime immutable `6512ea4b3700d9da72826b0ccfeb41f962b6ed0b`
  was deployed; docs-only closeout `22503102c59a31098d77d19f732c2739b38a8748`
  is the current VPS checkout. Backend services are active; nginx static root
  synchronized and source/public hashes matched; public `/`, `/app.html`, and
  `/generaciya-video.html` all returned HTTP 200.
- Runtime pool: exactly `sub1`, `sub4`, `sub2`; `IDLE_PARK_SEC=0`; gost
  sub1/sub2/sub4 active+enabled; old gost account services inactive+disabled.
  Egress checks were sanitized to status/IP only and matched the operator's
  three supplied endpoints.
- Browser visual QA was not claimed because the browser connector exposed no
  available instance. When one is available, inspect `/app.html` at desktop
  and 360px widths, especially onboarding skip placement and video controls;
  do not generate or pay during that check.

## 2026-07-15 — web prompt-improve backend correction

- Focused web/API gate: `python -m unittest discover -s tests -p
  'test_web_app*.py' -q` — **34 tests OK**.
- `node --check deploy/photozhab/app.js` and `python -m py_compile
  channels/web/app.py flow_bot.py` — passed.
- Full offline gate: `python -m unittest discover -s tests -p 'test_*.py' -q`
  — **1549 tests OK**.
- The browser now calls same-origin `POST /web/api/prompt-improve`; the server
  delegates to the existing Flow `AgentFlow.improve_call`, charges 5 credits
  only on a valid result, and refunds provider failures. No live paid prompt
  request was made during validation.
- Deployment: commit `a6c791d8a4a471d49458140f15e4590141201779` is checked out on
  the VPS and pushed to `origin/refactor`; fresh protected backup is at
  `/root/backups/geminifree-20260715-1838/`. Production preflight passed,
  both bot services are active, source/public app hashes match, and public
  `/`, `/app.html`, and `/generaciya-video.html` return HTTP 200. Anonymous
  `POST /web/api/prompt-improve` returns 401 without a provider call.

## 2026-07-15 — durable web chats, clipboard paste, and archive-4 UI

- Canonical full offline gate: `python -m pytest -q` — **1556 tests OK**.
- Focused final gate: `python -m pytest -q tests/test_web_app.py
  tests/test_web_app_static.py tests/test_metrics.py` — **109 tests OK**.
- `node --check deploy/photozhab/app.js`, Python compilation for modified
  modules, and `git diff --check` passed.
- Covered contracts: authenticated owner-scoped chat list/detail, bounded
  ten-turn context, successful-only prompt/chat persistence, New chat reset,
  chat restore, clipboard image validation/attachment, cross-platform identity
  projection, admin user detail/escaping, slider/footer/accessibility, and
  truthful SEO metadata/crawl files.
- No generation, payment, OAuth token exchange, captcha, browser-profile, or
  other paid/stateful provider call was made. The in-app browser connector had
  no instance, so screenshot/manual visual QA is not claimed.
- VPS backup, `sub2` → `sub5` runtime switch, immutable deployment, and public
  health checks remain the controlled post-commit steps.

## 2026-07-15 — production closeout

- Pushed immutable commit `7c5132951f9a02ba9ebe900e4b908e7069f43e0a` to
  `origin/refactor`; `deploy.sh` passed tracked-secret audit, compilation, two
  production preflight runs, static synchronization, and bot restarts.
- Runtime state backup `/root/backups/geminifree-20260715-182759/` passed the
  repository backup verifier (13 state/SQLite files). Protected env/systemd/
  gost configuration and sub2/sub5 profile archives were retained separately;
  the manifest SHA-256 was recorded in `HANDOFF.md`.
- Sanitized VPS inspection confirmed exactly `sub1,sub4,sub5`, all three
  profiles and proxy channels present, and `IDLE_PARK_SEC=0`. `gost-sub2` is
  disabled/inactive; `gost-sub5` is enabled/active; consumer and seller bot
  services are active.
- `nginx -t` passed. Source and `/var/www/photozhab` hashes match for app
  HTML/JS/CSS. Public home, app, image page, video page, and sitemap returned
  HTTP 200. No paid generation, payment, captcha, OAuth token exchange, or
  browser-profile login smoke was performed.

## 2026-07-16 — result actions and owner-scoped downloads

- Canonical full offline gate: `python -m pytest -q` — **1559 tests passed**.
- Focused web/API/design gate: `python -m pytest -q tests/test_web_app.py
  tests/test_web_app_static.py tests/test_photozhab_design_static.py` — **48
  tests passed**.
- `python -m py_compile channels/web/app.py`, `node --check
  deploy/photozhab/app.js`, and `git diff --check` passed.
- Covered contracts: Google-host allowlisting, opaque owner-scoped image
  download tokens, fixed attachment disposition, cross-session denial,
  untrusted upstream rejection with credit refund, video inline/download
  separation, archive-4 Edit/Animate actions, button busy recovery,
  fly-to-composer motion, mobile wrapping, keyboard focus, and
  `prefers-reduced-motion` fallback.
- No generation, payment, captcha, OAuth exchange, proxy, or browser-profile
  call was made. The in-app browser runtime exposed no browser instance, so
  screenshot/manual visual QA is not claimed.
- Deployment: immutable `5b2f2ec440b1c9bbd186e92decce4a1239272266` is checked
  out on the VPS. `deploy.sh` created and verified 13 runtime backup files at
  `/root/backups/geminifree-deploy/runtime-20260715T193611Z-7c5132951f9a`;
  tracked-secret audit, both production preflight runs, service restarts, and
  static synchronization passed. Public `/`, `/app.html`, `/robots.txt`, and
  `/sitemap.xml` returned HTTP 200; an anonymous invalid download token returned
  HTTP 404. No paid generation/payment/OAuth/captcha smoke was performed.

## 2026-07-16 вЂ” security hardening pass

- Canonical full offline gate: `python -m pytest -q` вЂ” **1566 passed, 1
  skipped**.
- Focused security gate: `python -m pytest tests/test_admin_api.py
  tests/test_flow_accounts.py tests/test_logging_redaction.py
  tests/test_max_client.py tests/test_production_preflight.py
  tests/test_photozhab_design_static.py -q` вЂ” **167 passed**.
- Python compilation passed for `admin_api.py`, `accounts/pool.py`,
  `security/logging_redaction.py`, `channels/max/client.py`, and
  `tools/production_preflight.py`.
- `node --check deploy/photozhab/app.js` and syntax-checking the inline script
  extracted from `deploy/photozhab/admin.html` both passed.
- `python tools/check_tracked_secrets.py` and `git diff --check` passed.
- `git check-ignore -v .claude/settings.json bash.exe.stackdump` confirmed both
  local artifacts are ignored.
- Local `.env` production preflight was run without printing values and failed
  closed with missing/unsafe production settings: `FLOW_BROWSER_API_KEY`,
  `ROBOKASSA_PUBLIC_BASE_URL`, and `CREDITS_SQLITE=1`.
- No generation, payment, captcha, OAuth exchange, proxy verification,
  Telegram/MAX live call, or browser-profile action was made.
