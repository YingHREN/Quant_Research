# Forecast Correctness and Model Output UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify production forecasts on next-session-open executable labels and add a stable, point-in-time UI panel that exposes every production, research, and planned model output without confusing rule scores with probabilities.

**Architecture:** Keep the current `ForecastResult` API compatible while adding audited executable label metadata, richer evaluation evidence, and a model-output projection built by a pure backend assembler. Render the projection below the chart through a dedicated JavaScript module; chart markers and time-scale code do not consume the new panel contract.

**Tech Stack:** Python 3.9, pandas, NumPy, Flask JSON API, vanilla ES modules, CSS Grid, `unittest`.

## Global Constraints

- Observation-date features may use only information available by that session's close.
- Executable targets enter at the next trading session's open and exit at the 5th, 20th, or 60th future session's close.
- A training label is eligible only when its end date is strictly before the forecast date.
- Rule scores and shape states must never be labeled as probabilities.
- Raw Ridge output and the risk-adjusted decision must always remain separately visible.
- Historical locked dates must use same-date point-in-time model outputs.
- The model panel must not add chart time points, call chart auto-fit, or affect chart price scaling.
- Loading and unavailable states must reserve stable panel space.
- Existing untracked database WAL/SHM files and research files are user-owned and must not be modified or committed.

---

### Task 1: Executable production forecast labels

**Files:**
- Modify: `web/forecasts/dataset.py`
- Modify: `research/market_direction_model.py`
- Test: `tests/test_web_forecast_dataset.py`
- Test: `tests/test_market_direction_model.py`

**Interfaces:**
- Produces: `entry_date_column(horizon: int) -> str`
- Produces: feature frames containing raw `open` and `close` columns
- Produces: `target_return_{horizon}` computed as future close divided by next-session open minus one
- Consumes: existing `target_column`, `label_end_column`, and `SUPPORTED_HORIZONS`

- [x] **Step 1: Add failing executable-label tests**

Add a test using distinct open and close values:

```python
frame = build_feature_frame({"AAA": history})
attached = attach_forward_targets(frame, horizons=(5,))
aaa = attached.xs("AAA")
self.assertAlmostEqual(
    aaa["target_return_5"].iloc[2],
    history["Close"].iloc[7] / history["Open"].iloc[3] - 1.0,
)
self.assertEqual(aaa["label_entry_date_5"].iloc[2], history.index[3])
self.assertEqual(aaa["label_end_date_5"].iloc[2], history.index[7])
```

Also assert that the final `horizon` rows have no target/end date and the final row has no entry date.

- [x] **Step 2: Run the dataset tests and verify the old close-to-close assertion fails**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
./venv/bin/python -m unittest tests.test_web_forecast_dataset -q
```

Expected: failure because `open`, `label_entry_date_5`, and executable target semantics are absent.

- [x] **Step 3: Add raw open and executable dates to the canonical dataset**

Implement:

```python
def entry_date_column(horizon: int) -> str:
    return f"label_entry_date_{int(horizon)}"
```

Make `_ticker_features` emit `open` and `close`. In `attach_forward_targets`, compute:

```python
grouped_open = result["open"].groupby(level="ticker", sort=False)
grouped_close = result["close"].groupby(level="ticker", sort=False)
entry_open = grouped_open.shift(-1).replace(0.0, np.nan)
future_close = grouped_close.shift(-horizon)
result[target_name] = future_close / entry_open - 1.0
result[entry_name] = observation_dates.groupby(
    level="ticker", sort=False
).shift(-1)
result[end_name] = observation_dates.groupby(
    level="ticker", sort=False
).shift(-horizon)
```

Require both `open` and `close` in `_validate_feature_frame`. Validate ticker-local entry alignment at `-1` and end alignment at `-horizon`.

- [ ] **Step 4: Remove duplicate research label construction**

Change `research.market_direction_model.attach_next_open_targets` to derive its executable columns from canonical `target_return_*`, `label_entry_date_*`, and `label_end_date_*`, preserving its public output column names for existing studies.

- [x] **Step 5: Run forecast dataset, research direction, Ridge, and service tests**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
./venv/bin/python -m unittest \
  tests.test_web_forecast_dataset \
  tests.test_market_direction_model \
  tests.test_web_forecasts -q
```

Expected: all pass with executable targets.

- [x] **Step 6: Commit**

```bash
git add web/forecasts/dataset.py research/market_direction_model.py \
  tests/test_web_forecast_dataset.py tests/test_market_direction_model.py
git commit -m "fix: use executable next-open forecast targets"
```

### Task 2: Evaluation baselines and evidence status

**Files:**
- Modify: `web/forecasts/base.py`
- Modify: `web/forecasts/evaluation.py`
- Modify: `web/services/forecasts.py`
- Test: `tests/test_web_forecast_evaluation.py`
- Test: `tests/test_web_forecasts.py`

