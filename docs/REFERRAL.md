# REFERRAL.md — Referral Program Design

Last updated: 2026-06-27.

> **Update 2026-06-27 — friend-focused model (ground truth, overrides older
> sections below).**
> - **Friend gift on join:** the *invited* user gets **+15 credits**
>   (`REFERRAL_REFERRED_BONUS = 15`), once, the moment the referral row is created
>   in `record_referral_join` (deep-link `/start ref_<id>`). It goes through
>   `credit_store.add` outside the payments pipeline, so it does **not** trigger
>   any reward to the referrer (free-action farming stays closed).
> - **Referrer tiers (unchanged):** +20 / +30 / +50 on the friend's **first
>   payment** (`stars >= 200 / 450`), once, via `mark_referral_rewarded`
>   (`joined → rewarded`).
> - **Ongoing revenue share: 10%** (`REFERRAL_ONGOING_PCT = 0.10`) of credits
>   issued on every later top-up.
> - **Attribution expires after ~3 months** (`REFERRAL_REWARD_WINDOW_DAYS = 90`);
>   after that neither the tier bonus nor the ongoing % is paid. Gated by
>   `metrics.referral_is_active(...)` in `_maybe_apply_referral_rewards`.
> - **REMOVED: the "+50 for the first invited user's generation" reward.** All
>   referrer rewards now fire **only** in `on_successful_payment` (anti-farm, §3).
>   The `referral_first_generation_rewards` table is kept read-only for historical
>   analytics; nothing writes to it.

---

## 0. Ground truth used in this design

| Constant | Value | Source |
|---|---|---|
| `PRICE_PER_IMAGE` | 10 bot credits | `flow_core.py` |
| `STARTER_CREDITS` | 30 credits | `flow_core.py` |
| `STARS_TO_RUB` | 1.3 ₽/Star | bot env, `flow_core.py` comment |
| Telegram Stars cut | ~30% | Telegram standard |
| Net ₽ per Star to bot | ~0.91 ₽ | 1.3 × 0.70 |
| 1 bot credit retail value | ~1.0 ₽ | conservative midpoint of 0.9–1.2 range from MONETIZATION.md |
| Variable cost of 1 image | 0 G-credits | confirmed in MONETIZATION.md |

Pack ₽ equivalents (gross = Stars × 1.3; net = gross × 0.70):

| Pack id | Stars | Credits | Gross ₽ | Net ₽ to bot |
|---|---:|---:|---:|---:|
| trial | 35 | 45 | 45.5 | 31.9 |
| small | 75 | 100 | 97.5 | 68.3 |
| medium | 200 | 290 | 260 | 182 |
| large | 450 | 700 | 585 | 409.5 |
| xl | 900 | 1500 | 1170 | 819 |

---

## 1. Reward ladder (exact, unambiguous)

### 1.1. Design decision on the owner's scheme

The owner proposed +20/+30/+50 bonuses with unclear stacking. This design resolves it as follows:

**First-payment milestone bonus**: on the referred user's first paid transaction, the referrer receives exactly one bonus from the table below — the highest tier the payment qualifies for. Tiers are mutually exclusive on first payment. Ongoing percentage applies to every payment after first as well as the first.

This is cleaner than additive stacking because: it is easy to explain to users, impossible to game by splitting payments, and keeps reward costs predictable.

### 1.2. Pack-to-₽ tier mapping

The payment system is in Stars, not ₽. The ₽ thresholds from the owner's proposal map to specific packs using `STARS_TO_RUB = 1.3`:

| Owner's threshold | Stars threshold | First pack that qualifies | Pack id |
|---|---:|---|---|
| "buys anything" | any paid pack | trial (35⭐ = 45.5₽) | trial or above |
| ">100₽" | >77 Stars | medium (200⭐ = 260₽) | medium or above |
| ">500₽" | >385 Stars | large (450⭐ = 585₽) | large or xl |

Note: the small pack (75⭐ = 97.5₽) falls below the 100₽ threshold, so it triggers only tier 1 (base join bonus). This is intentional — small is the gateway pack; getting a bonus on it anyway is fine since the referred user still paid.

### 1.3. Final reward table

