export class ApiError extends Error {
  constructor(code, message, status) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

async function requestJson(path, options = {}) {
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

export function getUniverse() {
  return requestJson("/api/universe");
}

export function getStock(ticker) {
  return requestJson(`/api/stocks/${encodeURIComponent(ticker)}`);
}

export function startUpdate() {
  return requestJson("/api/update", { method: "POST" });
}

export function getUpdateStatus() {
  return requestJson("/api/update/status");
}

export const api = Object.freeze({ getUniverse, getStock, startUpdate, getUpdateStatus });
