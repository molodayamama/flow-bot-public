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

## MAX transport smoke

The harness is offline by default and documents each external side effect:

```bash
python -m unittest discover -s tests -p "test_max_smoke.py"
python tools/max_smoke.py --mode plan --env-file .env
```

After explicit operator approval, run `subscription`, then `message`, then
`media` as documented in `docs/MAX_SMOKE.md`. The last two modes send visible
content to the configured operator-owned MAX target; none of the modes print
configuration values or response bodies.

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
  env file when seller is absent.
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
