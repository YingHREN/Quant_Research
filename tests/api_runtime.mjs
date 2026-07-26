import assert from "node:assert/strict";

const [apiUri] = process.argv.slice(2);
const { requestJson } = await import(apiUri);

function response(payload, status = 200, jsonError = null) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() {
      if (jsonError) throw jsonError;
      return payload;
    },
  };
}

const noWait = async () => {};

let attempts = 0;
globalThis.fetch = async () => {
  attempts += 1;
  if (attempts < 3) throw new TypeError("temporary network failure");
  return response({ ok: true });
};
assert.deepEqual(
  await requestJson("/network", {}, { retryDelays: [0, 0], sleep: noWait }),
  { ok: true },
);
assert.equal(attempts, 3);

attempts = 0;
globalThis.fetch = async () => {
  attempts += 1;
  return attempts === 1
    ? response({ error: { code: "internal_error" } }, 503)
    : response({ ok: true });
};
await requestJson("/server", {}, { retryDelays: [0, 0], sleep: noWait });
assert.equal(attempts, 2);

attempts = 0;
globalThis.fetch = async () => {
  attempts += 1;
  return response({ error: { code: "unknown_ticker" } }, 404);
};
await assert.rejects(
  requestJson("/missing", {}, { retryDelays: [0, 0], sleep: noWait }),
  (error) => error.code === "unknown_ticker" && error.status === 404,
);
assert.equal(attempts, 1);

attempts = 0;
globalThis.fetch = async () => {
  attempts += 1;
  return response(null, 200, new SyntaxError("temporary invalid JSON"));
};
await assert.rejects(
  requestJson("/invalid", {}, { retryDelays: [0, 0], sleep: noWait }),
  (error) => error.code === "invalid_response" && error.status === 200,
);
assert.equal(attempts, 3);

console.log(JSON.stringify({
  networkAttempts: 3,
  serverAttempts: 2,
  clientAttempts: 1,
  invalidAttempts: 3,
}));
