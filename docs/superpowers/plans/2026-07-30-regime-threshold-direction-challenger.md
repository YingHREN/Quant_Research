# Regime Threshold Direction Challenger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evaluate a leakage-safe five-session absolute-direction challenger with causal market-regime priors and an inner-OOF economic down threshold, while keeping a separately named QQQ-relative diagnostic head.

**Architecture:** Add a pure research module for exact-date QQQ-relative targets, training-only regime priors, nested threshold selection, and outer-fold predictions. Reuse the existing executable targets, purged folds, Ridge/global Logistic comparators, `market_regime_v1`, expanded research cohort, metric helpers, and atomic report publication; keep all production forecast and web paths unchanged.

**Tech Stack:** Python 3, pandas, NumPy, scikit-learn LogisticRegression/Ridge, `unittest`, SQLite-backed expanded research data, Markdown/CSV/JSON reports.

## Global Constraints

- Use the same 240-ticker cohort, 2018-01-01 start, and seed `20260726` as the hierarchical recency experiment.
- Use only horizon 5, next-session open entry, fifth-future-session close exit, and the frozen ±1% neutral band.
- QQQ-relative return is the stock executable return minus QQQ executable return over the stock row's exact entry and exit dates.
- QQQ-relative direction is a separate diagnostic target and must never be serialized as an absolute direction.
- Reuse causal `market_regime_v1`; do not fit hidden states or backfill current SEC classifications.
- Every preprocessing statistic, class weight, regime prior, and economic threshold is fitted from the current outer training fold only.
- The threshold grid is exactly `(0.40, 0.50, 0.60, 0.70)` with minimum 5% coverage, 500 predicted-down rows, negative mean absolute return, and a 2-point precision gain over the inner 0.50 threshold.
- Preserve `ridge_direction_v1`, `forecast_decision_policy`, every risk veto, API contract, and UI output.
- The challenger remains `research` with `online_authority=none`, even if historical metric gates pass.
- Do not stage SQLite WAL/SHM files, credentials, absolute database paths, or `research/high_level_reversal_study.py`.

---

### Task 1: Exact-date QQQ-relative executable targets

**Files:**
- Create: `research/regime_threshold_direction.py`
- Create: `tests/test_regime_threshold_direction.py`

**Interfaces:**
- Consumes: a feature frame already processed by `attach_next_open_targets()`, QQQ OHLC history, and horizon 5.
- Produces: `attach_qqq_relative_targets(frame, qqq_history, *, horizon=5) -> pandas.DataFrame`.
- Adds: `qqq_executable_return_5` and `qqq_relative_return_5`; preserves all absolute target columns and inputs.

- [x] **Step 1: Write failing exact-date target tests**

Create a multi-ticker frame whose stock rows share observation dates but one ticker skips a trading date. Assert literal QQQ returns from each stock row's `executable_entry_date_5` and `executable_label_end_date_5`, literal relative returns, unchanged absolute returns, input immutability, typed failures for missing columns/duplicate QQQ dates/unsupported horizons, and `NaN` when either exact QQQ endpoint is unavailable.

```python
result = attach_qqq_relative_targets(frame, qqq, horizon=5)
self.assertAlmostEqual(
    result.loc[("AAA", observation), "qqq_executable_return_5"],
    110.0 / 100.0 - 1.0,
)
self.assertAlmostEqual(
    result.loc[("AAA", observation), "qqq_relative_return_5"],
    absolute_return - (110.0 / 100.0 - 1.0),
)
self.assertEqual(
    direction_labels(pd.Series([0.02]), 5).tolist(),
    ["up"],
)
self.assertEqual(
    direction_labels(pd.Series([-0.06]), 5).tolist(),
    ["down"],
)
```

The last two assertions document that a positive absolute return may coexist with negative relative return; later serializers must retain both names.

- [x] **Step 2: Run the focused test and verify RED**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_regime_threshold_direction.QQQRelativeTargetTest -v
```

Expected: import failure because `research.regime_threshold_direction` does not exist.

- [x] **Step 3: Implement the minimal immutable target adapter**

Validate the standard `(ticker, observation_date)` index and required absolute target/date columns. Normalize a copy of QQQ's index to timezone-naive dates, reject duplicates, look up `Open` on each row's recorded entry date and `Close` on its recorded label-end date, and compute:

```python
benchmark_return = qqq_exit_close / qqq_entry_open.replace(0.0, np.nan) - 1.0
relative_return = frame[f"executable_return_{horizon}"] - benchmark_return
```

Never infer endpoints by shifting QQQ independently, because stock calendars may differ.

- [x] **Step 4: Run focused and target regressions**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_regime_threshold_direction.QQQRelativeTargetTest tests.test_market_direction_model -v
```

