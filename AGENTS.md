# AGENTS.md

This repository is prepared for Codex-driven development through a mandatory
five-role flow:

Architect -> Reviewer -> Implementer -> Verifier -> Committer

Do not skip roles for non-trivial work. If a task is tiny documentation-only
work, the same human/AI may perform several roles, but the outputs below must
still be present in the task notes.

## Project Snapshot

This is a Telegram automation workspace for AI chat/image bots and Google Labs
Flow image generation experiments.

Observed stack:

- Python scripts using `aiogram`, `python-telegram-bot`, `aiohttp`,
  `aiohttp-socks`, `playwright`, `python-dotenv`, `twocaptcha`, and `requests`.
- Node.js Telegram bot in `bot.js` using `node-telegram-bot-api`,
  `@openrouter/sdk`, `openai`, `socks-proxy-agent`, `cross-spawn`, `dotenv`,
  and Node built-ins.
- Browser automation through Playwright with persistent Chrome profile in
  `google_profile/`.
- External APIs: Telegram Bot API, OpenRouter, ImageRouter, Google Labs Flow
  web/API surface, 2Captcha.
- No package manifest was found: no `package.json`, no `requirements.txt`.
- Git **is** initialized (branch `main`); initial import committed 2026-06-08.
  Commit per new feature/change going forward. `.gitignore` excludes all
  secrets/runtime state — keep it that way.
- No application database schema was found. Runtime state is file-based.

Important files:

- `bot.js` - large Node.js Telegram bot with chat personas, image generation,
  payments, state files, admin/server commands.
- `flow_bot.py` - current-looking aiogram Google Flow bot using Playwright for
  session capture and HTTP API calls for image generation.
- `google_labs_flow_bot.py` - python-telegram-bot Google Flow HTTP client using
  bearer token/cookies and 2Captcha.
- `gemini_bot.py` - aiogram + Playwright bot that drives Google Flow through the
  browser UI.
- `login.py` - manual Chrome profile login helper; deletes/recreates
  `google_profile/`.
- `checker.py` - proxy checker script.
- `find_recaptcha_params.py` - helper that prints browser-console JS for
  reCAPTCHA parameter discovery.
- `test_recaptcha_params.py` - interactive 2Captcha parameter test script.
- `test_tokens.py` - interactive Google token/cookie/API diagnostic script.
- `.env`, `.env_flow`, `api_config.json`, `labs.google.har`,
  `google_profile/`, proxy lists - sensitive local configuration/artifacts.
- `HANDOFF.md` - cross-agent log between Claude Code and Codex: who did what,
  open questions, and what the other side should pick up next. Update it at the
  end of any non-trivial work session.

## Video generation (current state)

`flow_bot.py` + `flow_core.py` implement Google Flow video generation
(text-to-video). Verified async API contract is documented in
`docs/FLOW_QUOTA_PROFILER.md`-style notes and the `flow-video-api` memory:

- Async flow: POST `video:batchAsyncGenerateVideoText` -> poll
  `video:batchCheckAsyncVideoGenerationStatus` -> download via
  `labs.google/fx/api/trpc/media.getMediaUrlRedirect`.
- Model catalog `VIDEO_MODELS` (flow_core.py) is the single source of truth for
  friendly id -> Google `videoModelKey`, family, duration, price. Confirmed keys:
  `omni-flash-4s` (`abra_t2v_4s`), `veo-lite` (`veo_3_1_t2v_lite`). Others are
  pattern-inferred (`confirmed: False`) - confirm with `tools/capture_video.py`.
- UI: video wizard under callback prefix `v:` (family -> variant -> settings
  -> prompt). Veo variants are labelled with their real names ("Veo Lite",
  "Veo Fast", "Veo Quality"); Omni Flash variants stay benefit-led/neutral.
- **Verified live:** the video endpoint accepts reCAPTCHA action
  `VIDEO_GENERATION` (live smoke on 2026-06-08 reached HTTP 200 and
  `MEDIA_GENERATION_STATUS_SUCCESSFUL`). `generate_video` keeps fallback actions
  after `VIDEO_GENERATION` for provider drift, re-solving captcha and retrying
  only on 403.
- **Frames** (start/end frame + prompt -> video) is **captured and enabled**.
  Live capture `tools/video_frames_capture.json` confirmed endpoint
  `video:batchAsyncGenerateVideoStartAndEndImage` with `startImage`/`endImage`
  carrying `mediaId` (no `fe_id_` prefix) + `cropCoordinates`, and model key
  `veo_3_1_interpolation_{tier}` (owner-confirmed tiers lite/fast/quality; no
  orientation in the key). Default Frames model is `veo-lite`
  (`VID_FRAMES_DEFAULT_MODEL`).
- **Ingredients** (photos + prompt -> video, reference-to-video) is **captured and
  enabled**. Live capture `tools/video_ingredients_capture.json` confirmed endpoint
  `video:batchAsyncGenerateVideoReferenceImages` with `referenceImages: [{mediaId,
  imageUsageType: "IMAGE_USAGE_TYPE_ASSET"}]` and model key
  `veo_3_1_r2v_{tier}_{orientation}` (the r2v key encodes BOTH tier AND aspect;
  only `fast`+portrait is live-confirmed, other tiers/orientations pattern-match).
  Works from 1–4 photos; pricing = per-model price + `VIDEO_INGREDIENTS_SURCHARGE`.
