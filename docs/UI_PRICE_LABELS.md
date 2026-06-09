# UI Price Labels Spec

**Purpose:** Make the credit cost of every paid action visible at the moment the
user decides whether to tap it — on the button itself when the price is static
and predictable, or in the message caption when the price is dynamic (progressive
extend chain).

**Scope:** All `InlineKeyboardMarkup` builders and result captions in
`flow_bot.py`, cross-referenced with pricing in `flow_core.py` and copy in
`flow_copy.py`.

**Pricing reference (flow_core.py):**
- `PRICE_PER_IMAGE = 10`, `UPSCALE_PRICE = 5`
- `VIDEO_PROMPT_EDIT_PRICE = 20`
- `VIDEO_EXTEND_STEP = 5`
- `video_extend_price(model_id, extend_index)` = `video_price(model_id,1,"text") + VIDEO_EXTEND_STEP * extend_index`
  - For `veo-lite` (base 30 kr): 1st extend = 35, 2nd = 40, 3rd = 45, …

---

## Screen 1 — Image Generation Wizard

**Builder:** `wizard_kb(count, fmt)` — `flow_bot.py` lines 2201–2219  
**Message:** `wizard_screen` in `flow_copy.MESSAGES` — already shows
`"Стоимость: {price} кр. (у тебя {credits} кр.)"` in the message body.

| Button | copy key | callback_data | Paid? | Proposed change |
|--------|----------|---------------|-------|-----------------|
| `1 фото` | `cnt:1` | `w:cnt:1` | No — selection toggle only | No change |
| `2 фото` | `cnt:2` | `w:cnt:2` | No — selection toggle only | No change |
| `4 фото` | `cnt:4` | `w:cnt:4` | No — selection toggle only | No change |
| `🖥 Альбом 16:9` | `fmt:land` | `w:fmt:land` | No | No change |
| `📱 Портрет 9:16` | `fmt:port` | `w:fmt:port` | No | No change |
| `⬜ Квадрат 1:1` | `fmt:sq` | `w:fmt:sq` | No | No change |
| `✨ Сгенерировать` | `go` | `w:go` | **Yes — `price_gen(count)` кр** | See below |
| `✕ Отмена` | `cancel` | `w:cancel` | Free | No change |

**Note:** The wizard message body already shows a running total
(`Стоимость: {price} кр.`). There is no need to duplicate the cost inside the
"Сгенерировать" button — it is visible one line above. **No button-label change
needed here.** The message body satisfies the requirement.

---

## Screen 2 — Image Result (per-image action keyboard)

**Builder:** `_image_keyboard(token)` — `flow_bot.py` lines 2154–2168  
**Caption:** set in `_send_result_pairs` at line 2900 as a bare emoji + index + prompt fragment; no price line currently present.

| Button | copy key | callback_data | Price function | Paid? | Proposed label |
|--------|----------|---------------|----------------|-------|----------------|
| `✏️ Изменить` | `edit` | `edit:<token>` | `action_price("edit")` = **10 кр** | Yes | `✏️ Изменить · 10 кр` |
| `🎲 Варианты` | `revary` | `vary:<token>` | `action_price("revary")` = **20 кр** | Yes | `🎲 Варианты · 20 кр` |
| `🔄 Заново` | `regen` | `regen:<token>` | `action_price("regen",1)` = **10 кр** (single); actual count used at gen time can differ if regen re-uses wizard count — treat as 10 кр per image delivered | Yes | `🔄 Заново · 10 кр` |
| `➕ В микс` | `mix` | `mix:<token>` | Free (ingredient staging only, generation priced later) | Free | No change |
| `✨ Чёткость ×2` | `up2x` | `u2:<token>` | `action_price("up2x")` = **5 кр** | Yes | `✨ Чёткость ×2 · 5 кр` |
| `🔍 Апскейл` | `realup` | `ru:<token>` | `action_price("realup")` = **5 кр** | Yes | `🔍 Апскейл · 5 кр` |
| `⬇ Скачать оригинал` | `dl_raw` | `dl:<token>` | `action_price("dl_raw")` = **0** | **Free** | No change — no price suffix |

**Where to show:** Button-label suffix on every paid button. The prices are
static per action type (they do not depend on per-image runtime state), so they
fit comfortably on a button label without exceeding the 64-byte `callback_data`
limit (the suffix is in the button _text_, not in `callback_data`).

**Implementation location:**
- `flow_copy.LABELS` keys `edit`, `revary`, `regen`, `up2x`, `realup` — add
  price suffix directly to the Russian strings, OR
- Build the label dynamically inside `_image_keyboard` using `action_price()`.
  The dynamic approach is preferred because `action_price` is the single source
  of truth; if pricing changes, the button updates automatically without editing
  `flow_copy`.

