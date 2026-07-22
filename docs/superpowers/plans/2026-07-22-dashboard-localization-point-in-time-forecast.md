# Dashboard Localization and Point-in-Time Forecast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver deterministic chart dates, a persistent Chinese/English interface, accessible factor explanations, and point-in-time-safe 5/20/60-session direction forecasts with walk-forward evidence.

**Architecture:** Keep localization in a small client module consumed by existing renderers. Add a backend forecast provider with explicit contracts, pure feature/label builders, an expanding-window ridge model implemented with NumPy, and revision-keyed caching; the stock endpoint returns sparse date-indexed forecasts so crosshair interaction stays local and fast.

**Tech Stack:** Python 3.9, Flask, pandas, NumPy, SQLite, browser ES modules, Lightweight Charts 5.0.8, `unittest`, Node syntax/DOM harness tests.

## Global Constraints

- Default locale is `zh-CN`; supported locales are exactly `zh-CN` and `en`.
- Chart ticks use `MM-DD`; full dates use `YYYY-MM-DD`, independent of browser locale.
- Forecast horizons are exactly 5, 20, and 60 sessions; 20 is the default.
- A forecast at date `t` may use features through `t`, but training labels must be fully observable before `t`.
- Never emit probability unless out-of-sample calibration and its minimum sample threshold succeed.
- Preserve ticker symbols and VCP, EMA20, SMA50, SMA200, ATR20, and OHLCV abbreviations.
- Forecast failures must not prevent price, factor, structure, or scenario rendering.
- Do not add live trading, target-price recommendations, or profitability claims.

---

### Task 1: Deterministic Date Formatting and Locale Store

**Files:**
- Create: `web/static/js/i18n.js`
- Modify: `web/static/js/store.js`
- Test: `tests/test_web_assets.py`

**Interfaces:**
- Produces: `SUPPORTED_LOCALES`, `getLocale()`, `setLocale(locale)`, `subscribeLocale(listener)`, `t(key, params, locale)`, `formatChartTickDate(time)`, `formatFullDate(value)`.
- Persists: local-storage key `quant-dashboard-locale`.

- [ ] **Step 1: Write failing tests for locale fallback, persistence, interpolation, and dates**

Add a Node harness test that imports `i18n.js`, stubs local storage, and asserts:

```javascript
assert.equal(i18n.getLocale(), "zh-CN");
assert.equal(i18n.setLocale("en"), "en");
assert.equal(storage.getItem("quant-dashboard-locale"), "en");
assert.equal(i18n.setLocale("fr"), "zh-CN");
assert.equal(i18n.formatChartTickDate("2026-07-17"), "07-17");
assert.equal(i18n.formatFullDate({ year: 2026, month: 7, day: 17 }), "2026-07-17");
assert.equal(i18n.t("universe.shown", { shown: 2, total: 3 }, "zh-CN"), "显示 2/3 只股票");
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `./venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_i18n_module -v`

Expected: FAIL because `web/static/js/i18n.js` does not exist.

- [ ] **Step 3: Implement the locale module**

Implement validated locale state, subscriber notification, English fallback, parameter replacement, and manual ISO parsing. Date functions must construct strings from date parts and must not call locale-dependent `toLocaleDateString`.

```javascript
export function formatChartTickDate(value) {
  const part = dateParts(value);
  return part ? `${pad(part.month)}-${pad(part.day)}` : "—";
}
export function formatFullDate(value) {
  const part = dateParts(value);
  return part ? `${part.year}-${pad(part.month)}-${pad(part.day)}` : "—";
}
```

- [ ] **Step 4: Run focused and asset tests**

Run: `./venv/bin/python -m unittest tests.test_web_assets -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/static/js/i18n.js web/static/js/store.js tests/test_web_assets.py
git commit -m "feat: add deterministic dashboard localization core"
```

### Task 2: Complete Chinese/English Interface Switching

**Files:**
- Modify: `web/templates/index.html`
- Modify: `web/static/js/i18n.js`
- Modify: `web/static/js/app.js`
- Modify: `web/static/js/universe.js`
- Modify: `web/static/js/update.js`
- Modify: `web/static/js/scenarios.js`
- Modify: `web/static/css/dashboard.css`
- Test: `tests/test_web_assets.py`

**Interfaces:**
- Consumes: Task 1 localization exports.
- Produces: `applyDocumentLocale(root, locale)` and header buttons with `[data-locale]`.

- [ ] **Step 1: Add failing template and runtime localization tests**

Assert that the template has a two-button language selector, `aria-pressed`, localized keys for every visible static label, and that switching locale updates `document.documentElement.lang`, dynamic status text, range labels, and chart accessibility labels without reload.

- [ ] **Step 2: Verify RED**

Run: `./venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_bilingual_dashboard -v`

Expected: FAIL because the language control and translations are absent.

- [ ] **Step 3: Add complete catalogs and wire all renderers**

Use stable keys such as `header.latestDate`, `universe.filters.strictVcp`, `security.state.stale`, `chart.locked`, `factor.title`, `scenario.disclaimer`, and `update.state.rateLimited`. Replace embedded user-facing strings with `t(...)`; do not translate server payload identifiers before comparisons.

- [ ] **Step 4: Style and verify keyboard/mobile behavior**

Keep the locale control usable at 390px width, expose visible focus, and update `aria-pressed` on each switch.

Run: `./venv/bin/python -m unittest tests.test_web_assets -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/templates/index.html web/static/js/i18n.js web/static/js/app.js web/static/js/universe.js web/static/js/update.js web/static/js/scenarios.js web/static/css/dashboard.css tests/test_web_assets.py
git commit -m "feat: add persistent bilingual dashboard"
```

### Task 3: Fix Linked-Chart Dates and Localize Chart Details

**Files:**
- Modify: `web/static/js/charts.js`
- Test: `tests/test_web_assets.py`

**Interfaces:**
- Consumes: `formatChartTickDate`, `formatFullDate`, `t`, and locale subscriptions.
- Produces: `createLinkedCharts(...).setLocale(locale)` and locale-correct detail labels.

- [ ] **Step 1: Write a failing chart-options regression test**

Assert both price and volume charts receive:

```javascript
timeScale: { tickMarkFormatter: i18n.formatChartTickDate }
localization: { timeFormatter: i18n.formatFullDate }
```

Also assert the detail heading renders `2026-07-17`, never a browser-generated mixed-language date.

- [ ] **Step 2: Verify RED**

Run: `./venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_chart_dates_are_deterministic -v`

Expected: FAIL because chart options currently omit explicit formatters.

- [ ] **Step 3: Apply explicit formatters and localized series/detail labels**

Pass both formatters to `chartOptions`, replace `Open`, `Volume ratio change`, lock instructions, empty text, and series titles through `t`, and re-apply options when locale changes.

- [ ] **Step 4: Run asset tests**

Run: `./venv/bin/python -m unittest tests.test_web_assets -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/static/js/charts.js tests/test_web_assets.py
git commit -m "fix: make chart dates deterministic"
```

### Task 4: Localized Factor Metadata and Accessible Explanations

**Files:**
- Modify: `web/factors/base.py`
- Modify: `web/factors/builtin.py`
- Modify: `web/factors/registry.py`
- Modify: `web/static/js/factors.js`
- Modify: `web/static/css/dashboard.css`
- Test: `tests/test_web_factors.py`
- Test: `tests/test_web_assets.py`

**Interfaces:**
- Extends factor/group JSON with optional `i18n: {"zh-CN": {"label", "description", "methodology", "window", "direction"}}`.
- Produces: factor info buttons and one reusable `.factor-popover`.

- [ ] **Step 1: Write failing backend metadata tests**

Create a built-in factor and assert English compatibility fields remain unchanged while `i18n["zh-CN"]` contains all five explanation fields. Assert every built-in factor and group has complete Chinese metadata.

- [ ] **Step 2: Verify backend RED**

Run: `./venv/bin/python -m unittest tests.test_web_factors -v`

Expected: FAIL because factor results have no `i18n` metadata.

- [ ] **Step 3: Extend metadata contracts minimally**

Add immutable optional localization mappings to factor definitions/results/groups and serialize them through `json_safe`; populate all built-in factor and group translations.

- [ ] **Step 4: Write and verify failing interaction tests**

Test hover/focus/click/Enter/Space open behavior, `Escape` and outside-click close behavior, one-open-at-a-time behavior, `aria-expanded`, `aria-controls`, current value/date/version, and missing reason.

- [ ] **Step 5: Implement the reusable popover and localized factor rendering**

Use a real `<button type="button" class="factor-info">ⓘ</button>`, one body-level popover, safe `textContent`, viewport-aware placement, and cleanup when factors rerender.

- [ ] **Step 6: Run factor and asset suites**

Run: `./venv/bin/python -m unittest tests.test_web_factors tests.test_web_assets -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add web/factors web/static/js/factors.js web/static/css/dashboard.css tests/test_web_factors.py tests/test_web_assets.py
git commit -m "feat: explain factors in Chinese and English"
```

### Task 5: Forecast Contracts and Point-in-Time Dataset Builder

**Files:**
- Create: `web/forecasts/__init__.py`
- Create: `web/forecasts/base.py`
- Create: `web/forecasts/dataset.py`
- Test: `tests/test_web_forecast_dataset.py`

**Interfaces:**
- Produces: `ForecastResult.to_dict()`, `ForecastEvaluation.to_dict()`, `build_feature_frame(histories)`, `attach_forward_targets(frame, horizons=(5,20,60))`, and `eligible_training_rows(frame, asof, horizon)`.

- [ ] **Step 1: Write failing target-alignment and leakage tests**

Use synthetic closes `100, 101, ...` for two tickers. Assert a 5-session target at index `i` is `close[i+5] / close[i] - 1`; a training row for forecast date `t` is included only when its label-end date is strictly before `t`. Add a future-data trap whose post-`t` price spike cannot alter features at `t`.

- [ ] **Step 2: Verify RED**

Run: `./venv/bin/python -m unittest tests.test_web_forecast_dataset -v`

Expected: FAIL because the forecast package is absent.

- [ ] **Step 3: Implement immutable contracts and pure builders**

Use the registered factor formulas or shared indicator primitives, with columns keyed by `(ticker, observation_date)`. Store `label_end_date_{horizon}` alongside each target so eligibility is asserted directly.

- [ ] **Step 4: Add sparse-history, NaN, duplicate-key, and 5/20/60 tests**

Typed unavailable reasons must include `insufficient_history`, `insufficient_training_samples`, `degenerate_target`, and `model_error`.

- [ ] **Step 5: Run focused tests and commit**

Run: `./venv/bin/python -m unittest tests.test_web_forecast_dataset -v`

```bash
git add web/forecasts tests/test_web_forecast_dataset.py
git commit -m "feat: build point-in-time forecast datasets"
```

### Task 6: Expanding-Window Ridge Forecast Provider

**Files:**
- Create: `web/forecasts/ridge.py`
- Create: `web/forecasts/registry.py`
- Test: `tests/test_web_forecasts.py`

**Interfaces:**
- Produces: `RidgeForecastProvider.forecast_series(ticker, dates, horizons)`, `ForecastRegistry.register(provider)`, and model key `ridge_direction_v1`.
- Model output follows the Task 5 `ForecastResult` contract.

- [ ] **Step 1: Write failing deterministic model tests**

Build synthetic cross-sectional data with a known linear relationship. Assert deterministic predicted return, sign classification, expanding cutoff, minimum sample rejection, singular-matrix stability, and no forecast-row inclusion in training.

- [ ] **Step 2: Verify RED**

Run: `./venv/bin/python -m unittest tests.test_web_forecasts -v`

Expected: FAIL because the ridge provider is absent.

- [ ] **Step 3: Implement standardization, median imputation, and ridge fitting with NumPy**

Fit preprocessing only on eligible training rows. Solve `(X.T @ X + alpha * I) beta = X.T @ y`, leave the intercept unpenalized, and derive directions from versioned neutral bands: 5-day ±1%, 20-day ±2%, 60-day ±4%.

- [ ] **Step 4: Add honest confidence handling**

Return `up_probability: None` and `confidence_status: "uncalibrated"` until Task 7 supplies sufficient out-of-sample calibration. Never transform the raw prediction into a probability.

- [ ] **Step 5: Run focused tests and commit**

Run: `./venv/bin/python -m unittest tests.test_web_forecasts -v`

```bash
git add web/forecasts/ridge.py web/forecasts/registry.py tests/test_web_forecasts.py
git commit -m "feat: add expanding-window direction regression"
```

### Task 7: Walk-Forward Evaluation and Calibration Gate

**Files:**
- Create: `web/forecasts/evaluation.py`
- Modify: `web/forecasts/ridge.py`
- Test: `tests/test_web_forecast_evaluation.py`

**Interfaces:**
- Produces: `walk_forward_evaluate(frame, horizon, provider)` and `calibrate_up_probability(predictions, actuals, minimum_samples=100)`.

- [ ] **Step 1: Write failing evaluation tests**

Assert MAE, RMSE, three-class direction accuracy, coverage, zero-return baseline, historical-mean baseline, signal-bucket returns, and rank IC. Verify IC and buckets are unavailable below their cross-sectional thresholds.

- [ ] **Step 2: Verify RED**

Run: `./venv/bin/python -m unittest tests.test_web_forecast_evaluation -v`

Expected: FAIL because evaluation functions are absent.

- [ ] **Step 3: Implement expanding-window evaluation**

Each evaluation prediction must call the same eligibility boundary as live forecasts. Record evaluation start/end, model version, sample count, and explicit unavailable metrics.

- [ ] **Step 4: Implement calibration gate**

Fit monotonic empirical calibration exclusively from earlier out-of-sample predictions. Emit an up probability only with at least 100 calibration rows containing both up and non-up outcomes; otherwise preserve `None` and the reason.

- [ ] **Step 5: Run tests and commit**

Run: `./venv/bin/python -m unittest tests.test_web_forecast_evaluation tests.test_web_forecasts -v`

```bash
git add web/forecasts tests/test_web_forecast_evaluation.py tests/test_web_forecasts.py
git commit -m "feat: evaluate forecasts with walk-forward tests"
```

### Task 8: Forecast Service, Cache, and Stock API Integration

**Files:**
- Create: `web/services/forecasts.py`
- Modify: `web/app.py`
- Modify: `web/services/update_jobs.py`
- Test: `tests/test_web_api.py`
- Test: `tests/test_web_update_jobs.py`

**Interfaces:**
- Produces stock payload keys `forecasts: {model, horizons, by_date}` and `forecast_evaluation`.
- Cache key: `(database_revision, ticker, first_chart_date, last_chart_date, model_version)`.

- [ ] **Step 1: Write failing API isolation and schema tests**

Assert all horizons and contract fields serialize, chart dates map to forecast dates, a provider exception returns the rest of the stock payload plus typed forecast unavailability, and legacy clients still receive existing keys unchanged.

- [ ] **Step 2: Verify RED**

Run: `./venv/bin/python -m unittest tests.test_web_api -v`

Expected: FAIL because the stock endpoint omits forecasts.

- [ ] **Step 3: Implement lazy cached forecast service**

Compute one stock/date-range bundle per database revision, cap cache size, and return sparse dates. Use dependency injection through Flask config for deterministic tests.

- [ ] **Step 4: Invalidate after successful data update**

Connect update completion to `forecast_service.invalidate()`. A failed or rate-limited update must not discard a valid cache.

- [ ] **Step 5: Run API/update tests and commit**

Run: `./venv/bin/python -m unittest tests.test_web_api tests.test_web_update_jobs -v`

```bash
git add web/app.py web/services/forecasts.py web/services/update_jobs.py tests/test_web_api.py tests/test_web_update_jobs.py
git commit -m "feat: expose cached point-in-time forecasts"
```

### Task 9: Crosshair Forecast Direction and Horizon Controls

**Files:**
- Modify: `web/templates/index.html`
- Create: `web/static/js/forecasts.js`
- Modify: `web/static/js/charts.js`
- Modify: `web/static/js/app.js`
- Modify: `web/static/css/dashboard.css`
- Test: `tests/test_web_assets.py`

**Interfaces:**
- Produces: `indexForecasts(payload)`, `forecastFor(date, horizon)`, localized forecast detail panel, and `chartController.setForecasts(payload)` / `setForecastHorizon(horizon)`.

- [ ] **Step 1: Write failing interaction tests**

Assert default 20, switches for 5/20/60, crosshair date lookup, up/neutral/down marker placement, click-lock synchronization, localized probability/sample/cutoff/model labels, and `暂不可评估` for absent data.

- [ ] **Step 2: Verify RED**

Run: `./venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_chart_forecast_interaction -v`

Expected: FAIL because forecast UI does not exist.

- [ ] **Step 3: Implement local forecast indexing and chart overlay**

Index once when a ticker loads. Reuse one series-marker primitive; replace its marker on crosshair changes rather than accumulating markers. Use green up-arrow, gray circle, and red down-arrow, with accessible text that does not rely on color.

- [ ] **Step 4: Render evaluation evidence beside the signal**

Show coverage, direction accuracy, MAE, baseline comparison, sample period, and model version. Label all values as walk-forward historical evidence and keep the research disclaimer visible.

- [ ] **Step 5: Run asset tests and commit**

Run: `./venv/bin/python -m unittest tests.test_web_assets -v`

```bash
git add web/templates/index.html web/static/js/forecasts.js web/static/js/charts.js web/static/js/app.js web/static/css/dashboard.css tests/test_web_assets.py
git commit -m "feat: show point-in-time direction on chart hover"
```

### Task 10: Documentation, Performance, and Full Verification

**Files:**
- Modify: `docs/dashboard.md`
- Modify: `docs/research/vcp-integration-decision-v1.md`
- Test: all tests

**Interfaces:**
- Documents localization keys, factor translation extension, forecast provider registration, model limitations, leakage rules, cache invalidation, and interpretation of evaluation metrics.

- [ ] **Step 1: Add documentation assertions and update docs**

Document how to add a translated factor and a new forecast provider, the exact 5/20/60 targets and neutral bands, why probability may be absent, and how to reproduce walk-forward evaluation.

- [ ] **Step 2: Run full automated verification**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -W error -m unittest discover -s tests
for file in web/static/js/*.js; do node --check "$file"; done
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m py_compile web/app.py web/contracts.py web/services/*.py web/factors/*.py web/forecasts/*.py
git diff --check
```

Expected: all tests PASS, all JavaScript parses, Python compilation succeeds, and `git diff --check` is silent.

- [ ] **Step 3: Run browser verification at desktop and 390x844**

Verify Chinese default, English switch/persistence, `07-17` chart tick, ISO detail date, factor popover pointer/keyboard behavior, all three forecast horizons, crosshair hover and click-lock, unavailable state, no horizontal overflow, and no console errors.

- [ ] **Step 4: Measure endpoint behavior**

Load the same stock twice and confirm the second response uses the revision cache. Record response time and payload size in the implementation report; if the uncached route exceeds five seconds on the local database, profile before completion.

- [ ] **Step 5: Commit**

```bash
git add docs/dashboard.md docs/research/vcp-integration-decision-v1.md tests
git commit -m "docs: explain localized forecast workstation"
```

- [ ] **Step 6: Request code review and apply verification-before-completion**

Review specifically for future-data leakage, probability claims, translation coverage, keyboard accessibility, cache invalidation, and chart marker lifecycle. Re-run the full commands from Step 2 after every review fix.
