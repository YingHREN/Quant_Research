# Support First-Touch Reaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure whether frozen support zones produce an observable reaction after their first future touch, without the distance bias in the previous fixed-window break label.

**Architecture:** A focused `support_touch_reaction` module owns event creation, deduplication, first-touch search, mutually exclusive three-day labels, and continuous ATR diagnostics. A separate study runner reuses the existing frozen support variants, builds disjoint development and confirmation cohorts, attaches folds/groups/regimes, evaluates strict pairs and all eligible events, and publishes fail-closed evidence without changing online model authority.

**Tech Stack:** Python 3.9, pandas, NumPy, SQLite research store, `unittest`, Markdown/CSV/JSON reports.

## Global Constraints

- Every zone is frozen at the observation close and must never read a later model row.
- Waiting horizons are exactly 5, 10, and 20 sessions; reaction length is exactly 3 sessions including the touch day.
- A mature observation reserves `waiting_horizon + 2` future sessions, even when an earlier touch would have matured sooner.
- `failed` takes precedence over `accepted`; `accepted`, `failed`, and `ambiguous` are mutually exclusive.
- Volume is diagnostic only and must not define the reaction label.
- Repeated observations of one unresolved zone create one episode, not daily duplicate samples.
- The previous deterministic cohort and the disjoint confirmation cohort must never overlap.
- Historical group backfill keeps causal audit failed and blocks promotion.
- Ridge, downside vetoes, final policy, API payloads, and chart UI remain unchanged.
- Do not modify or commit `data/*.db-wal`, `data/*.db-shm`, or `research/high_level_reversal_study.py`.

---

### Task 1: Build the first-touch reaction label engine

**Files:**
- Create: `research/support_touch_reaction.py`
- Create: `tests/test_support_touch_reaction.py`

**Interfaces:**
- Consumes aligned point-in-time OHLCV history and one frozen signal frame.
- Produces one row per deduplicated support episode:

```python
def build_support_touch_reaction_rows(
    ticker: str,
    history: pd.DataFrame,
    signals: pd.DataFrame,
    *,
    waiting_horizon: int,
    reaction_sessions: int = 3,
) -> pd.DataFrame:
    """Return mature first-touch support episodes."""
```

- Output fields:

```text
ticker observation_date variant waiting_horizon
zone_lower zone_upper atr20 observation_distance_atr distance_bin
touch_status touch_type touch_date touch_delay_sessions
reaction_label accepted failed ambiguous reclaim_delay_sessions
maximum_rebound_atr maximum_penetration_atr close_change_from_touch
touch_volume_ratio event_end_date
```

- [ ] **Step 1: Write failing validation and maturity tests**

```python
def test_immature_tail_is_excluded_even_when_touch_would_be_early(self):
    history = _history_with_touch(total=9, touch_position=6)
    signals = _signals(history, active_position=5)
    result = build_support_touch_reaction_rows(
        "AAA", history, signals, waiting_horizon=3,
    )
    self.assertTrue(result.empty)

def test_rejects_misaligned_signal_dates(self):
    with self.assertRaisesRegex(ValueError, "align"):
        build_support_touch_reaction_rows(
            "AAA",
            _history(30),
            _signals(_history(30)).iloc[:-1],
            waiting_horizon=5,
        )
```

- [ ] **Step 2: Run the contract tests and verify RED**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_support_touch_reaction.SupportTouchReactionContractTest -v
```

Expected: import failure because `research.support_touch_reaction` does not exist.

- [ ] **Step 3: Implement strict inputs, ATR20, maturity, and stable empty output**

Require `Open`, `High`, `Low`, `Close`, `Volume`; require signal columns `variant`, `eligible`, `zone_lower`, `zone_upper`; reject duplicate or non-monotonic dates and non-finite OHLCV. Compute ATR20 only from information through the observation date and previous-20-session volume mean with `shift(1)`.

- [ ] **Step 4: Write failing touch-type and reaction-state tests**

Add separate tests for:

```python
def test_intersection_reclaimed_on_second_session_is_accepted(): ...
def test_gap_below_zone_without_reclaim_is_failed(): ...
def test_two_consecutive_closes_below_lower_are_failed(): ...
def test_half_atr_deep_close_is_failed(): ...
def test_failure_overrides_an_earlier_reclaim(): ...
def test_touch_without_reclaim_or_failure_is_ambiguous(): ...
def test_no_touch_is_retained_outside_touch_rate_denominator(): ...
```

Assert exact `touch_date`, delay, reclaim delay, ATR excursions, volume ratio, and mutually exclusive booleans.

- [ ] **Step 5: Run reaction tests and verify RED**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_support_touch_reaction.SupportTouchReactionLabelTest -v
```

