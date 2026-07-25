# Near Resistance Zone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute a causal near-resistance zone for every chart date, render the latest or locked-date zone without moving the chart, and explain it in the localized detail table.

**Architecture:** A new pure Python module owns candidate generation, ATR-based clustering, scoring, and far-resistance selection. `build_chart_rows` attaches its output to each point-in-time chart row. The frontend renders the selected zone with a non-autoscaling `BaselineSeries`, while detail formatting and localization remain separate from the financial calculation.

**Tech Stack:** Python 3.9, pandas, unittest, ES modules, Node test harness, Lightweight Charts 5, Flask dashboard.

## Global Constraints

- All pressure inputs must be available on or before the observation date.
- Free hover updates only the detail table; it must not move the chart zone.
- Clicking a date locks both detail and zone; unlocking restores the latest zone.
- The zone series must not participate in autoscaling or add forecast/future dates.
- Pressure strength is an explainable rule score, never a probability.
- Existing user-owned database WAL files and untracked research files must remain untouched.

---

### Task 1: Causal near-resistance calculation

**Files:**
- Create: `research/resistance.py`
- Create: `tests/test_resistance.py`

**Interfaces:**
- Consumes: daily OHLCV `pandas.DataFrame` and point-in-time rows from `research.reversal.build_reversal_rows`.
- Produces: `build_near_resistance_rows(history: pd.DataFrame, reversal_rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]`.
- Each returned row contains `near_resistance_lower`, `near_resistance_upper`, `near_resistance_mid`, `near_resistance_distance_pct`, `near_resistance_score`, `near_resistance_sources`, and `far_resistance`.

- [ ] **Step 1: Write failing aggregation and NBIS-shaped regression tests**

```python
def test_nearest_candidate_cluster_beats_far_twenty_day_pivot():
    history = nbis_shaped_history()
    reversal = build_reversal_rows(history)

    row = build_near_resistance_rows(history, reversal)[-1]

    self.assertAlmostEqual(row["near_resistance_lower"], 226.74, delta=0.2)
    self.assertAlmostEqual(row["near_resistance_upper"], 230.30, delta=0.2)
    self.assertAlmostEqual(row["far_resistance"], 276.17)
    self.assertIn("sma50", row["near_resistance_sources"])
    self.assertIn("recent_high_10", row["near_resistance_sources"])
```

Also add independent tests for a single candidate expanded by `0.15 × ATR20`, no valid candidate, score capping at 100, and finite-or-null output values.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
./venv/bin/python -m unittest tests.test_resistance -v
```

Expected: import failure because `research.resistance` does not exist.

- [ ] **Step 3: Implement candidate generation and clustering**

Implement:

```python
OUTPUT_KEYS = (
    "near_resistance_lower",
    "near_resistance_upper",
    "near_resistance_mid",
    "near_resistance_distance_pct",
    "near_resistance_score",
    "near_resistance_sources",
    "far_resistance",
)

def build_near_resistance_rows(history, reversal_rows):
    # Validate equal row counts and required OHLCV columns.
    # Compute causal EMA20, SMA50, SMA200, ATR20, 10-session high,
    # 20-session prior-close pivot, volume ratio, and upper-wick evidence.
    # Resolve the latest confirmed swing-high price from its date.
    # Filter candidates strictly above the observation-date close.
    # Group adjacent candidates at <= 0.5 * ATR20.
    # Select the nearest group and calculate score plus far resistance.
```

Use stable source keys:

```python
SOURCE_KEYS = (
    "ema20",
    "sma50",
    "sma200",
    "recent_high_10",
    "confirmed_swing_high",
    "descending_trendline",
    "twenty_session_pivot",
)
```

Calculate strength from distinct source count, twenty-session touches, rejection count, and one combined volume/wick confirmation. Do not award the same bar more than one rejection-confirmation bonus.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
./venv/bin/python -m unittest tests.test_resistance -v
```

Expected: all resistance tests pass.

- [ ] **Step 5: Add and verify prefix-invariance test**

```python
def test_appending_future_rows_does_not_change_historical_zones():
    original = nbis_shaped_history()
    extended = append_future_bars(original)
    short_rows = build_near_resistance_rows(original, build_reversal_rows(original))
    long_rows = build_near_resistance_rows(extended, build_reversal_rows(extended))
    assert long_rows[: len(short_rows)] == short_rows
```

Run the focused suite again and commit:

```bash
git add research/resistance.py tests/test_resistance.py
git commit -m "feat: compute causal near resistance zones"
```

### Task 2: Attach resistance fields to chart rows

