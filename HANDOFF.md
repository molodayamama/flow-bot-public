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

## Publication closeout

The operator explicitly authorized uploading without prior credential rotation.
Both remote branches were atomically replaced with the reviewed root commit;
a fresh GitHub clone contained one commit and passed the secret scan. Both
GitHub CI runs succeeded. Original local history/backups remain private.

## Role Handoff

Task: Ignore seller.env and config_override.json.
Role completed: Architect
Files touched: HANDOFF.md; planned .gitignore only.
Assumptions: Work in the published clean repository; keep main and refactor aligned.
Validation: Clean worktree; both remote branches match the published root.
Use git check-ignore, tracked-file audit and diff checks from VALIDATION.md.
Risks: Low; no API, payment, browser, runtime or business-logic changes. Existing
requirements.txt remains applicable. Preserve UTF-8 and existing local files.
Rollback: Revert the two ignore entries; no runtime migration or deletion.
Next role: Reviewer

## Role Handoff

Task: Review the two missing ignore entries.
Role completed: Reviewer
Files touched: HANDOFF.md
Assumptions: Match the exact filenames; keep env templates tracked.
Validation: approved. No blockers or unrelated refactors. Verify neither runtime
file is tracked and both example env files remain available to Git.
Risks: Ignore rules cannot protect secrets embedded in tracked source files.
Next role: Implementer

## Role Handoff

Task: Add missing runtime ignore rules.
Role completed: Implementer
Files touched: .gitignore, HANDOFF.md
Assumptions: Exact filename rules are sufficient for this requested fix.
Validation: Added seller.env and config_override.json; no deviations.
Risks: No existing files changed or deleted beyond the two documentation files.
Next role: Verifier

## Role Handoff

Task: Verify ignore behavior.
Role completed: Verifier
Files touched: HANDOFF.md
Assumptions: No full test suite is needed for two ignore entries.
Validation: PASS git check-ignore for both runtime paths; PASS both are untracked;
PASS .env.example and deploy/examples/seller.env.example remain unignored;
PASS python tools/check_tracked_secrets.py; PASS git diff --check.
Used VALIDATION.md; no new validation procedure required. No external runtime calls.
Risks: None introduced to application behavior.
Next role: Committer

## Role Handoff

Task: Package the ignore fix for main and refactor.
Role completed: Committer
Files touched: .gitignore, HANDOFF.md
Assumptions: Publish one normal fast-forward commit to both clean branches.
Validation: Reviewed diff contains two ignore entries and task notes only;
stage exactly these two paths, repeat the staged diff check, then push atomically.
Commit message: Ignore seller environment and runtime config overrides
Body: Exclude seller.env and config_override.json while preserving env templates.
Co-Authored-By: Codex <noreply@openai.com>
Risks: No secret/runtime files, env values, profiles, captures or media included.
Next role: Verify remote branch tips after upload.

## History restoration — supersedes the earlier squash procedure

The operator needs the development history for reviewing changes. The initial
publication squash was the wrong choice. Restore all 538 original refactor
commits (including old main ancestry), with sensitive content removed, and
retain both subsequent publication commits. Commit hashes necessarily change.

## Role Handoff
Task: Restore sanitized development history without losing the current source.
Role completed: Architect
Files touched: Isolated rewritten repository, HANDOFF.md, VALIDATION.md.
Assumptions: Preserve commit chronology/topology, including empty commits, and
the already published README and ignore fixes. Work solo. Prior explicit
authorization for shared-history rewriting without rotation remains applicable.
Validation: Verified backup contains 538 refactor ancestors; current public
source is retained separately. Use git-filter-repo, Gitleaks and object checks.
Risks: High-impact ref rewrite; original runtime, profiles and backups untouched.
Rollback: Retain the current clean publication checkout until remote verification.
Next role: Reviewer

## Role Handoff
Task: Review preservation and privacy requirements.
Role completed: Reviewer
Files touched: HANDOFF.md
Assumptions: Removing secrets must not remove the development chronology.
Validation: approved with notes. Preserve all original commits with pruning
disabled. Remove private operational journals/account instructions from historic
snapshots and redact remaining credentials, personal contacts and Git emails.
Do not merge raw backup refs; reattach current publication commits after cleanup.
Risks: Historical private-file diffs cannot be retained alongside their secrets.
Next role: Implementer

## Role Handoff
Task: Rewrite historical content and reattach publication work.
Role completed: Implementer
Files touched: All affected historical blobs/metadata; HANDOFF.md, VALIDATION.md.
Assumptions: SHA changes are required; dates, commit order and parent relationships
remain useful. Restore current application files exactly from approved publication.
Validation: All 538 old commits map one-to-one to 538 rewritten commits. Both
publication commits replayed with original messages/dates. Resulting source tree
is byte-for-byte identical to the previous published tip before these notes.
Risks: No runtime, payment, browser or business-logic changes. No plan deviations.
Next role: Verifier

