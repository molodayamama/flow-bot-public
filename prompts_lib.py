"""Pure, stdlib-only prompt composition for the bot's "Идеи и шаблоны" hub.

This module powers two button-driven flows:

* **Templates** (branch A, "Готовые решения") — a small Q&A per template that
  composes a ready-made image-generation prompt.
* **Guided picker** (branch B, "Подбор по шагам") — a 3-step choice wizard that
  assembles a generic image prompt.

Design rules (per the repo's PromptOps + working-style conventions):

* No third-party imports, no project imports — stdlib only, fully unit-testable
  without aiogram or a browser.
* Composition is **deterministic** and **never raises**: every ``compose_*``
  call is wrapped so bad input degrades to a safe minimal prompt instead of
  throwing into the bot's hot path.
* Prompt **skeletons live in files** under ``PROMPTS_DIR`` (versioned, A/B-able
  by swapping files), never as giant string literals in code. Question wording
  (Russian) lives here so the bot can render inline keyboards from plain dicts.
* Skeleton files carry a ``#`` header comment block (purpose / inputs / outputs
  / example dialogs); those lines are stripped before substitution.

Public API (the bot wires directly to these names):

    PROMPTS_DIR
    template_ids() -> list[str]
    get_template(tid) -> dict | None
    template_questions(tid) -> list[dict]
    compose_template_prompt(tid, answers) -> str
    guided_steps() -> list[dict]
    compose_guided_prompt(answers) -> str
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

# ── prompt file location ───────────────────────────────────────────────
# Module-relative ``/prompts`` by default; overridable via env for A/B sets
# (e.g. point PROMPTS_DIR at a variant folder to swap skeletons at deploy time).
_DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
PROMPTS_DIR = Path(os.environ.get("PROMPTS_DIR") or _DEFAULT_PROMPTS_DIR)

_TEMPLATES_DIR = PROMPTS_DIR / "templates"
_GUIDED_FILE = PROMPTS_DIR / "guided" / "skeleton.txt"

# A guaranteed non-empty result for the worst case (no usable input at all).
_FALLBACK_PROMPT = "high quality image, detailed, sharp focus"


# ── option helpers ─────────────────────────────────────────────────────
def _opt(value: str, label: str) -> dict[str, str]:
    return {"value": value, "label": label}


def _choice(key: str, text: str, options: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "key": key,
        "text": text,
        "type": "choice",
        "options": options,
        "optional": False,
    }


def _text(key: str, text: str, optional: bool = False) -> dict[str, Any]:
    return {
        "key": key,
        "text": text,
        "type": "text",
        "options": [],
        "optional": optional,
    }


# ── template definitions (ids in stable display order) ─────────────────
# Each template: id, RU title, ordered question specs. The skeleton body
# (with {slot} placeholders matching the question keys) lives in
# prompts/templates/<id>.txt and is NOT exposed via get_template().
#
# CHOICE answers are stored by the bot as option *values*; compose_* maps each
# value to an English phrase via _CHOICE_PHRASES (falling back to the RU label,
# then the raw value). TEXT answers pass through verbatim.

_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "pet_photo_animation",
        "title": "🐾 Оживи фото питомца",
        "target": "video",
        "questions": [
            _text("pet", "Кто на фото? Например: рыжий кот, корги, попугай."),
            _choice(
                "emotion",
                "Какая эмоция нужна?",
                [
                    _opt("cute", "Милота"),
                    _opt("playful", "Играет"),
                    _opt("curious", "Любопытство"),
                    _opt("surprised", "Удивление"),
                    _opt("cozy", "Нежность"),
                ],
            ),
            _choice(
                "motion",
                "Что должен сделать питомец?",
                [
                    _opt("head_tilt", "Наклонить голову"),
                    _opt("look_camera", "Посмотреть в камеру"),
                    _opt("tail_wag", "Вильнуть хвостом"),
                    _opt("gentle_walk", "Сделать шаг"),
                    _opt("paw_wave", "Поднять лапу"),
                ],
            ),
            _text("detail", "Что важно не менять? (можно пропустить)", optional=True),
        ],
    },
    {
        "id": "marketplace_white_bg",
        "title": "⚪ Товар на белый фон за 10 сек",
        "questions": [
            _text("product", "Что за товар? Опиши коротко."),
            _choice(
                "platform",
                "Для какой площадки?",
                [
                    _opt("wb", "Wildberries"),
                    _opt("ozon", "Ozon"),
                    _opt("ym", "Яндекс Маркет"),
                    _opt("universal", "Универсально"),
                ],
            ),
            _choice(
                "angle",
                "Как показать товар?",
                [
                    _opt("front", "Фронтально"),
                    _opt("threequarter", "3/4"),
                    _opt("top", "Сверху"),
                    _opt("packshot", "Packshot"),
                ],
            ),
            _text("detail", "Что важно сохранить или подчеркнуть? (можно пропустить)", optional=True),
        ],
    },
    {
        "id": "product_card",
        "title": "🛒 Карточка товара для маркетплейса",
        "questions": [
            _text("product", "Что за товар? Опиши коротко."),
            _choice(
                "background",
                "Какой фон нужен?",
                [
                    _opt("white", "Белый"),
                    _opt("lifestyle", "Lifestyle"),
                    _opt("luxury", "Luxury"),
                    _opt("minimal", "Минимализм"),
                ],
            ),
            _text("audience", "Для кого этот товар? (можно пропустить)", optional=True),
            _choice(
                "need_text",
                "Нужен ли текст на изображении?",
                [
                    _opt("yes", "Да"),
                    _opt("no", "Нет"),
                ],
            ),
        ],
    },
    {
        "id": "ad_banner",
        "title": "📢 Рекламный баннер",
        "questions": [
            _text("subject", "Что рекламируем? Опиши главный объект."),
            _choice(
                "vibe",
                "Какое настроение баннера?",
                [
                    _opt("bright", "Яркий"),
                    _opt("premium", "Премиум"),
                    _opt("minimal", "Минимал"),
                    _opt("dynamic", "Динамичный"),
                ],
            ),
            _text("text_on_image", "Какой текст вынести на баннер? (можно пропустить)", optional=True),
        ],
    },
    {
        "id": "ugc_creative",
        "title": "📸 UGC-креатив",
        "questions": [
            _text("product", "Что за продукт? Опиши коротко."),
            _choice(
                "scene",
                "Где показываем продукт?",
                [
                    _opt("home", "Дома"),
                    _opt("street", "На улице"),
                    _opt("cafe", "В кафе"),
                    _opt("hands", "В руках"),
                ],
            ),
            _choice(
                "mood",
                "Какое настроение кадра?",
                [
                    _opt("natural", "Естественный"),
                    _opt("happy", "Радостный"),
                    _opt("calm", "Спокойный"),
                ],
            ),
        ],
    },
    {
        "id": "tg_post_cover",
        "title": "📄 Обложка Telegram-поста",
        "questions": [
            _text("topic", "О чём пост? Опиши тему."),
            _choice(
                "style",
                "Какой стиль обложки?",
                [
                    _opt("bold", "Bold"),
                    _opt("minimal", "Минимал"),
                    _opt("neon", "Неон"),
                    _opt("collage", "Коллаж"),
                ],
            ),
            _text("text_on_image", "Какой заголовок вынести на обложку? (можно пропустить)", optional=True),
        ],
    },
    {
        "id": "story",
        "title": "📱 Сторис",
        "questions": [
            _text("subject", "Что в центре сторис? Опиши."),
            _choice(
                "format_hint",
                "Формат сторис:",
                [
                    _opt("vertical", "Вертикаль 9:16"),
                ],
            ),
            _choice(
                "style",
                "Какой стиль?",
                [
                    _opt("trend", "Тренд"),
                    _opt("minimal", "Минимал"),
                    _opt("glossy", "Глянец"),
                ],
            ),
        ],
    },
    {
        "id": "brand_avatar",
        "title": "🏷 Аватар бренда",
        "questions": [
            _text("brand", "Название или суть бренда?"),
            _choice(
                "style",
                "Какой стиль аватара?",
                [
                    _opt("lettering", "Леттеринг"),
                    _opt("icon", "Иконка"),
                    _opt("mascot", "Маскот"),
                    _opt("gradient", "Градиент"),
                ],
            ),
            _text("colors", "Какие цвета предпочитаешь? (можно пропустить)", optional=True),
        ],
    },
    {
        "id": "product_on_bg",
        "title": "🎁 Фото товара на красивом фоне",
        "questions": [
            _text("product", "Что за товар? Опиши коротко."),
            _choice(
                "background",
                "На каком фоне?",
                [
                    _opt("studio", "Студия"),
                    _opt("nature", "Природа"),
                    _opt("marble", "Мрамор"),
                    _opt("neon", "Неон"),
                    _opt("wood", "Дерево"),
                ],
            ),
        ],
    },
]

_TEMPLATES_BY_ID: dict[str, dict[str, Any]] = {t["id"]: t for t in _TEMPLATES}


# ── guided picker steps ────────────────────────────────────────────────
_GUIDED_STEPS: list[dict[str, Any]] = [
    _choice(
        "what",
        "🧭 Что делаем?",
        [
            _opt("image", "🖼 Картинку"),
            _opt("ad", "📢 Рекламу"),
            _opt("avatar", "👤 Аватар"),
            _opt("product", "📦 Товар"),
            _opt("video", "🎬 Видео"),
        ],
    ),
    _choice(
        "style",
        "🧭 Стиль?",
        [
            _opt("realism", "📷 Реализм"),
            _opt("3d", "🧊 3D"),
            _opt("luxury", "💎 Luxury"),
            _opt("anime", "🎌 Anime"),
            _opt("cinematic", "🎬 Cinematic"),
            _opt("minimal", "◻️ Minimal"),
            _opt("mktclean", "🛒 Marketplace clean"),
        ],
    ),
    _choice(
        "format",
        "🧭 Формат?",
        [
            _opt("square", "⬜ Квадрат"),
            _opt("story", "📱 Сторис"),
            _opt("banner", "🖥 Баннер"),
            _opt("avatar", "👤 Аватар"),
        ],
    ),
]


# ── value -> English phrase maps ───────────────────────────────────────
# Choice answers (option *values*) are translated to descriptive English so the
# downstream image model gets a rich prompt. Keys are namespaced by question key
# to avoid collisions (e.g. "background" vs "style" both having a "minimal").

_CHOICE_PHRASES: dict[str, dict[str, str]] = {
    # pet_photo_animation
    "pet_photo_animation.emotion": {
        "cute": "heartwarming cute emotion",
        "playful": "playful joyful energy",
        "curious": "curious attentive expression",
        "surprised": "gentle funny surprise",
        "cozy": "soft affectionate cozy mood",
    },
    "pet_photo_animation.motion": {
        "head_tilt": "slowly tilts the head in a charming way",
        "look_camera": "looks toward the camera with natural eye movement",
        "tail_wag": "gently wags the tail with subtle body motion",
        "gentle_walk": "takes one small natural step",
        "paw_wave": "raises one paw slightly as if greeting",
    },
    # marketplace_white_bg
    "marketplace_white_bg.platform": {
        "wb": "optimized for a Wildberries marketplace product card",
        "ozon": "optimized for an Ozon marketplace product card",
        "ym": "optimized for a Yandex Market product card",
        "universal": "optimized for a universal e-commerce catalog",
    },
    "marketplace_white_bg.angle": {
        "front": "front-facing product view",
        "threequarter": "three-quarter product view",
        "top": "clean top-down product view",
        "packshot": "classic packshot composition",
    },
    # product_card.background
    "product_card.background": {
        "white": "pure white seamless background",
        "lifestyle": "lifestyle scene with natural context and props",
        "luxury": "premium luxury setting with rich textures and warm bokeh",
        "minimal": "clean minimalist background with lots of negative space",
    },
    "product_card.need_text": {
        "yes": "clear space reserved for a text or price label",
        "no": "no text overlay",
    },
    # ad_banner
    "ad_banner.vibe": {
        "bright": "bright vivid energetic mood",
        "premium": "premium and elegant mood with refined lighting",
        "minimal": "clean minimalist mood with calm tones",
        "dynamic": "dynamic high-energy motion mood",
    },
    # ugc_creative
    "ugc_creative.scene": {
        "home": "at home in a cozy everyday setting",
        "street": "outdoors on a city street",
        "cafe": "in a cozy cafe",
        "hands": "held in hands, close-up",
    },
    "ugc_creative.mood": {
        "natural": "natural candid mood",
        "happy": "joyful upbeat mood",
        "calm": "calm relaxed mood",
    },
    # tg_post_cover
    "tg_post_cover.style": {
        "bold": "bold high-contrast style with strong typography space",
        "minimal": "clean minimalist style with lots of negative space",
        "neon": "vibrant neon style with glowing accents on dark",
        "collage": "playful collage style mixing elements",
    },
    # story
    "story.format_hint": {
        "vertical": "vertical 9:16 aspect",
    },
    "story.style": {
        "trend": "trendy modern social style",
        "minimal": "clean minimalist style with breathing room",
        "glossy": "glossy polished magazine style",
    },
    # brand_avatar
    "brand_avatar.style": {
        "lettering": "elegant lettering / wordmark style",
        "icon": "flat icon style, simple memorable symbol",
        "mascot": "friendly mascot character style",
        "gradient": "smooth modern gradient style",
    },
    # product_on_bg
    "product_on_bg.background": {
        "studio": "a clean studio backdrop",
        "nature": "a natural outdoor setting",
        "marble": "an elegant marble surface",
        "neon": "a vibrant neon-lit backdrop",
        "wood": "warm natural wood",
    },
    # guided picker
    "what": {
        "image": "A striking image",
        "ad": "An advertising creative",
        "avatar": "A brand avatar",
        "product": "A product hero shot",
        "video": "A cinematic video keyframe",
    },
    "style": {
        "realism": "photorealistic, lifelike detail",
        "3d": "3D render, soft global illumination",
        "luxury": "luxury premium aesthetic, elegant",
        "anime": "anime illustration style, clean linework",
        "cinematic": "cinematic lighting, dramatic mood",
        "minimal": "clean minimalist style, lots of negative space",
        "mktclean": "clean marketplace product style, crisp and bright",
    },
    "format": {
        "square": "square 1:1 composition",
        "story": "vertical 9:16 story composition",
        "banner": "wide 16:9 banner composition",
        "avatar": "centered square avatar composition",
    },
}

# Optional questions whose phrasing wraps the user's free text when present.
_TEXT_WRAPPERS: dict[str, str] = {
    "pet_photo_animation.detail": "preserve exactly: {v}",
    "marketplace_white_bg.detail": "preserve and emphasize: {v}",
    "product_card.audience": "aimed at {v}",
    "ad_banner.text_on_image": 'with clear space for the headline text "{v}"',
    "tg_post_cover.text_on_image": 'with space for the title text "{v}"',
    "brand_avatar.colors": "color palette: {v}",
}


# ── skeleton loading (lazy + cached) ───────────────────────────────────
_SKELETON_CACHE: dict[str, str] = {}


def _strip_header(raw: str) -> str:
    """Drop leading ``#`` comment lines; keep the skeleton body."""
    body_lines: list[str] = []
    for line in raw.splitlines():
        if line.lstrip().startswith("#"):
            continue
        body_lines.append(line)
    return "\n".join(body_lines).strip()


