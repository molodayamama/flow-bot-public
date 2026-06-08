"""
Скрипт для поиска правильных параметров reCAPTCHA на labs.google
"""

print("=" * 60)
print("🔍 Поиск параметров reCAPTCHA для Google Labs Flow")
print("=" * 60)

print("\n📋 Инструкция:")
print("\n1. Откройте https://labs.google/fx/tools/flow")
print("2. Откройте DevTools (F12)")
print("3. Перейдите в Console")
print("4. Вставьте и выполните следующий код:\n")

js_code = """
// Скрипт для поиска параметров reCAPTCHA
(function() {
    console.log("=".repeat(60));
    console.log("🔍 Поиск reCAPTCHA параметров");
    console.log("=".repeat(60));
    
    // Метод 1: Поиск по data-sitekey
    const sitekeyElements = document.querySelectorAll('[data-sitekey]');
    if (sitekeyElements.length > 0) {
        console.log("\\n✅ Найдены элементы с data-sitekey:");
        sitekeyElements.forEach((el, i) => {
            const sitekey = el.getAttribute('data-sitekey');
            const action = el.getAttribute('data-action');
            console.log(`\\n  ${i + 1}. Sitekey: ${sitekey}`);
            console.log(`     Action: ${action || 'не указан'}`);
        });
    }
    
    // Метод 2: Поиск в скриптах
    console.log("\\n🔎 Поиск в скриптах...");
    const scripts = document.querySelectorAll('script');
    scripts.forEach((script, i) => {
        const content = script.textContent;
        
        // Ищем sitekey
        const sitekeyMatch = content.match(/sitekey['":\\s]+([a-zA-Z0-9_-]+)/i);
        if (sitekeyMatch) {
            console.log(`\\n  Script ${i}: Sitekey = ${sitekeyMatch[1]}`);
        }
        
        // Ищем action
        const actionMatch = content.match(/action['":\\s]+([a-zA-Z0-9_-]+)/i);
        if (actionMatch) {
            console.log(`  Script ${i}: Action = ${actionMatch[1]}`);
        }
    });
    
    // Метод 3: Поиск в window объекте
    console.log("\\n🌐 Поиск в window объекте...");
    for (let key in window) {
        if (key.toLowerCase().includes('recaptcha') || key.toLowerCase().includes('captcha')) {
            try {
                console.log(`  window.${key} =`, window[key]);
            } catch (e) {}
        }
    }
    
    // Метод 4: Перехват grecaptcha.execute
    console.log("\\n🎣 Устанавливаю перехватчик grecaptcha.execute...");
    if (window.grecaptcha && window.grecaptcha.execute) {
        const originalExecute = window.grecaptcha.execute;
        window.grecaptcha.execute = function(...args) {
            console.log("\\n🎯 ПЕРЕХВАЧЕН ВЫЗОВ grecaptcha.execute:");
            console.log("  Arguments:", args);
            if (args[0]) console.log("  Sitekey:", args[0]);
            if (args[1]) console.log("  Options:", args[1]);
            return originalExecute.apply(this, args);
        };
        console.log("✅ Перехватчик установлен! Теперь сделайте генерацию изображения.");
    } else {
        console.log("⚠️ grecaptcha.execute не найден");
    }
    
    console.log("\\n" + "=".repeat(60));
    console.log("📝 Теперь сделайте генерацию изображения в Flow");
    console.log("   и посмотрите что выведет перехватчик!");
    console.log("=".repeat(60));
})();
"""

print(js_code)

print("\n" + "=" * 60)
print("📝 Что делать дальше:")
print("=" * 60)

print("\n5. Вы увидите вывод с найденными параметрами")
print("6. Сделайте генерацию изображения")
print("7. Перехватчик покажет ТОЧНЫЕ параметры:")
print("   • Sitekey")
print("   • Action")
print("   • Enterprise (да/нет)")
print("   • MinScore")

print("\n8. Скопируйте найденные параметры сюда:")
print("\n   Sitekey: ________________")
print("   Action: ________________")
print("   Enterprise: ☐ Да  ☐ Нет")
print("   MinScore: ________________")

print("\n" + "=" * 60)
print("💡 Альтернативный метод (через Network):")
print("=" * 60)

print("\n1. DevTools → Network")
print("2. Очистите (кнопка 🚫)")
print("3. Сделайте генерацию")
print("4. Найдите запрос к 'recaptcha' или 'anchor'")
print("5. В URL параметрах ищите:")
print("   • k= или sitekey= (это sitekey)")
print("   • co= (домен)")
print("6. В Payload ищите:")
print("   • action (это action для v3)")

print("\n" + "=" * 60)
print("🎯 Пример правильных параметров:")
print("=" * 60)

print("""
Google обычно использует:
  • Sitekey: 6Ld... (40 символов)
  • Action: конкретное действие типа "submit", "generate", "verify"
  • Enterprise: Обычно НЕТ (0)
  • MinScore: 0.5 - 0.9
  
Для labs.google может быть:
  • Action: "PINHOLE" (название инструмента)
  • Action: "flowMedia"
  • Action: "generate"
  • Action: "generateImages"
""")

print("\n" + "=" * 60)
print("⚠️ ВАЖНО:")
print("=" * 60)

print("""
Если reCAPTCHA это Enterprise версия (часто у Google):
  • Нужно использовать enterprise=1 в 2Captcha
  • Sitekey может быть другим
  • Action может быть обязательным
  
Проверьте в Network запросах наличие:
  • enterprise/anchor вместо обычного api.js
  • Это будет означать Enterprise версию
""")

print("\n" + "=" * 60)
print("📤 Что делать после того, как нашли параметры:")
print("=" * 60)

print("""
1. Отправьте мне найденные параметры:
   Sitekey: ...
   Action: ...
   Enterprise: да/нет
   MinScore: ...

2. Я обновлю код бота с правильными параметрами

3. Протестируем снова!
""")

print("\n✅ Готово! Следуйте инструкциям выше.\n")
