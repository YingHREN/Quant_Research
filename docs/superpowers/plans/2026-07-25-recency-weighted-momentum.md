# Recency-Weighted Momentum Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and evaluate a causal multi-scale recency-weighted momentum challenger for the five-session executable forecast without changing production predictions.

**Architecture:** A focused `research/temporal_momentum.py` module computes causal per-session temporal features from stock, QQQ, and optional sector OHLCV histories. The existing forecast dataset remains frozen; a separate research frame joins the temporal features to it, and a dedicated ablation runner evaluates fixed Ridge/logistic specifications on identical purged walk-forward folds.

**Tech Stack:** Python 3, pandas, NumPy, scikit-learn, SQLite market-history repository, `unittest`.

## Global Constraints

- No dashboard prediction, production factor list, or forecast label changes.
- Features at date \(t\) may use observations only through the close at \(t\).
- Executable five-session targets enter at next-session open and exit at the fifth future close.
- Training preprocessing and clipping remain fit on training folds only.
- Failure of the challenger is a valid result and must not promote it.
- Named MU/NBIS dates are diagnostics only and cannot select parameters.

---

### Task 1: Causal multi-scale decay features

**Files:**
- Create: `research/temporal_momentum.py`
- Create: `tests/test_temporal_momentum.py`

**Interfaces:**
- Produces: `decayed_return(close: pd.Series, start_lag: int, end_lag: int, half_life: float) -> pd.Series`
- Produces: `stock_temporal_features(history: pd.DataFrame) -> pd.DataFrame`
- Produces: `TEMPORAL_STOCK_COLUMNS: tuple[str, ...]`

- [ ] **Step 1: Write failing tests for decay direction and recency**

