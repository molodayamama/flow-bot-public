# Live Test Failures

Purpose: record sanitized failures from approved live Telegram/Google Flow
checks so the next fixer can reproduce the problem without reading secrets,
runtime databases, browser profiles, HAR files, or raw logs.

Use this file only after a live/stateful check was explicitly approved and
failed. Keep entries short. Do not paste bearer tokens, cookies, captcha keys,
proxy credentials, Telegram tokens, Telegram `file_id` values, account emails,
phone numbers, raw HAR bodies, or full response bodies.

## Severity

- `S0`: money/credits charged incorrectly, secret exposure, account lock, or
  broad production outage.
- `S1`: paid generation/edit/video flow fails or refunds incorrectly for normal
  users.
- `S2`: non-paid UX failure, retry/cooldown confusion, account-routing issue, or
  partial feature failure with workaround.
- `S3`: cosmetic copy/menu issue or low-risk diagnostics gap.

## Entry Template

```md
### YYYY-MM-DD LF-000 short title

- Severity: S1
- Status: verified fixed
- Next fix owner: n/a
- Live check approved by: operator/name or issue link
- Environment: local / VPS / other; bot commit or file timestamp if known
- Surface: image / image edit / video text / video frames / video ingredients /
  video edit / payment / Telegram menu / other

Telegram input:

- User/chat: sanitized id label only, for example `owner-test-chat`
- Command/callback/path: `/start`, `/imgn 4`, `v:*`, button label, or short
  navigation path
- Attachments: none / photo count / video present; do not paste Telegram
  `file_id`
- Prompt/caption: short sanitized summary, not private user content

Telegram output:

- User-facing text: exact short text if safe, otherwise sanitized summary
- Messages/media sent: count and type only
- Credits/refund observed: charged/refunded/unchanged/unknown

Flow account:

- Account label: internal safe label such as `cap1`, `image-only`, or `unknown`
- Project/media ownership notes: same account / failover reupload / unknown
- Proxy/profile notes: category only, no URLs or credentials

Google/Flow evidence:

- Endpoint/action: sanitized endpoint suffix or feature name
- HTTP status: 400 / 401 / 403 / 429 / 500 / timeout / unknown
- Error class/code: sanitized provider code if present
- Body snippet: max 300 chars, redact tokens, cookies, ids, URLs with secrets, and private
  prompts

Reproduction:

1. Start from approved live test preconditions.
2. Navigate/send sanitized Telegram input.
3. Observe sanitized output and provider status.

Expected:

- What should have happened.

Actual:

- What happened instead.

Suspected cause:

- Best current hypothesis or `unknown`.

Next fix notes:

- Smallest likely code/doc/config area to inspect next.
```

## Recorded Failures

### 2026-06-14 LF-001 photo upload returns no Flow mediaId

- Severity: S1
- Status: verified fixed
- Next fix owner: completed in current Codex session
- Live check approved by: operator request in current Codex session
- Environment: VPS `/opt/geminifree`; `geminifree-bot` active; `@photozhab_bot`
- Surface: image edit / photo upload

Telegram input:

- User/chat: `owner-test-chat`
- Command/callback/path: direct Telegram photo upload
- Attachments: 1 photo (`kotenok.jpg` test image); Telegram `file_id` omitted
- Prompt/caption: short non-private test caption

Telegram output:

- User-facing text: `Не удалось загрузить фото. Попробуй отправить ещё раз.`
- Messages/media sent: one status message edited to failure; no media result
- Credits/refund observed: unchanged/unknown; upload failed before paid edit generation

Flow account:

- Account label: unknown image-upload route; image-only account was available in pool
- Project/media ownership notes: no `mediaId` returned
- Proxy/profile notes: browser upload path; no credentials logged

Google/Flow evidence:

- Endpoint/action: image upload through browser helper
- HTTP status: unknown
- Error class/code: `mediaId` missing
- Body snippet: log summary reported `upload_image: mediaId не получен (ответов: 0). Схемы: upload_capture.json`

Reproduction:

1. Start from approved live Telegram E2E preconditions on the VPS.
2. Send one photo with a short caption to the bot.
3. Observe upload status and the final edited failure message.

Expected:

- The photo should upload into Flow, produce a usable `mediaId`, and continue to caption-driven image edit or prompt collection.

Actual:

- The upload status changed to a generic retry message; no Flow `mediaId` was captured.

Suspected cause:

- Browser upload contract or capture listener drifted; current helper did not observe any matching upload response.

Next fix notes:

- Inspect `SessionKeeper.upload_image`, `upload_capture.json` parsing/listener rules, and upload endpoint drift using an abort-safe capture before changing bot behavior.

Resolution:

- Implemented direct Flow image upload API use before the old browser upload fallback.
- Live Telegram photo+caption check with `kotenok.jpg` succeeded after deploy:
  the bot received a Flow `mediaId`, image generation returned HTTP 200, and a
  generated image result was delivered in Telegram.

### 2026-06-14 LF-002 text-to-video rejected with 403 on all captcha actions

- Severity: S1
- Status: mitigated; automatic runtime cooldown implemented
- Next fix owner: future live tester for recurrence monitoring
- Live check approved by: operator request in current Codex session
- Environment: VPS `/opt/geminifree`; `geminifree-bot` active; `@photozhab_bot`
- Surface: video text

Telegram input:

- User/chat: `owner-test-chat`
- Command/callback/path: `/menu` -> `m:vid` -> `v:fam:omni` -> `v:model:omni-flash-4s` -> plain text prompt
- Attachments: none
- Prompt/caption: short non-private test prompt

Telegram output:

- User-facing text: `Не удалось создать видео. Кредиты возвращены — попробуй ещё раз!`
- Messages/media sent: no video; retry/menu buttons shown
- Credits/refund observed: refunded; balance stayed at 1845 credits before and after

Flow account:

- Account label: `main` (admin report showed one new failure on `main`)
- Project/media ownership notes: no media result
- Proxy/profile notes: active video-capable pool account; no proxy details logged

Google/Flow evidence:

- Endpoint/action: text-to-video async generation
- HTTP status: 403 for every tried action
- Error class/code: provider rejected all reCAPTCHA actions
- Body snippet: log summary reported `Сервис отклонил запрос видео (403) на всех action.`

Reproduction:

1. Open video wizard from `/menu`.
2. Select quick Omni 4s, keep default 16:9 and count 1.
3. Send a short text prompt.
4. Observe retry UI and logs.

Expected:

- Bot should generate and deliver one 4-second video, then show video result actions.

Actual:

- The provider rejected every captcha action (`VIDEO_GENERATION`, `PINHOLE`, `batchAsyncGenerateVideoText`, `GENERATE_VIDEO`, `IMAGE_GENERATION`) with HTTP 403; the bot refunded credits and showed retry.

Suspected cause:

- Google Flow video captcha action/session/API contract drift or account-level video access problem.

Next fix notes:

- Re-capture the current video text request contract and reCAPTCHA action with `tools/capture_video.py` or another approved live capture; verify account-specific video capability before code changes.

Resolution:

- Implemented one bearer refresh on the first video 403 before trying the next
  captcha action, plus one retry of the current action after a video 401.
- Operationally quarantined failing video accounts with `/acc_vid_off main` and
  `/acc_vid_off sub1`.
- A follow-up Omni 4s text-to-video live run routed to `sub2`, returned HTTP 200,
  reached successful provider status, downloaded the video, and delivered it in
  Telegram.

Residual risk:

- Strong video account-risk responses now place the account into bounded runtime
  cooldown automatically. Persistent `/acc_vid_off` remains available for
  operator-confirmed bad accounts.

### 2026-06-14 LF-003 VPS shell admin helper is absent

- Severity: S3
- Status: verified fixed
- Next fix owner: completed in current Codex session
- Live check approved by: operator request in current Codex session
- Environment: VPS `/opt/geminifree`; `geminifree-bot` active
- Surface: admin/server command

Telegram input:

- User/chat: n/a
- Command/callback/path: shell command `./admin_help`
- Attachments: none
- Prompt/caption: n/a

