# Asymmetric Tail Risk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and evaluate a causal five-session asymmetric downside-tail challenger without changing any online forecast or UI authority.

**Architecture:** A focused research module owns exact executable labels, two calibrated binary heads, two conditional quantile heads, nested threshold selection, and fold predictions. A separate runner loads the frozen cohort and features, evaluates overlapping/non-overlapping results, audits extreme counterexamples, applies a preregistered gate, and atomically publishes strict reports.

**Tech Stack:** Python 3, pandas, NumPy, scikit-learn LogisticRegression, IsotonicRegression and HistGradientBoostingRegressor, unittest, SQLite research inputs.

## Global Constraints

- Entry is the next stock-session open and exit is the fifth future stock-session close.
- `down_event_5` means terminal return <= -5% or five-session MAE <= -7%.
- `extreme_rebound_5` means terminal return >= +10%.
- Probability calibration and threshold selection use inner purged OOF rows only.
- Economic metrics use raw, unwinsorized outcomes and preserve extreme winners.
- The model remains `research` with `online_authority=none`; no Ridge, policy, API or UI change is permitted.
- Missing endpoints, classes, calibration support or thresholds fail closed with typed reasons.

---

### Task 1: Exact asymmetric path targets

**Files:**
- Create: `research/asymmetric_tail_risk.py`
- Create: `tests/test_asymmetric_tail_risk.py`

**Interfaces:**
- Consumes: a MultiIndex feature frame and `{ticker: OHLCV DataFrame}` histories.
- Produces: `attach_asymmetric_tail_targets(frame, histories, horizon=5) -> DataFrame`.

- [ ] **Step 1: Write failing exact-label tests**

```python
def test_targets_use_next_open_terminal_close_and_complete_future_low_path():
    result = attach_asymmetric_tail_targets(frame, {"AAA": history})
    row = result.loc[("AAA", dates[0])]
    self.assertAlmostEqual(row["terminal_return_5"], close_5 / open_1 - 1.0)
    self.assertAlmostEqual(row["path_mae_5"], min(low_1_to_5) / open_1 - 1.0)
    self.assertEqual(row["down_event_5"], 1.0)
    self.assertEqual(row["extreme_rebound_5"], 0.0)
    self.assertEqual(row["tail_label_end_date_5"], dates[5])

def test_incomplete_or_missing_path_is_not_shifted_or_fabricated():
    result = attach_asymmetric_tail_targets(frame, {"AAA": history_with_gap})
    self.assertTrue(result.loc[key, target_columns].isna().all())
```

- [ ] **Step 2: Run tests and verify RED**

Run: `../../venv/bin/python -m unittest tests.test_asymmetric_tail_risk -v`
Expected: FAIL because `research.asymmetric_tail_risk` does not exist.

- [ ] **Step 3: Implement strict target attachment**

```python
def attach_asymmetric_tail_targets(frame, histories, horizon=5):
    # validate named unique MultiIndex and exact OHLC endpoints
    # align each ticker by its own sorted trading sessions
    # emit terminal_return_5, path_mae_5, down_event_5,
    # extreme_rebound_5 and tail_label_end_date_5
    return result
```

- [ ] **Step 4: Run focused and existing target tests**

Run: `../../venv/bin/python -m unittest tests.test_asymmetric_tail_risk tests.test_downside_specialist tests.test_regime_threshold_direction -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add research/asymmetric_tail_risk.py tests/test_asymmetric_tail_risk.py
git commit -m "feat: add exact asymmetric tail targets"
```

### Task 2: OOF probability calibration

**Files:**
- Modify: `research/asymmetric_tail_risk.py`
- Modify: `tests/test_asymmetric_tail_risk.py`

**Interfaces:**
- Produces: `fit_oof_isotonic(scores, outcomes, minimum_rows=500, minimum_class_rows=50) -> CalibrationResult`.
- Produces: `CalibrationResult.transform(scores) -> ndarray`.

- [ ] **Step 1: Write failing calibration tests**

