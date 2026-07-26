# Causal Supply and Demand Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build independent causal daily supply-pressure and demand-confirmation scores, attach them to historical chart dates, and render both as advisory model outputs.

**Architecture:** A pure research module computes point-in-time evidence, capped group scores, coverage, and a combined state. A thin web service aligns the model rows to chart rows and resolves the optional QQQ and thematic benchmark context. The existing model-output registry presents those chart fields without modifying Ridge values, forecast decisions, persistent-risk artifacts, or chart series.

**Tech Stack:** Python 3.9, pandas, NumPy, Flask API contracts, vanilla JavaScript, `unittest`, Node runtime tests.

## Global Constraints

- Every rolling prior baseline and resistance level must be shifted before use.
- Outputs have `close_confirmed` timing and `advisory` decision permission.
- Scores are rule scores from 0 through 100, never probabilities.
- Daily OHLCV proxies must not claim verified institutional or active order flow.
- Supply and demand are independently scored and are not arithmetic opposites.
- Missing QQQ or sector context lowers coverage instead of becoming zero evidence.
- No new Lightweight Charts series, price line, marker, or future time point.
- Every production behavior starts with a failing test and completes a red-green-refactor cycle.

---

### Task 1: Pure causal supply and demand model

**Files:**
- Create: `research/supply_demand.py`
- Create: `tests/test_supply_demand.py`

**Interfaces:**
- Consumes: `research.market_pressure.build_pressure_rows(history) -> pd.DataFrame`
- Produces: `build_supply_demand_rows(history, *, qqq_close=None, sector_close=None) -> pd.DataFrame`
- Produces stable output fields specified in `docs/superpowers/specs/2026-07-26-causal-supply-demand-model-design.md`

- [ ] **Step 1: Write failing tests for independent capped scores**

Create synthetic 40-session OHLCV fixtures and assert that a final session satisfying `distribution_day`, `negative_signed_volume`, and `high_volume_non_progress` produces all three condition keys but:

```python
row = build_supply_demand_rows(frame).iloc[-1]
self.assertLessEqual(row["supply_close_volume_score"], 40.0)
self.assertGreater(row["supply_pressure_score"], 0.0)
self.assertNotEqual(
    row["demand_confirmation_score"],
    100.0 - row["supply_pressure_score"],
)
```

Name the production break caught by the test: removing the group cap or defining demand as the inverse of supply.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
/Users/renyinghao.1/Project/stock_screener/venv/bin/python \
-m unittest tests.test_supply_demand -v
```

Expected: import failure for `research.supply_demand`.

- [ ] **Step 3: Implement validation, causal series, evidence weights, caps, and coverage**

Implement:

```python
def build_supply_demand_rows(
    history: pd.DataFrame,
    *,
    qqq_close: pd.Series | None = None,
    sector_close: pd.Series | None = None,
) -> pd.DataFrame:
    checked = _validated_history(history)
    pressure = build_pressure_rows(checked)
    # Prior resistance and volume baselines come from pressure or shift(1).
    # Compute boolean evidence with nullable availability masks.
    # Score each group with _capped_group(), then derive model coverage.
    # Return one row for every input date without mutating inputs.
```

Use explicit dictionaries for atomic weights. `_capped_group()` must calculate points, available raw weight, condition tuples, and the configured group cap. Model coverage is available raw weight divided by total raw weight. A model score is `NaN` when coverage is below `0.75`.

- [ ] **Step 4: Add failing tests for absorption, exhaustion, higher-low, and breakout semantics**

Add separate positive and negative fixtures for:

- `seller_exhaustion`
- `buyer_absorption`
- `low_volume_higher_low`
- `breakout_acceptance`
- `breakout_follow_through`
- `failed_breakout`
- `pressure_test_efficiency_decay`

Use hand-derived expected booleans. Do not calculate expected values with production helpers.

- [ ] **Step 5: Implement the minimum causal rules and combined states**

Implement the exact thresholds and weights from the approved design. Freeze accepted breakout pivots for the three-session follow-through window. Derive:

```python
healthy = (demand >= 60.0) & (supply < 40.0)
contest = (demand >= 50.0) & (supply >= 50.0)
distribution = (supply >= 60.0) & (demand < 50.0)
low_participation = (supply < 40.0) & (demand < 40.0)
```

All other available combinations are `mixed`; insufficient core coverage is `unavailable`.

- [ ] **Step 6: Add and pass prefix-invariance and missing-context tests**

Assert:

```python
expected = build_supply_demand_rows(base)
actual = build_supply_demand_rows(pd.concat([base, future])).loc[base.index]
pd.testing.assert_frame_equal(actual, expected)
```

Also assert that missing QQQ and sector context lower the appropriate coverage, list stable unavailable reasons, and never turn missing evidence into a met or unmet boolean.

- [ ] **Step 7: Run focused and neighboring model tests**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
/Users/renyinghao.1/Project/stock_screener/venv/bin/python \
-m unittest tests.test_supply_demand tests.test_market_pressure \
tests.test_high_level_distribution -v
```

