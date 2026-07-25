# Near Support Detail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a causal near-support diagnosis to the selected-date detail table and remove the near-resistance band from the price chart without removing resistance data.

**Architecture:** Persist the latest causally confirmed swing low in reversal rows, then extend the existing Python structure-diagnosis module to calculate support candidates, clusters, strength, and state. The chart-row API passes those values through; JavaScript only localizes and renders them. The existing near-resistance calculation remains available to the table, while its BaselineSeries and update path are removed from the chart.

**Tech Stack:** Python 3, pandas, NumPy, Flask chart-row payloads, browser-native ES modules, Lightweight Charts, `unittest`, Node.js assertions.

## Global Constraints

- Every historical value must use only data known by that session's close.
- The prior 10-session low excludes the observation session itself.
- Missing support is represented by null numeric values, an empty source array, and `unavailable`.
- JavaScript must not calculate or select financial support levels.
- Do not draw a support band or line on the price chart.
- Keep near-resistance fields in the lower detail table.
- Remove only the near-resistance BaselineSeries, its right-axis label, and its update logic.
- Do not change the descending resistance line, EMA20, SMA50, SMA200, 20-session pivot, or forecast series.
- Chart pan, zoom, hover, date lock, and autoscaling behavior must remain unchanged.
- Preserve unrelated working-tree changes and untracked database/research files.

---

### Task 1: Persist the Latest Confirmed Swing Low

**Files:**
- Modify: `research/reversal.py`
- Test: `tests/test_reversal.py`

**Interfaces:**
- Consumes: `build_reversal_rows(history: pd.DataFrame) -> list[dict[str, object]]`
- Produces on every row after confirmation: `latest_confirmed_low_date: str | None`, `latest_confirmed_low_price: float | None`, and `latest_confirmed_low_confirmed_date: str | None`

- [ ] **Step 1: Write failing tests for causal and persistent low fields**

Add tests that construct a falling-then-rising close sequence and assert:

```python
rows = build_reversal_rows(frame)
confirmation_index = next(
    index for index, row in enumerate(rows)
    if row["latest_confirmed_low_confirmed_date"] is not None
)
confirmed = rows[confirmation_index]
assert confirmed["latest_confirmed_low_date"] is not None
assert confirmed["latest_confirmed_low_price"] is not None
assert rows[confirmation_index - 1]["latest_confirmed_low_date"] is None
assert rows[-1]["latest_confirmed_low_date"] == confirmed["latest_confirmed_low_date"]
assert rows[-1]["latest_confirmed_low_price"] == confirmed["latest_confirmed_low_price"]
```

Add an append-only check:

```python
prefix_rows = build_reversal_rows(prefix)
extended_rows = build_reversal_rows(pd.concat((prefix, future)))
assert extended_rows[: len(prefix_rows)] == prefix_rows
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
./venv/bin/python -m unittest tests.test_reversal -v
```

Expected: FAIL because the three latest-confirmed-low keys do not exist.

- [ ] **Step 3: Add the persistent output fields**

Extend `_empty_row()`:

```python
"latest_confirmed_low_date": None,
"latest_confirmed_low_price": None,
"latest_confirmed_low_confirmed_date": None,
```

After the pivot-confirmation state machine updates `confirmed_lows`, populate each current row:

```python
if confirmed_lows:
    latest_low = confirmed_lows[-1]
    row["latest_confirmed_low_date"] = _iso(latest_low.date)
    row["latest_confirmed_low_price"] = float(latest_low.price)
    row["latest_confirmed_low_confirmed_date"] = _iso(latest_low.confirmed_date)
```

Do not write these values onto rows before `confirmed_lows` contains the low.

- [ ] **Step 4: Run reversal tests and verify GREEN**

Run:

```bash
./venv/bin/python -m unittest tests.test_reversal -v
```

Expected: PASS.

- [ ] **Step 5: Commit the causal low fields**

```bash
git add research/reversal.py tests/test_reversal.py
git commit -m "feat: expose confirmed swing low history"
```

---

### Task 2: Calculate Causal Near-Support Rows

