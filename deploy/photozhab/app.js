"use strict";

const state = {
  session: null,
  mode: "image",
  imageData: null,
  imageName: "",
  busy: false,
  maxAuthAttempted: false,
  initialized: false,
  history: [],
};

const launchHash = new URLSearchParams(window.location.hash.slice(1));
const maxInitData = launchHash.get("WebAppData") || "";
if (maxInitData) history.replaceState(null, "", `${location.pathname}${location.search}`);
const TELEGRAM_PENDING_KEY = "photozhabTelegramPendingAt";
const TELEGRAM_PENDING_MAX_AGE = 11 * 60 * 1000;

const $ = (selector) => document.querySelector(selector);
const elements = {
  conversation: $("#conversation"), messages: $("#message-list"), welcome: $("#welcome-card"),
  prompt: $("#prompt"), send: $("#send-button"), model: $("#model-select"),
  mode: $("#mode-select"),
  aspect: $("#aspect-select"), count: $("#count-select"), countWrap: $(".count-select"),
  uploadButton: $("#upload-button"), uploadInput: $("#image-upload"), uploadPreview: $("#upload-preview"),
  uploadImage: $("#upload-preview-image"), uploadName: $("#upload-file-name"),
  price: $("#request-price"), title: $("#chat-mode-title"), subtitle: $("#chat-mode-subtitle"),
  sidebarBalance: $("#sidebar-balance"), mobileBalance: $("#mobile-balance-value"),
  dialog: $("#payment-dialog"), packs: $("#pack-grid"), toast: $("#toast"),
  authDialog: $("#auth-dialog"), telegramLogin: $("#login-telegram"),
  maxLogin: $("#login-max"), yandexLogin: $("#login-yandex"),
  telegramForm: $("#telegram-code-form"), telegramCode: $("#telegram-code"),
  authNote: $("#auth-note"), accountCard: $("#account-card"),
  accountName: $("#account-name"), logout: $("#logout-button"),
  mobileAccount: $("#mobile-account"),
  historyList: $("#chat-history-list"), historyEmpty: $("#chat-history-empty"),
};

const modeCopy = {
  image: ["Генерация картинки", "Nano Banana создаст изображение по описанию", "Опиши изображение, которое хочешь создать…"],
  edit: ["Редактирование фото", "Загрузи фото и опиши нужные изменения", "Что нужно изменить на фотографии?"],
  video: ["Генерация видео", "Veo или Omni создаст ролик по описанию", "Опиши сцену, движение камеры и атмосферу…"],
  animate: ["Оживление фото", "Загрузи фото и опиши желаемое движение", "Как должно ожить это фото?"],
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
  });
  let payload = {};
  try { payload = await response.json(); } catch (_) { payload = {error: "bad_response"}; }
  if (!response.ok) {
    const error = new Error(payload.error || "request_failed");
    error.payload = payload;
    error.status = response.status;
    throw error;
  }
  return payload;
}

function setBalance(value) {
  const balance = Number.isFinite(Number(value)) ? Number(value) : 0;
  elements.sidebarBalance.textContent = String(balance);
  elements.mobileBalance.textContent = String(balance);
  if (state.session) state.session.balance = balance;
}

function option(value, label) {
  const node = document.createElement("option");
  node.value = value;
  node.textContent = label;
  return node;
}

function selectedModel() {
  const models = state.mode === "image" || state.mode === "edit"
    ? state.session.image_models : state.session.video_models;
  return models.find((model) => model.id === elements.model.value) || models[0];
}

function requestPrice() {
  if (!state.session) return 0;
  const model = selectedModel();
  if (state.mode === "image") return Number(model.price) * Number(elements.count.value || 1);
  if (state.mode === "edit") return Number(state.session.prices.edit) + Number(model.price - state.session.prices.image);
  if (state.mode === "animate") return Number(model.animate_price ?? model.price);
  return Number(model.price);
}

function updatePrice() { elements.price.textContent = `${requestPrice()} кр`; }

function renderModels() {
  if (!state.session) return;
  const models = state.mode === "image" || state.mode === "edit"
    ? state.session.image_models : state.session.video_models;
  elements.model.replaceChildren(...models.map((model) => option(model.id, `${model.label} · ${state.mode === "animate" ? (model.animate_price ?? model.price) : model.price} кр`)));
  updatePrice();
}

function clearUpload() {
  state.imageData = null;
  state.imageName = "";
  elements.uploadInput.value = "";
  elements.uploadImage.removeAttribute("src");
  elements.uploadPreview.hidden = true;
}

