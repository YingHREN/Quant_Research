# Unified Downside Walkforward v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a leakage-safe offline benchmark that compares Ridge, the existing downside rules, TOPRISK, Logistic challengers, and ablations on identical point-in-time test rows.

**Architecture:** A pure research module owns next-open path labels, exact-key model alignment, stratified metrics, paired fold comparisons, and evidence-group ablations. A separate runner reuses existing data/model builders, point-in-time group assignments, and market regimes, then atomically publishes JSON, CSV, and Markdown without changing online policy.

**Tech Stack:** Python 3, pandas, NumPy, scikit-learn through existing model modules, SQLite research repository, `unittest`.

## Global Constraints

- Signals are generated with information available at the observation-date close and execute at the next trading-day open.
- Use 5/10/20-session horizons with fixed MAE event thresholds of -5%/-7.5%/-10%.
- Every paired comparison must use identical `ticker × observation_date × horizon × fold` keys.
- Historical groups use persisted `[effective_from, effective_to)` assignments; unknown dates remain `unclassified`.
- Missing model output is `unavailable`, never an implicit negative signal.
- Binary rule scores are not probabilities and must not produce fabricated calibration or AUC metrics.
- No experiment may mutate price data, analysis caches, production thresholds, or `forecast_decision_policy`.
- Use test-first red-green-refactor cycles and commit each task independently.

---

### Task 1: Executable Next-Open Path Labels

**Files:**
- Create: `research/unified_downside_benchmark.py`
- Create: `tests/test_unified_downside_benchmark.py`

**Interfaces:**
- Consumes: OHLCV frame with `ticker`, `observation_date`, `Open`, `High`, `Low`, `Close`.
- Produces: `attach_next_open_path_targets(frame: pd.DataFrame, horizons: tuple[int, ...] = (5, 10, 20), adverse_thresholds: Mapping[int, float] | None = None) -> pd.DataFrame`.

- [ ] **Step 1: Write failing label tests**

At the top of the test file define the deterministic fixture:

```python
def example_prices(extra_rows=0):
    dates = pd.bdate_range("2026-01-02", periods=5 + extra_rows)
    values = [100.0, 102.0, 104.0, 103.0, 105.0, 106.0, 107.0]
    lows = [99.0, 100.0, 95.0, 101.0, 103.0, 104.0, 105.0]
    highs = [101.0, 103.0, 106.0, 105.0, 107.0, 108.0, 109.0]
    frame = pd.DataFrame(
        {
            "ticker": "AAA",
            "observation_date": dates,
            "Open": values[: len(dates)],
            "High": highs[: len(dates)],
            "Low": lows[: len(dates)],
            "Close": values[: len(dates)],
        }
    )
    return frame.set_index(["ticker", "observation_date"])
```

```python
def test_next_open_targets_use_future_open_and_drop_immature_tail():
    labeled = attach_next_open_path_targets(example_prices(), horizons=(2,))
    row = labeled.loc[("AAA", pd.Timestamp("2026-01-02"), 2)]
    assert row["entry_open"] == 102.0
    assert row["terminal_return"] == 104.0 / 102.0 - 1.0
    assert row["mae"] == 95.0 / 102.0 - 1.0
    assert row["mfe"] == 106.0 / 102.0 - 1.0
    assert labeled["immature"].sum() == 2

def test_appending_future_rows_does_not_change_mature_prefix_labels():
    before = attach_next_open_path_targets(example_prices(), horizons=(2,))
    after = attach_next_open_path_targets(example_prices(extra_rows=2), horizons=(2,))
    pd.testing.assert_frame_equal(
        before.loc[before["mature"]],
        after.loc[before.index[before["mature"]]],
    )
```

- [ ] **Step 2: Run tests and verify RED**

Run: `../../venv/bin/python -m unittest tests.test_unified_downside_benchmark -v`

Expected: import failure for `research.unified_downside_benchmark`.

- [ ] **Step 3: Implement label validation and construction**

Implement:

```python
DEFAULT_ADVERSE_THRESHOLDS = {5: -0.05, 10: -0.075, 20: -0.10}

def attach_next_open_path_targets(frame, horizons=(5, 10, 20), adverse_thresholds=None):
    """Return one row per observation and horizon using next-open execution."""
```

