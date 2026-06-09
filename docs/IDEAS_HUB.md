# IDEAS_HUB — UX Design Spec (three new features)

Design-only document. No code changes.
Authors: `telegram-ux-flow` agent + Claude Code.

---

## Callback-prefix registry (existing + new)

| Prefix | Owner |
|--------|-------|
| `w:`   | Image wizard (count / fmt / imodel / go / cancel) |
| `es:`  | Edit-settings picker (fmt / imodel / cancel) |
| `m:`   | Main-menu actions (gen / vid / balance / topup / help / myphoto / menu / pack / repeat) |
| `v:`   | Video wizard (fam / model / fmt / cnt / go / back / cancel / dl / edit / extend / retry / ing / frm / vmod) |
| `edit:` `vary:` `regen:` `dl:` `u2:` `ru:` | Per-image token actions |
| **`ih:`** | **Ideas Hub — top-level (new, Feature 1)** |
| **`tp:`** | **Template Q&A wizard (new, Feature 1-A)** |
| **`gp:`** | **Guided picker wizard (new, Feature 1-B)** |
| **`an:`** | **Animate / image-to-video (new, Feature 2)** |
| **`vu:`** | **Video-upload-to-edit wizard (new, Feature 3)** |

All new callback_data tokens stay within Telegram's 64-byte limit (verified in each section).

---

## FEATURE 1 — "💡 Идеи и шаблоны" Ideas Hub

### 1.0 Entry point

`main_menu_kb` gains one extra row (inserted after `vid_gen`, before `myphoto`/`balance`):

```
[💡 Идеи и шаблоны]   callback_data = "m:ideas"
```

`on_menu_action` handles `data == "m:ideas"` → `show_ideas_hub(msg, user_id=user_id, edit=True)`.

Metric event fired: `ideas_hub_opened`.

---

### 1.1 Screen: Ideas Hub root (`ih:root`)

**Text:**
```
💡 Идеи и шаблоны

Выбери, как хочешь начать:
```

**Keyboard:**

| Button | callback_data | Byte count |
|--------|--------------|-----------|
| 📦 Готовые решения | `ih:templates` | 13 |
| 🧭 Подбор по шагам | `ih:guided` | 11 |
| ← Назад | `m:menu` | 6 |

Builder function: `ideas_hub_kb() -> InlineKeyboardMarkup`

---

### 1.2 Branch A — "📦 Готовые решения" (Template Q&A)

#### Screen: Template picker (`ih:templates`)

Text: `📦 Выбери шаблон — я задам пару вопросов и составлю промпт:`

Keyboard — one button per template, plus Back:

| Button label (RU) | callback_data | Bytes |
|-------------------|--------------|-------|
| 🛒 Карточка товара | `tp:tpl:product_card` | 22 |
| 📢 Рекламный баннер | `tp:tpl:ad_banner` | 18 |
| 📸 UGC-креатив | `tp:tpl:ugc` | 11 |
| 📄 Обложка поста | `tp:tpl:post_cover` | 19 |
| 📱 Сторис | `tp:tpl:stories` | 15 |
| 🏷 Аватар бренда | `tp:tpl:brand_avatar` | 21 |
| 🎁 Фото товара на фоне | `tp:tpl:product_bg` | 19 |
| ← Назад | `ih:root` | 7 |

All under 64 bytes. Metric: `template_opened` fired when any `tp:tpl:*` is pressed (before Q&A starts).

#### Template Q&A flow (state machine)

`wizard_state[user_id]` gains keys under the `tp_` namespace so they cannot collide with `w:` or `v:` keys:

```python
{
    # Feature 1-A
    "tp_tpl":     str,         # template id, e.g. "product_card"
    "tp_step":    int,         # 0-based index of current question
    "tp_answers": list[str],   # collected answers (parallel to tpl questions list)
    "tp_msg_id":  int,         # message id of the active Q&A screen (for edit-in-place)
    "tp_await":   str | None,  # "text" when waiting for free-text input
    # Feature 1-B (guided picker)
    "gp_step":    int,
    "gp_answers": list[str],
    "gp_msg_id":  int,
}
```

