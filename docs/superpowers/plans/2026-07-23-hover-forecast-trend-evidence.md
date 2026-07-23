# Historical Hover Forecast and Trend Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep chart dates fully visible, make every historical point-in-time forecast line unmistakable, and explain causal conditions that strengthen an uptrend or accelerate a downtrend.

**Architecture:** Keep chart interaction in `charts.js`, move trend-condition calculation and rendering into a focused `trend_evidence.js` module, and reuse the selected chart row plus earlier rows only. The forecast detail renderer receives already-calculated evidence and remains presentation-only.

**Tech Stack:** Flask, vanilla ES modules, TradingView Lightweight Charts, CSS Grid, Python `unittest` with Node-based JavaScript adapter tests.

## Global Constraints

- All evidence must use only observations available on or before the selected date.
- Forecast paths are straight endpoint guides, not predicted daily price paths.
- Missing causal inputs render as unavailable and never borrow the latest value.
- Chinese and English copy must remain complete.
- The UI must not provide trade entries, stops, position sizes, guarantees, or investment advice.
- Use TDD for every behavior change and keep `main` clean until branch completion.

---

### Task 1: Date-Axis Gutter and Historical Forecast Emphasis

**Files:**
- Modify: `web/static/js/charts.js`
- Modify: `web/static/js/i18n.js`
- Modify: `web/static/css/dashboard.css`
- Test: `tests/test_web_assets.py`

**Interfaces:**
- Consumes: `createLinkedCharts(priceEl, volumeEl, detailEl, options)`
- Produces: `chartHeight(element) -> number` internally; visible forecast marker text tied to the selected date.

- [ ] **Step 1: Write failing adapter and CSS tests**

Add assertions that chart creation and resize reserve a 12-pixel axis gutter, and that the forecast series is a three-pixel solid line with a start label:

```python
self.assertIn("const AXIS_GUTTER_PX = 12", chart_source)
self.assertIn("height: chartHeight(element)", chart_source)
self.assertIn("height: chartHeight(priceEl)", chart_source)
self.assertIn("height: chartHeight(volumeEl)", chart_source)
self.assertIn("--chart-axis-gutter: 12px", css)
self.assertIn("预测起点", zh_marker_text)
```

Extend the Lightweight Charts test stub with `LineStyle: { Solid: 0, Dashed: 2 }` and assert:

```javascript
const projection = created[0].series.find(
  (series) => series.options.title === "模型预测线",
);
assert.equal(projection.options.lineWidth, 3);
assert.equal(projection.options.lineStyle, LightweightCharts.LineStyle.Solid);
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_assets.WebAssetTest.test_chart_adapter_plots_shape_levels_annotations_and_volume_diagnostics \
  tests.test_web_assets.WebAssetTest.test_dashboard_css_reserves_chart_axis_gutter
```

Expected: failure because the gutter constant, solid forecast style, and CSS variable do not exist.

- [ ] **Step 3: Implement the chart gutter and forecast emphasis**

In `charts.js`, add:

```javascript
const AXIS_GUTTER_PX = 12;

function chartHeight(element) {
  return Math.max(1, element.clientHeight - AXIS_GUTTER_PX);
}
```

Use `chartHeight(element)` in `chartOptions()` and use `chartHeight(priceEl)` /
`chartHeight(volumeEl)` in `resizeCharts()`.

Change the forecast series options to:

```javascript
{
  title: t("chart.series.forecastProjection", {}, locale),
  color: COLORS.forecast,
  lineWidth: 3,
  lineStyle: LightweightCharts.LineStyle.Solid,
  crosshairMarkerVisible: false,
  priceLineVisible: false,
  lastValueVisible: true,
}
```

Add `--chart-axis-gutter: 12px` to `:root` and document the reserved inset on
`.chart-placeholder, .volume-placeholder`.

Change `forecast.marker` to “预测起点 · {direction}” and
“Forecast start · {direction}”.

- [ ] **Step 4: Run focused tests**

Run the command from Step 2.

Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_web_assets.py web/static/js/charts.js \
  web/static/js/i18n.js web/static/css/dashboard.css
