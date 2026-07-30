# Point-in-Time Sector Feature Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover broad, leakage-safe sector-relative-strength coverage for the frozen 8,199 tail-direction pairs using monthly price-behavior assignments, then decide whether those features qualify for a later conditional-direction challenger.

**Architecture:** A pure module computes monthly residual-correlation assignments from prices available at each cutoff and makes them effective only on the next stock session. It then attaches exact-date 20-session stock/sector/QQQ relative-strength features with a 45-calendar-day staleness bound. A separate offline runner reuses the already frozen matched pair keys and existing statistical gate, publishes strict research artifacts, and grants no online authority.

**Tech Stack:** Python 3, pandas, NumPy, existing `data.market_behavior` semantics, SQLite research histories, `unittest`.

## Global Constraints

- Candidate proxies are the 11 standard sector ETFs plus `SOXX` as `semiconductor` and `IGV` as `software`.
- Monthly classification uses 126–252 common return observations through the final completed session of the month.
- A classification is never effective on its own cutoff date; it begins on the next recorded stock session.
- An assignment older than 45 calendar days is unavailable.
- Stock, proxy, and QQQ returns require exact observation and 20-stock-session start dates; no adjacent-date filling.
- Existing static sector features remain unchanged.
- The primary audit reuses exactly the existing 8,199 unique `case_key/control_key` pairs.
- Feature admission retains 90% class coverage, absolute effect 0.20, bootstrap interval excluding zero, 4/5 fold direction stability, and 2/3 large-group stability.
- Every outer fold must separately have at least 85% pair availability.
- Present-day SEC or `group_assignments` data may not classify historical rows.
- `lifecycle=research`, `online_authority=none`; no Ridge, policy, UI, or veto changes.

---

### Task 1: Monthly causal behavior assignments

**Files:**
- Create: `research/point_in_time_sector_features.py`
- Create: `tests/test_point_in_time_sector_features.py`

**Interfaces:**
- Produces: `PIT_SECTOR_CANDIDATES: Mapping[str, str]`
- Produces: `build_monthly_behavior_assignments(histories: Mapping[str, pandas.DataFrame], tickers: Iterable[str], *, start_date=None, minimum_observations: int = 126, maximum_observations: int = 252, maximum_age_days: int = 45) -> pandas.DataFrame`
- Output columns: `ticker`, `classification_date`, `effective_from`, `expires_after`, `sector_key`, `benchmark_ticker`, `residual_correlation`, `residual_beta`, `common_days`, `rule_version`.

- [ ] **Step 1: Write failing cutoff and next-session tests**

```python
def test_month_end_assignment_starts_on_next_stock_session():
    assignments = build_monthly_behavior_assignments(
        synthetic_histories(),
        ("AAA",),
        minimum_observations=4,
        maximum_observations=6,
    )
    row = assignments.iloc[-1]
    self.assertLess(row["classification_date"], row["effective_from"])
    self.assertEqual(
        row["effective_from"],
        next_stock_session("AAA", row["classification_date"]),
    )
```

Also assert one row per ticker/month, exact 126 inclusive availability, 125 rejection, at most 252 observations, and `expires_after = classification_date + 45 days`.

- [ ] **Step 2: Verify RED**

Run:

```bash
LOKY_MAX_CPU_COUNT=8 PYTHONWARNINGS=error ../../venv/bin/python -m unittest \
  tests.test_point_in_time_sector_features.MonthlyAssignmentTest -v
```

Expected: import failure because the module does not exist.

- [ ] **Step 3: Implement rolling residual classification**

Normalize OHLCV histories without mutation and precompute daily close returns. For each ticker/proxy pair, align ticker, SPY, and proxy returns on exact common dates. For each month-end cutoff, use the final `maximum_observations` rows and compute:

```python
stock_beta = cov(stock, spy) / var(spy)
proxy_beta = cov(proxy, spy) / var(spy)
stock_residual = stock - stock_beta * spy
proxy_residual = proxy - proxy_beta * spy
correlation = corr(stock_residual, proxy_residual)
residual_beta = cov(stock_residual, proxy_residual) / var(proxy_residual)
```

Select the maximum `(correlation, sector_key)` exactly like `market_behavior_v1`. Store only finite candidates with enough observations. Resolve `effective_from` from the ticker’s first session strictly after the cutoff.

- [ ] **Step 4: Write failing determinism and future-invariance tests**

```python
def test_appended_future_prices_do_not_change_existing_assignments():
    before = build_monthly_behavior_assignments(base_histories(), ("AAA",))
    after = build_monthly_behavior_assignments(
        histories_with_future_spike(), ("AAA",)
    )
    pd.testing.assert_frame_equal(
        before,
        after.loc[
            after["classification_date"]
            <= before["classification_date"].max()
        ].reset_index(drop=True),
    )
```