Expected: missing or incorrect reaction fields.

- [ ] **Step 6: Implement first-touch search and three-day labels**

Use:

```python
intersects = day_low <= zone_upper and day_high >= zone_lower
gap_through = day_high < zone_lower
deep_failure = close < zone_lower - 0.5 * atr20
accepted = not failed and any(reaction_close >= zone_upper)
ambiguous = touched and not accepted and not failed
```

For a gap-through event, failure also occurs when no reaction close reclaims `zone_lower`.

- [ ] **Step 7: Write failing episode-deduplication and prefix tests**

```python
def test_same_unresolved_zone_creates_one_episode(): ...
def test_quarter_atr_zone_move_can_create_a_new_episode(): ...
def test_ten_session_spacing_can_create_a_new_episode(): ...
def test_appended_future_rows_do_not_change_mature_episodes(): ...
```

- [ ] **Step 8: Implement episode state and distance bins**

An episode remains active through waiting and reaction completion. After it ends, accept a new episode only when center movement is at least `0.25 × observation ATR20` or the new observation is at least 10 sessions after the previous episode observation. Emit distance bins:

```text
0_0.5_atr 0.5_1.0_atr 1.0_2.0_atr 2.0_3.5_atr
```

Reject candidates farther than `3.5 × ATR20`.

- [ ] **Step 9: Run all engine tests and commit**

Run:

```bash
./venv/bin/python -m unittest tests.test_support_touch_reaction -v
```

Expected: all tests pass.

Commit:

```bash
git add research/support_touch_reaction.py tests/test_support_touch_reaction.py
git commit -m "research: add support first-touch labels"
```

---

### Task 2: Build paired metrics and frozen cohort selection

**Files:**
- Create: `research/run_support_touch_reaction_study.py`
- Create: `tests/test_run_support_touch_reaction_study.py`

**Interfaces:**

```python
def select_touch_reaction_cohorts(
    groups: dict[str, str],
    *,
    cohort_size: int = 240,
    development_seed: int = 20260726,
    confirmation_seed: int = 20260729,
) -> dict[str, tuple[str, ...]]:
    """Return disjoint deterministic development and confirmation cohorts."""

def assign_reaction_folds(rows: pd.DataFrame, *, n_folds: int = 5) -> pd.DataFrame:
    """Assign whole observation dates to chronological folds."""

def evaluate_touch_reactions(rows: pd.DataFrame) -> pd.DataFrame:
    """Aggregate event, touch, and reaction metrics."""
```

- [ ] **Step 1: Write failing cohort tests**

```python
def test_cohorts_are_deterministic_disjoint_and_focus_preserving(self):
    first = select_touch_reaction_cohorts(_groups(700))
    second = select_touch_reaction_cohorts(_groups(700))
    self.assertEqual(first, second)
    self.assertTrue(set(first["development"]).isdisjoint(
        first["confirmation"]
    ))
    self.assertIn("MU", first["development"])
```

Also assert graceful smaller confirmation cohorts and no duplicate tickers.

- [ ] **Step 2: Verify RED and implement cohort selection**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_run_support_touch_reaction_study.TouchReactionCohortTest -v
```

Reuse `select_analysis_tickers` for the previous development cohort. Remove it from the group map before applying the confirmation seed.

- [ ] **Step 3: Write failing strict-pair and metric tests**

Construct rows where baseline and challenger have both common and unique event keys. Assert `paired` keeps only common `(ticker, observation_date, waiting_horizon, fold)` keys while `all_eligible` retains coverage rows.

Assert:

```text
event_count touch_count touch_rate gap_through_rate
accepted_rate failed_rate ambiguous_rate
mean_reclaim_delay mean_maximum_rebound_atr
mean_maximum_penetration_atr
```

Touch-conditioned rates must exclude `not_touched`.

- [ ] **Step 4: Verify RED and implement fold/pair/metric helpers**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_run_support_touch_reaction_study.TouchReactionMetricTest -v
```

Aggregate by:

```text
cohort comparison_scope variant waiting_horizon fold
group regime distance_bin
```

Also emit `group=all`, `regime=all`, and `distance_bin=all` summary slices without losing detailed rows.

- [ ] **Step 5: Add fail-closed research decision tests**

```python
def test_gate_never_promotes_when_group_audit_failed(): ...
def test_preregistered_metrics_are_reported_without_online_authority(): ...
```

The decision records performance conditions but always returns:

```python
{
    "eligible": False,
    "authority": "advisory_only",
    "reasons": ["causal_audit_failed", "future_holdout_required"],
}
```

unless a future version explicitly supplies both audits as true.