Reject duplicate point-in-time keys, nonpositive opens, nonfinite OHLC rows, nonpositive horizons, and missing threshold definitions. Preserve immature rows with `mature=False`, null outcomes, and a deterministic reason.

- [ ] **Step 4: Run label tests and verify GREEN**

Run: `../../venv/bin/python -m unittest tests.test_unified_downside_benchmark -v`

- [ ] **Step 5: Commit**

```bash
git add research/unified_downside_benchmark.py tests/test_unified_downside_benchmark.py
git commit -m "research: build executable downside path labels"
```

### Task 2: Exact-Key Model Alignment and Point-in-Time Stratification

**Files:**
- Modify: `research/unified_downside_benchmark.py`
- Modify: `tests/test_unified_downside_benchmark.py`

**Interfaces:**
- Consumes: labeled key table, model prediction frames, effective-dated group assignments, market regime frame.
- Produces:
  - `align_model_predictions(labels: pd.DataFrame, predictions: Mapping[str, pd.DataFrame]) -> pd.DataFrame`
  - `attach_point_in_time_strata(frame: pd.DataFrame, assignments: pd.DataFrame, regimes: pd.DataFrame) -> pd.DataFrame`

- [ ] **Step 1: Write failing alignment tests**

Define exact input frames directly in each test. The alignment test uses two label
keys and one Ridge key; the interval test uses assignments ending at
`2026-01-10` and beginning at `2026-02-01`:

```python
def test_alignment_requires_unique_exact_keys_and_keeps_missing_unavailable():
    keys = pd.DataFrame(
        {
            "ticker": ["AAA", "AAA"],
            "observation_date": pd.to_datetime(["2026-01-02", "2026-01-05"]),
            "horizon": [5, 5],
            "fold": [1, 1],
        }
    )
    ridge = keys.iloc[[0]].assign(
        predicted_event=True,
        predicted_score=pd.NA,
        model_version="ridge_direction_v1",
    )
    aligned = align_model_predictions(keys, {"ridge_down": ridge})
    missing = aligned.loc[aligned["observation_date"] == pd.Timestamp("2026-01-05")]
    assert missing["status"].eq("unavailable").all()
    with self.assertRaisesRegex(ValueError, "duplicate"):
        align_model_predictions(keys, {"ridge_down": pd.concat([ridge, ridge])})

def test_strata_use_half_open_assignment_intervals():
    keys = pd.DataFrame(
        {
            "ticker": ["AAA"] * 3,
            "observation_date": pd.to_datetime(
                ["2026-01-09", "2026-01-10", "2026-02-02"]
            ),
            "horizon": [5] * 3,
            "fold": [1] * 3,
        }
    )
    assignments = pd.DataFrame(
        {
            "ticker": ["AAA", "AAA"],
            "theme_key": ["semiconductor", "software_cloud"],
            "effective_from": pd.to_datetime(["2025-01-01", "2026-02-01"]),
            "effective_to": pd.to_datetime(["2026-01-10", None]),
            "source": ["override", "override"],
        }
    )
    regimes = pd.DataFrame(
        {"observation_date": keys["observation_date"], "regime": "uptrend"}
    )
    stratified = attach_point_in_time_strata(keys, assignments, regimes)
    by_date = stratified.set_index("observation_date")
    assert by_date.loc[pd.Timestamp("2026-01-09"), "group_key"] == "semiconductor"
    assert by_date.loc[pd.Timestamp("2026-01-10"), "group_key"] == "unclassified"
    assert by_date.loc[pd.Timestamp("2026-02-02"), "group_key"] == "software_cloud"
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `../../venv/bin/python -m unittest tests.test_unified_downside_benchmark -v`

Expected: missing alignment and stratification functions.

- [ ] **Step 3: Implement strict alignment**

Normalize every prediction to:

```python
MODEL_KEY_COLUMNS = ("ticker", "observation_date", "horizon", "fold")
MODEL_OUTPUT_COLUMNS = (
    "specification",
    "predicted_event",
    "predicted_score",
    "available_at_close",
    "executable_at",
    "model_version",
    "status",
)
```

Use `validate="one_to_one"` joins. For missing rows emit `status="unavailable"` and nullable Boolean `predicted_event`; never fill missing with `False`.

- [ ] **Step 4: Implement point-in-time strata**

Join assignments with `effective_from <= observation_date < effective_to`, map canonical theme keys to `semiconductor`, `software_cloud`, `other`, and leave missing as `unclassified`. Join `market_regime_v1` by observation date and leave missing as `unavailable`.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `../../venv/bin/python -m unittest tests.test_unified_downside_benchmark tests.test_group_assignments -v`

- [ ] **Step 6: Commit**

```bash
git add research/unified_downside_benchmark.py tests/test_unified_downside_benchmark.py
git commit -m "research: align downside models on point-in-time keys"
```

### Task 3: Metrics, Paired Fold Comparisons, and Evidence Ablation

**Files:**
- Modify: `research/unified_downside_benchmark.py`
- Modify: `tests/test_unified_downside_benchmark.py`

**Interfaces:**
- Produces:
  - `evaluate_unified_predictions(frame: pd.DataFrame, minimum_group_samples: int = 50) -> pd.DataFrame`
  - `compare_folds(metrics: pd.DataFrame, baseline: str = "ridge_down") -> pd.DataFrame`
  - `build_evidence_ablations(feature_frame: pd.DataFrame, scorer: Callable[..., pd.Series]) -> dict[str, pd.Series]`
  - `evidence_overlap_matrix(evidence: pd.DataFrame) -> pd.DataFrame`

- [ ] **Step 1: Write failing metric tests**

```python
def test_binary_models_do_not_fabricate_auc_or_probability_metrics():
    metrics = evaluate_unified_predictions(binary_predictions())
    row = metrics.loc[metrics["specification"] == "immediate_8"].iloc[0]
    assert pd.isna(row["roc_auc"])
    assert pd.isna(row["pr_auc"])
    assert row["precision"] == 0.5

