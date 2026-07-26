# Dashboard Request Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the stock universe usable during transient request failures or a temporary old-template/new-script mismatch, with bounded automatic retries and explicit manual recovery actions.

**Architecture:** Add a retry policy at the front-end API boundary, keep optional component guards at their own renderer boundary, and preserve successful universe state when later work fails. Recovery controls call the same idempotent load functions rather than duplicating fetch or rendering logic.

**Tech Stack:** Browser ES modules, Fetch API, Flask/Jinja HTML, Node runtime harness, Python `unittest`.

## Global Constraints

- Retry only network errors, invalid temporary responses, and HTTP `5xx`.
- Use exactly two retry delays: `400ms` and `1200ms`; the maximum is three actual attempts.
- Never retry deterministic HTTP `4xx`.
- Do not add infinite polling or a permanent health-check loop.
- A missing optional `top-risk-state` element must not block the rest of the dashboard.
- Do not make global `setText()` silently ignore every missing required element.
- Preserve the last successful universe during refresh failures.
- Do not modify forecasting, VCP, Pocket Pivot, research-price, market-behavior, or momentum-model code.

---

### Task 1: Bounded API Retry Policy

**Files:**
- Create: `tests/api_runtime.mjs`
- Modify: `web/static/js/api.js`
- Modify: `tests/test_web_assets.py`

**Interfaces:**
- Consumes: existing `ApiError(code, message, status)` and browser `fetch`.
- Produces: exported `requestJson(path, options = {}, retryOptions = {}) -> Promise<object>`.
- Produces: internal `isRetryable(error) -> boolean`.
- `retryOptions.retryDelays` is an array of milliseconds and defaults to `[400, 1200]`.
- `retryOptions.sleep` is an async function and defaults to a timer-backed promise.

- [ ] **Step 1: Write the failing API runtime test**

Create `tests/api_runtime.mjs` with deterministic cases that inject a zero-wait sleep:

```js
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

console.log(JSON.stringify({ networkAttempts: 3, serverAttempts: 2, clientAttempts: 1 }));
```

Add `run_api_runtime()` and `test_api_retries_only_transient_failures()` to `tests/test_web_assets.py`, following the existing Node runtime runners.

- [ ] **Step 2: Run the API runtime test and verify RED**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_api_retries_only_transient_failures -v
```

Expected: FAIL because `requestJson` is not exported and has no retry contract.

- [ ] **Step 3: Implement the minimal retry loop**

Refactor `web/static/js/api.js` around this interface:

```js
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

export async function requestJson(path, options = {}, retryOptions = {}) {
  const retryDelays = retryOptions.retryDelays ?? DEFAULT_RETRY_DELAYS;
  const sleep = retryOptions.sleep ?? defaultSleep;
  let attempt = 0;

  while (true) {
    try {
      return await requestJsonOnce(path, options);
    } catch (error) {
      if (!isRetryable(error) || attempt >= retryDelays.length) throw error;
      await sleep(retryDelays[attempt]);
      attempt += 1;
    }
  }
}
```

Move the existing single-attempt fetch/JSON/error-envelope behavior into `requestJsonOnce(path, options)`. Preserve every existing `ApiError` code and message.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_assets.WebAssetTest.test_api_retries_only_transient_failures \
  tests.test_web_assets.WebAssetTest.test_known_update_errors_are_localized_and_unknown_errors_remain_safe_fallbacks \
  -v
```

Expected: both tests PASS.

- [ ] **Step 5: Commit the retry policy**

```bash
git add web/static/js/api.js tests/api_runtime.mjs tests/test_web_assets.py
git commit -m "feat: retry transient dashboard requests"
```

---

### Task 2: Defend the Optional Top-Risk Badge Boundary

**Files:**
- Modify: `web/static/js/app.js`
- Modify: `tests/dashboard_runtime.mjs`
- Modify: `tests/test_web_assets.py`

**Interfaces:**
- Consumes: `renderTopRiskBadge(topRisk, element, locale)`.
- Produces: the same function with a no-op only when `element` is absent.
- Does not change `setText()` behavior for required elements.