**Regen price note:** `regen` logically regenerates the same count as the
original batch (not always 1). However `_image_keyboard` does not receive the
original count; it only has the token. The per-image keyboard is built once per
delivered image, so charging 10 кр per image is correct per the current
`action_price("regen", 1)` contract. Show `10 кр` on the button. If the product
owner later wants count-aware regen pricing, `_image_keyboard` will need to
accept a count argument.

---

## Screen 3 — Post-result Navigation Menu

**Builder:** `_after_result` — `flow_bot.py` lines 2867–2878  
Buttons: `🔁 Повторить так же` (`m:repeat`), `🎨 Создать картинку` (`m:gen`),
`💳 Баланс и пополнение` (`m:balance`).

All three are navigation only — no action is charged here. **No price labels
needed.**

---

## Screen 4 — Balance Screen

**Builder:** inline keyboard inside `show_balance` — `flow_bot.py` lines 2635–2650  
**Message:** `balance_screen` — already says `"1 картинка — {price} кр.
Скачивание оригинала — бесплатно."` All cost context is in the message body.

| Button | copy key | callback_data | Paid? | Change |
|--------|----------|---------------|-------|--------|
| `➕ Пополнить баланс` | `topup` | `m:topup` | No (navigates to topup) | No change |
| `🏠 Главное меню` | `menu` | `m:menu` | Free | No change |

No change needed.

---

## Screen 5 — Top-up / Stars Packs Screen

**Builder:** `topup_kb()` — `flow_bot.py` lines 2605–2611  
**Label builder:** `pack_label(pid)` in `flow_core.py` lines 2716–2728 — already
renders `"700 кр · ~70 ген · 450⭐ 🔥 Выгодно"` (credits + gen count + stars).

These buttons are purchase actions, not credit-spending actions. The existing
`pack_label` format is sufficient. **No change needed.**

---

## Screen 6 — Video Family Picker

**Builder:** `video_family_kb()` — `flow_bot.py` lines 2324–2332

| Button | copy key | callback_data | Paid? | Change |
|--------|----------|---------------|-------|--------|
| `⚡ Быстрое — дешевле` | `vid_fam:omni` | `v:fam:omni` | No (picker nav) | No change |
| `✨ Кино — премиум` | `vid_fam:veo` | `v:fam:veo` | No (picker nav) | No change |
| `🧩 Из фото + текст` | `vid_fam:ing` | `v:fam:ing` | No (picker nav) | No change |
| `🎞 Старт → Финиш` | `vid_fam:frm` | `v:fam:frm` | No (picker nav) | No change |
| `✕ Отмена` | `cancel` | `v:cancel` | Free | No change |

Price is unknown until a model variant is selected. **No change needed here.**

---

## Screen 7 — Video Variant Picker (Omni / Veo)

**Builder:** `video_variant_kb(family, selected_model)` — `flow_bot.py` lines 2340–2353

Each model button already includes the per-video base price in the label:
```python
label = f"{name} · {meta['price']} кр"
```
Example: `"⚡ 4 сек · мини · 20 кр"`.

This satisfies the price-visibility requirement for the first generation.
**No change needed here.**

| Button | current format | Paid? | Status |
|--------|---------------|-------|--------|
| Each model variant | `{name} · {price} кр` | Yes (first gen) | Already shown — OK |
| `← Стиль` | nav | Free | No change |
| `✕ Отмена` | nav | Free | No change |

---

## Screen 8 — Video Settings Wizard

**Builder:** `video_wizard_kb(vfmt, vcount)` — `flow_bot.py` lines 2356–2371  
**Message:** `vid_settings_screen` — already shows
`"Стоимость: {price} кр. (у тебя {credits} кр.)"`.

| Button | copy key | callback_data | Paid? | Change |
|--------|----------|---------------|-------|--------|
| Format toggles (`land`/`port`) | selection | `v:fmt:*` | No | No change |
| Count toggles (1–4) | selection | `v:cnt:*` | No | No change |
| `▶️ Создать видео` | `vid_go` | `v:go` | **Yes — total shown in message** | No change — cost visible in message body |
| `← Изменить вариант` | `vid_back:model` | `v:back:model` | Free | No change |
| `✕ Отмена` | `cancel` | `v:cancel` | Free | No change |

The message body already shows the running total. **No button-label change needed.**

---

## Screen 9 — Video Ingredients Screen

**Builder:** `ingredients_kb(n, vfmt, vcount, vmodel)` — `flow_bot.py` lines 2434–2441  
**Message:** `vid_ing_screen` — already shows `"Стоимость: {price} кр."`.  
**Model row:** `_vid_model_row("ingredients", vmodel)` — already shows price per model.