def _load_skeleton(path: Path, cache_key: str) -> str | None:
    """Read + strip a skeleton file, caching the result. None if unreadable."""
    if cache_key in _SKELETON_CACHE:
        return _SKELETON_CACHE[cache_key]
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    body = _strip_header(raw)
    if not body:
        return None
    _SKELETON_CACHE[cache_key] = body
    return body


def _template_skeleton(tid: str) -> str | None:
    return _load_skeleton(_TEMPLATES_DIR / f"{tid}.txt", f"tpl:{tid}")


def _guided_skeleton() -> str | None:
    return _load_skeleton(_GUIDED_FILE, "guided")


# ── text normalization ─────────────────────────────────────────────────
def _collapse(text: str) -> str:
    """Collapse repeated spaces/commas, drop empty fragments, trim."""
    # Remove standalone leftover format markers just in case.
    text = text.replace("{}", " ")
    # Collapse whitespace.
    text = re.sub(r"[ \t]+", " ", text)
    # Drop spaces before punctuation.
    text = re.sub(r"\s+([,.])", r"\1", text)
    # Collapse runs of commas (possibly separated by spaces) into one.
    text = re.sub(r"(,\s*){2,}", ", ", text)
    # Collapse comma directly before a period.
    text = re.sub(r",\s*\.", ".", text)
    # Collapse multiple periods.
    text = re.sub(r"\.{2,}", ".", text)
    # Tidy newlines.
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    # Trim leading commas/spaces and trailing separators per line.
    out_lines = []
    for line in text.split("\n"):
        line = line.strip()
        line = re.sub(r"^[,\s]+", "", line)
        line = re.sub(r"[ ,]+$", "", line)
        out_lines.append(line)
    text = "\n".join(l for l in out_lines if l)
    return text.strip()


