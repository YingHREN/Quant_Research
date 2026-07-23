# Stable Hover Forecast Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the K-line chart horizontally and vertically stationary while hover forecasts are loaded or replaced.

**Architecture:** Add a whitespace-only line series that owns the complete historical and maximum future trading calendar, so the visible forecast series never changes the shared time index. Keep the forecast on the candle price scale for correct coordinates, but return no autoscale contribution so forecast targets cannot resize the candle layout.

**Tech Stack:** Vanilla JavaScript ES modules, Lightweight Charts 5.0.8, Python `unittest`, Node.js test harness.

## Global Constraints

- Forecast values, thresholds, trend evidence, chart styling, and market data must not change.
- Hovering, locking, asynchronous historical forecast loading, and 5/20/60-session switching must preserve the visible date mapping.
- The forecast line must not participate in vertical autoscaling; off-scale targets may be clipped.
- Price and volume time scales must remain synchronized.
- No new runtime dependency is allowed.

## File Structure

- Modify `web/static/js/charts.js`: own the fixed timeline anchor and exclude the forecast series from price autoscaling.
- Modify `tests/test_web_assets.py`: model the chart's shared time index and assert horizontal and vertical layout stability.

---

### Task 1: Stabilize the shared time index

**Files:**
- Modify: `tests/test_web_assets.py:1038`
- Modify: `web/static/js/charts.js:248`
- Modify: `web/static/js/charts.js:295`
- Modify: `web/static/js/charts.js:634`

**Interfaces:**
- Consumes: stock-detail payloads shaped as `{chart, forecasts: {by_date}}`.
- Produces: internal `forecastTimelineDates(payload) -> string[]` and `replaceTimelineAnchor(payload, reset) -> void`.
- Produces: a whitespace-only `timelineAnchorSeries` whose data is `{time: string}[]`.

- [ ] **Step 1: Make the chart test harness model the shared time index**

In `test_chart_forecast_interaction`, make every fake series retain its data and
derive the chart's sorted union of time values:

```javascript
const value = {
  name,
  series: [],
  sharedTimes() {
    return [...new Set(
      this.series.flatMap((series) => series.data.map((point) => point.time)),
    )].sort();
  },
  // existing fake-chart fields
};

const series = {
  type,
  options,
  data: [],
  setData(data) {
    this.data = data;
    // retain the existing synthetic forecast autos-scroll behavior
  },
  // existing fake-series methods
};
```

- [ ] **Step 2: Write the failing horizontal-stability assertions**

After `controller.setChartData(...)`, capture the shared calendar, hover the
historical date that creates a forecast path, and assert that the calendar and
date-to-index mapping are unchanged:

```javascript
const sharedTimesBeforeHover = created[0].sharedTimes();
const selectedIndexBeforeHover = sharedTimesBeforeHover.indexOf("2026-07-17");

created[0].crosshairHandler({time: "2026-07-17"});
await new Promise((resolve) => setTimeout(resolve, 0));

const sharedTimesAfterHover = created[0].sharedTimes();
assert.deepEqual(sharedTimesAfterHover, sharedTimesBeforeHover);
assert.equal(
  sharedTimesAfterHover.indexOf("2026-07-17"),
  selectedIndexBeforeHover,
);
```

Also identify the whitespace-only anchor and assert it contains observed dates,
projection dates, and target dates without duplicates:

```javascript
const timelineAnchor = created[0].series.find(
  (series) => series.options.title === "",
);
assert.ok(timelineAnchor);
assert.deepEqual(
  timelineAnchor.data.map((point) => point.time),
  ["2026-07-17", "2026-07-18", "2026-07-20", "2026-08-14", "2026-08-17"],
);
assert.ok(timelineAnchor.data.every((point) => !("value" in point)));
```

- [ ] **Step 3: Run the focused test and verify RED**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_assets.WebAssetTest.test_chart_forecast_interaction
```

Expected: FAIL because no timeline-anchor series exists and forecast `setData`
changes `sharedTimes()`.

- [ ] **Step 4: Add the pure date collector**

Add this helper near the existing chart-data helpers in
`web/static/js/charts.js`:

```javascript
function forecastTimelineDates(payload) {
  const dates = new Set();
  const byDate = payload?.forecasts?.by_date;
  if (!byDate || typeof byDate !== "object") return [];
  Object.values(byDate).forEach((horizons) => {
    if (!horizons || typeof horizons !== "object") return;
    Object.values(horizons).forEach((forecast) => {
      if (!forecast || typeof forecast !== "object") return;
      if (typeof forecast.target_date === "string") {
        dates.add(forecast.target_date);
      }
      if (Array.isArray(forecast.projection_dates)) {
        forecast.projection_dates.forEach((date) => {
          if (typeof date === "string") dates.add(date);
        });
      }
    });
  });
  return [...dates].sort();
}
```

- [ ] **Step 5: Add and populate the invisible anchor series**

Create the anchor before the visible forecast series:

```javascript
const timelineAnchorSeries = priceChart.addSeries(
  LightweightCharts.LineSeries,
  {
    title: "",
    color: "transparent",
    crosshairMarkerVisible: false,
    priceLineVisible: false,
    lastValueVisible: false,
  },
);
```

Add monotonic anchor state and its updater:

```javascript
let timelineAnchorDates = new Set();

