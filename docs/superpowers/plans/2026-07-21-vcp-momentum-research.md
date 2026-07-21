# VCP Momentum Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a leakage-safe historical VCP event scanner and test whether preregistered momentum features add out-of-sample explanatory information.

**Architecture:** Add an isolated `research/` package alongside the legacy factor and scoring code. The package will separate data alignment, VCP geometry, event transitions, momentum features, future outcomes, dataset generation, and statistical validation. Legacy behavior remains unchanged until the new pipeline has characterized tests and validated outputs.

**Tech Stack:** Python 3.9, pandas, NumPy, scikit-learn, SQLite, standard-library `unittest`; no new runtime dependency is required.

## Global Constraints

- Signals use information through date `t`; executable outcomes begin at the next available open.
- Missing ticker bars are explicit and are never replaced by stale prices for execution.
- VCP detection, economic outcomes, and portfolio decisions remain separate.
- Primary observation stage is the first `near_pivot` date.
- Primary momentum family is `mom_3_1`, `mom_6_1`, and `mom_12_1`, with a 21-trading-day skip.
- Primary continuous outcomes are 20-, 40-, and 60-day returns relative to SPY.
- Secondary barrier outcome is `+2 ATR` before `-1 ATR`; same-bar double touches are ambiguous.
- Compare VCP-only, momentum-only, and fixed interaction specifications on common rows.
- Do not modify `scoring/engine.py` or `engine_v2.py` during the research-pipeline tasks.
- The repository currently has no `.git` directory. Do not initialize Git or execute commit steps without user authorization; use the verification command at the end of each task as the checkpoint.

---

## File Structure

- `research/__init__.py`: package marker and version constants.
- `research/market_data.py`: SQLite loading, calendar alignment, and next-bar lookup.
- `research/vcp.py`: typed VCP geometry and deterministic detection.
- `research/events.py`: event stages, identities, transitions, and deduplication.
- `research/momentum.py`: point-in-time momentum values and cross-sectional ranks.
- `research/outcomes.py`: next-open forward returns and barrier outcomes.
- `research/build_events.py`: historical scan and versioned event-table CLI.
- `research/regression.py`: preregistered model matrices and chronological evaluation.
- `research/report.py`: regression, bootstrap, matched-control, and decision report CLI.
- `tests/helpers.py`: synthetic OHLCV builders.
- `tests/test_market_data.py`: date/freshness tests.
- `tests/test_vcp.py`: geometry and rejection tests.
- `tests/test_events.py`: state-machine and deduplication tests.
- `tests/test_momentum.py`: skipped-window and rank tests.
- `tests/test_outcomes.py`: next-open and ambiguous-barrier tests.
- `tests/test_build_events.py`: integrated event-table schema and leakage tests.
- `tests/test_regression.py`: folds, common samples, and baseline comparison tests.

---

### Task 1: Establish the Research Package and Fresh-Bar Data Contract

**Files:**
- Create: `research/__init__.py`
- Create: `research/market_data.py`
- Create: `tests/__init__.py`
- Create: `tests/helpers.py`
- Create: `tests/test_market_data.py`

**Interfaces:**
- Produces: `load_price_panel(db_path: str, tickers: Sequence[str]) -> dict[str, pd.DataFrame]`
- Produces: `bar_on(frame: pd.DataFrame, date: pd.Timestamp) -> pd.Series | None`
- Produces: `next_bar(frame: pd.DataFrame, after: pd.Timestamp) -> tuple[pd.Timestamp, pd.Series] | None`
- Produces: `make_ohlcv(closes, highs=None, lows=None, opens=None, volumes=None, start="2020-01-01") -> pd.DataFrame`

- [ ] **Step 1: Write failing freshness and next-bar tests**

```python
# tests/test_market_data.py
import unittest
import pandas as pd
from research.market_data import bar_on, next_bar
from tests.helpers import make_ohlcv

class MarketDataTest(unittest.TestCase):
    def test_bar_on_does_not_forward_fill_missing_date(self):
        frame = make_ohlcv([10, 11, 12]).drop(pd.Timestamp("2020-01-02"))
        self.assertIsNone(bar_on(frame, pd.Timestamp("2020-01-02")))

    def test_next_bar_returns_actual_later_bar_and_open(self):
        frame = make_ohlcv([10, 11, 12], opens=[9, 10.5, 11.5])
        date, bar = next_bar(frame, pd.Timestamp("2020-01-01"))
        self.assertEqual(date, pd.Timestamp("2020-01-02"))
        self.assertEqual(float(bar["Open"]), 10.5)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify the import fails**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_market_data -v`

