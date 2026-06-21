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