**Interfaces:**
- Extends: `ForecastEvaluation`
- Produces: `always_up_direction_accuracy`, `balanced_accuracy`, `macro_f1`, `non_overlapping_sample_count`, `non_overlapping_direction_accuracy`, and `evidence_status`
- Consumes: executable `target_return_*` from Task 1

- [x] **Step 1: Write failing metric-contract tests**

Construct three-class observations and assert:

```python
self.assertAlmostEqual(result.always_up_direction_accuracy, 1.0 / 3.0)
self.assertIsNotNone(result.balanced_accuracy)
self.assertIsNotNone(result.macro_f1)
self.assertGreater(result.non_overlapping_sample_count, 0)
self.assertIn(result.evidence_status, {"unproven", "proven"})
```

For unavailable evaluations assert `evidence_status == "not_precomputed"` and all performance metrics remain `None`.

- [x] **Step 2: Run evaluation tests and verify new fields are absent**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
./venv/bin/python -m unittest tests.test_web_forecast_evaluation -q
```

Expected: failing attribute and JSON contract assertions.

- [x] **Step 3: Extend and validate `ForecastEvaluation`**

Add optional finite metrics plus a required evidence status:

```python
EVIDENCE_STATUSES = frozenset(
    ("proven", "unproven", "insufficient", "not_precomputed")
)
```

Serialize every new field in `to_dict`. Available evaluations use `proven` only when the model beats configured return-error baselines and the always-up direction baseline; otherwise use `unproven`.

- [x] **Step 4: Compute always-up and non-overlapping evidence**

In `walk_forward_evaluate`, store actual/predicted directions and calculate:

```python
always_up_accuracy = float(np.mean(evaluated["actual_direction"] == "up"))
```

For non-overlapping evidence, within each ticker keep rows whose observation-session ordinal is spaced by at least `horizon`; calculate its sample count and direction accuracy without changing the primary overlapping metrics.

- [x] **Step 5: Update typed unavailable evaluations**

Set `evidence_status="not_precomputed"` in service fallbacks. Use `insufficient` for evaluated datasets that lack enough usable predictions.

- [x] **Step 6: Run evaluation and service tests**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
./venv/bin/python -m unittest \
  tests.test_web_forecast_evaluation \
  tests.test_web_forecasts \
  tests.test_web_api -q
```

Expected: all pass and JSON values are finite or `null`.

- [x] **Step 7: Commit**

```bash
git add web/forecasts/base.py web/forecasts/evaluation.py \
  web/services/forecasts.py tests/test_web_forecast_evaluation.py \
  tests/test_web_forecasts.py
git commit -m "feat: expose forecast baseline evidence"
```

### Task 3: Unified model-output backend contract

**Files:**
- Create: `web/forecasts/model_outputs.py`
- Modify: `web/app.py`
- Test: `tests/test_web_model_outputs.py`
- Test: `tests/test_web_api.py`

**Interfaces:**
- Produces: `build_model_outputs(forecast, chart_row, evaluation) -> dict`
- Adds: `model_outputs` to each available `forecasts.by_date[date][horizon]`
- Consumes: serialized forecast, same-date chart row, and horizon evaluation

- [x] **Step 1: Write failing pure-contract tests**

Use a serialized forecast with a decision and a chart row containing structure fields. Assert exact groups:

```python
outputs = build_model_outputs(forecast, chart_row, evaluation)
self.assertEqual(
    set(outputs),
    {"primary", "downside", "bullish_structure", "decision"},
)
self.assertEqual(outputs["primary"][0]["kind"], "statistical_forecast")
self.assertEqual(outputs["downside"][0]["kind"], "rule_score")
self.assertEqual(outputs["decision"]["kind"], "decision_policy")
self.assertNotIn("probability", outputs["downside"][0])
```

Assert planned demand, macro, and intraday entries have `lifecycle="planned"`, `status="unavailable"`, and no fabricated score.

- [x] **Step 2: Run the new tests and verify the module is missing**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
./venv/bin/python -m unittest tests.test_web_model_outputs -q
```

Expected: import failure.

- [x] **Step 3: Implement a pure, JSON-safe assembler**

Define model entries with these required keys:

```python
{
    "key": "ridge_direction_v1",
    "version": "v4",
    "kind": "statistical_forecast",
    "lifecycle": "production",
    "status": "available",
    "timing": "next_session_open",
    "explanation_key": "model.ridge.explanation",
    "limitation_key": "model.ridge.limitation",
}
```

Build separate entries for immediate eight-condition risk, individual remembered risk, group stress, slow decline, structural strengthening, early bullish reversal, strict VCP, tight platform, and final policy. Preserve actual conditions and memory age.

- [x] **Step 4: Attach outputs point-in-time in both stock endpoints**

Add an app helper:

```python
def _attach_model_outputs(forecast_payload, chart):
    rows = {row["time"]: row for row in chart}
    evaluations = forecast_payload.get("forecast_evaluation", {})
    for date, horizons in forecast_payload["forecasts"]["by_date"].items():
        for horizon, forecast in horizons.items():
            forecast["model_outputs"] = build_model_outputs(
                forecast,
                rows.get(date, {}),
                evaluations.get(str(horizon), {}),
            )
