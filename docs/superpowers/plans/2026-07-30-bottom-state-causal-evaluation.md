# Bottom State Causal Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a leakage-safe offline evaluation that measures whether causal bottoming-state transitions improve future 5/10/20-session outcomes versus matched downtrend-continuation baselines.

**Architecture:** A pure replay module assembles the exact evidence consumed by `bottoming_reversal_state_v1` without importing Flask. A focused evaluation module owns transition events, mature future labels, non-overlap episodes, matching, aggregation, ablations, and fail-closed gates. A CLI runner loads frozen development/confirmation cohorts, publishes aggregate CSV/JSON/Markdown evidence, and never changes online authority.

**Tech Stack:** Python 3.9, pandas, NumPy, SQLite read-only research store, `unittest`, Markdown/CSV/JSON.

## Global Constraints

- Use only information available through each observation close.
- Freeze data at `2026-07-24`, start at `2018-01-01`, and evaluate horizons `5`, `10`, and `20`.
- Main performance samples use transition events; memory-extension days are coverage diagnostics only.
- Emit both `all_transitions` and 20-session `non_overlapping` scopes.
- A same-day failure takes precedence over confirmation.
- Tail samples without the full future horizon are excluded.
- Development and confirmation cohorts are deterministic, disjoint, and at most 240 stocks each.
- Existing historical group backfill keeps the causal audit failed.
- All results remain `research` / `advisory_only`; do not change API, UI, Ridge, downside vetoes, or final policy.
- Do not modify or commit `data/*.db-wal`, `data/*.db-shm`, or `research/high_level_reversal_study.py`.

---

### Task 1: Build a production-parity bottom-state replay boundary

**Files:**
- Create: `research/bottom_state_replay.py`
- Modify: `web/services/bottom_state.py`
- Create: `tests/test_bottom_state_replay.py`
- Modify: `tests/test_web_bottom_state_service.py`

**Interfaces:**
- Consumes: `ticker: str`, `histories: Mapping[str, pd.DataFrame]`.
- Produces:

```python
def bottom_evidence_frame(chart: list[dict]) -> pd.DataFrame:
    """Return the typed evidence frame consumed by the bottom-state model."""

def build_bottom_state_replay(
    ticker: str,
    histories: Mapping[str, pd.DataFrame],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return causal evidence and bottom-state rows for one ticker."""
```

- The replay sequence is `build_chart_rows` → `build_entry_signal_rows` / `merge_entry_signal_rows` → `attach_supply_demand_rows` → `attach_historical_demand_support_rows` → `build_market_gate_frame` → `build_bottom_state_rows`.

- [ ] **Step 1: Write failing evidence-contract tests**

```python
def test_bottom_evidence_frame_matches_service_input_contract(self):
    chart = [_chart_row("2026-01-02")]
    evidence = bottom_evidence_frame(chart)
    self.assertEqual(list(evidence.index), [pd.Timestamp("2026-01-02")])
    self.assertEqual(evidence.iloc[0]["near_support_state"], "testing")
    self.assertEqual(
        evidence.iloc[0]["market_regime_state"],
        "confirmed_uptrend",
    )

def test_replay_does_not_import_or_construct_flask_app(self):
    source = Path("research/bottom_state_replay.py").read_text()
    self.assertNotIn("from web.app import", source)
```

- [ ] **Step 2: Run the contract tests and verify RED**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_bottom_state_replay.BottomStateReplayContractTest -v
```

Expected: import failure because `research.bottom_state_replay` and public `bottom_evidence_frame` do not exist.

- [ ] **Step 3: Expose the evidence frame without changing web behavior**

Move `_evidence_row` use behind:

```python
def bottom_evidence_frame(chart):
    if not isinstance(chart, list):
        raise TypeError("chart must be a list")
    dates = pd.to_datetime([row.get("time") for row in chart], errors="raise")
    return pd.DataFrame([_evidence_row(row) for row in chart], index=dates)