git commit -m "fix: keep chart dates and historical forecasts visible"
```

---

### Task 2: Pure Causal Trend-Evidence Model

**Files:**
- Create: `web/static/js/trend_evidence.js`
- Modify: `tests/test_web_assets.py`

**Interfaces:**
- Consumes: `row`, `rows`, selected `index`, optional date-matched factor map.
- Produces:

```javascript
trendEvidence(row, {
  rows = [],
  index = -1,
  factorsByDate = new Map(),
} = {}) -> {
  upward: Array<{key, state, evidence, threshold}>,
  downward: Array<{key, state, evidence, threshold}>,
}
```

- [ ] **Step 1: Write failing model tests**

Add a Node test covering met, near, not-met, and unavailable states:

```javascript
const row = {
  time: "2026-07-10",
  close: 102,
  daily_return: 0.018,
  true_range_pct: 3.2,
  volume_ratio: 1.35,
  ema20: 100,
  sma50: 99,
  atr20: 2,
  prior_high_resistance: 101,
  prior_high_breakout: true,
  descending_trendline: 103,
  trendline_breakout: false,
  higher_low_confirmed: true,
  higher_low_latest_price: 96,
};
const evidence = trendEvidence(row, { rows: [row], index: 0 });
assert.equal(evidence.upward.find(item => item.key === "prior_high_breakout").state, "met");
assert.equal(evidence.upward.find(item => item.key === "trendline_breakout").state, "near");
assert.equal(evidence.upward.find(item => item.key === "volume_confirmation").state, "met");
assert.equal(evidence.downward.find(item => item.key === "support_loss").state, "not_met");
assert.equal(evidence.upward.find(item => item.key === "momentum").state, "unavailable");
```

Add a failed-breakout case with an earlier breakout row and a later close below
that row's resistance. Assert no rows after `index` are inspected.

- [ ] **Step 2: Run the model test and confirm failure**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_assets.WebAssetTest.test_trend_evidence_is_causal_and_stateful
```

Expected: failure because `trend_evidence.js` does not exist.

- [ ] **Step 3: Implement `trendEvidence`**

Implement helpers:

```javascript
const STATES = Object.freeze({
  MET: "met",
  NEAR: "near",
  NOT_MET: "not_met",
  UNAVAILABLE: "unavailable",
});

function distanceState(close, level, atr, crossed) {
  if (!Number.isFinite(level)) return STATES.UNAVAILABLE;
  if (crossed) return STATES.MET;
  if (!Number.isFinite(atr) || atr <= 0) return STATES.NOT_MET;
  return level - close >= 0 && level - close <= atr * 0.5
    ? STATES.NEAR
    : STATES.NOT_MET;
}
```

Use these exact causal rules:

- prior-high and trendline: met from their boolean; near within `0.5 * ATR`;
- higher low: met/not-met from the boolean;
- trend support: met above EMA20 and SMA50, near above exactly one;
- volume confirmation: up day plus ratio `>= 1.2` is met, ratio `>= 1.0` is near;
- support loss: met below EMA20 and SMA50, near below exactly one;
- lower-low risk: met below `higher_low_latest_price`, near within `0.5 * ATR` above it;
- distribution volume: down day plus ratio `>= 1.2` is met, ratio `>= 1.0` is near;
- volatility expansion: met when `true_range_pct >= (atr20 / close * 100) * 1.25`,
  near at the ATR percentage;
- failed breakout: inspect only `rows.slice(Math.max(0, index - 10), index + 1)`;
- momentum: read only `factorsByDate.get(row.time)`; otherwise unavailable.

- [ ] **Step 4: Run the model test**

Run the command from Step 2.

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add web/static/js/trend_evidence.js tests/test_web_assets.py
git commit -m "feat: add causal trend evidence model"
```

---

### Task 3: Trend-Evidence Rendering and Selected-Date Wiring

**Files:**
- Modify: `web/static/js/trend_evidence.js`
- Modify: `web/static/js/charts.js`
- Modify: `web/static/js/forecasts.js`
- Modify: `web/static/js/i18n.js`
- Modify: `web/static/css/dashboard.css`
- Test: `tests/test_web_assets.py`

**Interfaces:**
- Consumes: `trendEvidence(...)` from Task 2.
- Produces: `renderTrendEvidence(container, evidence, locale)` and
  `trendEvidenceText(item, locale)`.

- [ ] **Step 1: Write failing rendering and wiring tests**

Add a DOM-stub test:

```javascript
const section = node();
renderTrendEvidence(section, {
  upward: [{ key: "prior_high_breakout", state: "met", evidence: "102", threshold: "101" }],
  downward: [{ key: "support_loss", state: "near", evidence: "EMA20", threshold: "100" }],
}, "zh-CN");
assert.match(textTree(section), /上涨强化条件/);
assert.match(textTree(section), /突破前高 已满足/);
assert.match(textTree(section), /下跌加速条件/);
assert.match(textTree(section), /跌破趋势支撑 接近/);
```

In the chart adapter test, assert the detail renderer receives evidence for the
same selected date and that changing the crosshair changes the evidence source.

- [ ] **Step 2: Run focused tests and confirm failure**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_assets.WebAssetTest.test_trend_evidence_rendering_is_localized \
  tests.test_web_assets.WebAssetTest.test_chart_forecast_interaction
```