Telegram output:

- User-facing text: n/a
- Messages/media sent: n/a
- Credits/refund observed: n/a

Flow account:

- Account label: n/a
- Project/media ownership notes: n/a
- Proxy/profile notes: n/a

Google/Flow evidence:

- Endpoint/action: n/a
- HTTP status: n/a
- Error class/code: command not found
- Body snippet: n/a

Reproduction:

1. SSH to the VPS and change to `/opt/geminifree`.
2. Run `./admin_help` or resolve `admin_help` in the shell.
3. Observe that no shell helper exists.

Expected:

- The operator-provided admin helper command should print account-control commands, or the runbook should name the Telegram command instead.

Actual:

- No shell command/helper was found. The Telegram command `/admin_help` works and lists `/acc_off`, `/acc_on`, `/acc_vid_off`, and `/acc_vid_on`.

Suspected cause:

- Deployment helper missing or operator/runbook instruction is stale.

Next fix notes:

- Either add a documented shell wrapper for admin help or update runbook instructions to use Telegram `/admin_help`.
- Added repo-root `admin_help` shell helper and deployed it to the VPS. Live
  shell check showed `./admin_help` prints the Telegram admin commands and
  service-log helpers.

### 2026-06-14 LF-004 image regen button underquoted generated count

- Severity: S0
- Status: verified fixed
- Next fix owner: completed in current Codex session
- Live check approved by: operator request in current Codex session
- Environment: VPS `/opt/geminifree`; `geminifree-bot` active; `@photozhab_bot`
- Surface: image result action / credits

Telegram input:

- User/chat: `owner-test-chat`
- Command/callback/path: click image result button `Заново · 10 кр`
- Attachments: none
- Prompt/caption: short non-private live E2E prompt

Telegram output:

- User-facing text: before fix, the clicked button advertised one-image regen
  pricing; after fix, the status/result both show one generated image.
- Messages/media sent: before fix, four regenerated photos were delivered;
  after fix, one regenerated photo was delivered.
- Credits/refund observed: before fix, the regen click consumed four image units;
  after fix, the regen click consumed one image unit.

Flow account:

- Account label: normal image route
- Project/media ownership notes: same user-owned image result action
- Proxy/profile notes: normal live Flow image generation path

Google/Flow evidence:

- Endpoint/action: image generation
- HTTP status: 200
- Error class/code: n/a
- Body snippet: n/a

Reproduction:

1. Generate one image with a short prompt.
2. Click `Заново · 10 кр` under that result.
3. Compare delivered result count and balance delta with the button price.

Expected:

- The result button should produce one new image and charge one image unit.

Actual:

- Before the fix, the handler generated four images from a button labelled as a
  one-image 10-credit action.

Suspected cause:

- `_image_keyboard()` used the default `action_price("regen")` label for one
  image, while `_regen_and_send()` hardcoded `num_images=4`.

Next fix notes:

- Fixed by making the result-button regen request one image and adding a
  source-level regression guard.

Resolution:

- Changed `_regen_and_send()` to call `_generate_and_send(..., num_images=1,
  action="regen")`.
- Live verification after deploy: `/one` consumed one image unit, then
  `Заново · 10 кр` delivered one `1/1` photo and consumed one image unit.

### 2026-06-14 LF-005 my-photo text escaped the photo-upload state

- Severity: S2
- Status: verified fixed
- Next fix owner: completed in current Codex session
- Live check approved by: operator request in current Codex session
- Environment: VPS `/opt/geminifree`; `geminifree-bot` active; `@photozhab_bot`
- Surface: image edit entry / state routing

Telegram input:

- User/chat: `owner-test-chat`
- Command/callback/path: main menu -> `m:myphoto`, then plain text while the bot
  is waiting for a photo upload
- Attachments: none
- Prompt/caption: sanitized live E2E marker text

Telegram output:

- User-facing text: before fix, the bot opened the normal image-generation
  wizard with the typed text as a pending prompt; after fix, it repeated the
  photo request message.
