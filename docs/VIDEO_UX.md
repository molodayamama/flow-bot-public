# VIDEO_UX.md — Video Wizard: Button-Driven State-Machine Spec

Authored for the engineer implementing "Создать видео" in `flow_bot.py`.
Read `flow_bot.py`, `flow_core.py`, and `flow_copy.py` before implementing.
The image wizard (`w:` prefix) is the reference pattern; mirror it exactly
unless this spec says otherwise.

---

## 0. Design principles and deviations from the image wizard

| Topic | Image wizard | Video wizard decision |
|---|---|---|
| callback prefix | `w:` | `v:` for all video callbacks |
| wizard state dict | `wizard_state[uid]` shared | same dict, namespaced keys `vmodel`, `vfmt`, `vcount`, `vmode`, `vawait`, `vlast` — no collision with `w:` keys |
| count | 1/2/4 | 1/2/3/4 (video is slower; show 1 selected by default) |
| format | land/port/sq | land/port only — NO square (`VIDEO_UI_ASPECTS`) |
| model selection | none (single model) | two-step family picker then variant picker, then settings screen |
| prompt entry | after `w:go` | same pattern: after `v:go` edit the wizard message to ask_prompt |
| per-image actions | 8 buttons | video: download only (Phase 1); Phase 2 adds re-gen |
| reply keyboard | `kb_gen` button | add `kb_vid` button on the same row as `kb_gen` |
| pending_prompt | supported | supported identically |

Improvement over the brief: the "family pick" and "variant pick" screens are
separate inline messages that are edited in-place (not new messages), so the
user never loses context. Switching family clears the variant and returns to the
variant screen automatically.

---

## 1. Reply keyboard change

```
Row 1: [kb_gen: "🎨 Создать картинку"] [kb_vid: "🎬 Создать видео"]
Row 2: [kb_menu: "🏠 Меню"]            [kb_balance: "💳 Баланс"]
```

`kb_vid` sends the literal label text, handled in `handle_plain_text` exactly
like `kb_gen`. The existing `reply_menu_kb()` function gains one button.

---

## 2. Main menu change

Add a new row between "gen" and "myphoto":

```
[m:gen   "🎨 Создать картинку"]
[m:vid   "🎬 Создать видео"  ]       ← NEW
[m:myphoto ...] [m:balance ...]
[m:help  ...]
```

`main_menu_kb()` gains the `m:vid` button. The `on_menu_action` handler routes
`m:vid` identically to how it routes `m:gen`, but calls `show_video_family()`
instead of `show_wizard()`.

---

## 3. Screen map

