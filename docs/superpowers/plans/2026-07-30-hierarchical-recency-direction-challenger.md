# Hierarchical Recency Direction Challenger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and evaluate a leakage-safe 5/20/60-session direction challenger that combines fixed exponential recency weights with fold-frozen market-behavior groups and empirical-Bayes group/ticker shrinkage.

**Architecture:** Add a pure research module for weights, behavior-group freezing, hierarchical posterior corrections, and walk-forward predictions. Reuse the existing executable targets, purged folds, Ridge comparator, expanded research cohort, market-regime splitter, and report conventions; keep every production forecast and web contract unchanged.

**Tech Stack:** Python 3, pandas, NumPy, scikit-learn LogisticRegression/Ridge, `unittest`, SQLite-backed expanded research data, Markdown/CSV/JSON reports.

## Global Constraints

- Preserve `ridge_direction_v1`, `forecast_decision_policy`, every risk veto, API contract, and UI output.
- Use next-session open to horizon close labels with neutral bands 1%/2%/4% for 5/20/60 sessions.
- Every preprocessing, weighting, group freeze, and hierarchy statistic is training-fold only.
- Never backfill current SEC or current unified groups into historical model rows.
- Fixed half-lives are 126/252/504 sessions for 5/20/60 days.
- Full candidate stays `research` with `online_authority=none`, even if the historical metric gate passes.
- Do not stage SQLite WAL/SHM files or `research/high_level_reversal_study.py`.

---

### Task 1: Public leakage-safe direction helpers

**Files:**
- Modify: `research/market_direction_model.py`
- Modify: `tests/test_market_direction_model.py`

**Interfaces:**
- Produces: `direction_labels(returns, horizon) -> numpy.ndarray`.
- Produces: `training_only_design(train, test, columns) -> tuple[numpy.ndarray, numpy.ndarray]`.
- Existing private callers and prediction output remain byte-for-byte equivalent.

- [ ] **Step 1: Write failing helper-contract tests**

Add tests that call the two public helpers, compare them with the existing walk-forward output, verify unsupported horizons fail closed, and prove changing test-only values cannot alter the fitted training transformation.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_market_direction_model -v
```

Expected: import failure because the public helpers do not exist.

- [ ] **Step 3: Add minimal public wrappers and migrate internal calls**

Expose validated public wrappers around the existing `_directions` and `_training_only_design` implementations. Replace internal calls with the public names, retain private aliases only if another existing module imports them, and do not change any estimator settings.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the command from Step 2. Expected: PASS with existing prediction fixtures unchanged.

- [ ] **Step 5: Commit Task 1**

```bash
git add research/market_direction_model.py tests/test_market_direction_model.py
git commit -m "refactor: expose leakage-safe direction helpers"
```

### Task 2: Recency weights and effective-sample diagnostics

**Files:**
- Create: `research/hierarchical_direction.py`
- Create: `tests/test_hierarchical_direction.py`

**Interfaces:**
- Produces: `HALF_LIFE_BY_HORIZON = {5: 126, 20: 252, 60: 504}`.
- Produces: `recency_class_weights(index, labels, horizon) -> tuple[numpy.ndarray, dict]`.
- Diagnostics contain `raw_sample_count`, `weight_sum`, `effective_sample_size`, per-class effective counts, and min/median/max weights.

- [ ] **Step 1: Write failing recency-weight tests**

Cover exact half-life weight `0.5`, monotonic decay, normalized mean `1.0`, time-weighted class balancing, immutable inputs, unsupported horizons, non-finite labels, total effective sample below 1,000, and class effective sample below 100.

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_hierarchical_direction.RecencyWeightTest -v
```

Expected: module or symbol import failure.

- [ ] **Step 3: Implement fixed recency and class weights**

Use sorted unique observation dates to compute session age, calculate
`0.5 ** (age / half_life)`, derive class multipliers from recency-weighted class counts, normalize the combined weights to mean one, and calculate Kish effective sample size as `(sum(w) ** 2) / sum(w ** 2)`.