function replaceTimelineAnchor(payload, reset = false) {
  if (reset) timelineAnchorDates = new Set();
  rows.forEach((row) => {
    const date = timeKey(row.time);
    if (date !== null) timelineAnchorDates.add(date);
  });
  forecastTimelineDates(payload).forEach((date) => {
    timelineAnchorDates.add(date);
  });
  timelineAnchorSeries.setData(
    [...timelineAnchorDates].sort().map((time) => ({time})),
  );
}
```

Call `replaceTimelineAnchor(payload, true)` in `setChartData` after `rows` is
assigned and before visible series data is painted. Call
`replaceTimelineAnchor(payload)` at the beginning of `setForecasts` so an
unexpected new target extends, but never shrinks, the anchor.

- [ ] **Step 6: Run the focused test and verify GREEN**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_assets.WebAssetTest.test_chart_forecast_interaction
```

Expected: PASS; the time union and the logical index of `2026-07-17` remain
unchanged after the forecast path is replaced.

- [ ] **Step 7: Commit the horizontal fix**

```bash
git add web/static/js/charts.js tests/test_web_assets.py
git commit -m "fix: stabilize forecast hover timeline"
```

---

### Task 2: Remove forecast targets from vertical autoscaling

**Files:**
- Modify: `tests/test_web_assets.py:1038`
- Modify: `web/static/js/charts.js:248`

**Interfaces:**
- Consumes: the visible forecast `LineSeries` options.
- Produces: `autoscaleInfoProvider() -> null` for only the forecast series.

- [ ] **Step 1: Write the failing autoscale assertion**

Locate the visible forecast series by its translated title and assert that its
autoscale provider opts out:

```javascript
const forecastSeries = created[0].series.find(
  (series) => series.options.title === "模型预测线",
);
assert.ok(forecastSeries);
assert.equal(typeof forecastSeries.options.autoscaleInfoProvider, "function");
assert.equal(forecastSeries.options.autoscaleInfoProvider(), null);
```

Also assert that candle and moving-average series do not receive this override:

```javascript
assert.equal(created[0].series[0].options.autoscaleInfoProvider, undefined);
const emaSeries = created[0].series.find(
  (series) => series.options.title === "EMA20",
);
assert.equal(emaSeries.options.autoscaleInfoProvider, undefined);
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_assets.WebAssetTest.test_chart_forecast_interaction
```

Expected: FAIL because the forecast series currently contributes its min/max
values to candle price-scale autosizing.

- [ ] **Step 3: Add the minimal autoscale override**

Add one option to `forecastProjectionSeries`:

```javascript
const forecastProjectionSeries = priceChart.addSeries(
  LightweightCharts.LineSeries,
  {
    // existing forecast presentation options
    autoscaleInfoProvider: () => null,
  },
);
```

Do not put the forecast on a separate price scale; it must keep candle-price
coordinates. Do not add the override to trend, candle, or moving-average
series.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_assets.WebAssetTest.test_chart_forecast_interaction
```

Expected: PASS.

- [ ] **Step 5: Run all automated tests**

Run:

```bash
./venv/bin/python -m unittest discover -s tests -p "test_*.py"
```

Expected: all tests PASS with no new warnings or errors.

- [ ] **Step 6: Verify the live NBIS interaction**

Start or reuse the local server, reload `http://127.0.0.1:5000/`, select NBIS,
one year, and 20 sessions. Lock 2025-10-20 and wait for the downward forecast.
Verify:

```text
detail date == crosshair axis date == 2025-10-20
visible first/last dates before forecast == visible first/last dates after forecast
candle vertical coordinates before forecast == candle vertical coordinates after forecast
```

Unlock and move the pointer through at least five dates to the right. Repeat
with 5- and 60-session horizons. Expected: prediction content changes while
candles, averages, structure levels, dates, and volume bars remain stationary.

- [ ] **Step 7: Commit the autoscale fix**

```bash
git add web/static/js/charts.js tests/test_web_assets.py
git commit -m "fix: exclude forecast path from chart autoscale"
```