**Files:**
- Modify: `research/resistance.py`
- Test: `tests/test_resistance.py`
- Test: `tests/test_web_factors.py`
- Test: `tests/test_web_api.py`

**Interfaces:**
- Consumes: sorted OHLCV history and aligned reversal rows containing the Task 1 low fields
- Produces within each result from `build_near_resistance_rows(...)`:
  - `near_support_lower: float | None`
  - `near_support_upper: float | None`
  - `near_support_mid: float | None`
  - `near_support_distance_pct: float | None`
  - `near_support_score: int | None`
  - `near_support_sources: list[str]`
  - `near_support_state: str`

- [ ] **Step 1: Write failing support-selection tests**

Add focused tests covering:

```python
row = build_near_resistance_rows(history, build_reversal_rows(history))[-1]
assert row["near_support_lower"] <= row["near_support_mid"]
assert row["near_support_mid"] <= row["near_support_upper"]
assert row["near_support_upper"] <= history["Close"].iloc[-1]
assert row["near_support_state"] in {"above", "testing", "inside"}
assert 0 <= row["near_support_score"] <= 100
assert row["near_support_sources"]
```

Create a multi-cluster fixture where one support group is far below and another is within `0.5 * ATR20` of the close. Assert that the higher group is selected. Create a rising fixture whose observation-day low is much lower than all prior lows and assert `recent_low_10` still equals the shifted prior-window value rather than the current low.

- [ ] **Step 2: Write failing missing-data and causality tests**

For a frame shorter than 20 sessions and reversal rows without a valid lower candidate, assert:

```python
assert row["near_support_lower"] is None
assert row["near_support_upper"] is None
assert row["near_support_mid"] is None
assert row["near_support_distance_pct"] is None
assert row["near_support_score"] is None
assert row["near_support_sources"] == []
assert row["near_support_state"] == "unavailable"
```

For prefix invariance:

```python
short_rows = build_near_resistance_rows(prefix, build_reversal_rows(prefix))
long_rows = build_near_resistance_rows(extended, build_reversal_rows(extended))
assert long_rows[: len(short_rows)] == short_rows
```

- [ ] **Step 3: Add failing chart-row contract tests**

Add the three Task 1 confirmed-low keys to `reversal_keys` in `tests/test_web_api.py`. Add these support keys to both factor and API assertions:

```python
support_keys = (
    "near_support_lower",
    "near_support_upper",
    "near_support_mid",
    "near_support_distance_pct",
    "near_support_score",
    "near_support_sources",
    "near_support_state",
)
```

Assert numeric-or-null types, list type for sources, and a state in:

```python
{"above", "testing", "inside", "unavailable"}
```

- [ ] **Step 4: Run support and chart-row tests and verify RED**

Run:

```bash
./venv/bin/python -m unittest tests.test_resistance tests.test_web_factors tests.test_web_api -v
```

Expected: FAIL because `near_support_*` fields are absent.

- [ ] **Step 5: Add support keys and candidate helpers**

Add these result defaults:

```python
"near_support_lower": None,
"near_support_upper": None,
"near_support_mid": None,
"near_support_distance_pct": None,
"near_support_score": None,
"near_support_sources": [],
"near_support_state": "unavailable",
```

Add a support candidate helper that accepts finite positive values at or below close:

```python
def _support_candidate(source: str, value, close: float):
    if not _finite(value):
        return None
    price = float(value)
    return (price, source) if 0.0 < price <= close else None
```

Add a resolver for `latest_confirmed_low_price` that returns only finite positive values already exposed by the causal reversal row.

- [ ] **Step 6: Build and select support clusters**

Calculate:

```python
recent_low_10 = frame["Low"].shift(1).rolling(10).min()
breakout_retest_20 = close.shift(1).rolling(20).max()
```

Build candidates in fixed source order:

```python
SUPPORT_SOURCE_ORDER = (
    "ema20",
    "sma50",
    "sma200",
    "recent_low_10",
    "confirmed_swing_low",
    "breakout_retest_20",
)
```

Cluster with the existing `_clusters(candidates, atr * 0.5)` and select `groups[-1]`, the highest group below the current close. For one source use:

```python
lower = max(0.0, center - atr * 0.15)
upper = min(current_close, center + atr * 0.15)
```

