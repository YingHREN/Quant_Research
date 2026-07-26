# Platform Next Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add cache observability, extensible model-output registration, collision-safe chart markers, true cross-sectional RS Rating, and a reproducible TOPRISK comparison study.

**Architecture:** Online Flask requests expose only lightweight cache metadata and precomputed research snapshots. Presentation models use a Python registry with failure isolation, chart marker layout is a pure JavaScript transform, RS is generated offline into the research SQLite database, and TOPRISK evaluation remains an offline point-in-time report that cannot mutate production policy.

**Tech Stack:** Python 3.9, Flask, SQLite, pandas, NumPy, vanilla ES modules, Lightweight Charts 5.0.8, `unittest`, Node runtime harnesses.

## Global Constraints

- Do not expose filesystem paths, checksums, raw exception text, or credentials in web responses.
- Do not scan the 2.3-million-row research price table from `/api/universe`; RS must be precomputed.
- Do not modify raw research `daily_prices`.
- Do not change production risk thresholds or decision authority from evaluation results.
- All historical calculations must use only information available at the observation date.
- New chart decorations must not change price-axis autoscaling, time range, panning, zooming, or date locking.
- Every production behavior begins with a failing test.

---

### Task 1: Forecast Cache Status Contract and API

**Files:**
- Modify: `web/services/forecast_artifacts.py`
- Modify: `web/services/forecasts.py`
- Modify: `web/app.py`
- Test: `tests/test_web_forecast_artifacts.py`
- Test: `tests/test_web_api.py`

**Interfaces:**
- Produces: `ForecastArtifactStore.status() -> dict`
- Produces: `ForecastService.cache_status() -> dict`
- Produces: `GET /api/cache/status`

- [ ] **Step 1: Write failing artifact status tests**

Add tests asserting an empty store returns `state=empty`, a saved artifact returns its public model/version/created time/count/size, and malformed SQLite returns `state=unavailable` without a path.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```bash
../../venv/bin/python -m unittest \
  tests.test_web_forecast_artifacts.ForecastArtifactStoreTest.test_status_is_safe_and_reports_latest_entry \
  tests.test_web_forecast_artifacts.ForecastArtifactStoreTest.test_status_degrades_for_invalid_database -v
```

Expected: failure because `status` does not exist.

- [ ] **Step 3: Implement `ForecastArtifactStore.status()`**

Query only public metadata:

```python
{
    "state": "ready",
    "entry_count": 2,
    "latest_created_at": "...",
    "market_asof": "2026-07-24",
    "model_key": "...",
    "model_version": "...",
    "feature_version": "...",
    "risk_context_version": "...",
    "format_version": "...",
    "size_bytes": 1234,
}
```

Derive `market_asof` from the artifact coverage metadata during save by adding a nullable `market_asof` schema column with backward-compatible migration.

- [ ] **Step 4: Add failing service and API tests**

Assert memory hit, disk hit, rebuild lifecycle, and API safe degradation. Inject a fake service returning a known status and verify exact JSON.

- [ ] **Step 5: Implement service telemetry and API**

Track `last_access`, `build_started_at`, and `build_finished_at` under the existing service lock. Expose `cache_status()` and route it through `create_app`.

- [ ] **Step 6: Run focused tests and commit**

```bash
../../venv/bin/python -m unittest \
  tests.test_web_forecast_artifacts tests.test_web_api -v
git add web/services/forecast_artifacts.py web/services/forecasts.py web/app.py \
  tests/test_web_forecast_artifacts.py tests/test_web_api.py
git commit -m "feat: expose forecast cache status"
```

---

### Task 2: Cache Status UI

**Files:**
- Modify: `web/templates/index.html`
- Modify: `web/static/js/api.js`
- Modify: `web/static/js/app.js`
- Modify: `web/static/js/i18n.js`
- Modify: `web/static/css/dashboard.css`
- Modify: `tests/dashboard_runtime.mjs`
- Test: `tests/test_web_assets.py`

**Interfaces:**
- Consumes: `api.getCacheStatus()`
- Produces: `#forecast-cache-status` and `#forecast-cache-detail`