```
[MAIN MENU]
     |
     | m:vid  (or kb_vid text, or reply-kb tap)
     v
[SCREEN A — FAMILY PICK]          callback_data: v:fam:<family>
  "🎬 Создать видео\nВыбери стиль:"
  [ Omni Flash — быстро/дёшево  v:fam:omni ]
  [ Veo 3.1 — качество          v:fam:veo  ]
  [ ✕ Отмена                    v:cancel   ]
     |
     | v:fam:omni  ──────────────────────────────────────────────
     v                                                           |
[SCREEN B1 — VARIANT PICK: OMNI FLASH]                          |
  "Omni Flash — выбери длительность:"                           |
  [ 4 с · 7 кр  v:model:omni-flash-4s ]                        |
  [ 6 с · 10 кр v:model:omni-flash-6s ]                        |
  [ 8 с · 12 кр v:model:omni-flash-8s ]                        |
  [10 с · 15 кр v:model:omni-flash-10s]                        |
  [ ← Назад     v:back:fam ]                                   |
  [ ✕ Отмена    v:cancel   ]                                   |
                                                                |
     | v:fam:veo                                                |
     v                                                          |
[SCREEN B2 — VARIANT PICK: VEO]                                 |
  "Veo 3.1 — выбери качество:"                                  |
  [ Lite  · 10 кр   v:model:veo-lite    ]                      |
  [ Fast  · 20 кр   v:model:veo-fast    ]                      |
  [ Качество · 100 кр v:model:veo-quality]                     |
  [ ← Назад  v:back:fam ]                                      |
  [ ✕ Отмена v:cancel   ]                                      |
     |                                                          |
     | v:model:<id>  (from either B1 or B2) ←──────────────────
     v
[SCREEN C — SETTINGS]
  copy key: vid_settings_screen
  Shows: chosen model name, format (✅ marker), count (✅ marker), total price,
  user balance.
  -----------------------------------------------------------------------
  [ ✅ 16:9   v:fmt:land ] [ 9:16      v:fmt:port ]   (only two options)
  [ ✅ 1 вид  v:cnt:1    ] [ 2 вид.    v:cnt:2    ] [ 3 вид. v:cnt:3 ] [ 4 вид. v:cnt:4 ]
  [ ✨ Сгенерировать      v:go   ]
  [ ← Изменить модель    v:back:model ]
  [ ✕ Отмена             v:cancel     ]
  -----------------------------------------------------------------------
     |
     | v:go  (no pending_prompt yet)
     v
[SCREEN D — PROMPT ENTRY]
  Edit the wizard message to copy key: vid_ask_prompt
  bot sets vawait="prompt"
  user types plain text
     |
     | plain text received while vawait="prompt"
     v
[SCREEN E — GENERATING]
  New status message (not editing wizard — wizard msg is gone / was edited).
  Progress: vid_working → vid_generating_N (poll tick every ~15s)
  Duration: up to VIDEO_POLL_TIMEOUT (300s), progress every 3 poll ticks
     |
     | success: video bytes delivered
     v
[SCREEN F — RESULT]
  send_video per video, caption: vid_result_caption
  Per-video inline keyboard:
    [ ⬇ Скачать оригинал   v:dl:<vtoken> ]   (Phase 1 only)
  After all videos:
    short continuation menu (same as image: repeat_last / m:vid / m:balance)
     |
     | error during generation
     v
[SCREEN G — ERROR]
  edit status message to vid_gen_failed (credits refunded)
  [ 🔄 Попробовать снова  v:retry ] [ 🏠 Меню  m:menu ]
```

---

## 4. callback_data scheme

All video callbacks use prefix `v:`. Maximum token = 16 hex chars.

| callback_data | max bytes | purpose |
|---|---|---|
| `v:fam:omni` | 12 | pick Omni Flash family |
| `v:fam:veo` | 11 | pick Veo family |
| `v:model:omni-flash-4s` | 22 | pick specific variant (longest: 22 bytes) |
| `v:model:omni-flash-10s` | 23 | longest model id — within 64 byte limit |
| `v:model:veo-quality` | 20 | |
| `v:fmt:land` | 12 | choose landscape |
| `v:fmt:port` | 12 | choose portrait |
| `v:cnt:1` .. `v:cnt:4` | 8 | choose count 1–4 |
| `v:go` | 4 | confirm / go to prompt |
| `v:back:fam` | 11 | back to family picker |
| `v:back:model` | 13 | back to variant picker (remembers family) |
| `v:cancel` | 9 | cancel wizard, return to main menu |
| `v:retry` | 8 | retry after generation failure |
| `v:dl:<16-char-hex>` | 23 | download original video (per-result token) |

All values are well within the 64-byte limit. Model ids from `VIDEO_MODELS`
keys: longest is `omni-flash-10s` (14 chars), giving `v:model:omni-flash-10s`
= 23 bytes.

For Ingredients/Frames sub-flows (Phase 2):

| callback_data | max bytes | purpose |
|---|---|---|
| `v:ing:done` | 12 | signal "done adding images" in Ingredients flow |
| `v:ing:clear` | 13 | clear the Ingredients basket |
| `v:frm:slot:start` | 18 | tap to set/replace the Start frame |
| `v:frm:slot:end` | 16 | tap to set/replace the End frame |
| `v:frm:clear` | 13 | clear both frame slots |
| `v:frm:go` | 10 | confirm frames and enter prompt |

---

## 5. Per-user wizard_state keys (video flow)

These live in the existing `wizard_state[user_id]` dict alongside the image
wizard keys. They are prefixed with `v` to avoid collision. The image wizard
uses `step`, `count`, `fmt`, `await`, `last`, `pending_prompt`; the video
wizard MUST NOT reuse those keys.