function setMode(mode) {
  if (!modeCopy[mode] || state.busy) return;
  state.mode = mode;
  elements.mode.value = mode;
  document.querySelectorAll(".mode-nav-button").forEach((button) => button.classList.toggle("is-active", button.dataset.mode === mode));
  const [title, subtitle, placeholder] = modeCopy[mode];
  elements.title.textContent = title;
  elements.subtitle.textContent = subtitle;
  elements.prompt.placeholder = placeholder;
  const needsImage = mode === "edit" || mode === "animate";
  elements.uploadButton.hidden = !needsImage;
  elements.countWrap.hidden = mode !== "image";
  [...elements.aspect.options].forEach((item) => { item.disabled = (mode === "video" || mode === "animate") && !["portrait", "landscape"].includes(item.value); });
  if ((mode === "video" || mode === "animate") && !["portrait", "landscape"].includes(elements.aspect.value)) elements.aspect.value = "portrait";
  if (!needsImage) clearUpload();
  renderModels();
}

function toast(message) {
  elements.toast.textContent = message;
  elements.toast.hidden = false;
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => { elements.toast.hidden = true; }, 3600);
}

function providerLabel(provider) {
  return {telegram: "Telegram", max: "MAX", yandex: "Яндекс"}[provider] || "аккаунт";
}

function rememberRequest(prompt) {
  state.history = [prompt, ...state.history.filter((item) => item !== prompt)].slice(0, 6);
  elements.historyList.replaceChildren();
  state.history.forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chat-history-item";
    button.title = item;
    const mark = document.createElement("span");
    mark.textContent = "✦";
    mark.setAttribute("aria-hidden", "true");
    const label = document.createElement("span");
    label.textContent = item;
    button.append(mark, label);
    button.addEventListener("click", () => {
      elements.prompt.value = item;
      resizePrompt();
      elements.prompt.focus();
    });
    elements.historyList.append(button);
  });
}

function setProviderAvailability() {
  const providers = state.session?.auth?.providers || {};
  const maxEnabled = Boolean(providers.max);
  const yandexEnabled = Boolean(providers.yandex);
  elements.maxLogin.setAttribute("aria-disabled", String(!maxEnabled));
  elements.maxLogin.href = maxEnabled ? (state.session.auth.max_url || "#") : "#";
  elements.yandexLogin.setAttribute("aria-disabled", String(!yandexEnabled));
  elements.yandexLogin.href = yandexEnabled ? "/web/api/auth/yandex/start" : "#";
  elements.yandexLogin.querySelector("strong").textContent = yandexEnabled
    ? "Войти с Яндекс ID"
    : "Яндекс ID пока недоступен";
  const maxNote = maxEnabled
    ? "MAX-вход работает внутри официального мини-приложения Photozhab."
    : "Вход через MAX появится после подключения Mini App.";
  elements.authNote.textContent = yandexEnabled
    ? maxNote
    : `${maxNote} Яндекс ID включим после регистрации OAuth-приложения.`;
}

function applyAuthState() {
  const authenticated = Boolean(state.session?.authenticated);
  elements.accountCard.hidden = !authenticated;
  elements.accountName.textContent = authenticated
    ? `${state.session.identity?.display_name || "Пользователь"} · ${providerLabel(state.session.identity?.provider)}`
    : "—";
  elements.mobileAccount.textContent = authenticated ? "Аккаунт" : "Войти";
  elements.prompt.disabled = !authenticated;
  elements.send.disabled = !authenticated || state.busy;
  setProviderAvailability();
  if (authenticated) {
    if (elements.authDialog.open) elements.authDialog.close();
  } else if (!elements.authDialog.open) {
    elements.authDialog.showModal();
  }
}

async function startTelegramLogin() {
  elements.telegramLogin.disabled = true;
  const userAgent = globalThis.navigator?.userAgent || "";
  const useSameTab = window.innerWidth <= 820
    || window.matchMedia("(pointer: coarse)").matches
    || /Android|iPhone|iPad|iPod|Mobile/i.test(userAgent);
  const popup = useSameTab ? null : window.open("", "photozhab-telegram-login");
  if (popup) popup.opener = null;
  try {
    const result = await api("/web/api/auth/telegram/start", {method: "POST", body: "{}"});
    elements.telegramForm.hidden = false;
    elements.telegramCode.value = "";
    sessionStorage.setItem(TELEGRAM_PENDING_KEY, String(Date.now()));
    if (popup) popup.location.href = result.url;
    else {
      window.location.assign(result.url);
      return;
    }
    elements.telegramCode.focus();
  } catch (_) {
    if (popup) popup.close();
    toast("Не удалось открыть вход через Telegram. Попробуйте ещё раз.");
  } finally {
    elements.telegramLogin.disabled = false;
  }
}

