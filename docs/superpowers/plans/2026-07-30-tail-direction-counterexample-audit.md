# Tail Direction Counterexample Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic point-in-time matched audit that separates high-down-score extreme winners from true terminal downs and decides whether any existing daily feature is stable enough to preregister for a later conditional-direction challenger.

**Architecture:** Extract the existing asymmetric study prediction construction into a reusable in-memory builder without changing model semantics. A new pure analysis module assigns mutually exclusive outcome states, performs exact-stratum one-to-one matching, computes paired feature evidence, and applies a frozen feature-admission gate. A separate runner publishes strict research-only artifacts and never changes Ridge, policy, UI, or online authority.

**Tech Stack:** Python 3, pandas, NumPy, SciPy-free deterministic statistics, scikit-learn only through the existing asymmetric study, SQLite research repository, `unittest`.

## Global Constraints

- Observation date `t` enters at `t+1` open and exits at the fifth future session close; future five-session lows define path stress.
- Audit population is mature outer predictions with calibrated down probability at least `0.40`.
- Outcome precedence is `terminal_down`, `extreme_up`, `path_only_stress`, then `other`.
- Matching is one-to-one without replacement within outer fold, group, and regime; calendar distance must be at most 63 days.
- Matching minimizes date distance, then realized-volatility distance, then ticker identity in deterministic order.
- Earnings proximity remains explicitly unavailable because current coverage is zero; market capitalization remains explicitly unavailable.
- Feature admission requires at least 1,000 pairs, at least 90% availability in each class, absolute standardized difference at least 0.20, a date-block bootstrap interval excluding zero, at least four folds with the same direction, and at least two of three large groups with the same direction.
- Economic outcomes remain raw and unwinsorized.
- `lifecycle=research` and `online_authority=none`; no UI or decision-policy changes.
- Reports must contain only strict finite JSON values or explicit missing states, no absolute local paths, secrets, or temporary artifacts.

---

### Task 1: Reusable asymmetric-study prediction dataset

**Files:**
- Modify: `research/run_asymmetric_tail_risk.py`
- Modify: `tests/test_run_asymmetric_tail_risk.py`

**Interfaces:**
- Produces: `build_tail_study_dataset(*, database: str, start_date: str, max_tickers: int, seed: int, minimum_samples: int) -> dict`
- Returned mapping keys: `predictions: pandas.DataFrame`, `feature_frame: pandas.DataFrame`, `histories: dict`, `groups: dict`, `analysis_tickers: tuple[str, ...]`, `regimes: pandas.DataFrame`, `metadata: dict`
- Existing `run_study(...) -> tuple[pandas.DataFrame, pandas.DataFrame, dict]` keeps its public behavior and consumes this builder.

- [ ] **Step 1: Write a failing extraction contract test**

```python
def test_run_study_uses_reusable_dataset_without_changing_public_result(self):
    dataset = {
        "predictions": self.predictions.copy(),
        "feature_frame": self.features.copy(),
        "histories": self.histories,
        "groups": {"AAA": "software"},
        "analysis_tickers": ("AAA",),
        "regimes": self.regimes.copy(),
        "metadata": {"latest_date": "2026-07-29"},
    }
    with mock.patch.object(runner, "build_tail_study_dataset", return_value=dataset):
        metrics, counterexamples, manifest = runner.run_study(
            database="research.db", minimum_samples=10
        )
    self.assertEqual(manifest["prediction_count"], len(self.predictions))
    self.assertIn("ticker", counterexamples)
```

- [ ] **Step 2: Run the focused test and confirm the missing builder failure**

Run:

```bash
PYTHONWARNINGS=error ../../venv/bin/python -m unittest \
  tests.test_run_asymmetric_tail_risk.AsymmetricTailRunnerTest.test_run_study_uses_reusable_dataset_without_changing_public_result -v
```

Expected: failure because `build_tail_study_dataset` does not exist.

- [ ] **Step 3: Extract the existing construction without altering semantics**

Implement the exact signature:

```python
def build_tail_study_dataset(
    *,
    database="data/research_prices.db",
    start_date="2018-01-01",
    max_tickers=240,
    seed=20260726,
    minimum_samples=1_000,
):
    ...
    return {
        "predictions": predictions,
        "feature_frame": feature_frame,
        "histories": histories,
        "groups": normalized_groups,
        "analysis_tickers": tuple(analysis_tickers),
        "regimes": regimes,
        "metadata": metadata,
    }
```

Move only the repository load, feature/target construction, nested prediction, baseline merge, group attachment, regime attachment, and point-in-time context attachment into the builder. Leave metric evaluation, promotion decision, Git state, manifest rendering, and publication in `run_study`.

- [ ] **Step 4: Run existing asymmetric runner and model tests**

Run:

```bash
PYTHONWARNINGS=error ../../venv/bin/python -m unittest \
  tests.test_asymmetric_tail_risk tests.test_run_asymmetric_tail_risk -v
```

Expected: all pass with unchanged report semantics.

- [ ] **Step 5: Commit the extraction**

```bash
git add research/run_asymmetric_tail_risk.py tests/test_run_asymmetric_tail_risk.py
git commit -m "refactor: expose tail study dataset"
```

### Task 2: Mutually exclusive audit population and deterministic matching

**Files:**
- Create: `research/tail_direction_counterexample_audit.py`
- Create: `tests/test_tail_direction_counterexample_audit.py`

**Interfaces:**
- Produces: `build_audit_population(predictions: pandas.DataFrame, feature_frame: pandas.DataFrame, histories: Mapping[str, pandas.DataFrame]) -> pandas.DataFrame`
- Produces: `match_extreme_up_to_terminal_down(population: pandas.DataFrame, *, maximum_calendar_days: int = 63) -> tuple[pandas.DataFrame, pandas.DataFrame]`
- Produces: `AUDIT_FEATURE_TYPES: Mapping[str, str]`, the frozen numeric/boolean feature registry consumed by Task 3.
- Match rows contain `pair_id`, case/control ticker and date, fold, group, regime, date distance, volatility distance, plus suffixed audit features.
- Coverage rows contain stable status codes and counts by fold, group, and regime.

- [ ] **Step 1: Write failing tests for label precedence and point-in-time merge**

```python
def test_population_assigns_mutually_exclusive_outcomes_in_frozen_order():
    predictions = prediction_rows(
        terminal=(-0.06, 0.12, 0.02, 0.01),
        mae=(-0.10, -0.20, -0.08, -0.02),
        down_score=(0.5, 0.5, 0.5, 0.5),
    )
    result = build_audit_population(
        predictions, feature_rows(), point_in_time_histories()
    )
    self.assertEqual(
        result["outcome_state"].tolist(),
        ["terminal_down", "extreme_up", "path_only_stress", "other"],
    )
    self.assertFalse(result.duplicated(["ticker", "observation_date"]).any())
```

Also assert that a future row appended after an observation date cannot change any already-built observation feature, duplicated keys fail closed, and scores below 0.40 are excluded.

- [ ] **Step 2: Run the population tests and confirm import failure**

Run:

```bash
PYTHONWARNINGS=error ../../venv/bin/python -m unittest \
  tests.test_tail_direction_counterexample_audit.AuditPopulationTest -v
```

Expected: failure because the module is absent.

- [ ] **Step 3: Implement validated audit population construction**

Use constants:

```python
HIGH_DOWN_SCORE = 0.40
TERMINAL_DOWN = -0.05
EXTREME_UP = 0.10
PATH_STRESS = -0.07
```

Merge only exact `ticker, observation_date` keys. Copy the Ridge v4 atomic fields and derive only observation-day `opening_gap`, `log_dollar_volume_20`, `dollar_volume_ratio_20`, and `realized_volatility_change_20` from histories truncated through that date. Represent unavailable earnings and market cap with status columns, not numeric zeroes.

- [ ] **Step 4: Write failing one-to-one matching tests**