For multiple sources use the minimum and maximum prices. Set `near_support_mid` to their midpoint and clamp distance to zero:

```python
distance = max(0.0, (current_close / upper - 1.0) * 100.0)
```

- [ ] **Step 7: Implement support strength and state**

Use the latest 20 sessions and `0.25 * ATR20` tolerance. Count a test when the daily range intersects the support zone plus tolerance; count acceptance when that bar closes at or above the zone's upper edge. Award:

```python
source_points = min(45, source_count * 15)
test_points = min(30, test_count * 10)
acceptance_points = min(20, acceptance_count * 10)
confirmation_points = 5 if volume_or_lower_wick_confirmation else 0
score = min(100, source_points + test_points + acceptance_points + confirmation_points)
```

Classify:

```python
if lower <= current_close <= upper:
    state = "inside"
elif current_close - upper <= atr * 0.5:
    state = "testing"
else:
    state = "above"
```

- [ ] **Step 8: Run support, reversal, and chart-row tests and verify GREEN**

Run:

```bash
./venv/bin/python -m unittest tests.test_resistance tests.test_reversal tests.test_web_factors tests.test_web_api -v
```

Expected: PASS.

- [ ] **Step 9: Commit the support algorithm and chart-row contract**

```bash
git add research/resistance.py tests/test_resistance.py tests/test_web_factors.py tests/test_web_api.py
git commit -m "feat: calculate causal near support zones"
```

---

### Task 3: Render Support Details and Remove the Pressure Band

**Files:**
- Modify: `web/static/js/charts.js`
- Modify: `web/static/js/i18n.js`
- Modify: `tests/test_web_assets.py`

**Interfaces:**
- Consumes: Task 2 chart-row support fields
- Produces: localized support detail items; no near-resistance BaselineSeries

- [ ] **Step 1: Replace the pressure-band behavior test with a failing absence test**

Replace `test_near_resistance_zone_is_stable_on_hover_and_switches_only_on_lock` with a source/Node contract that asserts:

```javascript
assert.equal(
  created[0].series.filter((series) => series.type === "baseline").length,
  0,
);
```

Update `test_linked_chart_contract` to expect:

```python
self.assertNotIn("LightweightCharts.BaselineSeries", source)
self.assertNotIn("nearResistanceSeries", source)
self.assertNotIn("renderNearResistanceZone", source)
self.assertEqual(source.count("LightweightCharts.LineSeries"), 7)
```

Run:

```bash
./venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_linked_chart_contract tests.test_web_assets.WebAssetTest.test_near_resistance_band_is_not_created -v
```

Expected: FAIL because the BaselineSeries still exists.

- [ ] **Step 2: Write failing localized support-detail assertions**

Extend the representative row in the Node detail test:

```javascript
near_support_lower: 94.5,
near_support_upper: 97.0,
near_support_mid: 95.75,
near_support_distance_pct: 4.12,
near_support_score: 72,
near_support_sources: ["ema20", "confirmed_swing_low"],
near_support_state: "above",
latest_confirmed_low_date: "2026-07-10",
latest_confirmed_low_price: 94,
latest_confirmed_low_confirmed_date: "2026-07-14",
```

Assert the English and Chinese detail arrays contain all six support labels and readable values. Run the focused detail test and verify it fails because support detail items and translation keys are absent.

- [ ] **Step 3: Remove only the near-resistance chart series**

Delete from `charts.js`:

- `COLORS.nearResistance` if no remaining chart code uses it.
- The `nearResistanceSeries` creation block.
- The complete `renderNearResistanceZone()` function.
- The `renderNearResistanceZone()` call in `setChartData`.
- The near-resistance redraw call from click/lock handlers.
- The `chart.series.nearResistance` locale refresh path.

Keep `resistanceZoneText`, resistance source formatting, and every near-resistance item in `detailItems`.

- [ ] **Step 4: Add support detail formatting**

Add pure formatters:

```javascript
function supportZoneText(row) {
  if (!finite(row?.near_support_lower) || !finite(row?.near_support_upper)) return "—";
  return `${numberText(row.near_support_lower)}–${numberText(row.near_support_upper)}`;
}
```

