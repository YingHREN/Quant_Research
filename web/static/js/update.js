import { api } from "./api.js";
import { getLocale, subscribeLocale, t, translateError } from "./i18n.js";

const TERMINAL_STATES = new Set(["idle", "completed", "partial", "rate_limited", "failed"]);
const POLL_INTERVAL_MS = 1000;
const MAX_RETRY_INTERVAL_MS = 5000;

export function isUpdateTerminal(state) {
  return TERMINAL_STATES.has(state);
}

export function updateRetryDelay(failureCount) {
  const count = Number.isFinite(failureCount) ? Math.max(1, failureCount) : 1;
  return Math.min(
    MAX_RETRY_INTERVAL_MS,
    POLL_INTERVAL_MS * (2 ** Math.min(count, 3)),
  );
}

function progress(snapshot) {
  const completed = Number.isFinite(snapshot.completed) ? snapshot.completed : 0;
  const total = Number.isFinite(snapshot.total) ? snapshot.total : 0;
  const updated = Number.isFinite(snapshot.updated) ? snapshot.updated : 0;
  return { completed, total, updated };
}

function updateMessage(snapshot = {}, locale = getLocale()) {
  const { completed, total, updated } = progress(snapshot);
  if (snapshot.state === "running") {
    return {
      key: "update.state.running",
      params: {
        completed,
        total,
        updated,
        ticker: snapshot.current_ticker
          ? t("update.state.runningTicker", { ticker: snapshot.current_ticker }, locale)
          : "",
      },
    };
  }
  if (snapshot.state === "completed") {
    return { key: "update.state.completed", params: { updated, total } };
  }
  if (snapshot.state === "partial") {
    return { key: "update.state.partial", params: { completed, total, updated } };
  }
  if (snapshot.state === "rate_limited") {
    return { key: "update.state.rateLimited", params: { completed, total, updated } };
  }
  if (snapshot.state === "failed") {
    return { key: "update.state.failed", params: { completed, total, updated } };
  }
  return { key: "update.state.idle", params: {} };
}

export function describeUpdateSnapshot(snapshot = {}, locale = getLocale()) {
  const message = updateMessage(snapshot, locale);
  return t(message.key, message.params, locale);
}

export function shouldReloadSelectedTicker(selectedTicker, observationDate, rows) {
  if (!selectedTicker) return false;
  const selected = (Array.isArray(rows) ? rows : []).find(
    (row) => String(row.ticker || "").toUpperCase() === String(selectedTicker).toUpperCase(),
  );
  if (!selected) return false;
  return (selected.latest_date || null) !== (observationDate || null);
}

function setTone(element, tone) {
  if (!element) return;
  if (tone) element.dataset.tone = tone;
  else delete element.dataset.tone;
}

function buttonLabel(snapshot, locale) {
  if (snapshot.state === "rate_limited" && snapshot.resumable) {
    return t("update.button.resume", {}, locale);
  }
  if (snapshot.state === "failed" && snapshot.resumable) {
    return t("update.button.resume", {}, locale);
  }
  return t("update.button.start", {}, locale);
}