Expected: PASS with absolute target fixtures unchanged.

- [x] **Step 5: Commit Task 1**

```bash
git add research/regime_threshold_direction.py tests/test_regime_threshold_direction.py docs/superpowers/plans/2026-07-30-regime-threshold-direction-challenger.md
git commit -m "research: add exact qqq relative targets"
```

### Task 2: Causal regime attachment and training-only priors

**Files:**
- Modify: `research/regime_threshold_direction.py`
- Modify: `tests/test_regime_threshold_direction.py`

**Interfaces:**
- Consumes: `build_market_regime_frame(histories)`, absolute direction labels, training weights, and observation dates.
- Produces: `attach_causal_regimes(frame, regimes) -> pandas.DataFrame`.
- Produces: `fit_regime_priors(labels, weights, regimes, classes) -> RegimePriors`.
- Produces: `adjust_regime_log_probabilities(log_probabilities, regimes, priors) -> numpy.ndarray`.
- Uses fixed prior strength `1000.0`.

- [ ] **Step 1: Write failing causal-prior tests**

Assert date-normalized attachment, `unavailable` fallback, input immutability, exact global fallback for unseen regimes, strong shrinkage for small regimes, larger movement for large regimes, finite normalized posteriors, and zero correction for missing/test-only regimes. Append extreme future QQQ/SPY rows and require all earlier attached regimes and corrections to remain identical.

```python
priors = fit_regime_priors(
    labels,
    np.ones(len(labels)),
    regimes,
    ("down", "neutral", "up"),
)
np.testing.assert_allclose(
    priors.regime_prior("never_seen"),
    priors.global_prior,
)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_regime_threshold_direction.RegimePriorTest -v
```

Expected: missing regime-prior symbols.

- [ ] **Step 3: Implement immutable regime priors**

Use weighted class counts. For each observed regime calculate:

```python
posterior = (
    regime_counts + 1000.0 * global_prior
) / (regime_weight + 1000.0)
```

Store tuple-backed immutable state. Clip only at machine epsilon before log correction. Missing or unseen regimes add exactly zero to the global log scores.

- [ ] **Step 4: Run focused and causal-regime regressions**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_regime_threshold_direction.RegimePriorTest tests.test_market_regime -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add research/regime_threshold_direction.py tests/test_regime_threshold_direction.py
git commit -m "research: add causal direction regime priors"
```

### Task 3: Inner-OOF economic down-threshold selection

**Files:**
- Modify: `research/regime_threshold_direction.py`
- Modify: `tests/test_regime_threshold_direction.py`

**Interfaces:**
- Consumes: inner-OOF rows with `down_score`, `actual_direction`, and `actual_return`.
- Produces: `select_economic_down_threshold(oof_predictions, *, thresholds=(0.40, 0.50, 0.60, 0.70), minimum_coverage=0.05, minimum_down_rows=500, minimum_precision_gain=0.02) -> tuple[float | None, dict]`.
- Produces: `threshold_directions(log_probabilities, threshold, classes) -> numpy.ndarray`.

- [ ] **Step 1: Write failing threshold-contract tests**

Use hand-counted fixtures to assert all four gates, exact boundary inclusion, balanced-accuracy selection, higher-threshold tie-break, and explicit `economic_threshold_unavailable`. Assert thresholding only changes the down boundary and otherwise chooses between neutral/up. Mutate outer-test labels passed to a caller fixture and require the selected threshold to remain unchanged.

```python
threshold, diagnostics = select_economic_down_threshold(oof)
self.assertEqual(threshold, 0.60)
self.assertEqual(diagnostics["status"], "available")
self.assertEqual(
    diagnostics["candidate_thresholds"],
    [0.40, 0.50, 0.60, 0.70],
)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_regime_threshold_direction.EconomicThresholdTest -v
```

Expected: missing threshold-selection symbols.

- [ ] **Step 3: Implement frozen threshold evaluation**

For every threshold, derive directions, predicted-down count/coverage, down precision, predicted-down mean absolute return, and balanced accuracy. Compare precision to the same OOF rows thresholded at `0.50`; filter by all gates; sort eligible candidates by `(balanced_accuracy, threshold)` descending. Return `None` plus stable reason `economic_threshold_unavailable` if none qualify.

- [ ] **Step 4: Run focused tests**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_regime_threshold_direction.EconomicThresholdTest -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add research/regime_threshold_direction.py tests/test_regime_threshold_direction.py
git commit -m "research: select economic down thresholds"
```

