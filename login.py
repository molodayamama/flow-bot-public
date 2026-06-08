"""Одноразовый помощник: создать залогиненный Chrome-профиль для Flow-бота.

Что делает:
  1. (Пере)создаёт папку профиля ``USER_DATA_DIR`` (по умолчанию ./google_profile).
  2. Открывает видимый Chrome на странице Google Labs Flow.
  3. Ты вручную логинишься в Google, создаёшь New flow и генерируешь одну
     картинку на сайте (прогревает проект/квоту).
  4. По Enter профиль сохраняется и используется ботом (flow_bot.py) и
     инструментом захвата (tools/capture_video.py).

ВНИМАНИЕ: шаг 1 удаляет существующий ``USER_DATA_DIR``. Запускай осознанно.

Прокси по умолчанию НЕ используется (мы от них отказались). Если профиль нужно
создать через прокси — задай ``BROWSER_PROXY_URL`` в .env (значения off/none/direct
отключают его), как и для самого бота.
"""
import os
import shutil

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

FLOW_URL = "https://labs.google/fx/tools/flow"
USER_DATA_DIR = os.path.abspath(os.getenv("USER_DATA_DIR", "./google_profile"))


def _browser_proxy() -> dict | None:
    """Опциональный прокси для Chrome из BROWSER_PROXY_URL (по умолчанию — нет)."""
    raw = (os.getenv("BROWSER_PROXY_URL") or "").strip()
    if not raw or raw.lower() in {"off", "none", "direct", "0", "false"}:
        return None
    if "://" not in raw:
        raw = f"http://{raw}"
    return {"server": raw}


def login_manual() -> None:
    if os.path.exists(USER_DATA_DIR):
        print("🧹 Удаляю старый профиль...")
        shutil.rmtree(USER_DATA_DIR)
    os.makedirs(USER_DATA_DIR)

    proxy = _browser_proxy()
    print(f"🚀 Запускаю Chrome{' через прокси' if proxy else ' без прокси'}...")

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            channel="chrome",
            headless=False,  # видимое окно для ручного логина
            proxy=proxy,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--start-maximized",
            ],
        )

        page = browser.pages[0] if browser.pages else browser.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        print("🌍 Перехожу на Google Labs Flow...")
        page.goto(FLOW_URL, timeout=0)

        print("\n" + "=" * 60)
        print("ТВОЯ ЗАДАЧА в открытом окне:")
        print("1. Войди в Google аккаунт.")
        print("2. Нажми 'New flow', чтобы открылся холст с проектом.")
        print("3. Сгенерируй ОДНУ картинку прямо на сайте.")
        print("4. Когда картинка создалась — вернись сюда и нажми ENTER.")
        print("=" * 60 + "\n")

        input("Нажми Enter после генерации картинки на сайте...")

        print(f"Текущий URL: {page.url}")
        browser.close()
        print("✅ Профиль успешно создан и сохранён.")


if __name__ == "__main__":
    login_manual()
