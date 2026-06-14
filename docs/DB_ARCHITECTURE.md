# DB Architecture — User Data on SQLite

Status: design (for implementation agent). Owner: solo operator (@photozhab_bot).
Scope: the SQLite source of truth for all user-level data and the per-user /
operator analytics derived from it.

This document is concrete enough to implement directly. The DDL and the Python
API surface below are the contract — build them as written.

---

## 0. Goal + assumptions (state the unknowns)

**Goal.** A single SQLite source of truth that can answer 8 per-user business
questions and the operator rollups derived from them, without adding any external
service and without breaking the existing `metrics.py` contract (single
connection, `threading.Lock`, never raise into caller).

**Assumptions (verified against the current tree on 2026-06-14):**

- `metrics.py` already owns one SQLite file (default `metrics.db`, env
  `METRICS_DB`) with these tables: `events`, `flow_jobs`, `transactions`,
  `referrals`, `referral_ongoing_rewards`, `acquisitions`, **and `credits`**.
  Credits are *already* migrated to SQLite — `flow_core.CreditStoreSQLite`
  delegates to `metrics.credits_*`. The JSON `CreditStore` still exists as a
  fallback / legacy path.
- `transactions.amount_rub` is already populated by the payment handler using
  `STARS_TO_RUB` (default 1.3, env `STARS_TO_RUB`, lives in `flow_bot.py`). So
  "money in RUB" is a stored column, not something we recompute.
- First-touch acquisition channel is already captured in `acquisitions`
  (`user_id UNIQUE`, `channel`) from `seed_<channel>` deep links.
- Referral graph + rewards already live in `referrals` /
  `referral_ongoing_rewards`.

**The one real gap:** there is **no `users` profile table**. `telegram_id`,
`first_seen`, `last_active`, and a `username` snapshot are only derivable today by
scanning the PII-retention-limited `events` table (purged after 90 days). That is
the table this design adds. Everything else is aggregation over tables that
already exist.

**Unknowns / decisions deferred to the operator:**

- Is `STARS_TO_RUB` ever going to vary per pack or over time? Today it is a single
  constant baked into `amount_rub` at write time. We keep that (historical RUB is
  frozen at the rate when paid — correct for accounting). Flagged, not changed.
- "Requests by type" granularity: we map to `flow_jobs.operation_type`. The exact
  set of operation_type strings the bot writes is the authority; this doc uses
  `image / video / edit / upscale` as the canonical buckets and a CASE map.

---

## 1. Schema decision: extend `metrics.db` (Option A). Recommended.

**Decision: Option A — add a `users` table to the existing `metrics.db`. Do not
create a separate `user_data.db`.**

Reasoning:

1. **Credits, transactions, referrals, and acquisitions are already in
   `metrics.db`.** Six of the eight business questions are answered today by
   `JOIN`/aggregate over those tables. A separate `user_data.db` would force
   cross-database `ATTACH` (or app-side join) for *every* per-user report — the
   referral-RUB question alone needs `referrals` + `transactions` + the new
   `users` row together. Splitting the file buys nothing and costs every join.
2. **One connection, one lock, one contract.** `metrics.py` is built around a
   single module-level connection guarded by `_LOCK`. A second DB means a second
   connection + second lock + a second init/retention lifecycle, doubling the
   surface where the never-raise contract can be violated. For a solo-operator bot
   this is pure downside.
3. **SQLite is a single-writer file anyway.** There is no scaling win from two
   files in one process; you do not get more write throughput, you just get two
   things to back up and keep consistent.
4. **Migration is trivial.** Adding a table is `CREATE TABLE IF NOT EXISTS` in the
   existing `_SCHEMA` string — it runs on every startup, already idempotent.

When Option B (separate DB) *would* be right — and why it is not now: if user PII
needed a different retention/backup/encryption policy than financial data, or if a
separate process owned user data. Neither is true here. The existing design
already isolates PII concern at the *column* level (`events.username` is purged;
financial tables are not). We follow the same pattern: the new `users` table holds
a username snapshot and is subject to the same retention reasoning (see §6).