def _phrase_for(qkey_ns: str, raw_value: str, label_by_value: dict[str, str]) -> str:
    """Map a choice value to an English phrase, falling back to label/value."""
    table = _CHOICE_PHRASES.get(qkey_ns)
    if table and raw_value in table:
        return table[raw_value]
    # Fall back to the RU label, then the raw value itself.
    return label_by_value.get(raw_value, raw_value)


def _slot_value(template_id: str, q: dict[str, Any], answers: dict[str, Any]) -> str:
    """Resolve the substitution string for one question's slot."""
    key = q["key"]
    raw = answers.get(key, "")
    if raw is None:
        raw = ""
    raw = str(raw).strip()
    ns = f"{template_id}.{key}"

    if q["type"] == "choice":
        if not raw:
            return ""
        label_by_value = {o["value"]: o["label"] for o in q["options"]}
        return _phrase_for(ns, raw, label_by_value)

    # free text
    if not raw:
        return ""
    wrapper = _TEXT_WRAPPERS.get(ns)
    if wrapper:
        return wrapper.format(v=raw)
    return raw


# ── public API: templates ──────────────────────────────────────────────
def template_ids() -> list[str]:
    """Template ids in stable display order."""
    return [t["id"] for t in _TEMPLATES]


def get_template(tid: str) -> dict | None:
    """Public template descriptor (id/title/questions). No skeleton text."""
    t = _TEMPLATES_BY_ID.get(tid)
    if t is None:
        return None
    return {
        "id": t["id"],
        "title": t["title"],
        "questions": template_questions(tid),
    }