```python
# Added to wizard_state[user_id] by the video wizard:
{
    # Which screen is active in the video wizard.
    # Values: "vfam" | "vmodel" | "vsettings" | "vprompt" | "vgenerating"
    "vstep": str,

    # Selected family: "omni-flash" | "veo"
    "vfamily": str,

    # Selected model id (key from VIDEO_MODELS), e.g. "omni-flash-4s"
    "vmodel": str,

    # Selected aspect: "land" | "port"
    "vfmt": str,            # default "land"

    # Number of videos: 1–4
    "vcount": int,          # default 1

    # What the message handler should do with the next plain-text message.
    # "vprompt"   — collect the generation prompt
    # "ving"      — Phase 2: next photo goes into Ingredients basket
    # "vfrm:start"— Phase 2: next photo sets the Start frame
    # "vfrm:end"  — Phase 2: next photo sets the End frame
    "vawait": str | None,

    # message_id of the active wizard message (for edit_text).
    "vmsg_id": int | None,

    # Saved settings for one-tap repeat (vid_repeat button).
    # {"model": str, "fmt": str, "count": int, "prompt": str}
    "vlast": dict | None,

    # Prompt typed in chat before tapping v:go (mirrors image pending_prompt).
    "vpending_prompt": str | None,

    # === Phase 2 only ===
    # "text" | "ingredients" | "frames"
    "vmode": str,           # default "text"

    # Ingredients basket: list of uploaded image source dicts (max 4).
    "ving_basket": list,    # default []

    # Frames: {"start": source_dict | None, "end": source_dict | None}
    "vfrm_slots": dict,
}
```

Isolation rule: entering `m:vid` calls a helper that clears all `v*` keys
from `wizard_state[user_id]` (preserving `vlast` for repeat), then sets
`vstep = "vfam"`. The image wizard keys (`step`, `count`, `fmt`, `await`,
`pending_prompt`) are left untouched. The image wizard's `w:cancel` clears
only image keys; `v:cancel` clears only video keys.

---

## 6. Transition table

### Phase 1 (text-to-video)

| Current state (vstep) | Event | Action | Next state |
|---|---|---|---|
| — (any) | `m:vid` pressed | clear v-keys (keep vlast), restore vlast defaults into vfmt/vcount if present | `vfam` |
| — (any) | `kb_vid` text | same as `m:vid` | `vfam` |
| `vfam` | `v:fam:omni` | store `vfamily="omni-flash"` | `vmodel` |
| `vfam` | `v:fam:veo` | store `vfamily="veo"` | `vmodel` |
| `vfam` | `v:cancel` | clear v-keys | main menu |
| `vmodel` | `v:model:<id>` | store `vmodel=<id>`, ensure vfmt/vcount defaults | `vsettings` |
| `vmodel` | `v:back:fam` | clear vmodel | `vfam` |
| `vmodel` | `v:cancel` | clear v-keys | main menu |
| `vsettings` | `v:fmt:land` | store vfmt | redraw settings (edit_text) |
| `vsettings` | `v:fmt:port` | store vfmt | redraw settings (edit_text) |
| `vsettings` | `v:cnt:N` | store vcount=N | redraw settings (edit_text) |
| `vsettings` | `v:go` + vpending_prompt set | clear vpending_prompt, run generation with stored settings | `vgenerating` |
| `vsettings` | `v:go` + no pending | set vawait="vprompt" | `vprompt` (edit msg to ask_prompt) |
| `vsettings` | `v:back:model` | stay on same family, clear vmodel | `vmodel` |
| `vsettings` | `v:cancel` | clear v-keys | main menu |
| `vprompt` | plain text received (vawait="vprompt") | clear vawait, run generation | `vgenerating` |
| `vprompt` | photo received (vawait="vprompt") | ignore photo, reply vid_text_only_hint | `vprompt` |
| `vgenerating` | generation success | send video(s) with buttons, send continuation menu | idle (clear vstep) |
| `vgenerating` | generation failure | edit status msg to vid_gen_failed + retry/menu buttons, issue refund | idle |
| `vgenerating` | any new wizard tap | answer callback "⏳ Генерация в процессе" (show_alert), do nothing | `vgenerating` |
| result shown | `v:dl:<token>` | fetch and send video as document | — |
| result shown | `v:retry` | re-enter with same vmodel/vfmt/vcount, go to vprompt | `vprompt` |

### Phase 2 — Ingredients (mark as BLOCKED-PENDING-CAPTURE)