> Note on the task framing: the brief says "credits currently in
> `user_credits.json` must migrate here." That migration **already exists** as
> `metrics.credits_migrate_from_json()` and the `credits` table. This design does
> not re-do it; it documents how to *finish* it safely (§4) and adds only the
> missing `users` table.

---

## 2. Full DDL

Add the following to the `_SCHEMA` string in `metrics.py` (after the `credits`
table). It is `IF NOT EXISTS`, so it runs safely on every `init_db()`.

```sql
-- ── users: profile + denormalized snapshot for fast per-user reports ──────
-- One row per Telegram user. This is the profile/identity table. Money, credits,
-- requests, and referrals live in their own tables and are JOINed/aggregated;
-- we do NOT duplicate balances here (credits table is the single source of truth
-- for balance). The denormalized counters below (req_* ) are OPTIONAL fast-path
-- caches — see §3 "counter policy". If you do not want cache-invalidation risk,
-- leave them at 0 and always compute from flow_jobs. They are included so the
-- operator's "top users" screen does not scan flow_jobs every render.
CREATE TABLE IF NOT EXISTS users (
    user_id        INTEGER PRIMARY KEY,            -- Telegram user id (stable, unique)
    username       TEXT,                           -- latest @username snapshot (no leading @); nullable
    first_name     TEXT,                           -- latest first_name snapshot; nullable, PII
    first_seen     TEXT DEFAULT (datetime('now')), -- UTC, first /start or first interaction
    last_active    TEXT DEFAULT (datetime('now')), -- UTC, updated on every interaction (DAU/WAU/MAU)
    acq_channel    TEXT,                           -- first-touch channel mirror (see note); nullable
    is_blocked     INTEGER NOT NULL DEFAULT 0,     -- 1 if user blocked the bot (set from TelegramForbidden)
    updated_at     TEXT DEFAULT (datetime('now'))  -- UTC, any profile field change
);

CREATE INDEX IF NOT EXISTS idx_users_last_active ON users(last_active);
CREATE INDEX IF NOT EXISTS idx_users_first_seen  ON users(first_seen);
CREATE INDEX IF NOT EXISTS idx_users_channel     ON users(acq_channel);
```

Notes on specific columns:

- **No `balance` column in `users`.** Balance stays in `credits` (already the
  source of truth, already concurrency-safe via the `WHERE balance>=?` charge
  pattern). Duplicating it invites drift. Per-user reports `JOIN credits`.
- **`acq_channel` is a convenience mirror of `acquisitions.channel`.** The
  authoritative first-touch record stays in `acquisitions` (it has the
  `UNIQUE(user_id)` first-touch guarantee). The mirror exists only so a single
  `SELECT * FROM users` row can answer the profile screen without a join. The
  upsert that writes it must use first-touch semantics (write only if currently
  NULL) to match `acquisitions`. If you prefer zero redundancy, drop this column
  and always `LEFT JOIN acquisitions` — both are acceptable; the column is the
  faster default for a solo operator.
- **Starter-grant tracking is NOT duplicated here.** `credits.granted` (0/1) is
  already the idempotent flag for "was the starter bonus given". Question 8 reads
  `credits.granted`. Do not add a second flag to `users`.
- **`is_blocked`** is new and cheap: when a send raises `TelegramForbidden`
  (user blocked the bot), set it. It makes DAU/retention honest and lets the
  operator exclude dead users. Optional but recommended.

No changes to existing tables are required. All eight questions are answerable
with the existing tables + this one new table.

---

## 3. API surface (functions to add to `metrics.py`)

Follow the existing module contract for every function: acquire `_LOCK`, use
`_conn()`, wrap in `try/except`, log at WARNING, never raise into the caller,
return a safe default. Add new names to `__all__`.

### 3.1 Profile writers