Expected: `ERROR` with `ModuleNotFoundError: No module named 'research'`.

- [ ] **Step 3: Implement minimal package, fixture builder, and data contract**

```python
# research/__init__.py
DETECTOR_VERSION = "vcp-research-v1"
FEATURE_SPEC_VERSION = "momentum-v1"

# research/market_data.py
from collections.abc import Sequence
import sqlite3
import pandas as pd

def load_price_panel(db_path: str, tickers: Sequence[str]) -> dict[str, pd.DataFrame]:
    panel = {}
    with sqlite3.connect(db_path) as con:
        for ticker in tickers:
            df = pd.read_sql_query(
                "SELECT date,open,high,low,close,volume FROM prices WHERE ticker=? ORDER BY date",
                con, params=(ticker,))
            if df.empty:
                continue
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
            df.columns = ["Open", "High", "Low", "Close", "Volume"]
            panel[ticker] = df
    return panel

def bar_on(frame, date):
    date = pd.Timestamp(date)
    return frame.loc[date] if date in frame.index else None

def next_bar(frame, after):
    later = frame.index[frame.index > pd.Timestamp(after)]
    if len(later) == 0:
        return None
    date = later[0]
    return date, frame.loc[date]
```

Implement `tests/helpers.py::make_ohlcv` with business-day dates and explicit OHLCV columns.

- [ ] **Step 4: Run the focused and discovery test suites**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_market_data -v`

Expected: `Ran 2 tests ... OK`.

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest discover -s tests -v`

Expected: all discovered tests pass.

---

### Task 2: Implement Typed VCP Geometry with Dated Legs

**Files:**
- Create: `research/vcp.py`
- Create: `tests/test_vcp.py`

**Interfaces:**
- Produces: `ContractionLeg(peak_date, trough_date, peak, trough, depth_pct, mean_volume, confirmed)`
- Produces: `VCPPattern(asof_date, accepted, stage, base_start, base_end, legs, pending_leg, pivot, pivot_date, distance_to_pivot_pct, reject_reason, metrics)`
- Produces: `detect_vcp(history: pd.DataFrame, asof: pd.Timestamp | None = None) -> VCPPattern`

- [ ] **Step 1: Add synthetic geometry tests**

Tests must assert these independent behaviors:

```python
class VCPDetectorTest(unittest.TestCase):
    def test_decreasing_swings_return_dated_confirmed_legs(self):
        pattern = detect_vcp(textbook_vcp_fixture())
        self.assertTrue(pattern.accepted)
        self.assertGreaterEqual(len(pattern.legs), 2)
        self.assertGreater(pattern.legs[0].depth_pct, pattern.legs[-1].depth_pct)
        self.assertLess(pattern.legs[0].peak_date, pattern.legs[0].trough_date)

    def test_increasing_swings_are_rejected(self):
        pattern = detect_vcp(increasing_swings_fixture())
        self.assertFalse(pattern.accepted)
        self.assertEqual(pattern.reject_reason, "contractions_not_decreasing")

    def test_monotonic_rally_is_not_a_base(self):
        pattern = detect_vcp(monotonic_rally_fixture())
        self.assertFalse(pattern.accepted)

    def test_pending_final_leg_is_not_counted_as_confirmed(self):
        pattern = detect_vcp(unconfirmed_tail_fixture())
        self.assertIsNotNone(pattern.pending_leg)
        self.assertFalse(pattern.pending_leg.confirmed)
```

- [ ] **Step 2: Verify RED**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_vcp -v`

Expected: import failure for `research.vcp`.

- [ ] **Step 3: Implement immutable dataclasses and swing extraction**

Use frozen dataclasses. Detect extrema from High/Low, require alternating pivots, and retain the current unconfirmed extremum separately. Use ATR percentage to set a threshold clipped to 3%–10%.

```python
@dataclass(frozen=True)
class ContractionLeg:
    peak_date: pd.Timestamp
    trough_date: pd.Timestamp
    peak: float
    trough: float
    depth_pct: float
    mean_volume: float
    confirmed: bool = True