| Current state | Event | Action | Next state |
|---|---|---|---|
| `vfam` | `v:fam:ing` | store vmode="ingredients", init ving_basket=[] | `ving_collect` |
| `ving_collect` | photo received (vawait="ving") | upload photo, append to ving_basket, edit status message | `ving_collect` |
| `ving_collect` | `v:ing:done` (basket >= 2) | answer `vid_gen_blocked`, clear video state, no charge | blocked |
| `ving_collect` | `v:ing:done` (basket < 2) | answer callback vid_ing_need_more (show_alert) | `ving_collect` |
| `ving_collect` | `v:ing:clear` | clear basket, edit status | `ving_collect` |
| `ving_collect` | `v:cancel` | clear v-keys | main menu |
| `ving_prompt` | plain text (vawait="ving_prompt") | obsolete until API capture; keep fail-closed | blocked |

### Phase 2 — Frames (mark as BLOCKED-PENDING-CAPTURE)

| Current state | Event | Action | Next state |
|---|---|---|---|
| `vfam` | `v:fam:frm` | store vmode="frames", init vfrm_slots={start:None,end:None} | `vfrm_collect` |
| `vfrm_collect` | `v:frm:slot:start` tap | set vawait="vfrm:start", edit msg to ask_start_frame | awaiting photo |
| `vfrm_collect` | `v:frm:slot:end` tap | set vawait="vfrm:end", edit msg to ask_end_frame | awaiting photo |
| awaiting vfrm photo | photo received | upload, store in vfrm_slots[start/end], redraw frame screen | `vfrm_collect` |
| awaiting vfrm photo | text received | ignore, reply vid_photo_expected | same |
| `vfrm_collect` | `v:frm:go` (both slots filled) | answer `vid_gen_blocked`, clear video state, no charge | blocked |
| `vfrm_collect` | `v:frm:go` (slot missing) | answer callback vid_frm_need_both (show_alert) | `vfrm_collect` |
| `vfrm_collect` | `v:frm:clear` | clear both slots, redraw | `vfrm_collect` |
| `vfrm_collect` | `v:cancel` | clear v-keys | main menu |
| `vfrm_prompt` | plain text (vawait="vfrm_prompt") | obsolete until API capture; keep fail-closed | blocked |

---

## 7. Screen text and keyboard layouts (detailed)

### Screen A — Family pick

```
Text: vid_family_screen
Keyboard:
  Row 1: [vid_fam:omni   "⚡ Omni Flash — быстро, дёшево"   v:fam:omni]
  Row 2: [vid_fam:veo    "✨ Veo 3.1 — лучшее качество"     v:fam:veo ]
  Row 3: [cancel         "✕ Отмена"                         v:cancel  ]
```

Phase 2 additions (when implemented): add rows for Ingredients and Frames
entry points above Cancel:
```
  [vid_fam:ing  "🖼 Ингредиенты (фото + текст)"   v:fam:ing]
  [vid_fam:frm  "🎞 Кадры (старт → финиш)"         v:fam:frm]
```

### Screen B — Variant pick

Dynamically built from `video_models_in_family(family)`. Each row is one
model. Button label = `vid_variant_label` rendered with duration, price:
`"{duration}с · {price} кр"`, with `✅ ` prefix if this model is currently
`vmodel`.

```
Text: vid_variant_screen (shows family name)
Keyboard (example, Omni Flash):
  Row 1: [4с · 7 кр    v:model:omni-flash-4s ]   ← ✅ if selected
  Row 2: [6с · 10 кр   v:model:omni-flash-6s ]
  Row 3: [8с · 12 кр   v:model:omni-flash-8s ]
  Row 4: [10с · 15 кр  v:model:omni-flash-10s]
  Row 5: [back          v:back:fam            ]
  Row 6: [cancel        v:cancel              ]
```

### Screen C — Settings

Helper `_vid_settings_text(user_id)` renders `vid_settings_screen` with:
- model display name (from `vid_model_name:<model_id>` copy key, fallback to model_id)
- fmt display string ("16:9" or "9:16")
- vcount
- total price via `video_price(vmodel, vcount)`
- user credit balance

Helper `video_wizard_kb(vmodel, vfmt, vcount)` returns:

```
Row 1: [✅ 16:9  v:fmt:land] [9:16  v:fmt:port]      (only two format buttons)
Row 2: [✅ 1    v:cnt:1] [2    v:cnt:2] [3    v:cnt:3] [4    v:cnt:4]
Row 3: [go       "✨ Сгенерировать"         v:go          ]
Row 4: [back     "← Изменить модель"        v:back:model  ]
Row 5: [cancel   "✕ Отмена"                v:cancel      ]
```

