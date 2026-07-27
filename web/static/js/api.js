export class ApiError extends Error {
  constructor(code, message, status) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

const DEFAULT_RETRY_DELAYS = Object.freeze([400, 1200]);

function defaultSleep(delayMs) {
  return new Promise((resolve) => globalThis.setTimeout(resolve, delayMs));
}

function isRetryable(error) {
  return error instanceof ApiError
    && (
      error.status === 0
      || error.status >= 500
      || error.code === "invalid_response"
    );
}

async function requestJsonOnce(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: { Accept: "application/json", ...(options.headers || {}) },
    });
  } catch (_error) {
    throw new ApiError("network_error", "The local dashboard could not be reached", 0);
  }

  let payload;
  try {
    payload = await response.json();
  } catch (_error) {
    throw new ApiError("invalid_response", "The dashboard returned an invalid response", response.status);
  }

  if (!response.ok) {
    const envelope = payload && payload.error ? payload.error : {};
    throw new ApiError(
      envelope.code || "request_failed",
      envelope.message || "The request could not be completed",
      response.status,
    );
  }
  return payload;
}

export async function requestJson(path, options = {}, retryOptions = {}) {
  const retryDelays = retryOptions.retryDelays ?? DEFAULT_RETRY_DELAYS;
  const sleep = retryOptions.sleep ?? defaultSleep;
  let retryIndex = 0;

  while (true) {
    try {
      return await requestJsonOnce(path, options);
    } catch (error) {
      if (!isRetryable(error) || retryIndex >= retryDelays.length) throw error;
      await sleep(retryDelays[retryIndex]);
      retryIndex += 1;
    }
  }
}

export function getUniverse() {
  return requestJson("/api/universe");
}

export function getStock(ticker) {
  return requestJson(`/api/stocks/${encodeURIComponent(ticker)}`);
}

export function getStockForecast(ticker, date) {
  return requestJson(
    `/api/stocks/${encodeURIComponent(ticker)}/forecasts/${encodeURIComponent(date)}`,
  );
}

export function setResearchPoolMembership(ticker, included) {
  return requestJson(
    `/api/research-pool/${encodeURIComponent(ticker)}`,
    { method: included ? "PUT" : "DELETE" },
  );
}

export function getMarketOverview({
  asof = "",
  horizon = 5,
  sector = "semiconductor",
} = {}) {
  const params = new URLSearchParams({
    horizon: String(horizon),
    sector: String(sector),
  });
  if (asof) params.set("asof", String(asof));
  return requestJson(`/api/market-overview?${params.toString()}`);
}

export function getMacroHistory({
  asof = "",
  range = "3y",
  benchmark = "SPY",
} = {}) {
  const params = new URLSearchParams({
    range: String(range),
    benchmark: String(benchmark),
  });
  if (asof) params.set("asof", String(asof));
  return requestJson(`/api/macro-history?${params.toString()}`);
}

export function startUpdate() {
  return requestJson("/api/update", { method: "POST" });
}

export function getUpdateStatus() {
  return requestJson("/api/update/status");
}

export function getCacheStatus() {
  return requestJson("/api/cache/status");
}

export const api = Object.freeze({
  getUniverse,
  getStock,
  getStockForecast,
  setResearchPoolMembership,
  getMarketOverview,
  getMacroHistory,
  startUpdate,
  getUpdateStatus,
  getCacheStatus,
});