export function createUpdateController(options = {}) {
  const button = options.button || globalThis.document?.getElementById("update-data");
  const status = options.status || globalThis.document?.getElementById("update-status");
  const apiClient = options.apiClient || api;
  const schedule = options.schedule || globalThis.setTimeout;
  const cancel = options.cancel || globalThis.clearTimeout;
  const onTerminal = typeof options.onTerminal === "function" ? options.onTerminal : async () => {};
  let timer = null;
  let generation = 0;
  let destroyed = false;
  let statusFailureCount = 0;
  let locale = getLocale();
  let lastSnapshot = { state: "idle" };
  let lastStatus = { kind: "message", key: "update.state.idle", params: {} };

  function localizedStatus(key, params = {}) {
    lastStatus = { kind: "message", key, params };
    if (status) status.textContent = t(key, params, locale);
  }

  function localizedErrorStatus(error, fallbackKey) {
    lastStatus = { kind: "error", error, fallbackKey };
    if (status) status.textContent = translateError(error, fallbackKey, locale);
  }

  function render(snapshot) {
    lastSnapshot = snapshot;
    const message = updateMessage(snapshot, locale);
    localizedStatus(message.key, message.params);
    if (button) {
      button.textContent = buttonLabel(snapshot, locale);
      button.disabled = snapshot.state === "running";
    }
    const errorTone = snapshot.state === "failed" ? "error" : null;
    const partialTone = snapshot.state === "partial" || snapshot.state === "rate_limited" ? "warning" : errorTone;
    setTone(status, partialTone);
  }

  function clearPoll() {
    if (timer != null) cancel(timer);
    timer = null;
  }

  async function accept(snapshot, currentGeneration, notifyTerminal = true) {
    if (destroyed || currentGeneration !== generation) return;
    statusFailureCount = 0;
    render(snapshot);
    if (snapshot.state === "running") {
      timer = schedule(() => poll(currentGeneration), POLL_INTERVAL_MS);
      return;
    }
    clearPoll();
    if (notifyTerminal && snapshot.state && snapshot.state !== "idle" && isUpdateTerminal(snapshot.state)) {
      await onTerminal(snapshot);
    }
  }

  async function initialize() {
    if (destroyed) return;
    generation += 1;
    const currentGeneration = generation;
    clearPoll();
    try {
      const snapshot = await apiClient.getUpdateStatus();
      await accept(snapshot, currentGeneration, false);
    } catch (_error) {
      if (destroyed || currentGeneration !== generation) return;
      localizedStatus("update.statusUnavailable");
      setTone(status, "warning");
      if (button) button.disabled = false;
    }
  }

  async function poll(currentGeneration) {
    timer = null;
    try {
      const snapshot = await apiClient.getUpdateStatus();
      await accept(snapshot, currentGeneration);
    } catch (error) {
      if (destroyed || currentGeneration !== generation) return;
      statusFailureCount += 1;
      localizedStatus("update.statusRetrying");
      setTone(status, "warning");
      if (button) button.disabled = true;
      const retryDelay = updateRetryDelay(statusFailureCount);
      timer = schedule(() => poll(currentGeneration), retryDelay);
    }
  }

  async function start() {
    if (destroyed) return;
    generation += 1;
    const currentGeneration = generation;
    statusFailureCount = 0;
    clearPoll();
    render({ state: "running", completed: 0, total: 0, updated: 0 });
    try {
      const snapshot = await apiClient.startUpdate();
      await accept(snapshot, currentGeneration);
    } catch (error) {
      if (destroyed || currentGeneration !== generation) return;
      if (error && error.code === "update_in_progress") {
        await poll(currentGeneration);
        return;
      }
      localizedErrorStatus(error, "update.startFailed");
      setTone(status, "error");
      if (button) button.disabled = false;
    }
  }

  const clickHandler = () => { void start(); };
  if (button) {
    button.addEventListener("click", clickHandler);
    render({ state: "idle" });
  }

  const unsubscribeLocale = subscribeLocale((nextLocale) => {
    locale = nextLocale;
    if (lastStatus && status) {
      if (lastStatus.kind === "error") {
        status.textContent = translateError(lastStatus.error, lastStatus.fallbackKey, locale);
      } else if (lastStatus.key === "update.state.running") {
        lastStatus = { kind: "message", ...updateMessage(lastSnapshot, locale) };
        status.textContent = t(lastStatus.key, lastStatus.params, locale);
      } else {
        status.textContent = t(lastStatus.key, lastStatus.params, locale);
      }
    }
    if (button) button.textContent = buttonLabel(lastSnapshot, locale);
  });

  return Object.freeze({
    initialize,
    start,
    isTerminal: isUpdateTerminal,
    destroy() {
      destroyed = true;
      generation += 1;
      clearPoll();
      unsubscribeLocale();
      if (button && typeof button.removeEventListener === "function") {
        button.removeEventListener("click", clickHandler);
      }
    },
  });
}