`✅ ` prefix applied via the existing `_sel()` helper (reuse unchanged).

### Screen D — Prompt entry (edit of wizard message)

After `v:go` edit the wizard message text to `vid_ask_prompt`. No keyboard —
the user types free text. Keep `vmsg_id` so we can delete it when generation
starts.

### Screen E — Generating (new message, not edit of wizard)

```python
status_msg = await message.answer(flow_copy.msg("vid_working"))
```

Progress edits (same pattern as image generation):
- on start: `vid_working`
- every 3 poll ticks: `vid_generating_elapsed` with elapsed seconds

The wizard message (`vmsg_id`) is deleted when generation starts (or on
`v:go` if a pending_prompt existed and we skip the prompt step).

### Screen F — Result (per video)

```python
await message.answer_video(
    video=BufferedInputFile(video_bytes, filename),
    caption=flow_copy.msg("vid_result_caption", i=i, n=n, prompt=prompt[:80]),
    reply_markup=video_result_kb(vtoken),
)
```

```python
def video_result_kb(vtoken: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=L("vid_dl"), callback_data=f"v:dl:{vtoken}")]
    ])
```

`vtoken` is a 16-char hex token from a new `VideoRef` registry (same pattern
as `ImageRegistry`; add a `VideoRegistry` in `flow_core.py` or reuse
`ImageRegistry` with a separate instance).

After all videos, send a continuation menu:

```
[vid_repeat_last  "🔁 Повторить так же"  v:repeat]
[m:vid            "🎬 Создать новое"     m:vid   ]
[m:balance        "💳 Баланс"            m:balance]
```

`v:repeat` replays `vlast` (model, fmt, count, prompt) without going through
the wizard — single-tap repeat. If `vlast` is None, `v:repeat` is not shown.

### Screen G — Error

Edit the status message:

```
Text: vid_gen_failed
Keyboard:
  [vid_retry  "🔄 Попробовать снова"  v:retry]
  [menu       "🏠 Главное меню"       m:menu ]
```

---

## 8. Ingredients collect screen (collect/upload implemented, generation blocked)

Current implementation uploads Telegram photos into Flow and stores their
`mediaGenerationId` values. Generation is still blocked intentionally: a live
2026-06-08 probe confirmed that guessed reference fields must not be sent to
`video:batchAsyncGenerateVideoText` without capturing the real contract.

```
SCREEN ING-A — collect
Text: vid_ing_screen (shows basket count, max 4)
  "🖼 Ингредиенты: добавь 2–4 фотографии.\n
   Пришли фото в чат. Добавлено: {n}/4."
Keyboard:
  [vid_ing_done   "Готово →"           v:ing:done  ]   (enabled only if n >= 2)
  [vid_ing_clear  "Очистить корзину"   v:ing:clear ]
  [cancel         "✕ Отмена"           v:cancel    ]
```

While `vawait = "ving"`, every photo the user sends is uploaded via
`keeper.upload_image()` and appended to `ving_basket`. The screen message
(identified by `vmsg_id`) is edited after each upload to reflect the new count.
Plain text while `vawait = "ving"` replies `vid_ing_send_photo` and is ignored.

When `v:ing:done` is tapped and basket >= 2:

- BLOCKED: bot replies `vid_gen_blocked`, clears the video state, and issues no
  charge. Do not transition to prompt entry until the real API contract is
  captured.

### PHASE 2 implementation gate

Before implementing the generation step, run `capture_video.py` with an
Ingredients-style request to obtain the `imageInputs`-equivalent field for
video. Document it in `flow_core.py` the same way `DEFAULT_EDIT_CAPTURE` is
documented for images.

---

## 9. Frames collect screen (collect/upload implemented, generation blocked)

Current implementation uploads Telegram photos into Flow and stores their
`mediaGenerationId` values. Generation is still blocked intentionally: a live
2026-06-08 probe showed that `startImage`/`endImage` are not valid fields for
`video:batchAsyncGenerateVideoText`.