```

Call it after target dates are attached for both full stock and historical-forecast responses.

- [x] **Step 5: Run contract and API tests**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
./venv/bin/python -m unittest \
  tests.test_web_model_outputs tests.test_web_api -q
```

Expected: each forecast row has complete, same-date model groups.

- [x] **Step 6: Commit**

```bash
git add web/forecasts/model_outputs.py web/app.py \
  tests/test_web_model_outputs.py tests/test_web_api.py
git commit -m "feat: add unified model output contract"
```

### Task 4: Dedicated model decision panel

**Files:**
- Create: `web/static/js/model_outputs.js`
- Modify: `web/static/js/charts.js`
- Modify: `web/templates/index.html`
- Modify: `web/static/css/dashboard.css`
- Create: `tests/model_outputs_runtime.mjs`
- Modify: `tests/test_web_assets.py`

**Interfaces:**
- Produces: `renderModelOutputs(container, options)`
- Consumes: `forecast.model_outputs`, selected date, locale, request state
- Does not consume or mutate chart/time-scale objects

- [x] **Step 1: Add failing JavaScript rendering tests**

Build a minimal DOM fixture and assert:

```javascript
renderModelOutputs(container, { forecast, date: "2026-07-01", locale: "zh-CN" });
assert.equal(container.querySelectorAll("[data-model-card]").length >= 8, true);
assert.match(container.textContent, /Ridge/);
assert.match(container.textContent, /规则分数，不是概率/);
assert.match(container.textContent, /最终方向/);
```

Also render `requestState: "loading"` and assert the stable shell remains.

- [x] **Step 2: Run JS tests and verify the renderer is missing**

Run:

```bash
node --test tests/model_outputs_runtime.mjs
```

Expected: module import failure.

- [x] **Step 3: Add semantic panel markup**

Insert below `crosshair-detail`:

```html
<section id="model-output-panel" class="model-output-panel"
         aria-labelledby="model-output-title" aria-live="polite">
  <h3 id="model-output-title" data-i18n="modelOutput.title">模型决策面板</h3>
  <div id="model-output-content" class="model-output-shell"></div>
</section>
```

The shell reserves a minimum height and uses four responsive groups. Cards use `<details>` for secondary evidence; final decision and abnormal risk cards are open by default.

- [x] **Step 4: Implement the renderer**

Render cards from the contract without hard-coding financial calculations. Choose labels from `kind`, `lifecycle`, and translation keys. For `rule_score`, always append the rule-score disclaimer. Render actual conditions, threshold fields when supplied, unavailable reasons, versions, and timing.

- [x] **Step 5: Wire selected-date updates without chart mutation**

Have `charts.js` call the renderer from the existing selected-date render path. Do not call `timeScale().fitContent`, `setVisibleLogicalRange`, `applyOptions` on price scales, or add model-output series.

- [x] **Step 6: Run JS and template tests**

Run:

```bash
node --test tests/model_outputs_runtime.mjs tests/dashboard_runtime.mjs
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
./venv/bin/python -m unittest tests.test_web_assets -q
```

Expected: panel markup, cards, loading shell, and existing chart behavior pass.

- [x] **Step 7: Commit**

```bash
git add web/static/js/model_outputs.js web/static/js/charts.js \
  web/templates/index.html web/static/css/dashboard.css \
  tests/model_outputs_runtime.mjs tests/test_web_assets.py
git commit -m "feat: render model decision panel"
```

### Task 5: Chinese and English model explanations

**Files:**
- Modify: `web/static/js/i18n.js`
- Modify: `tests/model_outputs_runtime.mjs`
- Modify: `tests/test_web_assets.py`

**Interfaces:**
- Produces: complete `modelOutput.*` and `model.*` keys in `zh-CN` and `en`
- Consumes: backend `explanation_key` and `limitation_key`

- [x] **Step 1: Add failing locale-completeness tests**

Assert both locales include model names, kinds, lifecycles, timing, explanations, limitations, unavailable reasons, and the rule-score disclaimer.

- [x] **Step 2: Run i18n tests and verify keys are missing**

Run:

```bash
node --test tests/model_outputs_runtime.mjs
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
./venv/bin/python -m unittest tests.test_web_assets -q
```

Expected: missing-key assertions fail.

- [x] **Step 3: Add concise bilingual copy**