- [ ] **Step 1: Write failing DOM and runtime tests**

Require a collapsible cache status region, Chinese/English states, safe unavailable rendering, and refresh after update completion.

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
../../venv/bin/python -m unittest \
  tests.test_web_assets.WebAssetTest.test_page_has_forecast_cache_status \
  tests.test_web_assets.WebAssetTest.test_dashboard_renders_cache_status -v
```

- [ ] **Step 3: Implement minimal UI**

Add `api.getCacheStatus()`, a `renderCacheStatus(payload)` function, initial load after update-controller initialization, and reload from `refreshUniverseAfterUpdate`. Render `ready`, `rebuilding`, `empty`, and `unavailable` without changing chart layout.

- [ ] **Step 4: Run web asset tests and commit**

```bash
../../venv/bin/python -m unittest tests.test_web_assets -v
git add web/templates/index.html web/static/js/api.js web/static/js/app.js \
  web/static/js/i18n.js web/static/css/dashboard.css \
  tests/dashboard_runtime.mjs tests/test_web_assets.py
git commit -m "feat: show forecast cache status"
```

---

### Task 3: Model Output Registry

**Files:**
- Create: `web/forecasts/output_registry.py`
- Modify: `web/forecasts/model_outputs.py`
- Test: `tests/test_web_model_outputs.py`

**Interfaces:**
- Produces: `ModelOutputContext`
- Produces: `ModelOutputRegistration`
- Produces: `ModelOutputRegistry.register()`
- Produces: `default_model_output_registry()`
- Extends: `build_model_outputs(..., registry=None)`

- [ ] **Step 1: Write failing registry contract tests**

Test stable family/order output, duplicate-key rejection, invalid family rejection, immutable context copies, extension registration, and builder failure isolation.

- [ ] **Step 2: Run tests and confirm RED**

```bash
../../venv/bin/python -m unittest tests.test_web_model_outputs -v
```

- [ ] **Step 3: Implement registry primitives**

Use frozen dataclasses:

```python
@dataclass(frozen=True)
class ModelOutputRegistration:
    key: str
    family: str
    order: int
    builder: Callable[[ModelOutputContext], Mapping]
```

Registry output must validate the built key and convert a builder exception into a typed unavailable item with `unavailable_reason="model_output_error"`.

- [ ] **Step 4: Register existing builders without changing JSON**

Adapt private builders through small context lambdas. Keep the final `decision` object outside model registration because it is policy output, not a model.

- [ ] **Step 5: Run model/API tests and commit**

```bash
../../venv/bin/python -m unittest \
  tests.test_web_model_outputs tests.test_web_api -v
git add web/forecasts/output_registry.py web/forecasts/model_outputs.py \
  tests/test_web_model_outputs.py
git commit -m "refactor: register model output builders"
```

---

### Task 4: Collision-Safe Chart Markers

**Files:**
- Create: `web/static/js/marker_layout.js`
- Create: `tests/marker_layout_runtime.mjs`
- Modify: `web/static/js/charts.js`
- Test: `tests/test_web_assets.py`

**Interfaces:**
- Produces: `layoutChartMarkers(markers) -> markers`

- [ ] **Step 1: Write failing Node runtime**

Cover same-date/same-side merge, label deduplication, above/below separation, prediction-marker independence, stable priority style, unknown marker preservation, and input immutability.

- [ ] **Step 2: Run test and confirm RED**

```bash
node tests/marker_layout_runtime.mjs \
  "$(python -c 'from pathlib import Path; print(Path(\"web/static/js/marker_layout.js\").resolve().as_uri())')"
```

- [ ] **Step 3: Implement pure layout function**

Markers receive optional `priority` and `group`. Use `group="forecast"` to keep forecast markers separate. Remove layout-only fields before returning values to Lightweight Charts.

- [ ] **Step 4: Integrate after layer filtering**

Apply layout in `refreshMarkers()` after shape and forecast markers are combined. Assign explicit priorities at marker creation.

- [ ] **Step 5: Run chart tests and commit**

```bash
../../venv/bin/python -m unittest tests.test_web_assets -v
git add web/static/js/marker_layout.js web/static/js/charts.js \
  tests/marker_layout_runtime.mjs tests/test_web_assets.py