No additional price changes needed here.

---

## Screen 10 — Video Frames Screen

**Builder:** `frames_kb(...)` — `flow_bot.py` lines 2444–2451  
**Message:** `vid_frm_screen` — already shows `"Стоимость: {price} кр."`.  
**Model row:** `_vid_model_row("frames", vmodel)` — already shows price per model.

No additional price changes needed here.

---

## Screen 11 — Video Result Actions

**Builder:** `video_result_kb(vtoken)` — `flow_bot.py` lines 2389–2401  
**Caption:** set at line 3871 as `vid_result_caption` = `"Видео {i} из {n} · «{prompt}»"` with no price context.

| Button | copy key | callback_data | Price | Paid? | Proposed label |
|--------|----------|---------------|-------|-------|----------------|
| `⬇ Скачать видео` | `vid_dl` | `v:dl:<token>` | 0 | **Free** | No change |
| `✂️ Только новый фрагмент` | `vid_dl_seg` | `v:dl_seg:<token>` | 0 | **Free** | No change |
| `✏️ Изменить` | `vid_edit` | `v:edit:<token>` | `VIDEO_PROMPT_EDIT_PRICE` = **20 кр** | Yes | `✏️ Изменить · 20 кр` |
| `⏩ Продлить` | `vid_extend` | `v:extend:<token>` | `video_extend_price(model_id, ref.extend_index + 1)` — **progressive** | Yes | See below |

### Extend button — progressive price (special handling required)

The extend price is NOT static. For `veo-lite` (base 30 кр):
- Button appears on the original video (`extend_index = 0`): next extend costs **35 кр**
- Button on first extend result (`extend_index = 1`): next extend costs **40 кр**
- Etc.

Because the price changes per `VideoRef` instance and is only known at the time
`video_result_kb` is called (where `ref` is already in scope), it can be
computed dynamically right inside the builder. The result fits on the button
label without making `callback_data` longer.

**Proposed approach — compute in `video_result_kb`:**

```python
from flow_core import video_extend_price  # add to imports

# inside video_result_kb, replacing the current static extend button:
if _video_can_extend(ref):
    next_idx = (ref.extend_index or 0) + 1
    ext_price = video_extend_price(ref.model_id, next_idx)
    rows.append([B(
        text=f"{L('vid_extend')} · {ext_price} кр",
        callback_data=f"v:extend:{vtoken}",
    )])
```

This requires `extend_index` to be populated on `VideoRef` when the extend
result is stored. Currently the `VideoRef` constructor in `_video_generate_and_send`
(line 3855) does not set `extend_index`. See implementation notes below.

**Also show the extend price in the result caption**, because users see the
caption before looking at buttons:

Proposed addition to the caption for Veo-lite results (append to the
`vid_result_caption` line when `_video_can_extend(vref)` is true):

```
Видео {i} из {n} · «{prompt}»
⏩ Продлить — {ext_price} кр · ✏️ Изменить — 20 кр
```

This is assembled in the generation loop at line 3871 in `flow_bot.py`, where
`vref` is already constructed and `_video_can_extend(vref)` can be evaluated.

**New copy key needed** in `flow_copy.MESSAGES`:

```python
"vid_result_actions_hint": "⏩ Продлить — {ext_price} кр · ✏️ Изменить — {edit_price} кр",
```

---

## Screen 12 — Video Edit Prompt Request

**Trigger:** `_video_edit_start` handler — `flow_bot.py` line 4014  
**Message sent:** `vid_edit_ask_prompt` from `flow_copy.MESSAGES`:
```
"Напиши, что изменить в видео.\nЯ сделаю новую версию на основе прошлого описания. Стоимость: {price} кр."
```
Price is already embedded in this message using `action_price("video_prompt_edit")`.
**No change needed.**

---

## Screen 13 — Video Extend Prompt Request

**Trigger:** `_video_extend_start` handler — `flow_bot.py` line 4032  
**Message sent:** `vid_ask_prompt` from `flow_copy.MESSAGES`:
```
"Опиши, что должно происходить в видео. …"
```
This message does NOT mention the cost. The user has already tapped the
`⏩ Продлить · {price} кр` button (which shows the price), but a confirmation
line in the prompt message adds clarity.

**Proposed change to `vid_ask_prompt`:** add a new key rather than modifying the
shared `vid_ask_prompt` (which is also used for the first-time video prompt).

New copy key in `flow_copy.MESSAGES`:

```python
"vid_extend_ask_prompt": (
    "Опиши, что должно происходить в продолжении видео.\n"
    "Стоимость: {price} кр. Кредиты спишутся при старте."
),
```