- Both reference modes share a UI model picker (Veo Lite/Fast/Quality) plus format
  + count pickers, caption-as-prompt, and album upload. `build_video_payload`
  routes by input (text / frames / reference); `generate_video` selects the
  matching endpoint. **A live run is still recommended** to confirm HTTP 200 and
  pin the working reCAPTCHA action per mode.
- Pricing/economics (incl. the new **100 credits = $1** rate and the
  pay-via-intermediary cost model for the Google account) live in
  `docs/MONETIZATION.md`.

## Universal Rules

- Do not change business logic during architecture/review/verification tasks.
- Do not quote secret values from `.env`, `.env_flow`, `api_config.json`,
  `labs.google.har`, `google_profile/`, or proxy lists.
- Treat bearer tokens, cookies, Telegram tokens, captcha keys, proxy
  credentials, browser profiles, HAR files, and generated logs as secrets.
- Do not run scripts that contact paid or stateful external services unless the
  task explicitly asks for it and the operator accepts the cost/risk.
- Do not run `login.py` unless the task explicitly requires recreating the
  Chrome profile; it removes `google_profile/`.
- Do not run admin/server commands exposed by `bot.js` from Telegram or shell
  during development validation unless explicitly requested.
- Do not invent commands. If a command is inferred from code or comments rather
  than a manifest, label it `assumption`.
- Prefer small, reviewable changes. Keep runtime behavior changes separate from
  secret hygiene, packaging, formatting, and documentation.
- Preserve existing local state files and user artifacts unless a task explicitly
  authorizes migration or deletion.

## Role Contracts

### Architect

Purpose: define the smallest coherent change and protect project boundaries.

Required output before implementation:

- Problem statement and desired behavior.
- Files likely to change.
- API/env/state surfaces affected.
- Risk level and validation plan.
- Assumptions, explicitly marked.
- Rollback approach for local files/state.

Architect must check:

- Whether the task touches external APIs, browser profile, payment flow,
  generated media, admin commands, or secrets.
- Whether package manifests are still absent and commands are still assumptions.
- Whether existing code has mojibake/encoding risk.

### Reviewer

Purpose: challenge the plan before code changes.

Required output:

- Blocking issues in the plan.
- Security/privacy concerns.
- Missing validation.
- Scope creep or unrelated refactors.
- Decision: `approved`, `approved with notes`, or `blocked`.

Reviewer should focus on bugs, regressions, token leakage, paid API usage,
destructive profile actions, and unverified assumptions.

### Implementer

Purpose: make the approved change only.

Required behavior:

- Follow the Architect plan and Reviewer notes.
- Touch only the files approved for the task.
- Keep secrets out of logs, docs, commits, and examples.
- Do not normalize/format giant files unless the task is specifically about
  formatting.
- If a needed command is missing because there is no manifest, stop and update
  task notes rather than inventing a dependency set silently.

Required output:

- Changed files.
- What changed.
- Any deviations from the plan.

### Verifier

Purpose: prove the change is correct enough for the risk level.

Required output:

- Commands run, with pass/fail.
- Commands not run and why.
- Manual checks performed.
- External calls avoided or performed intentionally.
- Residual risk.

Verifier must use `VALIDATION.md` and update it when validation knowledge
changes.

### Committer

Purpose: package the change for version control.

Current workspace note: git **is** initialized (branch `main`). Committer creates
one focused commit per feature/change. Before committing, run `git status` and
confirm no secret/runtime files are staged (they should be `.gitignore`d).
End commit messages with a `Co-Authored-By:` line when AI-authored.

**Hardcoded-secret caveat:** `checker.py` still carries hardcoded token/proxy
material. `login.py` was cleaned (proxy is now optional via `BROWSER_PROXY_URL`,
no secrets), but the **initial commit** `e0c2cd3` still contains its old hardcoded
proxy line in history. Scrub + rotate before adding any git remote or pushing —
the repo is local-only until then.

Required output:

- Final diff summary.
- Validation summary.
- Commit message (subject + body).
- Confirmation that no secret/runtime files were committed:
  `.env`, `.env_flow`, `api_config.json`, `labs.google.har`, `google_profile/`,
  proxy lists, `tools/*_capture.json`, logs, runtime state files, generated images.

## Task Handoff Template

Use this block at every role boundary:

```md
## Role Handoff

Task:
Role completed:
Files touched:
Assumptions:
Validation:
Risks:
Next role:
```

## Danger Zones

- `api_config.json` currently contains live-looking bearer/cookie material and
  is not excluded by `.gitignore`.
- `.env_flow` contains token/captcha/proxy configuration and should never be
  printed or committed.
- `gemini_bot.py` and `checker.py` contain hardcoded token/proxy configuration.
  (`login.py` was de-proxied/cleaned — no hardcoded secrets there anymore.)
- `labs.google.har` is a captured browser archive and may contain tokens,
  cookies, request bodies, and headers.
- `google_profile/` contains browser profile databases, cookies, account state,
  caches, and IndexedDB data.
- `bot.js` exposes server/admin commands through Telegram and can run shell
  commands via `exec`.
- `login.py` deletes `google_profile/` before recreating it.
- `test_recaptcha_params.py` can spend 2Captcha balance.
- `test_tokens.py` and bot scripts can call external APIs and generate media.
