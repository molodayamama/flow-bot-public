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
  chats: [],
  chatId: null,
  gallery: [],
  galleryFilter: "all",
  galleryOpen: false,
  selectedPack: null,
  onboardingStep: 0,
};

const launchHash = new URLSearchParams(window.location.hash.slice(1));
const maxInitData = launchHash.get("WebAppData") || "";
if (maxInitData) history.replaceState(null, "", `${location.pathname}${location.search}`);
const TELEGRAM_PENDING_KEY = "photozhabTelegramPendingAt";
const TELEGRAM_PENDING_MAX_AGE = 11 * 60 * 1000;
const ONBOARDING_KEY_PREFIX = "photozhabOnboardingV1";
let telegramLoginPollTimer = null;
let telegramLoginPollInFlight = false;

const $ = (selector) => document.querySelector(selector);
const elements = {
  conversation: $("#conversation"), messages: $("#message-list"), welcome: $("#welcome-card"),
  prompt: $("#prompt"), send: $("#send-button"), model: $("#model-select"),
  modelOptions: $("#model-options"),
  mode: $("#mode-select"),
  aspect: $("#aspect-select"), count: $("#count-select"), countWrap: $(".count-select"),
  aspectOptions: $("#aspect-options"), countRange: $("#count-range"), countOutput: $("#count-output"),
  uploadButton: $("#upload-button"), uploadInput: $("#image-upload"), uploadPreview: $("#upload-preview"),
  uploadDropzone: $("#upload-dropzone"), uploadDropzoneTrigger: $("#upload-dropzone-trigger"),
  uploadImage: $("#upload-preview-image"), uploadName: $("#upload-file-name"),
  price: $("#request-price"), title: $("#chat-mode-title"), subtitle: $("#chat-mode-subtitle"),
  sidebarBalance: $("#sidebar-balance"), mobileBalance: $("#mobile-balance-value"),
  dialog: $("#payment-dialog"), packs: $("#pack-grid"), toast: $("#toast"),
  authDialog: $("#auth-dialog"), telegramLogin: $("#login-telegram"),
  maxLogin: $("#login-max"), yandexLogin: $("#login-yandex"),
  authNote: $("#auth-note"), accountCard: $("#account-card"),
  accountName: $("#account-name"), accountAvatar: $("#account-avatar"), logout: $("#logout-button"),
  mobileAccount: $("#mobile-account"),
  historyList: $("#chat-history-list"), historyEmpty: $("#chat-history-empty"),
  welcomeTitle: $("#welcome-title"), welcomeCopy: $("#welcome-copy"),
  galleryView: $("#gallery-view"), galleryGrid: $("#gallery-grid"), galleryEmpty: $("#gallery-empty"),
  composerWrap: $(".composer-wrap"), galleryButton: $("#open-gallery"),
  paymentBalance: $("#payment-current-balance"), paymentSubmit: $("#payment-submit"),
  composer: $("#composer"), improveButton: $("#improve-button"),
  improvePanel: $("#improve-panel"), improveLoading: $("#improve-loading"),
  improveVariants: $("#improve-variants"),
  onboarding: $("#onboarding"), onboardingArt: $("#onboarding-art"),
  onboardingIcon: $("#onboarding-icon"), onboardingKicker: $("#onboarding-kicker"),
  onboardingTitle: $("#onboarding-title"), onboardingCopy: $("#onboarding-copy"),
  onboardingDots: $("#onboarding-dots"), onboardingBack: $("#onboarding-back"),
  onboardingNext: $("#onboarding-next"), onboardingSkip: $("#skip-onboarding"),
};

const modeCopy = {
  image: ["Генерация картинки", "Nano Banana создаст изображение по описанию", "Опиши изображение, которое хочешь создать…", "Что создадим?", "Опиши идею обычными словами — модель, формат и стоимость выбираются в панели ниже."],
  edit: ["Редактирование фото", "Загрузи фото и опиши нужные изменения", "Что нужно изменить на фотографии?", "Что изменим?", "Добавь фотографию и опиши нужный результат — модель сохранит важные детали кадра."],
  video: ["Генерация видео", "Veo или Omni создаст ролик по описанию", "Опиши сцену, движение камеры и атмосферу…", "Какое видео снимем?", "Опиши сцену, движение камеры и атмосферу — стоимость видна до запуска."],
  animate: ["Оживление фото", "Загрузи фото и опиши желаемое движение", "Как должно ожить это фото?", "Как оживим фото?", "Добавь фотографию и расскажи, какое движение должно появиться в кадре."],
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
  if (elements.paymentBalance) elements.paymentBalance.textContent = `${balance} кр`;
  if (state.session) state.session.balance = balance;
}

function option(value, label) {
  const node = document.createElement("option");
  node.value = value;
  node.textContent = label;
  return node;
}

function telegramLoginPending() {
  const pendingAt = Number(sessionStorage.getItem(TELEGRAM_PENDING_KEY));
  return Number.isFinite(pendingAt) && pendingAt > 0 && Date.now() - pendingAt <= TELEGRAM_PENDING_MAX_AGE;
}

function clearTelegramLoginPending() {
  sessionStorage.removeItem(TELEGRAM_PENDING_KEY);
}

function stopTelegramLoginPolling() {
  if (telegramLoginPollTimer !== null) {
    window.clearInterval(telegramLoginPollTimer);
    telegramLoginPollTimer = null;
  }
  telegramLoginPollInFlight = false;
}

