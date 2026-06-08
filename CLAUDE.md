# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An experimental, single-folder automation workspace for Telegram bots and Google
Labs Flow image generation. There is **no package manifest** (`requirements.txt`/
`package.json` are absent), **no git repository**, and **no formal database** —
runtime state lives in local JSON/pickle files and a persistent Chrome profile.
Python targets 3.11 (per `__pycache__`).

Read these companion docs before non-trivial work — they are the source of truth
and this file only summarizes them:

- `AGENTS.md` — mandatory 5-role flow (Architect → Reviewer → Implementer →
  Verifier → Committer), universal rules, and the **Danger Zones** list.
- `AI_WORKBENCH.md` — full file/stack inventory, every env var, and API contracts.
- `VALIDATION.md` — exactly which checks are safe (offline) vs. require approval.
- `PROJECT_PLAN.md` / `TASKS.md` — phased roadmap and current task status.
- `docs/FLOW_QUOTA_PROFILER.md` — the `flow_profiler` design and safety model.
- `docs/MONETIZATION.md` — credits/pricing model, margin tiers, Telegram Stars
  payout economics, break-even (authored by the `monetization-strategist` agent).

## Three runtime families

1. **`bot.js`** (Node) — large multipurpose Telegram bot: chat personas via
   OpenRouter, images via ImageRouter (OpenAI-compatible client), Telegram
   payments, and `SAFE_COMMANDS`/admin handlers that run shell commands via
   `exec`/`spawn`. State in `bot_states_multi.json`. Everything is in one file.
2. **Python Google Flow bots** — `flow_bot.py` is the **primary, maintained**
   Flow bot (drive `labs.google/fx/tools/flow`, base API
   `aisandbox-pa.googleapis.com/v1`):
   - `flow_bot.py`: `SessionKeeper` holds a live Playwright Chrome session to
     intercept fresh Bearer tokens + reCAPTCHA, then replays them via an aiohttp
     client (hybrid browser+HTTP). A **button-driven casual bot**: a persistent
     bottom reply-keyboard (always visible) + an inline main menu → a
     single-screen generation wizard (count + format in ONE message, then
     prompt) → per-image action buttons. Native command menu via
     `set_my_commands`. Pure logic is in `flow_core.py`
     (payload/parse/edit-capture, **credits & Stars pricing**, stores) and RU
     microcopy in `flow_copy.py`. Per-user state: own Flow project
     (`user_projects.json`), credit balance (`user_credits.json`), learned edit
     shape (`edit_capture.json`), learned upscale request (`upscale_capture.json`)
     — all gitignored runtime state. Per-image buttons: edit / vary / regen / mix
     / ✨ Чёткость ×2 (prompt enhance) / 🔍 Увеличить HD (the service's REAL
     upscale, learned-and-replayed) / ⬇ original. User-facing copy is
     backend-neutral (never names Google/Flow, never says "captcha"); a test
     enforces this. Monetization: 1 image = 10 credits, 50 starter, upscale = +5
     (0.5×), Telegram Stars (XTR) top-ups (pack buttons show ≈generations);
     pricing model & margins in `docs/MONETIZATION.md`. Admins (`ADMIN_IDS` in
     `.env`) can `/grant <user_id> <credits>`.
   - `google_labs_flow_bot.py` / `gemini_bot.py`: legacy strategies, not present
     in this tree.
3. **`flow_profiler/`** (well-structured) — a safety-first CLI package for
   profiling Flow quota. Entry point `flow_quota_profiler.py`. This (and now
   `flow_core.py`) is the model for new code here: layered modules, pure testable
   helpers, secret-redacting writers, and a covered test suite.

Design agents live in `.claude/agents/` (`telegram-copywriter`,
`telegram-ux-flow`, `monetization-strategist`) — use them to author bot
microcopy, button-driven UX flows, and pricing/upsell before implementing.

Diagnostic/support scripts (`login.py`, `checker.py`, `test_tokens.py`,
`test_recaptcha_params.py`, `find_recaptcha_params.py`) are not part of the bots.

## Commands

**Offline / safe — run these freely:**

```bash
# flow_profiler test suite (the only real automated tests)
python -m unittest discover -s tests -p "test_flow_profiler_*.py"
python -m unittest tests.test_flow_profiler_rl001a   # single test module

# flow_bot offline tests (edit/menu/credits — no browser/network)
python -m unittest discover -s tests -p "test_flow_edit.py"
python -m unittest discover -s tests -p "test_flow_menu.py"
python -m unittest discover -s tests -p "test_*.py"   # full suite

# syntax checks (assumption: Python 3.11, no pinned version)
python -m py_compile flow_quota_profiler.py flow_profiler/*.py
python -m py_compile flow_bot.py flow_core.py flow_copy.py
node --check bot.js

# offline profiler dry-run (no browser/network/secrets)
python flow_quota_profiler.py --mode dry-run --prompt-id test-001 --prompt "safe prompt"
```

The profiler CLI only branches on two `--mode` values — `single` and
`browser-check` (both stateful, both gated behind `--approve-external-action`).
**Every other `--mode` value falls through to the offline dry-run**, so
`dry-run` is just the default path, and the stray `ramp-mode.*`/`batch-mode.*`
files in root are *not* evidence of real `ramp`/`batch` modes. Runs are written
under `--output-dir` (default `flow_profiler_runs/`).

**Requires explicit operator approval — never run as routine validation:** any
bot entry point (`python flow_bot.py`, `node bot.js`, …), `python login.py`
(it deletes and recreates `google_profile/`), `test_tokens.py`,
`test_recaptcha_params.py` (spends 2Captcha balance), and the profiler `single`
/ `browser-check` modes (open a real browser, touch the Chrome profile/account).
See `VALIDATION.md` for the full risk-tiered list.

There are no installed dependencies in the tree (`node_modules/` absent). Install
commands are assumptions only — confirm before adding a manifest.

## Conventions specific to this repo

- **Secret hygiene is the top priority.** Never read, print, or commit values
  from `.env`, `.env_flow`, `api_config.json`, `labs.google.har`,
  `google_profile/`, or `proxylist*.txt`. Note that `.gitignore` does **not** yet
  cover `.env_flow`, `api_config.json`, `google_profile/`, or `proxylist*.txt`.
  Secret scans must use file-only output (`rg -l`), never `-n/-o/-C/-A/-B`.
- **Hardcoded secrets exist in source** (e.g. a SOCKS proxy in `bot.js`, tokens/
  proxies in `gemini_bot.py`/`checker.py`/`login.py`). Treat them as live; do not
  echo them, and remove only as a dedicated, approved task.
- **`flow_profiler` safety model**: stateful modes are gated behind
  `--approve-external-action`; `config.py` rejects unsafe flags, path traversal,
  `.har` files, and the sensitive names above. Preserve these invariants and
  their tests when editing the package.
- **Encoding**: many comments/strings contain Russian text and mojibake. Don't
  bulk-reformat or "fix" encoding except as a dedicated migration task.
- **`bot.js` admin/shell surface** (`SAFE_COMMANDS`, `DANGEROUS_ACTIONS`,
  `CRITICAL_ACTIONS`, `exec`) is high-risk — never trigger these actions to
  validate changes.
- **Ignore the root-level scratch artifacts**: the many empty/near-empty
  `*-mode.{stdout,stderr}.txt`, `single-*.txt`, `unknown-*.txt`, `lock*.txt`,
  `stale.*`, `flagcheck.*` files and the `flow_profiler_runs/` directory are
  leftover validation/run output, not source. `tempCodeRunnerFile.py` is an
  editor temp file. Don't treat any of these as meaningful or "fix" them.