- [ ] **Step 1: Add a failing old-template/new-script runtime case**

Extend `tests/dashboard_runtime.mjs` so mode `missing-top-risk-element` omits only `top-risk-state` from the element map. After `initializeDashboard()`, assert:

```js
assert.equal(elements.get("universe-count").textContent, "1/1");
assert.match(elements.get("research-status").textContent, /2026-07-22/);
assert.equal(elements.get("selected-ticker").textContent, "AAA");
console.log(JSON.stringify({
  count: elements.get("universe-count").textContent,
  ticker: elements.get("selected-ticker").textContent,
}));
```

Add `test_current_script_survives_template_without_optional_top_risk_badge()` to `tests/test_web_assets.py`.

- [ ] **Step 2: Run the mismatch test and verify RED**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_assets.WebAssetTest.test_current_script_survives_template_without_optional_top_risk_badge \
  -v
```

Expected: FAIL with `Cannot set properties of null (setting 'textContent')`.

- [ ] **Step 3: Add the targeted optional-component guard**

At the start of `renderTopRiskBadge()` in `web/static/js/app.js`, add:

```js
function renderTopRiskBadge(topRisk, element, locale) {
  if (!element) return;
  // existing renderer follows unchanged
}
```

Do not change `setText()` into a global null-safe function.

- [ ] **Step 4: Run mismatch and existing badge tests**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_assets.WebAssetTest.test_current_script_survives_template_without_optional_top_risk_badge \
  tests.test_web_assets.WebAssetTest.test_top_risk_badge_distinguishes_fading_and_unavailable \
  -v
```

Expected: both tests PASS.

- [ ] **Step 5: Commit the component-boundary fix**

```bash
git add web/static/js/app.js tests/dashboard_runtime.mjs tests/test_web_assets.py
git commit -m "fix: tolerate stale optional dashboard badge"
```

---

### Task 3: Preserve State and Add Manual Recovery Controls

**Files:**
- Modify: `web/templates/index.html`
- Modify: `web/static/js/app.js`
- Modify: `web/static/js/i18n.js`
- Modify: `tests/dashboard_runtime.mjs`
- Modify: `tests/test_web_assets.py`

**Interfaces:**
- Produces DOM elements `#universe-retry` and `#stock-retry`.
- `loadUniverse()` remains the only stock-pool reload entry point.
- `selectTicker(ticker)` remains the only single-stock load entry point.
- Produces helper `setRecoveryControl(element, { visible, loading })`.

- [ ] **Step 1: Add failing HTML and runtime recovery tests**

Add a page-contract test requiring:

```python
self.assertIn('id="universe-retry"', html)
self.assertIn('id="stock-retry"', html)
self.assertIn('data-i18n="recovery.universe"', html)
self.assertIn('data-i18n="recovery.stock"', html)
```

Extend `tests/dashboard_runtime.mjs` with:

- `stock-error-then-retry`: the first three stock attempts return `500`; clicking `stock-retry` makes the fourth attempt succeed.
- `universe-error-then-retry`: the first three universe attempts return `503`; clicking `universe-retry` makes the fourth attempt succeed.
- `universe-refresh-error`: first load succeeds, a later exported/manual `loadUniverse()` failure leaves the count and rows unchanged.

For error modes, replace the runtime’s timer with a zero-delay implementation before importing `app.js` so the production `400ms` and `1200ms` policy does not slow tests.

Assert after exhausted stock retries:

```js
assert.equal(elements.get("universe-count").textContent, "1/1");
assert.equal(elements.get("stock-retry").hidden, false);
```

Assert after a manual retry succeeds:

```js
assert.equal(elements.get("stock-retry").hidden, true);
assert.match(elements.get("research-status").textContent, /2026-07-22/);
```

- [ ] **Step 2: Run recovery tests and verify RED**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_assets.WebAssetTest.test_page_has_request_recovery_controls \
  tests.test_web_assets.WebAssetTest.test_dashboard_recovers_universe_and_stock_requests \
  -v