Use this key in `_video_extend_start` instead of `vid_ask_prompt`, passing
`price=video_extend_price(ref.model_id, ref.extend_index + 1)`.

---

## Screen 14 — Low-balance / Insufficient Credits Interrupts

These appear inline at various points (image gen, video gen, per-action handlers)
and already show `flow_copy.msg("low_balance", needed=price, have=have)` which
says `"Не хватает кредитов: нужно {needed} кр., а у тебя {have} кр."`.

**No change needed.**

---

## Summary of what already has price visibility (no action required)

| Screen | Mechanism |
|--------|-----------|
| Image wizard | `wizard_screen` message body shows total |
| Video variant picker | `video_variant_kb` button labels show `· {price} кр` |
| Video settings wizard | `vid_settings_screen` message body shows total |
| Video ingredients screen | `vid_ing_screen` message body shows total |
| Video frames screen | `vid_frm_screen` message body shows total |
| Video edit prompt | `vid_edit_ask_prompt` message body shows `Стоимость: {price} кр.` |
| Balance screen | `balance_screen` body shows per-image price |
| Top-up packs | `pack_label` shows credits + gen count + stars |

---

## Implementation Notes

### `extend_index` must be populated in `VideoRef` construction

In `_video_generate_and_send` at line 3855 of `flow_bot.py`, the `VideoRef`
constructor does not set `extend_index`. The field defaults to `0` on
`flow_core.VideoRef` (line 1587 of `flow_core.py`). For the progressive price to
be correct, the implementer must:

1. Pass `source_video.extend_index + 1` when `video_operation == "extend"` and
   `source_video` is not None, otherwise pass `0`.
2. Import `video_extend_price` from `flow_core` in `flow_bot.py`.

### New `flow_copy` imports needed in `flow_bot.py`

```python
from flow_core import video_extend_price
```

### Byte budget for button labels

`callback_data` is unaffected by label text. A label like
`"⏩ Продлить · 35 кр"` is 17 characters — well within Telegram's 64-character
display limit. All proposed suffixes are 6–10 characters.

---

## Prioritised Implementation Checklist

### P1 — Highest user impact, touches result screens the user sees after every generation

- [ ] **P1-A** `_image_keyboard` in `flow_bot.py` (~line 2154): add price suffix
  to `edit` (10 кр), `revary` (20 кр), `regen` (10 кр), `up2x` (5 кр),
  `realup` (5 кр) button labels. Build labels dynamically using `action_price()`
  rather than hardcoding strings in `flow_copy.LABELS`, so a price change in
  `flow_core` propagates automatically. `dl_raw` stays unchanged (free).

- [ ] **P1-B** `video_result_kb` in `flow_bot.py` (~line 2389): add
  `· 20 кр` suffix to `vid_edit` button. Import `video_extend_price` from
  `flow_core` and compute the extend label dynamically as
  `f"{L('vid_extend')} · {video_extend_price(ref.model_id, (ref.extend_index or 0) + 1)} кр"`.

- [ ] **P1-C** `VideoRef` construction in `_video_generate_and_send` (~line 3855):
  set `extend_index=source_video.extend_index + 1` when
  `video_operation == "extend" and source_video is not None`, otherwise leave
  as default `0`. This is required for P1-B to produce correct progressive prices.

- [ ] **P1-D** Caption for Veo-lite video results in `_video_generate_and_send`
  (~line 3871): when `_video_can_extend(vref)` is true, append an action-hint
  line. Add `"vid_result_actions_hint"` to `flow_copy.MESSAGES` and format it
  with `ext_price` and `edit_price=VIDEO_PROMPT_EDIT_PRICE`.

### P2 — Confirmatory copy, slightly lower urgency

- [ ] **P2-A** `_video_extend_start` in `flow_bot.py` (~line 4032): replace
  `flow_copy.msg("vid_ask_prompt")` with a new `"vid_extend_ask_prompt"` key that
  includes `Стоимость: {price} кр.`. Add the new key to `flow_copy.MESSAGES`.
  Pass `price=video_extend_price(ref.model_id, ref.extend_index + 1)` (requires
  P1-C first so `extend_index` is reliable on the ref).

- [ ] **P2-B** Audit `flow_copy.LABELS` for keys `edit`, `revary`, `regen`,
  `up2x`, `realup`, `vid_edit`, `vid_extend`: if the dynamic label approach from
  P1-A/P1-B is adopted (building labels in the keyboard function rather than from
  `LABELS`), document the keys as "label-only, price suffix added at runtime"
  with a comment so future copywriters do not add a static suffix that would
  double-show the price.