git commit -m "feat: merge overlapping chart markers"
```

---

### Task 5: Cross-Sectional RS Snapshot Builder

**Files:**
- Create: `research/relative_strength.py`
- Create: `build_research_rs.py`
- Create: `web/services/research_relative_strength.py`
- Create: `tests/test_relative_strength.py`
- Create: `tests/test_build_research_rs.py`
- Create: `tests/test_web_research_relative_strength.py`

**Interfaces:**
- Produces: `build_relative_strength_snapshot(prices, asof) -> DataFrame`
- Produces: `persist_relative_strength_snapshot(database, snapshot, model_version)`
- Produces: `ResearchRelativeStrengthService.build(tickers) -> dict`

- [ ] **Step 1: Write failing formula tests**

Use synthetic histories with known 63/126/189/252-session returns. Assert the 40/20/20/20 composite, same-day percentile order, `1..99` rating, and exclusion below 253 rows.

- [ ] **Step 2: Run formula tests and confirm RED**

```bash
../../venv/bin/python -m unittest tests.test_relative_strength -v
```

- [ ] **Step 3: Implement causal formula**

Require unique `(ticker,date)`, positive adjusted close, and no forward fill. Rating is:

```python
rank_pct = composite.rank(method="average", pct=True)
rs_rating = (1 + 98 * rank_pct).round().clip(1, 99).astype(int)
```

- [ ] **Step 4: Write failing persistence/CLI/service tests**

Test transactional replacement, schema metadata, safe missing table, ticker normalization, and no query of `daily_prices` in the web service.

- [ ] **Step 5: Implement builder, persistence and reader**

The CLI accepts `--database` and optional `--asof`, reads only the required 253-session window per ticker, writes one complete snapshot transactionally, and prints safe JSON summary.

- [ ] **Step 6: Run RS tests and commit**

```bash
../../venv/bin/python -m unittest \
  tests.test_relative_strength \
  tests.test_build_research_rs \
  tests.test_web_research_relative_strength -v
git add research/relative_strength.py build_research_rs.py \
  web/services/research_relative_strength.py \
  tests/test_relative_strength.py tests/test_build_research_rs.py \
  tests/test_web_research_relative_strength.py
git commit -m "feat: build cross-sectional RS snapshots"
```

---

### Task 6: RS Integration and UI

**Files:**
- Modify: `web/app.py`
- Modify: `web/services/universe.py`
- Modify: `web/templates/index.html`
- Modify: `web/static/js/universe.js`
- Modify: `web/static/js/app.js`
- Modify: `web/static/js/i18n.js`
- Modify: `tests/test_web_universe_service.py`
- Modify: `tests/test_web_api.py`
- Modify: `tests/test_web_assets.py`
- Modify: `tests/dashboard_runtime.mjs`

**Interfaces:**
- Adds universe row fields: `rs_rating`, `rs_asof`, `rs_sample_count`, `rs_model_version`
- Adds filters: `rs80`, `rs90`
- Adds sort key: `rs_rating`

- [ ] **Step 1: Write failing universe/API/UI tests**

Assert service merge, unavailable state without snapshots, RS sorting, mutually consistent RS filters, bilingual labels, and detail metadata.

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
../../venv/bin/python -m unittest \
  tests.test_web_universe_service tests.test_web_assets tests.test_web_api -v
```

- [ ] **Step 3: Wire service and payload**

Inject `ResearchRelativeStrengthService` beside classification service. Merge by ticker without replacing current `momentum_percentile`.

- [ ] **Step 4: Implement stock-pool UI**

Add RS badge, sort option, filters and selected-stock metadata. Label it “横截面 RS” / “Cross-sectional RS” and expose `cross_sectional_rs_v1`.

- [ ] **Step 5: Run web regressions and commit**

```bash
../../venv/bin/python -m unittest \
  tests.test_web_universe_service tests.test_web_assets tests.test_web_api -v
git add web/app.py web/services/universe.py web/templates/index.html \
  web/static/js/universe.js web/static/js/app.js web/static/js/i18n.js \
  tests/test_web_universe_service.py tests/test_web_api.py \
  tests/test_web_assets.py tests/dashboard_runtime.mjs
git commit -m "feat: expose cross-sectional RS in dashboard"
```