```

Change `attach_bottom_state_rows` to call this public helper. Preserve all current unavailable defaults and serialized fields.

- [ ] **Step 4: Write failing full replay and prefix tests**

```python
def test_replay_returns_one_aligned_state_per_price_session(self):
    histories = _histories_with_downtrend()
    evidence, states = build_bottom_state_replay("AAA", histories)
    self.assertTrue(states.index.equals(histories["AAA"].index))
    self.assertTrue(evidence.index.equals(states.index))

def test_appending_future_prices_cannot_rewrite_replayed_prefix(self):
    prefix = _histories_with_downtrend()
    extended = _append_future(prefix, sessions=5)
    expected = build_bottom_state_replay("AAA", prefix)[1]
    actual = build_bottom_state_replay("AAA", extended)[1].iloc[: len(expected)]
    pd.testing.assert_frame_equal(actual, expected)
```

- [ ] **Step 5: Implement the pure replay**

Use `AnalysisContext` with the ticker history and SPY benchmark, call existing public atomic services, attach market state from `research.market_gate.build_market_gate_frame`, then call `bottom_evidence_frame` and `build_bottom_state_rows`. Validate ticker/history inputs and return aligned frames without mutating the supplied histories.

- [ ] **Step 6: Run replay and existing web service tests**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_bottom_state_replay \
  tests.test_web_bottom_state_service -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add research/bottom_state_replay.py \
  web/services/bottom_state.py \
  tests/test_bottom_state_replay.py \
  tests/test_web_bottom_state_service.py
git commit -m "research: add bottom state evidence replay"
```

---

### Task 2: Build transition events and mature future labels

**Files:**
- Create: `research/bottom_state_evaluation.py`
- Create: `tests/test_bottom_state_evaluation.py`

**Interfaces:**

```python
def build_bottom_transition_events(
    ticker: str,
    history: pd.DataFrame,
    states: pd.DataFrame,
    *,
    horizons: tuple[int, ...] = (5, 10, 20),
    non_overlap_sessions: int = 20,
) -> pd.DataFrame:
    """Return mature transition-event outcomes for both event scopes."""
```

Each row contains:

```text
ticker observation_date observation_state observation_rank
scope horizon observation_close drawdown_63 drawdown_bin
forward_return positive_return maximum_favorable_excursion
maximum_adverse_excursion confirmed_within_horizon
failed_within_horizon first_terminal_state
sessions_to_confirmation sessions_to_failure state_maintained
bottom_score bottom_coverage bottom_state_age_sessions
```

- [ ] **Step 1: Write failing transition and maturity tests**

```python
def test_memory_extension_days_do_not_create_transition_events(self):
    states = _states(["potential_support"] * 4, transitions=[True, False, False, False])
    rows = build_bottom_transition_events("AAA", _history(30), states, horizons=(5,))
    self.assertEqual(rows["observation_date"].nunique(), 1)

def test_immature_tail_is_excluded(self):
    states = _transition_at(_history(25).index[-4], "seller_exhaustion_watch")
    rows = build_bottom_transition_events("AAA", _history(25), states, horizons=(5,))
    self.assertTrue(rows.empty)
```

- [ ] **Step 2: Verify RED**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_bottom_state_evaluation.BottomTransitionContractTest -v
```

Expected: import failure because the evaluation module does not exist.

- [ ] **Step 3: Implement validated transition extraction**

Require aligned unique increasing dates, positive finite OHLC, nonnegative volume, valid state names, positive unique horizons, and a positive integer non-overlap window. Compute 63-session drawdown using only history through the observation date and emit the four frozen bins from the design.

- [ ] **Step 4: Write failing outcome and terminal-state tests**

```python
def test_future_return_mfe_and_mae_use_exact_future_window(self):
    history, states = _labeled_fixture(
        future_closes=[100.0, 104.0, 102.0, 106.0, 101.0, 105.0],
        future_highs=[101.0, 105.0, 103.0, 108.0, 102.0, 106.0],
        future_lows=[99.0, 98.0, 100.0, 101.0, 97.0, 104.0],
    )
    row = build_bottom_transition_events(
        "AAA", history, states, horizons=(5,),
    ).iloc[0]
    self.assertAlmostEqual(row["forward_return"], 0.05)
    self.assertAlmostEqual(row["maximum_favorable_excursion"], 0.08)
    self.assertAlmostEqual(row["maximum_adverse_excursion"], -0.03)