- Messages/media sent: no media.
- Credits/refund observed: no charge; no provider call after the fixed check.

Flow account:

- Account label: n/a
- Project/media ownership notes: n/a
- Proxy/profile notes: no new provider request in the fixed check

Google/Flow evidence:

- Endpoint/action: n/a after fix
- HTTP status: n/a after fix
- Error class/code: n/a
- Body snippet: n/a

Reproduction:

1. Open the main menu.
2. Click `m:myphoto`.
3. Send plain text instead of a photo.

Expected:

- The bot should stay in the photo-upload state and ask the user to send a
  photo.

Actual:

- Before the fix, the plain text fell through to the generic image prompt
  fallback and opened the image-generation wizard.

Suspected cause:

- `handle_plain_text()` did not handle `await == "photo"` before the generic
  image wizard fallback.

Next fix notes:

- Fixed by adding a narrow `awaiting == "photo"` guard before prompt/wizard
  fallback and a source-level regression guard.

Resolution:

- Live verification after deploy: text sent in the photo-upload state returned
  the photo request message again, and the service journal showed only the
  handled Telegram update, with no Flow HTTP request.

### 2026-06-14 LF-006 restart emits Event loop is closed cleanup noise

- Severity: S3
- Status: verified fixed
- Next fix owner: n/a
- Live check approved by: operator request in current Codex session
- Environment: VPS `/opt/geminifree`; `geminifree-bot` restart during deploy
- Surface: service shutdown logs / Playwright cleanup

Telegram input:

- User/chat: n/a
- Command/callback/path: service restart
- Attachments: none
- Prompt/caption: n/a

Telegram output:

- User-facing text: n/a
- Messages/media sent: n/a
- Credits/refund observed: n/a

Flow account:

- Account label: browser/account pool startup/shutdown
- Project/media ownership notes: n/a
- Proxy/profile notes: persistent profiles were reused; `login.py` was not run

Google/Flow evidence:

- Endpoint/action: n/a
- HTTP status: n/a
- Error class/code: `RuntimeError: Event loop is closed`
- Body snippet: n/a

Reproduction:

1. Restart `geminifree-bot` while browser subprocesses have been initialized.
2. Inspect recent `journalctl -u geminifree-bot` output.

Expected:

- Service shutdown/restart should not emit Python ignored-exception tracebacks.

Actual:

- During restart, several ignored `BaseSubprocessTransport.__del__` tracebacks
  were logged after SIGINT. The service still stopped and restarted
  successfully.

Suspected cause:

- Browser/Playwright subprocess transports are being garbage-collected after the
  asyncio event loop is already closed.

Next fix notes:

- Implemented as `LFX-013`: explicit shutdown cleanup now closes Playwright
  keepers, Robokassa callback runner, and the Telegram bot session before the
  event loop closes.
- A narrow asyncio exception filter suppresses the known Playwright driver
  future raised during intentional shutdown, while all other loop exceptions
  still use the default handler.

Resolution:

- VPS validation on 2026-06-15 restarted the deployed service after the new
  cleanup path was active. The service returned to `active`, and the fresh
  journal interval contained zero `Event loop is closed` markers and zero
  Playwright shutdown `Future` markers.

### 2026-06-14 LF-007 image edit all-action 403 skipped failover classification

- Severity: S1
- Status: verified fixed
- Next fix owner: current Codex session
- Live check approved by: operator request in current Codex session
- Environment: VPS `/opt/geminifree`; `geminifree-bot`; approved live Telegram E2E
- Surface: image edit / account failover / user-facing error copy

Telegram input:

- User/chat: owner-test-chat
- Command/callback/path: main menu -> `m:myphoto` -> upload photo -> send edit prompt
- Attachments: one local test photo (`kotenok.jpg`), no Telegram `file_id` stored here
- Prompt/caption: safe edit prompt asking for a small visual change

Telegram output:

- User-facing text before fix: generic generation failure text.
- User-facing text after fix: temporary edit limit text; context-save/retry guidance.
- Messages/media sent: no edited media returned in this provider state.
- Credits/refund observed: balance unchanged during the fixed-path validation.

