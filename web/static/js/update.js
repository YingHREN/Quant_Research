import { api } from "./api.js";

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

export function describeUpdateSnapshot(snapshot = {}) {
  const { completed, total, updated } = progress(snapshot);
  if (snapshot.state === "running") {
    const ticker = snapshot.current_ticker ? ` · checking ${snapshot.current_ticker}` : "";
    return `Price-only update running: ${completed}/${total} checked · ${updated} updated${ticker}`;
  }
  if (snapshot.state === "completed") {
    return `Price-only update finished: ${updated}/${total} updated.`;
  }
  if (snapshot.state === "partial") {
    return `Price-only update stopped with partial results: ${completed}/${total} checked · ${updated} updated.`;
  }
  if (snapshot.state === "rate_limited") {
    return `Rate limited after ${completed}/${total} checked; ${updated} updated. Resume preserves remaining work.`;
  }
  if (snapshot.state === "failed") {
    return `Price-only update failed after ${completed}/${total} checked; ${updated} updated.`;
  }
  return "Price-only update status: idle";
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

function buttonLabel(snapshot) {
  if (snapshot.state === "rate_limited" && snapshot.resumable) return "Resume price update";
  if (snapshot.state === "failed" && snapshot.resumable) return "Resume price update";
  return "Update market data";
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

  function render(snapshot) {
    if (status) status.textContent = describeUpdateSnapshot(snapshot);
    if (button) {
      button.textContent = buttonLabel(snapshot);
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
      if (status) status.textContent = "Update status is temporarily unavailable.";
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
      if (status) {
        status.textContent = "Update status is temporarily unavailable; still running and retrying.";
      }
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
      if (status) status.textContent = error && error.message ? error.message : "Unable to start price update";
      setTone(status, "error");
      if (button) button.disabled = false;
    }
  }

  const clickHandler = () => { void start(); };
  if (button) {
    button.addEventListener("click", clickHandler);
    render({ state: "idle" });
  }

  return Object.freeze({
    initialize,
    start,
    isTerminal: isUpdateTerminal,
    destroy() {
      destroyed = true;
      generation += 1;
      clearPoll();
      if (button && typeof button.removeEventListener === "function") {
        button.removeEventListener("click", clickHandler);
      }
    },
  });
}
