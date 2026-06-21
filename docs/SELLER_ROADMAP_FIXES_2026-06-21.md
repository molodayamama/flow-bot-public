# Seller Roadmap Fixes 2026-06-21

## Major 7 — platform adaptation

What changed:
- Added a real platform format map: WB/Ozon use `f34` (`portrait_34`, 3:4 1080x1440), Яндекс Маркет uses `sq` (`square`, 1:1 1000x1000).
- Marketplace image jobs and series now set `edit_fmt` from the selected platform and `mp:create` derives generation aspect from that platform.
- Photo request, confirmation, platform picker labels, prompts, series prompts, and export filenames now show/use the real platform format.
- Added tasteful platform-specific prompt guidance for WB, Ozon, and Яндекс Маркет.

Files:
- `flow_bot.py`
- `tests/test_seller_bot.py`
- `docs/SELLER_ROADMAP_FIXES_2026-06-21.md`

Verified:
- `ENV_FILE=.env.example python -m py_compile flow_bot.py tests/test_seller_bot.py` — pass.
- `ENV_FILE=.env.example python -m unittest discover -s tests -p "test_*.py"` — pass, 601 tests.

Role handoff:
- Task: Major 7 platform adaptation.
- Role completed: Architect, Reviewer, Implementer, Verifier.
- Files touched: `flow_bot.py`, `tests/test_seller_bot.py`, `docs/SELLER_ROADMAP_FIXES_2026-06-21.md`.
- Assumptions: Яндекс Маркет image cards should use square output; video aspect remains unchanged because the current video UI/API only supports landscape/portrait.
- Validation: full offline suite and syntax checks passed.
- Risks: live generated composition quality still depends on the backend model following the new guidance.
- Next role: Committer.

## Major 9 — SKU/projects workspace

What changed:
- Added explicit seller SKU projects in metrics so a SKU can exist before any card is saved.
- The projects screen now has clickable SKU buttons plus a direct `➕ Новый SKU` action.
- Opening a SKU shows slide count, platform, latest prompt, and actions to add the current/latest card, rename, delete, or return to all SKUs.
- Existing result-toolbar SKU saving still works, and SKU rename/delete updates both project metadata and saved slide rows.
- Seller analytics now count explicit SKU projects as well as legacy item-only SKU rows.

Files:
- `metrics.py`
- `flow_bot.py`
- `tests/test_metrics.py`
- `tests/test_seller_bot.py`
- `docs/SELLER_ROADMAP_FIXES_2026-06-21.md`

Verified:
- `ENV_FILE=.env.example python -m py_compile flow_bot.py metrics.py tests/test_seller_bot.py tests/test_metrics.py` — pass.
- `ENV_FILE=.env.example python -m unittest discover -s tests -p "test_*.py"` — pass, 603 tests.

Role handoff:
- Task: Major 9 SKU/projects workspace.
- Role completed: Architect, Reviewer, Implementer, Verifier.
- Files touched: `metrics.py`, `flow_bot.py`, `tests/test_metrics.py`, `tests/test_seller_bot.py`, `docs/SELLER_ROADMAP_FIXES_2026-06-21.md`.
- Assumptions: empty SKU projects should be first-class rows instead of fake slide rows; export/series-building from a SKU remains outside this task.
- Validation: full offline suite and syntax checks passed.
- Risks: Telegram file ids for latest gallery items are reused as saved SKU slide refs; if the latest gallery item is stale in Telegram, add-last may need the user to use the result toolbar instead.
- Next role: Committer.

## Major 10 — seller history vs gallery

What changed:
- Seller `Мои запросы` now renders recent jobs from `flow_jobs` instead of consumer-only `prompt_history`.
- Seller job history pairs each job with the latest marketplace `mp_job` event to show platform/job context, status, and credit cost/refund.
- If detailed job rows are unavailable but gallery entries exist, history now shows a gallery-backed fallback instead of claiming the history is empty.
- Consumer prompt history behavior is unchanged.

Files:
- `metrics.py`
- `flow_bot.py`
- `flow_copy.py`
- `tests/test_metrics.py`
- `tests/test_seller_bot.py`
- `docs/SELLER_ROADMAP_FIXES_2026-06-21.md`

