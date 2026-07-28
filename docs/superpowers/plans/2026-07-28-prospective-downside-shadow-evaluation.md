# Prospective Downside Shadow Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze the promoted downside challengers at the current market cutoff, record only genuinely future predictions in an append-only ledger, and evaluate them after their next-open path labels mature.

**Architecture:** A pure model-artifact module fits and validates immutable Logistic artifacts. A separate SQLite store owns experiments, predictions, and outcomes with conflict-detecting idempotency. A CLI orchestrates freeze, capture, and evaluate without entering the web request path, while the existing unified benchmark gains observable stage timings.

**Tech Stack:** Python 3.9, pandas, NumPy, scikit-learn-compatible frozen coefficients, SQLite, JSON/CSV/Markdown, `unittest`.

## Global Constraints

- The first experiment is `downside-shadow-v1`.
- Predictions are legal only when `observation_date > frozen_market_asof`; historical backfill is rejected.
- Signals are generated at the observation-date close and execute at the next trading-day open.
- The research cohort is fixed when the experiment is created.
- Pressure Logistic supports only 5 and 20 sessions; memory rules support 5/10/20; unsupported output is unavailable, never negative.
- Frozen feature order, imputation values, scales, coefficients, intercepts, thresholds, code commit, database fingerprint, and checksum are immutable.
- Predictions and outcomes are append-only. Exact retries are idempotent; conflicting retries fail closed.
- No step changes Ridge, TOPRISK, `forecast_decision_policy`, web requests, or price databases.
- `online_authority` remains `none`.
- Use red-green-refactor cycles and commit each task separately.

---

### Task 1: Immutable Frozen Linear-Model Artifacts

**Files:**
- Create: `research/downside_shadow.py`
- Create: `tests/test_downside_shadow.py`

**Interfaces:**
- Consumes: point-in-time feature rows and matured binary labels.
- Produces:
  - `FrozenLinearArtifact`
  - `fit_frozen_ridge(frame, *, feature_columns, target_column, label_end_column, frozen_market_asof, specification, horizon) -> FrozenLinearArtifact`
  - `fit_frozen_direction_logistic(frame, *, feature_columns, target_column, label_end_column, frozen_market_asof, specification, horizon, neutral_band) -> FrozenLinearArtifact`
  - `fit_frozen_binary_logistic(frame, *, feature_columns, target_column, label_end_column, frozen_market_asof, specification, horizon) -> FrozenLinearArtifact`
  - `predict_frozen_linear(artifact, frame) -> pd.DataFrame`
  - `write_shadow_model_bundle(path, bundle) -> str`
  - `read_shadow_model_bundle(path, expected_checksum=None) -> dict`

- [ ] **Step 1: Write failing cutoff and determinism tests**

```python
def test_frozen_fit_uses_only_labels_ending_by_cutoff():
    frame = labeled_feature_frame()
    artifact = fit_frozen_binary_logistic(
        frame,
        feature_columns=("x1", "x2"),
        target_column="downside_event_5",
        label_end_column="downside_label_end_date_5",
        frozen_market_asof="2026-07-24",
        specification="pressure_downside_logistic_v1",
        horizon=5,
    )
    assert artifact.training_label_end_max <= "2026-07-24"
    assert artifact.training_samples == 4

def test_frozen_prediction_preserves_feature_order_and_is_repeatable():
    first = predict_frozen_linear(frozen_artifact(), prediction_frame())
    second = predict_frozen_linear(frozen_artifact(), prediction_frame())
    pd.testing.assert_frame_equal(first, second)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
./venv/bin/python -m unittest tests.test_downside_shadow -v
```

Expected: import failure for `research.downside_shadow`.

- [ ] **Step 3: Implement validated artifact types and training**

`FrozenLinearArtifact` is a frozen dataclass containing:

```python
specification: str
horizon: int
model_kind: str
feature_columns: tuple[str, ...]
imputation_values: tuple[float, ...]
centers: tuple[float, ...]
scales: tuple[float, ...]
coefficients: tuple[tuple[float, ...], ...]
intercepts: tuple[float, ...]
classes: tuple[str, ...]
event_threshold: float
training_samples: int
training_event_rate: float
training_label_end_max: str
frozen_market_asof: str
model_version: str
```