Add tests using a flat history with a +10% latest return and a +10% older
return. Assert `decay_mom_1_3` reacts more strongly to the latest return and
that appending future observations cannot change a prior feature row.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
../../venv/bin/python -m unittest tests.test_temporal_momentum -v
```

Expected: import failure because `research.temporal_momentum` does not exist.

- [ ] **Step 3: Implement the normalized causal decay**

Implement lagged log returns, weights
`exp(-log(2) * (lag - start_lag) / half_life)`, finite-history masking, and
the five columns from the design:

```python
DECAY_WINDOWS = {
    "decay_mom_1_3": (1, 3, 2.0),
    "decay_mom_4_10": (4, 10, 4.0),
    "decay_mom_11_20": (11, 20, 7.0),
    "decay_mom_21_60": (21, 60, 20.0),
    "decay_mom_1_20": (1, 20, 7.0),
}
```

Return NaN until the entire requested lag window is available and preserve
the input index.

- [ ] **Step 4: Run tests and verify GREEN**

Run the focused test command, then:

```bash
../../venv/bin/python -m unittest tests.test_momentum tests.test_temporal_momentum -v
```

- [ ] **Step 5: Commit**

```bash
git add research/temporal_momentum.py tests/test_temporal_momentum.py
git commit -m "feat: add causal decayed momentum features"
```

### Task 2: Volume and close-location confirmation

**Files:**
- Modify: `research/temporal_momentum.py`
- Modify: `tests/test_temporal_momentum.py`

**Interfaces:**
- Extends: `stock_temporal_features(history)` with
  `decay_volume_confirmation_1_20` and
  `decay_close_location_pressure_1_20`

- [ ] **Step 1: Write failing tests for participation and weak-close sign**

Construct two histories with identical returns but high versus normal volume
on the latest up session. Assert high-volume progress increases
`decay_volume_confirmation_1_20`. Construct a high-volume session closing near
its low and assert `decay_close_location_pressure_1_20` is negative.

- [ ] **Step 2: Run and verify RED**

Run the focused temporal-momentum test module. Expected: missing confirmation
columns.

- [ ] **Step 3: Implement causal confirmation**

Compute expected volume as `Volume.rolling(20, min_periods=10).median().shift(1)`;
clip the ratio to `[0, 5]`. Compute close location as
`2 * (Close - Low) / (High - Low) - 1`, using zero for a zero-range bar.
Apply the 1–20 decay weights to `log_return * volume_ratio` and
`close_location * volume_ratio`.

- [ ] **Step 4: Run focused and full tests**

Run:

```bash
../../venv/bin/python -m unittest tests.test_temporal_momentum -v
../../venv/bin/python -m unittest discover -s tests -q
```

- [ ] **Step 5: Commit**

```bash
git add research/temporal_momentum.py tests/test_temporal_momentum.py
git commit -m "feat: add volume gated temporal momentum"
```

### Task 3: QQQ and sector temporal context

**Files:**
- Modify: `research/temporal_momentum.py`
- Modify: `tests/test_temporal_momentum.py`

**Interfaces:**
- Produces: `temporal_feature_frame(histories: Mapping[str, pd.DataFrame], sector_members: Mapping[str, str | None]) -> pd.DataFrame`
- Produces: `TEMPORAL_FEATURE_COLUMNS: tuple[str, ...]`

- [ ] **Step 1: Write failing alignment and leakage tests**

Use asynchronous stock, QQQ, and sector dates. Assert each stock date uses the
last context observation on or before that exact date only when an exact market
session exists; do not forward-fill a missing market bar. Append a future QQQ
spike and assert an earlier stock row is unchanged.

- [ ] **Step 2: Run and verify RED**

Expected: `temporal_feature_frame` is missing.

- [ ] **Step 3: Implement cross-market context**

Compute the stock and benchmark `decay_mom_1_20` series independently and join
them by exact date. Add:

```text
decay_excess_qqq_1_20
decay_excess_sector_1_20
decay_market_agreement_1_20
```

Agreement is the stock momentum magnitude multiplied by the average of the
QQQ and sector sign matches; it remains NaN if either required context value is
missing. Tickers without a mapped sector retain NaN sector fields.

- [ ] **Step 4: Run focused and full tests**

Run both commands from Task 2 Step 4.

- [ ] **Step 5: Commit**

```bash
git add research/temporal_momentum.py tests/test_temporal_momentum.py
git commit -m "feat: add market gated temporal momentum"
```

### Task 4: Research-only frame and ablation runner

**Files:**
- Create: `research/run_temporal_momentum_study.py`
- Create: `tests/test_run_temporal_momentum_study.py`

**Interfaces:**
- Produces: `build_temporal_research_frame(histories: Mapping[str, pd.DataFrame]) -> pd.DataFrame`
- Produces: `temporal_feature_sets() -> dict[str, tuple[str, ...]]`
- Produces: `evaluate_temporal_scope(frame: pd.DataFrame, scope: str, n_folds: int, minimum_samples: int) -> tuple[pd.DataFrame, pd.DataFrame]`
- Produces: `temporal_promotion_decision(metrics: pd.DataFrame) -> dict[str, object]`

- [ ] **Step 1: Write failing feature-set and promotion tests**

Assert the four specifications are nested exactly:
`ridge_current`, `ridge_decay_only`, `ridge_decay_volume`, and
`ridge_decay_market`; assert a challenger fails promotion if aggregate
balanced accuracy improves but fold-majority improvement or down recall fails.

- [ ] **Step 2: Run and verify RED**

Run:

```bash
../../venv/bin/python -m unittest tests.test_run_temporal_momentum_study -v
```

Expected: module import failure.

- [ ] **Step 3: Implement the research frame and fixed comparisons**

Build the frozen forecast frame with `build_feature_frame`, join
`temporal_feature_frame` by its MultiIndex, attach next-open targets, and call
the existing purged Ridge/logistic helpers. Rename each returned Ridge
specification to its fixed ablation name before concatenation. Add fold-level
metric rows so the promotion decision can require improvement in at least
three of five eligible folds.

- [ ] **Step 4: Run study tests and regression tests**

Run:

```bash
../../venv/bin/python -m unittest tests.test_run_temporal_momentum_study tests.test_market_direction_model -v
../../venv/bin/python -m unittest discover -s tests -q
```

- [ ] **Step 5: Commit**

```bash
git add research/run_temporal_momentum_study.py tests/test_run_temporal_momentum_study.py
git commit -m "research: add temporal momentum ablation"
```

### Task 5: Execute the local study and record evidence

**Files:**
- Create: `docs/research/temporal-momentum-ablation-2026-07-25.md`
- Create: `docs/research/temporal-momentum-ablation-2026-07-25.csv`
- Modify: `docs/modeling-todo.md`

**Interfaces:**
- Consumes: local `data/prices.db` through `MarketDataRepository`
- Produces: human-readable promotion decision, aggregate/fold metrics, and
  MU/NBIS diagnostics

- [ ] **Step 1: Add a report-contract test**

Assert the Markdown report states the data cutoff, executable label, full and
semiconductor scopes, MU/NBIS diagnostics, and explicit `PROMOTE` or
`DO NOT PROMOTE`.

- [ ] **Step 2: Run and verify RED**

Run the report test and verify it fails because the rendering entry point is
missing.

- [ ] **Step 3: Implement CLI and reporting**

Add `--database`, `--report`, `--metrics`, and `--folds` arguments. The report
must state that the challenger remains offline and must include promotion-gate
reasons.

- [ ] **Step 4: Run the real experiment**

Run against the main checkout database without copying WAL files:

```bash
../../venv/bin/python research/run_temporal_momentum_study.py \
  --database ../../data/prices.db \
  --report docs/research/temporal-momentum-ablation-2026-07-25.md \
  --metrics docs/research/temporal-momentum-ablation-2026-07-25.csv
```

Review full-universe, semiconductor, MU, and NBIS results. Mark the TODO as
completed research and record whether the promotion gate passed.

- [ ] **Step 5: Verify and commit**

Run:

```bash
../../venv/bin/python -m unittest discover -s tests -q
git diff --check
```

Then commit:

```bash
git add research/run_temporal_momentum_study.py \
  tests/test_run_temporal_momentum_study.py \
  docs/research/temporal-momentum-ablation-2026-07-25.md \
  docs/research/temporal-momentum-ablation-2026-07-25.csv \
  docs/modeling-todo.md
git commit -m "research: evaluate recency weighted momentum"
```