Return a typed unavailable result with stable reason codes instead of fitting when the frozen effective-sample gates fail.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add research/hierarchical_direction.py tests/test_hierarchical_direction.py
git commit -m "feat: add fixed recency sample weights"
```

### Task 3: Fold-frozen behavior groups

**Files:**
- Modify: `research/hierarchical_direction.py`
- Modify: `tests/test_hierarchical_direction.py`

**Interfaces:**
- Consumes: pandas OHLCV histories, a training cutoff, tickers, and `web.market_groups.SECTOR_ETFS`.
- Produces: `freeze_behavior_groups(histories, tickers, cutoff, sector_etfs=SECTOR_ETFS) -> tuple[dict[str, str | None], dict]`.
- Uses `data.market_behavior.classify_market_behavior` with 126 minimum and 252 maximum common sessions.

- [ ] **Step 1: Write failing causal-group tests**

Construct synthetic SPY/XLK/XLE/stock histories and assert the stock maps to the expected residual-behavior group. Append extreme post-cutoff rows and require identical output. Cover missing SPY, missing sector ETF, fewer than 126 common rows, deterministic ticker ordering, and input immutability.

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_hierarchical_direction.FoldFrozenGroupTest -v
```

Expected: missing group-freeze function.

- [ ] **Step 3: Implement the pandas-to-market-behavior adapter**

Select `Adj Close` when present and otherwise `Close`, truncate at the exact cutoff, convert to `(ISO date, price)` rows, and invoke the existing point-in-time classifier. Record classified/unavailable counts, sector counts, common-day coverage, cutoff, and `market_behavior_v1`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add research/hierarchical_direction.py tests/test_hierarchical_direction.py
git commit -m "feat: freeze causal behavior groups per fold"
```

### Task 4: Hierarchical posterior correction

**Files:**
- Modify: `research/hierarchical_direction.py`
- Modify: `tests/test_hierarchical_direction.py`

**Interfaces:**
- Produces: `fit_hierarchical_priors(labels, weights, tickers, groups, classes) -> HierarchicalPriors`.
- Produces: `adjust_log_probabilities(log_probabilities, tickers, groups, priors, include_group, include_ticker) -> numpy.ndarray`.
- Fixed prior strengths: group `1000.0`, ticker `252.0`.

- [ ] **Step 1: Write failing hierarchy tests**

Verify group posteriors fall back to the global prior without observations, ticker posteriors fall back to their group prior, small samples shrink more strongly than large samples, every posterior sums to one, unseen tickers/groups add zero correction, and group-only versus group-plus-ticker corrections are distinct.

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_hierarchical_direction.HierarchicalPriorTest -v
```

Expected: hierarchy symbols are absent.

- [ ] **Step 3: Implement immutable posterior state and corrections**

Compute weighted global counts, group posteriors around the global prior, and ticker posteriors around the matching group prior. Clip probabilities only at machine-safe epsilon before taking logs. Never mutate input arrays or expose model-internal probabilities as calibrated probabilities.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add research/hierarchical_direction.py tests/test_hierarchical_direction.py
git commit -m "feat: add empirical bayes direction hierarchy"
```

### Task 5: Walk-forward challenger and fixed ablations

**Files:**
- Modify: `research/hierarchical_direction.py`
- Modify: `tests/test_hierarchical_direction.py`

**Interfaces:**
- Produces: `walk_forward_hierarchical_predictions(frame, histories, *, horizon, feature_columns, n_test_folds=5, minimum_samples=1000) -> tuple[pandas.DataFrame, pandas.DataFrame, pandas.DataFrame]`.
- Prediction specifications: `logistic_global`, `logistic_time`, `logistic_group`, `logistic_time_group`, `logistic_time_group_ticker`.
- Returns prediction rows, weight diagnostics, and fold-group diagnostics.

- [ ] **Step 1: Write failing walk-forward tests**

Assert all five candidates share identical `(ticker, observation_date, horizon, fold)` test keys; every training label end precedes the test start; changing test outcomes cannot change predictions; changing future prices cannot change an earlier fold group map; fallback rows remain predicted; and insufficient folds emit typed diagnostics without fabricated predictions.

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_hierarchical_direction.HierarchicalWalkForwardTest -v
```

Expected: missing walk-forward function.

- [ ] **Step 3: Implement the five ablations**

Use six chronological edges to produce exactly five expanding test folds. Fit `LogisticRegression(max_iter=1000, random_state=0, solver="liblinear")`; `logistic_global` and `logistic_group` use manually balanced non-recency weights, while the other candidates use Task 2 weights. Use `predict_log_proba`, reorder columns to the frozen `("down", "neutral", "up")` order, apply Task 4 corrections, and emit deterministic prediction rows.