async function completeTelegramLogin(event) {
  event.preventDefault();
  const code = elements.telegramCode.value.trim();
  if (!/^[0-9]{6}$/.test(code)) {
    toast("Введите шестизначный код из Telegram");
    return;
  }
  const submit = elements.telegramForm.querySelector("button[type=submit]");
  submit.disabled = true;
  try {
    await api("/web/api/auth/telegram/complete", {method: "POST", body: JSON.stringify({code})});
    sessionStorage.removeItem(TELEGRAM_PENDING_KEY);
    elements.telegramForm.hidden = true;
    await refreshSession();
    toast("Вход через Telegram выполнен");
  } catch (error) {
    toast(error.message === "invalid_code" ? "Код неверный, истёк или уже использован" : "Не удалось подтвердить вход");
  } finally {
    submit.disabled = false;
  }
}

function restoreTelegramPending() {
  const pendingAt = Number(sessionStorage.getItem(TELEGRAM_PENDING_KEY));
  if (!Number.isFinite(pendingAt) || pendingAt <= 0 || Date.now() - pendingAt > TELEGRAM_PENDING_MAX_AGE) {
    sessionStorage.removeItem(TELEGRAM_PENDING_KEY);
    return;
  }
  elements.telegramForm.hidden = false;
}

function handleYandexLogin(event) {
  if (elements.yandexLogin.getAttribute("aria-disabled") !== "true") return;
  event.preventDefault();
  toast("Вход через Яндекс пока подключается. Сейчас используйте Telegram или MAX.");
}

function consumeAuthResult() {
  const url = new URL(window.location.href);
  const result = url.searchParams.get("auth");
  if (!result) return "";
  url.searchParams.delete("auth");
  history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  return result;
}

async function tryMaxLogin() {
  if (!maxInitData || state.maxAuthAttempted || state.session?.authenticated) return;
  state.maxAuthAttempted = true;
  try {
    await api("/web/api/auth/max", {method: "POST", body: JSON.stringify({init_data: maxInitData})});
    state.session = await api("/web/api/session", {headers: {}});
    setBalance(state.session.balance); renderModels(); applyAuthState();
    toast("Вход через MAX выполнен");
  } catch (_) {
    toast("MAX не смог подтвердить вход. Откройте мини-приложение заново.");
  }
}

async function logoutUser() {
  try {
    await api("/web/api/auth/logout", {method: "POST", body: "{}"});
    state.session = await api("/web/api/session", {headers: {}});
    setBalance(0); applyAuthState();
  } catch (_) {
    toast("Не удалось выйти. Обновите страницу.");
  }
}

function message(role, text, sourceImage = null) {
  elements.welcome.hidden = true;
  const article = document.createElement("article");
  article.className = `chat-message ${role}`;
  const avatar = document.createElement("span");
  avatar.className = "message-avatar";
  avatar.textContent = role === "user" ? "Вы" : "P";
  const content = document.createElement("div");
  content.className = "message-content";
  if (text) { const paragraph = document.createElement("p"); paragraph.textContent = text; content.append(paragraph); }
  if (sourceImage) { const image = document.createElement("img"); image.className = "user-source-thumb"; image.src = sourceImage; image.alt = "Исходное изображение"; content.append(image); }
  article.append(avatar, content);
  elements.messages.append(article);
  scrollBottom();
  return {article, content};
}

function loadingMessage() {
  const item = message("assistant", "");
  const typing = document.createElement("span");
  typing.className = "typing";
  typing.setAttribute("aria-label", "Генерация выполняется");
  typing.append(document.createElement("i"), document.createElement("i"), document.createElement("i"));
  item.content.append(typing);
  const meta = document.createElement("div");
  meta.className = "message-meta";
  meta.textContent = state.mode === "video" || state.mode === "animate" ? "Видео обычно занимает несколько минут" : "Создаю результат";
  item.content.append(meta);
  return item;
}

function scrollBottom() { requestAnimationFrame(() => { elements.conversation.scrollTop = elements.conversation.scrollHeight; }); }

