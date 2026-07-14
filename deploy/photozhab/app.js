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
  gallery: [],
  galleryFilter: "all",
  galleryOpen: false,
  selectedPack: null,
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
  modelOptions: $("#model-options"),
  mode: $("#mode-select"),
  aspect: $("#aspect-select"), count: $("#count-select"), countWrap: $(".count-select"),
  aspectOptions: $("#aspect-options"), countRange: $("#count-range"), countOutput: $("#count-output"),
  uploadButton: $("#upload-button"), uploadInput: $("#image-upload"), uploadPreview: $("#upload-preview"),
  uploadImage: $("#upload-preview-image"), uploadName: $("#upload-file-name"),
  price: $("#request-price"), title: $("#chat-mode-title"), subtitle: $("#chat-mode-subtitle"),
  sidebarBalance: $("#sidebar-balance"), mobileBalance: $("#mobile-balance-value"),
  dialog: $("#payment-dialog"), packs: $("#pack-grid"), toast: $("#toast"),
  authDialog: $("#auth-dialog"), telegramLogin: $("#login-telegram"),
  maxLogin: $("#login-max"), yandexLogin: $("#login-yandex"),
  telegramForm: $("#telegram-code-form"), telegramCode: $("#telegram-code"),
  authNote: $("#auth-note"), accountCard: $("#account-card"),
  accountName: $("#account-name"), accountAvatar: $("#account-avatar"), logout: $("#logout-button"),
  mobileAccount: $("#mobile-account"),
  historyList: $("#chat-history-list"), historyEmpty: $("#chat-history-empty"),
  welcomeTitle: $("#welcome-title"), welcomeCopy: $("#welcome-copy"),
  galleryView: $("#gallery-view"), galleryGrid: $("#gallery-grid"), galleryEmpty: $("#gallery-empty"),
  composerWrap: $(".composer-wrap"), galleryButton: $("#open-gallery"),
  paymentBalance: $("#payment-current-balance"), paymentSubmit: $("#payment-submit"),
  telegramSlots: $("#telegram-code-slots"),
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
  elements.accountAvatar.textContent = authenticated
    ? (state.session.identity?.display_name || "P").trim().charAt(0).toLocaleUpperCase("ru-RU") || "P"
    : "P";
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
    renderTelegramCode();
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

function renderTelegramCode() {
  const value = elements.telegramCode.value.replace(/\D/g, "").slice(0, 6);
  if (elements.telegramCode.value !== value) elements.telegramCode.value = value;
  elements.telegramSlots.querySelectorAll("span").forEach((slot, index) => {
    const digit = value[index];
    slot.textContent = digit || "•";
    slot.classList.toggle("is-filled", Boolean(digit));
  });
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
    let node;
    if (media.type === "video") {
      node = document.createElement("video"); node.controls = true; node.playsInline = true; node.preload = "metadata";
    } else {
      node = document.createElement("img"); node.alt = "Изображение, созданное Photozhab"; node.loading = "lazy";
    }
    node.src = media.url;
    const actions = document.createElement("div"); actions.className = "media-result-actions";
    const link = document.createElement("a");
    link.href = media.url; link.target = "_blank"; link.rel = "noopener"; link.download = ""; link.textContent = "↓ Скачать";
    actions.append(link);
    if (media.type !== "video") {
      const edit = document.createElement("button"); edit.type = "button"; edit.textContent = "✎ Изменить";
      edit.addEventListener("click", () => useResultAsSource(media.url, "edit"));
      const animate = document.createElement("button"); animate.type = "button"; animate.textContent = "◉ Оживить";
      animate.addEventListener("click", () => useResultAsSource(media.url, "animate"));
      actions.append(edit, animate);
    }
    figure.append(node, actions); grid.append(figure);
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

async function useResultAsSource(url, mode) {
  try {
    const response = await fetch(url, {credentials: "same-origin"});
    if (!response.ok) throw new Error("media_unavailable");
    const blob = await response.blob();
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
    setMode(mode);
    state.imageData = dataUrl;
    state.imageName = "Результат Photozhab";
    elements.uploadImage.src = dataUrl;
    elements.uploadName.textContent = state.imageName;
    elements.uploadPreview.hidden = false;
    elements.prompt.focus();
  } catch (_) {
    toast("Откройте результат и загрузите его как референс");
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
  rememberRequest(prompt);
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
      mode: state.mode, prompt, aspect: elements.aspect.value, count: Number(elements.count.value),
      image_model: state.mode === "image" || state.mode === "edit" ? model.id : undefined,
      video_model: state.mode === "video" || state.mode === "animate" ? model.id : undefined,
      image_b64: state.imageData,
    })});
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
    const retry = document.createElement("button"); retry.type = "button"; retry.textContent = "Изменить запрос";
    retry.addEventListener("click", () => { elements.prompt.value = prompt; resizePrompt(); elements.prompt.focus(); });
    errorBox.append(title, paragraph, retry); pending.content.append(errorBox);
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
  updatePrice();
};
elements.count.addEventListener("change", () => syncCount(elements.count.value));
elements.countRange.addEventListener("input", () => syncCount(elements.countRange.value));
document.querySelectorAll(".mode-nav-button").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.mode)));
document.querySelectorAll("#prompt-suggestions button").forEach((button) => button.addEventListener("click", () => { elements.prompt.value = button.textContent; resizePrompt(); elements.prompt.focus(); }));
elements.uploadInput.addEventListener("change", () => {
  const file = elements.uploadInput.files[0];
  if (!file) return;
  if (!["image/png", "image/jpeg", "image/webp"].includes(file.type) || file.size > (state.session?.limits.image_bytes || 10485760)) { clearUpload(); toast("PNG, JPEG или WebP — не больше 10 МБ"); return; }
  if (state.mode === "image") setMode("edit");
  const reader = new FileReader();
  reader.onload = () => { state.imageData = String(reader.result); state.imageName = file.name; elements.uploadImage.src = state.imageData; elements.uploadName.textContent = file.name; elements.uploadPreview.hidden = false; };
  reader.onerror = () => { clearUpload(); toast("Не удалось прочитать изображение"); };
  reader.readAsDataURL(file);
});
$("#remove-upload").addEventListener("click", clearUpload);
$("#new-chat").addEventListener("click", () => { closeGallery(); elements.messages.replaceChildren(); elements.welcome.hidden = false; clearUpload(); elements.prompt.value = ""; resizePrompt(); });
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
elements.telegramLogin.addEventListener("click", startTelegramLogin);
elements.telegramForm.addEventListener("submit", completeTelegramLogin);
elements.telegramCode.addEventListener("input", renderTelegramCode);
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