| Event | Trigger condition | Reward to referrer |
|---|---|---|
| **Tier 0 — join bonus** | referred user's first `/start` via ref link (no payment required) | 0 credits — join alone gives nothing (anti-farming) |
| **Tier 1 — first purchase** | referred user completes first paid transaction of any pack (trial / small) where `stars < 200` | +20 credits |
| **Tier 2 — mid purchase** | referred user's first paid transaction is medium pack or above (`stars >= 200`, gross >= 260₽) | +30 credits (replaces tier 1 — not additive) |
| **Tier 3 — large purchase** | referred user's first paid transaction is large or xl pack (`stars >= 450`, gross >= 585₽) | +50 credits (replaces tier 2 — not additive) |
| **Ongoing — revenue share** | every subsequent paid top-up by the referred user (2nd purchase onward) | +10% of credits issued, rounded down, subject to daily cap |

Implementation note: the first-payment tier is evaluated once and recorded in `mark_referral_rewarded`. Ongoing payments fire separately through a `referral_ongoing_reward` event. Both are idempotent on `provider_payment_id`.

---

## 2. Margin check

All rewards are funded by the referred user's actual paid revenue. Image credits cost 0 G-credits to serve, so the only cost of issuing reward credits is opportunity cost: credits given to the referrer that are later spent on images represent forgone retail revenue. At 1 credit ≈ 1.0₽ retail value:

### 2.1. First-payment tiers

| Tier | Reward | OC (₽) | Referred user min net ₽ | Net margin after reward | Safe? |
|---|---:|---:|---:|---:|---|
| Tier 1 (trial or small, <200⭐) | 20 cr | ~20₽ | 31.9₽ (trial net) | 11.9₽ | Yes |
| Tier 2 (medium+, >=200⭐) | 30 cr | ~30₽ | 182₽ (medium net) | 152₽ | Yes — very comfortable |
| Tier 3 (large+, >=450⭐) | 50 cr | ~50₽ | 409.5₽ (large net) | 359.5₽ | Yes — very comfortable |

Tier 1 is the tightest: 20₽ opportunity cost against 31.9₽ net. Effective referral cost = 62.7% of net revenue on a trial purchase. This is acceptable because (a) a referred user who buys trial typically upgrades to larger packs, and (b) the reward is only 20 credits — one extra image. If this feels too expensive, drop Tier 1 to +15 credits (OC ~15₽, net after = 16.9₽, referral cost 47%). The design uses +20 per the owner's intent; flag this as a tuning knob.

### 2.2. Ongoing 10%

| Pack | Credits issued | 10% reward | OC (₽) | Net ₽ to bot | Referral cost as % of net |
|---|---:|---:|---:|---:|---:|
| trial | 45 | 4 | ~4₽ | 31.9₽ | 12.5% |
| small | 100 | 10 | ~10₽ | 68.3₽ | 14.6% |
| medium | 290 | 29 | ~29₽ | 182₽ | 15.9% |
| large | 700 | 70 | ~70₽ | 409.5₽ | 17.1% |
| xl | 1500 | 150 | ~150₽ | 819₽ | 18.3% |

Ongoing 10% is consistent at 12–18% of net revenue. This is a healthy CAC on repeat revenue and stays safely below the 30% threshold that would threaten profitability. No adjustment needed.

### 2.3. Daily cap check

At the daily cap of 500 credits/referrer/day, a referrer maxes out at ~500₽ opportunity cost per day. To hit this cap they need referred users spending ~2 750₽/day (net) in referred purchases, which implies real user activity. The cap is not punitive to legitimate top referrers and prevents a coordinated abuse ring from draining credits without actual revenue.

---

## 3. Anti-abuse rules

