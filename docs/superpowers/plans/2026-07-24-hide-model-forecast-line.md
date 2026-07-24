# Hide the Model Forecast Line Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hide the blue future model projection while preserving all forecast calculations, details, markers, and interactions.

**Architecture:** Add Lightweight Charts' `visible: false` option to the existing forecast projection series. Extend the existing chart interaction test so the rendering policy and the retained data/autoscale behavior remain explicit.

**Tech Stack:** JavaScript ES modules, Lightweight Charts 5.0.8, Python `unittest`, Node.js test harness, Flask browser UI.

## Global Constraints

- Do not render the blue model forecast projection or its right-axis title.
- Keep selected-date markers, model direction, horizon, predicted return, provenance, and trend evidence.
- Keep forecast projection data flow intact.
- Keep the projection series excluded from autoscaling.
- Do not change price candles, volume, moving averages, structural lines, key levels, or range controls.

---

### Task 1: Hide only the model forecast projection series

**Files:**
- Modify: `tests/test_web_assets.py:1231-1236`
- Modify: `web/static/js/charts.js:285-295`

**Interfaces:**
- Consumes: Existing `forecastProjectionSeries` created through `priceChart.addSeries`.
- Produces: A forecast series configured with `visible: false` while retaining projection data and `autoscaleInfoProvider`.

- [ ] **Step 1: Add the failing visibility assertion**

Add this assertion after `assert.ok(forecastSeries)`:

```js
assert.equal(forecastSeries.options.visible, false);
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run:

```bash
../../venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_chart_forecast_interaction -v
```

Expected: `FAIL` because `forecastSeries.options.visible` is undefined.

- [ ] **Step 3: Hide the forecast series**

Add `visible: false` to the forecast projection series options:

```js
const forecastProjectionSeries = priceChart.addSeries(LightweightCharts.LineSeries, {
  title: t("chart.series.forecastProjection", {}, locale),
  visible: false,
  color: COLORS.forecast,
  lineWidth: 3,
  lineStyle: LightweightCharts.LineStyle.Solid,
  crosshairMarkerVisible: false,
  priceLineVisible: false,
  lastValueVisible: true,
  autoscaleInfoProvider: () => null,
});
```

- [ ] **Step 4: Run the focused test and confirm it passes**

Run:

```bash
../../venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_chart_forecast_interaction -v
```

Expected: one passing test.

- [ ] **Step 5: Run the complete test suite**

Run:

```bash
../../venv/bin/python -m unittest discover -s tests -v
```

Expected: all tests pass without failures or errors.

- [ ] **Step 6: Verify NBIS in the browser**

Start the implementation branch on a temporary local port and load NBIS:

1. Select a historical date.
2. Confirm the forecast detail panel still shows direction, horizon, predicted return, provenance, and strengthening/acceleration evidence.
3. Confirm the blue future model forecast line and its right-axis label are absent.
4. Confirm candles, moving averages, structural trend lines, volume, selected-date marker, and date locking remain visible and interactive.

- [ ] **Step 7: Commit the tested change**

```bash
git add tests/test_web_assets.py web/static/js/charts.js
git commit -m "fix: hide model forecast projection"
```