```python
def upsert_user(
    user_id: int,
    *,
    username: str | None = None,
    first_name: str | None = None,
    channel: str | None = None,
) -> None:
    """Insert the user on first sight, else refresh snapshot + last_active.

    Idempotent and safe to call on EVERY update (cheap single UPSERT). On first
    insert sets first_seen=last_active=now and acq_channel=channel (first-touch).
    On conflict: updates username/first_name if non-None, always bumps
    last_active and updated_at, and sets acq_channel only if it is still NULL
    (first-touch wins, matching acquisitions). Never raises.

    Call site: at the top of the message/callback handler, once per interaction.
    This is the single source of last_active for DAU/WAU/MAU.
    """

def touch_user(user_id: int) -> None:
    """Bump only last_active=now for an existing user (lighter than upsert).

    Use when you have no fresh username/first_name to record. No-op if the user
    row does not exist yet (upsert_user creates it). Never raises.
    """

def mark_user_blocked(user_id: int, blocked: bool = True) -> None:
    """Set users.is_blocked. Call when a send raises TelegramForbidden (blocked)
    or succeeds again (blocked=False). Never raises. No-op if no row.
    """
```

### 3.2 Per-user read (the profile screen / admin `/user <id>`)

```python
def get_user_profile(user_id: int) -> dict:
    """Full per-user dossier answering business questions 1-8 for ONE user.

    Returns a dict (zeros/None on missing data, never raises):
        {
          "user_id": int,
          "username": str | None,
          "first_name": str | None,
          "first_seen": str | None,           # UTC text
          "last_active": str | None,          # UTC text
          "is_blocked": bool,
          "balance": int,                     # from credits
          "starter_granted": bool,            # credits.granted == 1  (Q8)
          "acq_channel": str | None,          # from acquisitions (Q7)
          "requests": {                       # from flow_jobs (Q3)
              "image": int, "video": int, "edit": int,
              "upscale": int, "total": int,
          },
          "spend": {                          # from transactions, status='paid' (Q4)
              "stars": int, "rub": float, "payments": int,
          },
          "referrals": {                      # Q5 + Q6
              "invited": int,                 # COUNT referrals.referrer_user_id
              "invited_paid": int,            # invited users who paid >=1
              "referred_revenue_rub": float,  # RUB from this user's invitees
              "referred_revenue_stars": int,
              "reward_credits_earned": int,   # milestone + ongoing
          },
        }

    Implementation: one function, several small queries under a single _LOCK
    (cheaper and simpler than one mega-JOIN; all reads are indexed point/range
    lookups). The operation_type -> bucket map for "requests" is the CASE in §5 Q3.
    """
```

### 3.3 Operator rollups (mostly already exist — add the new ones)

These already exist and need no change: `report_today`, `report_revenue`,
`report_flow`, `report_accounts`, `report_refs`, `report_channels`,
`report_errors`. Add:

```python
def report_active_users(now: bool = False) -> dict:
    """DAU/WAU/MAU from users.last_active (local-day windows).

    Returns {"dau": int, "wau": int, "mau": int}. DAU = users active today
    (local), WAU = last 7 days, MAU = last 30 days. Counts users.is_blocked=0.
    Never raises; returns zeros on error.
    """

def report_top_users(limit: int = 20, days: int | None = None) -> dict:
    """Top users by RUB spend (optionally restricted to last `days`).

    Returns {"users": [{"user_id", "username", "rub", "stars", "payments"}...]}
    ordered by rub DESC. JOINs users for the username snapshot (LEFT JOIN so a
    payer with no profile row still appears). Never raises; empty list on error.
    """

def report_top_referrers(limit: int = 20) -> dict:
    """Top referrers by referred-revenue (RUB their invitees brought in).

    Returns {"referrers": [{"referrer_user_id", "username", "invited",
    "referred_revenue_rub", "reward_credits_earned"}...]} ordered by
    referred_revenue_rub DESC. This is the money-weighted version of the existing
    report_refs top_referrers (which ranks by raw invite count). Never raises.
    """
```

> `report_channels` already gives channel ROI (users acquired + paid_users +
> revenue per channel). No new function needed for "Channel ROI" — it is shipped.
> Error rates per backend account = existing `report_accounts` + `report_errors`.

### 3.4 Migration helper

```python
def backfill_users_from_metrics() -> int:
    """One-time idempotent backfill of the users table from existing tables.

    For every distinct user_id seen in credits / transactions / acquisitions /
    events, INSERT OR IGNORE a users row with best-effort first_seen
    (= MIN(created_at) across those tables for that user) and the latest known
    username from events. acq_channel is filled from acquisitions. Safe to run
    repeatedly (INSERT OR IGNORE; never overwrites a live row). Returns the count
    of rows inserted. Never raises; returns 0 on error.

    Run once after deploying the users table so historical users appear in
    reports immediately instead of only on their next interaction.
    """
```