Reuse a generic source-list helper or add `supportSourcesText` that maps source keys through `chart.support.source.<key>`. Add support strength and state formatters that map the backend score/state without recomputing them.

Insert detail rows adjacent to pressure rows:

```javascript
{ label: t("chart.field.nearSupportZone", {}, locale), value: supportZoneText(row) },
{ label: t("chart.field.nearSupportMid", {}, locale), value: numberText(row.near_support_mid) },
{ label: t("chart.field.nearSupportDistance", {}, locale), value: percentText(row.near_support_distance_pct) },
{ label: t("chart.field.nearSupportSources", {}, locale), value: supportSourcesText(row.near_support_sources, locale) },
{ label: t("chart.field.nearSupportStrength", {}, locale), value: supportStrengthText(row.near_support_score, locale) },
{ label: t("chart.field.nearSupportState", {}, locale), value: supportStateText(row.near_support_state, locale) },
```

- [ ] **Step 5: Add complete Chinese and English localization**

Add matching keys for:

- Six field labels.
- Six source names: EMA20, SMA50, SMA200, recent 10-session low, confirmed swing low, breakout retest.
- Weak, medium, and strong.
- Above, testing, inside, and unavailable.

Use concise Chinese text:

```text
近端支撑区
支撑区中心
距近端支撑
支撑来源
支撑强度
支撑状态
位于支撑上方
正在测试支撑
进入支撑区
不可用
```

- [ ] **Step 6: Run focused asset tests and verify GREEN**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_assets -v
```

Expected: PASS with no BaselineSeries pressure band and complete bilingual support details.

- [ ] **Step 7: Commit the UI behavior**

```bash
git add web/static/js/charts.js web/static/js/i18n.js tests/test_web_assets.py
git commit -m "feat: show support details without pressure band"
```

---

### Task 4: Full Regression and Browser Verification

**Files:**
- Verify only; do not modify unrelated files

**Interfaces:**
- Consumes: Tasks 1–4
- Produces: evidence that the feature satisfies the design without interaction regressions

- [ ] **Step 1: Run Python and JavaScript-backed test suites**

Run:

```bash
./venv/bin/python -m unittest tests.test_reversal tests.test_resistance tests.test_web_factors tests.test_web_api tests.test_web_assets -v
```

Expected: PASS.

- [ ] **Step 2: Run repository-wide tests**

Run:

```bash
./venv/bin/python -m unittest discover -s tests -v
```

Expected: PASS. Record any pre-existing unrelated failure separately rather than editing unrelated code.

- [ ] **Step 3: Restart the local service using the project’s existing command**

Inspect the current process and documented start command, then restart only the stock dashboard service. Confirm:

```text
GET http://127.0.0.1:5000/ returns 200
GET http://127.0.0.1:5000/api/stocks/NBIS returns chart rows with near_support_* fields
```

- [ ] **Step 4: Verify the UI in the local browser**

For NBIS and at least one additional stock:

1. Confirm the orange near-pressure band and its right-axis label are absent.
2. Confirm pressure fields still appear in the lower detail table.
3. Confirm support zone, center, distance, sources, strength, and state appear in Chinese.
4. Hover several dates and confirm support details follow the hover date.
5. Lock one date, move the pointer into the lower table, and confirm the date does not change.
6. Drag and zoom horizontally and confirm the chart does not jump or become stuck.

- [ ] **Step 5: Inspect the final diff and status**

Run:

```bash
git diff --check
git status --short
```

Confirm only intended implementation/test files plus the pre-existing `docs/modeling-todo.md` and untracked user files appear.

- [ ] **Step 6: Commit any final test-only corrections**

If verification required scoped corrections, commit only those files:

```bash
git add research/reversal.py research/resistance.py web/static/js/charts.js web/static/js/i18n.js tests/test_reversal.py tests/test_resistance.py tests/test_web_factors.py tests/test_web_api.py tests/test_web_assets.py
git commit -m "test: verify near support detail behavior"
```

Do not stage `data/prices.db-shm`, `data/prices.db-wal`, `research/high_level_reversal_study.py`, or unrelated TODO changes.
