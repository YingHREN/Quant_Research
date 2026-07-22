const STORAGE_KEY = "quant-dashboard-locale";

export const SUPPORTED_LOCALES = Object.freeze(["zh-CN", "en"]);

const messages = {
  "zh-CN": {
    "universe.shown": "显示 {shown}/{total} 只股票",
  },
  en: {
    "universe.shown": "Showing {shown}/{total} stocks",
  },
};

function normalizeLocale(locale) {
  return SUPPORTED_LOCALES.includes(locale) ? locale : "zh-CN";
}

function readStoredLocale() {
  try {
    return globalThis.localStorage?.getItem(STORAGE_KEY);
  } catch (_error) {
    return null;
  }
}

let currentLocale = normalizeLocale(readStoredLocale());
const listeners = new Set();

export function getLocale() {
  return currentLocale;
}

export function setLocale(locale) {
  currentLocale = normalizeLocale(locale);
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, currentLocale);
  } catch (_error) {
    // Browser privacy settings can disable storage; locale still works in memory.
  }
  listeners.forEach((listener) => listener(currentLocale));
  return currentLocale;
}

export function subscribeLocale(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function t(key, params = {}, locale = currentLocale) {
  const selectedLocale = normalizeLocale(locale);
  const message = messages[selectedLocale]?.[key] ?? messages.en?.[key] ?? key;
  return message.replace(/\{(\w+)\}/g, (match, name) => (
    Object.hasOwn(params, name) ? String(params[name]) : match
  ));
}

function validDateParts(year, month, day) {
  if (![year, month, day].every(Number.isInteger)) return null;
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year
    && date.getUTCMonth() === month - 1
    && date.getUTCDate() === day
    ? { year, month, day }
    : null;
}

function dateParts(value) {
  if (typeof value === "string") {
    const match = /^(\d{4})-(\d{2})-(\d{2})(?:$|T)/.exec(value);
    return match ? validDateParts(...match.slice(1).map(Number)) : null;
  }
  if (value && typeof value === "object" && !(value instanceof Date)) {
    return validDateParts(value.year, value.month, value.day);
  }
  if (value instanceof Date && !Number.isNaN(value.valueOf())) {
    return validDateParts(
      value.getUTCFullYear(), value.getUTCMonth() + 1, value.getUTCDate(),
    );
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    const date = new Date(value * 1000);
    return validDateParts(
      date.getUTCFullYear(), date.getUTCMonth() + 1, date.getUTCDate(),
    );
  }
  return null;
}

function pad(value) {
  return String(value).padStart(2, "0");
}

export function formatChartTickDate(value) {
  const part = dateParts(value);
  return part ? `${pad(part.month)}-${pad(part.day)}` : "—";
}

export function formatFullDate(value) {
  const part = dateParts(value);
  return part ? `${part.year}-${pad(part.month)}-${pad(part.day)}` : "—";
}
