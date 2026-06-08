"""Russian microcopy for the button-driven Flow bot UI.

Authored by the ``telegram-copywriter`` agent (see
``.claude/agents/telegram-copywriter.md``). Kept as a standalone, stdlib-only
module so wording is versioned in one place (per PromptOps) and unit-testable
without aiogram. Engineers wire labels/messages by stable English keys; values
are Russian. Pack labels are built from pricing numbers (see ``flow_core``), not
hardcoded here, so copy and prices never drift apart.
"""

from __future__ import annotations

# ── button labels (key -> Russian label) ──────────────────────────────

LABELS = {
    # main menu (inline)
    "gen": "🎨 Создать картинку",
    "balance": "💳 Баланс и пополнение",
    "help": "❓ Как пользоваться",
    "myphoto": "🖼 Изменить моё фото",
    "menu": "🏠 Главное меню",
    # persistent reply-keyboard (always visible at the bottom of the chat)
    "kb_gen": "🎨 Создать картинку",
    "kb_menu": "🏠 Меню",
    "kb_balance": "💳 Баланс",
    # wizard: count
    "cnt:1": "1 фото",
    "cnt:2": "2 фото",
    "cnt:4": "4 фото",
    # wizard: format
    "fmt:land": "🖥 Альбом 16:9",
    "fmt:port": "📱 Портрет 9:16",
    "fmt:sq": "⬜ Квадрат 1:1",
    # wizard: confirm / nav
    "go": "✨ Сгенерировать",
    "back": "← Назад",
    "cancel": "✕ Отмена",
    "repeat_last": "🔁 Повторить так же",
    # per-image actions
    "dl_raw": "⬇ Скачать оригинал",
    "up2x": "✨ Чёткость ×2",       # быстрая доработка детализации (по промпту)
    "realup": "🔍 Апскейл",         # увеличение разрешения (родное у сервиса)
    "edit": "✏️ Изменить",
    "revary": "🎲 Варианты",
    "regen": "🔄 Заново",
    "mix": "➕ В микс",             # добавить картинку в коллаж-микс
    # credits
    "topup": "➕ Пополнить баланс",
    # ── video: main entry ─────────────────────────────────────────────────
    "vid_gen": "🎬 Создать видео",
    "kb_vid": "🎬 Создать видео",
    # video: family picker
    "vid_fam:omni": "⚡ Быстрое — дешевле",
    "vid_fam:veo": "✨ Кино — премиум",
    "vid_fam:ing": "🧩 Из фото + текст",
    "vid_fam:frm": "🎞 Старт → Финиш",
    # video: wizard confirm / nav
    "vid_go": "▶️ Создать видео",
    "vid_back:fam": "← Стиль",
    "vid_back:model": "← Изменить вариант",
    "vid_repeat_last": "🔁 Повторить",
    # video: per-video actions
    "vid_dl": "⬇ Скачать оригинал",
    "vid_retry": "🔄 Попробовать снова",
    # video: ingredients mode
    "vid_ing_done": "✅ Готово — ввести запрос",
    "vid_ing_clear": "🗑 Очистить",
    # video: frames mode
    "vid_frm_start": "🖼 Задать начало",
    "vid_frm_end": "🖼 Задать конец",
    "vid_frm_clear": "🗑 Очистить оба",
    "vid_frm_go": "Далее →",
    # video: model display names (neutral, benefit-led — never brand names)
    "vid_model_name:omni-flash-4s": "⚡ 4 сек · мини",
    "vid_model_name:omni-flash-6s": "⚡ 6 сек",
    "vid_model_name:omni-flash-8s": "⚡ 8 сек",
    "vid_model_name:omni-flash-10s": "⚡ 10 сек",
    "vid_model_name:veo-lite": "Veo Lite",
    "vid_model_name:veo-fast": "Veo Fast",
    "vid_model_name:veo-quality": "Veo Quality",
}

# ── selection markers for the single-screen picker ─────────────────────

SELECTED = "✅ "  # prefix shown on the currently chosen count/format button

# ── screen / status / error messages (key -> template) ─────────────────

