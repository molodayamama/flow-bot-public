"""
Скрипт для тестирования Bearer Token и Cookies
Использование: python test_tokens.py
"""

import asyncio
import aiohttp
import os
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv(".env_flow")

BEARER_TOKEN = os.getenv("BEARER_TOKEN")
GOOGLE_COOKIES = {
    "SID": os.getenv("COOKIE_SID"),
    "__Secure-1PSID": os.getenv("COOKIE_SECURE_1PSID"),
    "__Secure-3PSID": os.getenv("COOKIE_SECURE_3PSID"),
    "HSID": os.getenv("COOKIE_HSID"),
    "SSID": os.getenv("COOKIE_SSID"),
    "__Secure-1PSIDTS": os.getenv("COOKIE_SECURE_1PSIDTS"),
    "__Secure-3PSIDTS": os.getenv("COOKIE_SECURE_3PSIDTS"),
}


def print_header(text):
    """Печатает красивый заголовок"""
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60)


def check_config():
    """Проверка конфигурации"""
    print_header("🔍 ПРОВЕРКА КОНФИГУРАЦИИ")

    issues = []

    # Проверка Bearer Token
    if not BEARER_TOKEN or BEARER_TOKEN.startswith("your_"):
        issues.append("❌ BEARER_TOKEN не настроен")
        print("❌ Bearer Token: НЕ НАСТРОЕН")
    else:
        print(f"✅ Bearer Token: {BEARER_TOKEN[:30]}...{BEARER_TOKEN[-20:]}")
        print(f"   Длина: {len(BEARER_TOKEN)} символов")

    # Проверка Cookies
    print("\n🍪 Cookies:")
    missing_cookies = []
    for name, value in GOOGLE_COOKIES.items():
        if not value or value.startswith("your_"):
            missing_cookies.append(name)
            print(f"   ❌ {name:25} НЕ НАСТРОЕН")
        else:
            print(f"   ✅ {name:25} {value[:20]}...")

    if missing_cookies:
        issues.append(f"❌ Не настроены cookies: {', '.join(missing_cookies)}")

    if issues:
        print("\n⚠️  ОБНАРУЖЕНЫ ПРОБЛЕМЫ:")
        for issue in issues:
            print(f"   {issue}")
        print("\n💡 Проверьте файл .env и обновите значения")
        return False

    print("\n✅ Конфигурация в порядке!")
    return True


async def test_api():
    """Тестирование API запроса"""
    print_header("🧪 ТЕСТИРОВАНИЕ API")

    api_url = "https://generativelanguage.googleapis.com/v1beta/models/nanobananapro-3:generateImages"

    headers = {
        "Authorization": f"Bearer {BEARER_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Origin": "https://labs.google",
        "Referer": "https://labs.google/fx/tools/flow",
    }

    payload = {
        "prompt": "test image generation",
        "config": {
            "aspectRatio": "16:9",
            "numberOfImages": 1,
            "model": "nanobananapro-3",
        },
    }

    print(f"📡 Отправка запроса к API...")
    print(f"   Endpoint: {api_url}")
    print(f"   Prompt: {payload['prompt']}")

    try:
        async with aiohttp.ClientSession(cookies=GOOGLE_COOKIES) as session:
            async with session.post(
                api_url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as response:

                status = response.status
                print(f"\n📊 Статус ответа: {status}")

                if status == 200:
                    data = await response.json()

                    if "images" in data and len(data["images"]) > 0:
                        print("✅ УСПЕХ! API работает корректно")
                        print(f"\n📷 Сгенерировано изображений: {len(data['images'])}")

                        for i, img in enumerate(data["images"], 1):
                            if "url" in img:
                                print(f"   {i}. {img['url'][:60]}...")

                        return True
                    else:
                        print("⚠️  Ответ получен, но изображения отсутствуют")
                        print(f"   Ответ: {str(data)[:200]}")
                        return False

                elif status == 401:
                    print("❌ ОШИБКА 401: Unauthorized")
                    print("\n💡 Решение:")
                    print("   1. Bearer Token устарел (живёт ~1 час)")
                    print("   2. Получите новый токен через DevTools:")
                    print("      - Откройте https://labs.google/fx/tools/flow")
                    print("      - F12 → Network → сделайте генерацию")
                    print("      - Найдите запрос generateImages")
                    print("      - Скопируйте новый Bearer Token")
                    print("   3. Обновите BEARER_TOKEN в .env файле")
                    return False

                elif status == 403:
                    print("❌ ОШИБКА 403: Forbidden")
                    print("\n💡 Возможные причины:")
                    print("   • Cookies устарели")
                    print("   • Google требует капчу")
                    print("   • Слишком много запросов")
                    print("\n💡 Решение:")
                    print("   1. Обновите все cookies в .env")
                    print("   2. Проверьте что сайт работает в браузере")
                    print("   3. Подождите 10-15 минут")
                    return False

                elif status == 429:
                    print("❌ ОШИБКА 429: Too Many Requests")
                    print("\n💡 Решение:")
                    print("   • Подождите 5-10 минут")
                    print("   • Не делайте слишком много запросов")
                    return False

                else:
                    text = await response.text()
                    print(f"❌ ОШИБКА {status}")
                    print(f"\n📄 Ответ сервера:")
                    print(f"   {text[:300]}")
                    return False

    except asyncio.TimeoutError:
        print("❌ TIMEOUT: Запрос занял слишком много времени")
        print("\n💡 Попробуйте ещё раз")
        return False

    except aiohttp.ClientError as e:
        print(f"❌ СЕТЕВАЯ ОШИБКА: {e}")
        print("\n💡 Проверьте подключение к интернету")
        return False

    except Exception as e:
        print(f"❌ НЕОЖИДАННАЯ ОШИБКА: {e}")
        import traceback

        traceback.print_exc()
        return False


async def main():
    """Основная функция"""
    print_header("🔧 Google Labs Token Tester")

    # Проверка конфигурации
    if not check_config():
        print("\n❌ Тест прерван из-за проблем с конфигурацией")
        return

    # Тестирование API
    print("\n⏳ Подождите 5 секунд перед тестом API...")
    await asyncio.sleep(5)

    success = await test_api()

    # Итоговый результат
    print_header("📋 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ")

    if success:
        print("✅ ВСЁ РАБОТАЕТ!")
        print("\n🎉 Вы можете запускать бота:")
        print("   python google_labs_bot_improved.py")
    else:
        print("❌ ОБНАРУЖЕНЫ ПРОБЛЕМЫ")
        print("\n📖 Следующие шаги:")
        print("   1. Изучите ошибки выше")
        print("   2. Обновите токены в .env файле")
        print("   3. Запустите тест снова: python test_tokens.py")
        print("   4. См. INSTALLATION_GUIDE.md для помощи")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Тест прерван пользователем")
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        import traceback

        traceback.print_exc()