Flow account:

- Account label: image account pool (`cap1` first, then failover to `main` after fix)
- Project/media ownership notes: after fix, failover account reuploaded the source photo before retry.
- Proxy/profile notes: existing persistent profile/account pool reused; `login.py` was not run.

Google/Flow evidence:

- Endpoint/action: image generation/edit via `batchGenerateImages`
- HTTP status: 403 on every recaptcha action for the tested account before fix.
- Error class/code: provider unusual-activity class surfaced through the 403 body.
- Body snippet: sanitized; no full provider body copied.

Reproduction:

1. Open `m:myphoto` in the approved Telegram test chat.
2. Upload `kotenok.jpg`.
3. Send a short edit prompt while the selected image account returns HTTP 403
   for all recaptcha actions and browser fallback is disabled for edit/i2i.
4. Observe the Telegram response and `/admin_accounts` health counters.

Expected:

- All-action HTTP 403 in the no-browser-fallback edit path should be classified
  as a temporary account/rate-limit failure so existing edit failover and saved
  context copy can run.

Actual:

- Before the fix, `generate_images()` lost the all-403 signal after exhausting
  actions and returned generic `gen_failed`, so the edit path did not use the
  intended rate-limit/failover handling.

Suspected cause:

- The final no-browser-fallback branch did not remember whether the exhausted
  action loop failed specifically due to HTTP 403.

Resolution:

- `FlowHttpClient.generate_images()` now tracks whether any action returned 403
  and returns the existing `rate_limited` message when all actions are exhausted
  without browser fallback.
- Live verification after deploy showed the first account hit all-action 403,
  the bot reuploaded the source photo on the failover account, the failover
  account also hit the provider 403 state, and the user received the temporary
  edit-limit copy. Balance remained unchanged.

### 2026-06-14 LF-008 image edit provider unusual-activity on active accounts

- Severity: S2
- Status: mitigated; clean live retry verified, healthy-account delivery pending
- Next fix owner: operator for account health / future live tester
- Live check approved by: operator request in current Codex session
- Environment: VPS `/opt/geminifree`; `geminifree-bot`; approved live Telegram E2E
- Surface: image edit / provider account health

Telegram input:

- User/chat: owner-test-chat
- Command/callback/path: main menu -> `m:myphoto` -> upload photo -> send edit prompt
- Attachments: one local test photo (`kotenok.jpg`), no Telegram `file_id` stored here
- Prompt/caption: safe edit prompt asking for a small visual change

Telegram output:

- User-facing text: temporary edit limit text after failover exhausted the tested accounts.
- Messages/media sent: no edited image delivered.
- Credits/refund observed: balance unchanged.

Flow account:

- Account label: tested current image account and one failover image account
- Project/media ownership notes: failover reuploaded the source photo before retry.
- Proxy/profile notes: existing persistent profiles reused; `login.py` was not run.

Google/Flow evidence:

- Endpoint/action: image generation/edit via `batchGenerateImages`
- HTTP status: repeated 403 on each recaptcha action for both tested accounts.
- Error class/code: `PUBLIC_ERROR_UNUSUAL_ACTIVITY`
- Body snippet: sanitized; no raw provider response copied.

Reproduction:

1. With the `LF-007` classification fix deployed, open `m:myphoto`.
2. Upload `kotenok.jpg`.
3. Send a short edit prompt.
4. Inspect the bot response, `/admin_accounts`, and recent service journal.

Expected:

- At least one healthy image-capable account should complete the edit request
  or the bot should keep the failure no-charge and route future retries away
  from accounts that remain in the provider unusual-activity state.

Actual:

- Both tested accounts returned all-action 403 with provider unusual-activity
  evidence. The bot preserved user credits and showed the temporary edit-limit
  copy, but no edited image was delivered.
- A 2026-06-15 live retry after `LFX-010` again hit all-action provider
  `PUBLIC_ERROR_UNUSUAL_ACTIVITY` on the tested image accounts. `/admin_accounts`
  showed the affected accounts in bounded runtime cooldown, and the user-facing
  result stayed the no-charge temporary edit-limit copy.