| Rule | Implementation | Where enforced |
|---|---|---|
| **No self-referral** | `referrer_user_id != referred_user_id` | Already enforced in `record_referral_join` (returns `False` silently) |
| **One referrer per user** | `referred_user_id UNIQUE` in `referrals` table | Already enforced by the DB schema + `INSERT OR IGNORE` |
| **No reward on join alone** | Tier 0 = 0 credits; reward only fires in `on_successful_payment` | See integration contract §4 |
| **No bot accounts** | Check `message.from_user.is_bot` before processing `/start ref_*` | `flow_bot.py` entry point |
| **First payment only, for milestone bonus** | `mark_referral_rewarded` writes `status='rewarded'`; `on_successful_payment` checks `referral_status == 'joined'` before paying milestone bonus | metrics.py query `get_referrer_of` + status check |
| **Ongoing idempotency** | Gate on `provider_payment_id` — already unique in `transactions`; store `referral_ongoing_reward` event keyed on `provider_payment_id` to prevent double-pay on webhook replay | New `referral_ongoing_rewards` table (see §4.4) |
| **Daily cap per referrer** | Sum of credits issued to referrer today (milestone + ongoing) must not exceed `REFERRAL_DAILY_CAP_CREDITS = 500` | Checked in `on_successful_payment` before calling `CreditStore.add` |
| **Refund clawback** | If a payment is refunded via `/refund`, the referral reward issued for that payment's `provider_payment_id` is also clawed back via `CreditStore.charge` on the referrer | `/refund` handler reads the `referral_ongoing_rewards` table to find rewards linked to the refunded transaction |
| **No credit farming via free actions** | Referral rewards are only triggered by actual Stars payments (transactions table, `status='paid'`), never by generation events | Only `on_successful_payment` triggers referral logic |
| **Starter credits are not a referral trigger** | Starter credits are granted by `CreditStore.balance()` (first access), not through the payments pipeline; no referral hook fires | Structural — `STARTER_CREDITS` path never touches `on_successful_payment` |

---

## 4. Integration contract

### 4.1. Deep link format

Referral links use Telegram's standard `/start` deep link parameter:

```
https://t.me/<bot_username>?start=ref_<referrer_user_id>
```

Parameter format: `ref_` prefix + referrer's Telegram user id (integer).

- No short codes needed at this scale; user ids are already short enough and unguessable enough for the purpose.
- If a short code is later required (e.g. for analytics tracking of specific campaigns), add a `referral_codes` table — but do not implement now.

On `/start ref_<referrer_id>` received by the bot:

1. Parse `referrer_id` from the payload (integer, reject if not numeric or if == `message.from_user.id`).
2. Call `metrics.record_referral_join(referrer_user_id=referrer_id, referred_user_id=user_id)`. This is idempotent and returns `True` only on first registration.
3. If `record_referral_join` returned `True`, emit `log_event("referral_joined", user_id=user_id, payload={"referrer": referrer_id})`.
4. Do NOT grant any credits here.

### 4.2. On first payment (`on_successful_payment`, milestone path)

After `record_transaction` returns `True` (new row — prevents double processing):

```python
referrer_id = get_referrer_of(referred_user_id=user_id)
if referrer_id is not None:
    ref_status = referral_status(referred_user_id=user_id)
    if ref_status == "joined":  # not yet rewarded
        stars = payment.invoice_payload_stars  # or from STARS_PACKS
        bonus = _milestone_bonus(stars)
        if bonus > 0 and not _daily_cap_exceeded(referrer_id, bonus):
            CreditStore.add(referrer_id, bonus)
            mark_referral_rewarded(
                referred_user_id=user_id,
                reward_credits=bonus,
                first_payment_transaction_id=transaction_row_id,
            )
            log_event("referral_reward_paid",
                      user_id=referrer_id,
                      payload={"referred": user_id, "bonus": bonus,
                               "tier": "milestone", "stars": stars,
                               "transaction_id": transaction_row_id})
```

`_milestone_bonus(stars)` logic:

```python
def _milestone_bonus(stars: int) -> int:
    if stars >= REFERRAL_TIER3_STARS:   # 450
        return REFERRAL_TIER3_BONUS     # 50
    if stars >= REFERRAL_TIER2_STARS:   # 200
        return REFERRAL_TIER2_BONUS     # 30
    return REFERRAL_TIER1_BONUS         # 20
```

### 4.3. On every payment (`on_successful_payment`, ongoing path)

After `record_transaction` returns `True` AND after the milestone check above (regardless of whether milestone was triggered — a user who has already been `rewarded` still generates ongoing revenue share):

