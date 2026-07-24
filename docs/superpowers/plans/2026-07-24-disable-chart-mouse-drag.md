# Disable Chart Mouse-Drag Panning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent a pressed mouse movement from panning the linked price and volume charts while preserving forecast inspection, wheel navigation, range buttons, and touch gestures.

**Architecture:** Configure the existing shared `chartOptions(element)` factory so both linked charts receive the same explicit Lightweight Charts scroll policy. Extend the existing Node-backed chart interaction asset test to lock that policy down alongside its visible-range stability assertions.

**Tech Stack:** JavaScript ES modules, Lightweight Charts 5.0.8, Python `unittest`, Node.js test harness, Flask browser UI.

## Global Constraints

- Disable mouse-button drag panning on both the price chart and the volume chart.
- Keep pointer inspection, click-to-lock, mouse-wheel navigation, time-range buttons, and horizontal and vertical touch gestures available.
- Do not change forecast calculations, factor scores, chart data, axis formatting, or prediction rendering.
- Browser acceptance uses the NBIS chart with a locked historical forecast.

---

### Task 1: Lock the linked charts against pressed-mouse panning

**Files:**
- Modify: `tests/test_web_assets.py:1254-1257`
- Modify: `web/static/js/charts.js:193-218`

**Interfaces:**
- Consumes: Lightweight Charts `createChart(element, options)` and its `handleScroll` chart option.
- Produces: `chartOptions(element)` values with `handleScroll.pressedMouseMove === false` and the supported wheel/touch flags set to `true`.

- [ ] **Step 1: Write the failing chart-options assertions**

Add these assertions to `test_chart_forecast_interaction` immediately after the existing `shiftVisibleRangeOnNewBar` assertions:

```js
assert.equal(created[0].chartOptions.handleScroll.pressedMouseMove, false);
assert.equal(created[1].chartOptions.handleScroll.pressedMouseMove, false);
assert.equal(created[0].chartOptions.handleScroll.mouseWheel, true);
assert.equal(created[0].chartOptions.handleScroll.horzTouchDrag, true);
assert.equal(created[0].chartOptions.handleScroll.vertTouchDrag, true);
```

- [ ] **Step 2: Run the focused test and verify the red state**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_chart_forecast_interaction -v
```

Expected: `FAIL` because `created[0].chartOptions.handleScroll` is not defined.

- [ ] **Step 3: Add the minimal shared chart interaction configuration**

In `chartOptions(element)`, insert this property between `rightPriceScale` and `timeScale`:

```js
handleScroll: {
  mouseWheel: true,
  pressedMouseMove: false,
  horzTouchDrag: true,
  vertTouchDrag: true,
},
```

Because both `priceChart` and `volumeChart` are constructed with `chartOptions(...)`, the setting applies to both panels without runtime state coupling.

- [ ] **Step 4: Run the focused test and verify the green state**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_chart_forecast_interaction -v
```

Expected: `OK` with one passing test.

- [ ] **Step 5: Run the complete automated test suite**

Run:

```bash
./venv/bin/python -m unittest discover -s tests -v
```

Expected: all tests pass with no failures or errors.

- [ ] **Step 6: Verify the gesture in the browser**

Start or restart the local server from the implementation branch:

```bash
source env.sh
./venv/bin/python web/app.py
```

Open `http://127.0.0.1:5000/?ticker=NBIS`, then:

1. Click a historical candle and wait for the forecast line.
2. Press the mouse button over the price chart and drag to the right.
3. Confirm the visible dates on both the price and volume panels remain fixed.
4. Move the pointer without pressing and confirm historical inspection still updates.
5. Click another date and confirm click-to-lock still works.
6. Use the mouse wheel and a time-range button and confirm those interactions remain available.

- [ ] **Step 7: Commit the tested implementation**

```bash
git add tests/test_web_assets.py web/static/js/charts.js
git commit -m "fix: disable chart mouse drag panning"
```