Fit medians and scaling only on rows whose label and label-end are present and
whose label-end is no later than the frozen cutoff. Ridge freezes executable
return regression; direction Logistic freezes the same up/neutral/down target
used by the historical challenger; pressure Logistic freezes the binary MAE
event target. Reject degenerate targets, nonfinite fitted parameters, duplicate
features, and a label-end after cutoff.

- [ ] **Step 4: Implement deterministic inference and JSON bundle checksums**

Inference validates exact feature identity, clips source values using the
existing specialist cap, applies stored preprocessing, and branches by frozen
`model_kind`. Ridge returns predicted return and direction, direction Logistic
returns its frozen class and down-class probability, and pressure Logistic
returns binary event and probability. Bundle JSON uses sorted keys and returns
its SHA-256 checksum. Loading verifies schema, finite values, matrix shapes,
class identity, feature lengths, and optional expected checksum.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
./venv/bin/python -m unittest tests.test_downside_shadow -v
```

- [ ] **Step 6: Commit**

```bash
git add research/downside_shadow.py tests/test_downside_shadow.py
git commit -m "research: freeze prospective downside models"
```

---

### Task 2: Append-Only Shadow Ledger

**Files:**
- Create: `research/downside_shadow_store.py`
- Create: `tests/test_downside_shadow_store.py`

**Interfaces:**
- Produces:
  - `ShadowExperiment`
  - `ShadowPrediction`
  - `ShadowOutcome`
  - `DownsideShadowStore.create_experiment(experiment) -> bool`
  - `DownsideShadowStore.append_predictions(experiment_id, rows) -> int`
  - `DownsideShadowStore.append_outcomes(experiment_id, rows) -> int`
  - `DownsideShadowStore.load_experiment(experiment_id) -> ShadowExperiment | None`
  - `DownsideShadowStore.load_predictions(experiment_id) -> pd.DataFrame`
  - `DownsideShadowStore.load_outcomes(experiment_id) -> pd.DataFrame`

- [ ] **Step 1: Write failing immutable-experiment and prediction tests**

```python
def test_experiment_is_idempotent_but_conflicting_identity_is_rejected():
    store = DownsideShadowStore(database)
    assert store.create_experiment(experiment()) is True
    assert store.create_experiment(experiment()) is False
    with self.assertRaisesRegex(ValueError, "conflict"):
        store.create_experiment(replace(experiment(), frozen_market_asof="2026-07-25"))

def test_prediction_rejects_freeze_date_and_conflicting_retry():
    store = initialized_store(database)
    with self.assertRaisesRegex(ValueError, "strictly after"):
        store.append_predictions("downside-shadow-v1", [prediction("2026-07-24")])
    assert store.append_predictions(
        "downside-shadow-v1", [prediction("2026-07-27")]
    ) == 1
    assert store.append_predictions(
        "downside-shadow-v1", [prediction("2026-07-27")]
    ) == 0
    with self.assertRaisesRegex(ValueError, "conflict"):
        store.append_predictions(
            "downside-shadow-v1",
            [replace(prediction("2026-07-27"), predicted_event=False)],
        )
```

- [ ] **Step 2: Run store tests and verify RED**

Run:

```bash
./venv/bin/python -m unittest tests.test_downside_shadow_store -v
```

- [ ] **Step 3: Implement schema and experiment identity**

Create `shadow_experiments`, `shadow_predictions`, and `shadow_outcomes` exactly
as specified in the design. Enable foreign keys, set a finite busy timeout, and
initialize the schema inside the store. Store canonical JSON and SHA-256
checksums for immutable payloads.

- [ ] **Step 4: Implement transactional append semantics**

Before insertion, compare every existing primary key. Exact payload checksum
means idempotent retry; a different checksum raises `ValueError`. Validate the
entire batch before `BEGIN IMMEDIATE`, then insert all rows in one transaction.
An injected or SQLite failure must leave zero rows from that batch.

- [ ] **Step 5: Write and pass outcome maturity/identity tests**

```python
def test_outcome_requires_existing_prediction_and_exact_identity():
    store = initialized_store_with_prediction(database)
    assert store.append_outcomes(
        "downside-shadow-v1", [mature_outcome()]
    ) == 1
    with self.assertRaisesRegex(ValueError, "prediction"):
        store.append_outcomes(
            "downside-shadow-v1",
            [replace(mature_outcome(), ticker="UNKNOWN")],
        )