@dataclass(frozen=True)
class VCPPattern:
    asof_date: pd.Timestamp
    accepted: bool
    stage: str
    base_start: pd.Timestamp | None
    base_end: pd.Timestamp
    legs: tuple[ContractionLeg, ...]
    pending_leg: ContractionLeg | None
    pivot: float | None
    pivot_date: pd.Timestamp | None
    distance_to_pivot_pct: float | None
    reject_reason: str | None
    metrics: dict[str, float]
```

- [ ] **Step 4: Implement deterministic base selection**

Evaluate 20–80-day candidate windows. Reject depth over 35%, monotonic efficiency over 0.50 when return exceeds 15%, price below MA50, and non-rising long trend. Score only accepted candidates with the frozen tuple:

```python
score = (
    number_of_confirmed_legs,
    -last_first_ratio,
    -terminal_range_pct,
    -days_since_last_pivot,
    -base_length,
)
```

Choose the lexicographic maximum and use earliest base start as the final tie-breaker. This rule is deterministic and does not use future returns.

- [ ] **Step 5: Run tests and legacy smoke check**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_vcp -v`

Expected: all VCP tests pass.

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python daily_monitor.py --no-fetch`

Expected: legacy monitor still exits 0 because its detector was not changed.

---

### Task 3: Add the Historical Event State Machine

**Files:**
- Create: `research/events.py`
- Create: `tests/test_events.py`

**Interfaces:**
- Consumes: `detect_vcp(...) -> VCPPattern`
- Produces: `VCPEvent(event_id, ticker, base_start, first_seen, near_pivot_date, breakout_date, invalidated_date, expired_date, transitions)`
- Produces: `scan_ticker_events(ticker: str, history: pd.DataFrame, min_history: int = 252, max_lifetime: int = 60) -> list[VCPEvent]`

- [ ] **Step 1: Write transition and deduplication tests**

```python
def test_repeated_near_pivot_days_are_one_event(self):
    events = scan_ticker_events("TEST", multi_day_vcp_fixture())
    self.assertEqual(len(events), 1)
    self.assertEqual(sum(t.stage == "near_pivot" for t in events[0].transitions), 1)

def test_breakout_uses_pivot_known_before_breakout(self):
    event = scan_ticker_events("TEST", breakout_fixture())[0]
    self.assertIsNotNone(event.breakout_date)
    prior = [t for t in event.transitions if t.date < event.breakout_date]
    self.assertTrue(any(t.pivot == event.breakout_pivot for t in prior))

def test_failed_structure_becomes_invalidated_not_successful(self):
    event = scan_ticker_events("TEST", invalidation_fixture())[0]
    self.assertIsNotNone(event.invalidated_date)
    self.assertIsNone(event.breakout_date)
```

- [ ] **Step 2: Verify RED**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_events -v`

Expected: import failure for `research.events`.

- [ ] **Step 3: Implement stable event identities and transitions**

Event identity is SHA-1 of `ticker|base_start_date|rounded_initial_pivot`. The scanner calls the detector on slices ending at each date, opens an event on first `forming`, records the first `near_pivot`, and evaluates later bars against only the stored pre-breakout pivot. It closes the event on breakout, invalidation, or expiry.

The preregistered stage rules are:

```python
NEAR_PIVOT_PCT = 5.0
BREAKOUT_VOLUME_RATIO = 1.4
INVALIDATION_ATR = 1.0
MAX_EVENT_LIFETIME = 60
```

Store volume ratio as an attribute. Report both price-only breakout and volume-confirmed breakout; do not discard the former.

- [ ] **Step 4: Verify event tests and full discovery**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_events -v`

Expected: all event tests pass.

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest discover -s tests -v`

Expected: all discovered tests pass.

---

### Task 4: Add Point-in-Time Momentum Features

**Files:**
- Create: `research/momentum.py`
- Create: `tests/test_momentum.py`