def test_fold_comparison_uses_only_paired_rows_and_excludes_sparse_folds():
    metrics = pd.DataFrame(
        {
            "specification": ["ridge_down"] * 3 + ["toprisk_stateful"] * 3,
            "fold": [1, 2, 3, 1, 2, 3],
            "status": ["ok"] * 6,
            "balanced_accuracy": [0.50, 0.60, 0.55, 0.60, 0.61, 0.50],
            "sample_count": [100] * 6,
        }
    )
    comparison = compare_folds(metrics, baseline="ridge_down")
    row = comparison.loc[comparison["specification"] == "toprisk_stateful"].iloc[0]
    assert row["comparable_fold_count"] == 3
    assert row["fold_win_count"] == 2
```

- [ ] **Step 2: Write failing ablation tests**

```python
def test_each_ablation_removes_only_one_registered_evidence_group():
    evidence = pd.DataFrame(
        {
            "volume_ratio": [1.5],
            "volume_change": [0.5],
            "close_location": [0.1],
            "signed_volume_proxy": [-1.0],
            "below_ema20": [True],
            "failed_breakout": [False],
            "sector_relative_return": [-0.05],
            "market_under_pressure": [True],
            "prior_runup": [0.8],
            "extended_from_ema20": [0.2],
        }
    )
    score_evidence = lambda frame: frame.notna().sum(axis=1).astype(float)
    outputs = build_evidence_ablations(evidence, score_evidence)
    assert set(outputs) == {
        "full",
        "without_volume_participation",
        "without_close_sell_pressure",
        "without_trend_structure",
        "without_relative_environment",
        "without_high_level_context",
    }
    assert outputs["without_volume_participation"].equals(
        score_evidence(evidence.drop(columns=VOLUME_PARTICIPATION_COLUMNS))
    )
