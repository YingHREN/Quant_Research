# Historical Demand Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a causal, remembered historical-demand support zone to the stock dashboard, integrate it into support and bottoming diagnostics without double counting, and measure its out-of-sample value before granting decision authority.

**Architecture:** A new research module consumes point-in-time OHLCV plus already-computed demand and entry-signal evidence, emits one immutable diagnostic row per session, and owns event memory, ATR-normalized clustering, decay, retests, and invalidation. A thin web service serializes those rows onto existing chart dates and asks the existing support module to merge only a valid below-price zone; model-output and chart UI layers remain consumers of the stable payload. A separate study runner compares the existing support baseline against the challenger without changing Ridge or the final policy.

**Tech Stack:** Python 3.9, pandas, NumPy, Flask service contracts, vanilla JavaScript, Lightweight Charts, `unittest`, SQLite-backed research inputs, Markdown/CSV reports.

## Global Constraints

- Every historical output must use only data available through its observation date; future appended rows must not alter a mature prefix.
- Model lifecycle is `research`; authority is `advisory`.
- Rule scores are 0–100 evidence scores, never probabilities.
- UI copy must say “历史需求支撑区” / “Historical demand support”, never “institutional cost line” or guaranteed institutional buying.
- Daily OHLCV evidence must remain distinct from future minute/tick evidence.
- Event memory uses a 40-session half-life and a hard 120-session maximum.
- Candidate zones cluster only when their centers are within `0.5 × ATR20`.
- Missing ATR, volume, alignment, or context must degrade only this model to an explicit unavailable state.
- Historical-demand location evidence must not be counted again in the current-session demand score.
- Ridge raw forecasts and `forecast_decision_policy` must not consume this model in this implementation.
- Do not modify or commit runtime database WAL/SHM files or `research/high_level_reversal_study.py`.

---

### Task 1: Build the causal event and zone engine

**Files:**
- Create: `research/historical_demand_support.py`
- Create: `tests/test_historical_demand_support.py`

**Interfaces:**
- Consumes:
  - `history: pd.DataFrame` with `Open`, `High`, `Low`, `Close`, `Volume`.
  - `demand_rows: pd.DataFrame` aligned one-to-one with history and containing the existing demand-condition fields.
  - `entry_signal_rows: Sequence[Mapping[str, object]]` aligned one-to-one with history and containing Pocket Pivot evidence.
  - Optional `qqq_close: pd.Series` and `sector_close: pd.Series`.
- Produces:

```python
MODEL_KEY = "historical_demand_support_v1"
MODEL_VERSION = "v1"

def build_historical_demand_support_rows(
    history: pd.DataFrame,
    *,
    demand_rows: pd.DataFrame,
    entry_signal_rows: Sequence[Mapping[str, object]],
    qqq_close: pd.Series | None = None,
    sector_close: pd.Series | None = None,
) -> pd.DataFrame:
    """Return one point-in-time historical-demand support row per session."""
```

- Output columns are the `historical_demand_support_*` fields defined in the approved specification, plus `historical_demand_support_unavailable_reason`.

- [ ] **Step 1: Write failing validation and unavailable-state tests**

```python
class HistoricalDemandSupportContractTest(unittest.TestCase):
    def test_rejects_misaligned_evidence(self):
        with self.assertRaisesRegex(ValueError, "align"):
            build_historical_demand_support_rows(
                _history(40),
                demand_rows=pd.DataFrame(index=_history(40).index[:-1]),
                entry_signal_rows=[{}] * 40,
            )

    def test_short_history_is_explicitly_unavailable(self):
        result = _build(_history(19))
        self.assertEqual(result.iloc[-1]["historical_demand_support_state"], "unavailable")
        self.assertEqual(
            result.iloc[-1]["historical_demand_support_unavailable_reason"],
            "insufficient_atr_history",
        )
```