**Interfaces:**
- Produces: `momentum_features(history: pd.DataFrame, benchmark: pd.DataFrame, asof: pd.Timestamp) -> dict[str, float | bool | None]`
- Produces: `add_cross_sectional_ranks(rows: pd.DataFrame, date_col="observation_date") -> pd.DataFrame`

- [ ] **Step 1: Write exact-window tests**

```python
def test_mom_3_1_skips_latest_21_bars(self):
    h = indexed_price_fixture(300)
    got = momentum_features(h, h, h.index[-1])
    expected = h.Close.iloc[-22] / h.Close.iloc[-64] - 1
    self.assertAlmostEqual(got["mom_3_1"], expected)

def test_short_history_does_not_fake_twelve_month_momentum(self):
    h = indexed_price_fixture(200)
    got = momentum_features(h, h, h.index[-1])
    self.assertIsNone(got["mom_12_1"])
    self.assertTrue(got["mom_12_1_missing"])

def test_ranks_are_computed_within_the_same_date(self):
    ranked = add_cross_sectional_ranks(two_date_feature_rows())
    self.assertEqual(ranked.groupby("observation_date")["mom_6_1_rank"].count().tolist(), [3, 3])
```

- [ ] **Step 2: Verify RED**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_momentum -v`

Expected: import failure for `research.momentum`.

- [ ] **Step 3: Implement raw, excess, and volatility-adjusted momentum**

Use positional windows ending at `-22` so the latest 21 completed bars are excluded. Return raw momentum, SPY-excess momentum, recent 21-day return, trailing 63-day realized volatility, and raw momentum divided by volatility. Preserve `None` plus explicit missing flags.

- [ ] **Step 4: Implement same-date percentile ranks**

Use `groupby(date_col)[feature].rank(pct=True, method="average")`; never calculate ranks before grouping by observation date.

- [ ] **Step 5: Run focused and full tests**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_momentum -v`

Expected: all momentum tests pass.

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest discover -s tests -v`

Expected: all discovered tests pass.

---

### Task 5: Centralize Leakage-Safe Outcomes

**Files:**
- Create: `research/outcomes.py`
- Create: `tests/test_outcomes.py`

**Interfaces:**
- Consumes: `next_bar(...)`
- Produces: `forward_outcomes(history, benchmark, observation_date, horizons=(20, 40, 60)) -> dict`
- Produces: `barrier_outcome(history, observation_date, horizon=40, up_atr=2.0, down_atr=1.0) -> dict`

- [ ] **Step 1: Write next-open and ambiguity tests**

```python
def test_forward_return_enters_at_next_open(self):
    got = forward_outcomes(gap_fixture(), benchmark_fixture(), pd.Timestamp("2020-01-10"), (20,))
    self.assertEqual(got["entry_date"], pd.Timestamp("2020-01-13"))
    self.assertEqual(got["entry_price"], gap_fixture().loc["2020-01-13", "Open"])

def test_same_bar_double_touch_is_ambiguous(self):
    got = barrier_outcome(double_touch_fixture(), pd.Timestamp("2020-01-10"))
    self.assertEqual(got["barrier_label"], "ambiguous")

def test_missing_next_bar_has_no_executable_outcome(self):
    got = forward_outcomes(last_bar_fixture(), benchmark_fixture(), pd.Timestamp("2020-01-10"), (20,))
    self.assertTrue(got["missing_entry_bar"])
```

- [ ] **Step 2: Verify RED**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_outcomes -v`

Expected: import failure for `research.outcomes`.

- [ ] **Step 3: Implement frozen-ATR next-open outcomes**

ATR is computed only from bars through the observation date. Future paths start at the next actual ticker bar. Benchmark returns use the same entry and exit calendar dates; if either benchmark bar is missing, relative return is missing and flagged.

- [ ] **Step 4: Run focused and full tests**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_outcomes -v`

Expected: all outcome tests pass.

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest discover -s tests -v`

Expected: all discovered tests pass.

---

### Task 6: Build a Versioned Historical Event Dataset

**Files:**
- Create: `research/build_events.py`
- Create: `tests/test_build_events.py`
- Create: `output/research/.gitkeep`