```
SCREEN FRM-A — two-slot upload
Text: vid_frm_screen
  "🎞 Кадры: установи начальный и конечный кадр."
Keyboard:
  Row 1: [vid_frm_start  "🟢 Начальный кадр [установить]"   v:frm:slot:start]
          (or "🟢 Начальный кадр [✅ установлен]" when filled)
  Row 2: [vid_frm_end    "🔴 Конечный кадр [установить]"    v:frm:slot:end  ]
          (or "🔴 Конечный кадр [✅ установлен]" when filled)
  Row 3: [vid_frm_clear  "Очистить оба"                      v:frm:clear     ]
  Row 4: [vid_frm_go     "Далее →"                           v:frm:go        ]
          (greyed-out text only if either slot is empty — cannot disable inline
          buttons in Telegram; use show_alert in callback handler instead)
  Row 5: [cancel         "✕ Отмена"                          v:cancel        ]
```

When user taps a slot button:
1. Set `vawait = "vfrm:start"` or `"vfrm:end"`.
2. Edit the screen message to `vid_frm_send_photo_start` or `vid_frm_send_photo_end`.
3. User sends photo → upload → store in `vfrm_slots[start/end]` → redraw screen.

When `v:frm:go` tapped and both slots filled:
- BLOCKED: bot replies `vid_gen_blocked`, clears the video state, and issues no
  charge. Do not transition to prompt entry until the real API contract is
  captured.

### PHASE 2 implementation gate

Run `capture_video.py` with a Frames-style request. Field for start/end frame
inputs is unconfirmed — do not guess; update `flow_core.py` once captured.

---

## 10. In-flight / cooldown / busy edge cases

These mirror the image wizard exactly.

| Situation | Handling |
|---|---|
| User taps `m:vid` while `vstep = "vgenerating"` (their own generation) | `callback.answer(flow_copy.msg("vid_busy"), show_alert=True)` — do not start a new wizard |
| User taps `m:vid` while another user is generating | No conflict — state is per-user |
| User in `vstep = "vgenerating"` sends plain text | Ignore (no `vawait` set during generation) |
| `v:dl:<token>` after bot restart | Token not in VideoRegistry → `callback.answer(flow_copy.msg("expired"), show_alert=True)` |
| `v:model:*`, `v:fam:*`, etc. after bot restart | `vstep` is cleared on restart (in-memory) → these callbacks arrive with no `vstep`; handler checks: if `vstep` not in expected set, answer `flow_copy.msg("vid_expired_wizard")` and show main menu |
| Insufficient credits before `v:go` | The settings screen already shows the total price and balance. `v:go` check: if `credit_store.balance(uid) < video_price(vmodel, vcount)` → edit text to `low_balance` (reuse existing copy key) with topup/menu buttons, do NOT enter prompt step |
| Prompt too short (< 3 chars) | Same guard as image wizard: `vid_prompt_too_short` |
| Generation failure (HTTP error, timeout, status FAILED) | Refund via `credit_gate` pattern (charge.ok stays False), show Screen G |
| User sends photo while `vawait = "vprompt"` | Reply `vid_text_only_hint`, do not change state |
| User switches family during variant pick | `v:back:fam` clears `vmodel` and `vfamily`, edits to Screen A |
| `vcount x video_price` overflows credits after switching model | Settings screen always shows updated price; `v:go` re-checks balance |
| User already has image wizard open (`step = "wizard"`) and taps `kb_vid` | Video wizard clears only v-keys; image wizard is left in its current state. On next `kb_gen` or `m:gen` tap the image wizard resumes from stored image keys. No cross-contamination. |

---

## 11. Copy keys for `flow_copy.py`

The copywriter must fill all of these. English key → one-line description of
what the string should say (in Russian in the final file).

### LABELS (button text)

