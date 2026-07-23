# Disable Forecast Auto-Shift Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the price chart, volume chart, and date axes stationary while hover forecasts add future projection points.

**Architecture:** Disable Lightweight Charts' built-in new-bar visible-range shift in the shared chart options so it applies to both linked charts. Retain the existing timeline anchor and logical-range snapshot as compatibility defenses, and add a regression fake that models an asynchronous library-driven shift.

**Tech Stack:** JavaScript ES modules, Lightweight Charts 5.0.8, Python `unittest`, Node.js assertions, Flask dashboard.

## Global Constraints

- Hovering updates only the forecast path, forecast marker, and detail content.
- Price and volume logical ranges remain unchanged.
- Forecasts outside the viewport may be clipped.
- Manual zoom, scroll, range buttons, and linked-chart synchronization remain available.
- Do not change forecast values, factors, trend evidence, styling, or market data.

---

### Task 1: Prevent Forecast Data from Shifting the Linked Time Scales

**Files:**
- Modify: `tests/test_web_assets.py`
- Modify: `web/static/js/charts.js`

**Interfaces:**
- Consumes: `chartOptions(element) -> ChartOptions`, `createLinkedCharts(...) -> controller`
- Produces: `ChartOptions.timeScale.shiftVisibleRangeOnNewBar === false` for both charts

- [ ] **Step 1: Extend the chart fake with asynchronous auto-shift behavior**

In `test_chart_forecast_interaction`, retain each chart's options, make visible
range subscriptions functional, and model the library moving the range in a
microtask when future forecast points are added:

Change `function chart(name)` to:

```javascript
function chart(name, chartOptions) {
```

Replace the existing `scale` declaration with:

```javascript
const scale = {
  range: null,
  rangeHandler: null,
  subscribeVisibleLogicalRangeChange(handler) { this.rangeHandler = handler; },
  unsubscribeVisibleLogicalRangeChange() { this.rangeHandler = null; },
  getVisibleLogicalRange() { return this.range; },
  setVisibleLogicalRange(next) {
    this.range = next;
    this.rangeHandler?.(next);
  },
  fitContent() {},
};
```

Add `chartOptions` immediately after `name` in the existing `value` object.
Replace the synchronous auto-scroll block inside the existing series
`setData(data)` method with:

```javascript
if (
  options.lineWidth === 3
  && data.length
  && scale.range
  && chartOptions.timeScale.shiftVisibleRangeOnNewBar !== false
) {
  value.forecastAutoScrolls += 1;
  queueMicrotask(() => {
    scale.setVisibleLogicalRange({ from: 999, to: 1000 });
  });
}
```

Replace the fake `createChart` method with:

```javascript
createChart(element, options) {
  return chart(element.name, options);
}
```

After creating the controller, require both linked charts to disable the
option:

```javascript
assert.equal(created[0].chartOptions.timeScale.shiftVisibleRangeOnNewBar, false);
assert.equal(created[1].chartOptions.timeScale.shiftVisibleRangeOnNewBar, false);
```

Retain the existing post-hover assertions that compare the price and volume
logical ranges after an asynchronous turn.

- [ ] **Step 2: Run the focused regression test and verify RED**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_assets.WebAssetTest.test_chart_forecast_interaction
```

Expected: `FAIL` because `shiftVisibleRangeOnNewBar` is currently undefined,
and the asynchronous fake shift changes the linked logical ranges.

- [ ] **Step 3: Disable new-bar range shifting in the shared options**

Add the option to `chartOptions` in `web/static/js/charts.js`:

```javascript
timeScale: {
  borderColor: COLORS.grid,
  timeVisible: false,
  shiftVisibleRangeOnNewBar: false,
  tickMarkFormatter: formatChartTickDate,
},
```

Do not add hover-time `setRange` calls or remove the existing timeline anchor
and logical-range snapshot.

- [ ] **Step 4: Run the focused regression test and verify GREEN**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_assets.WebAssetTest.test_chart_forecast_interaction
```

Expected: `Ran 1 test ... OK`.

- [ ] **Step 5: Run the complete automated suite**

Run:

```bash
./venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Expected: all tests pass with no failures or errors.

- [ ] **Step 6: Verify the live NBIS interaction**

Restart `web/app.py`, open `http://127.0.0.1:5000/`, select NBIS with the
one-year range and 20-day forecast, and move across several historical dates.
Wait for each forecast to render. Confirm:

```text
price logical range before === price logical range after
volume logical range before === volume logical range after
candles, volume bars, and date labels remain stationary
forecast line and detail content change
```

- [ ] **Step 7: Commit the fix**

```bash
git add tests/test_web_assets.py web/static/js/charts.js
git commit -m "fix: prevent forecast hover chart drift"
```