**Interfaces:**
- Consumes: market panel, event scanner, momentum features, and outcomes.
- Produces: `build_event_table(panel, benchmark, tickers, stages=("near_pivot",)) -> pd.DataFrame`
- CLI: `python -m research.build_events --db data/prices.db --output output/research/vcp_events_v1.csv`

- [ ] **Step 1: Write integrated schema and temporal-integrity tests**

Assert that one primary row exists per event, required version columns are present, `observation_date < entry_date`, all feature source dates are at or before observation date, and no outcome column is used by detector or momentum functions.

```python
REQUIRED = {
    "event_id", "ticker", "observation_stage", "observation_date", "entry_date",
    "base_start", "pivot", "mom_3_1", "mom_6_1", "mom_12_1",
    "rel_ret_20", "rel_ret_40", "rel_ret_60", "barrier_label",
    "detector_version", "feature_spec_version",
}
self.assertTrue(REQUIRED.issubset(table.columns))
self.assertTrue((table.observation_date < table.entry_date).all())
self.assertFalse(table.event_id.duplicated().any())
```

- [ ] **Step 2: Verify RED**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_build_events -v`

Expected: import failure for `research.build_events`.

- [ ] **Step 3: Implement table assembly and atomic versioned output**

Build rows first, add cross-sectional ranks second, then sort by observation date and ticker. Reject an output path that already exists unless `--force` is explicitly supplied. With `--force`, write to a temporary sibling and replace only after successful CSV serialization.

- [ ] **Step 4: Run fixture integration tests**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_build_events -v`

Expected: all integration tests pass.

- [ ] **Step 5: Generate a development event table from the local database**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m research.build_events --db data/prices.db --output output/research/vcp_events_v1.csv`

Expected: exit 0; report ticker count, event count, stage counts, date range, missing-feature counts, and output path. Do not interpret returns in this task.

---

### Task 7: Implement Preregistered Regression and Walk-Forward Evaluation

**Files:**
- Create: `research/regression.py`
- Create: `tests/test_regression.py`

**Interfaces:**
- Produces: `chronological_folds(df, horizon, n_folds=5) -> list[tuple[np.ndarray, np.ndarray]]`
- Produces: `design_matrix(df, specification, train_stats=None) -> tuple[np.ndarray, dict]`
- Produces: `evaluate_specifications(df, target="rel_ret_40") -> pd.DataFrame`
- Specifications: `vcp_only`, `momentum_only`, `vcp_momentum`

- [ ] **Step 1: Write purge and common-sample tests**

```python
def test_training_outcome_window_ends_before_test_start(self):
    for train_idx, test_idx in chronological_folds(rows(), horizon=40):
        train = rows().iloc[train_idx]
        test = rows().iloc[test_idx]
        self.assertLess(train.observation_date.max(), test.observation_date.min() - pd.offsets.BDay(40))

def test_all_specs_use_identical_common_rows(self):
    result = evaluate_specifications(model_fixture())
    self.assertEqual(result.groupby("specification").n_obs.first().nunique(), 1)
```

- [ ] **Step 2: Verify RED**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_regression -v`

Expected: import failure for `research.regression`.

- [ ] **Step 3: Implement frozen feature lists**

```python
VCP_FEATURES = (
    "n_legs", "last_first_ratio", "contraction_slope", "terminal_range_pct",
    "volume_dryup_ratio", "distance_to_pivot_pct", "base_depth_pct",
)
MOMENTUM_FEATURES = (
    "mom_3_1_rank", "mom_6_1_rank", "mom_12_1_rank", "ret_1m",
    "excess_mom_6_1", "vol_adjusted_mom_6_1",
)
INTERACTIONS = (
    ("last_first_ratio", "mom_6_1_rank"),
    ("volume_dryup_ratio", "mom_6_1_rank"),
    ("terminal_range_pct", "mom_12_1_rank"),
)
```

Training-fold medians and standardization parameters are applied to test rows. Continuous models use `sklearn.linear_model.Ridge(alpha=1.0)`; binary models use `LogisticRegression(C=1.0, max_iter=2000)`. Hyperparameters are fixed rather than searched on the current data.

- [ ] **Step 4: Implement evaluation metrics**

