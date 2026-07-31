const STORAGE_KEY = "malta_locale";
const SUPPORTED = new Set(["en", "pl"]);

let _locale = "en";
let _messages = {};

function getNested(obj, key) {
  return obj[key];
}

export function getLocale() {
  return _locale;
}

export function normalizeLocale(value) {
  const code = String(value || "en").trim().toLowerCase().slice(0, 2);
  return SUPPORTED.has(code) ? code : "en";
}

export async function initI18n() {
  const params = new URLSearchParams(window.location.search);
  const fromUrl = params.get("lang");
  const stored = localStorage.getItem(STORAGE_KEY);
  _locale = normalizeLocale(fromUrl || stored || "en");
  localStorage.setItem(STORAGE_KEY, _locale);
  document.documentElement.lang = _locale;

  const [enRes, plRes] = await Promise.all([
    fetch("/static/locales/en.json"),
    fetch("/static/locales/pl.json"),
  ]);
  _messages = {
    en: await enRes.json(),
    pl: await plRes.json(),
  };
  applyTranslations();
}

export function setLocale(locale) {
  const next = normalizeLocale(locale);
  if (next === _locale) return;
  _locale = next;
  localStorage.setItem(STORAGE_KEY, _locale);
  document.documentElement.lang = _locale;
  applyTranslations();
  const params = new URLSearchParams(window.location.search);
  params.set("lang", _locale);
  const qs = params.toString();
  const target = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
  history.replaceState(null, "", target);
  window.dispatchEvent(new CustomEvent("localechange", { detail: { locale: _locale } }));
}

export function t(key, vars = {}) {
  const table = _messages[_locale] || _messages.en || {};
  let text = getNested(table, key) || getNested(_messages.en || {}, key) || key;
  for (const [name, value] of Object.entries(vars)) {
    text = text.replaceAll(`{${name}}`, String(value));
  }
  return text;
}

export function intlLocale() {
  return _locale === "pl" ? "pl-PL" : "en-GB";
}

export function applyTranslations() {
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    if (!key) return;
    el.textContent = t(key);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    const key = el.getAttribute("data-i18n-placeholder");
    if (key) el.placeholder = t(key);
  });
  document.querySelectorAll("[data-i18n-aria]").forEach((el) => {
    const key = el.getAttribute("data-i18n-aria");
    if (key) el.setAttribute("aria-label", t(key));
  });
  document.querySelectorAll("[data-i18n-title]").forEach((el) => {
    const key = el.getAttribute("data-i18n-title");
    if (key) el.title = t(key);
  });
  document.querySelectorAll(".lang-switch button").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.lang === _locale);
  });
  const title = document.querySelector("title");
  if (title) {
    title.textContent = `${t("brand")} — ${t("results.title")}`;
  }
}

export function wireLangSwitcher() {
  document.querySelectorAll(".lang-switch button").forEach((btn) => {
    btn.addEventListener("click", () => {
      setLocale(btn.dataset.lang);
    });
  });
}