function updateTelegramAuthNote(maxEnabled, yandexEnabled) {
  if (telegramLoginPending()) {
    elements.authNote.textContent = "Откройте Telegram, подтвердите вход и вернитесь сюда — сайт подхватит авторизацию автоматически.";
    return;
  }
  const maxNote = maxEnabled
    ? "MAX-вход работает внутри официального мини-приложения Photozhab."
    : "Вход через MAX появится после подключения Mini App.";
  elements.authNote.textContent = yandexEnabled
    ? maxNote
    : `${maxNote} Яндекс ID включим после регистрации OAuth-приложения.`;
}

async function pollTelegramLogin() {
  if (!telegramLoginPending() || state.session?.authenticated || telegramLoginPollInFlight) return false;
  const pendingAt = Number(sessionStorage.getItem(TELEGRAM_PENDING_KEY));
  if (!Number.isFinite(pendingAt) || pendingAt <= 0 || Date.now() - pendingAt > TELEGRAM_PENDING_MAX_AGE) {
    clearTelegramLoginPending();
    stopTelegramLoginPolling();
    updateTelegramAuthNote(Boolean(state.session?.auth?.providers?.max), Boolean(state.session?.auth?.providers?.yandex));
    return false;
  }
  telegramLoginPollInFlight = true;
  try {
    await api("/web/api/auth/telegram/complete", {method: "POST", body: "{}"});
    clearTelegramLoginPending();
    stopTelegramLoginPolling();
    await refreshSession({retryAuthenticated: true});
    toast("Вход через Telegram выполнен");
    return true;
  } catch (error) {
    if (error.status === 409) return false;
    clearTelegramLoginPending();
    stopTelegramLoginPolling();
    updateTelegramAuthNote(Boolean(state.session?.auth?.providers?.max), Boolean(state.session?.auth?.providers?.yandex));
    toast(error.status === 401
      ? "Ссылка для входа недействительна или уже использована. Запросите новую."
      : "Не удалось подтвердить вход через Telegram");
    return false;
  } finally {
    telegramLoginPollInFlight = false;
  }
}

