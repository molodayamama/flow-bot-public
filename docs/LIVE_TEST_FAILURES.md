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
- Status: open
- Next fix owner: unassigned
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

## Open Failures

### 2026-06-14 LF-001 photo upload returns no Flow mediaId

- Severity: S1
- Status: open
- Next fix owner: unassigned
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

### 2026-06-14 LF-002 text-to-video rejected with 403 on all captcha actions

- Severity: S1
- Status: open
- Next fix owner: unassigned
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

### 2026-06-14 LF-003 VPS shell admin helper is absent

- Severity: S3
- Status: open
- Next fix owner: unassigned
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

## Closed Failures

No entries yet.