def test_failure_wins_when_confirmation_occurs_on_same_day(self):
    history, states = _terminal_fixture(
        transitions={2: "bullish_structure_confirmed"},
        failed_positions={2},
    )
    row = build_bottom_transition_events(
        "AAA", history, states, horizons=(5,),
    ).iloc[0]
    self.assertEqual(row["first_terminal_state"], "failed")
    self.assertEqual(row["sessions_to_failure"], 2)

def test_first_terminal_state_uses_the_earlier_transition(self):
    history, states = _terminal_fixture(
        transitions={2: "bullish_structure_confirmed", 4: "bottom_failed"},
    )
    row = build_bottom_transition_events(
        "AAA", history, states, horizons=(5,),
    ).iloc[0]
    self.assertEqual(row["first_terminal_state"], "confirmed")
    self.assertEqual(row["sessions_to_confirmation"], 2)

def test_structure_state_at_observation_has_zero_confirmation_delay(self):
    history, states = _transition_fixture("bullish_structure_confirmed")
    row = build_bottom_transition_events(
        "AAA", history, states, horizons=(5,),
    ).iloc[0]
    self.assertEqual(row["sessions_to_confirmation"], 0)

def test_non_overlapping_scope_skips_second_active_positive_event(self):
    history, states = _two_positive_transition_fixture(spacing=4)
    rows = build_bottom_transition_events(
        "AAA", history, states, horizons=(5, 20),
    )
    selected = rows.loc[
        rows["scope"].eq("non_overlapping")
        & rows["horizon"].eq(20)
    ]
    self.assertEqual(len(selected), 1)

def test_failure_can_terminate_an_active_non_overlapping_episode(self):
    history, states = _positive_failure_positive_fixture()
    rows = build_bottom_transition_events(
        "AAA", history, states, horizons=(5,),
    )
    selected = rows.loc[
        rows["scope"].eq("non_overlapping")
        & rows["observation_state"].ne("bottom_failed")
    ]
    self.assertEqual(len(selected), 2)
```

Assert exact numeric returns, high/low excursions, terminal labels, delays, and both scopes.

- [ ] **Step 5: Implement future labels and event scopes**

For each horizon use positions `observation + 1` through `observation + horizon` for high/low excursions and the terminal close at `observation + horizon`. Scan state transitions in chronological order; on the same date set `first_terminal_state="failed"`.

For `non_overlapping`, keep the first positive event and suppress later positive transitions through session 20 unless a `bottom_failed` transition ends the active episode. Preserve all transitions in `all_transitions`.

- [ ] **Step 6: Write and pass prefix-invariance tests**

```python
def test_appended_future_rows_do_not_change_already_mature_events(self):
    expected = build_bottom_transition_events("AAA", prefix_history, prefix_states)
    actual = build_bottom_transition_events("AAA", extended_history, extended_states)
    pd.testing.assert_frame_equal(
        actual.loc[actual.event_end_date <= prefix_history.index[-1]].reset_index(drop=True),
        expected.reset_index(drop=True),
    )
```

Run:

```bash
./venv/bin/python -m unittest tests.test_bottom_state_evaluation -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add research/bottom_state_evaluation.py \
  tests/test_bottom_state_evaluation.py