#### Q&A screen layout

Each question is displayed by editing the SAME message (tp_msg_id). The text shows:

```
📦 {template_display_name}
Шаг {current}/{total}

{question_text}
```

Questions can be one of two types:

- **button** — a row of option buttons (callback `tp:ans:{option_key}`); chosen option turns green (`style="success"`).
- **free-text** — an answer button is absent; `tp_await="text"` is set; user types a reply; bot collects it and advances.
- **skip** — every free-text question has a `tp:skip` button that stores `""` as the answer and advances.

Common bottom row on every Q&A screen: `[← Назад  |  ✕ Отмена]`
- `tp:back` → decrement `tp_step`, re-render previous question (edit in place).
- `tp:cancel` → clear all `tp_*` keys, return to `ih:templates`.

Example: "Карточка товара" (`product_card`) — 4 questions:

| Step | Type | Question | Options (callback suffix after `tp:ans:`) |
|------|------|----------|------------------------------------------|
| 0 | free-text | "Что за товар? Опиши коротко." | — (tp:skip available) |
| 1 | button | "Какой фон нужен?" | `bg_white` "Белый" / `bg_life` "Lifestyle" / `bg_lux` "Luxury" / `bg_min` "Минимализм" |
| 2 | free-text | "Для кого этот товар? (можно пропустить)" | — (tp:skip available) |
| 3 | button | "Нужен ли текст на изображении?" | `txt_yes` "Да" / `txt_no` "Нет" |

After final question answered: immediately show the **Wizard confirm screen** (count/fmt/model picker), same as `show_wizard()`, with the composed prompt pre-filled as `pending_prompt`. The existing `w:go` path then generates. No extra confirmation step needed.

#### Prompt composition

When all answers are collected, the bot calls `_compose_template_prompt(tpl_id, answers) -> str`. This function loads the template skeleton from `/prompts/templates/{tpl_id}.txt`, then substitutes `{slot_0}`, `{slot_1}`, … with `answers[0]`, `answers[1]`, … (empty slots are dropped gracefully). The resulting string is stored in `wizard_state[user_id]["pending_prompt"]` and `show_wizard()` is called with `edit=False` (new message, because the Q&A message is now stale). Metric: `template_used` fired here.

#### PromptOps file layout

```
/prompts/
  templates/
    product_card.txt
    ad_banner.txt
    ugc.txt
    post_cover.txt
    stories.txt
    brand_avatar.txt
    product_bg.txt
  guided/
    skeleton.txt
```

Format of each template file (example `/prompts/templates/product_card.txt`):

```
# purpose: Marketplace product card
# inputs: {slot_0}=product_name, {slot_1}=background_style, {slot_2}=target_audience, {slot_3}=has_text
# outputs: a detailed image prompt for a product card photo
# version: 1
#
# example_dialog_1:
#   slot_0: ceramic mug
#   slot_1: Белый
#   slot_2: office workers
#   slot_3: Нет
#   result: Product card photo of a ceramic mug, pure white background, studio lighting,
#           sharp focus, marketplace style, no text overlay
#
# example_dialog_2:
#   slot_0: leather wallet
#   slot_1: Luxury
#   slot_2: men 30-50
#   slot_3: Да
#   result: Luxury leather wallet, dark velvet background, warm bokeh, text overlay area at bottom

Товар: {slot_0}. Стиль фона: {slot_1}. Аудитория: {slot_2}. Текст на фото: {slot_3}.
Профессиональная карточка товара для маркетплейса, чистый свет, резкий фокус на товаре.
```

The bot loads the file at startup (or lazily on first use) into a `dict[str, str]` cache. Changing a `.txt` file takes effect on next bot restart (or hot-reload if the team adds that).

The `_compose_template_prompt` helper strips the header comment block (lines starting with `#`) before substituting slots.

---

### 1.3 Branch B — "🧭 Подбор по шагам" (Guided Picker)

Linear 4-step wizard. State keys: `gp_step`, `gp_answers`, `gp_msg_id` (all in `wizard_state[user_id]`).

Each step edits the same message in place via `_vid_edit`-style helper (swallow "not modified").

#### Step 0 — "Что делаем?"

