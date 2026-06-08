import requests

# === Вставь сюда ОДИН из сгенерированных IP:PORT ===
PROXY_SERVER = "192.0.2.10:9797"

# Формируем URL для requests
proxy_url = f"http://{PROXY_SERVER}"

proxies = {
    "http": proxy_url,
    "https": proxy_url,
}

print(f"⏳ Проверяю Whitelist прокси: {PROXY_SERVER}...")

try:
    # Проверяем IP, который видит конечный сайт
    r = requests.get("http://ip-api.com/json", proxies=proxies, timeout=5)
    r.raise_for_status()  # Проверка на ошибки
    data = r.json()

    print("\n✅ УСПЕХ! Прокси работает.")
    print(f"🌍 IP, который видит Google: {data.get('query')}")
    print(f"📍 Страна: {data.get('country')}, Город: {data.get('city')}")
    print("\nТеперь бот точно запустится!")

except Exception as e:
    print(f"\n❌ ОШИКА ПОДКЛЮЧЕНИЯ: {e}")
    print(
        "Что делать:\n1. Убедись, что ты запускаешь скрипт с IP, который в белом списке (192.0.2.10)."
    )
    print("2. Попробуй другой IP:PORT из списка на сайте 2captcha.")