- [ ] **Step 2: Run the contract tests and verify they fail**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_historical_demand_support.HistoricalDemandSupportContractTest -v
```

Expected: import failure because `research.historical_demand_support` does not exist.

- [ ] **Step 3: Implement strict normalization and stable unavailable rows**

Create constants for the seven permitted states, required columns, half-life, maximum age, and output columns. Reject duplicate/non-monotonic dates, non-finite OHLCV, and evidence length/index mismatches. Return typed nulls, empty lists, `coverage=0.0`, and a stable reason when ATR20 cannot be formed.

- [ ] **Step 4: Run contract tests and verify they pass**

Run the command from Step 2.

Expected: all contract tests pass.

- [ ] **Step 5: Write failing event-detection and same-day deduplication tests**

```python
def test_absorption_and_pocket_pivot_on_same_bar_create_one_event(self):
    result = _build(
        _absorption_history(),
        demand_conditions={40: ["buyer_absorption", "up_volume_confirmation"]},
        pocket_pivots={40: True},
    )
    row = result.iloc[40]
    self.assertEqual(row["historical_demand_support_event_count"], 1)
    self.assertIn("buyer_absorption", row["historical_demand_support_event_types"])
```

Add separate cases for up-volume confirmation, buyer absorption, Pocket Pivot, breakout acceptance, and breakout follow-through. Assert stable event dates, ATR-bounded zone width, event volume ratio, close location, and reason codes.

- [ ] **Step 6: Run event tests and verify they fail**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_historical_demand_support.HistoricalDemandSupportEventTest -v
```

Expected: event fields remain unavailable or absent.

- [ ] **Step 7: Implement event extraction and price anchors**

Represent internal events with an immutable dataclass:

```python
@dataclass(frozen=True)
class DemandEvent:
    date: pd.Timestamp
    event_type: str
    center: float
    lower: float
    upper: float
    atr20: float
    volume_ratio: float
    close_location: float
    environment_confirmed: bool | None
```

Merge same-session conditions into one event using explicit priority:

```python
EVENT_PRIORITY = (
    "breakout_follow_through",
    "breakout_acceptance",
    "buyer_absorption",
    "pocket_pivot",
    "up_volume_confirmation",
)
```

Use only the same session’s ATR and price data to create a zone whose half-width is between `0.15 × ATR20` and `0.5 × ATR20`.

- [ ] **Step 8: Write failing clustering, decay, retest, and invalidation tests**

Include exact boundaries:

```python
def test_clusters_centers_at_half_atr_boundary(self):
    row = _cluster_case(center_gap_atr=0.5).iloc[-1]
    self.assertEqual(row["historical_demand_support_event_count"], 2)

def test_drops_event_after_120_sessions(self):
    row = _single_event_case(age=121).iloc[-1]
    self.assertEqual(row["historical_demand_support_state"], "unavailable")

def test_high_volume_half_atr_break_invalidates_immediately(self):
    row = _break_case(distance_atr=0.5, volume_ratio=1.2).iloc[-1]
    self.assertEqual(row["historical_demand_support_state"], "invalidated")
```

Also test the 40-session score half-life, two closes below the lower bound, intraday penetration followed by acceptance, successful retest, untested/approaching/testing/weakened states, and creation of a new zone after an old zone invalidates.

- [ ] **Step 9: Implement remembered clustering and state transitions**

At every session:

1. Keep only events no older than 120 sessions.
2. Apply exponential weight `0.5 ** (age / 40.0)`.
3. Cluster event centers using the current session’s `0.5 × ATR20`.
4. Select the closest still-valid cluster whose upper bound is not materially above current close.
5. Score event quality, relative volume, overlap, retest acceptance, and environment with caps `30/20/20/20/10`.
6. Apply invalidation before acceptance or decay.
7. Emit fresh lists and finite-or-null scalars.

- [ ] **Step 10: Write and pass prefix-invariance tests**

```python
def test_future_append_does_not_change_historical_rows(self):
    prefix = _build(_history(140))
    extended = _build(_history(180))
    pd.testing.assert_frame_equal(prefix, extended.loc[prefix.index])
```

Run:

```bash
./venv/bin/python -m unittest tests.test_historical_demand_support -v
```

Expected: all tests pass with no warnings.

- [ ] **Step 11: Commit the core engine**

```bash
git add research/historical_demand_support.py tests/test_historical_demand_support.py
git commit -m "research: add historical demand support engine"
```

---

### Task 2: Integrate demand zones into near support without double counting

**Files:**
- Modify: `research/resistance.py`
- Modify: `research/bottom_state.py`
- Modify: `tests/test_resistance.py`
- Modify: `tests/test_bottom_state.py`

**Interfaces:**
- Consumes one row from `build_historical_demand_support_rows`.
- Produces:

```python
def merge_historical_demand_support(
    support_row: Mapping[str, object],
    demand_row: Mapping[str, object],
    *,
    close: float,
    atr20: float,
) -> dict[str, object]:
    """Return a copied near-support row augmented by one valid demand zone."""
```

- Adds the support source key `historical_demand_zone`.
- Leaves baseline near-support results byte-for-byte equivalent when the model is unavailable, invalidated, above price, or malformed.

- [ ] **Step 1: Write failing support-integration tests**

```python
def test_active_historical_demand_zone_becomes_near_support_source(self):
    merged = merge_historical_demand_support(
        _baseline_support(),
        _active_demand(lower=94.0, upper=96.0, score=80.0),
        close=100.0,
        atr20=4.0,
    )
    self.assertIn("historical_demand_zone", merged["near_support_sources"])
    self.assertLessEqual(merged["near_support_score"], 100)

def test_invalidated_zone_does_not_change_baseline(self):
    baseline = _baseline_support()
    self.assertEqual(
        merge_historical_demand_support(
            baseline,
            _invalidated_demand(),
            close=100.0,
            atr20=4.0,
        ),
        baseline,
    )
```

Test selection of the nearest cluster, `0.5 ATR` merge tolerance, source ordering, copied list semantics, malformed rows, and a demand zone that is above current close.

- [ ] **Step 2: Run support tests and verify they fail**

Run:

```bash
./venv/bin/python -m unittest tests.test_resistance -v
```

Expected: missing `merge_historical_demand_support`.

- [ ] **Step 3: Implement capped support integration**

Merge the historical zone with the existing near-support cluster only when the gap is at most `0.5 × ATR20`; otherwise replace the baseline only if the historical zone is closer below current price. Add at most the existing one-source contribution to strength—do not add the historical score directly. Preserve `SUPPORT_SOURCE_ORDER` with `historical_demand_zone` last.

- [ ] **Step 4: Write failing bottom-location isolation tests**

```python
def test_historical_support_changes_location_but_not_demand_subscore(self):
    baseline = build_bottom_state_rows(history, evidence_without_history)
    challenger = build_bottom_state_rows(history, evidence_with_history)
    self.assertGreater(
        challenger.iloc[-1]["bottom_location_score"],
        baseline.iloc[-1]["bottom_location_score"],
    )
    self.assertEqual(
        challenger.iloc[-1]["bottom_demand_score"],
        baseline.iloc[-1]["bottom_demand_score"],
    )
```

- [ ] **Step 5: Implement bottom-location consumption**

Pass these fields through the bottom evidence contract:

```python
"historical_demand_support_state"
"historical_demand_support_score"
"historical_demand_support_invalidation_level"
```

Use active/approaching/testing/accepted historical support only in `_location_score`. Cap the entire location group at its existing maximum. Add `historical_demand_support` to conditions and `historical_demand_support_invalidated` to counter-conditions without changing `_demand_score`.

- [ ] **Step 6: Run focused integration tests**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_resistance \
  tests.test_bottom_state -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit support integration**

```bash
git add research/resistance.py research/bottom_state.py \
  tests/test_resistance.py tests/test_bottom_state.py
git commit -m "research: integrate remembered demand support"
```

---

### Task 3: Attach the model to every chart path