## Role Handoff
Task: Verify restored history and current source.
Role completed: Verifier
Files touched: HANDOFF.md, VALIDATION.md; private mapping/scan reports outside Git.
Assumptions: Previous successful unit tests/CI remain applicable because the
application tree is unchanged; run the history and secret checks again.
Validation: PASS one-to-one mapping of 538 commits; PASS every parent order and
author/committer timestamp; PASS scan of 4089 rewritten Git objects for detected
credentials and personal identifiers; PASS independent Gitleaks history scan.
PASS equality of publication trees after replaying both newer commits. Final
source changes are restoration documentation only. No paid/live API calls.
Risks: Backup copies and GitHub cached objects are separate from rewritten refs.
Next role: Committer

## Role Handoff
Task: Publish the restored history on main and refactor.
Role completed: Committer
Files touched: HANDOFF.md and VALIDATION.md in the final documentation commit.
Assumptions: Both branches receive the same verified history through an atomic
push guarded by explicit expected remote SHAs. Keep private visibility.
Validation: Stage only restoration documentation; inspect staged paths and run
diff/secret checks before commit. Re-run full-history scanning before push,
then verify a fresh GitHub clone, branch tips and both CI runs.
Commit message: Restore sanitized development history and preserve future commits
Co-Authored-By: Codex <noreply@openai.com>
Risks: No real env, tokens, Gmail credentials, profiles, databases, payment state,
captures, logs or private runtime media are introduced. Public site assets remain.
Next role: Verify remote publication and synchronize the clean working copy.

## New independent public repository — 2026-09-09

## Role Handoff
Task: Publish sanitized history in a new independent GitHub repository.
Role completed: Architect
Files touched: HANDOFF.md; planned README.md and VALIDATION.md.
Assumptions: Operator accepted creating a new repository for public access.
Preserve all 541 existing commits and current application behavior. Keep the
original repository private; publish only main, without creating a fork.
Validation: Clean working copy; remote main unchanged; destination name available.
Re-run current-file and full-history secret scans; verify old unfiltered SHA
is unavailable in the new repository before and after making it public.
Risks: Public disclosure is consequential. Create private first, upload only
clean main, inspect server state, then change visibility. No runtime deployment.
Rollback: Original private repository/backups remain unchanged; a failed
publication gate leaves the new repository private.
Next role: Reviewer

## Role Handoff
Task: Review publication boundaries.
Role completed: Reviewer
Files touched: HANDOFF.md
Assumptions: A new independent repository must not inherit the old object network.
Validation: approved with notes. Preserve dates/topology; no squash or raw backup
refs. Update clone instructions, use explicit main-only push, confirm fork=false,
and verify current/old commit access without printing sensitive file contents.
Risks: Do not change the original repository's visibility or delete local state.
Next role: Implementer

## Role Handoff
Task: Prepare independent publication.
Role completed: Implementer
Files touched: README.md, VALIDATION.md, HANDOFF.md.
Assumptions: New repository name is flow-bot-public. HTTPS clone requires no SSH setup.
Validation: Updated clone URL and directory; documented independent-repository
verification. No application, dependency, model, payment or runtime changes.
Risks: No secrets or local runtime data added. No deviations from reviewed scope.
Next role: Verifier

## Role Handoff
Task: Validate publication source.
Role completed: Verifier
Files touched: HANDOFF.md; redacted scanner reports outside the repository.
Assumptions: Existing application tests remain applicable for documentation-only
changes; target repository CI will independently validate the uploaded revision.
Validation: PASS python tools/check_tracked_secrets.py; PASS git diff --check;
PASS complete main-history Gitleaks scan with reviewed public-fixture exceptions;
PASS count of 541 preserved commits. No live provider, payment or browser calls.
Risks: Repository-specific old-object access and anonymous checks follow upload.
Next role: Committer

## Role Handoff
Task: Commit documentation and publish clean main to the independent repository.
Role completed: Committer
Files touched: README.md, VALIDATION.md, HANDOFF.md only.
Assumptions: Create private, explicitly push main, validate, then enable public
access as requested. Keep original flow-bot private and retain its history.
Validation: Inspect exactly three staged documentation paths and staged diff;
repeat the source scan. After upload verify branch tips, no old raw commit,
fork=false, CI, then public anonymous access and fresh-clone history.
Commit message: Prepare independent public repository and clone instructions
Co-Authored-By: Codex <noreply@openai.com>
Risks: No real env, credentials, Gmail account data, profiles, HAR, payment state,
logs, captures, databases or runtime media included. Preserve public site assets.
Next role: Verify actual GitHub publication and record the observed result.

## Additional pre-publication verification

A targeted inspection of the initial login helper found a historical proxy
endpoint without a password; standard credential scanners did not flag it.
Extended all-history inspection found four infrastructure IP values in old
login/checker code and an admin input placeholder. These were replaced with a
documentation address, and the placeholder now uses generic example credentials.
All 542 commits, dates and parent relationships are retained. Current changes
are limited to the admin placeholder and corresponding proxy test data; no
runtime behavior or user data changed. Nine static-page tests and seventeen
proxy-supervisor tests pass, as do the tracked-file and Gitleaks history audits.

The initial private staging upload must not be made public, since it received
those historical endpoint objects. Preserve it privately under a staging name
and create the final repository independently again. Require a different GitHub
repository ID and verify both the original raw commits and staging-upload SHA
are unavailable there before public access. The original flow-bot stays private.

Final commit subject: Remove remaining historical proxy endpoints before publication
Co-Authored-By: Codex <noreply@openai.com>