```python
def test_matching_is_exact_stratum_bounded_unique_and_deterministic():
    population = matching_fixture()
    first_pairs, first_coverage = match_extreme_up_to_terminal_down(population)
    second_pairs, second_coverage = match_extreme_up_to_terminal_down(population)
    pd.testing.assert_frame_equal(first_pairs, second_pairs)
    pd.testing.assert_frame_equal(first_coverage, second_coverage)
    self.assertTrue((first_pairs["calendar_distance_days"] <= 63).all())
    self.assertFalse(first_pairs["control_key"].duplicated().any())
    self.assertTrue(
        (first_pairs["case_fold"] == first_pairs["control_fold"]).all()
    )
```

Include tests for the inclusive 63-day boundary, a 64-day rejection, missing volatility ordered after finite candidates, and no cross-group/regime/fold fallback.

- [ ] **Step 5: Implement deterministic greedy matching**

Sort cases by `fold, group, regime, observation_date, ticker`. For each case, filter unused controls in the exact stratum and 63-day window, then sort candidates by:

```python
(
    abs(control_date - case_date).days,
    finite_volatility_distance_or_infinity,
    control_date,
    control_ticker,
)
```

Assign sequential `pair_id`; never reuse a control. Emit explicit unmatched reasons such as `no_exact_stratum_control`, `outside_date_window`, and `control_exhausted`.

- [ ] **Step 6: Run Task 2 tests**

Run:

```bash
PYTHONWARNINGS=error ../../venv/bin/python -m unittest \
  tests.test_tail_direction_counterexample_audit -v
```

Expected: all population and matching tests pass.

- [ ] **Step 7: Commit population and matching**

```bash
git add research/tail_direction_counterexample_audit.py \
  tests/test_tail_direction_counterexample_audit.py
git commit -m "feat: match tail direction counterexamples"
```

### Task 3: Paired feature evidence and frozen admission gate

**Files:**
- Modify: `research/tail_direction_counterexample_audit.py`
- Modify: `tests/test_tail_direction_counterexample_audit.py`

**Interfaces:**
- Produces: `paired_feature_evidence(pairs: pandas.DataFrame, *, feature_types: Mapping[str, str], bootstrap_samples: int = 2_000, bootstrap_block_days: int = 20, seed: int = 20260730) -> pandas.DataFrame`
- Produces: `admitted_feature_hypotheses(evidence: pandas.DataFrame) -> tuple[str, ...]`
- Evidence contains finite-or-null metrics, missing rates, paired effect, confidence interval, BH-adjusted p-value, fold direction count, and large-group direction count.

- [ ] **Step 1: Write failing numeric and boolean evidence tests**

```python
def test_numeric_evidence_reports_paired_effect_and_deterministic_interval():
    pairs = paired_fixture(case_values=[3, 4, 5], control_values=[1, 2, 3])
    first = paired_feature_evidence(
        pairs, feature_types={"realized_vol_63": "numeric"},
        bootstrap_samples=200, seed=7,
    )
    second = paired_feature_evidence(
        pairs, feature_types={"realized_vol_63": "numeric"},
        bootstrap_samples=200, seed=7,
    )
    pd.testing.assert_frame_equal(first, second)
    self.assertGreater(first.loc[0, "paired_median_difference"], 0)
```

Add exact tests for boolean rate difference, zero pooled variance, missing values, no usable pairs, and input immutability.

- [ ] **Step 2: Implement paired summaries and date-block bootstrap**

Compute standardized mean difference as paired mean difference divided by the sample standard deviation of paired differences. Bootstrap unique observation-date blocks rather than individual rows; preserve every pair assigned to a sampled block. Use a fixed RNG and percentile 2.5%/97.5% bounds. If the variance or sample size is insufficient, emit `effect_unavailable` rather than infinity.
Compute a two-sided bootstrap p-value as
`2 * min((replicate_effect <= 0).mean(), (replicate_effect >= 0).mean())`,
clipped to `[0, 1]`, before applying BH correction.

- [ ] **Step 3: Write failing BH and admission-gate tests**

