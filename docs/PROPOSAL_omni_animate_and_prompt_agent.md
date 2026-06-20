# PROPOSAL: Omni Flash photo-animate + in-bot prompt-improve agent

Status: design only, not implemented. No production code changed by this doc.
Author: product-architect agent. Date: 2026-06-20.

This proposal grounds every claim in current code (file/line refs are exact at
the time of writing; re-check before implementing since the tree moves fast —
3 video-routing commits landed today alone). Where a claim is unverified it is
called out explicitly in "Open questions" rather than asserted as fact.

---

## FEATURE 1 — Let users animate a photo with Omni Flash OR Veo, keep Extend Veo-only

### 1.1 Restate goal + assumptions

Goal: today, attaching a photo to the new video wizard always forces Veo
(`_nwiz_model`, flow_bot.py:4779-4783). Google Flow itself now supports
animating an image with Omni Flash too (cheaper, faster, shorter clips). We
want the user to pick the engine (Omni Flash vs Veo) when a photo is attached,
each with its own duration/quality controls and its own price, while keeping
the hard rule "Extend only works on Veo-sourced videos" intact.

Assumptions (explicit):
- The `abra_r2v_{duration}s` r2v key for Omni Flash is **already live, captured,
  and unit-tested today** (flow_core.py:1005-1017, commit `304fab0` "Family-aware
  r2v key + surface audio/danger video filters", `tests/test_flow_video.py:209-215`).
  This is more current than `docs/MONETIZATION.md` line 100-102, which still says
  "Current reference-to-video is Veo-only" and "Do not advertise Omni Flash
  reference videos until an Omni reference-to-video key is captured" — **that doc
  is stale and must be updated alongside this feature**, not treated as a blocker.
- Durations confirmed for Omni Flash r2v are inherited from the existing
  `VIDEO_MODELS` catalog duration field (4/6/8/10s) — but only the **4s** key
  (`abra_t2v_4s`/`abra_r2v_4s`) is marked `"confirmed": True` in the catalog
  (flow_core.py:1053). The 6/8/10s variants are `"confirmed": False`
  (pattern-inferred). This proposal does NOT change that confirmation status;
  it only adds UI to reach the keys that already exist in code.
- `_video_can_extend` already gates on the **friendly** `model_id` stored on
  `VideoRef` (flow_core.py:1863, `model_id: str = ""`), not on the raw Google
  `videoModelKey`. Friendly ids for Omni are always `omni-flash-{4,6,8,10}s`
  (never start with `"veo-"`), so the existing gate
  (`str(ref.model_id).startswith("veo-")`, flow_bot.py:4586) **already correctly
  excludes any Omni-animated video from Extend with zero code changes** — this
  is the load-bearing fact behind the whole feature design.

Unknowns (see "Open questions" for the full list):
- Exact live-confirmed `abra_r2v_6s/8s/10s` and `veo_3_1_t2v_fast/quality`
  behavior beyond 4s/lite — pattern-inferred only.
- Whether Omni r2v has its own duration ceiling tied to the source image
  (Flow's web UI may clamp longer Omni animate durations differently than
  pure text-to-video; unverified for r2v specifically).

### 1.2 Current architecture (as found)

```
Telegram photo / "Оживить фото" (an:img:*)
        │
        ▼
flow_bot.py: show_video_prompt_input()/show_new_video_wizard()  ("new wizard", v:n* callbacks)
        │  st["vphoto"] = <source>; st["vmode"] = "ingredients"
        ▼
_nwiz_model(st)            <-- HARD-CODES Veo whenever st["vphoto"] is set (4779-4783)
        │
        ▼
_nwiz_kb(user_id)          <-- when has_photo: only shows a Veo quality toggle (4840-4846)
        │
        ▼
v:ngo callback (8920-8951) <-- st["vmodel"] = _nwiz_model(st); st["vmode"]="ingredients"
        │
        ▼
_video_generate_and_send -> _do_video_generate_and_send (9292+)
        │  model_key = model_id (the FRIENDLY id, e.g. "veo-lite")
        ▼
FlowHttpClient.generate_video(model_key=..., reference_sources=[...])  (2596+)
        │  effective_model_key = video_reference_model_key(model_key, aspect)  (2667-2668)
        │    -> veo family:  veo_3_1_r2v_{tier}
        │    -> omni family: abra_r2v_{duration}s        <-- ALREADY WORKS, just unreachable from UI
        ▼
VideoRef(model_id=model_key, ...)   <-- friendly id persisted (e.g. "veo-lite" / "omni-flash-6s")
        │
        ▼
video_result_kb(vtoken) -> _video_can_extend(ref)
        │  str(ref.model_id).startswith("veo-")          <-- ALREADY excludes omni-* correctly
        ▼
"➕ Продлить" button shown only for veo-* sources
```

There is also a SECOND, older video entry path (`v:fam:`/`v:vmod:`,
`show_video_ingredients`, `ingredients_kb`, flow_bot.py:4641-4665) that already
lets the user pick Veo lite/fast/quality for Ingredients mode via
`_vid_model_row("ingredients", vmodel)` (4615-4623). That path is NOT currently
reachable from "Оживить фото" or the main "Создать видео" entry (both route
into the new prompt-first wizard per flow_bot.py:8037, 8039, 10964) — it is
reachable today only via lower-level video-family navigation. This proposal
extends the **new** wizard (the one users actually reach) rather than trying
to surface the old one, to avoid maintaining two divergent UX trees.

### 1.3 Design: engine toggle on the new wizard

Add an **engine** axis (`vengine`: `"omni"` | `"veo"`) to wizard state,
orthogonal to the existing `vphoto` presence check. Today `_nwiz_model`
conflates "has a photo" with "must be Veo" — split that into two independent
choices: (a) is this image-animate (has photo) or text-to-video (no photo),
and (b) which engine, with engine choice visible ONLY in the image-animate
branch (text-to-video already lets the user pick Omni durations; Veo
text-to-video for the new wizard is out of scope here — no change requested).

```python
# flow_bot.py — replace _nwiz_model (4779-4783)
_VID_OMNI_R2V_DURATIONS = [4, 6, 8]   # drop 10s from r2v UI until captured for r2v specifically (open question)

def _nwiz_model(st: dict) -> str:
    """Модель для нового wizard на основе текущего состояния."""
    if st.get("vphoto"):
        engine = st.get("vengine", "veo")  # default unchanged: existing users keep today's behavior
        if engine == "omni":
            return _VID_OMNI_DUR_MODEL.get(st.get("vdur", 4), "omni-flash-4s")
        return _VID_VEO_QUAL_MODEL.get(st.get("vquality", "lite"), "veo-lite")
    return _VID_OMNI_DUR_MODEL.get(st.get("vdur", 4), "omni-flash-4s")
```

`_nwiz_kb` (4822-4865): when `has_photo`, add an engine toggle row ABOVE the
existing quality/duration row, and make that row's content conditional on
`vengine`:

```python
if has_photo:
    engine = st.get("vengine", "veo")
    next_engine = "veo" if engine == "omni" else "omni"
    engine_label = "⚡ Omni Flash" if engine == "omni" else "🎥 Veo"
    rows.append([B(text=f"🔁 Сменить движок · {engine_label}", callback_data=f"v:nengine:{next_engine}")])
    if engine == "omni":
        rows.append([_sel_btn(f"{d}с", st.get("vdur", 4) == d, f"v:ndur:{d}")
                     for d in _VID_OMNI_R2V_DURATIONS])
    else:
        # existing Veo quality cycle row, unchanged
        ...
```

New callback handler `v:nengine:{omni|veo}` (alongside the existing `v:n*`
block at flow_bot.py:8849-8955):

```python
if data.startswith("v:nengine:"):
    eng = data.split(":", 2)[2]
    if eng in ("omni", "veo"):
        st["vengine"] = eng
        if eng == "omni" and st.get("vdur", 4) not in _VID_OMNI_R2V_DURATIONS:
            st["vdur"] = 4
    await callback.answer()
    await show_new_video_wizard(msg, user_id=user_id, edit=True)
    return
```

`show_new_video_wizard` (4917+) already calls `st["vmodel"] = _nwiz_model(st)`
on entry (4923) — no change needed there beyond the function it calls.
`_nwiz_text` (4792-4819) needs the same has_photo branch split so the
displayed "details" line shows duration for Omni and quality for Veo:

```python
if has_photo:
    engine = st.get("vengine", "veo")
    if engine == "omni":
        dur = st.get("vdur", 4)
        details = f"Формат: {_VID_FMT_NAMES.get(vfmt, vfmt)} · ⚡ Omni {dur}с"
    else:
        quality = st.get("vquality", "lite")
        q_name = _VID_VEO_QUAL_NAMES.get(quality, "Lite")
        details = f"Формат: {_VID_FMT_NAMES.get(vfmt, vfmt)} · 🎥 Veo {q_name}"
```

**Default engine**: default `vengine` to `"veo"` when unset, so existing users
(and any in-flight wizard state across a deploy) see byte-identical behavior to
today unless they explicitly tap the new engine toggle. This is a pure
additive change — nothing about the current Veo-animate path regresses.

**Entry points that set `st["vphoto"]` directly must also default `vengine`**:
`on_animate_action` (`an:img:*`, flow_bot.py:8188-8217, sets `vmodel` at 8203)
and the photo-in-wizard handler (flow_bot.py:10720-10740, sets `vmodel` at
10738) both call `_nwiz_model(st)` already — they get the new engine logic for
free as long as `st.setdefault("vengine", "veo")` is added next to their
existing `st.setdefault(...)` calls (8204-8207, and equivalent block before
10738 if missing).

### 1.4 Hard constraint verification: Extend stays Veo-only

Confirmed exactly where this is enforced — **no change required here**:

- `VIDEO_EXTEND_MODEL = "veo-lite"` (flow_bot.py:4577) — extend always runs on
  veo-lite regardless of source tier (existing, unrelated to this feature).
- `_video_can_extend(ref)` (flow_bot.py:4580-4588):
  ```python
  return bool(
      ref and ref.media_id and ref.project_id and ref.workflow_id
      and str(ref.model_id).startswith("veo-")
      and not ref.prompt_edited
  )
  ```
  `ref.model_id` is set from the friendly id at generation time
  (`VideoRef(model_id=model_id, ...)`, flow_bot.py:9611, where `model_id =
  st.get("vmodel", "omni-flash-4s")`, flow_bot.py:9311). Friendly Omni ids are
  always `"omni-flash-{n}s"` — never `"veo-*"`. So an Omni-animated photo
  result will have `ref.model_id == "omni-flash-6s"` (etc.), `startswith("veo-")`
  is `False`, **Extend is never offered**. `video_result_kb` (4591-4608) then
  shows ONLY the "✏️ Изменить" row (if `_video_can_edit` passes) or falls back
  to the menu button — confirmed by the existing regression test
  `tests/test_flow_menu.py:1734-1738` (`omni = VR(..., model_id="omni-flash-4s",
  ...)`; `assertFalse(fb._video_can_extend(omni))`), which already covers this
  exact case for text-to-video Omni and needs no change to also cover
  Omni r2v (the gate doesn't look at `mode`, only `model_id`).
- No code path constructs a `videoModelKey` like `veo_3_1_extension_*` for an
  Omni source: `video_extend_model_key` (flow_core.py:1025-1032) is only ever
  called from the extend branch of `generate_video`/`build_video_extend_payload`,
  which is only reachable via the `v:extend:*` callback, which is only shown
  when `_video_can_extend` returns `True`. There is no "extend omni" key in
  `VIDEO_MODEL_KEYS` or `VIDEO_MODELS` to accidentally resolve to — Omni simply
  has no extension key in the catalog, so even a forced/buggy call into
  `video_extend_model_key("omni-flash-6s")` would fall through `_veo_tier`'s
  default (`"fast"`) and produce a nonsensical `veo_3_1_extension_fast` key
  that the API would reject — belt-and-suspenders, but the real gate is the
  button visibility above. **No new test is needed to "prove" omni can't
  extend; the existing test already proves it.** Add one new test only to
  prove the *r2v-specific* path produces a non-`veo-*` `model_id` (see 1.7).

### 1.5 Pricing

`video_price(model_id, num_videos, mode)` (flow_core.py:1103-1117) already
prices Omni cheaper than Veo and already applies the Ingredients surcharge
uniformly:

| Model | Base (text) | + Ingredients surcharge (+15) |
|---|---:|---:|
| omni-flash-4s | 50 | 65 |
| omni-flash-6s | 70 | 85 |
| omni-flash-8s | 85 | 100 |
| veo-lite | 60 | 75 |
| veo-fast | 120 | 135 |
| veo-quality | 450 | 465 |

No pricing code change needed — `_nwiz_price` (flow_bot.py:4786-4789) already
calls `video_price(mid, 1, vmode)` with `vmode = "ingredients" if
st.get("vphoto") else "text"`, and `mid = _nwiz_model(st)` will now correctly
resolve to an Omni id when `vengine == "omni"`. The price shown on the
"🎬 Создать" button (4862) and in `_nwiz_text` (4818) updates automatically.

**docs/MONETIZATION.md must be updated** (separate small change, not code):
remove "Current reference-to-video is Veo-only" (line 99-102) and add an Omni
r2v row to the reference-and-edit-surcharges table reflecting 65/85/100.

### 1.6 Copy (flow_copy.py)

No new top-level screens are needed (the new wizard is reused as-is); add two
label keys near the existing `vid_model_name:*` block (flow_copy.py:110-116):

```python
"vid_engine_omni": "⚡ Omni Flash",
"vid_engine_veo":  "🎥 Veo",
```

Use these instead of hardcoding the Russian text inline in `_nwiz_kb`, per the
repo's "labels live in flow_copy, not inline" convention used everywhere else
in that file (see `L("fmt:land")`, `L("cancel")` usage in the same functions).

### 1.7 Credit wiring

No new credit code: `credit_store.charge`/`refund` flow through
`_do_video_generate_and_send` exactly as today (flow_bot.py:9400, 9453); the
only change is which `model_id` lands in `st["vmodel"]` before that function
reads it (line 9311). Charge-on-success/refund-on-failure is already in place
and untouched.

### 1.8 Task breakdown (strict order)

1. **Add `vengine` defaulting** in the three entry points that set
   `st["vphoto"]` (an:img: handler 8188-8217, new-wizard photo intake
   10720-10740, plus any guided-picker/template path that pre-fills a video
   photo — grep `st\["vphoto"\]\s*=` to enumerate all writers before editing).
   Verify: `python -m py_compile flow_bot.py`.
2. **Split `_nwiz_model`** to branch on `vengine` per 1.3. Verify: existing
   `tests/test_flow_menu.py` and `tests/test_flow_video.py` still pass
   (`python -m unittest discover -s tests -p "test_flow_menu.py"` and
   `test_flow_video.py`) — these exercise `_nwiz_model`/`_video_can_extend`
   indirectly and must not regress.
3. **Add the `v:nengine:*` callback** and engine-toggle button in `_nwiz_kb`.
   Verify: a new offline test constructs `wizard_state` with `vphoto` set,
   simulates the callback data string, and asserts `st["vmodel"]` switches
   between `omni-flash-4s` and `veo-lite` (no network — these are pure
   dict/string operations, follow the pattern of
   `tests/test_flow_menu.py:1727-1738`).
4. **Update `_nwiz_text`** branch per 1.3 (details line). Verify: new test
   asserts the rendered text contains "Omni" when `vengine=="omni"` and "Veo"
   when not, e.g. `assertIn("Omni", fb._nwiz_text(uid))` after setting state
   directly (mirrors existing direct-state-manipulation tests in
   `test_flow_menu.py`).
5. **Add `vid_engine_omni`/`vid_engine_veo` to flow_copy.py** and swap the
   inline strings in `_nwiz_kb` for `L(...)` calls. Verify: the existing
   flow_copy backend-neutrality test (the one that forbids "flow"/"google"/
   "капч" — see flow_bot_architecture memory) still passes; run
   `python -m unittest discover -s tests -p "test_flow_menu.py"` to catch any
   copy-coverage test.
6. **New regression test**: confirm `video_reference_model_key("omni-flash-6s",
   "portrait")` still returns `"abra_r2v_6s"` AND that a `VideoRef` built from
   an Omni r2v generation (`mode="ingredients"`, `model_id="omni-flash-6s"`)
   fails `_video_can_extend` — this nails down the Feature-1 hard constraint
   end-to-end (not just the existing text-to-video case). Add alongside
   `tests/test_flow_menu.py:1727-1738` or as a new method in the same test
   class. Verify: `python -m unittest discover -s tests -p "test_flow_menu.py"`.
7. **Docs**: update `docs/MONETIZATION.md` §"Videos" per 1.5 and
   `docs/VIDEO_UX.md` to describe the engine toggle. Verify: no code check,
   manual read-through; this is the only step that touches a non-code file
   besides this proposal.
8. **(Approval-gated, NOT part of offline verification)** A live
   `tools/capture_video.py --ingredients` run for `omni-flash-6s`/`8s` to move
   `"confirmed": False` to `True` in `VIDEO_MODELS` r2v rows specifically (the
   current `confirmed` flag tracks the *text-to-video* key, not r2v — see
   Open Questions). Do not run without explicit operator approval per
   `CLAUDE.md`/`VALIDATION.md`.

### 1.9 Failure cases

- **User toggles engine after already typing a prompt, then attaches/removes
  photo** — `v:nremove_photo` (8904-8910) already resets `vmode`/`vmodel` via
  `_nwiz_model(st)`; it does NOT currently reset `vengine`. Harmless (engine
  is only read when `vphoto` is set again), but to avoid a stale `vengine`
  silently resurrecting Omni after the user explicitly picked Veo for a
  *previous* photo in the same session, reset `st.pop("vengine", None)` in the
  remove-photo handler too — cheap defensive fix, add to step 1.
- **Omni r2v 4xx/403 the same way Veo r2v can** — already handled generically:
  `generate_video`'s retry/backoff/account-failover logic (flow_bot.py:2700+)
  doesn't branch on model family for error handling, only for payload shape.
  No new failure-handling code needed.
- **User picks Omni engine, the duration UI offers 8s but the live capture for
  `abra_r2v_8s` turns out wrong** — bounded by `"confirmed": False` already
  existing in the catalog; this is a pre-existing risk for ALL unconfirmed
  durations (even text-to-video), not unique to this feature. Mitigate by
  restricting the r2v duration buttons to `[4, 6, 8]` (1.3) and treating 10s as
  text-to-video-only until r2v-specifically captured, OR ship all four and
  rely on the existing `_fail_retry`/refund path (9450+) if the API rejects an
  unconfirmed duration — refund-on-failure already protects the user's
  credits either way.
- **Stale wizard state from before this change reaches `_nwiz_model` without
  `vengine` set** — `st.get("vengine", "veo")` defaults to Veo, preserving
  exact current behavior for in-flight sessions across a deploy. No migration
  needed (in-memory `wizard_state`, not persisted, so a restart clears it
  anyway per the existing `defaultdict` pattern, flow_bot.py:3403).

---

## FEATURE 2 — In-bot prompt-improve agent ("🤖 Агент", 5 credits)

### 2.1 Restate goal + assumptions

Goal: add a button that puts the user into "agent mode"; their next message(s)
go to Google Flow's own `flowCreationAgent:streamChat` endpoint instead of
straight to image/video generation. The headline action, "Улучшить промпт", is
charged 5 credits and returns either 3 prompt variants (buttons) or one
improved prompt, which the user can accept (it becomes their working prompt in
the image/video wizard), edit, or cancel.

Assumptions:
- The endpoint contract given in the task is the ground truth for THIS
  proposal (it was captured from F12, not from this repo) — no file in this
  repo currently calls `flowCreationAgent`. This is genuinely new integration
  surface, not a refactor of existing code.
- Auth/recaptcha plumbing (bearer, browser-JS solver, project/session ids) is
  fully reusable from `FlowHttpClient`/`SessionKeeper` as verified below — no
  new auth mechanism needed.
- SSE parsing is new: nothing in this codebase currently parses a streaming
  response (`generate_video`/`generate_images` are both single-shot POST +
  `await resp.text()` or `.json()`, e.g. flow_bot.py:2767-2777). This is the
  one genuinely unfamiliar piece — per the user's "no magic tech" rule, treat
  SSE parsing as a **mini-spike**: a 15-line offline unit test that feeds a
  canned multi-chunk `data: {...}` byte stream into the parser and asserts the
  concatenated JSON comes out right, BEFORE wiring it to a real network call.
  This proposal includes that test in the task breakdown (step 2) precisely so
  the spike happens before any real HTTP code is written.

Unknowns (see "Open questions"): the exact reCAPTCHA `action` string the
endpoint expects/validates server-side (the task notes
`RECAPTCHA_APPLICATION_TYPE_WEB` but not the `action` field value); whether
`agentSessionId` must persist across turns within one conversation or can be
re-minted per call; rate limits on this endpoint specifically.

### 2.2 Reusable auth/session plumbing (verified)

- **Bearer + cookies + project_id**: `SessionKeeper.get_session()`
  (flow_bot.py:1547-1577) returns `{"bearer", "cookies", "project_id",
  "headers"}` — refreshes the bearer if older than `TOKEN_TTL_SEC`. This is
  exactly what every existing `FlowHttpClient` method calls first
  (`generate_video`: flow_bot.py:2628; `video_transport_ab_test`: 2081).
- **Headers**: `FlowHttpClient._build_headers(session)` (flow_bot.py:2021-2052)
  already sets `Authorization: Bearer ...`, `Origin: https://labs.google`,
  `Referer: https://labs.google/`, plus browser-fingerprint headers
  (`User-Agent`, `Sec-Ch-Ua*`, `X-Client-Data`) copied from the live browser
  session. The only header to override per-call for the agent endpoint is
  `Content-Type` (the spec wants `application/json`, `_build_headers` defaults
  to `text/plain;charset=UTF-8` at line 2027 — existing video/image calls pass
  a `json=` body to aiohttp which sets its own content-type regardless, so
  this default has never mattered; for the agent call, set
  `headers["Content-Type"] = "application/json"` explicitly to match the
  captured contract).
- **reCAPTCHA**: `keeper.solve_captcha(action: str)` (flow_bot.py:814-824) →
  `_solve_via_browser_js(action)` (851-878) → live
  `grecaptcha.enterprise.execute(sitekey, {action})` in the persistent Chrome
  profile. This is the SAME mechanism `generate_video` uses
  (`SessionKeeper.VIDEO_RECAPTCHA_ACTION = "VIDEO_GENERATION"`, line 775) and
  is already proven to beat 2captcha for Google endpoints (see
  `feedback_captcha_browser_js` memory — confirmed working pattern, no new
  captcha code needed). Add a new class constant
  `AGENT_RECAPTCHA_ACTION = "FLOW_CREATION_AGENT"` (best-guess action string,
  **unverified** — flagged in Open Questions) next to
  `VIDEO_RECAPTCHA_ACTION` (line 775) and `RECAPTCHA_ACTIONS` (line 767), with
  the SAME retry-on-403-with-fresh-token pattern already used for video
  (flow_bot.py:2795-2805) since a wrong/low-score token is indistinguishable
  from a 403 until tried live.
- **Per-account routing**: reuse `_account_for(user_id)` /
  `_keeper_for(user_id)` / `_client_for(user_id)` (flow_bot.py:3192-3204) — the
  agent call should run on the SAME account the user's image/video generation
  already uses, via `ensure_user_project(user_id, account_id=acc_id)`
  (3544-3556) for `projectId`. No new account-pool logic needed; this is a
  fourth "thing this account does" alongside generate/edit/video.

### 2.3 `FlowHttpClient.improve_prompt` — design

```python
# flow_bot.py, alongside generate_video (new method on FlowHttpClient)

AGENT_ENDPOINT = "https://aisandbox-pa.googleapis.com/v1/flowCreationAgent:streamChat?alt=sse"

async def improve_prompt(
    self,
    text: str,
    *,
    user_id: int,
    mode: str = "variants",          # "variants" | "single"
    agent_session_id: str | None = None,
    turn_number: int = 0,
    project_id: str | None = None,
) -> dict:
    """Call Flow's creation agent to improve a prompt.

    Returns one of:
      {"ok": True, "mode": "variants", "variants": [{"title": str, "prompt": str}, ...],
       "agent_session_id": str, "turn_number": int}
      {"ok": True, "mode": "single", "prompt": str,
       "agent_session_id": str, "turn_number": int}
      {"ok": False, "error": "<short_code>"}   # never raises
    """
    session = await self.keeper.get_session()
    if not session["bearer"]:
        return {"ok": False, "error": "missing_bearer"}
    project_id = project_id or session.get("project_id")
    if not project_id:
        return {"ok": False, "error": "missing_project_id"}

    agent_session_id = agent_session_id or str(uuid.uuid4())
    headers = self._build_headers(session)
    headers["Content-Type"] = "application/json"
    proxy = self._api_proxy()

    captcha_token = await self.keeper.solve_captcha(SessionKeeper.AGENT_RECAPTCHA_ACTION)
    if not captcha_token:
        return {"ok": False, "error": "captcha_unavailable"}

    payload = {
        "agentSessionId": agent_session_id,
        "agentClientContext": {
            "projectId": f"projects/{project_id}",
            "clientSessionId": f";{int(time.time() * 1000)}",
            "recaptchaContext": {
                "token": captcha_token,
                "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB",
            },
            "turnNumber": turn_number,
        },
        "userMessage": {"userPrompt": {"parts": [{"text": text}]}},
    }

    try:
        async with aiohttp.ClientSession(cookies=session["cookies"]) as http:
            async with http.post(
                self.AGENT_ENDPOINT, headers=headers, json=payload, proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.warning("agent improve_prompt -> %s: %s", resp.status, body[:200])
                    return {"ok": False, "error": f"http_{resp.status}"}
                raw_chunks: list[str] = []
                async for line in resp.content:
                    decoded = line.decode("utf-8", errors="ignore").strip()
                    if not decoded.startswith("data:"):
                        continue
                    raw_chunks.append(_extract_agent_text_chunk(decoded[5:].strip()))
    except Exception as exc:
        log.error("agent improve_prompt network error: %s", exc)
        return {"ok": False, "error": "network_error"}

    full_text = "".join(c for c in raw_chunks if c)
    parsed = parse_agent_response(full_text, mode=mode)   # pure helper, flow_core.py
    if parsed is None:
        return {"ok": False, "error": "parse_failed"}
    parsed.update({"ok": True, "agent_session_id": agent_session_id,
                   "turn_number": turn_number + 1})
    return parsed
```

`_extract_agent_text_chunk` and `parse_agent_response` should be **pure,
stdlib-only helpers in `flow_core.py`** (per the repo convention that
flow_core holds parse/payload logic, unit-tested without network — same
pattern as `parse_video_gen_response`, `parse_concat_status`, etc.):

```python
# flow_core.py

def _extract_agent_text_chunk(sse_data_json: str) -> str:
    """Pull one agentMessage's response.text out of one SSE `data:` line.

    Returns "" for thinkingEvent-only or malformed chunks — never raises.
    """
    try:
        obj = json.loads(sse_data_json)
    except (json.JSONDecodeError, TypeError):
        return ""
    msg = obj.get("agentMessage") if isinstance(obj, dict) else None
    if not isinstance(msg, dict):
        return ""
    resp = msg.get("response")
    if not isinstance(resp, dict):
        return ""
    text = resp.get("text")
    return text if isinstance(text, str) else ""


def parse_agent_response(full_text: str, *, mode: str = "variants") -> dict | None:
    """Parse the concatenated SSE response.text into variants or a single prompt.

    ``full_text`` is the A2UI JSON array (often fenced in ```json ... ```).
    Returns None on any parse failure (caller treats as a soft error, refunds).
    """
    cleaned = full_text.strip()
    if cleaned.startswith("```"):
        # Strip a leading ```json / ``` fence and a trailing ``` fence.
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        a2ui = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(a2ui, list):
        return None

    components: list[dict] = []
    for item in a2ui:
        update = item.get("surfaceUpdate") if isinstance(item, dict) else None
        if isinstance(update, dict):
            comps = update.get("components")
            if isinstance(comps, list):
                components.extend(c for c in comps if isinstance(c, dict))

    if mode == "variants":
        for comp in components:
            mc = comp.get("MultipleChoice") or comp.get("multipleChoice")
            if isinstance(mc, dict):
                options = mc.get("options") or []
                variants = []
                for opt in options:
                    label = ((opt.get("label") or {}).get("literalString") or "").strip()
                    if not label:
                        continue
                    title, _, body = label.partition("\n")
                    title = re.sub(r"^\*+|\*+$", "", title).strip() or "Вариант"
                    variants.append({"title": title, "prompt": body.strip() or label})
                if variants:
                    return {"mode": "variants", "variants": variants}
        # Fall through to single-text extraction if no MultipleChoice found —
        # the agent may answer with a single Text component even when variants
        # were requested (observed shape per the task spec).

    for comp in components:
        text_comp = comp.get("Text") or comp.get("text")
        if isinstance(text_comp, dict):
            literal = (text_comp.get("literalString") or "").strip()
            if literal:
                # Strip a markdown blockquote prefix `> **Title**\n...` if present.
                literal = re.sub(r"^>\s*", "", literal, flags=re.MULTILINE)
                literal = re.sub(r"^\*\*[^*]+\*\*\s*", "", literal).strip()
                if literal:
                    return {"mode": "single", "prompt": literal}
    return None
```

**Robust parsing / failure modes covered:**
- Network error / non-200 → `{"ok": False, "error": "..."}`, never raises.
- Partial/invalid JSON after fence-stripping → `parse_agent_response` returns
  `None` → caller surfaces a generic "agent didn't understand, try rephrasing"
  message and refunds (see 2.6).
- Agent returns no `MultipleChoice` and no usable `Text` → `None` → same
  refund path.
- Agent returns `MultipleChoice` with empty `options` → falls through to
  `Text` extraction rather than returning an empty-variants success (avoids
  showing the user 0 buttons).
- SSE stream truncated mid-chunk (connection drop) → `full_text` is whatever
  was concatenated before the drop; `parse_agent_response` will most likely
  fail (incomplete JSON) → `None` → refund. No retry-mid-stream complexity.

### 2.4 Per-user agent session state

Lives in the existing `wizard_state[user_id]` dict (flow_bot.py:3403,
`defaultdict(dict)`, already strictly per-`user_id` — satisfies "no global
mutable state that can mix user sessions"). New keys, prefixed `ag_` to avoid
collision with `v*`/`tp_*`/`gp_*` prefixes already in use:

| Key | Type | Meaning |
|---|---|---|
| `ag_active` | bool | user is in "agent mode" (next text message routes to agent, not gen) |
| `ag_session_id` | str (uuid4) | `agentSessionId`, minted on first agent call, reused for follow-ups |
| `ag_turn` | int | next `turnNumber` to send (starts at 0, incremented after each successful call) |
| `ag_target` | `"image"` \| `"video"` | which wizard's prompt field receives the accepted result |
| `ag_variants` | list[dict] | the 3 `{title, prompt}` options from the last `variants` call, for button callback resolution |
| `ag_source_prompt` | str | the prompt text the user asked to improve, for "improve again" / regen without re-typing |

**Reset rules**: clear all `ag_*` keys (a) when the user leaves agent mode via
"❌ Отмена"/cancel, (b) when `m:menu`/`/start` is hit (mirrors how `_vid_clear`
is called on menu navigation elsewhere, e.g. flow_bot.py:10942-10945), (c)
after the improved prompt is accepted into the target wizard (one-shot use —
re-entering "🤖 Агент" starts a fresh `agentSessionId`/`turn=0` rather than
silently continuing an old conversation the user may not remember). Add a
`_agent_clear(user_id)` helper mirroring `_vid_clear` (flow_bot.py:4438-4445):

```python
def _agent_clear(user_id: int) -> None:
    st = wizard_state[user_id]
    for key in list(st):
        if key.startswith("ag_"):
            st.pop(key, None)
```

**Isolation between users**: guaranteed structurally — `wizard_state` is keyed
by `user_id` (int), every read/write in this design goes through `_ws(user_id)`
or `wizard_state[user_id]` exactly like every other feature in this file.
There is no shared/global agent state (no module-level dict keyed by anything
other than `user_id`). `agent_session_id` is per-user-per-session, never
shared, never reused across users.

### 2.5 Bot UX state machine

**Entry points** (button surfaces under prompt-input screens, per the task):
- Under `show_video_prompt_input` (flow_bot.py:4879-4915), add a row:
  `[B(text="🤖 Улучшить промпт · 5 кр", callback_data="ag:improve:video")]`
  shown only once the user has typed something (`st.get("vprompt")` truthy) —
  improving an empty prompt is meaningless. Since that screen currently shows
  only a cancel button before text arrives (4907-4909), surface the agent
  button instead on the FOLLOW-UP screen, i.e. `_nwiz_kb` (4822-4865), where a
  prompt already exists: add `[B(text=f"🤖 Улучшить · {action_price('improve_prompt')} кр",
  callback_data="ag:improve:video")]` near the "✏️ Изменить"/"🎬 Создать" row.
- Under the image wizard's equivalent prompt-input screen (the `gen`/`w:`
  flow) — same idea, callback `ag:improve:image`. (Exact image-wizard
  function name to wire into is out of scope for this read-only investigation
  pass; locate the analogous "prompt already typed, about to confirm" screen
  in the image wizard before implementing — likely near where `st["pending_prompt"]`
  is shown, grep `pending_prompt` for the render function.)
- A persistent top-level entry, `"🤖 Агент"`, is optional for v1 — the
  task's primary ask is the upsell button under prompt screens, which is
  cheaper to ship and test in isolation. Treat a standalone always-available
  agent chat (free-form Q&A, not just improve) as a Phase 2 (see rollout).

**Callback prefix**: `ag:*`, parallel to existing `v:`/`w:`/`an:`/`ih:`. New
handler block:

```python
@dp.callback_query(F.data.startswith("ag:"))
async def on_agent_action(callback: types.CallbackQuery):
    data = callback.data or ""
    user_id = callback.from_user.id
    msg = callback.message
    st = _ws(user_id)

    if data.startswith("ag:improve:"):
        target = data.split(":", 2)[2]   # "image" | "video"
        prompt = (st.get("vprompt") if target == "video" else st.get("pending_prompt")) or ""
        if len(prompt.strip()) < 3:
            await callback.answer("Сначала введите описание", show_alert=True)
            return
        await callback.answer()
        await _agent_offer_mode(msg, user_id=user_id, target=target, prompt=prompt)
        return

    if data == "ag:mode:variants" or data == "ag:mode:single":
        mode = data.split(":")[2]
        await callback.answer()
        await _agent_run_improve(msg, user_id=user_id, mode=mode)
        return

    if data.startswith("ag:pick:"):
        idx = int(data.split(":", 2)[2])
        variants = st.get("ag_variants") or []
        if not (0 <= idx < len(variants)):
            await callback.answer(flow_copy.msg("expired"), show_alert=True)
            return
        chosen = variants[idx]["prompt"]
        await callback.answer()
        await _agent_accept(msg, user_id=user_id, chosen_prompt=chosen)
        return

    if data == "ag:cancel":
        _agent_clear(user_id)
        await callback.answer("Отменено")
        # Return to whichever wizard screen the user came from.
        if st.get("ag_target") == "video":
            await show_new_video_wizard(msg, user_id=user_id, edit=True)
        else:
            await callback.answer()
        return
```

**Flow**:
1. User has a prompt typed → taps "🤖 Улучшить · 5 кр" (`ag:improve:<target>`).
2. Bot shows a small chooser: "3 варианта" vs "Улучшить мой промпт"
   (`ag:mode:variants` / `ag:mode:single`) — both still pre-charge nothing yet;
   charging happens in `_agent_run_improve` once the user commits to a mode.
3. `_agent_run_improve` performs `credit_gate(user_id, "improve_prompt", ...)`
   (5 credits, see 2.6), then calls `client.improve_prompt(...)`. On success:
   - `mode="variants"` → render 3 inline buttons (`ag:pick:0/1/2`), each
     labelled with the variant's `title` (≤30 chars per the
     `telegram_button_colors`/copy-length convention noted in flow_copy.py),
     full `prompt` text shown in the message body (HTML `<blockquote>`,
     matching the existing prompt-preview pattern at `_nwiz_text`
     flow_bot.py:4805).
   - `mode="single"` → render the improved prompt in a blockquote with
     "✅ Принять" / "✏️ Свой вариант" / "❌ Отмена" buttons
     (`ag:pick:0` reusing the same accept path with a 1-element variants list,
     `ag:edit`, `ag:cancel`).
4. `_agent_accept(chosen_prompt)` writes the chosen text back into
   `st["vprompt"]` (video target) or `st["pending_prompt"]` (image target),
   clears `ag_*` state, and re-renders the originating wizard screen
   (`show_new_video_wizard` or the image wizard's equivalent) so the user sees
   their improved prompt already populated — exactly mirroring how
   `show_new_video_wizard` already re-renders after `v:nstyle:*`/`v:nqual:*`
   picks (flow_bot.py:8893-8900).
5. "✏️ Свой вариант" / "❌ Отмена" return to the prompt-input screen without
   spending another charge (the 5 credits already paid for the agent CALL, not
   per-accept — accepting/rejecting a returned variant is free, matching how
   the image wizard's existing "Изменить" buttons don't re-charge).

### 2.6 Monetization

Add a new `action_price` branch (flow_core.py:2552-2583), next to the existing
ones:

```python
PROMPT_IMPROVE_PRICE = 5  # matches docs/MONETIZATION.md's existing "Prompt enhance" price point

def action_price(action: str, num_images: int = 1) -> int:
    ...
    if action == "improve_prompt":
        return _price_override("improve_prompt", PROMPT_IMPROVE_PRICE)
    ...
```

This deliberately reuses the SAME 5-credit price point `docs/MONETIZATION.md`
already documents under "Prompt enhance / service upscale" (line 51) — that
line currently has no corresponding `action_price` key in code (it was
aspirational/placeholder), so this feature is the first real implementation of
it. No collision with `up2x`/`realup` (those are a different feature, image
sharpness enhance, already implemented and unrelated to text prompts).

**Charging**: use the existing `credit_gate` async-context-manager pattern
(flow_bot.py:3497-3536) exactly as `gen`/`edit`/`realup` already do (see
call sites at 6358, 6628, 7155, 7296, 7417):

```python
async def _agent_run_improve(message, *, user_id: int, mode: str) -> None:
    st = _ws(user_id)
    try:
        async with credit_gate(user_id, "improve_prompt", message) as charge:
            result = await _client_for(user_id).improve_prompt(
                st.get("ag_source_prompt", ""),
                user_id=user_id, mode=mode,
                agent_session_id=st.get("ag_session_id"),
                turn_number=st.get("ag_turn", 0),
                project_id=await ensure_user_project(user_id, account_id=_account_for(user_id)),
            )
            if not result.get("ok"):
                await message.answer(flow_copy.msg("agent_improve_failed"))
                metrics.log_event("agent_improve_failed", user_id=user_id,
                                   payload={"error": result.get("error"), "mode": mode})
                return  # charge.ok stays False -> credit_gate refunds automatically
            charge.ok = True
            st["ag_session_id"] = result["agent_session_id"]
            st["ag_turn"] = result["turn_number"]
            ...  # render variants/single per 2.5
    except NotEnoughCredits:
        return
```

`credit_gate`'s existing finally-block refund (flow_bot.py:3534-3536, "если
тело не выставило charge.ok — рефанд") gives refund-on-failure for free; no new
refund code needed, exactly matching the project rule "all external calls must
be... refund-on-failure" already established for images/video.

**Upsell placement** (per the task's "after weak prompts" ask): a "weak
prompt" heuristic (very short, e.g. <15 chars, or generic single-word like
"кот"/"видео") could auto-suggest the agent button with a nudge line in the
prompt-confirmation screen text, e.g. append "💡 Промпт можно усилить —
попробуйте 🤖 Улучшить" to `_nwiz_text`/the image wizard's equivalent when
`len(prompt) < 15`. This is a copy/UX nicety, not required for v1 — flag as
Phase 2 in rollout (don't couple it to the core agent plumbing landing).

### 2.7 PromptOps

No giant prompt is sent BY the bot to an LLM here — the "prompt" being
improved is the END USER's image/video prompt, and the "agent" is Google
Flow's own hosted service (we are a client, not the model owner). There is
still a small amount of bot-authored framing text that belongs in
`/prompts/` per the repo convention (purpose/inputs/outputs/examples header,
same as `prompts/templates/*.txt`):

`prompts/agent/improve_variants_intro.txt` (shown to the user before the
agent call, explaining what "3 варианта" means) and
`prompts/agent/improve_single_intro.txt` — these are RU **microcopy**, not
LLM system prompts (the actual instructions live server-side in Google's
`flowCreationAgent`, which we don't control and can't version). Given that,
the more honest home for this text is actually `flow_copy.py` (it's UI copy,
not a model-facing prompt) — reserve `/prompts/agent/` only if/when this
feature grows a bot-side system prompt of its own (e.g. Phase 2's free-form
agent chat, where the bot might prepend its own framing before forwarding to
`flowCreationAgent`). For v1, add these as `flow_copy.py` keys:

```python
"ag_choose_mode": "Как улучшить промпт?",
"ag_mode_variants": "🎲 3 варианта",
"ag_mode_single": "✨ Улучшить мой",
"agent_improve_failed": "Агент сейчас не отвечает. Попробуйте ещё раз через минуту.",
```

If Phase 2 adds a bot-authored framing message prepended to the user's text
before forwarding to `flowCreationAgent` (e.g. "Ты помогаешь улучшать промпты
для генерации изображений/видео..."), THAT framing text must live under
`/prompts/agent/system.md` with the standard purpose/inputs/outputs/examples
header and be swappable for A/B testing, per CLAUDE.md's PromptOps rule. Not
needed for v1 since v1 forwards the user's raw text with no bot-side framing.

### 2.8 Risk / cost notes

- **Cost**: each `improve_prompt` call burns one reCAPTCHA solve (browser-JS,
  ~200ms, free per the existing memory) + one Flow agent call. Unlike
  image/video generation, there is no Google Flow credit (G-credit) cost
  mentioned in the task's captured contract — confirm this empirically before
  launch (Open Questions) since an unexpectedly G-credit-costly endpoint would
  break the "images/agent are margin-safe" assumption that underlies why this
  is priced at only 5 bot credits.
- **Quota/account health risk**: this is a NEW endpoint the existing
  account-health/failover logic (`_video_account_health_reason`,
  `account_pool.pick_for_video`) doesn't know about. For v1, run agent calls
  on the user's already-assigned account (`_account_for(user_id)`) WITHOUT
  wiring it into the video-specific health/failover machinery — if the call
  fails, the user just gets a refund and "try again" (2.6), no automatic
  account failover. Add agent-specific health tracking only if failure rates
  in production data justify the complexity (avoid speculative
  infrastructure, per "MVP speed, stable foundations" — don't build the
  failover before there's a failure-rate signal to size it against).
- **Streaming parsing fragility**: the A2UI JSON-inside-SSE-inside-markdown-
  fence shape is exactly the kind of "format the model owner can change any
  time" risk the task itself flags. `parse_agent_response`'s graceful `None`
  return + refund (2.3/2.6) is the mitigation; no retry-with-different-prompt
  logic is proposed (that would silently double-spend reCAPTCHA solves on a
  parse bug, not a content issue).
- **Abuse**: a user could spam "🤖 Улучшить" to probe the agent for unrelated
  chat (cost to them: 5 credits/call, naturally rate-limiting; cost to us:
  reCAPTCHA solve volume on the shared account). No additional rate-limit is
  proposed for v1 beyond the existing per-user credit balance acting as the
  limiter — revisit if abuse is observed (`metrics.log_event("agent_improve_failed"
  | "agent_improve_ok", ...)` from day one gives the signal to decide).

### 2.9 Task breakdown (strict order)

1. **Spike**: write `flow_core._extract_agent_text_chunk` +
   `flow_core.parse_agent_response` as pure functions, with a hand-built
   canned SSE byte sequence (a few `data: {...}` lines copied from the task's
   contract shape, no real network) as the FIRST unit test —
   `tests/test_flow_core_agent.py` or a new class in `tests/test_flow_video.py`'s
   sibling pattern. Verify: `python -m unittest discover -s tests -p
   "test_flow_core_agent.py"` (or wherever placed) passes BEFORE writing any
   `aiohttp`/SSE network code. This is the "smallest proof-of-concept first"
   step for the one genuinely unfamiliar piece (per CLAUDE.md).
2. **Add `parse_agent_response` edge-case tests**: empty options, malformed
   JSON, missing fence, `Text`-only response, blockquote-wrapped single
   prompt. Verify: same test file, all green.
3. **Add `action_price("improve_prompt")`** to flow_core.py + a test asserting
   it returns 5 (and respects `_price_override`, matching the pattern other
   `action_price` branches already have tests for — grep
   `test_action_price` in the existing test suite for the pattern to copy).
4. **Add `AGENT_RECAPTCHA_ACTION` constant + `FlowHttpClient.improve_prompt`**
   in flow_bot.py per 2.3. No live call yet — this step is code-complete but
   untested against the network (that's gated behind operator approval).
   Verify: `python -m py_compile flow_bot.py`.
5. **Add `ag_*` wizard-state keys + `_agent_clear`** per 2.4. Verify: a unit
   test sets `ag_*` keys on a `wizard_state[uid]` dict, calls `_agent_clear`,
   asserts only `ag_*` keys vanish and sibling `v*`/`tp_*` keys survive
   (mirrors the existing `_vid_clear` test pattern if one exists — grep
   `_vid_clear` in test files for the pattern).
6. **Add the `ag:*` callback handler + entry buttons** per 2.5 (video-wizard
   entry first, since that screen/state shape is already fully mapped in this
   proposal; image-wizard entry as a fast follow once the equivalent
   prompt-confirm screen is located). Verify: `python -m py_compile flow_bot.py`
   + a `test_flow_menu.py`-style test that constructs the keyboard and asserts
   the `ag:improve:video` button is present with the correct price label
   (mirrors `test_video_result_edit_and_extend_wiring`,
   `tests/test_flow_menu.py:873`).
7. **Wire `_agent_run_improve`/`_agent_accept`** per 2.5-2.6, using
   `credit_gate`. Verify: offline tests CANNOT exercise the real network call
   (no live Flow calls per the task's "offline only" constraint for this
   design pass) — instead, write a test that monkeypatches/stubs
   `FlowHttpClient.improve_prompt` to return a canned `{"ok": True, "mode":
   "variants", "variants": [...]}" dict and asserts `_agent_accept` correctly
   writes into `st["vprompt"]` and clears `ag_*` state. This keeps the test
   offline while covering the real wiring logic.
8. **flow_copy.py additions** per 2.7. Verify: backend-neutrality test (no
   "flow"/"google"/"капч" leakage) still passes.
9. **docs**: add a short `docs/PROMPT_AGENT.md` (or a section in
   `docs/MONETIZATION.md`) documenting the 5-credit price and the
   `ag:`-prefixed callback contract, mirroring how `docs/IDEAS_HUB.md`
   documents the `tp:`/`gp:` prefixes.
10. **(Approval-gated, NOT part of offline verification)** First live call to
    `flowCreationAgent:streamChat` via `tools/`-style one-off capture script
    (mirroring `tools/capture_video.py`'s abort-mode pattern) to confirm: (a)
    the actual response shape matches the task's captured contract exactly,
    (b) whether a G-credit is consumed, (c) the correct reCAPTCHA `action`
    string (try `"FLOW_CREATION_AGENT"` first, log the raw 403 body if
    rejected — Google's reCAPTCHA action mismatches don't always 403
    descriptively, per the existing video 403 diagnostic work in
    `video_403_diag.txt`/`docs/` from recent commits). Do not run without
    explicit operator approval.

### 2.10 Failure cases

- **`improve_prompt` returns `{"ok": False, "error": "captcha_unavailable"}`**
  — `credit_gate` refunds automatically (charge.ok never set True); user sees
  `flow_copy.msg("agent_improve_failed")`; metrics event logged with the error
  code for later triage. No retry loop (matches video's bounded-retry
  philosophy, but simpler — only one attempt for v1, since this is a
  lower-stakes/lower-cost action than video generation; add the video-style
  multi-attempt-with-backoff only if production data shows it's needed).
- **SSE connection drops mid-stream** — caught by the generic `except
  Exception` around the `aiohttp` block (2.3); returns `network_error`; same
  refund path.
- **Agent returns variants but the user's session/message expires before they
  tap a button (Telegram message too old, or bot restarted and lost
  `wizard_state`)** — `ag:pick:*` handler checks `0 <= idx < len(variants)`
  against CURRENT `st.get("ag_variants")`; if the in-memory state was lost
  (process restart), `variants` is `[]`, the check fails, user sees
  `flow_copy.msg("expired")` exactly like the existing
  `video_registry.get(token)`/`image_registry.get(token)` expiry pattern
  (e.g. flow_bot.py:8191-8193). No special new "session expired" code needed
  — same shape as everywhere else in this file.
- **User taps "🤖 Улучшить" twice rapidly (double-tap)** — NOT separately
  debounced in this design; relies on the same per-user `user_slot` lock
  pattern used for image/video generation (`async with user_slot(user_id,
  message)`, flow_bot.py:9280) to serialize concurrent actions for one user.
  Recommend wrapping `_agent_run_improve`'s body in `user_slot(user_id,
  message)` too, for the same race-prevention reason video already does
  (flow_bot_architecture memory: "без него параллельные видео+картинка
  одного юзера читали баланс до списания друг друга") — add this in step 7
  of the task breakdown, not as an afterthought.
- **Agent endpoint is fully down (Google-side outage)** — every call returns
  `http_5xx` or `network_error`; refund-on-failure means users lose no
  credits, just can't use the feature; no circuit-breaker proposed for v1
  (premature for a single low-traffic feature — revisit if it becomes
  high-traffic enough to need one).

---

## Open questions / unknowns

1. **Omni r2v duration confirmation**: only `omni-flash-4s`'s underlying
   `abra_t2v_4s` key is `"confirmed": True` in `VIDEO_MODELS` (flow_core.py:1053);
   6/8/10s are pattern-inferred, AND the catalog's `confirmed` flag tracks the
   text-to-video key, not the r2v key specifically — a live
   `--ingredients` capture per duration is the only way to fully confirm
   Feature 1's duration picker is safe to ship at all four durations vs. just
   4s (and maybe 6s, which already has a unit test asserting its r2v key
   shape at `tests/test_flow_video.py:214`, though that test asserts the KEY
   FORMAT, not that Google's API accepts it).
2. **Agent endpoint reCAPTCHA `action` string**: the task's contract doesn't
   specify it; this proposal guesses `"FLOW_CREATION_AGENT"` by analogy with
   `VIDEO_GENERATION`/`IMAGE_GENERATION`. Must be confirmed via the approval-
   gated live capture (task 2.9 step 10) before shipping — a wrong action
   string may not error explicitly (recaptcha actions are sometimes silently
   accepted with a lower score rather than rejected outright, per the existing
   403-diagnosis work referenced in this repo's recent commits).
3. **`agentSessionId` lifecycle**: unclear whether Google expects ONE
   `agentSessionId` to persist across a user's entire multi-turn improve
   conversation (this proposal assumes yes, incrementing `turnNumber`) or
   whether each "improve" button-press should mint a fresh session (simpler,
   stateless, but loses conversational context if the user says "make it
   shorter" as a follow-up). Confirm via the same live capture.
4. **G-credit cost of the agent call**: not stated in the task's contract.
   If it turns out to consume Flow quota (unlike images, per
   `docs/MONETIZATION.md`'s "images are 0 G-credits" principle), the 5-credit
   bot price may need revisiting against the same blended-cost model used for
   video (`docs/MONETIZATION.md` "Provider Economics" table) — flag for the
   monetization-strategist design agent before final pricing sign-off, not
   just this architecture pass.
5. **Image-wizard entry point exact location**: this proposal fully maps the
   video-wizard agent entry (`_nwiz_kb`/`show_new_video_wizard`) but only
   sketches the image-wizard equivalent (`pending_prompt`-based screen) since
   that screen's exact function name wasn't in scope of the files explicitly
   called out in the task. Locate it (`grep -n pending_prompt flow_bot.py`)
   before implementing task 2.9 step 6's image-wizard fast-follow.
6. **Whether the old Ingredients wizard (`v:fam:`/`v:vmod:`) should also gain
   the Omni engine toggle**: this proposal deliberately scoped Feature 1 to
   the NEW prompt-first wizard only, since that's the only reachable entry
   point today (1.2). If product later re-exposes the old wizard as an
   alternate entry, it already has Veo tier selection (`_vid_model_row`) and
   would need the equivalent Omni-vs-Veo family choice added separately —
   out of scope for this proposal unless explicitly requested.