```python
def test_isotonic_calibration_is_monotonic_and_bounded():
    fitted = fit_oof_isotonic(scores, outcomes, minimum_rows=6, minimum_class_rows=2)
    calibrated = fitted.transform(np.array([0.1, 0.5, 0.9]))
    self.assertTrue(np.all(np.diff(calibrated) >= 0.0))
    self.assertTrue(np.all((calibrated >= 0.0) & (calibrated <= 1.0)))

def test_calibration_fails_closed_for_one_class_or_constant_scores():
    self.assertEqual(fit_oof_isotonic(...).reason, "calibration_unavailable")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `../../venv/bin/python -m unittest tests.test_asymmetric_tail_risk -v`
Expected: FAIL because the calibration API is missing.

- [ ] **Step 3: Implement immutable calibration result**

Use `IsotonicRegression(out_of_bounds="clip")`; reject non-finite input,
insufficient rows, insufficient positive/negative rows and fewer than two unique
scores. Store only finite threshold arrays and return defensive copies.

- [ ] **Step 4: Run focused tests**

Run: `../../venv/bin/python -m unittest tests.test_asymmetric_tail_risk -v`
Expected: PASS without warnings.

- [ ] **Step 5: Commit**

```bash
git add research/asymmetric_tail_risk.py tests/test_asymmetric_tail_risk.py
git commit -m "feat: add oof tail probability calibration"
```

### Task 3: Nested multi-head walk-forward challenger

**Files:**
- Modify: `research/asymmetric_tail_risk.py`
- Modify: `tests/test_asymmetric_tail_risk.py`

**Interfaces:**
- Produces: `walk_forward_asymmetric_tail_predictions(frame, feature_columns, n_test_folds=5, minimum_samples=1000) -> DataFrame`.
- Produces: `select_tail_boundary(inner_oof, ...) -> TailBoundaryResult`.

- [ ] **Step 1: Write failing fold, leakage and semantic tests**

```python
def test_outer_predictions_have_four_distinct_head_outputs():
    rows = walk_forward_asymmetric_tail_predictions(sample, ("feature",), ...)
    self.assertIn("calibrated_down_probability", rows)
    self.assertIn("predicted_median_return", rows)
    self.assertIn("predicted_lower_quantile_return", rows)
    self.assertIn("calibrated_rebound_probability", rows)

def test_outer_outcomes_cannot_change_inner_boundary_or_predictions():
    changed = sample.copy()
    changed.loc[outer_keys, outcome_columns] *= -10
    pd.testing.assert_frame_equal(predictions(sample), predictions(changed))

def test_quantile_predictions_are_ordered_or_explicitly_unavailable():
    self.assertTrue((rows["predicted_lower_quantile_return"]
                     <= rows["predicted_median_return"]).all())
```

- [ ] **Step 2: Run tests and verify RED**

Run: `../../venv/bin/python -m unittest tests.test_asymmetric_tail_risk -v`
Expected: FAIL because nested predictions are missing.

- [ ] **Step 3: Implement fixed heads and inner OOF selection**

Fit balanced Logistic heads and fixed
`HistGradientBoostingRegressor(loss="quantile", quantile=0.5/0.2,
max_iter=100, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=50,
l2_regularization=1.0, random_state=0)`. Use training-only median imputation,
three inner purged folds, OOF isotonic calibration and the frozen
down-threshold/rebound-cap grid. Enforce lower quantile <= median at serialization
and retain both raw values for audit.

- [ ] **Step 4: Run focused tests plus causal model tests**

Run: `../../venv/bin/python -m unittest tests.test_asymmetric_tail_risk tests.test_regime_threshold_direction tests.test_market_direction_model -v`
Expected: PASS without numeric warnings.

- [ ] **Step 5: Commit**

```bash
git add research/asymmetric_tail_risk.py tests/test_asymmetric_tail_risk.py
git commit -m "feat: add nested asymmetric tail challenger"
```

### Task 4: Metrics, counterexample audit and frozen gate

**Files:**
- Modify: `research/asymmetric_tail_risk.py`
- Modify: `tests/test_asymmetric_tail_risk.py`

**Interfaces:**
- Produces: `evaluate_tail_predictions(predictions, group_map) -> DataFrame`.
- Produces: `audit_extreme_counterexamples(predictions) -> DataFrame`.
- Produces: `tail_promotion_decision(metrics, causal_audit) -> dict`.

- [ ] **Step 1: Write failing raw-return and gate tests**

```python
def test_metrics_keep_extreme_winner_in_untrimmed_mean():
    metrics = evaluate_tail_predictions(predictions_with_100_percent_winner, {})
    self.assertAlmostEqual(metrics.loc[scope, "mean_terminal_return"], expected_raw_mean)

