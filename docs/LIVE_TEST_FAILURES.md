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

No entries yet.

## Closed Failures

No entries yet.
