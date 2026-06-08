import os
from playwright.sync_api import sync_playwright

# Папка профиля
USER_DATA_DIR = os.path.join(os.getcwd(), "google_profile")

# Твой Whitelist прокси
PROXY_SERVER = "192.0.2.10:8419"


def login_manual():
    # 1. Удаляем старый профиль, если он есть, для чистоты
    if os.path.exists(USER_DATA_DIR):
        print("🧹 Удаляю старый профиль...")
        import shutil

        shutil.rmtree(USER_DATA_DIR)

    os.makedirs(USER_DATA_DIR)

    print(f"🚀 Запускаю Chrome через прокси {PROXY_SERVER}...")

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            channel="chrome",
            headless=False,  # Видимое окно для логина
            proxy={"server": f"http://{PROXY_SERVER}"},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--start-maximized",
            ],
        )

        page = browser.pages[0] if browser.pages else browser.new_page()
        # Скрываем бота
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        print("🌍 Перехожу на Google Labs Flow...")
        page.goto("https://labs.google/fx/tools/flow", timeout=0)

        print("\n" + "=" * 60)
        print("🇺🇸 ТЫ ТЕПЕРЬ В НЬЮ-ЙОРКЕ. ТВОЯ ЗАДАЧА:")
        print("1. Войди в Google аккаунт.")
        print("2. Нажми кнопку 'New flow', чтобы открылся холст с проектом.")
        print("3. Сгенерируй ОДНУ картинку прямо там на сайте.")
        print("4. Если всё работает и картинка создалась — жми ENTER здесь.")
        print("=" * 60 + "\n")

        input("Нажми Enter после генерации картинки на сайте...")

        # Проверяем URL
        print(f"Текущий URL: {page.url}")
        browser.close()
        print("✅ Профиль 'американца' успешно создан и сохранен.")


if __name__ == "__main__":
    login_manual()
