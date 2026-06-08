# VALIDATION.md

Validation must be chosen by risk level. Prefer offline checks first. Network,
paid, account-mutating, or browser-profile-mutating checks require explicit
task approval.

## Validation Categories

### Offline/Safe

Use for documentation, packaging, and pure refactors.

- Inspect file list: `rg --files`
- Check git status if git exists: `git status --short`
- Python syntax check, assumption:
  `python -m py_compile flow_bot.py google_labs_flow_bot.py gemini_bot.py checker.py find_recaptcha_params.py login.py test_recaptcha_params.py test_tokens.py`
- Node syntax check, assumption:
  `node --check bot.js`
- Search for accidental secret patterns before commit without printing matched
  lines, assumption:
  `rg -l "Bearer |TELEGRAM_TOKEN=|TWOCAPTCHA|session-token|ya29\\.|socks5h?://|http://[^\\s]+:[^\\s]+@"`

Notes:

- `python -m py_compile` is an assumption because Python version is not pinned.
- `node --check` is an assumption because Node version is not pinned and there
  is no `package.json`.
- Secret scans must not print secret values in command output or final reports.
  Use file-only output such as `rg -l` / `--files-with-matches`, or a dedicated
  redaction tool. Do not use `rg -n`, `-o`, `-C`, `-A`, or `-B` for secret
  scans in this repository.
- `node --check bot.js` was observed to pass as a syntax-only check, but
  runtime Node dependencies are not installed in this workspace because
  `node_modules/` and `package.json` are absent.

### Local Browser/Stateful

Requires explicit approval.

- `python login.py`
- Any script that opens persistent Chrome profile via Playwright.

Risks:

- Can mutate or delete `google_profile/`.
- Can open a visible browser.
- Can affect logged-in Google account/session state.

### Network/External API

Requires explicit approval.

- `python checker.py`
- `python test_tokens.py`
- `python test_recaptcha_params.py`
- `python flow_bot.py`
- `python google_labs_flow_bot.py`
- `python gemini_bot.py`
- `python telegram_bot_tester.py --mode telegram-smoke --approve-external-action`
- `python telegram_bot_tester.py --mode telegram-generation --prompt "simple safe landscape test" --approve-external-action`
- `python telegram_bot_tester.py --mode telegram-ramp --prompt "simple safe landscape test" --max-steps 3 --delay-sec 90 --approve-external-action`
- `node bot.js` - inferred entry point only; do not run until Node dependencies
  are installed and external Telegram/API startup is explicitly approved.

Risks:

- Calls Telegram, Google, OpenRouter, ImageRouter, 2Captcha, proxy services, or
  IP APIs.
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
- `python google_labs_flow_bot.py`
- `python gemini_bot.py`
- `python login.py`
- `python checker.py`
- `python find_recaptcha_params.py`
- `python test_recaptcha_params.py`
- `python test_tokens.py`
- `python telegram_bot_tester.py`

Assumptions:

- `node bot.js` - inferred entry point only; currently blocked by missing
  dependency manifest/installed Node modules.
- `pip install python-telegram-bot twocaptcha requests`
- `npm install node-telegram-bot-api @openrouter/sdk openai socks-proxy-agent cross-spawn dotenv`
- `python -m py_compile ...`
- `node --check bot.js`
- `python -m py_compile telegram_bot_tester.py tg_e2e/config.py tg_e2e/runner.py tg_e2e/telethon_client.py`

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

- Parse/install validation depends on selected tooling.
- Since no manifests exist yet, first manifest task must document exact install
  commands and lockfile policy.

Python code change:

- `python -m py_compile <changed .py files>` - assumption.
- Add targeted offline tests before external validation where possible.
- Avoid running Telegram/Google scripts unless explicitly approved.

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
- Manual Telegram smoke after login, approval required:
  `python telegram_bot_tester.py --mode telegram-smoke --approve-external-action`
- Manual single generation, approval required and only after smoke passes:
  `python telegram_bot_tester.py --mode telegram-generation --prompt "simple safe landscape test" --approve-external-action`
- Manual ramp, approval required and only after single generation passes:
  `python telegram_bot_tester.py --mode telegram-ramp --prompt "simple safe landscape test" --max-steps 3 --delay-sec 90 --approve-external-action`

Telegram E2E external modes contact Telegram and may cause the bot to contact
Google Flow, mutate bot cooldown state, spend quota, trigger captcha/rate-limit
signals, or create/update `.sessions/` files. Never run them as routine
validation or CI.

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

- `node --check bot.js` - assumption.
- Add package scripts once `package.json` exists.
- Avoid starting Telegram bot unless explicitly approved.

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
- `build_video_reference_images` and `build_video_frame_images` must use the
  uploaded Flow asset `mediaId` values, stripping any `fe_id_` prefix. Do not use
  Telegram `file_id` values.
- Live 2026-06-08 evidence in `HANDOFF.md`: text-to-video, Frames, and
  Ingredients generated successfully through the bot. Keep using the captured
  endpoint/request-shape helpers instead of guessing new video endpoints.
- Video prompt edit currently generates a new text-to-video version from the
  previous prompt plus the user's edit instruction and charges 20 bot credits.
  It is not a native video-to-video edit endpoint.
- Video Extend is UI-gated to original `veo-lite` results only, but the callback
  must remain fail-closed/no-charge until a real extend endpoint is captured.
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

## Current Bootstrap Validation

For the documentation bootstrap task:

- Files expected: `AGENTS.md`, `AI_WORKBENCH.md`, `PROJECT_PLAN.md`,
  `TASKS.md`, `VALIDATION.md`.
- No business logic files should be modified.
- No network/stateful tests are required.
- Secrets must be described by category/name only, not copied as values.