**Files:**
- Create: `web/services/historical_demand_support.py`
- Create: `tests/test_web_historical_demand_support_service.py`
- Modify: `web/services/bottom_state.py`
- Modify: `web/app.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Produces:

```python
def attach_historical_demand_support_rows(
    chart: list[dict[str, object]],
    ticker: str,
    histories: Mapping[str, pd.DataFrame],
) -> None:
    """Attach causal demand support and merge valid zones into near support."""
```

- Reads existing `demand_confirmation_conditions`, Pocket Pivot fields, OHLCV history, QQQ history, and point-in-time sector benchmarks.
- Mutates only existing chart rows by matching ISO session dates.

- [ ] **Step 1: Write failing service default and serialization tests**

```python
def test_missing_history_degrades_only_historical_support(self):
    chart = [{"time": "2026-07-01", "close": 100.0}]
    attach_historical_demand_support_rows(chart, "AAA", {})
    self.assertEqual(
        chart[0]["historical_demand_support_state"],
        "unavailable",
    )
    self.assertEqual(chart[0]["close"], 100.0)
```

Test finite-or-null serialization, fresh list values, invalid input history, missing sector/QQQ context, and date-only mutation.

- [ ] **Step 2: Run service tests and verify they fail**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_historical_demand_support_service -v
```

Expected: module import failure.

- [ ] **Step 3: Implement the thin chart service**

Build aligned demand and entry evidence from existing chart rows, call the research model, serialize all output fields, and call `merge_historical_demand_support` for valid rows. Reuse `_sector_close` behavior from `web/services/supply_demand.py`; move shared benchmark helpers into a private focused helper only if duplication would otherwise diverge.

- [ ] **Step 4: Add the service to all application paths**

Import the service in `web/app.py`. Insert:

```python
attach_supply_demand_rows(chart, ticker, histories)
attach_historical_demand_support_rows(chart, ticker, histories)
attach_bottom_state_rows(chart, history)
```

at each latest-stock, historical-forecast, and research-only chart pipeline. Keep the historical-demand service after entry signals and supply/demand attachment, and before bottom-state/model-output construction.

- [ ] **Step 5: Write API ordering and historical-causality tests**

Add assertions that:

- the latest row exposes every stable field;
- selecting an old date exposes only events available at that date;
- appending future chart rows does not change prior payload rows;
- a service exception marks the new model unavailable without failing `/api/stock/<ticker>`;
- bottom-state input sees the augmented support on the same date.

- [ ] **Step 6: Run service and API tests**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_historical_demand_support_service \
  tests.test_web_api -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit the web data path**

```bash
git add web/services/historical_demand_support.py \
  web/services/bottom_state.py web/app.py \
  tests/test_web_historical_demand_support_service.py tests/test_web_api.py
git commit -m "web: attach historical demand support evidence"
```

---

### Task 4: Expose an advisory model card and chart diagnostics

**Files:**
- Modify: `web/forecasts/model_outputs.py`
- Modify: `tests/test_web_model_outputs.py`
- Modify: `web/static/js/charts.js`
- Modify: `web/static/js/i18n.js`
- Modify: `web/static/js/model_outputs.js`
- Modify: `web/static/js/marker_layers.js`
- Modify: `web/static/css/dashboard.css`
- Modify: `web/templates/index.html`
- Modify: `web/app.py`
- Modify: `tests/test_web_api.py`
- Modify: `tests/test_web_assets.py`
- Modify: `tests/model_outputs_runtime.mjs`
- Modify: `tests/marker_layers_runtime.mjs`

**Interfaces:**
- Registers `historical_demand_support_v1` in the `bullish_structure` family after the bottom-state card.
- Card identity:

```python
model_key="historical_demand_support_v1"
model_version="v1"
output_kind="remembered_zone"
lifecycle="research"
authority="advisory"
timing="close_confirmed"
```

- [ ] **Step 1: Write failing model-output contract tests**

```python
def test_historical_demand_support_is_research_advisory(self):
    output = _output_by_key(build_model_outputs(_context()), "historical_demand_support_v1")
    self.assertEqual(output["lifecycle"], "research")
    self.assertEqual(output["authority"], "advisory")
    self.assertEqual(output["output_kind"], "remembered_zone")
    self.assertIsNone(output.get("probability"))
```