git commit -m "research: label bottom state transition outcomes"
```

---

### Task 3: Add matching, aggregation, ablations, and the research gate

**Files:**
- Modify: `research/bottom_state.py`
- Modify: `research/bottom_state_evaluation.py`
- Modify: `tests/test_bottom_state.py`
- Modify: `tests/test_bottom_state_evaluation.py`

**Interfaces:**

```python
BOTTOM_ABLATIONS = (
    "full",
    "no_location",
    "no_exhaustion",
    "no_demand",
    "no_structure",
    "no_environment",
)

def build_bottom_state_rows(
    history: pd.DataFrame,
    evidence: pd.DataFrame,
    *,
    disabled_components: frozenset[str] = frozenset(),
) -> pd.DataFrame:
    """Return one diagnosis per session, optionally disabling named groups."""

def match_downtrend_baselines(events: pd.DataFrame) -> pd.DataFrame:
    """Return deterministic one-to-one positive-event/baseline pairs."""

def evaluate_bottom_events(events: pd.DataFrame) -> pd.DataFrame:
    """Aggregate unmatched and matched outcome metrics."""

def bottom_evaluation_decision(
    metrics: pd.DataFrame,
    *,
    evidence_contract_passed: bool,
    group_causal_audit_passed: bool,
    future_holdout_passed: bool,
) -> dict[str, object]:
    """Return a fail-closed advisory-only decision."""
```

- [ ] **Step 1: Write failing independent-ablation tests**

```python
def test_disabling_demand_does_not_disable_exhaustion(self):
    rows = build_bottom_state_rows(
        frame,
        evidence,
        disabled_components=frozenset({"demand"}),
    )
    self.assertGreater(rows.iloc[-1]["bottom_exhaustion_score"], 0)
    self.assertEqual(rows.iloc[-1]["bottom_demand_score"], 0)

def test_unknown_disabled_component_is_rejected(self):
    with self.assertRaisesRegex(ValueError, "disabled"):
        build_bottom_state_rows(frame, evidence, disabled_components=frozenset({"x"}))
```

- [ ] **Step 2: Implement component switches without changing default output**

Validate against `{"location", "exhaustion", "demand", "structure", "environment"}`. Set only the disabled component score and availability contribution to zero/false. Add a regression assertion that the default call is byte-for-byte equal to the pre-change expected frame.

- [ ] **Step 3: Write failing deterministic matching tests**

```python
def test_matching_prefers_same_ticker_then_same_group(self):
    matched = match_downtrend_baselines(_same_ticker_and_group_candidates())
    self.assertEqual(matched.iloc[0]["baseline_ticker"], "AAA")
    self.assertEqual(matched.iloc[0]["match_tier"], "same_ticker_exact_bin")

def test_matching_never_crosses_market_regime(self):
    matched = match_downtrend_baselines(_cross_regime_only_candidates())
    self.assertTrue(matched.empty)

def test_matching_can_use_only_an_adjacent_drawdown_bin(self):
    matched = match_downtrend_baselines(_adjacent_and_distant_bin_candidates())
    self.assertEqual(matched.iloc[0]["baseline_drawdown_bin"], "-25_-40")

def test_one_baseline_row_cannot_be_reused(self):
    matched = match_downtrend_baselines(_two_events_one_baseline())
    self.assertEqual(matched["baseline_event_id"].nunique(), len(matched))
    self.assertEqual(len(matched), 1)

def test_matching_is_independent_of_future_outcome_columns(self):
    source = _matching_fixture()
    changed = source.copy()
    changed.loc[:, "forward_return"] = source["forward_return"] * -100.0
    first = match_downtrend_baselines(source)
    second = match_downtrend_baselines(changed)
    self.assertEqual(
        first[["event_id", "baseline_event_id"]].to_dict("records"),
        second[["event_id", "baseline_event_id"]].to_dict("records"),
    )