| Key | Description |
|---|---|
| `vid_gen` | Main menu button: "Create video" |
| `kb_vid` | Reply keyboard button: "Create video" |
| `vid_fam:omni` | Family picker button: Omni Flash, speed/price tagline |
| `vid_fam:veo` | Family picker button: Veo 3.1, quality tagline |
| `vid_fam:ing` | Phase 2: Ingredients mode entry button |
| `vid_fam:frm` | Phase 2: Frames mode entry button |
| `vid_go` | Settings confirm button: "Generate" |
| `vid_back:fam` | Back to family picker |
| `vid_back:model` | Back to variant picker |
| `vid_repeat_last` | Repeat last video settings in one tap |
| `vid_dl` | Per-video download button: "Download original" |
| `vid_retry` | Retry after failure button |
| `vid_ing_done` | Ingredients: "Done, enter prompt" |
| `vid_ing_clear` | Ingredients: "Clear basket" |
| `vid_frm_start` | Frames: "Set start frame" |
| `vid_frm_end` | Frames: "Set end frame" |
| `vid_frm_clear` | Frames: "Clear both frames" |
| `vid_frm_go` | Frames: "Next" (proceed to prompt) |
| `vid_model_name:omni-flash-4s` | Display name shown on settings screen |
| `vid_model_name:omni-flash-6s` | — |
| `vid_model_name:omni-flash-8s` | — |
| `vid_model_name:omni-flash-10s` | — |
| `vid_model_name:veo-lite` | — |
| `vid_model_name:veo-fast` | — |
| `vid_model_name:veo-quality` | — |

### MESSAGES (screen text / status)

| Key | Placeholders | Description |
|---|---|---|
| `vid_family_screen` | — | Screen A intro text, asks to choose style |
| `vid_variant_screen` | `{family}` | Screen B header showing family name |
| `vid_settings_screen` | `{model}`, `{fmt}`, `{count}`, `{price}`, `{credits}` | Screen C: current settings and price summary |
| `vid_ask_prompt` | — | Screen D: ask for descriptive prompt |
| `vid_working` | — | Generation start status |
| `vid_generating_elapsed` | `{elapsed}` | Periodic progress during poll |
| `vid_result_caption` | `{i}`, `{n}`, `{prompt}` | Caption under delivered video |
| `vid_gen_failed` | — | Generation error, credits returned |
| `vid_busy` | — | Alert: this user's generation is still running |
| `vid_expired_wizard` | — | Alert: wizard buttons from a previous session |
| `vid_prompt_too_short` | — | Prompt is too short |
| `vid_text_only_hint` | — | User sent photo during prompt step (text expected) |
| `vid_low_balance` | `{needed}`, `{have}` | Reuse existing `low_balance` key or alias it |
| `vid_ing_screen` | `{n}` | Ingredients screen showing basket count |
| `vid_ing_ask_prompt` | — | After basket filled, ask for text prompt |
| `vid_ing_send_photo` | — | Hint: send a photo (not text) to add to basket |
| `vid_ing_need_more` | — | Alert: need at least 2 images |
| `vid_gen_blocked` | — | Phase 2: generation not yet available for this mode |
| `vid_frm_screen` | — | Frames screen with slot status |
| `vid_frm_send_photo_start` | — | Prompt to send the start frame photo |
| `vid_frm_send_photo_end` | — | Prompt to send the end frame photo |
| `vid_frm_ask_prompt` | — | Optional directing prompt for frames mode |
| `vid_frm_need_both` | — | Alert: both slots must be filled |
| `vid_photo_expected` | — | User sent text when a photo was expected (frm slots) |

---

## 12. New data structures in flow_core.py

```python
# Add a VideoRef dataclass (mirror of ImageRef):
@dataclass(frozen=True)
class VideoRef:
    user_id: int
    project_id: str | None
    media_id: str          # video media_id for fetching bytes
    prompt: str = ""
    model_id: str = ""
    aspect_ratio: str = "landscape"

# Add a VideoRegistry instance in flow_bot.py:
video_registry = ImageRegistry(max_entries=2000)  # reuse ImageRegistry class
```

No new file needed — `ImageRegistry` is generic enough. Add `VideoRef` to
`flow_core.py` next to `ImageRef`.

---

## 13. Implementation order (suggested)

1. Add `kb_vid` to reply keyboard and `m:vid` to main menu. Wire both to a
   stub `show_video_family()` that shows Screen A.
2. Implement Screen A → B → C (family/variant/settings) with no generation.
   Verify edit_text navigation and ✅ markers.
3. Implement `v:go` → Screen D (prompt entry) → generation call →
   `generate_video()` in `FlowHttpClient` (already implemented) → Screen E
   progress → Screen F delivery.
4. Add `v:dl:<token>` handler using `fetch_video_bytes()` (already implemented).
5. Add `v:retry` and `vlast` repeat.
6. Phase 2: implement Ingredients UX; gate generation behind capture flag.
7. Phase 2: implement Frames UX; gate generation behind capture flag.