Assert score, coverage, state, first/confirmed dates, age, event types/count, retests, volume ratio, zone bounds, invalidation, conditions, and counter-conditions.

- [ ] **Step 2: Run model-output tests and verify they fail**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_model_outputs -v
```

Expected: output key is absent.

- [ ] **Step 3: Register and serialize the model card**

Add `_historical_demand_support(row)` beside `_bottom_state(row)`. Use `_identity`, `json_safe`, `_metrics`, stable condition codes, and a typed unavailable reason. Do not add the model to `forecast_decision_policy`.

- [ ] **Step 4: Write failing chart-detail and bilingual-copy tests**

Assert Chinese and English rendering for:

- zone;
- distance;
- state;
- first and latest confirmation dates;
- event types/count;
- retests;
- event volume ratio;
- score/coverage;
- invalidation;
- counter-evidence.

Assert forbidden phrases are absent and unknown reason codes use safe fallbacks.

- [ ] **Step 5: Add chart fields and translations**

Extend `detailItems(row, locale)` in `web/static/js/charts.js`. Add `historical_demand_zone` to support-source translations. Add the seven state translations and every metric/condition/counter-condition key in both locales.

- [ ] **Step 6: Add non-disruptive transition markers**

Register one marker layer `historical_demand_support`, default enabled. In `web/app.py`, generate API annotations only when the chart row changes into `testing`, `accepted`, or `invalidated`, and add API assertions that continuous states do not repeat annotations. Do not draw future projections or persistent horizontal primitives, and do not call any time-scale fit/scroll method from hover or lock handlers.

- [ ] **Step 7: Render the selected-date zone without changing chart scale**

Place a pointer-events-none absolute overlay inside the price-chart container. On hover or locked-date changes, convert the selected row’s lower/upper prices with the existing candlestick series `priceToCoordinate()` method and position a translucent band between those pixel coordinates. Hide the band for unavailable/invalidated rows and on selection clear. The overlay must not add series data, price lines, time points, width, or height, so it cannot shift autoscale, time range, drag bounds, or page layout.

- [ ] **Step 8: Run JS and asset tests**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_assets -v
node tests/model_outputs_runtime.mjs
node tests/marker_layers_runtime.mjs
```

Expected: all commands pass; locked-date and drag tests remain green.

- [ ] **Step 9: Commit the UI**

```bash
git add web/forecasts/model_outputs.py tests/test_web_model_outputs.py \
  web/static/js/charts.js web/static/js/i18n.js \
  web/static/js/model_outputs.js web/static/js/marker_layers.js \
  web/static/css/dashboard.css web/templates/index.html \
  web/app.py tests/test_web_api.py \
  tests/test_web_assets.py \
  tests/model_outputs_runtime.mjs tests/marker_layers_runtime.mjs
git commit -m "ui: explain historical demand support"
```

---

### Task 5: Add the frozen out-of-sample ablation study

**Files:**
- Create: `research/run_historical_demand_support_study.py`
- Create: `tests/test_run_historical_demand_support_study.py`
- Create at runtime: `reports/historical-demand-support-study.csv`
- Create at runtime: `reports/historical-demand-support-study.md`

**Interfaces:**
- CLI:

```text
python -m research.run_historical_demand_support_study
  --database data/research_prices.db
  --assignments data/research_group_assignments.json
  --output-csv reports/historical-demand-support-study.csv
  --output-markdown reports/historical-demand-support-study.md
  --asof 2026-07-24
```

- Variants: `baseline`, `baseline_plus_historical_demand`, `historical_demand_only`, `no_volume`, `no_retests`, `no_environment`, `no_decay`.
- Horizons: 5, 10, 20 sessions.

- [ ] **Step 1: Write failing label, fold, and metric tests**