Expected: all tests pass with no new warnings.

- [ ] **Step 8: Commit**

```bash
git add research/supply_demand.py tests/test_supply_demand.py
git commit -m "feat: add causal supply demand scores"
```

---

### Task 2: Chart-date integration service

**Files:**
- Create: `web/services/supply_demand.py`
- Create: `tests/test_web_supply_demand_service.py`
- Modify: `web/app.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `build_supply_demand_rows(...)`
- Produces: `attach_supply_demand_rows(chart: list[dict], ticker: str, histories: Mapping[str, pd.DataFrame]) -> None`
- Adds only the supply/demand output fields to matching chart dates

- [ ] **Step 1: Write failing service tests**

Test three real behaviors:

1. Every chart row receives stable unavailable defaults before optional context is resolved.
2. Matching dates receive finite scores, coverage, conditions, and state.
3. Unknown thematic membership still receives stock and QQQ evidence; known membership uses the normalized mean of available group benchmarks.

The test must assert chart input dates remain unchanged and no row is inserted.

- [ ] **Step 2: Run and verify RED**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
/Users/renyinghao.1/Project/stock_screener/venv/bin/python \
-m unittest tests.test_web_supply_demand_service -v
```

Expected: import failure for `web.services.supply_demand`.

- [ ] **Step 3: Implement the thin adapter**

Implement:

```python
def attach_supply_demand_rows(chart, ticker, histories):
    for row in chart:
        row.update(UNAVAILABLE_DEFAULTS)
    history = histories.get(ticker)
    if not chart or not isinstance(history, pd.DataFrame):
        return
    qqq_close = _close(histories.get("QQQ"))
    group = market_group_for_ticker(ticker)
    sector_close = _normalized_benchmark_close(histories, group)
    model = build_supply_demand_rows(
        history,
        qqq_close=qqq_close,
        sector_close=sector_close,
    )
    # Serialize only finite values and exact matching ISO dates.
```

Benchmark normalization must divide each valid benchmark by its first available close and average aligned normalized series. It must not backfill or forward-fill missing dates.

- [ ] **Step 4: Add failing API contract tests**

Patch the stock endpoint fixture with a deterministic supply/demand model row and assert the selected chart date contains:

```python
{
    "supply_pressure_model_key": "supply_pressure_v1",
    "demand_confirmation_model_key": "demand_confirmation_v1",
    "supply_pressure_score": ...,
    "demand_confirmation_score": ...,
    "supply_demand_state": ...,
}
```

Assert the historical forecast endpoint attaches evidence from the same date.

- [ ] **Step 5: Call the adapter before `_attach_model_outputs()`**

Invoke `attach_supply_demand_rows()` in both the main stock-detail route and historical forecast route, after chart rows and entry signals are built but before model outputs are attached.

- [ ] **Step 6: Run API and service tests**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
/Users/renyinghao.1/Project/stock_screener/venv/bin/python \
-m unittest tests.test_web_supply_demand_service tests.test_web_api -v
```

- [ ] **Step 7: Commit**

```bash
git add web/services/supply_demand.py web/app.py \
tests/test_web_supply_demand_service.py tests/test_web_api.py
git commit -m "feat: attach supply demand evidence to chart dates"
```

---

### Task 3: Register auditable advisory model outputs

**Files:**
- Modify: `web/forecasts/model_outputs.py`
- Modify: `web/forecasts/model_output_registry.py`
- Modify: `tests/test_web_model_outputs.py`
- Modify: `tests/test_web_model_output_registry.py`

**Interfaces:**
- Consumes chart-row fields from Task 2
- Produces `supply_pressure_v1` in `downside`
- Replaces planned `demand_confirmation` with production `demand_confirmation_v1` in `bullish_structure`

- [ ] **Step 1: Write failing registry and output tests**

Assert:

- `supply_pressure_v1` has kind `rule_score`, lifecycle `production`, timing `close_confirmed`, permission `advisory`.
- `demand_confirmation_v1` has the same identity semantics in the bullish group.
- Scores, coverage, state, group subscores, conditions, and unavailable reasons come from the same chart date.
- Neither model changes or appears in the final policy reasons.

- [ ] **Step 2: Run and verify RED**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
/Users/renyinghao.1/Project/stock_screener/venv/bin/python \
-m unittest tests.test_web_model_outputs \
tests.test_web_model_output_registry -v
```

Expected: missing supply definition and the old planned demand identity.

- [ ] **Step 3: Implement pure presentation builders**

Add builders equivalent to:

```python
def _supply_pressure(row):
    return _supply_demand_output(
        row,
        model_key="supply_pressure_v1",
        score_field="supply_pressure_score",
        coverage_field="supply_pressure_coverage",
        condition_field="supply_pressure_conditions",
        group_fields=(...),
    )
```

Builders must return payload data only; registry identity stays owned by `ModelOutputDefinition`.

- [ ] **Step 4: Bump registry version and pass tests**

Change `REGISTRY_VERSION` to `model_output_registry_v2` because public model membership changes. Update contract tests to derive and verify the new registry reference.

- [ ] **Step 5: Commit**

```bash
git add web/forecasts/model_outputs.py \
web/forecasts/model_output_registry.py \
tests/test_web_model_outputs.py tests/test_web_model_output_registry.py
git commit -m "feat: register supply demand model outputs"
```

---

### Task 4: Localized fixed-panel rendering

**Files:**
- Modify: `web/static/js/model_outputs.js`
- Modify: `web/static/js/i18n.js`
- Modify: `tests/model_outputs_runtime.mjs`
- Modify: `tests/test_web_assets.py`

**Interfaces:**
- Consumes generic model-output `coverage`, `supply_demand_state`, and `metrics`
- Renders localized names, evidence conditions, coverage, group subscores, state, timing, permission, explanation, and limitation

- [ ] **Step 1: Write failing Node runtime assertions**

Add supply and demand payloads to the runtime fixture and assert Chinese and English text contains:

- “疑似供给压力” / “Supply pressure proxy”
- “需求确认代理” / “Demand confirmation proxy”
- localized combined state
- coverage and all three group scores
- the limitation that daily data cannot verify institutional order flow

Render through the standalone model-output module used by the fixed panel. The
test fixture must not expose a chart object; this preserves the architectural
boundary that prevents the renderer from creating or mutating chart series.

- [ ] **Step 2: Run and verify RED**

Run:

```bash
node tests/model_outputs_runtime.mjs
```

Expected: missing translations or fields.

- [ ] **Step 3: Implement generic coverage and state rendering**

Render `coverage` as a percentage when present. Render `supply_demand_state` through `modelOutput.supplyDemandState.<value>` without reusing persistent-risk state labels. Use the existing `metrics` contract for group subscores instead of hard-coding supply/demand field names in the renderer.

- [ ] **Step 4: Add complete Chinese and English translations**

Add names, explanations, limitations, state labels, metric labels, condition labels, and unavailable reasons. Keep the disclaimer that a rule score is not a probability.

- [ ] **Step 5: Run frontend asset tests**

Run:

```bash
node tests/model_outputs_runtime.mjs
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
/Users/renyinghao.1/Project/stock_screener/venv/bin/python \
-m unittest tests.test_web_assets -v
```

- [ ] **Step 6: Commit**

```bash
git add web/static/js/model_outputs.js web/static/js/i18n.js \
tests/model_outputs_runtime.mjs tests/test_web_assets.py
git commit -m "feat: render localized supply demand evidence"
```

---

### Task 5: Documentation, full verification, and integration

**Files:**
- Modify: `docs/modeling-todo.md`
- Test: all Python and Node suites

**Interfaces:**
- Records implemented scope without claiming model promotion or completed walk-forward evidence

- [ ] **Step 1: Update the global task table truthfully**

Mark implemented SUPPLY-001 and DEMAND-001 atomic evidence, independent scores, group caps, point-in-time prefix tests, API fields, and UI outputs as complete. Keep “separate and joint walk-forward evaluation” open. Do not mark the overall tasks complete until expanded-universe evidence exists.

- [ ] **Step 2: Run document checks**

Run:

```bash
git diff --check
```

Read the plan once against the approved specification and confirm every
production field has a producer, consumer, test, and explicit unavailable
behavior.

- [ ] **Step 3: Run the full Python suite**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
LOKY_MAX_CPU_COUNT=8 \
/Users/renyinghao.1/Project/stock_screener/venv/bin/python \
-m unittest discover -s tests -p "test_*.py"
```

Expected: zero failures and zero errors.

- [ ] **Step 4: Run all standalone Node tests**

Run:

```bash
for test_file in tests/*.mjs; do node "$test_file"; done
```

Expected: every script exits zero.

- [ ] **Step 5: Verify repository scope and commit**

Run:

```bash
git status --short
git diff --stat HEAD
git diff --check
```

Confirm no database, cache, secret, user untracked file, Ridge policy, or chart series change is included. Then:

```bash
git add docs/modeling-todo.md
git commit -m "docs: record supply demand model progress"
```

- [ ] **Step 6: Merge safely**

Before merging, compare main status and recent commits. If overlapping files changed on main, rebase or merge main into the feature worktree and rerun the full verification suite. Merge to main only after fresh verification; preserve all pre-existing uncommitted main files.