callback: user arrives from `ih:guided`
Text: `🧭 Шаг 1 из 4 — Что делаем?`

| Button | callback_data | Bytes |
|--------|--------------|-------|
| 🖼 Картинку | `gp:s0:image` | 13 |
| 📢 Рекламу | `gp:s0:ad` | 10 |
| 👤 Аватар | `gp:s0:avatar` | 14 |
| 📦 Товар | `gp:s0:product` | 15 |
| 🎬 Видео | `gp:s0:video` | 13 |
| ← Назад | `ih:root` | 7 |

If user picks `gp:s0:video`, transition immediately to `show_video_family()` (no further guided steps) and clear `gp_*` state. This avoids building a partial video wizard inside the image flow.

#### Step 1 — "Стиль?"

Text: `🧭 Шаг 2 из 4 — Стиль?`

| Button | callback_data | Bytes |
|--------|--------------|-------|
| 📷 Реализм | `gp:s1:realism` | 15 |
| 🧊 3D | `gp:s1:3d` | 10 |
| 💎 Luxury | `gp:s1:luxury` | 14 |
| 🎌 Anime | `gp:s1:anime` | 13 |
| 🎬 Cinematic | `gp:s1:cinema` | 14 |
| ◻️ Minimal | `gp:s1:minimal` | 15 |
| 🛒 Marketplace | `gp:s1:mktplace` | 16 |
| ← Назад | `gp:back:0` | 10 |
| ✕ Отмена | `gp:cancel` | 10 |

#### Step 2 — "Формат?"

Text: `🧭 Шаг 3 из 4 — Формат?`

| Button | callback_data | Bytes |
|--------|--------------|-------|
| ⬜ Квадрат | `gp:s2:sq` | 9 |
| 📱 Сторис 9:16 | `gp:s2:port` | 12 |
| 🖥 Баннер 16:9 | `gp:s2:land` | 12 |
| 👤 Аватар 1:1 | `gp:s2:sq` | — (same as square, alias handled in logic) |
| ← Назад | `gp:back:1` | 10 |
| ✕ Отмена | `gp:cancel` | 10 |

Note: "Аватар" and "Квадрат" both map to `gp:s2:sq`. The button text differs; the format value is the same. If `gp_answers[0]` is `avatar`, the wizard pre-selects `sq` in step 2 (green) and can skip directly to step 3 (implementer optimization, not required in MVP).

#### Step 3 — Count + Price (confirm screen)

Text: `🧭 Шаг 4 из 4 — Сколько вариантов?`

Price shown inline on each button (computed at render time from `price_gen(n)`):

| Button | callback_data | Bytes |
|--------|--------------|-------|
| 1 вариант — N кр | `gp:s3:1` | 9 |
| 4 варианта — M кр | `gp:s3:4` | 9 |
| ← Назад | `gp:back:2` | 10 |
| ✕ Отмена | `gp:cancel` | 10 |

On selection: bot composes the prompt from `/prompts/guided/skeleton.txt` by filling in `{subject}`, `{style}`, `{format_hint}` from `gp_answers[0..2]`, stores it in `wizard_state[user_id]["pending_prompt"]`, sets format from `gp_answers[2]` and count from `gp_answers[3]`, then calls `show_wizard()` (the normal image wizard, pre-loaded with those settings). `_reset_image_flow` is NOT called here — we want to keep the count/fmt/pending_prompt we just set.

`/prompts/guided/skeleton.txt` format (same header convention as templates):

```
# purpose: Guided picker generic image prompt
# inputs: {subject}=what, {style}=visual_style, {format_hint}=aspect_or_use
# version: 1
{subject}, {style} стиль, {format_hint}, высокое качество, детализация.
```

Metric: `guided_picker_completed` fired when count is chosen.

#### Back navigation for Guided Picker

`gp:back:N` decrements step to N (0-indexed), re-renders that step's screen by editing in place. Back from step 0 goes to `ih:root`. `gp:cancel` clears all `gp_*` keys and returns to `ih:root`.

#### Edge cases — Feature 1