```python
def test_gate_requires_every_preregistered_condition():
    evidence = qualifying_evidence_row(
        pair_count=1000,
        case_availability=0.90,
        control_availability=0.90,
        standardized_difference=0.20,
        ci_low=0.01,
        ci_high=0.30,
        consistent_folds=4,
        consistent_large_groups=2,
    )
    self.assertEqual(admitted_feature_hypotheses(evidence), ("feature_a",))
    for field, value in (
        ("pair_count", 999),
        ("case_availability", 0.899),
        ("standardized_difference", 0.199),
        ("ci_low", -0.001),
        ("consistent_folds", 3),
        ("consistent_large_groups", 1),
    ):
        rejected = evidence.copy()
        rejected.loc[0, field] = value
        self.assertEqual(admitted_feature_hypotheses(rejected), ())
```

Test Benjamini–Hochberg monotonicity after sorting raw p-values and verify future-derived forbidden columns are rejected by name and provenance.

- [ ] **Step 4: Implement BH correction, stability slices, and gate reasons**

For each feature, compute its global direction sign. Count outer folds and predeclared groups (`semiconductor`, `software`, `other`) whose paired effect has the same nonzero sign. Emit one stable failed-condition code per unmet gate, and return admitted features sorted lexicographically.

- [ ] **Step 5: Run Task 3 tests**

Run:

```bash
PYTHONWARNINGS=error ../../venv/bin/python -m unittest \
  tests.test_tail_direction_counterexample_audit -v
```

Expected: all evidence and gate tests pass without warnings.

- [ ] **Step 6: Commit statistics and gate**

```bash
git add research/tail_direction_counterexample_audit.py \
  tests/test_tail_direction_counterexample_audit.py
git commit -m "feat: evaluate tail direction feature evidence"
```

### Task 4: Strict research runner and reports

**Files:**
- Create: `research/run_tail_direction_counterexample_audit.py`
- Create: `tests/test_run_tail_direction_counterexample_audit.py`
- Modify: `docs/modeling-todo.md`

**Interfaces:**
- Produces: `run_audit(...) -> tuple[pandas.DataFrame, pandas.DataFrame, pandas.DataFrame, dict]`
- Produces: `run_audit_from_dataset(dataset: Mapping) -> tuple[pandas.DataFrame, pandas.DataFrame, pandas.DataFrame, dict]` for deterministic offline tests.
- Produces: `publish_audit_reports(prefix, pairs, coverage, evidence, manifest, markdown) -> dict[str, pathlib.Path]`
- CLI defaults to `data/research_prices.db` and `reports/tail-direction-counterexample-audit`.

- [ ] **Step 1: Write failing runner contract tests**

```python
def test_runner_is_research_only_and_preserves_unavailable_data_states():
    pairs, coverage, evidence, manifest = run_audit_from_dataset(
        synthetic_tail_dataset()
    )
    self.assertEqual(manifest["decision"]["online_authority"], "none")
    self.assertEqual(manifest["data_availability"]["earnings_proximity"],
                     "unavailable")
    self.assertEqual(manifest["data_availability"]["market_cap"],
                     "unavailable")
    self.assertNotIn("ui", manifest["decision"]["authorized_consumers"])
```

Add tests for strict JSON (`allow_nan=False`), absolute-path redaction, credential-shaped text rejection, atomic replacement, no temporary remnants, sorted deterministic outputs, and explicit `no_features_admitted`.

- [ ] **Step 2: Implement runner data flow**

Call `build_tail_study_dataset`, `build_audit_population`,
`match_extreme_up_to_terminal_down`, `paired_feature_evidence`, and
`admitted_feature_hypotheses` in that order. Include source commit, dirty state,
database content fingerprint, outer-fold boundaries, score threshold, outcome
thresholds, matching rules, coverage, unavailable fields, and all gate reasons
in the manifest.

- [ ] **Step 3: Implement atomic report publication**

Publish:

```text
reports/tail-direction-counterexample-audit.json
reports/tail-direction-counterexample-audit-pairs.csv
reports/tail-direction-counterexample-audit-coverage.csv
reports/tail-direction-counterexample-audit-features.csv
reports/tail-direction-counterexample-audit.md
```