---

## 4. Migration plan (idempotent, zero-downtime)

The bot is a single process; "zero-downtime" here means: the new schema must
apply on a normal restart with no manual SQL, no data loss, and a half-applied
state must still be correct.

**Step 0 — credits (already done, verify only).**
`metrics.credits` table and `credits_migrate_from_json` already exist. Confirm the
bot constructs `CreditStoreSQLite` (not the JSON `CreditStore`). Verify:
`python -m unittest discover -s tests -p "test_*.py"` and grep the bot wiring for
which store is instantiated. If the JSON store is still wired, switching to
`CreditStoreSQLite` + calling `credits_migrate_from_json("user_credits.json")`
once at startup is the only credit migration step. Do NOT delete
`user_credits.json` — keep it as a frozen backup.

**Step 1 — ship the `users` DDL.** Add the `CREATE TABLE users` + indexes to
`_SCHEMA`. On next `init_db()` the table appears. No existing query breaks (purely
additive). *Verify:* startup logs clean; `python -m py_compile metrics.py`;
`SELECT name FROM sqlite_master WHERE type='table'` shows `users`.

**Step 2 — wire the writers.** Add a single `upsert_user(...)` call at the top of
the main message + callback handlers (where `user_started` / activity is already
logged). This is the only behavior change in the bot. *Verify:* send one test
message, `SELECT * FROM users WHERE user_id=<you>` shows a row with first_seen and
a moving last_active.

**Step 3 — backfill history.** Call `backfill_users_from_metrics()` once (a
guarded startup call, or an admin command). Idempotent, so a crash mid-backfill
is harmless — re-run. *Verify:* `SELECT COUNT(*) FROM users` is close to
`SELECT COUNT(DISTINCT user_id) FROM transactions UNION credits ...`.

**Step 4 — wire the new reports** (`report_active_users`, `report_top_users`,
`report_top_referrers`, `get_user_profile`) into the admin panel. Read-only; no
data risk. *Verify:* admin command renders without error on a populated DB and on
an empty DB (must return zeros, not crash).

Rollback at any step: the `users` table is additive and unread by the old code
path; reverting the bot binary leaves a harmless extra table. No down-migration
needed.

Ordering rule (per working style): do each step, verify it, then start the next.
Do not wire reports (Step 4) before the writer (Step 2) is confirmed populating
rows, or the reports will look empty and you will chase a phantom bug.

---

## 5. Query examples (the 8 business questions)

All windows use `'localtime'` to match the operator's day, consistent with
existing reports. `?` = bind parameter.

**Q1 — User profile (id, first_seen, last_active, username):**
```sql
SELECT user_id, username, first_name, first_seen, last_active, is_blocked
FROM users WHERE user_id = ?;
```

**Q2 — Credit balance:**
```sql
SELECT balance FROM credits WHERE user_id = ?;   -- NULL/absent => 0 (or starter on first touch)
```

**Q3 — Total requests by type:**
```sql
SELECT
  SUM(CASE WHEN operation_type IN ('gen','regen','revary') THEN 1 ELSE 0 END) AS image,
  SUM(CASE WHEN operation_type LIKE 'video%'               THEN 1 ELSE 0 END) AS video,
  SUM(CASE WHEN operation_type IN ('edit','myphoto')       THEN 1 ELSE 0 END) AS edit,
  SUM(CASE WHEN operation_type IN ('up2x','realup','upscale') THEN 1 ELSE 0 END) AS upscale,
  COUNT(*) AS total
FROM flow_jobs
WHERE user_id = ? AND status = 'success';
-- NOTE: align the operation_type literals with what log_flow_job() actually writes
-- (see flow_core.action_price for the action vocabulary). This is the canonical map.
```

**Q4 — Total money brought in (Stars + RUB):**
```sql
SELECT COALESCE(SUM(stars_amount),0) AS stars,
       COALESCE(SUM(amount_rub),0)   AS rub,
       COUNT(*)                      AS payments
FROM transactions
WHERE user_id = ? AND status = 'paid';
```