| Situation | Handling |
|-----------|----------|
| User sends free text while `tp_await != "text"` | `handle_plain_text` checks `st.get("tp_await") == "text"` before routing; if it is, collect answer, advance step. If not `"text"`, fall through to normal logic. |
| User presses button answer while bot awaits text | `tp_await` is cleared when a button answer is received; if user sends text anyway, it is treated as a free-text answer for the same step (overwriting). |
| Expired Q&A message (bot restarted, `tp_tpl` missing) | `tp:ans:*` / `tp:back` / `tp:skip` handlers check `st.get("tp_tpl")`; if absent, answer `show_alert="Сессия истекла — начни заново"` and route to `ih:root`. |
| Empty free-text answer submitted (whitespace only) | Treated as skip (same as pressing `tp:skip`). |
| Bot restarted mid-wizard | `wizard_state` is in-memory only; keys are gone. Button callback arrives, `tp_tpl` is absent → expired handling above. |
| Prompt composed is too short (< 3 chars) | `_compose_template_prompt` always produces at least 20 chars from the template skeleton; no extra guard needed. |
| Insufficient credits at wizard screen | Normal `credit_gate` in `_generate_and_send` handles it with the topup keyboard. |

---

## FEATURE 2 — "Оживить фото" (image → video animate)

### 2.0 Entry points

**Entry A — button under every generated image**

`_image_keyboard(token)` gains one new row at the bottom (after download, before or replacing nothing):

| Button | callback_data | Bytes |
|--------|--------------|-------|
| 🎬 Оживить | `an:img:{token}` | `5 + 1 + 16 = 22` |

Token is 16-char hex (same as other per-image tokens). 22 bytes < 64.

**Entry B — main menu**

`main_menu_kb` gains a row (after `vid_gen`, before `ideas`):

```
[🎬 Оживить фото]   callback_data = "m:animate"
```

`on_menu_action` handles `data == "m:animate"` → `show_animate_upload(msg, user_id=user_id, edit=True)`.

### 2.1 Route A — from generated image (token known)

Handler: `on_animate_action`, filter `F.data.startswith("an:")`.

`data == f"an:img:{token}"`:
1. Look up `ref = image_registry.get(token)`. If `ref is None or ref.user_id != user_id` → `callback.answer(msg("expired"), show_alert=True)`.
2. Extract source dict from `ref.source`. This is the Flow mediaId dict that `build_video_reference_images` already accepts.
3. Pre-seed wizard state:

```python
_vid_clear(user_id)
st["vmode"] = "ingredients"
st["vmodel"] = VID_REF_DEFAULT_MODEL   # "veo-fast"
st["vfmt"]   = _aspect_to_vfmt(ref.aspect_ratio)
st["vcount"] = 1
st["ving_photos"] = [ref.source]       # pre-seeded; user skips photo upload
st["an_from_image"] = True             # flag: suppress "send photos" instructions
```

4. Call `show_animate_settings(callback.message, user_id=user_id, edit=True)`.

No photo upload step. Transition goes straight to the model/settings/prompt screen.

### 2.2 Route B — from main menu (no image yet)

Screen: **"Оживить фото — загрузи фото"**

Text:
```
🎬 Оживить фото

Пришли своё фото (можно сразу добавить подпись — она станет описанием видео).
```

State: `st["vawait"] = "an_photo"`, `st["vstep"] = "an_upload"`, `st["vmode"] = "ingredients"`.

Keyboard:
```
[✕ Отмена]   callback_data = "an:cancel"
```

`an:cancel` clears `an_*` + `v*` state, returns to main menu (edit).

When user sends a photo (`handle_photo` or new handler branch on `vawait == "an_photo"`):
- Download and upload via `keeper.upload_image` (same as existing `_upload_photo_source_from_message`).
- Store result in `st["ving_photos"] = [source]`.
- Store caption (if any) in `st["vcaption_prompt"]`.
- Call `show_animate_settings(message, user_id=user_id, edit=False)`.

Wrong input type (text sent while `vawait == "an_photo"`):
- Reply: "Жду фото, а не текст. Пришли изображение." (new copy key `an_send_photo`).

### 2.3 Screen: Animate Settings (`vstep = "an_settings"`)

