# Live Test Fix Recommendations

Purpose: collect sanitized repair recommendations for live E2E failures recorded
in `docs/LIVE_TEST_FAILURES.md`. This is a sidecar planning file for fixers; it
must not contain secrets, raw logs, raw HAR bodies, Telegram `file_id` values,
account emails, phone numbers, payment credentials, bearer/cookie material,
proxy credentials, private prompts, or full provider responses.

Use this file when a live failure has enough evidence to propose an offline-safe
fix path. Do not invent failures here. Every entry must link back to one or more
failure ids from `docs/LIVE_TEST_FAILURES.md`.

## Failure Id References

- Use the failure id exactly as written in the failure log, for example `LF-001`.
- If one fix addresses several failures, list all ids: `LF-001`, `LF-003`.
- If a failure splits into multiple candidate fixes, create separate fix entries
  that all reference the same `LF-000`.
- Do not copy the full failure evidence into this file; summarize only the
  sanitized part needed to justify the proposed fix.

## Status Values

- `proposed`: hypothesis and fix path are drafted, not implemented.
- `accepted`: main fixer agreed this is the path to implement.
- `in_progress`: implementation is underway elsewhere.
- `blocked`: cannot proceed without more sanitized evidence or approval.
- `implemented`: code/config/doc change exists and is ready for verification.
- `verified`: offline or approved live validation passed.
- `rejected`: superseded or found to be the wrong fix path.

## Entry Template

```md
### YYYY-MM-DD LFX-000 short title

- Failure id: LF-000
- Status: proposed
- Priority: S1 / S2 / S3, matching or explaining divergence from the failure
- Fix owner: unassigned
- Proposed by: name/agent/session

Root cause hypothesis:

- Sanitized hypothesis for why the live E2E failed.
- Confidence: low / medium / high

Proposed fix:

- Smallest code, config, doc, or operational change likely to resolve the
  failure.

Owner files:

- `flow_bot.py` - reason this file may need changes
- `flow_core.py` - reason this file may need changes
- `tests/...` - targeted offline coverage to add or update

Validation:

- Offline checks: commands or manual checks that do not contact live services.
- Approved live checks: exact live check category needed, if any; do not include
  secrets or private inputs.
- Rollback check: how to confirm the fix can be reverted safely.

Notes:

- Redacted context, edge cases, or open questions for the implementer.
```

## Proposed Fixes

No entries yet.

## Implemented Or Verified Fixes

### 2026-06-14 LFX-001 direct Flow upload API for Telegram photos

- Failure id: LF-001
- Status: verified
- Priority: S1
- Fix owner: current Codex session
- Proposed by: current Codex live E2E session

Root cause hypothesis:

- The browser upload listener no longer reliably observes the Flow upload
  response, so Telegram photo uploads can complete without a usable `mediaId`.
- Confidence: medium

Implemented fix:

- Add a direct Flow image upload API path before the existing browser upload
  fallback. Keep the fallback for provider drift.

Owner files:

- `flow_core.py` - upload payload and response parsing helpers.
- `flow_bot.py` - `SessionKeeper` upload path and sanitized logging.
- `tests/test_flow_edit.py` - offline payload/parser coverage.

Validation:

- Offline checks passed for syntax and targeted image-edit tests.
- Approved live Telegram check with `kotenok.jpg` succeeded and delivered an
  edited image result.

Notes:

- Do not paste returned Flow media ids or Telegram file ids into docs/logs.

### 2026-06-14 LFX-002 video bearer refresh and retry handling

- Failure id: LF-002
- Status: implemented
- Priority: S1
- Fix owner: current Codex session
- Proposed by: current Codex live E2E session

Root cause hypothesis:

- Some video failures are stale bearer/session failures. The old path refreshed
  too late for a useful retry, especially after HTTP 401.
- Confidence: medium

Implemented fix:

- On first video 403, refresh bearer before trying the next captcha action.
- On first video 401 for an action, refresh bearer and retry that action once
  with a fresh captcha token and batch id.

Owner files:

- `flow_bot.py` - video generation retry path.
- `tests/test_flow_menu.py` - source-level guards for 401/403 retry behavior.

Validation:

- Offline syntax and targeted menu tests passed.
- Live 401 was seen before this fix; the direct 401 retry path was not
  re-triggered after deploy because the final healthy account returned HTTP 200.

Notes:

- Keep retries bounded. Do not loop indefinitely on provider auth errors.