def test_gate_requires_negative_mean_in_four_folds_and_both_large_groups():
    decision = tail_promotion_decision(metrics_missing_software, {"passed": True})
    self.assertFalse(decision["promoted"])

def test_counterexample_audit_contains_only_high_score_extreme_winners():
    audited = audit_extreme_counterexamples(predictions)
    self.assertTrue((audited["terminal_return_5"] >= 0.10).all())
```

- [ ] **Step 2: Run tests and verify RED**

Run: `../../venv/bin/python -m unittest tests.test_asymmetric_tail_risk -v`
Expected: FAIL because evaluation APIs are missing.

- [ ] **Step 3: Implement overlapping/non-overlapping metrics and gate**

Compute coverage, precision, recall, event rate, rebound rate and untouched mean
return by all/group/regime/fold/sample mode. Non-overlapping rows are selected
deterministically per ticker in five-session blocks. Emit every failed gate
condition and permanently set `online_authority` to `none`.

- [ ] **Step 4: Run focused tests**

Run: `../../venv/bin/python -m unittest tests.test_asymmetric_tail_risk -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add research/asymmetric_tail_risk.py tests/test_asymmetric_tail_risk.py
git commit -m "feat: evaluate asymmetric tail risk"
```

### Task 5: Reproducible real-data runner and reports

**Files:**
- Create: `research/run_asymmetric_tail_risk.py`
- Create: `tests/test_run_asymmetric_tail_risk.py`
- Modify: `docs/modeling-todo.md`
- Create at runtime: `reports/asymmetric-tail-risk.json`
- Create at runtime: `reports/asymmetric-tail-risk.csv`
- Create at runtime: `reports/asymmetric-tail-risk.md`

**Interfaces:**
- Produces: `run_study(...) -> dict`.
- Produces strict atomic reports with source/input/fold/calibration/gate evidence.

- [ ] **Step 1: Write failing report and safety tests**

```python
def test_reports_are_atomic_strict_and_research_only():
    payload = build_report(...)
    self.assertEqual(payload["model"]["lifecycle"], "research")
    self.assertEqual(payload["model"]["online_authority"], "none")
    json.dumps(payload, allow_nan=False)

def test_report_rejects_secrets_nonfinite_values_and_relative_paths():
    with self.assertRaises(ValueError):
        validate_report_payload(unsafe_payload)
```

- [ ] **Step 2: Run runner tests and verify RED**

Run: `../../venv/bin/python -m unittest tests.test_run_asymmetric_tail_risk -v`
Expected: FAIL because the runner module is missing.

- [ ] **Step 3: Implement runner and atomic publication**

Reuse the frozen 240-ticker cohort, causal feature builder, point-in-time group
assignments and market regimes from the existing direction studies. Record
source commit, dirty state, content fingerprints, exact folds, calibration
diagnostics, selected boundaries, full metrics, counterexamples and gate reasons.

- [ ] **Step 4: Run focused and full test suites**

Run: `../../venv/bin/python -m unittest tests.test_asymmetric_tail_risk tests.test_run_asymmetric_tail_risk -v`
Expected: PASS.

Run: `../../venv/bin/python -m unittest discover -s tests -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit implementation before real run**

```bash
git add research/run_asymmetric_tail_risk.py tests/test_run_asymmetric_tail_risk.py docs/modeling-todo.md
git commit -m "research: add asymmetric tail study runner"
```

- [ ] **Step 6: Run the real frozen experiment and inspect reports**

Run: `../../venv/bin/python -m research.run_asymmetric_tail_risk`
Expected: three strict reports, no credential text, no temporary files and an
explicit promoted/rejected decision.

- [ ] **Step 7: Record real evidence and commit reports**

Update `docs/modeling-todo.md` with exact sample counts, fold metrics and decision.
Then run `git diff --check`, focused tests and the full suite once more.

```bash
git add reports/asymmetric-tail-risk.json reports/asymmetric-tail-risk.csv reports/asymmetric-tail-risk.md docs/modeling-todo.md
git commit -m "research: publish asymmetric tail evidence"
```