This screen reuses the existing `ingredients_kb` keyboard verbatim (model row + fmt/count rows + Done / Clear / Back / Cancel). The only visual difference: title text changes to clarify the context.

Text:
```
🎬 Оживить фото

Модель: {model} · Формат: {fmt} · Кол-во: {count} шт.
💰 Цена: {price} кр.  ·  у тебя {credits} кр.

Выбери модель и настройки, потом нажми «✅ Готово» или сразу опиши движение в подписи.
```

Keyboard: `ingredients_kb(n=1, vfmt, vcount, vmodel, has_caption=bool(vcaption_prompt))` — identical function, unchanged.

All `v:vmod:*`, `v:fmt:*`, `v:cnt:*` callbacks work without modification because `_vid_rerender_settings` detects `vmode == "ingredients"` and calls `show_video_ingredients`. The only gap: `show_video_ingredients` renders `"vid_ing_screen"` copy (which mentions "фото"). We either reuse it as-is (acceptable MVP) or the copywriter adds an `"an_settings_screen"` variant behind a flag `st.get("an_from_image")`.

`v:ing:done` → already handled by existing `on_video_action`; it reads `st["ving_photos"]`, checks balance, then asks for prompt (or skips to generation if caption present). No changes needed.

`v:back:fam` → `_vid_clear(user_id)` + `show_main_menu`. (Back from animate goes to main menu, not family picker, because animate is not a member of the family picker hierarchy. The `v:back:fam` label still says "← Назад" which is correct.)

### 2.4 Generation

After prompt is submitted, `_video_generate_and_send` is called with:
- `vmode = "ingredients"`, `ving_photos = [ref.source]` (one image).
- Model chosen on the settings screen.
- `video_operation = "generate"` (not edit).

This is the existing `VIDEO_REFERENCE_ENDPOINT` path (`generate_video` with `reference_sources`). No new code in the generation layer.

Result keyboard: `video_result_kb(vtoken)` as usual (Download + Edit if eligible + Extend if eligible). Animate-sourced videos have `workflow_id` from the poll response and can be edited/extended normally.

### 2.5 Wizard state keys added by Feature 2

```python
"an_from_image": bool    # True when seeded from _image_keyboard, False/absent for menu route
# vawait="an_photo" added as a new valid value for existing vawait key
```

No other new keys. Feature 2 reuses the full `ving_*` + `v*` state already present.

### 2.6 `_image_keyboard` modification spec

Current rows:
```
[edit, vary]
[regen, realup]
[download]
```

New rows:
```
[edit, vary]
[regen, realup]
[animate]          ← new row; button label "🎬 Оживить · {price}⭐" where price = video_price("veo-fast", 1, "ingredients")
[download]
```

Price suffix on animate button is informational (cheapest model price); actual charge is determined at generation time by user's model choice.

### 2.7 Edge cases — Feature 2

| Situation | Handling |
|-----------|----------|
| Image token expired after bot restart | `image_registry.get(token)` returns `None` → `callback.answer(msg("expired"), show_alert=True)`. |
| User sends photo while already in animate upload AND has a pending image edit | `handle_photo` checks `vawait == "an_photo"` before checking `st.get("await") == "edit"`. Animate takes priority; the existing edit token is silently discarded. |
| User presses "Оживить" on two images quickly | Second press arrives with `vawait == "an_photo"` or `vstep == "an_settings"` already set. The new token overwrites `ving_photos[0]`. Acceptable — last action wins. |
| No reference-to-video capture available (ingredients path broken) | `_video_generate_and_send` returns error → credit refund + retry keyboard. Same as existing ingredients failure path. |
| User navigates away mid-upload (sends text, presses menu) | `vawait = "an_photo"` state is cleared by `_vid_clear` called from `m:menu` or `m:gen` handlers. |

---

## FEATURE 3 — "🎬 Изменить своё видео" (Upload-to-Edit)

### 3.0 Entry point

Added to the existing **video family screen** (`vid_family_screen`) as a new row:

| Button | callback_data | Bytes |
|--------|--------------|-------|
| 🎬 Изменить своё видео | `vu:start` | 8 |

