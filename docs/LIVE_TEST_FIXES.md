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

No entries yet.