**Files:**
- Modify: `web/factors/builtin.py:471-565`
- Modify: `tests/test_web_factors.py:207-300`

**Interfaces:**
- Consumes: `build_near_resistance_rows(history, reversal_rows)` from Task 1.
- Produces: seven new JSON-safe keys on every object returned by `build_chart_rows`.

- [ ] **Step 1: Extend the chart-row contract test first**

Add the seven exact field names to the expected key set and assertions:

```python
self.assertIsInstance(last["near_resistance_sources"], list)
for key in (
    "near_resistance_lower",
    "near_resistance_upper",
    "near_resistance_mid",
    "near_resistance_distance_pct",
    "near_resistance_score",
    "far_resistance",
):
    self.assertTrue(last[key] is None or isinstance(last[key], (int, float)))
```

Add a prefix-invariance integration assertion comparing chart rows built from a history prefix and the same prefix inside a longer history.

- [ ] **Step 2: Run the contract test and verify RED**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_factors.BuiltinFactorTest.test_chart_rows_include_ohlcv_indicators_and_prior_changes -v
```

Expected: failure because the seven keys are missing.

- [ ] **Step 3: Attach the pure calculation output**

In `build_chart_rows`:

```python
resistance_rows = build_near_resistance_rows(history, reversal_rows)
```

Then merge the same-position result after reversal and early-reversal fields:

```python
**resistance_rows[position],
```

- [ ] **Step 4: Verify focused and related Python tests**

Run:

```bash
./venv/bin/python -m unittest tests.test_resistance tests.test_web_factors -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add web/factors/builtin.py tests/test_web_factors.py
git commit -m "feat: expose near resistance chart fields"
```

### Task 3: Localized resistance details

**Files:**
- Modify: `web/static/js/charts.js:145-195`
- Modify: `web/static/js/i18n.js`
- Modify: `tests/test_web_assets.py:1840-1940`

**Interfaces:**
- Consumes: the seven chart-row fields from Task 2.
- Produces: exported pure helpers `resistanceStrengthKey(score)` and existing `detailItems(row, locale)` entries for range, center, distance, sources, strength, and far resistance.

- [ ] **Step 1: Add failing English and Chinese detail assertions**

Extend the existing JavaScript fixture:

```javascript
near_resistance_lower: 226.74,
near_resistance_upper: 230.30,
near_resistance_mid: 228.52,
near_resistance_distance_pct: 2.61,
near_resistance_score: 68,
near_resistance_sources: ['sma50', 'recent_high_10'],
far_resistance: 276.17,
```

Assert English details contain:

```text
Near resistance zone 226.74–230.30
Resistance center 228.52
Distance to resistance +2.61%
Resistance sources SMA50, recent 10-session high
Resistance strength 68/100 · Medium
Far structural resistance 276.17
```

Assert Chinese details contain the equivalent localized labels and source names.

- [ ] **Step 2: Run the focused asset test and verify RED**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_chart_module_renders_linked_series_details_markers_and_localization -v
```

Expected: missing localized resistance detail entries.

- [ ] **Step 3: Implement formatting and localization**

Add localized keys for:

```text
chart.field.nearResistanceZone
chart.field.nearResistanceMid
chart.field.nearResistanceDistance
chart.field.nearResistanceSources
chart.field.nearResistanceStrength
chart.field.farResistance
chart.resistance.source.*
chart.resistance.strength.weak|medium|strong
chart.resistance.scoreDisclaimer
```

Keep `detailItems` pure. Join localized sources in stable backend order, format ranges with an en dash, and return `—` when fields are absent.