function renderResult(target, payload) {
  target.content.replaceChildren();
  const intro = document.createElement("p");
  intro.textContent = "Готово. Результат можно открыть или сохранить:";
  target.content.append(intro);
  const grid = document.createElement("div");
  grid.className = "media-grid";
  payload.media.forEach((media) => {
    const figure = document.createElement("figure");
    figure.className = "media-result";
    let node;
    if (media.type === "video") {
      node = document.createElement("video"); node.controls = true; node.playsInline = true; node.preload = "metadata";
    } else {
      node = document.createElement("img"); node.alt = "Изображение, созданное Photozhab"; node.loading = "lazy";
    }
    node.src = media.url;
    const link = document.createElement("a");
    link.className = "media-result-action"; link.href = media.url; link.target = "_blank"; link.rel = "noopener"; link.textContent = "Открыть ↗";
    figure.append(node, link); grid.append(figure);
  });
  target.content.append(grid);
  const meta = document.createElement("div");
  meta.className = "message-meta"; meta.textContent = `Списано ${payload.charged} кр · баланс ${payload.balance} кр`;
  target.content.append(meta);
  setBalance(payload.balance);
  scrollBottom();
}

const errorMessages = {
  auth_required: "Сначала войдите через Telegram, MAX или Яндекс.",
  insufficient_credits: "Недостаточно кредитов. Выберите пакет для пополнения баланса.",
  invalid_image: "Нужен PNG, JPEG или WebP размером до 10 МБ.",
  generation_in_progress: "Дождитесь завершения текущей генерации.",
  rate_limited: "Слишком много запросов подряд. Подождите минуту.",
  origin_rejected: "Защитная проверка запроса не пройдена. Обновите страницу.",
  generation_failed: "Генерация не завершилась. Кредиты возвращены — попробуйте другой запрос.",
};

async function generate() {
  const prompt = elements.prompt.value.trim();
  if (state.busy) return;
  if (!state.session?.authenticated) { applyAuthState(); return; }
  if (prompt.length < 3) { toast("Опишите идею хотя бы тремя символами"); elements.prompt.focus(); return; }
  if ((state.mode === "edit" || state.mode === "animate") && !state.imageData) { toast("Сначала добавьте фотографию"); elements.uploadInput.click(); return; }
  state.busy = true; elements.send.disabled = true;
  rememberRequest(prompt);
  message("user", prompt, state.imageData);
  const pending = loadingMessage();
  elements.prompt.value = ""; resizePrompt();
  try {
    const model = selectedModel();
    const payload = await api("/web/api/generate", {method: "POST", body: JSON.stringify({
      mode: state.mode, prompt, aspect: elements.aspect.value, count: Number(elements.count.value),
      image_model: state.mode === "image" || state.mode === "edit" ? model.id : undefined,
      video_model: state.mode === "video" || state.mode === "animate" ? model.id : undefined,
      image_b64: state.imageData,
    })});
    renderResult(pending, payload);
    if (state.mode === "edit" || state.mode === "animate") clearUpload();
  } catch (error) {
    pending.content.replaceChildren();
    const paragraph = document.createElement("p");
    paragraph.textContent = errorMessages[error.message] || "Сервис временно недоступен. Кредиты не списаны.";
    pending.content.append(paragraph);
    if (error.payload && Number.isFinite(Number(error.payload.balance))) setBalance(error.payload.balance);
    if (error.message === "insufficient_credits") openPayment();
    if (error.message === "auth_required") applyAuthState();
  } finally {
    state.busy = false; elements.send.disabled = false; elements.prompt.focus(); scrollBottom();
  }
}

function resizePrompt() { elements.prompt.style.height = "auto"; elements.prompt.style.height = `${Math.min(elements.prompt.scrollHeight, 180)}px`; }

function renderPacks() {
  const packs = state.session?.packs || [];
  elements.packs.replaceChildren();
  if (!packs.length) { const p = document.createElement("p"); p.textContent = "Оплата временно недоступна."; elements.packs.append(p); return; }
  packs.forEach((pack) => {
    const button = document.createElement("button"); button.type = "button"; button.className = `pack-button${pack.best ? " is-best" : ""}`;
    const copy = document.createElement("span"); const strong = document.createElement("strong"); strong.textContent = `${pack.credits} кр`;
    const hint = document.createElement("span"); hint.textContent = `≈ ${Math.floor(pack.credits / 10)} изображений`; copy.append(strong, hint);
    const price = document.createElement("em"); price.textContent = `${pack.rub} ₽`; button.append(copy, price);
    button.addEventListener("click", () => beginPayment(pack.id, button)); elements.packs.append(button);
  });
}