```

- [ ] **Step 4: Implement matching**

Match only positive states. Candidate order is:

```text
same cohort/fold/regime/horizon/scope/variant
same ticker and exact drawdown bin
same ticker and adjacent drawdown bin
same group and exact drawdown bin
same group and adjacent drawdown bin
```

Within one tier choose smallest absolute date distance, then ticker/date lexical order. Add `pair_id`, `match_tier`, and `matched=true`; do not read return, MFE, MAE, confirmation, or failure columns while selecting.

- [ ] **Step 5: Write failing aggregation and gate tests**

Assert event count, matched count, coverage, mean/median return, positive rate, MFE, MAE, confirmation/failure/maintenance rates, mean delays, and annualized event-frequency proxy. Assert gates fail on fewer than 100 matched events in a group, fewer than 3 fold wins, missing distance/group slices, failed audits, or absent future holdout.

- [ ] **Step 6: Implement aggregation and fail-closed decision**

Emit detailed and `all` slices for fold/group/regime/drawdown bin. Matched deltas use one positive and one baseline per `pair_id`. The gate evaluates confirmation-cohort, 10-day, non-overlapping, matched early-state rows and always returns:

```python
{
    "eligible": False,
    "authority": "advisory_only",
    "reasons": ["future_holdout_required"],
    "performance_conditions": {
        "positive_rate_gain_at_least_5pp": False,
        "mean_return_gain_at_least_2pp": False,
    },
}
```

until every audit and future holdout flag is true.

- [ ] **Step 7: Run tests and commit**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_bottom_state \
  tests.test_bottom_state_evaluation -v
```

Expected: all tests pass and the default bottom model remains unchanged.

Commit:

```bash
git add research/bottom_state.py \
  research/bottom_state_evaluation.py \
  tests/test_bottom_state.py \
  tests/test_bottom_state_evaluation.py
git commit -m "research: evaluate matched bottom state events"
```

---

### Task 4: Build the frozen dual-cohort study runner

**Files:**
- Create: `research/run_bottom_state_evaluation.py`
- Create: `tests/test_run_bottom_state_evaluation.py`

**Interfaces:**

```python
def run_bottom_state_evaluation(
    histories: dict[str, pd.DataFrame],
    *,
    cohorts: dict[str, tuple[str, ...]],
    fallback_groups: dict[str, str],
    group_intervals: pd.DataFrame,
    asof: str,
    start: str = "2018-01-01",
    horizons: tuple[int, ...] = (5, 10, 20),
    n_folds: int = 5,
    replay_builder=build_bottom_state_replay,
    progress=None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    """Run all frozen variants and return metrics, events, and manifest."""

def write_bottom_evaluation_outputs(
    metrics: pd.DataFrame,
    manifest: dict[str, object],
    *,
    report_path: str | Path,
    metrics_path: str | Path,
    manifest_path: str | Path,
) -> None:
    """Write deterministic Markdown, CSV, and strict JSON artifacts."""
```

- [ ] **Step 1: Write the failing synthetic runner test**

Inject disjoint two-stock cohorts and a deterministic replay builder. Assert six variants, three horizons, five folds, both event scopes, group/regime/drawdown slices, explicit exclusions, typed coverage records, no online authority, and deterministic output ordering.

- [ ] **Step 2: Verify RED**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_run_bottom_state_evaluation.BottomStateEvaluationRunnerTest -v
```

Expected: import failure because the runner does not exist.

- [ ] **Step 3: Implement frozen orchestration**

Reuse `select_touch_reaction_cohorts`, `latest_point_in_time_groups`, point-in-time group intervals, and whole-date fold assignment. For each ticker:

1. replay full causal evidence;
2. build full plus five independent state variants;
3. label events;
4. attach cohort, point-in-time group, market regime, fold, and variant;
5. retain coverage even for zero-event variants.

Reject overlapping cohorts and histories below 220 sessions. Do not write raw event rows by default.

- [ ] **Step 4: Implement reports and strict manifest**

CSV contains aggregate metrics and typed coverage rows. JSON records cohort tickers, exclusions, counts, variants, horizons, audits, performance conditions, reasons, model version, and `advisory_only`. Markdown is Chinese and reports the confirmation 10-day matched result, stage monotonicity, fold/group results, ablation deltas, zero-event variants, and limitations.

- [ ] **Step 5: Add CLI**

```bash
./venv/bin/python -m research.run_bottom_state_evaluation \
  --database data/research_prices.db \
  --asof 2026-07-24 \
  --start 2018-01-01 \
  --cohort-size 240 \
  --folds 5