`video_family_kb()` gains this row above the Cancel button.

Metric event on entry: `video_upload_edit_opened`.

### 3.1 Screen: Upload prompt (`vstep = "vu_upload"`)

`vu:start` handler:
1. `_vid_clear(user_id)`.
2. Set `st["vstep"] = "vu_upload"`, `st["vawait"] = "vu_video"`.
3. Edit the family screen message to:

```
🎬 Изменить своё видео

Пришли видеофайл (MP4, до 50 МБ).
Я загружу его и применю твои правки.

Продление загруженных видео недоступно.
```

Keyboard:
```
[← Назад]   callback_data = "vu:back"
[✕ Отмена]  callback_data = "vu:cancel"
```

`vu:back` → `show_video_family(msg, user_id=user_id, edit=True)`.
`vu:cancel` → `_vid_clear(user_id)` + `show_main_menu(edit=True)`.

### 3.2 Video upload handling

A new `handle_video` handler (or a new branch in existing `handle_photo`) listens for:
```python
F.video | (F.document & F.document.mime_type.startswith("video/"))
```

Guard: only act when `st.get("vawait") == "vu_video"`.

Steps:
1. Acknowledge with "⬆️ Загружаю видео…" status message.
2. Download via `bot.download(file_id)` (aiogram handles Telegram's 50 MB cap).
3. Upload via `keeper.upload_image(data, filename="upload.mp4")`.

   **Workflow-id problem:** `keeper.upload_image` returns a `{"mediaId": ..., "_project_id": ...}` dict. It does NOT return a `workflow_id`. The Flow video edit endpoint (`VIDEO_EDIT_ENDPOINT`) requires BOTH `source_media_id` AND `source_workflow_id`. An uploaded video file has no workflow_id in the Flow system because it was not generated by Flow.

   **Design decision:** The edit path via `build_video_edit_payload` requires `workflow_id`. Without it, the call will fail at the API layer with the existing guard:
   ```python
   if not (source_media_id and source_workflow_id):
       return {"error": flow_copy.msg("vid_extend_unavailable")}
   ```

   **Mitigation strategy (to be noted in HANDOFF.md):** Set `st["vu_no_workflow"] = True` when the upload yields no `workflow_id`. When the user later triggers generation, the bot should attempt the edit call anyway (using an empty string or `None` for `workflow_id`) and surface the error gracefully ("Редактирование загруженного видео пока недоступно — сервис требует видео, созданное через бот"). Alternatively, the implementer can capture a real upload-via-browser flow (similar to how `edit_capture.json` captures image editing) to discover if Flow assigns a workflow_id on video upload. Until that capture exists, this feature is partially blocked. The UX design proceeds assuming the capture will be resolved.

4. If upload succeeds: store `st["vu_source"] = {"media_id": source["mediaId"], "project_id": source.get("_project_id"), "workflow_id": None}`.
5. Delete status message. Transition to **Edit prompt screen**.

Wrong input type (text or photo while `vawait == "vu_video"`):
- Text: reply "Жду видеофайл, а не текст. Пришли MP4." (copy key `vu_send_video`).
- Photo: reply "Жду видео, а не фото. Для оживления фото — воспользуйся «Оживить фото»." (copy key `vu_send_video_not_photo`).

Upload failure (network, file too large, mediaId not captured):
- Show: "Не удалось загрузить видео. Попробуй ещё раз или пришли другой файл." (copy key `vu_upload_failed`).
- Keyboard: `[🔄 Попробовать снова  |  ✕ Отмена]` → `vu:retry_upload` / `vu:cancel`.

### 3.3 Screen: Edit prompt (`vstep = "vu_prompt"`)

Text:
```
✏️ Видео загружено!

Напиши, что изменить. Например:
«сделай фон в тёмных тонах» или «замедли движение».

Стоимость: {price} кр.
```

`price = action_price("video_prompt_edit")` (same as the existing video-edit action price).

State: `st["vawait"] = "vu_edit_prompt"`, `st["vstep"] = "vu_prompt"`.

Keyboard:
```
[← Назад]   callback_data = "vu:back_to_upload"
[✕ Отмена]  callback_data = "vu:cancel"
```

`vu:back_to_upload` → re-show the upload screen (edit in place), reset `st["vawait"] = "vu_video"`.

### 3.4 Generation

`handle_plain_text` gains a branch at the top of the `vawait` checks:

```python
if st.get("vawait") == "vu_edit_prompt":
    source = st.get("vu_source")
    # source = {"media_id": str, "project_id": str|None, "workflow_id": None}
    ...
```

1. Validate prompt (>= 3 chars).
2. Check balance: `action_price("video_prompt_edit")`.
3. Build a synthetic `VideoRef` from `vu_source`:

```python
ref = VideoRef(
    user_id=user_id,
    project_id=source["project_id"],
    media_id=source["media_id"],
    workflow_id=source.get("workflow_id"),   # likely None for uploaded videos
    prompt="uploaded video",
    model_id="veo-fast",                     # fallback; edit doesn't require specific model
    aspect_ratio="landscape",                # unknown for uploaded video; default
    mode="edit",
    prompt_edited=False,
    extend_index=0,
)
```

4. Set wizard state: `st["vmode"] = "edit"`, `st["vmodel"] = "veo-fast"`, `st["vfmt"] = "land"`, `st["vcount"] = 1`.
5. Call `_video_generate_and_send(message, text, user_id=user_id, unit_price_override=action_price("video_prompt_edit"), source_video=ref, video_operation="edit", status_text="✏️ Применяю правку к видео…")`.

If `workflow_id is None`, `generate_video` inside `client` will hit the guard and return `{"error": vid_extend_unavailable}`. The caller (`_video_generate_and_send`) surfaces the error via the existing fail-retry keyboard. Credits are NOT charged (the fail path refunds before sending the error). User sees: "Редактирование пока недоступно — кредиты не списаны."

### 3.5 Result keyboard

After successful generation, `video_result_kb(vtoken)` is rendered. For uploaded-video edits:
- `_video_can_edit(ref)` → True if workflow_id present (likely False for first upload).
- `_video_can_extend(ref)` → False (model is `veo-fast` but we explicitly do NOT want extend for uploaded videos; flag: `ref.prompt_edited = True` which already blocks extend via `_video_can_extend`).

So the result keyboard shows only:
```
[⬇ Скачать видео]
[✏️ Изменить]     (only if workflow_id obtained from generation result)
```

No "Продлить" button. This matches the spec requirement.

If the generation result returns a `workflow_id` (the service assigned one to the edited output), that new VideoRef CAN be edited further (the edited output has a real workflow_id). This is correct behaviour.

### 3.6 Wizard state keys added by Feature 3

```python
"vstep":           "vu_upload" | "vu_prompt"    # re-uses existing vstep key
"vawait":          "vu_video" | "vu_edit_prompt" # new vawait values
"vu_source":       {"media_id": str, "project_id": str|None, "workflow_id": str|None}
"vu_no_workflow":  bool    # advisory flag for implementer debugging
```

`_vid_clear(user_id)` already drops all `v*` keys via the `key.startswith("v")` loop. Since `vu_*` starts with `v`, these keys ARE cleared by `_vid_clear`. This is correct — cancelling the video wizard cleans up upload state too.

### 3.7 Edge cases — Feature 3

| Situation | Handling |
|-----------|----------|
| File too large (> 50 MB, Telegram bot limit) | Telegram rejects the download at `bot.download` with an exception; handler catches it and shows `vu_upload_failed`. |
| Non-video file sent as document | `F.document.mime_type.startswith("video/")` filter rejects it; falls through to generic handler. |
| User sends photo while `vawait == "vu_video"` | `handle_photo` checks `vawait == "vu_video"` BEFORE the normal photo handler. Shows `vu_send_video_not_photo`. |
| Bot restarted mid-upload (`vu_source` lost) | `vawait == "vu_edit_prompt"` but `vu_source` is absent → show alert "Сессия истекла — загрузи видео заново" and route to `show_video_family`. |
| User presses "Изменить" on uploaded-edit result when workflow_id absent | `_video_can_edit` returns False; button is not shown in the keyboard. |
| Extend shown accidentally | `ref.prompt_edited = True` for all uploaded-video edits, which is the condition `_video_can_extend` already uses to block extend. No additional guard needed. |
| `generate_video` returns workflow_id for the edited clip | The new `VideoRef` stored in `video_registry` will have `workflow_id` set, enabling a further edit button on the result. Extend remains blocked by `prompt_edited=True`. |

---

## Per-user state shape summary (all three features combined)

```python
wizard_state[user_id] = {
    # ── existing image wizard ──────────────────
    "step":           str | None,
    "count":          int,
    "fmt":            str,
    "imodel":         str,
    "await":          str | None,    # "prompt" | "edit" | "revary" | "photo"
    "pending_prompt": str | None,
    "last":           dict | None,
    "edit_fmt":       str,
    "edit_imodel":    str,

    # ── existing video wizard ──────────────────
    "vstep":          str | None,
    "vawait":         str | None,    # extended with "an_photo", "vu_video", "vu_edit_prompt"
    "vmode":          str,
    "vmodel":         str | None,
    "vfmt":           str,
    "vcount":         int,
    "vfamily":        str | None,
    "vmsg_id":        int | None,
    "ving_photos":    list[dict],
    "vfrm_start":     dict | None,
    "vfrm_end":       dict | None,
    "vcaption_prompt":str | None,
    "vpending_prompt":str | None,
    "vedit_token":    str | None,
    "vextend_token":  str | None,
    "vlast":          dict | None,
    "vretry":         dict | None,

    # ── Feature 1-A: template Q&A ─────────────
    "tp_tpl":         str | None,    # e.g. "product_card"
    "tp_step":        int,           # current question index
    "tp_answers":     list[str],
    "tp_msg_id":      int | None,
    "tp_await":       str | None,    # "text" when awaiting free-text answer

    # ── Feature 1-B: guided picker ────────────
    "gp_step":        int,
    "gp_answers":     list[str],
    "gp_msg_id":      int | None,

    # ── Feature 2: animate ────────────────────
    "an_from_image":  bool,          # True = seeded from image keyboard

    # ── Feature 3: video upload-to-edit ───────
    "vu_source":      dict | None,   # {"media_id", "project_id", "workflow_id"}
    "vu_no_workflow": bool,          # advisory flag
}
```

---

## Metric events to fire

| Event name | Fires when |
|------------|-----------|
| `ideas_hub_opened` | `m:ideas` callback received |
| `template_opened` | `tp:tpl:*` callback received (before Q&A) |
| `template_used` | Prompt composed and `pending_prompt` stored |
| `guided_picker_completed` | Count chosen in step 3 of guided picker |
| `animate_from_image` | `an:img:*` callback received (Route A) |
| `animate_from_menu` | `m:animate` callback received (Route B) |
| `video_upload_edit_opened` | `vu:start` callback received |
| `video_upload_success` | Video file uploaded, mediaId captured |
| `video_upload_failed` | Upload attempt yielded no mediaId |

All events use the existing `metrics.log_event(name, user_id=..., source=...)` signature.

---

## Handler registration order (important for no-collision routing)

New callbacks must be registered BEFORE the catch-all `on_image_action` (which has no `startswith` filter). Recommended order:

1. `F.data.startswith("ih:")` → `on_ideas_hub_action`
2. `F.data.startswith("tp:")` → `on_template_action`
3. `F.data.startswith("gp:")` → `on_guided_picker_action`
4. `F.data.startswith("an:")` → `on_animate_action`
5. `F.data.startswith("vu:")` → `on_video_upload_action`
6. existing `m:`, `es:`, `w:`, `v:` handlers (already registered)
7. catch-all `on_image_action` last

---

## Byte-count verification for longest callback_data tokens

| callback_data | Bytes (UTF-8) |
|---------------|-------------|
| `tp:tpl:product_card` | 20 |
| `tp:tpl:brand_avatar` | 21 |
| `an:img:` + 16-hex-char token | 24 |
| `gp:back:2` | 10 |
| `vu:back_to_upload` | 18 |
| `vu:retry_upload` | 16 |

All well within 64 bytes.