function startTelegramLoginPolling() {
  if (telegramLoginPollTimer !== null) return;
  telegramLoginPollTimer = window.setInterval(() => {
    void pollTelegramLogin();
  }, 1500);
  void pollTelegramLogin();
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

function syncModelButtons() {
  elements.modelOptions.querySelectorAll(".model-option").forEach((button) => {
    const active = button.dataset.value === elements.model.value;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function syncAspectButtons() {
  elements.aspectOptions.querySelectorAll(".aspect-option").forEach((button) => {
    const active = button.dataset.value === elements.aspect.value;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function renderModels() {
  if (!state.session) return;
  const models = state.mode === "image" || state.mode === "edit"
    ? state.session.image_models : state.session.video_models;
  const previous = elements.model.value;
  elements.model.replaceChildren(...models.map((model) => option(model.id, `${model.label} · ${state.mode === "animate" ? (model.animate_price ?? model.price) : model.price} кр`)));
  if (models.some((model) => model.id === previous)) elements.model.value = previous;
  elements.modelOptions.replaceChildren(...models.map((model) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "choice-pill model-option";
    button.dataset.value = model.id;
    button.setAttribute("aria-pressed", "false");
    const label = document.createElement("strong");
    label.textContent = model.label;
    const price = document.createElement("small");
    price.textContent = `${state.mode === "animate" ? (model.animate_price ?? model.price) : model.price} кр`;
    button.append(label, price);
    button.addEventListener("click", () => {
      elements.model.value = model.id;
      syncModelButtons();
      updatePrice();
    });
    return button;
  }));
  syncModelButtons();
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
  closeGallery();
  state.mode = mode;
  elements.composer.dataset.mode = mode;
  closeImprove();
  elements.mode.value = mode;
  document.querySelectorAll(".mode-nav-button").forEach((button) => button.classList.toggle("is-active", button.dataset.mode === mode));
  const [title, subtitle, placeholder, welcomeTitle, welcomeCopy] = modeCopy[mode];
  elements.title.textContent = title;
  elements.subtitle.textContent = subtitle;
  elements.prompt.placeholder = placeholder;
  elements.welcomeTitle.textContent = welcomeTitle;
  elements.welcomeCopy.textContent = welcomeCopy;
  const needsImage = mode === "edit" || mode === "animate";
  elements.uploadButton.hidden = mode === "video";
  elements.uploadDropzone.hidden = !needsImage;
  if (needsImage) elements.uploadDropzone.querySelector("strong").textContent = mode === "animate" ? "Добавьте фото для оживления" : "Добавьте фотографию для редактирования";
  elements.countWrap.hidden = mode !== "image";
  [...elements.aspect.options].forEach((item) => { item.disabled = (mode === "video" || mode === "animate") && !["portrait", "landscape"].includes(item.value); });
  if ((mode === "video" || mode === "animate") && !["portrait", "landscape"].includes(elements.aspect.value)) elements.aspect.value = "portrait";
  elements.aspectOptions.querySelectorAll(".aspect-option").forEach((button) => {
    button.disabled = (mode === "video" || mode === "animate") && !["portrait", "landscape"].includes(button.dataset.value);
  });
  syncAspectButtons();
  if (!needsImage) clearUpload();
  renderModels();
}

function toast(message) {
  elements.toast.textContent = message;
  elements.toast.hidden = false;
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => { elements.toast.hidden = true; }, 3600);
}

function onboardingStorageKey() {
  const provider = state.session?.identity?.provider || "account";
  return `${ONBOARDING_KEY_PREFIX}:${provider}`;
}

function onboardingCompleted() {
  try { return localStorage.getItem(onboardingStorageKey()) === "done"; }
  catch (_) { return false; }
}

const onboardingSteps = [
  {
    kicker: "Добро пожаловать",
    title: "Photozhab — картинки и видео по тексту",
    copy: "Опишите идею обычными словами — нейросеть создаст изображение или видео. Без промпт-инженерии и VPN.",
    icon: "✦",
    art: "radial-gradient(circle at 50% 55%, rgba(211,243,107,.18), transparent 60%), linear-gradient(160deg, #17200f, #0e100f)",
  },
  {
    kicker: "Как это работает",
    title: "Кредиты вместо подписки",
    copy: "Цена видна до запуска: картинка — от 10 кредитов, видео — от 50. Платите только за то, что создаёте.",
    icon: "◈",
    art: "radial-gradient(circle at 50% 55%, rgba(127,216,215,.16), transparent 60%), linear-gradient(160deg, #0f2321, #0e100f)",
  },
  {
    kicker: "Подарок на старт",
    title: "30 кредитов на старте",
    copy: "Новому пользователю хватает стартового бонуса на 3 картинки. Пополнить баланс можно через СБП или карту.",
    icon: "30",
    art: "radial-gradient(circle at 50% 55%, rgba(211,243,107,.22), transparent 60%), linear-gradient(160deg, #1c2410, #0e100f)",
  },
];

function renderOnboarding() {
  const step = Math.max(0, Math.min(onboardingSteps.length - 1, state.onboardingStep));
  const item = onboardingSteps[step];
  elements.onboardingKicker.textContent = item.kicker;
  elements.onboardingTitle.textContent = item.title;
  elements.onboardingCopy.textContent = item.copy;
  elements.onboardingIcon.textContent = item.icon;
  elements.onboardingArt.style.background = item.art;
  elements.onboardingBack.hidden = step === 0;
  elements.onboardingNext.firstChild.textContent = step === onboardingSteps.length - 1 ? "Начать творить " : "Дальше ";
  elements.onboardingDots.setAttribute("aria-label", `Шаг ${step + 1} из ${onboardingSteps.length}`);
  elements.onboardingDots.replaceChildren(...onboardingSteps.map((_, index) => {
    const dot = document.createElement("i");
    dot.classList.toggle("is-active", index === step);
    return dot;
  }));
}

function maybeShowOnboarding() {
  if (!state.session?.authenticated || onboardingCompleted()) {
    elements.onboarding.hidden = true;
    return;
  }
  state.onboardingStep = 0;
  renderOnboarding();
  elements.onboarding.hidden = false;
  window.setTimeout(() => elements.onboardingSkip.focus(), 0);
}

function finishOnboarding() {
  try { localStorage.setItem(onboardingStorageKey(), "done"); } catch (_) { /* private mode */ }
  elements.onboarding.hidden = true;
  elements.prompt.focus();
}

function closeImprove() {
  if (!elements.improvePanel) return;
  elements.improvePanel.hidden = true;
  elements.improveLoading.hidden = true;
  elements.improveVariants.replaceChildren();
  elements.improveButton.setAttribute("aria-expanded", "false");
  updateImproveButton();
}

function updateImproveButton() {
  if (!elements.improveButton) return;
  const panelOpen = !elements.improvePanel.hidden;
  elements.improveButton.hidden = elements.prompt.value.trim().length < 3 || panelOpen;
}

/* Legacy local prompt variants intentionally disabled: prompt improvement is backend-only. */
function legacyPromptVariantsDisabled() {
  return [];
/*
  const baseRaw = elements.prompt.value.trim();
  const base = baseRaw.charAt(0).toLocaleUpperCase("ru-RU") + baseRaw.slice(1);
  const video = state.mode === "video" || state.mode === "animate";
  const sets = video ? [
    [
      ["Кинематограф", ", плавное движение камеры dolly-in, кинематографический свет, глубина резкости, 24 кадра в секунду, выразительная цветокоррекция"],
      ["Атмосфера", ", мягкий рассеянный свет золотого часа, лёгкий туман, частицы в воздухе, медленное панорамирование, спокойное настроение"],
      ["Динамика", ", энергичное движение камеры, контровой неоновый свет, отражения и блики, ясный главный объект, ощущение скорости"],
    ],
    [
      ["Реклама", ", чистый рекламный кадр, плавный облёт объекта, контролируемые блики, премиальный свет, точный фокус на продукте"],
      ["Документальный", ", естественное движение камеры с рук, реалистичный дневной свет, правдоподобная физика, ненавязчивое наблюдение"],
      ["Сказочный", ", медленный пролёт камеры, объёмный свет, воздушная дымка, мягкие частицы, выразительная глубина пространства"],
    ],
  ] : [
    [
      ["Детальный", ", сверхдетализация, студийный свет, объектив 85 мм, малая глубина резкости, фотореализм, богатая фактура"],
      ["Художественный", ", кинематографическая композиция, драматичный контровой свет, насыщенная палитра, атмосферная дымка"],
      ["Минимализм", ", чистая композиция, мягкий естественный свет, приглушённые тона, много воздуха, аккуратная геометрия кадра"],
    ],
    [
      ["Предметный", ", премиальная предметная съёмка, бесшовный фон, контролируемые отражения, резкий объект, мягкие студийные тени"],
      ["Редакционный", ", журнальная композиция, выразительный ракурс, естественная текстура, сложный мягкий свет, современная цветокоррекция"],
      ["Тёплый", ", уютный рассеянный свет, натуральные материалы, спокойная палитра, тактильные фактуры, сбалансированная композиция"],
    ],
  ];
  return sets[state.legacyImproveRound % sets.length].map(([tag, suffix]) => ({tag, text: base + suffix}));
}
*/
}

function renderImproveVariants(items) {
  elements.improveVariants.replaceChildren(...items.map((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "improve-variant";
    const tag = document.createElement("b"); tag.textContent = item.tag;
    const copy = document.createElement("span"); copy.textContent = item.text;
    button.append(tag, copy);
    button.addEventListener("click", () => {
      elements.prompt.value = item.text;
      closeImprove();
      resizePrompt();
      elements.prompt.focus();
    });
    return button;
  }));
}

async function openImprove() {
  const prompt = elements.prompt.value.trim();
  if (prompt.length < 3 || state.busy) return;
  if (!state.session?.authenticated) { applyAuthState(); return; }
  elements.improvePanel.hidden = false;
  elements.improveButton.setAttribute("aria-expanded", "true");
  updateImproveButton();
  elements.improveVariants.replaceChildren();
  elements.improveLoading.hidden = false;
  try {
    const result = await api("/web/api/prompt-improve", {method: "POST", body: JSON.stringify({
      prompt, mode: state.mode,
    })});
    elements.improveLoading.hidden = true;
    renderImproveVariants(Array.isArray(result.variants) ? result.variants : []);
    if (Number.isFinite(Number(result.balance))) setBalance(result.balance);
  } catch (error) {
    elements.improveLoading.hidden = true;
    if (error.payload && Number.isFinite(Number(error.payload.balance))) setBalance(error.payload.balance);
    if (error.message === "insufficient_credits") openPayment();
    else if (error.message === "auth_required") applyAuthState();
    else toast(error.message === "prompt_improve_failed"
      ? "Не удалось улучшить промпт. Кредиты возвращены — попробуйте ещё раз."
      : "Не удалось получить варианты промпта.");
    if (elements.improveVariants.childElementCount === 0) closeImprove();
  }
}

function providerLabel(provider) {
  return {telegram: "Telegram", max: "MAX", yandex: "Яндекс"}[provider] || "аккаунт";
}

function renderHistory() {
  elements.historyList.replaceChildren();
  if (!state.chats.length) {
    const empty = document.createElement("p");
    empty.className = "chat-history-empty";
    empty.textContent = "Успешные запросы появятся здесь";
    elements.historyList.append(empty);
    return;
  }
  state.chats.forEach((chat) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chat-history-item";
    button.classList.toggle("is-active", chat.chat_id === state.chatId);
    button.title = chat.title || "Новый чат";
    const mark = document.createElement("span");
    mark.textContent = "✦";
    mark.setAttribute("aria-hidden", "true");
    const label = document.createElement("span");
    label.textContent = chat.title || "Новый чат";
    button.append(mark, label);
    button.addEventListener("click", () => openChat(chat.chat_id));
    elements.historyList.append(button);
  });
}

function setChats(items) {
  state.chats = (Array.isArray(items) ? items : [])
    .filter((chat) => chat && typeof chat.chat_id === "string")
    .map((chat) => ({
      chat_id: chat.chat_id,
      title: String(chat.title || "Новый чат").trim().slice(0, 120) || "Новый чат",
      messages: Number(chat.messages || 0),
      updated_at: chat.updated_at || "",
    }))
    .slice(0, 30);
  renderHistory();
}

function setHistory(items) {
  state.history = (Array.isArray(items) ? items : [])
    .map((item) => String(item || "").trim())
    .filter((item) => item.length >= 3)
    .slice(0, 20);
}

function rememberRequest(prompt, chatSummary = null) {
  if (chatSummary && chatSummary.chat_id) {
    const existing = state.chats.filter((chat) => chat.chat_id !== chatSummary.chat_id);
    setChats([{...chatSummary, title: chatSummary.title || prompt}, ...existing]);
    state.chatId = chatSummary.chat_id;
    renderHistory();
  }
  state.history = [prompt, ...state.history.filter((item) => item !== prompt)].slice(0, 20);
}

function resetChatView() {
  state.chatId = null;
  elements.messages.replaceChildren();
  elements.welcome.hidden = false;
  elements.prompt.value = "";
  resizePrompt();
  clearUpload();
  renderHistory();
}

async function openChat(chatId) {
  if (!state.session?.authenticated || state.busy || !chatId) return;
  try {
    const data = await api(`/web/api/chats/${encodeURIComponent(chatId)}`);
    const messages = Array.isArray(data.messages) ? data.messages : [];
    state.chatId = chatId;
    state.gallery = [];
    elements.messages.replaceChildren();
    elements.welcome.hidden = messages.length > 0;
    for (const item of messages) {
      if (item.role === "user") {
        message("user", item.text || "");
        continue;
      }
      const target = message("assistant", item.text || "");
      if (Array.isArray(item.media) && item.media.length) {
        renderResult(target, {media: item.media, charged: item.charged || 0, balance: item.balance ?? state.session.balance}, {
          prompt: "", mode: item.mode || "image", modelLabel: item.model || "Photozhab", aspectLabel: item.aspect || "",
        });
      }
    }
    renderHistory();
    scrollBottom();
  } catch (error) {
    if (error.message === "chat_not_found") {
      setChats(state.chats.filter((chat) => chat.chat_id !== chatId));
      if (state.chatId === chatId) resetChatView();
    } else toast("Не удалось открыть историю чата");
  }
}

/* Keep the short prompt ledger for admin/audit, while the sidebar renders real chats. */
function rememberPrompt(prompt) {
  const seen = new Set();
  state.history = [prompt, ...state.history.filter((item) => item !== prompt)]
    .filter((item) => item.length >= 3 && !seen.has(item) && seen.add(item)).slice(0, 20);
}

function closeGallery() {
  state.galleryOpen = false;
  if (elements.galleryView) elements.galleryView.hidden = true;
  if (elements.composerWrap) elements.composerWrap.hidden = false;
  elements.messages.hidden = false;
  elements.welcome.hidden = elements.messages.childElementCount > 0;
  if (elements.galleryButton) elements.galleryButton.classList.remove("is-active");
}

function showGallery() {
  state.galleryOpen = true;
  elements.welcome.hidden = true;
  elements.messages.hidden = true;
  elements.galleryView.hidden = false;
  elements.composerWrap.hidden = true;
  document.querySelectorAll(".mode-nav-button").forEach((button) => button.classList.remove("is-active"));
  elements.galleryButton.classList.add("is-active");
  renderGallery();
}

function renderGallery() {
  const items = state.gallery.filter((item) => state.galleryFilter === "all" || item.type === state.galleryFilter);
  elements.galleryGrid.replaceChildren();
  elements.galleryEmpty.hidden = items.length > 0;
  items.forEach((item) => {
    const article = document.createElement("article");
    article.className = "gallery-card";
    const frame = document.createElement("div");
    frame.className = "gallery-card__frame";
    let media;
    if (item.type === "video") {
      media = document.createElement("video");
      media.src = item.url; media.muted = true; media.playsInline = true; media.preload = "metadata";
      media.setAttribute("aria-label", "Видео, созданное Photozhab");
      const badge = document.createElement("span"); badge.className = "gallery-card__badge"; badge.textContent = "▶ видео"; frame.append(badge);
    } else {
      media = document.createElement("img"); media.src = item.url; media.alt = "Работа, созданная Photozhab"; media.loading = "lazy";
    }
    frame.append(media);
    const footer = document.createElement("div");
    footer.className = "gallery-card__footer";
    const meta = document.createElement("span"); meta.textContent = item.meta;
    const actions = document.createElement("span");
    const open = document.createElement("a"); open.href = item.url; open.target = "_blank"; open.rel = "noopener"; open.title = "Открыть"; open.textContent = "↗";
    const repeat = document.createElement("button"); repeat.type = "button"; repeat.title = "Повторить запрос"; repeat.textContent = "↻";
    repeat.addEventListener("click", () => {
      setMode(item.mode);
      elements.prompt.value = item.prompt;
      resizePrompt();
      elements.prompt.focus();
    });
    actions.append(open, repeat); footer.append(meta, actions); article.append(frame, footer); elements.galleryGrid.append(article);
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
  updateTelegramAuthNote(maxEnabled, yandexEnabled);
}

function applyAuthState() {
  const authenticated = Boolean(state.session?.authenticated);
  elements.accountCard.hidden = !authenticated;
  elements.accountName.textContent = authenticated
    ? `${state.session.identity?.display_name || "Пользователь"} · ${providerLabel(state.session.identity?.provider)}`
    : "—";
  elements.accountAvatar.textContent = authenticated
    ? (state.session.identity?.display_name || "P").trim().charAt(0).toLocaleUpperCase("ru-RU") || "P"
    : "P";
  elements.mobileAccount.textContent = authenticated ? "Аккаунт" : "Войти";
  elements.prompt.disabled = !authenticated;
  elements.send.disabled = !authenticated || state.busy;
  setProviderAvailability();
  if (authenticated) {
    clearTelegramLoginPending();
    stopTelegramLoginPolling();
    if (elements.authDialog.open) elements.authDialog.close();
    if (state.initialized) maybeShowOnboarding();
  } else if (!elements.authDialog.open) {
    elements.onboarding.hidden = true;
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
    sessionStorage.setItem(TELEGRAM_PENDING_KEY, String(Date.now()));
    updateTelegramAuthNote(Boolean(state.session?.auth?.providers?.max), Boolean(state.session?.auth?.providers?.yandex));
    startTelegramLoginPolling();
    if (popup) popup.location.href = result.url;
    else {
      window.location.assign(result.url);
      return;
    }
  } catch (_) {
    if (popup) popup.close();
    clearTelegramLoginPending();
    stopTelegramLoginPolling();
    updateTelegramAuthNote(Boolean(state.session?.auth?.providers?.max), Boolean(state.session?.auth?.providers?.yandex));
    toast("Не удалось открыть вход через Telegram. Попробуйте ещё раз.");
  } finally {
    elements.telegramLogin.disabled = false;
  }
}

function restoreTelegramPending() {
  const pendingAt = Number(sessionStorage.getItem(TELEGRAM_PENDING_KEY));
  if (!Number.isFinite(pendingAt) || pendingAt <= 0 || Date.now() - pendingAt > TELEGRAM_PENDING_MAX_AGE) {
    clearTelegramLoginPending();
    stopTelegramLoginPolling();
    return false;
  }
  startTelegramLoginPolling();
  return true;
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
    setChats(state.session.chats);
    setHistory(state.session.history);
    setBalance(state.session.balance); renderModels(); applyAuthState();
    toast("Вход через MAX выполнен");
  } catch (_) {
    toast("MAX не смог подтвердить вход. Откройте мини-приложение заново.");
  }
}

async function logoutUser() {
  try {
    await api("/web/api/auth/logout", {method: "POST", body: "{}"});
    clearTelegramLoginPending();
    stopTelegramLoginPolling();
    state.session = await api("/web/api/session", {headers: {}});
    setChats([]); setHistory([]); state.chatId = null; setBalance(0); applyAuthState();
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
  const intro = document.createElement("p");
  const isVideo = state.mode === "video" || state.mode === "animate";
  intro.textContent = isVideo ? "Создаю видео — это обычно занимает несколько минут." : "Создаю результат — картинка обычно готова примерно за 1 минуту.";
  item.content.append(intro);
  const grid = document.createElement("div");
  grid.className = "generation-progress-grid";
  const count = state.mode === "image" ? Number(elements.count.value || 1) : 1;
  for (let index = 0; index < count; index += 1) {
    const card = document.createElement("div");
    card.className = "generation-progress-card";
    card.style.setProperty("--delay", `${index * 0.24}s`);
    const shimmer = document.createElement("span"); shimmer.className = "generation-progress-card__shimmer";
    const copy = document.createElement("span"); copy.className = "generation-progress-card__copy";
    const mark = document.createElement("b"); mark.textContent = "✦";
    const label = document.createElement("strong"); label.textContent = index === 0 ? "Создаём…" : `Вариант ${index + 1} в очереди`;
    const hint = document.createElement("small"); hint.textContent = isVideo ? "видео обрабатывается" : "примерно 1 минута";
    copy.append(mark, label, hint);
    const progress = document.createElement("span"); progress.className = "generation-progress-card__bar";
    card.append(shimmer, copy, progress); grid.append(card);
  }
  item.content.append(grid);
  const meta = document.createElement("div");
  meta.className = "message-meta";
  meta.textContent = `${requestPrice()} кр зарезервированы — вернутся при технической ошибке`;
  item.content.append(meta);
  return item;
}

function scrollBottom() { requestAnimationFrame(() => { elements.conversation.scrollTop = elements.conversation.scrollHeight; }); }

function renderResult(target, payload, request) {
  target.content.replaceChildren();
  const intro = document.createElement("p");
  intro.textContent = "Готово. Результат можно открыть или сохранить:";
  target.content.append(intro);
  const grid = document.createElement("div");
  grid.className = "media-grid";
  payload.media.forEach((media) => {
    const figure = document.createElement("figure");
    figure.className = "media-result";
    figure.dataset.result = "generated";
    const frame = document.createElement("div");
    frame.className = "media-result-frame";
    let node;
    if (media.type === "video") {
      node = document.createElement("video"); node.controls = true; node.playsInline = true; node.preload = "metadata";
    } else {
      node = document.createElement("img"); node.alt = "Изображение, созданное Photozhab"; node.loading = "lazy";
    }
    node.src = media.url;
    frame.append(node);
    const actions = document.createElement("div"); actions.className = "media-result-actions";
    const link = document.createElement("a");
    link.className = "media-result-action media-result-action--download";
    link.href = media.download_url || media.url;
    link.download = media.type === "video" ? "photozhab-video.mp4" : "photozhab-image";
    link.textContent = "↓ Скачать";
    link.setAttribute("aria-label", media.type === "video" ? "Скачать видео" : "Скачать изображение");
    actions.append(link);
    if (media.type !== "video") {
      const edit = document.createElement("button"); edit.type = "button"; edit.className = "media-result-action media-result-action--edit";
      const editIcon = document.createElement("span"); editIcon.setAttribute("aria-hidden", "true"); editIcon.textContent = "✎";
      const editLabel = document.createElement("span"); editLabel.textContent = "Редактировать";
      edit.append(editIcon, editLabel);
      edit.addEventListener("click", () => useResultAsSource(media, "edit", node, edit));
      const animate = document.createElement("button"); animate.type = "button"; animate.className = "media-result-action"; animate.textContent = "◉ Оживить";
      animate.addEventListener("click", () => useResultAsSource(media, "animate", node, animate));
      actions.append(edit, animate);
    }
    figure.append(frame, actions); grid.append(figure);
    state.gallery.unshift({
      type: media.type === "video" ? "video" : "image",
      url: media.url,
      prompt: request.prompt,
      mode: request.mode,
      meta: `${request.modelLabel} · ${request.aspectLabel}`,
    });
  });
  target.content.append(grid);
  const meta = document.createElement("div");
  meta.className = "message-meta"; meta.textContent = `Списано ${payload.charged} кр · баланс ${payload.balance} кр`;
  target.content.append(meta);
  setBalance(payload.balance);
  if (state.galleryOpen) renderGallery();
  scrollBottom();
}

function reducedMotion() {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}

async function animateReferenceTransfer(sourceNode) {
  if (!sourceNode || reducedMotion()) return;
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  const from = sourceNode.getBoundingClientRect();
  const to = elements.uploadImage.getBoundingClientRect();
  if (!from.width || !from.height || !to.width || !to.height) return;
  const clone = sourceNode.cloneNode(true);
  clone.className = "media-flight-clone";
  clone.removeAttribute("loading");
  Object.assign(clone.style, {
    left: `${from.left}px`, top: `${from.top}px`, width: `${from.width}px`, height: `${from.height}px`,
  });
  document.body.append(clone);
  const shiftX = to.left - from.left;
  const shiftY = to.top - from.top;
  const scaleX = to.width / from.width;
  const scaleY = to.height / from.height;
  try {
    await clone.animate([
      {transform: "translate3d(0, 0, 0) scale(1)", opacity: 1, borderRadius: "16px"},
      {transform: `translate3d(${shiftX}px, ${shiftY}px, 0) scale(${scaleX}, ${scaleY})`, opacity: 0.34, borderRadius: "9px"},
    ], {duration: 560, easing: "cubic-bezier(0.4, 0, 0.2, 1)", fill: "forwards"}).finished;
  } catch (_) {
    // Attachment is already complete; interrupted decorative motion must not fail it.
  } finally {
    clone.remove();
  }
  elements.uploadPreview.classList.remove("is-reference-arrival");
  void elements.uploadPreview.offsetWidth;
  elements.uploadPreview.classList.add("is-reference-arrival");
}

async function useResultAsSource(media, mode, sourceNode, trigger) {
  const idleLabel = trigger?.textContent || "";
  if (trigger) {
    trigger.disabled = true;
    trigger.setAttribute("aria-busy", "true");
    trigger.textContent = "Прикрепляю…";
  }
  try {
    const sourceUrl = media.download_url || media.url;
    const response = await fetch(sourceUrl, {credentials: "same-origin", cache: "no-store"});
    if (!response.ok) throw new Error("media_unavailable");
    const blob = await response.blob();
    const maximum = Number(state.session?.limits.image_bytes || 10485760);
    if (!blob.type.startsWith("image/") || !blob.size || blob.size > maximum) throw new Error("invalid_image");
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
    setMode(mode);
    state.imageData = dataUrl;
    state.imageName = mode === "edit" ? "Редактировать это фото" : "Оживить это фото";
    elements.uploadImage.src = dataUrl;
    elements.uploadName.textContent = state.imageName;
    elements.uploadPreview.hidden = false;
    await animateReferenceTransfer(sourceNode);
    elements.composerWrap.scrollIntoView({behavior: reducedMotion() ? "auto" : "smooth", block: "end"});
    elements.prompt.focus();
    toast(mode === "edit" ? "Фото прикреплено для редактирования" : "Фото прикреплено для оживления");
  } catch (_) {
    toast("Не удалось прикрепить результат. Скачайте его и загрузите вручную.");
  } finally {
    if (trigger) {
      trigger.disabled = false;
      trigger.removeAttribute("aria-busy");
      trigger.textContent = idleLabel;
    }
  }
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
  const model = selectedModel();
  const userMessage = message("user", prompt, state.imageData);
  const userMeta = document.createElement("div");
  userMeta.className = "message-meta";
  userMeta.textContent = `${model.label} · ${elements.aspect.options[elements.aspect.selectedIndex]?.textContent || elements.aspect.value} · ${requestPrice()} кр`;
  userMessage.content.append(userMeta);
  const pending = loadingMessage();
  elements.prompt.value = ""; resizePrompt();
  try {
    const payload = await api("/web/api/generate", {method: "POST", body: JSON.stringify({
      mode: state.mode, prompt, chat_id: state.chatId || undefined, aspect: elements.aspect.value, count: Number(elements.count.value),
      image_model: state.mode === "image" || state.mode === "edit" ? model.id : undefined,
      video_model: state.mode === "video" || state.mode === "animate" ? model.id : undefined,
      image_b64: state.imageData,
    })});
    state.chatId = payload.chat_id || state.chatId;
    rememberRequest(prompt, payload.chat);
    renderResult(pending, payload, {
      prompt,
      mode: state.mode,
      modelLabel: model.label,
      aspectLabel: elements.aspect.options[elements.aspect.selectedIndex]?.textContent || elements.aspect.value,
    });
    if (state.mode === "edit" || state.mode === "animate") clearUpload();
  } catch (error) {
    pending.content.replaceChildren();
    const errorBox = document.createElement("div");
    errorBox.className = "generation-error";
    const title = document.createElement("strong"); title.textContent = "Не получилось создать";
    const paragraph = document.createElement("p");
    paragraph.textContent = errorMessages[error.message] || "Сервис временно недоступен. Кредиты не списаны.";
    const actions = document.createElement("div");
    actions.className = "generation-error-actions";
    const retry = document.createElement("button");
    retry.type = "button";
    if (error.message === "insufficient_credits") {
      retry.textContent = "Пополнить баланс";
      retry.addEventListener("click", () => { elements.prompt.value = prompt; resizePrompt(); openPayment(); });
      const editPrompt = document.createElement("button");
      editPrompt.type = "button";
      editPrompt.className = "generation-error-secondary";
      editPrompt.textContent = "Изменить запрос";
      editPrompt.addEventListener("click", () => { elements.prompt.value = prompt; resizePrompt(); elements.prompt.focus(); });
      actions.append(retry, editPrompt);
    } else {
      retry.textContent = "Изменить запрос";
      retry.addEventListener("click", () => { elements.prompt.value = prompt; resizePrompt(); elements.prompt.focus(); });
      actions.append(retry);
    }
    errorBox.append(title, paragraph, actions); pending.content.append(errorBox);
    if (error.payload && Number.isFinite(Number(error.payload.balance))) setBalance(error.payload.balance);
    if (error.message === "insufficient_credits") openPayment();
    if (error.message === "auth_required") applyAuthState();
  } finally {
    state.busy = false; elements.send.disabled = false; elements.prompt.focus(); scrollBottom();
  }
}

function resizePrompt() {
  elements.prompt.style.height = "auto";
  elements.prompt.style.height = `${Math.min(elements.prompt.scrollHeight, 180)}px`;
  updateImproveButton();
}

function renderPacks() {
  const packs = state.session?.packs || [];
  elements.packs.replaceChildren();
  if (!packs.length) { const p = document.createElement("p"); p.textContent = "Оплата временно недоступна."; elements.packs.append(p); return; }
  state.selectedPack = packs.find((pack) => pack.best) || packs[0];
  packs.forEach((pack) => {
    const button = document.createElement("button"); button.type = "button"; button.className = `pack-button${pack.best ? " is-best" : ""}`;
    button.dataset.packId = pack.id;
    const copy = document.createElement("span"); const strong = document.createElement("strong"); strong.textContent = `${pack.credits} кр`;
    const hint = document.createElement("span");
    hint.textContent = Number(pack.credits) === 45
      ? "только картинки: примерно 4 изображения"
      : `примерно ${Math.floor(pack.credits / 10)} картинок или ${Math.max(1, Math.floor(pack.credits / 50))} коротких видео`;
    copy.append(strong, hint);
    const price = document.createElement("em"); price.textContent = `${pack.rub} ₽`; button.append(copy, price);
    button.addEventListener("click", () => selectPack(pack)); elements.packs.append(button);
  });
  selectPack(state.selectedPack);
}

function selectPack(pack) {
  state.selectedPack = pack;
  elements.packs.querySelectorAll(".pack-button").forEach((button) => {
    const selected = button.dataset.packId === pack.id;
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  elements.paymentSubmit.disabled = false;
  elements.paymentSubmit.textContent = `Оплатить ${pack.rub} ₽ → +${pack.credits} кр`;
}

function openPayment() {
  if (!state.session?.authenticated) { applyAuthState(); return; }
  setBalance(state.session.balance);
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
  setChats(state.session?.chats);
  setHistory(state.session?.history);
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
  maybeShowOnboarding();
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
elements.model.addEventListener("change", () => { syncModelButtons(); updatePrice(); });
elements.mode.addEventListener("change", () => setMode(elements.mode.value));
elements.aspect.addEventListener("change", syncAspectButtons);
elements.aspectOptions.querySelectorAll(".aspect-option").forEach((button) => button.addEventListener("click", () => {
  if (button.disabled) return;
  elements.aspect.value = button.dataset.value;
  syncAspectButtons();
}));
const syncCount = (value) => {
  const count = Math.max(1, Math.min(4, Number(value) || 1));
  elements.count.value = String(count);
  elements.countRange.value = String(count);
  elements.countOutput.value = String(count);
  elements.countOutput.textContent = String(count);
  elements.countRange.style.setProperty("--count-progress", `${((count - 1) / 3) * 100}%`);
  elements.countRange.setAttribute("aria-valuetext", `${count} ${count === 1 ? "изображение" : "изображения"}`);
  updatePrice();
};
elements.count.addEventListener("change", () => syncCount(elements.count.value));
elements.countRange.addEventListener("input", () => syncCount(elements.countRange.value));
syncCount(elements.countRange.value);
document.querySelectorAll(".mode-nav-button").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.mode)));
document.querySelectorAll("#prompt-suggestions button").forEach((button) => button.addEventListener("click", () => { elements.prompt.value = button.textContent; resizePrompt(); elements.prompt.focus(); }));
function attachImageFile(file, fallbackName = "Вставленное изображение") {
  if (!file) return;
  if (!["image/png", "image/jpeg", "image/webp"].includes(file.type) || file.size > (state.session?.limits.image_bytes || 10485760)) { clearUpload(); toast("PNG, JPEG или WebP — не больше 10 МБ"); return; }
  if (state.mode === "image") setMode("edit");
  const reader = new FileReader();
  reader.onload = () => { state.imageData = String(reader.result); state.imageName = file.name || fallbackName; elements.uploadImage.src = state.imageData; elements.uploadName.textContent = state.imageName; elements.uploadPreview.hidden = false; };
  reader.onerror = () => { clearUpload(); toast("Не удалось прочитать изображение"); };
  reader.readAsDataURL(file);
}
elements.uploadInput.addEventListener("change", () => attachImageFile(elements.uploadInput.files[0]));
elements.prompt.addEventListener("paste", (event) => {
  const files = Array.from(event.clipboardData?.files || []);
  const image = files.find((file) => String(file.type || "").startsWith("image/"));
  if (!image) return;
  event.preventDefault();
  attachImageFile(image, "Вставленное изображение");
  toast("Изображение добавлено из буфера обмена");
});
elements.uploadDropzoneTrigger.addEventListener("click", () => elements.uploadInput.click());
$("#remove-upload").addEventListener("click", clearUpload);
$("#new-chat").addEventListener("click", () => { closeGallery(); resetChatView(); elements.prompt.focus(); });
elements.galleryButton.addEventListener("click", showGallery);
$("#gallery-create").addEventListener("click", () => { setMode("image"); elements.prompt.focus(); });
document.querySelectorAll("[data-gallery-filter]").forEach((button) => button.addEventListener("click", () => {
  state.galleryFilter = button.dataset.galleryFilter;
  document.querySelectorAll("[data-gallery-filter]").forEach((item) => {
    const active = item === button;
    item.classList.toggle("is-active", active);
    item.setAttribute("aria-pressed", String(active));
  });
  renderGallery();
}));
$("#open-payment").addEventListener("click", openPayment);
$("#mobile-payment").addEventListener("click", openPayment);
$("#close-payment").addEventListener("click", () => elements.dialog.close());
elements.paymentSubmit.addEventListener("click", () => {
  if (state.selectedPack) beginPayment(state.selectedPack.id, elements.paymentSubmit);
});
elements.dialog.addEventListener("click", (event) => { if (event.target === elements.dialog) elements.dialog.close(); });
elements.authDialog.addEventListener("cancel", (event) => event.preventDefault());
window.addEventListener("pageshow", () => {
  if (state.initialized) restoreTelegramPending();
});
document.addEventListener("visibilitychange", () => {
  if (state.initialized && document.visibilityState === "visible") restoreTelegramPending();
});
elements.telegramLogin.addEventListener("click", startTelegramLogin);
elements.improveButton.addEventListener("click", () => openImprove());
$("#close-improve").addEventListener("click", closeImprove);
$("#regenerate-improve").addEventListener("click", () => openImprove());
elements.onboardingSkip.addEventListener("click", finishOnboarding);
elements.onboardingBack.addEventListener("click", () => { state.onboardingStep = Math.max(0, state.onboardingStep - 1); renderOnboarding(); });
elements.onboardingNext.addEventListener("click", () => {
  if (state.onboardingStep >= onboardingSteps.length - 1) finishOnboarding();
  else { state.onboardingStep += 1; renderOnboarding(); }
});
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