Also compare a selected classification date against direct
`classify_market_behavior`, reject duplicate dates and malformed prices, and verify inputs remain unchanged.

- [ ] **Step 5: Run Task 1 tests and commit**

```bash
LOKY_MAX_CPU_COUNT=8 PYTHONWARNINGS=error ../../venv/bin/python -m unittest \
  tests.test_point_in_time_sector_features.MonthlyAssignmentTest -v
git add research/point_in_time_sector_features.py \
  tests/test_point_in_time_sector_features.py
git commit -m "feat: build monthly point-in-time sector assignments"
```

### Task 2: Exact-date sector-relative feature frame

**Files:**
- Modify: `research/point_in_time_sector_features.py`
- Modify: `tests/test_point_in_time_sector_features.py`

**Interfaces:**
- Produces: `build_point_in_time_sector_features(histories: Mapping[str, pandas.DataFrame], assignments: pandas.DataFrame, observation_index: pandas.MultiIndex) -> pandas.DataFrame`
- Output uses the same `ticker, observation_date` index and columns `pit_sector_relative_strength_20`, `pit_stock_sector_relative_strength_20`, `pit_sector_assignment_age_days`, `pit_sector_residual_correlation`, `pit_sector_assignment_available`, `pit_sector_key`, `pit_sector_benchmark`, `pit_sector_unavailable_reason`.

- [ ] **Step 1: Write failing exact-date feature tests**

```python
def test_relative_returns_share_exact_stock_endpoints():
    result = build_point_in_time_sector_features(
        histories_with_known_returns(),
        assignments_effective_before_observation(),
        requested_index(),
    )
    row = result.loc[("AAA", pd.Timestamp("2026-03-31"))]
    self.assertAlmostEqual(
        row["pit_stock_sector_relative_strength_20"],
        known_stock_return - known_proxy_return,
    )
    self.assertAlmostEqual(
        row["pit_sector_relative_strength_20"],
        known_proxy_return - known_qqq_return,
    )
```

Add tests for same-day assignment rejection, next-session inclusion, 45-day inclusive validity, 46-day staleness, missing exact proxy/QQQ endpoint, unknown proxy, duplicate assignment interval, future append invariance, and output-index preservation.

- [ ] **Step 2: Verify RED**

Run:

```bash
LOKY_MAX_CPU_COUNT=8 PYTHONWARNINGS=error ../../venv/bin/python -m unittest \
  tests.test_point_in_time_sector_features.PointInTimeSectorFeatureTest -v
```

Expected: failure because the feature builder is missing.

- [ ] **Step 3: Implement bounded as-of assignment and exact returns**

For each ticker, sort observation rows and use `merge_asof` against
`effective_from` with backward direction. Reject assignments when
`observation_date > expires_after`. Determine the start date as the stock’s
20th prior recorded session; look up proxy and QQQ close at exactly that start
date and the observation date. Emit a stable unavailable reason for every
missing case.

- [ ] **Step 4: Run all sector-feature tests and commit**

```bash
LOKY_MAX_CPU_COUNT=8 PYTHONWARNINGS=error ../../venv/bin/python -m unittest \
  tests.test_point_in_time_sector_features -v
git add research/point_in_time_sector_features.py \
  tests/test_point_in_time_sector_features.py
git commit -m "feat: compute causal sector relative strength"
```

### Task 3: Fixed-pair recovery audit runner

**Files:**
- Create: `research/run_point_in_time_sector_recovery.py`
- Create: `tests/test_run_point_in_time_sector_recovery.py`
- Modify: `docs/modeling-todo.md`

**Interfaces:**
- Produces: `attach_recovered_features_to_pairs(pairs: pandas.DataFrame, features: pandas.DataFrame) -> pandas.DataFrame`
- Produces: `evaluate_sector_recovery(pairs: pandas.DataFrame, features: pandas.DataFrame, *, bootstrap_samples: int = 2_000, bootstrap_block_days: int = 20, seed: int = 20260730) -> tuple[pandas.DataFrame, pandas.DataFrame, dict]`
- Produces: `run_recovery(...) -> dict` and five strict report artifacts.

- [ ] **Step 1: Write failing fixed-key and gate tests**

```python
def test_recovery_reuses_frozen_pairs_without_rematching():
    enriched = attach_recovered_features_to_pairs(
        frozen_pairs(), feature_rows()
    )
    self.assertEqual(
        enriched[["case_key", "control_key"]].to_records(index=False).tolist(),
        frozen_pairs()[
            ["case_key", "control_key"]
        ].to_records(index=False).tolist(),
    )
```