function openPayment() {
  if (!state.session?.authenticated) { applyAuthState(); return; }
  renderPacks(); if (!elements.dialog.open) elements.dialog.showModal();
}

async function beginPayment(packId, button) {
  button.disabled = true;
  try { const result = await api("/web/api/payment", {method: "POST", body: JSON.stringify({pack_id: packId})}); window.location.assign(result.url); }
  catch (_) { toast("Не удалось создать счёт. Попробуйте ещё раз."); button.disabled = false; }
}

async function refreshSession({retryAuthenticated = false} = {}) {
  const attempts = retryAuthenticated ? 3 : 1;
  let session = null;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      session = await api("/web/api/session", {headers: {}});
    } catch (_) {
      if (attempt === attempts - 1) {
        elements.prompt.disabled = true;
        elements.send.disabled = true;
        toast("Не удалось подключиться к сервису генерации");
        return false;
      }
    }
    if (session?.authenticated || !retryAuthenticated) break;
    await new Promise((resolve) => window.setTimeout(resolve, 180));
  }
  state.session = session;
  setBalance(state.session.balance); renderModels(); applyAuthState(); await tryMaxLogin();
  return true;
}

async function initializeApp() {
  elements.prompt.disabled = true;
  elements.send.disabled = true;
  setMode("image");
  restoreTelegramPending();
  const authResult = consumeAuthResult();
  await refreshSession({retryAuthenticated: authResult === "success"});
  state.initialized = true;
  if (authResult === "success") {
    toast(state.session?.authenticated
      ? "Вход через Яндекс выполнен"
      : "Не удалось завершить вход через Яндекс. Попробуйте ещё раз.");
  } else if (authResult === "unavailable") {
    toast("Вход через Яндекс пока не подключён");
  } else if (authResult) {
    toast("Не удалось войти через Яндекс. Попробуйте ещё раз.");
  }
}

elements.prompt.addEventListener("input", resizePrompt);
elements.prompt.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); generate(); } });
elements.send.addEventListener("click", generate);
elements.model.addEventListener("change", updatePrice);
elements.mode.addEventListener("change", () => setMode(elements.mode.value));
elements.count.addEventListener("change", updatePrice);
document.querySelectorAll(".mode-nav-button").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.mode)));
document.querySelectorAll("#prompt-suggestions button").forEach((button) => button.addEventListener("click", () => { elements.prompt.value = button.textContent; resizePrompt(); elements.prompt.focus(); }));
elements.uploadInput.addEventListener("change", () => {
  const file = elements.uploadInput.files[0];
  if (!file) return;
  if (!["image/png", "image/jpeg", "image/webp"].includes(file.type) || file.size > (state.session?.limits.image_bytes || 10485760)) { clearUpload(); toast("PNG, JPEG или WebP — не больше 10 МБ"); return; }
  const reader = new FileReader();
  reader.onload = () => { state.imageData = String(reader.result); state.imageName = file.name; elements.uploadImage.src = state.imageData; elements.uploadName.textContent = file.name; elements.uploadPreview.hidden = false; };
  reader.onerror = () => { clearUpload(); toast("Не удалось прочитать изображение"); };
  reader.readAsDataURL(file);
});
$("#remove-upload").addEventListener("click", clearUpload);
$("#new-chat").addEventListener("click", () => { elements.messages.replaceChildren(); elements.welcome.hidden = false; clearUpload(); elements.prompt.value = ""; resizePrompt(); });
$("#open-payment").addEventListener("click", openPayment);
$("#mobile-payment").addEventListener("click", openPayment);
$("#close-payment").addEventListener("click", () => elements.dialog.close());
elements.dialog.addEventListener("click", (event) => { if (event.target === elements.dialog) elements.dialog.close(); });
elements.authDialog.addEventListener("cancel", (event) => event.preventDefault());
elements.telegramLogin.addEventListener("click", startTelegramLogin);
elements.telegramForm.addEventListener("submit", completeTelegramLogin);
elements.yandexLogin.addEventListener("click", handleYandexLogin);
elements.logout.addEventListener("click", logoutUser);
elements.mobileAccount.addEventListener("click", () => {
  if (!state.session?.authenticated) applyAuthState();
  else if (window.confirm("Выйти из аккаунта Photozhab на этом устройстве?")) logoutUser();
});
window.addEventListener("pageshow", () => {
  restoreTelegramPending();
  if (state.initialized) refreshSession();
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && state.initialized) refreshSession();
});

initializeApp();