Suspected cause:

- Provider/account-risk state, captcha action drift, proxy reputation, or
  account cooldown. This is operationally separate from the `LF-007` code
  classification bug.

Next fix notes:

- Implemented as `LFX-010`: no-browser-fallback image 403s that include the
  provider unusual-activity signal now return a symbolic `account_risk` marker.
  Image edit/i2i paths immediately place that account in the existing bounded
  runtime cooldown instead of waiting for the generic three-failure threshold.
- The user-facing behavior stays no-charge temporary edit-limit copy when all
  available accounts fail.
- Do not recreate profiles or run `login.py` without explicit approval.

Resolution:

- Offline regression tests prove the symbolic provider-risk marker and immediate
  cooldown routing path.
- Provider-backed live retry on 2026-06-15 confirmed the clean no-charge failure
  path and immediate runtime cooldown for risky image accounts. Successful media
  delivery still depends on routing to a healthy provider account.

### 2026-06-14 LF-009 status command exposed backend diagnostics

- Severity: S1
- Status: verified fixed
- Next fix owner: current Codex session
- Live check approved by: active live E2E goal
- Environment: VPS `/opt/geminifree`; `geminifree-bot`; approved live Telegram E2E
- Surface: Telegram command authorization / diagnostics exposure

Telegram input:

- User/chat: owner-test-chat
- Command/callback/path: `/status`
- Attachments: none
- Prompt/caption: n/a

Telegram output:

- User-facing text before fix: backend diagnostic status including bearer
  presence/age, project-id presence/value, cookie count, captcha provider, and
  captcha account balances.
- Messages/media sent: one text response.
- Credits/refund observed: unchanged.

Flow account:

- Account label: default session keeper diagnostics
- Project/media ownership notes: command exposed diagnostic project id to the
  caller before fix; raw value is not copied here.
- Proxy/profile notes: no profile recreation; `login.py` was not run.

Google/Flow evidence:

- Endpoint/action: n/a
- HTTP status: n/a
- Error class/code: n/a
- Body snippet: n/a

Reproduction:

1. Send `/status` to the bot.
2. Observe diagnostic details in the Telegram response.
3. Inspect `cmd_status` and confirm it lacked an `ADMIN_IDS` gate before fix.

Expected:

- Backend/session diagnostics should be restricted to bot admins and documented
  under the admin command section.

Actual:

- `/status` was wired as a public command and returned operational diagnostics.

Suspected cause:

- `cmd_status` was implemented before the newer admin command grouping and was
  left without the same `ADMIN_IDS` check used by other diagnostics.

Resolution:

- `cmd_status` now denies non-admin callers with the existing `admin_denied`
  copy.
- `/admin_help` now lists `/status` in the `ADMIN_IDS` command section, not the
  public user command section.
- Offline test guards the admin gate and help placement. Live owner/admin
  validation after deploy confirmed `/status` still returns diagnostics for an
  admin without printing raw diagnostic values in this log.

### 2026-06-14 LF-010 video photo-wait text opened image wizard

- Severity: S2
- Status: verified fixed
- Next fix owner: current Codex session
- Live check approved by: active live E2E goal
- Environment: VPS `/opt/geminifree`; `geminifree-bot`; approved live Telegram E2E
- Surface: video Ingredients/Frames state routing / plain text fallback

Telegram input:

- User/chat: owner-test-chat
- Command/callback/path: `/menu` -> `m:vid` -> `v:fam:ing`, then plain text
  while the bot was waiting for an Ingredients photo.
- Attachments: none
- Prompt/caption: harmless text used only to test state routing.

Telegram output:

- User-facing text before fix: the image generation wizard opened and treated
  the text as an image prompt.
- Messages/media sent: text-only wizard response; no generated media.
- Credits/refund observed: unchanged.

Flow account:

- Account label: n/a
- Project/media ownership notes: no media upload or provider generation started.
- Proxy/profile notes: no profile recreation; `login.py` was not run.

Google/Flow evidence:

- Endpoint/action: n/a
- HTTP status: n/a
- Error class/code: n/a
- Body snippet: n/a

Reproduction:

1. Open `/menu`, choose video, then choose Ingredients.
2. Send plain text instead of a photo while the bot asks for an Ingredients
   photo.
3. Observe the next Telegram response.

Expected:

- The bot should repeat the photo-specific hint and keep the active video
  photo-wait state.

Actual:

- Before the fix, the text fell through to the generic image prompt fallback and
  opened the image wizard.

Suspected cause:

- `handle_plain_text()` handled video prompt states, but did not guard
  `vawait == "ving_photo"`, `vawait == "vfrm_start"`, or
  `vawait == "vfrm_end"` before the generic image prompt fallback.

Resolution:

- `handle_plain_text()` now repeats the correct photo hint for Ingredients,
  Frames start, and Frames end wait states before the image fallback can run.
- Offline source-level test pins the branch order.
- Live post-fix validation on the VPS confirmed Ingredients plain text returned
  the Ingredients photo request and Frames-start plain text returned the first
  frame photo request. Recent service logs showed handled Telegram updates only
  for these checks; no provider generation was started.

### 2026-07-13 LF-011 new web identity has no Flow project

- Severity: S1
- Status: fix implemented, live recheck pending
- Next fix owner: current Codex session
- Live check approved by: operator in the active VPS/web-app request
- Environment: VPS `/opt/geminifree`; commit `6e4cd8f`; public web API
- Surface: first-party website / image generation

Web input/output:

- Session: fresh opaque browser session; no user value recorded.
- Path: `POST /web/api/generate`, text-to-image, Nano Banana 2, one image.
- Prompt: harmless synthetic studio scene; no private user content.
- Output: HTTP 502 with generic `generation_failed`; no media.
- Credits/refund observed: temporary smoke credit fully restored.

Flow account:

- Account label: sanitized pool account.
- Project/media ownership notes: new negative identity had no stored project.
- Proxy/profile notes: no profile recreation; `login.py` was not run.

Google/Flow evidence:

- Endpoint/action: project selection followed by browser image fallback.
- HTTP status: browser timeout before a direct generation request.
- Error class/code: `TimeoutError`; no provider body retained.
- Body snippet: n/a.

Reproduction:

1. Create a fresh web session and provide exactly the server image price.
2. Submit a valid same-origin text-to-image request.
3. Observe project creation fail because the current Flow UI has no matching
   New Project control, then browser fallback time out waiting for its textarea.

Expected:

- A fresh external identity uses a valid project on its selected pool account
  and returns one image, charging only on valid media.

Actual:

- The external identity attempted fragile per-user UI project creation, received
  no project id and fell into browser generation fallback.

Suspected cause:

- `ProjectManager.ensure()` treated negative web/MAX identities like Telegram
  users even though external identities are cheap/anonymous and the documented
  fallback already allows a shared session project.

Next fix notes:

- For negative identities only, reuse an existing stored project with the exact
  selected account prefix. Keep positive Telegram per-user creation unchanged
  and never borrow a project from another account.

## Closed Failures

- `LF-001`: fixed by Flow upload API path and live-verified with `kotenok.jpg`.
- `LF-002`: mitigated by bearer retry handling, manual video quarantine for
  `main` and `sub1`, and automatic bounded runtime cooldown for future strong
  video account-risk responses; live-verified video delivery on `sub2`.
- `LF-003`: fixed by the `admin_help` shell wrapper and live-verified on the VPS.
- `LF-004`: fixed by single-image result regen and live-verified on the VPS.
- `LF-005`: fixed by preserving the my-photo upload state on plain text and
  live-verified on the VPS.
- `LF-007`: fixed by preserving all-action HTTP 403 classification in the
  no-browser-fallback image edit path and live-verified on the VPS.
- `LF-009`: fixed by restricting `/status` diagnostics to `ADMIN_IDS` and
  moving the command to the admin help section.
- `LF-010`: fixed by preserving video photo-wait states on plain text and
  live-verified for Ingredients and Frames-start on the VPS.