```

- [ ] **Step 3: Run tests and verify RED**

Run: `../../venv/bin/python -m unittest tests.test_unified_downside_benchmark -v`

- [ ] **Step 4: Implement metrics**

For overall, group, regime, fold, horizon, and overlapping/non-overlapping scopes calculate sample/available/excluded counts, event rate, signal rate, precision, recall, specificity, balanced accuracy, macro F1, false-positive rate, terminal return, MAE, MFE, lead sessions, and opportunity cost. Emit `status="insufficient"` instead of a result when a group is below its sample gate or contains one actual class.

- [ ] **Step 5: Implement paired comparison and ablation registry**

Freeze five `EVIDENCE_GROUPS`; make each ablation call the injected scorer on a copy with only that group disabled. Calculate Pearson/Spearman correlations for numeric evidence and Jaccard/common-trigger rates for Boolean evidence.

- [ ] **Step 6: Run tests and verify GREEN**

Run: `../../venv/bin/python -m unittest tests.test_unified_downside_benchmark tests.test_evaluate_toprisk_comparison tests.test_downside_specialist -v`

- [ ] **Step 7: Commit**

```bash
git add research/unified_downside_benchmark.py tests/test_unified_downside_benchmark.py
git commit -m "research: evaluate downside models and factor ablations"
```

### Task 4: Deterministic Offline Runner and Atomic Reports

**Files:**
- Create: `research/run_unified_downside_benchmark.py`
- Create: `tests/test_run_unified_downside_benchmark.py`
- Modify: `research/unified_downside_benchmark.py`

**Interfaces:**
- Consumes existing `ExpandedMarketDataRepository`, walk-forward Ridge/Logistic functions, risk context, group assignment repository, and `market_regime_v1`.
- Produces:
  - `run_benchmark(config: BenchmarkConfig) -> BenchmarkArtifacts`
  - CLI outputs `reports/unified-downside-benchmark-v2.json`, `.csv`, `.md`.

Define immutable orchestration contracts:

```python
@dataclass(frozen=True)
class BenchmarkConfig:
    database: Path
    start_date: str
    max_tickers: int
    folds: int
    horizons: tuple[int, ...]
    output_directory: Path

@dataclass(frozen=True)
class BenchmarkInputs:
    prices: pd.DataFrame
    assignments: pd.DataFrame
    regimes: pd.DataFrame

@dataclass(frozen=True)
class BenchmarkDependencies:
    load_inputs: Callable[[BenchmarkConfig], BenchmarkInputs]
    build_predictions: Callable[
        [BenchmarkInputs, BenchmarkConfig],
        Mapping[str, pd.DataFrame],
    ]

@dataclass(frozen=True)
class BenchmarkArtifacts:
    manifest: dict[str, object]
    metrics: pd.DataFrame
    fold_comparisons: pd.DataFrame
    ablations: pd.DataFrame
    overlaps: pd.DataFrame
    output_paths: tuple[Path, ...]
```

- [ ] **Step 1: Write failing runner tests**

```python
def test_runner_publishes_manifest_only_after_all_reports_succeed(self):
    prices = example_prices(extra_rows=2).reset_index()
    assignments = pd.DataFrame(
        {
            "ticker": ["AAA"],
            "theme_key": ["semiconductor"],
            "effective_from": [pd.Timestamp("2020-01-01")],
            "effective_to": [pd.NaT],
            "source": ["override"],
        }
    )
    regimes = pd.DataFrame(
        {
            "observation_date": prices["observation_date"].unique(),
            "regime": "uptrend",
        }
    )
    with tempfile.TemporaryDirectory() as directory:
        output_directory = Path(directory)
        config = BenchmarkConfig(
            database=output_directory / "research.db",
            start_date="2018-01-01",
            max_tickers=2,
            folds=2,
            horizons=(5,),
            output_directory=output_directory,
        )
        inputs = BenchmarkInputs(prices, assignments, regimes)
        dependencies = BenchmarkDependencies(
            load_inputs=lambda _: inputs,
            build_predictions=lambda loaded, checked: {
                "ridge_down": pd.DataFrame(
                    columns=[
                        "ticker",
                        "observation_date",
                        "horizon",
                        "fold",
                        "predicted_event",
                        "predicted_score",
                        "model_version",
                    ]
                )
            },
        )
        artifacts = run_benchmark(config, dependencies=dependencies)
        self.assertEqual(
            artifacts.manifest["study_version"],
            "unified-downside-walkforward-v2",
        )
        self.assertEqual(artifacts.manifest["online_authority"], "none")
        self.assertTrue(all(path.exists() for path in artifacts.output_paths))