```

Run:

```bash
./venv/bin/python -m unittest tests.test_downside_shadow_store -v
```

- [ ] **Step 6: Commit**

```bash
git add research/downside_shadow_store.py tests/test_downside_shadow_store.py
git commit -m "research: add append-only downside shadow ledger"
```

---

### Task 3: Freeze and Capture Orchestration

**Files:**
- Create: `research/run_downside_shadow.py`
- Create: `tests/test_run_downside_shadow.py`

**Interfaces:**
- Produces:
  - `ShadowConfig`
  - `freeze_experiment(config, dependencies=None) -> dict`
  - `capture_latest(config, dependencies=None) -> dict`
  - CLI subcommands `freeze` and `capture`

- [ ] **Step 1: Write failing freeze boundary test**

```python
def test_freeze_persists_fixed_cohort_models_and_fingerprints():
    result = freeze_experiment(config(), dependencies=fake_dependencies())
    assert result["experiment_id"] == "downside-shadow-v1"
    assert result["online_authority"] == "none"
    assert result["frozen_market_asof"] == "2026-07-24"
    assert result["ticker_count"] == 240
    assert result["model_artifact_checksum"]
```

- [ ] **Step 2: Run the orchestration test and verify RED**

Run:

```bash
./venv/bin/python -m unittest tests.test_run_downside_shadow -v
```

- [ ] **Step 3: Implement `freeze_experiment`**

Reuse:

- `ExpandedMarketDataRepository`
- `classify_study_groups`
- `select_analysis_tickers`
- `prepare_expanded_frame`
- `attach_next_open_mae_targets`
- `SPECIALIST_FEATURE_COLUMNS`
- `expanded_feature_sets(RIDGE_V4_FEATURE_COLUMNS)`

First determine the latest session shared by required reference instruments,
then select 240 common stocks that have a valid bar on that cutoff and enough
causal history. Fit frozen Ridge artifacts and direction Logistic artifacts for
5/10/20 plus pressure Logistic artifacts for 5/20. Save one bundle containing
the fixed cohort, model artifacts, thresholds, group-assignment revision,
database fingerprint, code commit, and `online_authority=none`. Create the
experiment only after the model file has been written and reloaded successfully.

- [ ] **Step 4: Write failing no-backfill capture tests**

```python
def test_capture_records_only_the_latest_new_common_session():
    dependencies = fake_capture_dependencies(latest_date="2026-07-27")
    first = capture_latest(config(), dependencies=dependencies)
    second = capture_latest(config(), dependencies=dependencies)
    assert first["inserted_predictions"] > 0
    assert second["inserted_predictions"] == 0

def test_capture_does_not_replay_missed_sessions():
    dependencies = fake_capture_dependencies(
        latest_date="2026-07-29",
        prior_sessions=("2026-07-27", "2026-07-28"),
    )
    result = capture_latest(config(), dependencies=dependencies)
    assert result["captured_observation_dates"] == ["2026-07-29"]