Chinese examples:

```javascript
"model.ridge.explanation": "用点时趋势、动量、量价和市场环境估计目标周期收益。",
"model.ridge.limitation": "当前样本外优势尚未稳定证明，原始方向仅供研究。",
"modelOutput.ruleDisclaimer": "规则分数，不是概率。",
"modelOutput.lifecycle.not_promoted": "研究完成，未晋级",
```

English copy must carry the same semantics and must not use “probability” for rule outputs.

- [x] **Step 4: Run locale and renderer tests**

Run:

```bash
node --test tests/model_outputs_runtime.mjs
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
./venv/bin/python -m unittest tests.test_web_assets -q
```

Expected: both locales pass without fallback keys.

- [x] **Step 5: Commit**

```bash
git add web/static/js/i18n.js tests/model_outputs_runtime.mjs \
  tests/test_web_assets.py
git commit -m "feat: localize model output explanations"
```

### Task 6: Date locking and layout stability regression coverage

**Files:**
- Modify: `tests/dashboard_runtime.mjs`
- Modify: `tests/model_outputs_runtime.mjs`
- Modify: `tests/test_web_assets.py`
- Modify: `web/static/js/charts.js` only if a failing regression requires it
- Modify: `web/static/css/dashboard.css` only if a failing layout assertion requires it

**Interfaces:**
- Verifies: locked date is the sole source for the model panel until explicit unlock/relock
- Verifies: panel rendering cannot mutate the chart range or series

- [x] **Step 1: Add lock-state regression tests**

Simulate:

1. hover 2026-06-26;
2. click-lock 2026-06-26;
3. crosshair move to 2026-07-01;
4. pointer leaves chart;
5. page scroll occurs.

Assert the panel date remains `2026-06-26`. Then explicitly unlock and assert hover can update it.

- [x] **Step 2: Add chart-mutation spies**

Spy on `fitContent`, `setVisibleLogicalRange`, price-scale option changes, and series creation. Render loading, available, and unavailable model panels; assert no spy is called.

- [x] **Step 3: Run the regression tests**

Run:

```bash
node --test tests/dashboard_runtime.mjs tests/model_outputs_runtime.mjs
```

Expected: pass, or expose a concrete lock/layout bug before implementation changes.

- [x] **Step 4: Confirm the dedicated panel requires no additional chart lock/layout mutation**

Keep `lockedDate` authoritative in the existing selection state. Pass resolved date and forecast to the renderer; never let the renderer subscribe directly to pointer events.

- [x] **Step 5: Re-run all frontend tests**

Run:

```bash
node --test tests/dashboard_runtime.mjs tests/model_outputs_runtime.mjs
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
./venv/bin/python -m unittest tests.test_web_assets -q
```

Expected: all frontend tests pass.

- [x] **Step 6: Commit**

```bash
git add tests/dashboard_runtime.mjs tests/model_outputs_runtime.mjs \
  tests/test_web_assets.py web/static/js/charts.js web/static/css/dashboard.css
git commit -m "test: protect model panel date and layout"
```

### Task 7: Full verification, browser QA, and TODO synchronization

**Files:**
- Modify: `docs/modeling-todo.md`
- Modify: `docs/superpowers/plans/2026-07-26-forecast-correctness-model-output-ui.md`
- Test: full Python and JavaScript suites

**Interfaces:**
- Verifies the complete first-stage deliverable
- Produces final checked TODO items only for requirements proven by tests and manual QA

- [x] **Step 1: Run the complete automated suite**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
./venv/bin/python -m unittest discover -s tests -q
node --test tests/dashboard_runtime.mjs tests/model_outputs_runtime.mjs
```

Expected: all tests pass.

- [x] **Step 2: Start or restart the local service**

Run the repository's documented local server command and verify `/api/stocks/NBIS` returns `model_outputs` for available forecast dates.

- [ ] **Step 3: Perform browser QA**

Check NBIS and MU on:

- latest date;
- NBIS 2026-07-01;
- NBIS 2026-07-17;
- one unavailable historical forecast date.

Verify bilingual model cards, raw/final direction separation, rule disclaimers, fixed loading height, click lock, scroll stability, unlock behavior, and unchanged chart range.

- [ ] **Step 4: Update TODO and plan checkboxes honestly**

Mark only implemented and verified `FCAST-001`/`UI-002` items complete. Leave baseline proof and model promotion unchecked unless the new walk-forward evidence actually passes the specified gate.

- [ ] **Step 5: Run diff and repository checks**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; user-owned WAL/SHM and research files remain untracked and untouched.

- [ ] **Step 6: Commit documentation state**

```bash
git add docs/modeling-todo.md \
  docs/superpowers/plans/2026-07-26-forecast-correctness-model-output-ui.md
git commit -m "docs: record model panel verification"
```