Verified:
- `ENV_FILE=.env.example python -m py_compile flow_bot.py flow_copy.py metrics.py tests/test_seller_bot.py tests/test_metrics.py` — pass.
- `ENV_FILE=.env.example python -m unittest discover -s tests -p "test_*.py"` — pass, 606 tests.

Role handoff:
- Task: Major 10 Gallery vs History reconciliation.
- Role completed: Architect, Reviewer, Implementer, Verifier.
- Files touched: `metrics.py`, `flow_bot.py`, `flow_copy.py`, `tests/test_metrics.py`, `tests/test_seller_bot.py`, `docs/SELLER_ROADMAP_FIXES_2026-06-21.md`.
- Assumptions: exact original prompt text is not available in `flow_jobs`, so this task shows actual job metadata and uses gallery prompt snippets only for fallback.
- Validation: full offline suite and syntax checks passed.
- Risks: platform/job pairing is based on the nearest prior `mp_job` event; concurrent seller jobs are already constrained by user-level generation locking.
- Next role: Committer.

## Major 12 — stale marketplace buttons

What changed:
- Added active marketplace message stamping in `wizard_state` via `mp_active_msg_id`.
- `mp:*` callbacks now reject clicks from older marketplace screens with “Это старый экран — открой актуальное меню”.
- The guard attempts to remove the stale inline keyboard so old buttons stop looking live.
- The marketplace hub, SKU projects screen, and result-toolbar SKU choice message all stamp the active message id, so normal current-screen and SKU-add flows keep working.

Files:
- `flow_bot.py`
- `tests/test_seller_bot.py`
- `docs/SELLER_ROADMAP_FIXES_2026-06-21.md`

Verified:
- `ENV_FILE=.env.example python -m py_compile flow_bot.py tests/test_seller_bot.py` — pass.
- `ENV_FILE=.env.example python -m unittest discover -s tests -p "test_*.py"` — pass, 608 tests.

Role handoff:
- Task: Major 12 stale-button protection.
- Role completed: Architect, Reviewer, Implementer, Verifier.
- Files touched: `flow_bot.py`, `tests/test_seller_bot.py`, `docs/SELLER_ROADMAP_FIXES_2026-06-21.md`.
- Assumptions: rejecting by Telegram `message_id` is sufficient for the seller marketplace wizard because these screens are single-message inline flows.
- Validation: full offline suite and syntax checks passed.
- Risks: if Telegram does not allow editing an old message markup, the callback is still rejected but the old keyboard may remain visible client-side.
- Next role: Committer.

## Major 13 — payment copy

What changed:
- Normal top-up keyboards no longer show test packs to anyone, including admins.
- Test packs are available only when the explicit `TOPUP_TEST_PACKS_ENABLED`/`PAYMENT_TEST_PACKS_ENABLED` flag is enabled for an admin session.
- Stars and СБП/карта pack labels now show credits, approximate card/video capacity, and the plain price without percentage value badges.
- Top-up screens now explain what credits buy: image generation from the current image price and video generation from the cheapest video price.
- Payment copy stays provider-neutral and avoids backend/captcha wording.

Files:
- `flow_bot.py`
- `flow_copy.py`
- `tests/test_flow_menu.py`
- `docs/SELLER_ROADMAP_FIXES_2026-06-21.md`
- `HANDOFF.md`

Verified:
- `ENV_FILE=.env.example python -m py_compile flow_bot.py flow_copy.py tests\test_flow_menu.py` — pass.
- `ENV_FILE=.env.example python -m unittest discover -s tests -p "test_*.py"` — pass, 608 tests.

Role handoff:
- Task: Major 13 payment copy.
- Role completed: Architect, Reviewer, Implementer, Verifier.
- Files touched: `flow_bot.py`, `flow_copy.py`, `tests/test_flow_menu.py`, `docs/SELLER_ROADMAP_FIXES_2026-06-21.md`, `HANDOFF.md`.
- Assumptions: the explicit test-pack flag is an admin/dev path and should default off in live seller UI.
- Validation: full offline suite and syntax checks passed.
- Risks: live pack availability depends on deploy-time env/config, but the default code path hides test packs.
- Next role: Committer.