Add tests that duplicate or missing frozen keys fail closed, each fold coverage is reported, an 84.99% fold rejects admission, all five folds at 85% permit the existing global gate to decide, and `online_authority` is always `none`.

- [ ] **Step 2: Implement fixed-pair evaluation**

Load `reports/tail-direction-counterexample-audit-pairs.csv` as the immutable
pair cohort. Merge case/control features by exact keys, then call
`paired_feature_evidence` with only:

```python
{
    "pit_sector_relative_strength_20": "numeric",
    "pit_stock_sector_relative_strength_20": "numeric",
    "pit_sector_assignment_age_days": "numeric",
    "pit_sector_residual_correlation": "numeric",
}
```

Apply `admitted_feature_hypotheses`, then add the five-fold 85% availability
gate to the final decision. Assignment age and correlation are diagnostics and
must never be admitted as directional features even if their effect passes.

- [ ] **Step 3: Implement strict publication and Chinese report**

Publish:

```text
reports/point-in-time-sector-recovery.json
reports/point-in-time-sector-recovery-assignments.csv
reports/point-in-time-sector-recovery-coverage.csv
reports/point-in-time-sector-recovery-features.csv
reports/point-in-time-sector-recovery.md
```

The manifest records candidate proxies, refresh rule, exact return endpoints,
source commit, dirty state, database content fingerprint, original pair cohort
fingerprint, per-fold coverage, feature evidence, unavailable reasons, and
`online_authority=none`. Validate with the existing strict report validator and
atomic temporary-file replacement.

- [ ] **Step 4: Run focused tests and commit**

```bash
LOKY_MAX_CPU_COUNT=8 PYTHONWARNINGS=error ../../venv/bin/python -m unittest \
  tests.test_point_in_time_sector_features \
  tests.test_run_point_in_time_sector_recovery -v
git add research/run_point_in_time_sector_recovery.py \
  tests/test_run_point_in_time_sector_recovery.py docs/modeling-todo.md
git commit -m "research: add point-in-time sector recovery audit"
```

### Task 4: Real 240-stock recovery experiment and integration

**Files:**
- Create: `reports/point-in-time-sector-recovery*.{json,csv,md}`
- Modify: `docs/modeling-todo.md`

**Interfaces:**
- Consumes current `data/research_prices.db` read-only and the frozen 8,199 pair report.
- Produces research-only evidence and a decision about later conditional-direction modeling.

- [ ] **Step 1: Run pre-experiment full verification**

```bash
LOKY_MAX_CPU_COUNT=8 PYTHONWARNINGS=error \
PYTHONPYCACHEPREFIX=/private/tmp/pit-sector-pre-pycache \
../../venv/bin/python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 2: Freeze clean source and run the real audit**

Commit all implementation first and require an empty feature-worktree status.
Then run:

```bash
LOKY_MAX_CPU_COUNT=8 PYTHONWARNINGS=error \
PYTHONPYCACHEPREFIX=/private/tmp/pit-sector-real-pycache \
../../venv/bin/python research/run_point_in_time_sector_recovery.py \
  --database ../../data/research_prices.db \
  --pairs reports/tail-direction-counterexample-audit-pairs.csv \
  --output-prefix reports/point-in-time-sector-recovery
```

- [ ] **Step 3: Validate evidence**

Strict-load JSON with non-finite constants rejected; verify the report source
commit and clean flag; verify exactly 8,199 unique pair keys remain; verify no
assignment is effective on or before its classification date; verify all used
assignment ages are at most 45 days; scan all artifacts for absolute local
paths and credential shapes; verify no temporary artifact remains.

- [ ] **Step 4: Update TODO with exact result and rerun all tests**

Record total and per-fold coverage, admitted or rejected features, effect sizes,
confidence intervals, data limitations, report paths, source commit, and
`online_authority=none`. Run:

```bash
LOKY_MAX_CPU_COUNT=8 PYTHONWARNINGS=error \
PYTHONPYCACHEPREFIX=/private/tmp/pit-sector-final-pycache \
../../venv/bin/python -m unittest discover -s tests -v
```

- [ ] **Step 5: Commit evidence and integrate safely**

```bash
git add reports/point-in-time-sector-recovery* docs/modeling-todo.md
git commit -m "research: publish point-in-time sector recovery evidence"
```

If `main` is clean, merge current `main` into the feature branch when needed,
rerun affected tests, then fast-forward `main`. If `main` still contains
uncommitted concurrent work, preserve it and leave the verified branch intact
for a later safe merge.
