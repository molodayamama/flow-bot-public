# Flow Quota Profiler

RL-001A is an offline dry-run foundation. It does not open a browser, use a
Chrome profile, contact Google Flow, call Telegram, use captcha solving, read
env/config files, use proxies, or generate media. It does not measure real Flow
limits.

RL-001B adds `single` mode: one approval-required, headed browser smoke attempt
through the Google Labs Flow UI using one explicitly supplied existing browser
profile directory.

RL-001C adds `browser-check` mode: approval-required local Playwright/Chromium
launch diagnostics only. It never opens Google Flow, never generates media, and
uses only clean temporary profiles unless one existing `--user-data-dir` is
explicitly provided.

## Supported CLI

```bash
python flow_quota_profiler.py --mode dry-run --prompt-id test-001 --prompt "simple safe prompt"
python flow_quota_profiler.py --mode dry-run --prompt-file prompts.txt
python flow_quota_profiler.py --mode single --prompt-id smoke-001 --prompt "simple safe landscape test" --user-data-dir ./google_profile --approve-external-action
python flow_quota_profiler.py --mode browser-check --approve-external-action
python flow_quota_profiler.py --mode browser-check --user-data-dir ./google_profile --approve-external-action
```

Supported modes:

- `dry-run`: fully offline planning only.
- `single`: exactly one UI-only smoke attempt. It requires
  `--approve-external-action`, `--prompt`, and exactly one existing
  `--user-data-dir`. It rejects `--prompt-file`, repeated profile directory
  flags, path-list values, missing paths, files, parent traversal, delay,
  multiple requested outputs, and multiple generations.
- `browser-check`: local launch diagnostics only. It requires
  `--approve-external-action`, rejects prompt inputs, delay, multiple requested
  outputs, and multiple generations, and accepts zero or one existing
  `--user-data-dir`. When omitted, no existing profile path is resolved or
  touched.

`batch` and `ramp` remain rejected.

## Outputs

Each run writes:

- `flow_profiler_runs/<run_id>/events.jsonl`
- `flow_profiler_runs/<run_id>/summary.json`
- `flow_profiler_runs/.flow_profiler.lock` while the run is active

The prompt text is treated as sensitive input. Events store only prompt ID,
SHA-256 hash, and length. `single` mode also treats the browser profile path as
sensitive output data; it is used for browser launch only and is not written to
events, summaries, stdout, or stderr.

`browser-check` writes diagnostic events only. Diagnostic messages are redacted
before persistence, and output fields are allowlisted to avoid profile paths,
browser executable paths, command lines, raw logs, cookies, tokens, storage
data, URLs, screenshots, HARs, DOM dumps, generated media, or prompt text.

## Single Mode Safety

`single` mode:

- imports Playwright only inside the browser execution function;
- launches a headed persistent browser with the provided profile directory;
- does not use proxy settings, captcha solving, Telegram, direct Flow API calls,
  request bodies, response bodies, storage reads, browser archive capture, or
  screenshots;
- stops on login/account/challenge/captcha/human-verification, account warning,
  quota, rate-limit, access, unexpected redirect, timeout, and unavailable
  generation signals;
- writes one event and one summary, then releases the profiler lock.

## Browser Check Safety

`browser-check` runs these local checks in order:

- Playwright import.
- Playwright runtime manager startup.
- Bundled Chromium launch with a clean temporary non-persistent context.
- Persistent Chromium context launch with a clean temporary profile.
- Persistent Chromium context launch with the provided existing profile, only
  when `--user-data-dir` is supplied with approval.

The page target is `about:blank`. The mode does not navigate to Google Flow or
any external URL, does not read profile files, cookies, storage, IndexedDB, HAR
content, or environment/config secrets, and does not call Telegram, proxy,
captcha, direct Flow APIs, or media generation. Existing profile diagnostics use
headless launch settings to reduce local UI exposure, so they are safer than
`single` mode but not identical to it.

## Deferred Phases

Later work may add controlled batch, ramp profiling, Telegram reporting, and
CSV export, each as its own reviewed task.

## Validation

Use offline validation first:

```bash
python -m py_compile flow_quota_profiler.py flow_profiler/config.py flow_profiler/planner.py flow_profiler/runner.py flow_profiler/writers.py flow_profiler/report.py flow_profiler/safety.py flow_profiler/lock.py flow_profiler/browser_single.py flow_profiler/browser_check.py
python -m unittest discover -s tests -p "test_flow_profiler_*.py"
rg -l "<safe secret patterns from VALIDATION.md>" flow_profiler flow_quota_profiler.py tests docs
```

The file-only scan should produce no output for profiler files. Manual
`single` smoke validation is stateful and external; run it only with explicit
operator approval after offline checks pass. Manual `browser-check` validation
also launches local browsers and may mutate an existing profile when
`--user-data-dir` is supplied.