---

### Task 7: Unified TOPRISK Comparison Study

**Files:**
- Create: `research/evaluate_toprisk_comparison.py`
- Create: `run_toprisk_comparison.py`
- Create: `tests/test_evaluate_toprisk_comparison.py`
- Create: `tests/test_run_toprisk_comparison.py`

**Interfaces:**
- Produces: `build_comparison_frame(histories, forecasts=None, context=None)`
- Produces: `evaluate_signals(frame, horizons, adverse_threshold, groups)`
- Produces: JSON and Markdown report files

- [ ] **Step 1: Write failing metric and causality tests**

Synthetic cases must prove mature-tail exclusion, exact confusion metrics, first-signal lead time, signal definitions, group separation, and unchanged historical rows when future data is appended.

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
../../venv/bin/python -m unittest tests.test_evaluate_toprisk_comparison -v
```

- [ ] **Step 3: Implement comparison frame and metrics**

Reuse `build_forecast_risk_context`. Accept precomputed Ridge forecasts; when missing, mark Ridge-derived signals unavailable instead of fabricating them. Evaluate 5/10/20-session terminal return and MAE.

- [ ] **Step 4: Write failing CLI report test**

Use a temporary SQLite price database and injected small fixtures. Require deterministic JSON key order, Markdown tables, model versions, date coverage, group counts and limitations.

- [ ] **Step 5: Implement CLI**

Support:

```bash
../../venv/bin/python run_toprisk_comparison.py \
  --database data/prices.db \
  --output-json reports/toprisk-comparison.json \
  --output-markdown reports/toprisk-comparison.md
```

- [ ] **Step 6: Run study tests and commit**

```bash
../../venv/bin/python -m unittest \
  tests.test_evaluate_toprisk_comparison \
  tests.test_run_toprisk_comparison -v
git add research/evaluate_toprisk_comparison.py run_toprisk_comparison.py \
  tests/test_evaluate_toprisk_comparison.py tests/test_run_toprisk_comparison.py
git commit -m "feat: evaluate TOPRISK against risk baselines"
```

---

### Task 8: Real Data Build, Documentation, and Final Verification

**Files:**
- Modify: `docs/modeling-todo.md`
- Create: `reports/toprisk-comparison.md`
- Create: `reports/toprisk-comparison.json`

**Interfaces:**
- Consumes all Tasks 1–7.
- Produces auditable local RS snapshot and fixed-model TOPRISK report.

- [ ] **Step 1: Build real RS snapshot**

Run against the main research database after code verification:

```bash
../../venv/bin/python build_research_rs.py \
  --database /Users/renyinghao.1/Project/stock_screener/data/research_prices.db
```

Verify row count, date, sample size and SQLite integrity.

- [ ] **Step 2: Run real TOPRISK study**

```bash
../../venv/bin/python run_toprisk_comparison.py \
  --database /Users/renyinghao.1/Project/stock_screener/data/prices.db \
  --output-json reports/toprisk-comparison.json \
  --output-markdown reports/toprisk-comparison.md
```

If Ridge history is not available for all dates, the report must mark Ridge comparisons unavailable rather than infer them.

- [ ] **Step 3: Update global TODO honestly**

Mark only delivered engineering items complete. Keep model promotion/backtest tasks open unless the report meets predeclared thresholds across groups and regimes.

- [ ] **Step 4: Run complete verification**

```bash
git diff --check
../../venv/bin/python -m unittest discover -v
```

- [ ] **Step 5: Browser regression**

Start the feature branch on port 5001 and verify:

- cache state and details render;
- GOOGL and NBIS load;
- RS sort/filter works;
- model output order remains stable;
- merged markers do not affect chart range;
- console has no uncaught errors.

- [ ] **Step 6: Commit documentation and reports**

```bash
git add docs/modeling-todo.md reports/toprisk-comparison.md \
  reports/toprisk-comparison.json
git commit -m "docs: record RS and TOPRISK evaluation"
```
