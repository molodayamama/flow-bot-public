"""
Тестирование разных параметров reCAPTCHA для поиска правильной комбинации
"""

import asyncio
from twocaptcha import TwoCaptcha
import os
from dotenv import load_dotenv

load_dotenv(".env_flow")

API_KEY = os.getenv("TWOCAPTCHA_API_KEY")

if not API_KEY or API_KEY.startswith("your_"):
    print("❌ Установите TWOCAPTCHA_API_KEY в .env_flow")
    exit(1)

solver = TwoCaptcha(API_KEY)

# Параметры для тестирования
SITEKEY = "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV"  # Найденный sitekey
URL = "https://labs.google/fx/tools/flow"

# Возможные action
ACTIONS = [
    "PINHOLE",  # Название инструмента
    "generate",  # Общее действие
    "flowMedia",  # Из endpoint
    "batchGenerateImages",  # Метод API
    "submit",  # Стандартное
    "verify",  # Стандартное
]

# Возможные scores
MIN_SCORES = [0.3, 0.5, 0.7, 0.9]

# Enterprise или нет
ENTERPRISE_OPTIONS = [0, 1]

print("=" * 60)
print("🧪 Тестирование параметров reCAPTCHA")
print("=" * 60)


async def test_captcha(action, min_score, enterprise):
    """Тест одной комбинации параметров"""
    try:
        print(f"\n🔍 Тестирую:")
        print(f"   Action: {action}")
        print(f"   MinScore: {min_score}")
        print(f"   Enterprise: {enterprise}")

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: solver.recaptcha(
                sitekey=SITEKEY,
                url=URL,
                version="v3",
                action=action,
                min_score=min_score,
                enterprise=enterprise,
            ),
        )

        token = result["code"]
        print(f"   ✅ Получен токен! Длина: {len(token)}")

        return {
            "action": action,
            "min_score": min_score,
            "enterprise": enterprise,
            "token": token,
            "success": True,
        }

    except Exception as exc:
        print(f"   ❌ Ошибка: {exc.__class__.__name__}")
        return {
            "action": action,
            "min_score": min_score,
            "enterprise": enterprise,
            "success": False,
            "error_type": exc.__class__.__name__,
        }


async def main():
    print("\n⚠️  ВАЖНО:")
    print("Этот скрипт только ПОЛУЧАЕТ токены через 2Captcha.")
    print("Он НЕ тестирует их с Google API (это стоит денег).")
    print()
    print("Для полного теста нужно:")
    print("1. Получить токен")
    print("2. Отправить запрос к Google API")
    print("3. Проверить работает ли")
    print()

    choice = input("Продолжить? (y/n): ")
    if choice.lower() != "y":
        print("Отменено")
        return

    print("\n" + "=" * 60)
    print("Начинаю тестирование...")
    print("=" * 60)

    results = []

    # Тестируем все комбинации
    for action in ACTIONS:
        for min_score in MIN_SCORES:
            for enterprise in ENTERPRISE_OPTIONS:
                result = await test_captcha(action, min_score, enterprise)
                results.append(result)

                # Небольшая пауза между запросами
                await asyncio.sleep(2)

    # Отчёт
    print("\n" + "=" * 60)
    print("📊 РЕЗУЛЬТАТЫ")
    print("=" * 60)

    successful = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]

    print(f"\n✅ Успешно: {len(successful)}")
    print(f"❌ Ошибок: {len(failed)}")

    if successful:
        print("\n✅ Успешные комбинации:")
        for r in successful:
            print(
                f"   • Action: {r['action']}, Score: {r['min_score']}, Enterprise: {r['enterprise']}"
            )
            print(f"     Token: получен, длина {len(r['token'])} (значение скрыто)")

    if failed:
        print("\n❌ Провальные комбинации:")
        for r in failed:
            print(
                f"   • Action: {r['action']}, Score: {r['min_score']}, Enterprise: {r['enterprise']}"
            )
            print(f"     Error type: {r.get('error_type', 'Unknown')}")

    print("\n" + "=" * 60)
    print("💡 Следующие шаги:")
    print("=" * 60)
    print(
        """
1. Все токены получены успешно - это НОРМАЛЬНО
   2Captcha просто решает капчу, не проверяя параметры

2. Чтобы узнать правильные параметры, нужно:
   а) Использовать find_recaptcha_params.py
   б) Проверить Network в DevTools
   в) Протестировать каждый токен с Google API

3. Скорее всего правильные параметры:
   - Action: "PINHOLE" или что-то специфичное для Flow
   - Enterprise: 1 (Google часто использует Enterprise)
   - MinScore: 0.7 или выше

4. Проверьте RECAPTCHA_DEBUG.md для детальных инструкций
"""
    )

    print("=" * 60)
    print(f"💰 Потрачено: ~${len(results) * 0.003:.3f}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