```python
referrer_id = get_referrer_of(referred_user_id=user_id)
if referrer_id is not None:
    # Avoid double-paying the ongoing share if the milestone was also this transaction
    # (milestone already ran above) — only pay ongoing on 2nd+ purchases.
    ref_status = referral_status(referred_user_id=user_id)
    if ref_status == "rewarded":  # milestone already paid; this is a repeat purchase
        credits_issued = pack_credits  # from STARS_PACKS lookup
        ongoing_reward = int(credits_issued * REFERRAL_ONGOING_PCT)  # floor
        if ongoing_reward > 0 and not _daily_cap_exceeded(referrer_id, ongoing_reward):
            already_paid = _ongoing_reward_paid_for(provider_payment_id)
            if not already_paid:
                CreditStore.add(referrer_id, ongoing_reward)
                _record_ongoing_reward(referrer_id, user_id, ongoing_reward, provider_payment_id)
                log_event("referral_reward_paid",
                          user_id=referrer_id,
                          payload={"referred": user_id, "bonus": ongoing_reward,
                                   "tier": "ongoing", "provider_payment_id": provider_payment_id})
```

`_daily_cap_exceeded(referrer_id, amount)`: query the sum of credits issued to `referrer_id` today via referral rewards (milestone + ongoing combined) from the `referral_ongoing_rewards` table plus today's milestone from `referrals`. Returns True if adding `amount` would exceed `REFERRAL_DAILY_CAP_CREDITS`.

### 4.4. New table: `referral_ongoing_rewards`

Add to `metrics.py` `_SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS referral_ongoing_rewards (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_user_id     INTEGER NOT NULL,
    referred_user_id     INTEGER NOT NULL,
    reward_credits       INTEGER NOT NULL,
    provider_payment_id  TEXT UNIQUE,
    created_at           TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ror_referrer ON referral_ongoing_rewards(referrer_user_id, created_at);
```

The `UNIQUE` on `provider_payment_id` makes ongoing reward insertion idempotent, matching the same pattern as `transactions`.

### 4.5. Refund clawback

In the `/refund` admin handler, after `PaymentStore.mark_refunded(charge_id)` and `CreditStore.charge(user_id, credits)` (the existing refund logic):

```python
# Claw back any referral reward tied to this payment
row = _get_ongoing_reward_by_payment(provider_payment_id)
if row is not None:
    CreditStore.charge(row["referrer_user_id"], row["reward_credits"])
    log_event("referral_reward_clawback",
              user_id=row["referrer_user_id"],
              payload={"referred": row["referred_user_id"],
                       "clawed_back": row["reward_credits"],
                       "reason": "payment_refunded"})
# Also check milestone: if this was the first_payment_transaction_id
milestone_row = _get_milestone_by_transaction(transaction_db_id)
if milestone_row is not None and milestone_row["status"] == "rewarded":
    CreditStore.charge(milestone_row["referrer_user_id"], milestone_row["reward_credits"])
    # Reset referral status to 'joined' so the next genuine payment can re-trigger
    _reset_referral_to_joined(milestone_row["referred_user_id"])
```

Clawback should not go below zero — use `max(0, balance - clawback)` if needed, or simply call `CreditStore.charge` which already handles insufficient balance gracefully (returns False, does not go negative).

### 4.6. New query helpers for `metrics.py`

Minimal additions — all should follow the existing `try/except` + `_LOCK` pattern:

```python
def get_referrer_of(referred_user_id: int) -> int | None:
    """Return the referrer_user_id for a referred user, or None if not referred."""

def referral_status(referred_user_id: int) -> str | None:
    """Return status ('joined'|'rewarded') for a referred user, or None."""

def get_referral_credits_today(referrer_user_id: int) -> int:
    """Sum of reward_credits issued to referrer today (milestone + ongoing)."""

def get_ongoing_reward_by_payment(provider_payment_id: str) -> dict | None:
    """Return the referral_ongoing_rewards row for a payment id, or None."""

def get_milestone_by_transaction(transaction_id: int) -> dict | None:
    """Return the referrals row whose first_payment_transaction_id matches, or None."""

def reset_referral_to_joined(referred_user_id: int) -> None:
    """Undo a rewarded milestone (used on refund): set status back to 'joined'."""
```

These are the only new helpers needed. Do not add a separate JSON store for referral state — the `referrals` table in `metrics.db` is the single source of truth.

### 4.7. UI surfaces

**Main menu entry — "Пригласи друга":**