- [ ] **Step 4: Run focused and existing regression tests**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_hierarchical_direction tests.test_market_direction_model tests.test_run_expanded_walkforward_study -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 5**

```bash
git add research/hierarchical_direction.py tests/test_hierarchical_direction.py
git commit -m "feat: add hierarchical recency walk forward"
```

### Task 6: Study runner, metrics, promotion gate, and reports

**Files:**
- Create: `research/run_hierarchical_recency_direction.py`
- Create: `tests/test_run_hierarchical_recency_direction.py`

**Interfaces:**
- Reuses: expanded cohort selection, `prepare_expanded_frame`, Ridge predictions, market-regime frame, and non-overlapping sample selection.
- Produces: `hierarchical_promotion_decision(metrics, regime_metrics) -> dict`.
- CLI writes the three frozen report files plus diagnostics CSV files.

- [ ] **Step 1: Write failing runner-contract tests**

Test horizons 5/20/60, all fixed comparators, overlapping/non-overlapping metrics, semiconductor/software/other slices, regime slices, named-case diagnostics, exact threshold boundaries, four-of-five fold wins, class-return ordering, ablation increment requirement, and permanent `online_authority="none"`.

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_run_hierarchical_recency_direction -v
```

Expected: runner module is absent.

- [ ] **Step 3: Implement deterministic runner and gate**

Load the fixed cohort plus reference ETFs, attach 5/20/60 targets, generate Ridge/majority and all five challenger predictions on identical folds, aggregate metrics and folds, stratify by evaluation group and causal market regime, then apply every frozen gate from the design document. Keep `eligible=false` regardless of historical metric outcome and expose separate `metric_gate_passed`.

- [ ] **Step 4: Implement atomic report publication**

Write JSON, CSV, and Markdown to sibling temporary files, replace final outputs only after all serialization succeeds, and include study version, git commit, dirty-worktree flag, database content fingerprint, cohort seed, fold boundaries, configuration, weight/group diagnostics, gate reasons, and limitations. Do not include absolute paths or credentials.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 6: Commit Task 6**

```bash
git add research/run_hierarchical_recency_direction.py tests/test_run_hierarchical_recency_direction.py
git commit -m "research: add hierarchical recency direction study"
```

### Task 7: Real experiment, documentation, and final verification

**Files:**
- Create: `reports/hierarchical-recency-direction.json`
- Create: `reports/hierarchical-recency-direction.csv`
- Create: `reports/hierarchical-recency-direction.md`
- Modify: `docs/modeling-todo.md`

**Interfaces:**
- Consumes: `data/research_prices.db` read-only.
- Produces: reproducible frozen evidence and the next global-TODO decision.

- [ ] **Step 1: Run a small smoke study**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python research/run_hierarchical_recency_direction.py --max-tickers 30 --minimum-samples 300 --output-prefix /private/tmp/hierarchical-recency-smoke
```

Expected: all artifacts publish, no production file changes, and report states `online_authority=none`.

- [ ] **Step 2: Run the full frozen study**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python research/run_hierarchical_recency_direction.py
```

Expected: 5/20/60 results, five valid OOS test folds where effective-sample gates permit, complete group/regime diagnostics, and a deterministic gate decision.

- [ ] **Step 3: Audit the result before interpreting it**

Verify identical test-key counts across every comparator, no label-end leakage, no current SEC classification input, finite metrics, correct group/regime totals, no credentials/absolute paths, and stable rerun hashes for JSON/CSV/Markdown.

- [ ] **Step 4: Update the Chinese global TODO**

Record exact cohort dates/counts, candidate and comparator metrics, fold wins, subgroup/regime failures, effective-sample coverage, and the frozen conclusion. Mark the hierarchy experiment complete only when all artifacts and audits pass; do not mark Ridge repaired unless every historical gate and future-shadow requirement passes.

- [ ] **Step 5: Run the full repository test suite**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 6: Review and commit evidence**

Run `git diff --check`, stage only source/tests/reports/TODO, confirm protected runtime files are absent, and commit:

```bash
git commit -m "research: evaluate hierarchical recency direction"
```