For continuous targets report out-of-sample correlation, MAE, sign stability by fold, and incremental out-of-sample R-squared over the nested baseline. For the barrier target report log loss, Brier score, ROC AUC where both classes exist, and calibration by prediction quintile.

- [ ] **Step 5: Run focused and full tests**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_regression -v`

Expected: all regression tests pass.

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest discover -s tests -v`

Expected: all discovered tests pass.

---

### Task 8: Add Bootstrap, Matched Controls, and Decision Reporting

**Files:**
- Create: `research/report.py`
- Create: `tests/test_report.py`
- Create: `docs/research/vcp-momentum-preregistration-v1.md`

**Interfaces:**
- Produces: `date_block_bootstrap(values_by_date, block=40, n_boot=2000, seed=42) -> tuple[float, float]`
- Produces: `match_controls(events, snapshots) -> pd.DataFrame`
- Produces: `apply_bh_fdr(p_values: Sequence[float]) -> np.ndarray`
- CLI: `python -m research.report --events output/research/vcp_events_v1.csv --output output/research/vcp_momentum_report_v1.md`

- [ ] **Step 1: Write deterministic bootstrap, matching, and FDR tests**

Tests assert identical bootstrap results for the same seed, controls share ticker and market-regime bucket but not event dates, and Benjamini-Hochberg adjusted p-values are monotonic after sorting.

- [ ] **Step 2: Verify RED**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_report -v`

Expected: import failure for `research.report`.

- [ ] **Step 3: Write the frozen preregistration before viewing model results**

The document records the exact feature tuples from Task 7, primary target `rel_ret_40`, secondary targets, three model specifications, five chronological folds, 40-day embargo, 40-date bootstrap blocks, 2,000 bootstrap draws, seed 42, interaction list, and validation gates from the design document.

- [ ] **Step 4: Implement reporting without selective omission**

The Markdown report includes dataset coverage, event-stage counts, missingness, all specifications, every fold, all targets, adjusted p-values, matched-control effects, bootstrap intervals, concentration by ticker/sector when available, limitations, and one of `PASS`, `FAIL`, or `UNDERPOWERED` for each factor family.

- [ ] **Step 5: Run report tests and full suite**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_report -v`

Expected: all report tests pass.

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest discover -s tests -v`

Expected: all discovered tests pass.

- [ ] **Step 6: Generate and inspect the first honest research report**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m research.report --events output/research/vcp_events_v1.csv --output output/research/vcp_momentum_report_v1.md`

Expected: exit 0 and a report containing all preregistered sections. A null or underpowered result is an acceptable successful execution.

---

### Task 9: Characterize Legacy Conflicts and Decide Integration

**Files:**
- Create: `tests/test_legacy_scoring_contract.py`
- Create: `docs/research/vcp-integration-decision-v1.md`
- Modify only after an explicit follow-up approval: `scoring/engine.py`, `engine_v2.py`, `daily_monitor.py`

**Interfaces:**
- Consumes: the completed research report.
- Produces: a documented decision to keep VCP descriptive, place it in shadow mode, or integrate a validated feature.

- [ ] **Step 1: Characterize current legacy behavior without changing it**

Write tests showing that legacy `evaluate(..., price_only=True)` currently includes `score_vcp`, emits `vcp_breakout`, and may emit `buyable_now`. These tests document the known contradiction rather than endorsing it.

- [ ] **Step 2: Run characterization tests**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_legacy_scoring_contract -v`

Expected: tests pass against current legacy behavior.

- [ ] **Step 3: Write the integration decision**

If the research result is `FAIL` or `UNDERPOWERED`, specify that VCP remains display-only and propose removing it from new selection paths. If it is `PASS`, specify shadow-mode monitoring with no capital allocation until new forward data are accumulated. Do not alter live or backtest selection behavior in this task without separate user approval.

- [ ] **Step 4: Run final verification**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest discover -s tests -v`

Expected: zero failures and zero errors.

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m py_compile research/*.py tests/*.py`

Expected: exit 0 with bytecode written only under `/private/tmp/stock-screener-pycache`.

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python daily_monitor.py --no-fetch`

Expected: exit 0; legacy monitoring remains operational.

