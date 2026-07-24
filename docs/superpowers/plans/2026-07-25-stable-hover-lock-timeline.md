# Stable Hover and Lock Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent historical forecast loading from moving the NBIS chart timeline in either unlocked or locked interaction states.

**Architecture:** Price rows remain the sole owner of chart time coordinates. Forecast results update only the forecast marker and detail panel; the disabled forecast projection series remains empty and no transparent anchor series inserts future dates.

**Tech Stack:** Vanilla JavaScript ES modules, TradingView Lightweight Charts, Python `unittest`, Node-based JavaScript asset tests.

## Global Constraints

- Historical forecast responses must not add target dates, projection dates, whitespace points, or any other data to a chart series.
- Mouse-drag panning remains disabled in both locked and unlocked states.
- Mouse wheel and touch behavior remain unchanged.
- Forecast model calculations, factor scores, and market data remain unchanged.

---

### Task 1: Make Forecast Updates Time-Axis Neutral

**Files:**
- Modify: `tests/test_web_assets.py`
- Modify: `web/static/js/charts.js`

**Interfaces:**
- Consumes: `createLinkedCharts(priceEl, volumeEl, detailEl, options)`, `controller.setChartData(payload)`, and `controller.setForecasts(payload)`.
- Produces: the existing chart controller API with the guarantee that `setForecasts` never changes chart series dates or the visible logical range.

- [x] **Step 1: Write failing unlocked and locked regression assertions**

In `test_chart_forecast_interaction`, create a historical forecast payload whose
`target_date` and `projection_dates` are absent from the original price rows.
Call `controller.setForecasts(historicalPayload)` before and after locking
`2026-07-17`. Assert in both states:

```javascript
assert.deepEqual(created[0].sharedTimes(), priceTimesBeforeHistoricalForecast);
assert.deepEqual(created[0].timeScale().range, rangeBeforeHistoricalForecast);
assert.deepEqual(created[1].timeScale().range, rangeBeforeHistoricalForecast);
assert.deepEqual(forecastSeries.data, []);
```

After locking and applying the historical payload, also assert:

```javascript
assert.equal(created[0].element.dataset.panLocked, 'true');
assert.equal(created[1].element.dataset.panLocked, 'true');
assert.equal(created[0].crosshairPositions.at(-1).time, '2026-07-17');
assert.equal(created[1].crosshairPositions.at(-1).time, '2026-07-17');
assert.match(textTree(detail), /已锁定/);
```

Remove the old assertions that require a transparent timeline anchor or future
forecast dates to appear in `sharedTimes()`.

- [x] **Step 2: Run the focused test and verify RED**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
  ./venv/bin/python -m unittest \
  tests.test_web_assets.WebAssetTest.test_chart_forecast_interaction -v
```

Expected: FAIL because the transparent anchor and forecast projection series
still add the new target/projection dates to the chart's shared time index.

- [x] **Step 3: Remove forecast-owned time coordinates**

In `web/static/js/charts.js`:

1. Delete `forecastTimelineDates`.
2. Delete `timelineAnchorSeries`, `timelineAnchorDates`, and
   `replaceTimelineAnchor`.
3. Remove both `replaceTimelineAnchor(...)` calls.
4. Delete `setForecastProjectionData` and `updateForecastProjection`.
5. Remove the `updateForecastProjection(row, forecast)` call from
   `paintDetail`.
6. Keep `forecastProjectionSeries` registered and hidden for compatibility,
   but never call its `setData` method. A newly registered series is already
   empty, and even an empty `setData([])` can emit a chart interaction event.

No forecast response may call `setData` on any chart series.

- [x] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
  ./venv/bin/python -m unittest \
  tests.test_web_assets.WebAssetTest.test_chart_forecast_interaction -v
```

Expected: one test passes.

- [x] **Step 5: Run complete verification**

Run:

```bash
git diff --check
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
  ./venv/bin/python -m unittest discover -s tests -v
```

Expected: `git diff --check` exits zero and all tests pass.

- [x] **Step 6: Browser regression**

Restart `web/app.py`, reload `http://127.0.0.1:5000/?ticker=NBIS`, and verify:

1. Hovering across 2026-06-22 and 2026-06-24 remains continuous after the
   historical forecast response completes.
2. Locking 2026-06-30 keeps the price crosshair, volume crosshair, displayed
   date, and visible candle positions fixed while the pointer moves.
3. Clicking again unlocks the date.

- [x] **Step 7: Commit**

```bash
git add tests/test_web_assets.py web/static/js/charts.js \
  docs/superpowers/plans/2026-07-25-stable-hover-lock-timeline.md
git commit -m "fix: keep forecast updates off chart timeline"
```
