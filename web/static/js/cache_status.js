import { api } from "./api.js";
import { getLocale, subscribeLocale, t } from "./i18n.js";

function accessKey(value) {
  return new Set(["memory_hit", "disk_hit", "rebuilt", "miss"]).has(value)
    ? value
    : "unavailable";
}

function stateKey(value) {
  return new Set(["ready", "empty", "rebuilding", "unavailable"]).has(value)
    ? value
    : "unavailable";
}

function formatBytes(value, locale) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const position = Math.min(
    units.length - 1,
    Math.floor(Math.log(bytes) / Math.log(1024)),
  );
  const scaled = bytes / (1024 ** position);
  return `${new Intl.NumberFormat(locale, {
    maximumFractionDigits: position === 0 ? 0 : 1,
  }).format(scaled)} ${units[position]}`;
}

export function describeCacheStatus(status = {}, locale = getLocale()) {
  const state = stateKey(status.state);
  if (state !== "ready") {
    return t(`cache.state.${state}`, {}, locale);
  }
  return t("cache.state.ready", {
    access: t(`cache.access.${accessKey(status.last_access)}`, {}, locale),
    asof: status.market_asof || t("cache.value.unavailable", {}, locale),
    entries: Number.isFinite(status.entry_count) ? status.entry_count : 0,
    size: formatBytes(status.size_bytes, locale),
  }, locale);
}

function detailLines(status, locale) {
  return [
    ["cache.field.model", [status.model_key, status.model_version].filter(Boolean).join(" · ")],
    ["cache.field.features", status.feature_version],
    ["cache.field.risk", status.risk_context_version],
    ["cache.field.created", status.latest_created_at],
    ["cache.field.memory", t(
      status.memory_ready ? "cache.value.ready" : "cache.value.notReady",
      {},
      locale,
    )],
    ["cache.field.revision", status.database_revision],
  ];
}

function renderDetails(container, status, locale) {
  if (!container || !globalThis.document) return;
  container.replaceChildren();
  for (const [labelKey, rawValue] of detailLines(status, locale)) {
    const row = document.createElement("div");
    const label = document.createElement("span");
    const value = document.createElement("strong");
    label.textContent = t(labelKey, {}, locale);
    value.textContent = rawValue == null || rawValue === ""
      ? t("cache.value.unavailable", {}, locale)
      : String(rawValue);
    row.append(label, value);
    container.append(row);
  }
}

export function createCacheStatusController(options = {}) {
  const summary = options.summary;
  const details = options.details;
  const refreshButton = options.refreshButton;
  const apiClient = options.apiClient || api;
  let locale = getLocale();
  let latest = { state: "unavailable" };
  let destroyed = false;
  let generation = 0;

  function render() {
    if (summary) summary.textContent = describeCacheStatus(latest, locale);
    renderDetails(details, latest, locale);
  }

  async function refresh() {
    if (destroyed) return latest;
    generation += 1;
    const current = generation;
    if (refreshButton) refreshButton.disabled = true;
    try {
      const payload = await apiClient.getCacheStatus();
      if (!destroyed && current === generation) {
        latest = payload && typeof payload === "object"
          ? payload
          : { state: "unavailable" };
        render();
      }
    } catch (_error) {
      if (!destroyed && current === generation) {
        latest = { state: "unavailable" };
        render();
      }
    } finally {
      if (!destroyed && current === generation && refreshButton) {
        refreshButton.disabled = false;
      }
    }
    return latest;
  }

  const clickHandler = () => { void refresh(); };
  refreshButton?.addEventListener("click", clickHandler);
  const unsubscribeLocale = subscribeLocale((nextLocale) => {
    locale = nextLocale;
    render();
  });

  return Object.freeze({
    initialize: refresh,
    refresh,
    destroy() {
      destroyed = true;
      generation += 1;
      unsubscribeLocale();
      refreshButton?.removeEventListener?.("click", clickHandler);
    },
  });
}
