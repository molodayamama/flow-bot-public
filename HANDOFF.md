# Publication preparation — 2026-09-06

## Role Handoff
Task: Publish the latest refactor snapshot as main without historical production data.
Role completed: Architect
Files touched: HANDOFF.md; planned README.md, .gitignore, documentation/configuration containing private data, secret audit and its tests.
Assumptions: A new root commit is appropriate because the operator requests removal of extensive sensitive history. Use the latest origin/refactor, including four remote fixes. Preserve runtime files and original worktrees. Keep the repository private until publication is verified.
Validation: requirements.txt and CI exist; Python 3.11 offline commands come from VALIDATION.md. Git bundle created and verified outside the publish tree. UTF-8 must be preserved. Scan the complete publication tree and all new Git objects, including commit metadata.
Risks: High-impact Git history replacement; credentials need rotation. No payment, provider, browser profile, admin endpoint or production runtime calls are needed. Old clones/backups and GitHub cached objects require separate handling.
Next role: Reviewer

## Role Handoff
Task: Review publication plan.
Role completed: Reviewer
Files touched: HANDOFF.md
Assumptions: Both remote branches must reference clean history; leaving refactor unchanged would retain secrets.
Validation: approved with notes. No business logic refactors. Review every changed source file; remove private operational notes from the publication snapshot while preserving originals outside it. Run offline security tests and scan all committed objects. Push both branches atomically with explicit expected-SHA leases only after rotation confirmation.
Risks: Remote rewrite is blocked pending credential-rotation confirmation required by AGENTS.md. A clean root does not purge cached GitHub views or other clones. Rollback source is the verified private bundle; never push its old history after cleanup.
Next role: Implementer

## Role Handoff
Task: Sanitize the publication snapshot and document setup.
Role completed: Implementer
Files touched: README.md, HANDOFF.md, VALIDATION.md, .gitignore, .gitleaks.toml,
.env.example, docs/ENV_SETUP.md, docs/ACCOUNT_ONBOARDING.md, public HTML contact
fields, tests/test_flow_menu.py, tests/test_logging_redaction.py,
tools/check_tracked_secrets.py, tests/test_tracked_secret_audit.py.
Private memory and working-plan files are excluded from the publication only.
Assumptions: Public project branding/domains and provider site keys are not
credentials; preserve them. Preserve intentionally public landing media required
by the application, while excluding user-generated runtime media.
Validation: Latest remote source exported; private history bundle verified.
Independent Gitleaks findings reviewed as public site key and RFC test vector;
exceptions are restricted by both path and exact value. Built-in audit extended
to HTML/templates/fixtures and private account identifiers. Ten audit tests pass.
Risks: No business logic changed; contact/requisite examples need customization
before deployment. Operator confirmed credentials have NOT been rotated.
Next role: Verifier

## Role Handoff
Task: Verify the clean publication snapshot.
Role completed: Verifier
Files touched: HANDOFF.md, VALIDATION.md
Assumptions: Existing installed Python dependencies are used; no installation or
production deployment is performed. Current local aiohttp is 3.13.5, older than
the requirements security floor; CI must validate with requirements installed.
Validation: PASS full offline unittest suite (1596 tests, 1 skipped); PASS final
secret-audit suite (10 tests); PASS final log-redaction suite (7 tests); PASS
py_compile for entrypoints and the audit. PASS built-in staged-file audit and
Gitleaks directory scan with two exact/path-scoped public-fixture exceptions.
README relative links resolve. Exact matching against local env credentials
finds only the public reCAPTCHA site key. Original executable modes preserved.
Diff reviewed against exported latest refactor snapshot: application Python
business logic unchanged; changes are documentation, private-data sanitization,
audit tooling and test fixtures. Original text newline conventions preserved.
Risks: No live generation, payments, browser profiles or remote runtime touched.
One existing test skipped. The old local repository and verified private bundle
retain old history intentionally and must never be pushed into clean history.
Next role: Committer

## Role Handoff
Task: Package the publication as a new root shared by main and refactor.
Role completed: Committer
Files touched: Clean publication Git index and refs; HANDOFF.md
Assumptions: Both remote branches still require a coordinated, atomic leased push.
Validation: Staged paths inspected; no real env, API config, profile, HAR, proxy
list, log, database, account/payment state, capture or user-generated runtime
media included. Existing public website assets are intentionally preserved.
Commit message: Publish sanitized Flow Bot snapshot and setup guide
Body: Start main and refactor from the latest refactor code without historical
production data. Document setup and strengthen publication secret checks.
Co-Authored-By: Codex <noreply@openai.com>
Risks: Remote push NOT performed: operator says credentials are not rotated,
so the AGENTS.md shared-history rewrite gate is not satisfied. Repository
visibility remains private. GitHub caches and other clones are not purged.
Next role: Operator — rotate affected credentials or explicitly override the
repository rotation prerequisite, then complete the reviewed remote rewrite.
