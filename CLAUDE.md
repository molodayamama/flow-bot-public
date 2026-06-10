# CLAUDE.md

This file is a compact orientation note for Claude Code. The broader current
truth is in `AGENTS.md`, `AI_WORKBENCH.md`, `VALIDATION.md`, `PROJECT_PLAN.md`,
`TASKS.md`, and `HANDOFF.md`.

## Project

This is a Python Telegram automation workspace for Google Labs Flow image/video
generation experiments.

Current maintained app:

- `flow_bot.py` - primary Telegram bot;
- `flow_core.py` - payload builders, parsing, pricing, stores, account routing;
- `flow_copy.py` - user-facing Russian copy;
- `metrics.py` - local SQLite metrics;
- `prompts_lib.py` - Ideas Hub templates;
- `flow_quota_profiler.py` / `flow_profiler/` - approval-gated quota tooling;
- `telegram_bot_tester.py` / `tg_e2e/` - approval-gated Telegram E2E harness.

The old Node stack is gone. `bot.js` was removed on 2026-06-10. Historical docs
may mention `google_labs_flow_bot.py` or `gemini_bot.py`; they are not current
files in this tree.

There is no external database service. Runtime state is local files:
`metrics.db`, JSON state files, capture JSON, and a persistent Chrome profile in
`google_profile/`.

## Must-Read Docs

- `AGENTS.md` - mandatory Architect -> Reviewer -> Implementer -> Verifier ->
  Committer flow.
- `AI_WORKBENCH.md` - current file/stack inventory.
- `VALIDATION.md` - safe offline checks and approval-gated checks.
- `docs/ENV_SETUP.md` - how to fill `.env`.
- `docs/MONETIZATION.md` - pricing and account economics.
- `docs/VIDEO_UX.md` - current video UX and status.
- `docs/UI_PRICE_LABELS.md` - current pricing-label rules.
- `docs/GROWTH_PLAN.md` - growth experiments and guardrails.

## Safe Commands

Offline checks that do not open browsers or contact external services:

```bash
python -m unittest discover -s tests -p "test_*.py"
python -m unittest discover -s tests -p "test_flow_menu.py"
python -m unittest discover -s tests -p "test_flow_video.py"
python -m unittest discover -s tests -p "test_prompts_lib.py"
python -m py_compile flow_bot.py flow_core.py flow_copy.py metrics.py prompts_lib.py
python flow_quota_profiler.py --mode dry-run --prompt-id test-001 --prompt "safe prompt"
```

Do not run without explicit operator approval:

- `python flow_bot.py`;
- `python login.py` because it deletes/recreates `google_profile/`;
- `test_tokens.py`;
- `test_recaptcha_params.py`;
- live capture scripts;
- Telegram E2E modes that contact Telegram;
- anything that spends 2Captcha balance or generates real media.

## Secret Hygiene

Never print or commit values from:

- `.env`;
- `.env_flow`;
- `api_config.json`;
- `labs.google.har`;
- `google_profile/`;
- proxy lists;
- capture files with live request data;
- generated logs or runtime state.

Secret scans must use file-only output (`rg -l` or `git grep -l`), never matching
line output.

Known caveat: old hardcoded material existed in repository history. Rotate and
scrub before adding any remote or pushing.

## Current Product Notes

- First-start grant: `STARTER_CREDITS = 30`.
- Image generation remains the low-friction product: 10 credits per base image.
- Video is premium: Omni Flash 4s starts at 100 bot credits.
- Ingredients/reference-to-video and Frames/start-end video are enabled.
- Uploaded-video edit is captured but hidden behind
  `UPLOAD_VIDEO_EDIT_ENABLED = False`.
- Ideas Hub includes seller and pet hooks; see `docs/IDEAS_HUB.md`.

Keep user-facing copy provider-neutral: no Google/Flow/captcha wording in bot
messages.