Test next-session-open execution, non-overlapping evaluation option, immature-tail exclusion, maximum favorable/adverse excursion, support hold/break definitions, first-bounce delay, fixed chronological folds, point-in-time group assignment, and regime strata.

- [ ] **Step 2: Run study tests and verify they fail**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_run_historical_demand_support_study -v
```

Expected: module import failure.

- [ ] **Step 3: Implement the deterministic study runner**

Reuse existing research database readers, frozen assignment snapshots, market-regime states, and report-writing conventions. Do not write to `prices.db`, `research_prices.db`, or the online forecast cache. Report coverage and unavailable counts separately from performance.

- [ ] **Step 4: Add promotion-gate tests**

The gate passes only when:

```python
stable_fold_wins >= 3
and max_adverse_excursion_not_worse
and improved_group_count >= 2
and ablation_increment_is_positive
and causal_audit_passed
```

Assert missing strata, insufficient folds, and worse adverse excursion fail closed with stable reason codes.

- [ ] **Step 5: Run focused study tests**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_run_historical_demand_support_study -v
```

Expected: all tests pass.

- [ ] **Step 6: Run the real frozen study**

Run:

```bash
./venv/bin/python -m research.run_historical_demand_support_study \
  --database data/research_prices.db \
  --assignments data/research_group_assignments.json \
  --output-csv reports/historical-demand-support-study.csv \
  --output-markdown reports/historical-demand-support-study.md \
  --asof 2026-07-24
```

Expected: deterministic CSV/Markdown reports with aggregate, sector, market-regime, fold, horizon, variant, and coverage fields.

- [ ] **Step 7: Commit the study and evidence**

```bash
git add research/run_historical_demand_support_study.py \
  tests/test_run_historical_demand_support_study.py \
  reports/historical-demand-support-study.csv \
  reports/historical-demand-support-study.md
git commit -m "research: evaluate historical demand support"
```

---

### Task 6: Document the result and perform final verification

**Files:**
- Modify: `docs/modeling-todo.md`
- Modify: `docs/dashboard.md`
- Modify: `docs/superpowers/specs/2026-07-29-historical-demand-support-design.md`

**Interfaces:**
- Records the implemented lifecycle, authority, exact report path, promotion result, UI interpretation, data limitations, and future minute/tick upgrade path.

- [ ] **Step 1: Update documentation from measured evidence**

Mark implementation items complete only when their tests and real reports exist. Record exact sample counts, observation period, fold wins, support hold/break deltas, maximum adverse excursion, group results, and promotion decision. Replace the spec status with `implemented` while leaving lifecycle/authority unchanged unless the frozen gate passes.

- [ ] **Step 2: Run focused Python and JavaScript suites**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_historical_demand_support \
  tests.test_resistance \
  tests.test_bottom_state \
  tests.test_web_historical_demand_support_service \
  tests.test_web_model_outputs \
  tests.test_web_api \
  tests.test_web_assets \
  tests.test_run_historical_demand_support_study -v
node tests/model_outputs_runtime.mjs
node tests/marker_layers_runtime.mjs
```

Expected: all commands pass.

- [ ] **Step 3: Run the complete test suite**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
  ./venv/bin/python -m unittest discover -s tests
```

Expected: all tests pass with zero failures and zero errors.

- [ ] **Step 4: Run static repository checks**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
  ./venv/bin/python -m compileall \
  research web tests
git diff --check
git status --short
```

Expected: compilation succeeds, `git diff --check` is empty, and status contains only intended tracked changes plus the pre-existing ignored/untracked runtime files named in Global Constraints.

- [ ] **Step 5: Commit documentation and verification**

```bash
git add docs/modeling-todo.md docs/dashboard.md \
  docs/superpowers/specs/2026-07-29-historical-demand-support-design.md
git commit -m "docs: record historical demand support results"
```

- [ ] **Step 6: Review and integrate**

Use `superpowers:requesting-code-review`, address findings without weakening tests, rerun the complete suite, then use `superpowers:finishing-a-development-branch` to offer local merge, pull-request, or keep-branch options.