Expected: failure because rendering and chart wiring are absent.

- [ ] **Step 3: Implement semantic rendering**

Create a section with:

```html
<section class="trend-evidence" aria-label="Trend evidence">
  <div class="trend-evidence-column trend-evidence-up">...</div>
  <div class="trend-evidence-column trend-evidence-down">...</div>
</section>
```

Each item must contain a text state badge and optional evidence/threshold text.
Add localized keys for headings, all condition names, all four states, evidence,
threshold, and the research disclaimer.

Style two columns on desktop and one column under the existing mobile
breakpoint. Use color only as a secondary cue.

- [ ] **Step 4: Wire evidence to the selected row**

In `charts.js`, build:

```javascript
const selectedIndex = date === null ? -1 : rows.findIndex(
  candidate => timeKey(candidate.time) === date,
);
const evidence = trendEvidence(row, { rows, index: selectedIndex });
```

Pass `evidence` to `renderDetail`, and pass it through to
`renderForecastDetail`. Render trend evidence after forecast values and before
historical model evaluation.

When `lockedTime !== null`, continue using `displayedRow`; crosshair movement
must not replace its evidence.

- [ ] **Step 5: Run focused tests**

Run the command from Step 2.

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add web/static/js/trend_evidence.js web/static/js/charts.js \
  web/static/js/forecasts.js web/static/js/i18n.js \
  web/static/css/dashboard.css tests/test_web_assets.py
git commit -m "feat: explain trend strengthening conditions"
```

---

### Task 4: Browser Regression and Documentation

**Files:**
- Modify: `docs/dashboard.md`
- Test: `tests/test_web_assets.py`

**Interfaces:**
- Consumes: completed Tasks 1-3.
- Produces: documented behavior and browser evidence for the supplied failure.

- [ ] **Step 1: Document the endpoint-guide and causal evidence**

Add to `docs/dashboard.md`:

```markdown
The selected-date forecast line is a straight endpoint guide, not a predicted
daily path. Trend-strengthening evidence uses only rows available on or before
the selected date; missing inputs remain unavailable.
```

- [ ] **Step 2: Run full automated verification**

Run:

```bash
./venv/bin/python -m unittest discover -s tests
node --check web/static/js/api.js
node --check web/static/js/app.js
node --check web/static/js/charts.js
node --check web/static/js/factors.js
node --check web/static/js/forecasts.js
node --check web/static/js/i18n.js
node --check web/static/js/trend_evidence.js
node --check web/static/js/update.js
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
  ./venv/bin/python -m py_compile web/app.py web/market_calendar.py
git diff --check
```

Expected: all tests pass and every check exits zero.

- [ ] **Step 3: Reproduce the supplied visual case**

In the local browser:

1. use a wide viewport comparable to the supplied screenshot;
2. select MSFT and the one-year range;
3. hover three non-latest dates;
4. wait for each historical forecast;
5. confirm the full date label, forecast start marker, continuous line, endpoint,
   and detail date all agree;
6. lock one date and switch 5/20/60 sessions;
7. inspect the two evidence columns;
8. repeat at 390x844 and confirm no horizontal overflow;
9. confirm the console has no errors.

- [ ] **Step 4: Commit documentation**

```bash
git add docs/dashboard.md tests/test_web_assets.py
git commit -m "docs: describe historical trend evidence"
```

- [ ] **Step 5: Complete the branch**

Run the `superpowers:verification-before-completion`,
`superpowers:requesting-code-review`, and
`superpowers:finishing-a-development-branch` workflows. Merge the verified
branch into local `main` only after the completion checks pass.