MESSAGES = {
    "welcome": (
        "Привет! Я создаю картинки по текстовому описанию — быстро и красиво.\n\n"
        "Нажми «🎨 Создать картинку» внизу или в меню, опиши задумку — и получишь "
        "результат. Под каждой картинкой будут кнопки: изменить, сделать вариации, "
        "улучшить качество, скачать оригинал."
    ),
    "menu_title": "Главное меню. Выбери, что сделать:",
    "ask_count": "Сколько изображений создать?",
    "ask_format": "Выбери формат — под что нужна картинка?",
    "wizard_screen": (
        "⚙️ Настройки генерации\n\n"
        "Количество: {count} шт · Формат: {fmt}\n"
        "Стоимость: {price} кр. (у тебя {credits} кр.)\n\n"
        "Измени параметры кнопками ниже и нажми «✨ Сгенерировать»."
    ),
    # показывается над экраном настроек, когда промпт уже введён в чат
    "wizard_prompt_note": "📝 Твой запрос: «{prompt}»\n\n",
    "ask_prompt": (
        "Опиши, что хочешь увидеть на картинке.\n"
        "Чем подробнее — тем точнее результат. Например: "
        "«рыжий кот в очках сидит за ноутбуком, мягкий свет, фотореализм»."
    ),
    "generating": "🎨 Создаю изображение… это займёт несколько секунд.",
    "sending": "Готово! Отправляю результат…",
    "balance_screen": (
        "💳 Твой баланс: {credits} кр.\n\n"
        "1 картинка — {price} кр. Скачивание оригинала — бесплатно.\n"
        "Пополнить можно звёздами Telegram ⭐."
    ),
    "low_balance": (
        "Не хватает кредитов: нужно {needed} кр., а у тебя {have} кр.\n"
        "Пополни баланс звёздами Telegram — и продолжим!"
    ),
    "topup_screen": (
        "Выбери пакет кредитов. Оплата — звёздами Telegram ⭐, "
        "прямо внутри приложения:"
    ),
    "topup_done": "✅ Баланс пополнен на {credits} кр. Теперь у тебя {balance} кр.",
    "cooldown_wait": "Небольшая пауза — подожди ещё {sec} сек.",
    "busy": "Твой предыдущий запрос ещё выполняется — дождись результата 🙏",
    "prompt_too_short": "Опиши чуть подробнее — хотя бы пара слов.",
    "gen_failed": "Что-то пошло не так при создании. Кредиты не списаны, попробуй ещё раз!",
    "nothing_returned": "Сервис не вернул изображение. Кредиты не списаны, попробуй позже.",
    "photo_uploaded_ask_prompt": "Фото получено! Напиши, что в нём изменить.",
    "ask_photo": (
        "Пришли своё фото (можно сразу с подписью — что в нём изменить).\n"
        "Я загружу его и отредактирую по твоему описанию."
    ),
    "uploading_photo": "⬆️ Загружаю ваше фото…",
    "upload_failed": "Не удалось загрузить фото. Попробуй отправить ещё раз.",
    "result_caption": "Изображение {i} из {n}",
    "edit_caption": "✏️ Результат {i}/{n}",
    "ask_edit_prompt": "Напиши, что изменить в этой картинке (например: «сделай фон ночным»).",
    "ask_revary_prompt": "Напиши новый промпт — сделаю вариации на его основе.",
    "expired": "Эта кнопка устарела — создай картинку заново.",
    # нейтральные статусы/ошибки (без упоминания бэкенда)
    "working": "🔐 Подключаюсь к сервису…",
    "generating_n": "🎨 Создаю {n} изображений…",
    "applying_edit": "✏️ Применяю правку…",
    "parse_failed": "⚠️ Не удалось обработать ответ сервиса.",
    "rate_limited": "⏱️ Слишком много запросов. Подождите минуту и попробуйте снова.",
    "service_error": "⚠️ Сервис вернул ошибку: {status}",
    "preparing_file": "⬇️ Готовлю файл в полном качестве…",
    "upscaling": "🔍 Увеличиваю разрешение, подождите…",
    "upscale_unavailable": "🔍 Увеличение разрешения временно недоступно — попробуйте чуть позже.",
    "admin_granted": "✅ Начислено {credits} кр. пользователю {target}. Его баланс: {balance} кр.",
    "admin_denied": "Команда доступна только администраторам бота.",
    "admin_usage": "Использование: /grant <user_id> <кредиты>",
    # ── video messages ────────────────────────────────────────────────────
    "vid_family_screen": (
        "🎬 Создание видео\n\n"
        "Выбери стиль генерации:"
    ),
    "vid_variant_screen": (
        "🎬 {family}\n\n"
        "Выбери вариант — они отличаются длиной и качеством:"
    ),
    "vid_settings_screen": (
        "⚙️ Настройки видео\n\n"
        "Вариант: {model} · Формат: {fmt} · Кол-во: {count} шт.\n"
        "Стоимость: {price} кр. (у тебя {credits} кр.)\n\n"
        "Нажми «▶️ Создать видео» или измени параметры."
    ),
    "vid_ask_prompt": (
        "Опиши, что должно происходить в видео.\n"
        "Укажи движение и действие — так результат получится живее. Например: "
        "«камера медленно облетает заснеженный лес, хлопья снега падают крупным планом»."
    ),
    "vid_working": "🎬 Запускаю создание видео…",
    "vid_generating_elapsed": (
        "🎬 Создаю видео… уже {elapsed}.\n"
        "Видео готовится дольше картинок — пожалуйста, подожди немного."
    ),
    "vid_result_caption": "Видео {i} из {n} · «{prompt}»",
    "vid_gen_failed": "Не удалось создать видео. Кредиты возвращены — попробуй ещё раз!",
    "vid_busy": "Твоё предыдущее видео ещё создаётся — дождись результата 🙏",
    "vid_expired_wizard": "Эти кнопки устарели — начни создание видео заново.",
    "vid_prompt_too_short": "Опиши сцену чуть подробнее — хотя бы пара слов.",
    "vid_text_only_hint": "Здесь жду текстовый запрос, а не фото. Напиши описание видео.",
    "vid_ing_screen": (
        "🧩 Ингредиенты — {n}/4 фото добавлено\n"
        "Режим: {model} · Стоимость: {price} кр. (у тебя {credits} кр.)\n\n"
        "Пришли 2–4 фото, которые лягут в основу видео. "
        "Когда готово — нажми «✅ Готово — ввести запрос»."
    ),
    "vid_ing_ask_prompt": (
        "Фото получены! Теперь опиши, что должно происходить в видео."
    ),
    "vid_ing_send_photo": "Пришли фото — текст здесь не подойдёт.",
    "vid_ing_need_more": "Добавь ещё фото — нужно минимум два.",
    "vid_gen_blocked": (
        "Этот режим пока недоступен — скоро откроем. Кредиты не списаны."
    ),
    "vid_frm_screen": (
        "🎞 Кадры\n\n"
        "Режим: {model} · Стоимость: {price} кр. (у тебя {credits} кр.)\n"
        "Задай начальный и конечный кадр — и видео плавно перейдёт между ними."
    ),
    "vid_frm_send_photo_start": "Пришли фото для первого кадра (начало видео).",
    "vid_frm_send_photo_end": "Отлично! Теперь пришли фото для последнего кадра (конец видео).",
    "vid_frm_ask_prompt": (
        "Можешь добавить текстовое описание перехода — или просто нажми «Далее →», "
        "если описание не нужно."
    ),
    "vid_frm_need_both": "Сначала задай оба кадра — начало и конец.",
    "vid_photo_expected": "Здесь жду фото, а не текст. Пришли изображение.",
    "help": (
        "ℹ️ Как пользоваться ботом:\n\n"
        "1. Нажми «🎨 Создать картинку».\n"
        "2. На одном экране выбери количество и формат, затем «✨ Сгенерировать».\n"
        "3. Пришли текстовое описание картинки.\n"
        "4. Под каждым результатом — кнопки: ✏️ изменить, 🎲 варианты, "
        "🔄 заново, ✨ чёткость ×2, 🔍 апскейл, ⬇ скачать оригинал.\n\n"
        "🎬 «Создать видео» — выбери стиль и вариант, опиши сцену и получи видеоролик.\n\n"
        "🖼 «Изменить моё фото» — пришли своё фото с подписью, и я отредактирую его.\n"
        "🔁 «Повторить так же» — та же генерация в один тап.\n\n"
        "Кредиты тратятся на генерацию; скачивание оригинала бесплатно. "
        "Пополнение — звёздами Telegram ⭐."
    ),
}


def label(key: str) -> str:
    """Button label for ``key`` (falls back to the key itself)."""
    return LABELS.get(key, key)


def msg(key: str, **kwargs) -> str:
    """Message text for ``key``, ``.format(**kwargs)``-ed when placeholders exist."""
    template = MESSAGES.get(key, key)
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return template
    return template