```

- [ ] **Step 5: Implement atomic capture**

Require a complete current session for QQQ, SPY, and required group references.
An individual cohort stock missing that session is recorded unavailable and
reduces coverage; it does not block every other stock. Build features as of
that close, load rather than refit the frozen bundle, calculate:

- Ridge baseline output;
- general Logistic for 5/10/20;
- pressure Logistic for 5/20 only in pressure regimes;
- memory-12 rule output for 5/10/20.

Attach point-in-time group and market regime. Validate the entire snapshot, then
append it in one transaction. Any model exception rolls back the day.

- [ ] **Step 6: Add safe CLI and secret-redaction tests**

The CLI accepts:

```text
--research-database
--shadow-database
--model-artifact
--experiment-id
```

JSON stdout contains no environment variables or credentials. Known failures
return a nonzero exit code and a stable error code without raw exception text.

- [ ] **Step 7: Run focused and compatibility tests**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_run_downside_shadow \
  tests.test_run_pressure_downside_study \
  tests.test_evaluate_toprisk_comparison -v
```

- [ ] **Step 8: Commit**

```bash
git add research/run_downside_shadow.py tests/test_run_downside_shadow.py
git commit -m "research: capture prospective downside shadow predictions"
```

---

### Task 4: Mature Outcomes and Shadow Reports

**Files:**
- Modify: `research/run_downside_shadow.py`
- Modify: `tests/test_run_downside_shadow.py`
- Create after a real freeze: `reports/downside-shadow-v1-model.json`
- Create after evaluation:
  - `reports/downside-shadow-v1.json`
  - `reports/downside-shadow-v1.csv`
  - `reports/downside-shadow-v1.md`

**Interfaces:**
- Produces:
  - `evaluate_shadow(config, dependencies=None) -> ShadowEvaluationArtifacts`
  - CLI subcommand `evaluate`

- [ ] **Step 1: Write failing next-open maturity test**

```python
def test_evaluate_writes_only_complete_next_open_paths():
    artifacts = evaluate_shadow(
        config(horizons=(5, 20)),
        dependencies=fake_evaluation_dependencies(available_future_sessions=6),
    )
    outcomes = artifacts.outcomes
    assert set(outcomes["horizon"]) == {5}
    row = outcomes.iloc[0]
    assert row["entry_open"] == 101.0
    assert row["label_end_date"] == "2026-08-03"
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_run_downside_shadow.RunDownsideShadowTest.test_evaluate_writes_only_complete_next_open_paths -v
```

- [ ] **Step 3: Implement outcome attachment**

Use `attach_next_open_path_targets` on current histories, then select only
existing ledger prediction keys with `mature=True`. Preserve the original
prediction and entry semantics. Append new outcomes only after every selected
row passes identity and finite-value validation.

- [ ] **Step 4: Implement report metrics and capture-gap diagnostics**

Reuse `align_model_predictions`, `attach_point_in_time_strata`, and
`evaluate_unified_predictions` where their exact contracts match. Add:

- planned versus captured observation dates;
- actual capture gaps;
- model applicability and availability;
- first threshold-crossing lead sessions;
- signal and missed-opportunity terminal return;
- next-open fixed-horizon equity curve and maximum drawdown.

Rules retain null probability metrics. Fewer than 50 samples or one actual
class is `insufficient`.

- [ ] **Step 5: Implement the frozen shadow promotion gate**

Return `eligible_for_human_review=True` only when all exact gates from the
design pass: 60 captured sessions, required sample counts, group counts,
5/20 pressure model improvement, recall, specificity, drawdown, and zero audit
violations. Always return `online_authority="none"`.

- [ ] **Step 6: Implement atomic JSON/CSV/Markdown output and tests**

Write sibling temporary files and replace final paths only after all renderers
succeed. Markdown must state:

```text
这是冻结日之后的前瞻影子结果，不是历史回填。
即使达到研究门槛，也不具备线上否决权。
```

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_run_downside_shadow \
  tests.test_unified_downside_benchmark -v