### 2026-06-14 LFX-003 quarantine failing video accounts

- Failure id: LF-002
- Status: verified
- Priority: S1
- Fix owner: operator/current Codex session
- Proposed by: current Codex live E2E session

Root cause hypothesis:

- The repeated all-action 403 was account-specific: video-capable accounts
  `main` and `sub1` failed, while another video-capable account succeeded.
- Confidence: high for the live incident; medium as a general rule.

Implemented fix:

- Use existing admin commands to disable video routing for failing accounts:
  `/acc_vid_off main` and `/acc_vid_off sub1`.

Owner files:

- `flow_bot.py` - future automatic quarantine/health scoring should live near
  video failure handling and account routing.
- `flow_core.py` - future account capability/state helpers if needed.
- `tests/test_flow_menu.py` - future routing and quarantine coverage.

Validation:

- Approved live Telegram account checks showed `main` and `sub1` as image-only
  after the toggles.
- A subsequent Omni 4s text-to-video request routed to `sub2`, returned HTTP 200,
  reached successful provider status, downloaded the video, and delivered it in
  Telegram.

Notes:

- This is an operational mitigation, not full automation. Add automatic
  per-account video quarantine if repeated video 401/403 failures should stop
  routing without operator action.

### 2026-06-14 LFX-004 VPS admin_help wrapper

- Failure id: LF-003
- Status: verified
- Priority: S3
- Fix owner: current Codex session
- Proposed by: current Codex live E2E session

Root cause hypothesis:

- The operator runbook referenced a shell helper that was not deployed in the
  repo.
- Confidence: high

Implemented fix:

- Add repo-root `admin_help` wrapper that prints safe Telegram admin commands
  and common VPS service/log checks.

Owner files:

- `admin_help` - shell helper.

Validation:

- Deployed to the VPS, marked executable, and verified with `./admin_help`.

Notes:

- The wrapper prints command names only. It must not include secrets, proxy
  values, tokens, account emails, or raw runtime data.

### 2026-06-14 LFX-005 live probe callback collection

- Failure id: tooling-observation
- Status: verified
- Priority: S3
- Fix owner: current Codex session
- Proposed by: current Codex live E2E session

Root cause hypothesis:

- When clicking an old inline button, the probe collected messages after the
  clicked message id. If newer chat messages already existed, stale messages
  were mixed into the callback summary.
- Confidence: high

Implemented fix:

- Before a callback click, record the current latest chat message id and collect
  only bot replies after that point.

Owner files:

- `tools/live_telegram_probe.py`

Validation:

- `python -m py_compile tools\live_telegram_probe.py` passed.
- VPS callback check on an existing video download button returned only the two
  fresh download-preparation/result messages.

Notes:

- This is live-test tooling only; it does not change bot behavior.

### 2026-06-14 LFX-006 r2v/frames effective model logging

- Failure id: observability-observation
- Status: implemented
- Priority: S3
- Fix owner: current Codex session
- Proposed by: current Codex live E2E session

Root cause hypothesis:

- The r2v/frames diagnostic log ran before `build_video_payload()` converted the
  UI model id into the mode-specific provider key, so successful Reference and
  Frames requests looked like `veo_3_1_t2v_lite` in the journal.
- Confidence: high

Implemented fix:

- Log `effective_model_key` using `video_reference_model_key()` for Ingredients
  and `video_frames_model_key()` for Frames.

Owner files:

- `flow_bot.py`
- `tests/test_flow_menu.py`

Validation:

- Source-level guard added in `tests/test_flow_menu.py`.
- Live 2026-06-14 Reference and Frames requests both returned HTTP 200 and
  delivered videos; this fix only corrects future diagnostic output.

Notes:

- Do not add media ids, project ids, tokens, cookies, proxy URLs, or raw request
  bodies to this log.

### 2026-06-14 LFX-007 single-image result regen

- Failure id: LF-004
- Status: verified
- Priority: S0
- Fix owner: current Codex session
- Proposed by: current Codex live E2E session

Root cause hypothesis:

- The image result keyboard labelled `regen` with the default one-image price,
  but the callback handler reused the four-image default generation count.
- Confidence: high

Implemented fix:

- Make `_regen_and_send()` request one image for the inline result `Заново`
  action, matching the displayed 10-credit price and user expectation.

Owner files:

- `flow_bot.py` - image result action handler.
- `tests/test_flow_edit.py` - source-level regression guard.

Validation:

- Offline syntax and targeted image/menu tests passed.
- Approved live Telegram check generated one fresh image, clicked `Заново · 10
  кр`, received one regenerated image, and observed a one-image balance delta.

Notes:

- Keep the generic `action_price("regen", n)` function per-image; only the
  single-result button behavior changed.

### 2026-06-14 LFX-008 preserve my-photo upload state on text

- Failure id: LF-005
- Status: verified
- Priority: S2
- Fix owner: current Codex session
- Proposed by: current Codex live E2E session

Root cause hypothesis:

- The plain-text handler recognized image prompt/edit states, but did not
  recognize the `await == "photo"` state set by the "edit my photo" entry. Text
  therefore fell through to the generic image prompt wizard.
- Confidence: high

Implemented fix:

- Add a narrow `awaiting == "photo"` guard in `handle_plain_text()` before the
  prompt/wizard fallback. The guard repeats the photo request message and keeps
  the upload state active.

Owner files:

- `flow_bot.py` - plain-text state routing.
- `tests/test_flow_menu.py` - source-level regression guard for branch order.

Validation:

- Offline syntax and targeted menu tests passed.
- Approved live Telegram check opened `m:myphoto`, sent plain text, received the
  photo request message again, and confirmed no Flow HTTP request in the service
  journal.

Notes:

- This fix intentionally does not change photo upload, caption edit, video, or
  generic image prompt behavior.

### 2026-06-14 LFX-009 classify no-fallback image 403 as rate-limited

- Failure id: LF-007
- Status: verified
- Priority: S1
- Fix owner: current Codex session
- Proposed by: current Codex live E2E session

Root cause hypothesis:

- `FlowHttpClient.generate_images()` correctly rotated through recaptcha actions,
  but when browser fallback was disabled it returned generic `gen_failed` after
  exhausting the actions. The caller could no longer distinguish all-action HTTP
  403 from other provider failures, so image edit skipped the existing
  rate-limit/failover branch.
- Confidence: high

Implemented fix:

- Track whether any action returned HTTP 403 during `generate_images()`. If all
  actions are exhausted while `allow_browser_fallback=False`, return the
  existing `rate_limited` message instead of generic `gen_failed`.

Owner files:

- `flow_bot.py` - image generation/edit API error classification.
- `tests/test_flow_edit.py` - source-level guard that the no-fallback 403 branch
  returns `rate_limited` before generic `gen_failed`.

Validation:

- Offline syntax and targeted edit/menu tests passed.
- Approved live Telegram check ran `m:myphoto` with `kotenok.jpg` and an edit
  prompt. The first account exhausted HTTP 403 actions, the bot reuploaded on
  the failover account, the failover account also hit the provider 403 state,
  and the user received the temporary edit-limit copy with no balance debit.

Notes:

- This fix intentionally does not enable browser fallback for image edits. It
  only preserves the provider failure category so the existing edit failover and
  no-charge user copy can run.

### 2026-06-14 LFX-010 handle repeated image unusual-activity 403s

- Failure id: LF-008
- Status: proposed
- Priority: S2
- Fix owner: unassigned
- Proposed by: current Codex live E2E session

Root cause hypothesis:

- The image edit provider rejected both the first selected image account and one
  failover account with all-action HTTP 403 and `PUBLIC_ERROR_UNUSUAL_ACTIVITY`.
  The code now handles this as a no-charge temporary edit failure, but provider
  account health still prevents successful delivery.
- Confidence: medium

Proposed fix:

- Treat repeated all-action unusual-activity 403s as an account-health signal:
  either use existing admin commands to quarantine affected image accounts after
  repeated evidence, or add a bounded automatic cooldown/quarantine path for
  repeated image 403 runtime failures.

Owner files:

- `flow_bot.py` - if automatic cooldown is added to account-pool failure
  handling.
- `docs/LIVE_TEST_FAILURES.md` / `HANDOFF.md` - if the operator chooses manual
  quarantine and records the live decision instead of a code change.

Validation:

- Offline checks depend on the chosen implementation.
- Approved live check: retry a safe `m:myphoto` edit after cooldown or after
  routing away from affected accounts; confirm either successful media delivery
  or clean no-charge temporary-limit copy.

Notes:

- Do not run `login.py` or recreate browser profiles as part of this fix without
  explicit operator approval.