```

Expected: FAIL because the controls and recovery state do not exist.

- [ ] **Step 3: Add accessible recovery controls and translations**

In `web/templates/index.html`, place `universe-retry` next to `universe-status` and `stock-retry` next to `research-status`. Both start with the HTML `hidden` attribute and use `type="button"`.

Add exact translations to `web/static/js/i18n.js`:

```js
"recovery.universe": "重新加载股票池",
"recovery.stock": "重试当前股票",
"recovery.loading": "正在重试…",
```

and:

```js
"recovery.universe": "Reload stock pool",
"recovery.stock": "Retry current stock",
"recovery.loading": "Retrying…",
```

- [ ] **Step 4: Wire the controls and preserve successful state**

Capture both buttons in `captureElements()`. Add:

```js
function setRecoveryControl(element, { visible = false, loading = false } = {}) {
  if (!element) return;
  element.hidden = !visible;
  element.disabled = loading;
}
```

In `loadUniverse()`:

- hide and disable the universe retry while loading;
- on success, keep it hidden;
- on failure, inspect `store.getState().universe`;
- clear universe state only when no successful rows exist;
- otherwise repaint the existing rows and only show the localized error;
- show and enable `universe-retry`.

In `selectTicker()`:

- hide and disable `stock-retry` while loading;
- keep it hidden on success;
- show and enable it after the final error;
- never change `universe` or `universePayload`.

Bind:

```js
elements.universeRetry.addEventListener("click", () => {
  void loadUniverse();
});
elements.stockRetry.addEventListener("click", () => {
  const ticker = store.getState().selectedTicker;
  if (ticker) void selectTicker(ticker);
});
```

Keep the existing `stockRequestSequence` stale-response protection.

- [ ] **Step 5: Run recovery, localization, and interaction tests**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_assets.WebAssetTest.test_page_has_request_recovery_controls \
  tests.test_web_assets.WebAssetTest.test_dashboard_recovers_universe_and_stock_requests \
  tests.test_web_assets.WebAssetTest.test_actual_dashboard_locale_switch_preserves_safe_error_states \
  -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit manual recovery**

```bash
git add \
  web/templates/index.html \
  web/static/js/app.js \
  web/static/js/i18n.js \
  tests/dashboard_runtime.mjs \
  tests/test_web_assets.py
git commit -m "feat: add dashboard request recovery controls"
```

---

### Task 4: Regression Verification and TODO Closure

**Files:**
- Modify: `docs/modeling-todo.md`

**Interfaces:**
- Consumes: all behavior from Tasks 1–3.
- Produces: an auditable TODO entry describing the completed recovery behavior.

- [ ] **Step 1: Run the focused front-end suite**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_assets -v
```

Expected: all `tests.test_web_assets` tests PASS.

- [ ] **Step 2: Run the broader web regression suite**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_api \
  tests.test_web_assets \
  tests.test_web_update_jobs \
  tests.test_web_performance_contract \
  -v
```

Expected: all tests PASS.

- [ ] **Step 3: Perform local browser regression**

Using the restarted service:

1. Open `http://127.0.0.1:5000/?ticker=GOOGL`.
2. Confirm the universe shows `194/194`.
3. Confirm GOOGL loads without an uncaught console error.
4. Open NBIS and confirm its chart, date lock, model output panel, and selected marker layers still work.
5. Run the `missing-top-risk-element` runtime case as the automated reproduction of a stale template.

- [ ] **Step 4: Update the global TODO**

Under the performance/reliability section of `docs/modeling-todo.md`, add a completed item recording:

```markdown
- [x] 为股票池和单股请求增加有限自动重试、局部手动恢复和成功状态保留；旧模板暂缺可选顶部风险徽章时不再中断仪表板初始化。
```

Do not modify unrelated task statuses.

- [ ] **Step 5: Commit documentation and final verification evidence**

```bash
git add docs/modeling-todo.md
git commit -m "docs: close dashboard request recovery"
```

- [ ] **Step 6: Verify the feature branch is clean**

Run:

```bash
git status --short --branch
git log -6 --oneline
```

Expected: clean `fix/dashboard-request-recovery` worktree with the design, plan, implementation, tests, and TODO closure commits.