### Task 4: Nested walk-forward absolute challenger

**Files:**
- Modify: `research/regime_threshold_direction.py`
- Modify: `tests/test_regime_threshold_direction.py`

**Interfaces:**
- Consumes: absolute-target frame, causal regimes, feature columns, five outer folds, and three inner folds.
- Produces: `walk_forward_regime_threshold_predictions(frame, regimes, *, feature_columns, minimum_samples=1000) -> tuple[pandas.DataFrame, pandas.DataFrame]`.
- Absolute specifications: `logistic_global`, `logistic_regime_prior`, and `logistic_regime_threshold`.

- [ ] **Step 1: Write failing nested-fold tests**

Assert five outer test folds, identical keys for available candidates, every outer training label end before outer test start, every inner training label end before inner validation start, and threshold diagnostics containing only inner rows. Change outer test returns and future regimes independently; require the same selected threshold and pre-change predictions. Cover missing classes, insufficient inner OOF rows, and unavailable thresholds without fabricated main-candidate rows.

```python
predictions, diagnostics = walk_forward_regime_threshold_predictions(
    frame,
    regimes,
    feature_columns=("feature_a", "feature_b"),
    minimum_samples=100,
)
self.assertTrue(
    (
        pd.to_datetime(diagnostics["outer_train_label_end_max"])
        < pd.to_datetime(diagnostics["outer_test_start"])
    ).all()
)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_regime_threshold_direction.NestedWalkForwardTest -v
```

Expected: missing nested walk-forward function.

- [ ] **Step 3: Implement outer and inner purged folds**

Reuse `chronological_purged_folds()` for the five outer test folds and create three expanding purged folds inside each outer training frame. Fit train-only designs and class-balanced Logistic models with:

```python
LogisticRegression(
    max_iter=1000,
    random_state=0,
    solver="liblinear",
)
```

Apply training-only regime priors to inner and outer scores. Select the threshold from concatenated inner OOF rows only. Emit typed diagnostics and omit only `logistic_regime_threshold` when selection is unavailable.