The Markdown conclusion must state whether current daily data support a later
conditional-direction challenger, list admitted feature hypotheses or say none,
and explicitly state that neither earnings proximity nor true market cap was
available.

- [ ] **Step 4: Update the global Chinese TODO**

Under FCAST-001, add a checked implementation line only after the real report
exists. Record exact population, pair count, unmatched count, admitted features,
source commit, decision, and `online_authority=none`. Do not mark the later
two-stage model complete.

- [ ] **Step 5: Run focused runner tests**

Run:

```bash
PYTHONWARNINGS=error ../../venv/bin/python -m unittest \
  tests.test_tail_direction_counterexample_audit \
  tests.test_run_tail_direction_counterexample_audit -v
```

Expected: all pass and no warning output.

- [ ] **Step 6: Commit runner and TODO framework**

```bash
git add research/run_tail_direction_counterexample_audit.py \
  tests/test_run_tail_direction_counterexample_audit.py docs/modeling-todo.md
git commit -m "research: add tail direction counterexample audit"
```

### Task 5: Real point-in-time audit, evidence freeze, and integration

**Files:**
- Create: `reports/tail-direction-counterexample-audit.json`
- Create: `reports/tail-direction-counterexample-audit-pairs.csv`
- Create: `reports/tail-direction-counterexample-audit-coverage.csv`
- Create: `reports/tail-direction-counterexample-audit-features.csv`
- Create: `reports/tail-direction-counterexample-audit.md`
- Modify: `docs/modeling-todo.md`

**Interfaces:**
- Consumes the frozen CLI and current `data/research_prices.db`.
- Produces immutable research evidence; no online consumer.

- [ ] **Step 1: Run the pre-experiment full suite**

Run:

```bash
LOKY_MAX_CPU_COUNT=8 PYTHONWARNINGS=error \
PYTHONPYCACHEPREFIX=/private/tmp/tail-direction-audit-pycache \
../../venv/bin/python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 2: Commit code before the real run**

Ensure `git status --short` is empty, then record `git rev-parse HEAD`. The
report `source_commit` must equal this commit and `dirty_worktree` must be false.

- [ ] **Step 3: Run the real audit**

Run:

```bash
LOKY_MAX_CPU_COUNT=8 PYTHONWARNINGS=error \
PYTHONPYCACHEPREFIX=/private/tmp/tail-direction-audit-real-pycache \
../../venv/bin/python research/run_tail_direction_counterexample_audit.py \
  --database data/research_prices.db \
  --start-date 2018-01-01 \
  --max-tickers 240 \
  --minimum-samples 1000 \
  --output-prefix reports/tail-direction-counterexample-audit
```

Expected: five strict report artifacts and an explicit admission decision.

- [ ] **Step 4: Validate real artifacts**

Run a strict JSON load with non-finite constants rejected, scan every artifact
for API-key/secret shapes and absolute home paths, verify unique pair IDs and
control keys, verify every match respects exact strata and the 63-day bound,
and verify the TODO numbers exactly match the reports.

- [ ] **Step 5: Run post-experiment focused and full tests**

Run:

```bash
PYTHONWARNINGS=error ../../venv/bin/python -m unittest \
  tests.test_asymmetric_tail_risk \
  tests.test_run_asymmetric_tail_risk \
  tests.test_tail_direction_counterexample_audit \
  tests.test_run_tail_direction_counterexample_audit -v

LOKY_MAX_CPU_COUNT=8 PYTHONWARNINGS=error \
PYTHONPYCACHEPREFIX=/private/tmp/tail-direction-audit-final-pycache \
../../venv/bin/python -m unittest discover -s tests -v
```

Expected: both commands pass.

- [ ] **Step 6: Commit evidence and merge only after verification**

```bash
git add reports/tail-direction-counterexample-audit* docs/modeling-todo.md
git commit -m "research: publish tail direction audit evidence"
```

Merge current `main` into the feature branch if it advanced, rerun affected
tests, then fast-forward `main`. Preserve all pre-existing untracked database
WAL/SHM files and `research/high_level_reversal_study.py`.