- [ ] **Step 6: Run study-helper tests and commit**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_run_support_touch_reaction_study -v
```

Commit:

```bash
git add research/run_support_touch_reaction_study.py \
  tests/test_run_support_touch_reaction_study.py
git commit -m "research: add support reaction study runner"
```

---

### Task 3: Run frozen real-data studies and publish evidence

**Files:**
- Modify: `research/run_support_touch_reaction_study.py`
- Modify: `tests/test_run_support_touch_reaction_study.py`
- Create at runtime: `reports/support-touch-reaction-study.csv`
- Create at runtime: `reports/support-touch-reaction-study.md`
- Create at runtime: `reports/support-touch-reaction-study.json`

**CLI:**

```bash
./venv/bin/python -m research.run_support_touch_reaction_study \
  --database data/research_prices.db \
  --asof 2026-07-24 \
  --start 2018-01-01 \
  --cohort-size 240 \
  --folds 5
```

- [ ] **Step 1: Write failing end-to-end synthetic runner test**

Inject small in-memory histories and frozen signal frames. Assert both cohorts, all horizons, all variants, folds, detailed distance slices, concise report, strict JSON manifest, and no online-authority field are produced deterministically.

- [ ] **Step 2: Verify RED and implement real runner**

For every ticker:

1. Reuse `build_ticker_signal_variants`.
2. Build first-touch episodes for all seven existing variants and three horizons.
3. Attach effective group intervals and observation-date market regime.
4. Assign common chronological folds.
5. Produce `all_eligible` and strict `paired` metrics.

Emit a typed coverage record for every cohort × variant × horizon combination,
including variants with zero eligible episodes. Zero-event variants may have no
performance-rate rows, but they must remain visible as `event_count=0` with an
explicit `status=unavailable_no_events` reason in CSV, Markdown, and JSON.

Do not write raw multi-million-row outcomes by default.

- [ ] **Step 3: Run a six-ticker smoke study**

Write outputs under `/private/tmp`, inspect cohort overlap, touch counts, label shares, distance bins, and JSON validity. Fix only implementation defects; do not change frozen thresholds after reading outcomes.

- [ ] **Step 4: Run both real cohorts**

Write the tracked CSV, Markdown, and JSON reports. Record exact excluded tickers, cohort sizes, episode counts, touch coverage, 5/10/20 results, fold/group/regime/distance results, and audit reasons.

- [ ] **Step 5: Validate report consistency**

Use a read-only assertion script to verify:

```text
development ∩ confirmation = ∅
horizons = {5, 10, 20}
folds = {1, 2, 3, 4, 5}
reaction labels are mutually exclusive
paired baseline/challenger sample counts match
all seven variants have coverage records, including typed zero-event variants
manifest decision is advisory and fail-closed
```

- [ ] **Step 6: Commit the real evidence**

```bash
git add research/run_support_touch_reaction_study.py \
  tests/test_run_support_touch_reaction_study.py \
  reports/support-touch-reaction-study.csv \
  reports/support-touch-reaction-study.md \
  reports/support-touch-reaction-study.json
git commit -m "research: evaluate first-touch support reactions"
```

---

### Task 4: Update the global evidence record and integrate

**Files:**
- Modify: `docs/modeling-todo.md`
- Modify: `docs/dashboard.md`
- Modify: `docs/superpowers/specs/2026-07-29-support-first-touch-reaction-design.md`

- [ ] **Step 1: Update documentation from measured evidence**

Record exact cohort/event/touch counts, accepted/failed/ambiguous deltas, fold/group/distance consistency, exclusions, and the causal-audit limitation. Keep lifecycle `research` and authority `advisory`.

- [ ] **Step 2: Run focused verification**

```bash
./venv/bin/python -m unittest \
  tests.test_support_touch_reaction \
  tests.test_run_support_touch_reaction_study \
  tests.test_run_historical_demand_support_study \
  tests.test_historical_demand_support \
  tests.test_resistance -v
```

- [ ] **Step 3: Run full verification**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
  ./venv/bin/python -m unittest discover -s tests
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
  ./venv/bin/python -m compileall -q research web tests
git diff --check
```

- [ ] **Step 4: Commit documentation**

```bash
git add docs/modeling-todo.md docs/dashboard.md \
  docs/superpowers/specs/2026-07-29-support-first-touch-reaction-design.md
git commit -m "docs: record support reaction evidence"
```

- [ ] **Step 5: Review and merge locally**

Review the complete diff against the design, verify reports from tracked inputs, fast-forward merge the isolated branch into `main`, rerun the full suite on `main`, and preserve all pre-existing runtime WAL/SHM and untracked research files.