Data needed:
- User's referral link: `https://t.me/<BOT_USERNAME>?start=ref_<user_id>`
- Count of invited users: `SELECT COUNT(*) FROM referrals WHERE referrer_user_id = ?`
- Total credits earned: `SELECT COALESCE(SUM(reward_credits),0) FROM referrals WHERE referrer_user_id = ?` + sum from `referral_ongoing_rewards`

Display: referral link (copyable), stats line (N invited / M credits earned), share button.

**Under each generated image and video:**

An invite button showing the user's referral link with copy-to-clipboard or share action. The button label and the exact copy are owned by the `telegram-copywriter` agent. What the surface needs: the user's referral link string and the current join bonus equivalent in credits (for the CTA label — the relevant constant is `REFERRAL_TIER1_BONUS`).

Placement trigger: show after a successful generation (both image and video). Do not show during insufficient-balance flows (upsell to purchase takes priority there).

---

## 5. Constants table (ready to hardcode)

```python
# ── referral program constants ─────────────────────────────────────────

# Deep link prefix for referral parameter in /start payload
REFERRAL_PARAM_PREFIX = "ref_"

# First-payment milestone bonuses (tier = highest applicable tier for that payment)
# Tier 1: any purchase below medium (stars < REFERRAL_TIER2_STARS)
REFERRAL_TIER1_BONUS = 20           # credits to referrer
# Tier 2: medium pack or above (stars >= 200)
REFERRAL_TIER2_STARS = 200
REFERRAL_TIER2_BONUS = 30           # credits to referrer (replaces tier 1)
# Tier 3: large pack or above (stars >= 450)
REFERRAL_TIER3_STARS = 450
REFERRAL_TIER3_BONUS = 50           # credits to referrer (replaces tier 2)

# Ongoing revenue share on every top-up by a referred user after milestone is paid
REFERRAL_ONGOING_PCT  = 0.10        # 10% of credits_issued, floor division

# Anti-abuse: maximum referral reward credits one referrer can receive per calendar day
# (milestone + ongoing combined)
REFERRAL_DAILY_CAP_CREDITS = 500
```

Place these in `flow_core.py` alongside `PRICE_PER_IMAGE`, `STARTER_CREDITS`, etc.

---

## 6. Margin safety summary

| Scenario | Reward | Bot net on that payment | Reward as % of net | Verdict |
|---|---|---:|---:|---|
| Referred user buys trial (35⭐) | 20 cr (~20₽ OC) | 31.9₽ | 62.7% | Acceptable — first purchase LTV play |
| Referred user buys small (75⭐) | 20 cr (~20₽ OC) | 68.3₽ | 29.3% | Comfortable |
| Referred user buys medium (200⭐) | 30 cr (~30₽ OC) | 182₽ | 16.5% | Comfortable |
| Referred user buys large (450⭐) | 50 cr (~50₽ OC) | 409.5₽ | 12.2% | Very comfortable |
| Referred user buys xl (900⭐) | 50 cr (~50₽ OC) | 819₽ | 6.1% | Very comfortable |
| Ongoing: referred buys medium (200⭐) | 29 cr (~29₽ OC) | 182₽ | 15.9% | Comfortable |
| Ongoing: referred buys xl (900⭐) | 150 cr (~150₽ OC) | 819₽ | 18.3% | Comfortable |
| Daily cap hit (500 cr in one day) | 500 cr (~500₽ OC) | ≥2 750₽ net to trigger | ~18% max | Self-funding by construction |

The only scenario to watch: a referrer who sends many users who each only ever buy the trial pack. In that case each referral yields 31.9₽ net and costs 20₽ in credits. That is still positive (11.9₽ net after reward) but with thin margin. If trial-only behavior becomes a pattern, reduce `REFERRAL_TIER1_BONUS` from 20 to 15 credits without changing anything else.

---

## 7. What this design does NOT do (out of scope)

- Multi-level referrals (referrers of referrers). Not implemented — adds complexity and abuse surface with minimal LTV gain at this scale.
- Referred user also gets a bonus on their first payment. Could be added as `REFERRAL_REFERRED_BONUS = 10` (one extra image), but not in this design pass. The starter credits (30) already serve as the first-use hook.
- Leaderboard / gamification. Possible future layer on top of `report_refs()`.
- Expiry of unclaimed referral status. Not needed — `joined` rows with no conversion are inert and cost nothing.