def template_target(tid: str) -> str:
    """Return 'video' if the template should route to the video wizard, else 'image'."""
    t = _TEMPLATES_BY_ID.get(tid)
    return (t or {}).get("target", "image")


def template_questions(tid: str) -> list[dict]:
    """Ordered question specs for ``tid`` (empty list if unknown)."""
    t = _TEMPLATES_BY_ID.get(tid)
    if t is None:
        return []
    # Deep-ish copy so callers can't mutate module state.
    out: list[dict] = []
    for q in t["questions"]:
        out.append(
            {
                "key": q["key"],
                "text": q["text"],
                "type": q["type"],
                "options": [dict(o) for o in q["options"]],
                "optional": q["optional"],
            }
        )
    return out


def compose_template_prompt(tid: str, answers: dict) -> str:
    """Compose a final image prompt for template ``tid``. Never raises.

    Unknown ``tid`` -> "". On any internal error, degrade to a safe minimal
    prompt built from whatever free-text answers were supplied.
    """
    if tid not in _TEMPLATES_BY_ID:
        return ""
    try:
        return _compose_template_inner(tid, answers if isinstance(answers, dict) else {})
    except Exception:
        return _safe_fallback(answers)


def _compose_template_inner(tid: str, answers: dict) -> str:
    template = _TEMPLATES_BY_ID[tid]
    slots = {q["key"]: _slot_value(tid, q, answers) for q in template["questions"]}

    skeleton = _template_skeleton(tid)
    if skeleton is None:
        skeleton = _fallback_skeleton(template)

    # Substitute; tolerate any stray placeholder not covered by our slots.
    try:
        composed = skeleton.format_map(_DefaultBlank(slots))
    except Exception:
        composed = skeleton.format_map(_DefaultBlank({}))

    composed = _collapse(composed)
    if len(composed) < 3:
        return _safe_fallback(answers)
    return composed