**Q5 — Referral count (how many this user invited):**
```sql
SELECT COUNT(*) AS invited
FROM referrals WHERE referrer_user_id = ?;
```

**Q6 — Revenue from this user's referrals (RUB their invitees brought in):**
```sql
SELECT COALESCE(SUM(t.amount_rub),0)   AS referred_revenue_rub,
       COALESCE(SUM(t.stars_amount),0) AS referred_revenue_stars,
       COUNT(DISTINCT t.user_id)       AS invited_paid
FROM referrals r
JOIN transactions t
  ON t.user_id = r.referred_user_id AND t.status = 'paid'
WHERE r.referrer_user_id = ?;
```

**Q7 — Acquisition channel:**
```sql
SELECT channel FROM acquisitions WHERE user_id = ?;   -- first-touch, UNIQUE(user_id)
```

**Q8 — Starter grant given?:**
```sql
SELECT granted FROM credits WHERE user_id = ?;        -- 1 = starter already granted
```

**Operator rollups:**

DAU / WAU / MAU:
```sql
SELECT
  SUM(CASE WHEN date(last_active,'localtime') = date('now','localtime') THEN 1 ELSE 0 END) AS dau,
  SUM(CASE WHEN last_active >= datetime('now','-7 days')  THEN 1 ELSE 0 END) AS wau,
  SUM(CASE WHEN last_active >= datetime('now','-30 days') THEN 1 ELSE 0 END) AS mau
FROM users WHERE is_blocked = 0;
```

Revenue by day (already in `report_revenue`):
```sql
SELECT date(created_at,'localtime') AS day,
       COALESCE(SUM(amount_rub),0) AS rub, COUNT(*) AS payments
FROM transactions WHERE status='paid' AND created_at >= datetime('now', ?)
GROUP BY day ORDER BY day DESC;
```

Top users by spend:
```sql
SELECT t.user_id, u.username,
       COALESCE(SUM(t.amount_rub),0) AS rub,
       COALESCE(SUM(t.stars_amount),0) AS stars,
       COUNT(*) AS payments
FROM transactions t
LEFT JOIN users u ON u.user_id = t.user_id
WHERE t.status='paid'
GROUP BY t.user_id ORDER BY rub DESC LIMIT ?;
```

Top referrers by referred revenue:
```sql
SELECT r.referrer_user_id, u.username,
       COUNT(DISTINCT r.referred_user_id) AS invited,
       COALESCE(SUM(CASE WHEN t.status='paid' THEN t.amount_rub END),0) AS referred_revenue_rub
FROM referrals r
LEFT JOIN transactions t ON t.user_id = r.referred_user_id
LEFT JOIN users u ON u.user_id = r.referrer_user_id
GROUP BY r.referrer_user_id ORDER BY referred_revenue_rub DESC LIMIT ?;
```

Channel ROI (already `report_channels`):
```sql
SELECT a.channel,
       COUNT(DISTINCT a.user_id) AS users,
       COUNT(DISTINCT CASE WHEN t.status='paid' THEN t.user_id END) AS paid_users,
       COALESCE(SUM(CASE WHEN t.status='paid' THEN t.amount_rub END),0) AS revenue_rub
FROM acquisitions a
LEFT JOIN transactions t ON t.user_id = a.user_id
GROUP BY a.channel ORDER BY revenue_rub DESC;
```

Error rate per backend account (already `report_accounts` / `report_errors`):
```sql
SELECT account_id, COUNT(*) AS jobs,
       SUM(CASE WHEN status!='success' THEN 1 ELSE 0 END) AS fails,
       1.0*SUM(CASE WHEN status!='success' THEN 1 ELSE 0 END)/COUNT(*) AS fail_rate
FROM flow_jobs
WHERE created_at >= datetime('now','-7 days')
GROUP BY account_id ORDER BY fail_rate DESC;
```

---

## 6. Failure cases ("what if…")