```

- [ ] **Step 7: Commit**

```bash
git add research/run_downside_shadow.py tests/test_run_downside_shadow.py
git commit -m "research: evaluate matured downside shadow outcomes"
```

---

### Task 5: Unified Benchmark Stage Timings

**Files:**
- Modify: `research/run_unified_downside_benchmark.py`
- Modify: `tests/test_run_unified_downside_benchmark.py`

**Interfaces:**
- Adds `stage_timings_seconds` to the benchmark manifest.
- Adds progress messages to stderr without changing JSON/CSV/Markdown stdout.

- [ ] **Step 1: Write failing injected-clock test**

```python
def test_runner_records_nonnegative_named_stage_timings():
    clock = iter([0.0, 1.0, 1.5, 2.5, 3.0, 4.0, 4.2])
    artifacts = run_benchmark(
        config,
        dependencies=fake_dependencies(),
        monotonic=lambda: next(clock),
        progress=lambda message: messages.append(message),
    )
    timings = artifacts.manifest["stage_timings_seconds"]
    assert set(timings) == {
        "load_inputs",
        "build_statistical_predictions",
        "build_rule_context",
        "label_and_align",
        "evaluate",
        "publish",
        "total",
    }
    assert all(value >= 0 for value in timings.values())
    assert timings["total"] >= max(timings.values())
```

- [ ] **Step 2: Run focused test and verify RED**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_run_unified_downside_benchmark.RunUnifiedDownsideBenchmarkTest.test_runner_records_nonnegative_named_stage_timings -v
```

- [ ] **Step 3: Implement timing and stderr progress**

Add a small injected stage-recorder used by both `run_benchmark` and
`_build_real_predictions`, with production defaults `time.monotonic` and
`print(..., file=sys.stderr, flush=True)`. Statistical and rule-context timing
must remain separate even though both are assembled by the prediction adapter.
Do not put elapsed values into model identity or promotion logic.

- [ ] **Step 4: Run runner compatibility tests**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_run_unified_downside_benchmark \
  tests.test_run_expanded_walkforward_study \
  tests.test_run_pressure_downside_study -v
```

- [ ] **Step 5: Commit**

```bash
git add research/run_unified_downside_benchmark.py \
  tests/test_run_unified_downside_benchmark.py
git commit -m "research: expose unified benchmark stage timings"
```

---

### Task 6: Real Freeze, Documentation, and Full Verification

**Files:**
- Create: `reports/downside-shadow-v1-model.json`
- Modify: `docs/modeling-todo.md`
- Modify: `docs/dashboard.md`

**Interfaces:**
- Produces the real immutable experiment definition but does not fabricate any future predictions.

- [ ] **Step 1: Run full verification before touching real shadow state**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/downside-shadow-pycache \
  ./venv/bin/python -m unittest discover -s tests -v
```

Expected: zero failures.

- [ ] **Step 2: Freeze the real v1 experiment**

Run:

```bash
./venv/bin/python -m research.run_downside_shadow freeze \
  --research-database data/research_prices.db \
  --shadow-database data/downside_shadow.db \
  --model-artifact reports/downside-shadow-v1-model.json \
  --experiment-id downside-shadow-v1
```

Expected: freeze date equals the latest complete common session, the cohort is
240 stocks, every fitted label-end is no later than the freeze date, and
`online_authority` is `none`.

- [ ] **Step 3: Prove that the freeze does not backfill**

Run `capture` immediately against the same database. Expected:

```json
{
  "inserted_predictions": 0,
  "reason": "no_new_session_after_freeze"
}
```

Do not create fake outcomes or reports with future results.

- [ ] **Step 4: Document operations and TODO state**

`docs/dashboard.md` documents the three CLI commands and states that capture
must run only after a genuinely new completed session. `docs/modeling-todo.md`
records the experiment ID, freeze date, cohort, artifact checksum, zero
backfilled predictions, and remaining 60-session maturity gate.

- [ ] **Step 5: Run final verification**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/downside-shadow-final \
  ./venv/bin/python -m unittest discover -s tests -v
```

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/downside-shadow-compile \
  ./venv/bin/python -m compileall -q research web tests
```

Run:

```bash
git diff --check
```

- [ ] **Step 6: Commit**

Do not add `data/downside_shadow.db` to Git. Commit the immutable model artifact
and documentation:

```bash
git add reports/downside-shadow-v1-model.json \
  docs/modeling-todo.md docs/dashboard.md
git commit -m "research: freeze prospective downside shadow v1"
```