def _fallback_skeleton(template: dict[str, Any]) -> str:
    """Build a serviceable skeleton from the question keys if a file is missing."""
    parts = ["{" + q["key"] + "}" for q in template["questions"]]
    return ", ".join(parts) + ", high quality, detailed, sharp focus."


# ── public API: guided picker ──────────────────────────────────────────
def guided_steps() -> list[dict]:
    """Ordered guided-picker step specs (choice questions)."""
    out: list[dict] = []
    for s in _GUIDED_STEPS:
        out.append(
            {
                "key": s["key"],
                "text": s["text"],
                "type": s["type"],
                "options": [dict(o) for o in s["options"]],
                "optional": s["optional"],
            }
        )
    return out


def compose_guided_prompt(answers: dict) -> str:
    """Compose a generic image prompt from guided answers. Never raises."""
    try:
        return _compose_guided_inner(answers if isinstance(answers, dict) else {})
    except Exception:
        return _safe_fallback(answers)


def _compose_guided_inner(answers: dict) -> str:
    slots: dict[str, str] = {}
    for s in _GUIDED_STEPS:
        key = s["key"]
        raw = answers.get(key, "")
        if raw is None:
            raw = ""
        raw = str(raw).strip()
        if not raw:
            slots[key] = ""
            continue
        table = _CHOICE_PHRASES.get(key, {})
        label_by_value = {o["value"]: o["label"] for o in s["options"]}
        slots[key] = table.get(raw, label_by_value.get(raw, raw))

    skeleton = _guided_skeleton()
    if skeleton is None:
        skeleton = "{what}, {style}, {format}, high quality, detailed, sharp focus."

    try:
        composed = skeleton.format_map(_DefaultBlank(slots))
    except Exception:
        composed = skeleton.format_map(_DefaultBlank({}))

    composed = _collapse(composed)
    if len(composed) < 3:
        return _safe_fallback(answers)
    return composed


