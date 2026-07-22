import { getLocale } from "./i18n.js";

const STORAGE_KEY = "quant-workstation.selected-ticker";

function normalizeTicker(value) {
  return typeof value === "string" ? value.trim().toUpperCase() : "";
}

export function chooseInitialTicker(rows, restoredTicker) {
  const candidates = Array.isArray(rows) ? rows : [];
  const restored = normalizeTicker(restoredTicker);
  const restoredRow = candidates.find((row) => normalizeTicker(row.ticker) === restored);
  if (restoredRow) return normalizeTicker(restoredRow.ticker);

  const activeRow = candidates.find((row) => !row.inactive);
  const fallback = activeRow || candidates[0];
  return fallback ? normalizeTicker(fallback.ticker) : null;
}

export function readStoredTicker(storage = globalThis.localStorage) {
  try {
    return storage ? storage.getItem(STORAGE_KEY) : null;
  } catch (_error) {
    return null;
  }
}

export function persistSelectedTicker(ticker, storage = globalThis.localStorage) {
  try {
    if (storage && ticker) storage.setItem(STORAGE_KEY, normalizeTicker(ticker));
  } catch (_error) {
    // Browser privacy settings can disable storage; selection still works in memory.
  }
}

export function createStore(initialState = {}) {
  let state = {
    universe: [],
    universePayload: null,
    selectedTicker: null,
    stockPayload: null,
    query: "",
    filters: {},
    sortKey: "ticker",
    sortDirection: "asc",
    locale: getLocale(),
    ...initialState,
  };
  const listeners = new Set();

  return Object.freeze({
    getState() {
      return state;
    },
    setState(patch) {
      state = { ...state, ...patch };
      listeners.forEach((listener) => listener(state));
      return state;
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  });
}

export const store = createStore();