def test_report_states_identical_test_rows_and_blocked_authority():
    markdown = render_markdown(
        BenchmarkArtifacts(
            manifest={
                "study_version": "unified-downside-walkforward-v2",
                "online_authority": "none",
                "ticker_count": 2,
                "matched_row_count": 20,
            },
            metrics=pd.DataFrame(
                [{"group_key": "semiconductor"}, {"group_key": "software_cloud"}]
            ),
            fold_comparisons=pd.DataFrame(),
            ablations=pd.DataFrame(),
            overlaps=pd.DataFrame(),
            output_paths=(),
        )
    )
    assert "完全相同的测试行" in markdown
    assert "不具备线上否决权" in markdown
    assert "半导体" in markdown
    assert "软件与云服务" in markdown
```

- [ ] **Step 2: Run runner tests and verify RED**

Run: `../../venv/bin/python -m unittest tests.test_run_unified_downside_benchmark -v`

- [ ] **Step 3: Implement dependency adapters**

Reuse, rather than duplicate:

- `prepare_expanded_frame` and fixed ticker selection;
- `walk_forward_ridge_predictions`;
- `walk_forward_direction_predictions`;
- `walk_forward_downside_predictions`;
- `build_comparison_frame` for existing rule/TOPRISK signals;
- effective-dated `group_assignments`;
- `build_market_regime_frame`.

Persist the frozen configuration, database content fingerprint, model versions, row counts, exclusions, unavailable outputs, and promotion-gate result.

- [ ] **Step 4: Implement atomic report publication**

Write all outputs to sibling temporary files, flush and close them, then replace final paths only after all renderers succeed. Keep deterministic ordering and JSON keys.

- [ ] **Step 5: Run runner and compatibility tests**

Run: `../../venv/bin/python -m unittest tests.test_run_unified_downside_benchmark tests.test_run_pressure_downside_study tests.test_run_toprisk_comparison tests.test_run_expanded_walkforward_study -v`

- [ ] **Step 6: Commit**

```bash
git add research/run_unified_downside_benchmark.py research/unified_downside_benchmark.py tests/test_run_unified_downside_benchmark.py
git commit -m "research: run unified downside walkforward benchmark"
```

### Task 5: Real Study, Documentation, and Full Verification

**Files:**
- Create: `reports/unified-downside-benchmark-v2.json`
- Create: `reports/unified-downside-benchmark-v2.csv`
- Create: `reports/unified-downside-benchmark-v2.md`
- Modify: `docs/modeling-todo.md`

**Interfaces:**
- Produces auditable real-data evidence; does not change production policy.

- [ ] **Step 1: Run the real benchmark**

Run:

```bash
../../venv/bin/python research/run_unified_downside_benchmark.py \
  --database data/research_prices.db \
  --start 2018-01-01 \
  --max-tickers 240 \
  --folds 5 \
  --horizons 5 10 20
```

Expected: three reports with identical manifest fingerprint and no database writes.

- [ ] **Step 2: Verify causal and coverage diagnostics**

Run:

```bash
../../venv/bin/python -m unittest \
  tests.test_unified_downside_benchmark \
  tests.test_run_unified_downside_benchmark \
  tests.test_market_direction_model \
  tests.test_downside_specialist \
  tests.test_evaluate_toprisk_comparison \
  tests.test_group_assignments -v
```

Inspect the report for all five folds, semiconductor/software/unclassified scopes, all market stages, immature tail counts, and exact-key availability counts.

- [ ] **Step 3: Update the Chinese global TODO honestly**

Record the exact dataset dates, ticker/sample counts, model metrics, factor groups with or without incremental value, and promotion-gate result. Leave FCAST-001 and TOPRISK-001 open unless every frozen gate passes.

- [ ] **Step 4: Run full verification**

Run:

```bash
../../venv/bin/python -m pytest -q
../../venv/bin/python -m compileall research web tests
git diff --check
```

- [ ] **Step 5: Commit**

```bash
git add reports/unified-downside-benchmark-v2.json \
  reports/unified-downside-benchmark-v2.csv \
  reports/unified-downside-benchmark-v2.md \
  docs/modeling-todo.md
git commit -m "research: report unified downside walkforward evidence"
```