- **What if `upsert_user` is called on every message — too many writes?** A single
  UPSERT touching one indexed row is cheap; SQLite handles this fine at solo-bot
  volume. If write pressure ever shows in profiling, fall back to `touch_user`
  (last_active only) or throttle to once per N minutes per user in memory. Do not
  pre-optimize.
- **What if metrics DB is briefly unavailable / locked?** Same as today: the
  function logs WARNING and returns the safe default. `last_active` may miss one
  bump; a report may show stale activity. Never crashes the bot. This is the
  intended trade (analytics is best-effort; payments/credits are not).
- **What if the username snapshot is stale (user renamed)?** `users.username` is a
  *latest-seen* snapshot, refreshed on every `upsert_user`. Reports show the last
  value the bot saw. For historical audit, `transactions`/`events` are the record
  of truth at the time; `users` is current identity. Documented, acceptable.
- **What if `events` PII purge removes activity history?** Irrelevant to
  `last_active` now — it lives in `users`, which is NOT purged. This is exactly why
  we add the table: `events` (90-day purge) is the wrong place to derive
  first_seen/last_active.
- **What if RUB rate changes?** `amount_rub` is frozen at pay time (correct for
  accounting). Historical revenue is not retro-restated. If the operator wants a
  "current-rate" view, compute `stars_amount * STARS_TO_RUB` at read time — but
  the stored value is authoritative for what was actually charged.
- **What if a referred user pays before a `users` row exists?** Q6 joins
  `referrals` + `transactions` only; it does not need a `users` row. The
  referrer's profile screen still works. `users` is for identity/activity, never
  on the money path.
- **What if `backfill_users_from_metrics` runs twice / crashes mid-run?**
  `INSERT OR IGNORE` makes it idempotent; re-run freely. It never overwrites a
  row that the live writer has since updated.
- **What if two processes open the DB?** Out of scope — the bot is single-process.
  If a second reader (admin script) is ever added, open it read-only
  (`mode=ro`); do not add a second writer.

---

## 7. What we are NOT building (scope limits)

- **No separate `user_data.db`.** One file (`metrics.db`). See §1.
- **No balance column in `users` and no second starter-grant flag.** `credits` is
  the single source of truth for both. No duplication.
- **No per-event audit log of profile changes.** `users` holds current snapshot +
  `updated_at`. History of renames is not tracked.
- **No ORM, no migration framework (Alembic/etc).** `CREATE TABLE IF NOT EXISTS`
  in `_SCHEMA` is the migration mechanism, consistent with the existing module.
- **No materialized aggregate tables / triggers.** All rollups are live queries
  over indexed columns. The denormalized `req_*` caches were deliberately left
  OUT of the DDL (mentioned only as an option) to avoid cache-invalidation bugs;
  compute requests from `flow_jobs`.
- **No cross-DB joins, no ATTACH.** Direct consequence of Option A.
- **No GDPR/data-subject export/delete tooling** beyond the existing `events` PII
  purge. If needed later, a `delete_user(user_id)` that wipes `users` + nulls PII
  is a small follow-up — not in this scope.
- **No real-time dashboards.** Reports are on-demand admin commands, as today.

---

## 8. Implementer checklist (strict order)

1. Add `users` DDL + indexes to `_SCHEMA` in `metrics.py`. Verify:
   `python -m py_compile metrics.py`; table exists after `init_db()`.
2. Add `upsert_user`, `touch_user`, `mark_user_blocked` to `metrics.py` +
   `__all__`. Add unit tests (insert→update→last_active bump). Verify tests pass.
3. Add `get_user_profile` + the three new reports + `backfill_users_from_metrics`.
   Unit-test each against a seeded in-memory/temp DB AND an empty DB (zeros, no
   raise). Verify `python -m unittest discover -s tests -p "test_*.py"`.
4. Wire `upsert_user` into the main message + callback handlers in `flow_bot.py`
   (one call at handler top). Wire `mark_user_blocked` into the `TelegramForbidden`
   path. Verify with one live test message (operator-approved).
5. Add an admin command surfacing `get_user_profile` / `report_active_users` /
   `report_top_users` / `report_top_referrers`. Verify renders on empty + populated.
6. Run `backfill_users_from_metrics()` once. Verify row count sane. Done.
```