- [ ] **Step 4: Run asset tests and verify GREEN**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_assets -v
```

Expected: all asset tests pass.

- [ ] **Step 5: Commit**

```bash
git add web/static/js/charts.js web/static/js/i18n.js tests/test_web_assets.py
git commit -m "feat: explain near resistance in chart details"
```

### Task 4: Stable chart resistance-zone rendering

**Files:**
- Modify: `web/static/js/charts.js:275-835`
- Modify: `tests/dashboard_runtime.mjs`
- Modify: `tests/test_web_assets.py:1200-1580`

**Interfaces:**
- Consumes: `near_resistance_lower`, `near_resistance_upper`, and the latest/locked row.
- Produces: a `BaselineSeries` titled with `chart.series.nearResistance`, configured with `autoscaleInfoProvider: () => null`, and controller behavior that updates only on data load, lock, unlock, or locale change.

- [ ] **Step 1: Add failing stability assertions to the chart harness**

The test must assert:

```javascript
const resistance = created[0].series.find(
  (series) => series.options.title === 'Near resistance zone',
);
assert.equal(resistance.type, 'baseline');
assert.equal(resistance.options.autoscaleInfoProvider(), null);
assert.equal(resistance.options.baseValue.price, latest.near_resistance_lower);
assert.equal(resistance.data[0].value, latest.near_resistance_upper);
```

Capture the baseline options, data, shared times, and visible range before free hover. Verify none change after crosshair movement. After click-lock, verify the base and upper values switch to the locked row without changing shared times or range. After unlock, verify they return to latest values.

- [ ] **Step 2: Run the chart harness test and verify RED**

Run the containing `tests.test_web_assets` chart interaction test.

Expected: no baseline resistance series exists.

- [ ] **Step 3: Extend the fake Lightweight Charts runtime**

Expose a `BaselineSeries` token in `tests/dashboard_runtime.mjs` and retain `applyOptions`, `setData`, and options snapshots for assertions. Do not give the fake series behavior unavailable in the production chart API.

- [ ] **Step 4: Implement the non-autoscaling baseline band**

Create the series:

```javascript
const nearResistanceSeries = priceChart.addSeries(
  LightweightCharts.BaselineSeries,
  {
    title: t("chart.series.nearResistance", {}, locale),
    baseValue: { type: "price", price: 0 },
    topLineColor: COLORS.nearResistance,
    topFillColor1: "rgba(255, 159, 67, 0.20)",
    topFillColor2: "rgba(255, 159, 67, 0.08)",
    bottomLineColor: COLORS.nearResistance,
    bottomFillColor1: "rgba(255, 159, 67, 0.08)",
    bottomFillColor2: "rgba(255, 159, 67, 0.08)",
    baseLineVisible: true,
    priceLineVisible: false,
    lastValueVisible: true,
    autoscaleInfoProvider: () => null,
  },
);
```

Implement:

```javascript
function resistanceDisplayRow() {
  return lockedTime === null ? rows.at(-1) : rowByTime.get(lockedTime);
}

function renderNearResistanceZone() {
  const row = resistanceDisplayRow();
  if (!validZone(row)) {
    nearResistanceSeries.setData([]);
    return;
  }
  nearResistanceSeries.applyOptions({
    baseValue: {type: "price", price: row.near_resistance_lower},
  });
  nearResistanceSeries.setData(
    rows.map(({time}) => ({time, value: row.near_resistance_upper})),
  );
}
```

Call it only from `setChartData`, click-lock, unlock, and locale refresh paths. Do not call it from free crosshair handlers or forecast updates.

- [ ] **Step 5: Verify chart stability and all asset tests**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_assets -v
```

Expected: all tests pass and no range/shared-time assertion changes.

- [ ] **Step 6: Commit**

```bash
git add web/static/js/charts.js web/static/js/i18n.js tests/dashboard_runtime.mjs tests/test_web_assets.py
git commit -m "feat: render stable near resistance zone"
```

### Task 5: Documentation, full verification, and browser acceptance

**Files:**
- Modify: `docs/dashboard.md`
- Modify: `docs/modeling-todo.md`

**Interfaces:**
- Consumes: completed backend and UI behavior.
- Produces: user-facing explanation and updated global backlog status.

- [ ] **Step 1: Update dashboard documentation**

Document:

- the ATR-based candidate clustering;
- the distinction between near and far resistance;
- the `0～100` strength score as a rule score, not a probability;
- latest-versus-locked rendering behavior;
- absence of future data and absence of autoscale effects.

- [ ] **Step 2: Convert the global TODO to Chinese and register the feature**

Apply the approved global TODO design, preserving every checkbox state. Add a stable chart-level task entry for near resistance and mark only implemented and verified parts complete. Include `MACRO-001` as待实施.

- [ ] **Step 3: Run full verification**

Run:

```bash
./venv/bin/python -m unittest discover -s tests
git diff --check
```

Expected: all tests pass and `git diff --check` emits no output.

- [ ] **Step 4: Browser acceptance on NBIS**

Start the latest feature branch service, open `/?ticker=NBIS`, and verify:

- latest zone is approximately `226.7～230.3`;
- far resistance is `276.17`;
- free hover does not move the zone or chart;
- locking 2026-07-20 changes the zone and keeps the date fixed while moving into details;
- unlocking restores the latest zone;
- console contains no errors.

- [ ] **Step 5: Commit documentation**

```bash
git add docs/dashboard.md docs/modeling-todo.md
git commit -m "docs: explain near resistance zones"
```

- [ ] **Step 6: Review and integrate**

Run the requesting-code-review and verification-before-completion skills. If review is clean and all tests still pass, merge the feature branch into `main`, restart the local service, and retain the NBIS page for user inspection.