# ── shared fallbacks ───────────────────────────────────────────────────
class _DefaultBlank(dict):
    """dict for ``str.format_map`` that returns '' for any missing key."""

    def __missing__(self, key: str) -> str:  # noqa: D401
        return ""


def _safe_fallback(answers: Any) -> str:
    """Last-resort prompt: join free-text answers, else a generic prompt."""
    try:
        if isinstance(answers, dict):
            parts = [str(v).strip() for v in answers.values() if str(v).strip()]
            joined = _collapse(", ".join(parts))
            if len(joined) >= 3:
                return joined
    except Exception:
        pass
    return _FALLBACK_PROMPT


# Ready-made idea prompts for the "quick idea" nudge (digest re-engagement +
# wizard picker). Pure data, no logic.
QUICK_IDEAS: list[str] = [
    "котик в стиле студии Гибли, мягкий свет",
    "киберпанк Москва ночью, неоновые вывески",
    "акварельный портрет девушки с рыжими кудрями",
    "уютная кофейня осенью, дождь за окном, тёплый свет",
    "астронавт на Марсе, алый закат, одиночество",
    "дракон из кристаллов льда, горы на фоне",
    "магический лес с грибами-фонарями ночью",
    "ретро-автомобиль 60-х, пастельные тона, поп-арт",
    "детёныш лисы в снегу, крупный план, профессиональное фото",
    "японский сад сакуры на рассвете, туман",
    "пиратский корабль в шторм, масло, кино-кадр",
    "город-пузырь под водой, биолюминесценция",
    "девушка читает книгу в библиотеке с высокими потолками",
    "волк воет на луну, силуэт, минимализм",
    "тёплая кухня бабушки с пирогами, солнечный полдень",
    "неоновый самурай в пустом метро",
    "зачарованный замок в облаках, золотой час",
    "фотореализм: капля воды на лепестке розы, макро",
    "медведь-художник рисует пейзаж в берёзовом лесу",
    "будущее: летающие сады над мегаполисом",
    "лиса-шаман у костра в зимнем лесу, северное сияние",
    "стимпанк-дирижабль над облаками, тёплый закатный свет",
    "минималистичный логотип-горы, плоский дизайн, два цвета",
    "котёнок-астронавт в шлеме, смотрит на Землю, мультяшно",
    "уличная еда в Токио ночью, неон, отражения в лужах",
    "девушка-эльф в доспехах из листьев, фэнтези, кинопостер",
    "тёплый плед, какао и книга у окна, за окном снегопад",
    "робот поливает цветы на заброшенной станции, мягкий свет",
    "винтажный мотоцикл на фоне пустыни, золотой час, плёнка",
    "подводный город с медузами-фонарями, бирюзовая дымка",
    "пушистый корги в свитере, студийный портрет, боке",
    "горный замок на рассвете, туман в долине, эпично",
    "капкейк-галактика со звёздной глазурью, макро-съёмка",
    "лес из гигантских грибов, светлячки, сказочная атмосфера",
    "ретро-постер путешествия на Марс, плакат 50-х",
    "кот в деловом костюме пьёт кофе в офисе, юмор, фотореализм",
    "балерина из дыма и света на тёмной сцене, длинная выдержка",
    "домик на дереве с гирляндами в осеннем лесу, уют",
    "феникс из золотых искр взлетает над вулканом, динамично",
]
