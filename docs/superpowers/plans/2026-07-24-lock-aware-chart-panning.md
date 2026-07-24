# Lock-Aware Chart Panning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow deliberate mouse panning while a chart is unlocked, freeze panning while a date is locked, and restore panning on the next click.

**Architecture:** Keep one lock state inside `createLinkedCharts` and route every lock transition through a helper that updates both Lightweight Charts instances and both container state attributes. CSS maps the shared state attribute to grab, grabbing, and crosshair cursors.

**Tech Stack:** JavaScript ES modules, Lightweight Charts 5.0.8, CSS, Python `unittest`, Node.js test harness, Flask browser UI.

## Global Constraints

- Price and volume charts must always share the same panning state.
- Unlocked charts allow `pressedMouseMove`.
- Locked charts reject `pressedMouseMove` until the next chart click.
- Loading a different ticker or new chart payload restores the unlocked state.
- Forecast calculations, hidden forecast projection, chart data, and date-range behavior remain unchanged.

---

### Task 1: Make chart panning follow the date-lock state

**Files:**
- Modify: `tests/test_web_assets.py:1095-1140`
- Modify: `tests/test_web_assets.py:1254-1262`
- Modify: `tests/test_web_assets.py:1318-1340`
- Modify: `web/static/js/charts.js:207-212`
- Modify: `web/static/js/charts.js:320-340`
- Modify: `web/static/js/charts.js:575-586`
- Modify: `web/static/js/charts.js:695-705`
- Modify: `web/static/css/dashboard.css`

**Interfaces:**
- Consumes: Existing `lockedTime`, `priceChart.applyOptions`, `volumeChart.applyOptions`, and chart container `dataset`.
- Produces: `setPanLocked(locked: boolean)` behavior that synchronizes both charts and exposes `data-pan-locked`.

- [ ] **Step 1: Extend the chart stub and add failing state assertions**

In the test chart stub, retain the source element and record runtime options. Pass the whole element from `createChart` instead of only its name:

```js
function chart(element, chartOptions) {
  const value = {
  name: element.name,
  element,
  chartOptions,
  series: [],
  crosshairHandler: null,
  clickHandler: null,
  appliedOptions: [],
  // existing fields
  applyOptions(options) { this.appliedOptions.push(options); },
  remove() {},
  };
  created.push(value);
  return value;
}

// In LightweightCharts.createChart:
createChart(element, options) { return chart(element, options); }
```

Add `dataset: {}` to the inline price and volume chart elements passed to `createLinkedCharts`.

Replace the initial panning assertions with:

```js
assert.equal(created[0].chartOptions.handleScroll.pressedMouseMove, true);
assert.equal(created[1].chartOptions.handleScroll.pressedMouseMove, true);
assert.equal(created[0].element.dataset.panLocked, 'false');
assert.equal(created[1].element.dataset.panLocked, 'false');
```

After the first click that locks `2026-07-17`, add:

```js
assert.deepEqual(created[0].appliedOptions.at(-1), {
  handleScroll: {pressedMouseMove: false},
});
assert.deepEqual(created[1].appliedOptions.at(-1), {
  handleScroll: {pressedMouseMove: false},
});
assert.equal(created[0].element.dataset.panLocked, 'true');
assert.equal(created[1].element.dataset.panLocked, 'true');
```

After the next click that unlocks the chart, add:

```js
assert.deepEqual(created[0].appliedOptions.at(-1), {
  handleScroll: {pressedMouseMove: true},
});
assert.deepEqual(created[1].appliedOptions.at(-1), {
  handleScroll: {pressedMouseMove: true},
});
assert.equal(created[0].element.dataset.panLocked, 'false');
assert.equal(created[1].element.dataset.panLocked, 'false');
```

Add a CSS asset test:

```python
def test_chart_pan_state_has_distinct_cursors(self):
    css = (STATIC / "css/dashboard.css").read_text()
    self.assertIn('[data-pan-locked="false"]', css)
    self.assertIn('[data-pan-locked="true"]', css)
    self.assertIn("cursor: grab", css)
    self.assertIn("cursor: grabbing", css)
    self.assertIn("cursor: crosshair", css)
```

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run:

```bash
../../venv/bin/python -m unittest \
  tests.test_web_assets.WebAssetTest.test_chart_forecast_interaction \
  tests.test_web_assets.WebAssetTest.test_chart_pan_state_has_distinct_cursors -v
```

Expected: failures because initial panning is disabled, runtime state is not synchronized, and cursor rules do not exist.

- [ ] **Step 3: Implement the shared lock-aware interaction state**

Change the shared initial chart option:

```js
handleScroll: {
  mouseWheel: true,
  pressedMouseMove: true,
  horzTouchDrag: true,
  vertTouchDrag: true,
},
```

Inside `createLinkedCharts`, add:

```js
function setPanLocked(locked) {
  const panLocked = Boolean(locked);
  const options = {handleScroll: {pressedMouseMove: !panLocked}};
  priceChart.applyOptions(options);
  volumeChart.applyOptions(options);
  priceEl.dataset.panLocked = String(panLocked);
  volumeEl.dataset.panLocked = String(panLocked);
}

setPanLocked(false);
```

Update `handleClick`:

```js
function handleClick(param) {
  if (destroyed) return;
  const row = rowForParam(param);
  if (lockedTime !== null) {
    lockedTime = null;
    setPanLocked(false);
    paintDetail(row || rows.at(-1) || null, false);
    return;
  }
  if (!row) return;
  lockedTime = timeKey(row.time);
  setPanLocked(true);
  paintDetail(row, true);
}
```

After `lockedTime = null` in `setChartData`, call:

```js
setPanLocked(false);
```

- [ ] **Step 4: Add the state-specific cursors**

Append:

```css
.chart-placeholder[data-pan-locked="false"],
.volume-placeholder[data-pan-locked="false"] {
  cursor: grab;
}

.chart-placeholder[data-pan-locked="false"]:active,
.volume-placeholder[data-pan-locked="false"]:active {
  cursor: grabbing;
}

.chart-placeholder[data-pan-locked="true"],
.volume-placeholder[data-pan-locked="true"] {
  cursor: crosshair;
}
```

- [ ] **Step 5: Run the focused tests and confirm they pass**

Run the command from Step 2.

Expected: two passing tests.

- [ ] **Step 6: Run the complete automated suite**

Run:

```bash
../../venv/bin/python -m unittest discover -s tests -v
```

Expected: all tests pass without failures or errors.

- [ ] **Step 7: Verify NBIS interaction in the browser**

1. Load NBIS with no locked date and drag horizontally; confirm both charts pan together.
2. Click a historical date; confirm the date reads as locked and dragging no longer pans.
3. Click again; confirm the lock clears and horizontal dragging works again.
4. Confirm the cursor changes between grab/grabbing and crosshair.
5. Confirm hover inspection, forecast details, and the hidden model projection behavior are unchanged.

- [ ] **Step 8: Commit the tested implementation**

```bash
git add tests/test_web_assets.py web/static/js/charts.js web/static/css/dashboard.css
git commit -m "fix: make chart panning follow date lock"
```