- [ ] **Step 4: Run focused and hierarchy regressions**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_regime_threshold_direction.NestedWalkForwardTest tests.test_hierarchical_direction tests.test_market_direction_model -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add research/regime_threshold_direction.py tests/test_regime_threshold_direction.py
git commit -m "research: add nested regime threshold walk forward"
```

### Task 5: Independent QQQ-relative diagnostic head

**Files:**
- Modify: `research/regime_threshold_direction.py`
- Modify: `tests/test_regime_threshold_direction.py`

**Interfaces:**
- Consumes: `qqq_relative_return_5`, the same outer folds, and the same atomic features.
- Produces: `walk_forward_qqq_relative_predictions(frame, *, feature_columns, minimum_samples=1000) -> pandas.DataFrame`.
- Uses specification `logistic_qqq_relative` and output names `actual_relative_return`, `actual_relative_direction`, and `predicted_relative_direction`.

- [ ] **Step 1: Write failing semantic-separation tests**

Assert the relative head shares outer `(ticker, observation_date, horizon, fold)` keys but has no `actual_direction` or `predicted_direction` columns. Include a row with positive absolute return and negative QQQ-relative return; require `actual_relative_direction="down"` while the separately computed absolute label remains `"up"`. Assert missing QQQ targets produce no fabricated rows.

```python
self.assertNotIn("predicted_direction", relative_predictions)
self.assertEqual(row["actual_relative_direction"], "down")
self.assertEqual(
    direction_labels(pd.Series([row["actual_return"]]), 5)[0],
    "up",
)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_regime_threshold_direction.RelativeHeadTest -v
```

Expected: missing relative-head function.

- [ ] **Step 3: Implement the separately named relative head**

Train the same class-balanced Logistic on `direction_labels(qqq_relative_return_5, 5)`, using train-only designs and the existing purged fold indices. Serialize only relative-specific target and prediction names, plus `actual_return` as a diagnostic column; never alias relative direction into absolute names.

- [ ] **Step 4: Run focused tests**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_regime_threshold_direction.RelativeHeadTest -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 5**

```bash
git add research/regime_threshold_direction.py tests/test_regime_threshold_direction.py
git commit -m "research: add qqq relative diagnostic head"
```

### Task 6: Study runner, metric gate, and strict reports

**Files:**
- Create: `research/run_regime_threshold_direction.py`
- Create: `tests/test_run_regime_threshold_direction.py`

**Interfaces:**
- Reuses: expanded cohort loading, absolute comparators, market-regime evaluation, subgroup mapping, named cases, content fingerprints, and atomic report conventions.
- Produces: `regime_threshold_promotion_decision(metrics, regime_metrics) -> dict`.
- Produces: JSON/CSV/Markdown with separate `absolute` and `qqq_relative` sections.

- [ ] **Step 1: Write failing runner-contract tests**

Cover all five absolute specifications, the separately named relative specification, overlapping/non-overlapping metrics, exact four-of-five negative-return gate, precision/recall/accuracy/coverage boundaries, three subgroups, stressed-regime comparisons, positive ablation requirement, permanent `eligible=false`, and named cases. Serialize a fixture containing `NaN`, an absolute path, and a secret-like field; require strict rejection rather than lossy publication.

```python
decision = regime_threshold_promotion_decision(
    absolute_metrics,
    regime_metrics,
)
self.assertTrue(decision["metric_gate_passed"])
self.assertFalse(decision["eligible"])
self.assertEqual(decision["online_authority"], "none")
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_run_regime_threshold_direction -v
```

Expected: runner module is absent.

- [ ] **Step 3: Implement deterministic aggregation and frozen gate**

Build absolute comparator rows on identical outer keys. Aggregate absolute metrics, relative metrics, subgroup metrics, and causal regime metrics separately. Apply all nine frozen historical gates from the design without allowing relative metrics to satisfy an absolute gate. Keep `eligible=false` and `online_authority="none"` unconditionally.

- [ ] **Step 4: Implement atomic strict publication**

Publish sibling temporary JSON/CSV/Markdown files only after validating finite JSON values, relative path metadata, absence of credential-shaped keys/values, matching comparator keys, fold purge boundaries, and causal regime version. Include git commit, dirty flag, input fingerprint, full configuration, selected thresholds, diagnostics, limitations, and the pre-registered cases.

- [ ] **Step 5: Run focused tests**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_run_regime_threshold_direction tests.test_run_hierarchical_recency_direction -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 6**

```bash
git add research/run_regime_threshold_direction.py tests/test_run_regime_threshold_direction.py
git commit -m "research: add regime threshold study runner"
```

### Task 7: Real experiment, audit, and global TODO

**Files:**
- Create: `reports/regime-threshold-direction.json`
- Create: `reports/regime-threshold-direction.csv`
- Create: `reports/regime-threshold-direction.md`
- Modify: `docs/modeling-todo.md`

**Interfaces:**
- Consumes: `data/research_prices.db` read-only.
- Produces: reproducible historical evidence and the next global-TODO decision.

- [ ] **Step 1: Run a 30-ticker smoke study**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python research/run_regime_threshold_direction.py --max-tickers 30 --minimum-samples 300 --output-prefix /private/tmp/regime-threshold-smoke
```

Expected: all artifacts publish, absolute and relative sections remain separate, and the manifest states `online_authority=none`.

- [ ] **Step 2: Run the full frozen study**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python research/run_regime_threshold_direction.py
```

Expected: five outer folds where frozen inner gates permit, per-fold threshold diagnostics, complete subgroup/regime results, and a deterministic gate decision.

- [ ] **Step 3: Audit before interpretation**

Verify identical absolute comparator keys, exact QQQ endpoint dates, no label-end leakage, no outer labels in threshold selection, no relative-to-absolute field aliases, finite metrics, correct group/regime totals, no credentials/absolute paths, and stable rerun hashes for JSON/CSV/Markdown.

- [ ] **Step 4: Update the Chinese global TODO**

Record exact cohort/date/sample counts, chosen thresholds by fold, absolute and relative metrics, negative-return fold count, subgroup/regime failures, and the frozen conclusion. Keep the experiment advisory-only and the visible direction label disabled unless all historical plus future-shadow requirements pass.

- [ ] **Step 5: Run the full repository suite and service health check**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest discover -s tests -v
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/api/update/status
```

Expected: all tests pass and both HTTP responses are `200`.

- [ ] **Step 6: Review and commit evidence**

Run `git diff --check`, inspect `git diff --cached --name-only`, confirm protected runtime files are absent, and commit:

```bash
git add reports/regime-threshold-direction.json reports/regime-threshold-direction.csv reports/regime-threshold-direction.md docs/modeling-todo.md
git commit -m "research: evaluate regime threshold direction"
```