```

Use the repository read-only. Defaults write:

```text
reports/bottom-state-causal-evaluation.csv
reports/bottom-state-causal-evaluation.json
reports/bottom-state-causal-evaluation.md
```

- [ ] **Step 6: Run runner tests and commit**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_run_bottom_state_evaluation -v
```

Expected: all tests pass.

Commit:

```bash
git add research/run_bottom_state_evaluation.py \
  tests/test_run_bottom_state_evaluation.py
git commit -m "research: add bottom state evaluation runner"
```

---

### Task 5: Run the real study, update evidence, and verify

**Files:**
- Create at runtime: `reports/bottom-state-causal-evaluation.csv`
- Create at runtime: `reports/bottom-state-causal-evaluation.json`
- Create at runtime: `reports/bottom-state-causal-evaluation.md`
- Modify: `docs/modeling-todo.md`
- Modify: `docs/dashboard.md`
- Modify: `docs/superpowers/specs/2026-07-30-bottom-state-causal-evaluation-design.md`

- [ ] **Step 1: Run a six-ticker smoke study under `/private/tmp`**

Use synthetic cohort overrides containing MU, NBIS, SNDK, ADBE, MRVL, and AMD. Inspect event counts, state ordering, exact mature horizons, pair uniqueness, match tiers, zero-event variants, and JSON validity. Fix implementation defects only; do not change frozen thresholds after reading outcomes.

- [ ] **Step 2: Run both real cohorts**

Run the CLI from Task 4 against `data/research_prices.db`. Track exact requested/evaluated/excluded tickers, event/state coverage, pairs, fold/group/regime counts, and execution time.

- [ ] **Step 3: Verify artifact consistency**

Run a read-only assertion script that checks:

```text
development ∩ confirmation = ∅
label counts = manifest counts
all requested cohort × variant × horizon coverage rows exist
pair_id is unique per positive/baseline side
all matched slices have equal positive and baseline counts
all horizons and five folds are present where mature data exists
decision.eligible = false when any audit or future holdout is false
decision.authority = advisory_only
```

- [ ] **Step 4: Record exact results without promotion**

Update the TODO, dashboard research instructions, and design conclusion with measured values and explicit failed gates. Do not add UI model cards or change decision policy.

- [ ] **Step 5: Run focused and complete verification**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
  ./venv/bin/python -m unittest \
  tests.test_bottom_state \
  tests.test_bottom_state_replay \
  tests.test_bottom_state_evaluation \
  tests.test_run_bottom_state_evaluation \
  tests.test_web_bottom_state_service -v

PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
  ./venv/bin/python -m unittest discover -s tests

PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
  ./venv/bin/python -m compileall -q research web tests

git diff --check
```

Expected: all tests pass, compile exits zero, and diff check is clean.

- [ ] **Step 6: Commit**

```bash
git add reports/bottom-state-causal-evaluation.csv \
  reports/bottom-state-causal-evaluation.json \
  reports/bottom-state-causal-evaluation.md \
  docs/modeling-todo.md \
  docs/dashboard.md \
  docs/superpowers/specs/2026-07-30-bottom-state-causal-evaluation-design.md
git commit -m "research: evaluate causal bottom states"
```

- [ ] **Step 7: Review and integrate**

Use `superpowers:requesting-code-review`, then `superpowers:verification-before-completion`, then `superpowers:finishing-a-development-branch`. Because the established user choice is local integration, fast-forward merge the verified feature branch into `main`, rerun the complete suite on `main`, preserve all protected untracked runtime files, and confirm the existing local service remains healthy.
