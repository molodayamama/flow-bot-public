# Telegram E2E Tester

`telegram_bot_tester.py` is an approval-gated harness for checking the current
Google Flow Telegram bot from a real Telegram user account.

The default mode is offline only. It does not contact Telegram, Google Flow,
2Captcha, proxies, or the browser profile.

## Modes

```bash
python telegram_bot_tester.py --mode dry-run
```

External modes require `--approve-external-action` and local Telegram user
credentials:

```bash
python telegram_bot_tester.py --mode telegram-login --approve-external-action
python telegram_bot_tester.py --mode telegram-login-request --approve-external-action
python telegram_bot_tester.py --mode telegram-login-complete --tg-code 12345 --approve-external-action
python telegram_bot_tester.py --mode telegram-smoke --approve-external-action
python telegram_bot_tester.py --mode telegram-generation --prompt "simple safe landscape test" --approve-external-action
python telegram_bot_tester.py --mode telegram-ramp --prompt "simple safe landscape test" --max-steps 3 --delay-sec 90 --approve-external-action
```

Required external configuration is loaded from `.env` by default and can also
come from process environment variables or CLI flags. Priority is CLI flags,
then process environment, then `.env`.

- `BOT_USERNAME`
- `TG_API_ID`
- `TG_API_HASH`
- `TG_PHONE`
- `TG_CODE`, only for `telegram-login-complete` when not passed by `--tg-code`
- `TG_PASSWORD`, optional 2FA password for `telegram-login-complete`
- `TG_E2E_PROXY_URL`, optional MTProto proxy URL such as `tg://proxy?...`
- `TG_E2E_SESSION_FILE`, optional session base path, default `.sessions/tg_e2e`
- `--session-file`, default `.sessions/tg_e2e`

The tester also accepts `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and
`TELEGRAM_PHONE` for compatibility with the `telegram-pain-lead-finder`
project.

Use `--env-file <path>` to point at a different env file. Missing env files are
ignored so dry-run mode works before credentials are configured.

`TG_E2E_PROXY_URL` is preferred for the tester. `TG_PROXY_URL` is also accepted
only when it starts with `tg://proxy`, so the tester does not accidentally reuse
the bot API SOCKS proxy setting from `flow_bot.py`.

`telegram-login` creates or validates the Telethon user `.session` file only. It
does not send messages to the bot. Use it first when Telegram asks for a login
code, then run `telegram-smoke` after the session is authorized.

For non-interactive shells, use the two-step login:

```bash
python telegram_bot_tester.py --mode telegram-login-request --approve-external-action
python telegram_bot_tester.py --mode telegram-login-complete --tg-code 12345 --approve-external-action
```

`telegram-login-request` stores only the temporary Telegram `phone_code_hash` in
the gitignored `.sessions/*.login.json` file. `telegram-login-complete` deletes
that file after successful authorization.

`telegram-smoke` sends `/start` and `/menu` only. It intentionally avoids
`/balance`, image generation, captcha solving, browser launch, and Google Flow.
`/status` is an admin-only diagnostics command; check it separately from an
owner/admin Telegram session when operational diagnostics are needed.

`telegram-generation` sends one `/one <prompt>` request.

`telegram-ramp` sends at most five `/one <prompt>` requests. It stops on
Telegram flood wait, bot cooldown, Google quota/rate/captcha/auth signals, or
any client error. The minimum accepted delay is 17 seconds because `flow_bot.py`
currently has a 15 second per-user cooldown.

## Outputs

Runs are written under `tg_e2e_runs/<run_id>/`:

- `events.jsonl`
- `summary.json`

Prompt text, Telegram phone, API hash, and session path are treated as secrets.
Proxy URL/secret are also treated as secrets. Events store prompt hash and
length, not prompt text.

## Safety Notes

- Do not use this harness to bypass Telegram or Google Flow limits.
- Do not run external modes from CI.
- Do not commit `.sessions/`, `*.session`, `tg_e2e_runs/`, prompts with private
  content, or Telegram credentials.
- Use `telegram-generation` before `telegram-ramp`.
- Stop and inspect manually after any 401, 403, 429, captcha, quota, account
  warning, or Telegram flood wait signal.

## Role Notes

### Architect

Problem statement: automate bot checks as a real Telegram user while preserving
Google Flow website behavior and avoiding accidental paid/stateful calls.

Desired behavior: provide offline planning by default, gated real Telegram smoke
and single-generation checks, and conservative ramp checks that stop at the
first warning signal.

Files likely to change: `telegram_bot_tester.py`, `tg_e2e/`, `tests/`,
`docs/TELEGRAM_E2E_TESTER.md`, `.gitignore`, `VALIDATION.md`, `TASKS.md`.

API/env/state surfaces affected: Telegram user API credentials, Telethon session
file, bot cooldown state, Google Flow generation quota when generation modes are
approved.

Risk level: low for dry-run and offline tests; high for approved external modes.

Validation plan: syntax check, unit tests for config/runner behavior, dry-run
CLI execution, file-only secret scan. External Telegram/Google checks are
manual and skipped unless explicitly approved.

Assumptions: Telethon is the intended user-session client; installing it is an
assumption because there is no dependency manifest. `flow_bot.py` is the only
current bot entry point present in this workspace.

Rollback approach: delete `telegram_bot_tester.py`, `tg_e2e/`, test/docs
additions, `tg_e2e_runs/`, and any `.sessions/` files. No existing runtime state
is migrated.

## Role Handoff

Task: add Telegram E2E automation harness
Role completed: Architect
Files touched: none before implementation
Assumptions: Telethon is available or can be installed later; real runs need explicit approval
Validation: planned offline checks only
Risks: Telegram session leakage, Google Flow quota/captcha/rate limits, bot cooldown mutation
Next role: Reviewer

### Reviewer

Blocking issues in the plan: none for offline harness.

Security/privacy concerns: session files and Telegram credentials must never be
printed or committed. Prompt text must not be persisted.

Missing validation: real Telegram and Google Flow behavior still needs a manual
approved smoke run after offline checks.

Scope creep: do not refactor `flow_bot.py` in this task.

Decision: approved with notes.

## Role Handoff

Task: add Telegram E2E automation harness
Role completed: Reviewer
Files touched: none before implementation
Assumptions: external modes remain opt-in
Validation: require offline tests before any real Telegram run
Risks: external modes are high risk and must not run routinely
Next role: Implementer
