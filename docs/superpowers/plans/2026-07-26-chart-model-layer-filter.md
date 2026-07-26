# Chart Model Layer Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent, collapsible multi-select control that lets users choose which historical model markers appear on the price chart.

**Architecture:** Put layer definitions, normalization, presets, and local-storage behavior in a small pure JavaScript module. The dashboard owns controls and persistence; the chart controller receives a normalized layer set and rerenders markers from its existing payload without an API call.

**Tech Stack:** Browser ES modules, Lightweight Charts 5.0.8, HTML/CSS, Node runtime tests launched by Python `unittest`.

## Global Constraints

- Filtering changes chart markers only; it must not change API payloads, model computation, model-output cards, factors, or final decisions.
- Default enabled layers are Strict VCP formation, VCP volume breakout, and Pocket Pivot.
- Preferences persist across refreshes, locale changes, range changes, and ticker changes.
- Invalid storage safely falls back to the core preset.
- UI copy must be available in Chinese and English.

---

### Task 1: Pure layer preference model

**Files:**
- Create: `web/static/js/marker_layers.js`
- Create: `tests/marker_layers_runtime.mjs`
- Modify: `tests/test_web_assets.py`

**Interfaces:**
- Produces: `MARKER_LAYER_DEFINITIONS`, `MARKER_LAYER_PRESETS`, `normalizeMarkerLayers(value)`, `readMarkerLayers(storage)`, and `persistMarkerLayers(layers, storage)`.
- Layer keys: `strict_vcp`, `vcp_breakout`, `pocket_pivot`, `tight_platform`, `structure_reversal`, `early_reversal`, `prior_high_breakout`, `trendline_breakout`, `higher_low`.

- [ ] **Step 1: Write the failing runtime test**

```js
assert.deepEqual(normalizeMarkerLayers(undefined), [
  "strict_vcp", "vcp_breakout", "pocket_pivot",
]);
assert.deepEqual(normalizeMarkerLayers(["pocket_pivot", "unknown", "pocket_pivot"]), [
  "pocket_pivot",
]);
assert.deepEqual(readMarkerLayers(brokenStorage), MARKER_LAYER_PRESETS.core);
persistMarkerLayers(["pocket_pivot"], writableStorage);
assert.deepEqual(JSON.parse(writableStorage.value), ["pocket_pivot"]);
```

- [ ] **Step 2: Run the test and verify RED**

Run: `./venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_marker_layer_preferences`

Expected: FAIL because `marker_layers.js` and its runtime do not exist.

- [ ] **Step 3: Implement the pure preference module**

```js
export const MARKER_LAYER_DEFINITIONS = Object.freeze([
  { key: "strict_vcp", core: true },
  { key: "vcp_breakout", core: true },
  { key: "pocket_pivot", core: true },
  { key: "tight_platform", core: false },
  { key: "structure_reversal", core: false },
  { key: "early_reversal", core: false },
  { key: "prior_high_breakout", core: false },
  { key: "trendline_breakout", core: false },
  { key: "higher_low", core: false },
]);
```

Normalize to known, unique keys in definition order. Wrap storage reads, JSON parsing, and writes in `try/catch`.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `./venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_marker_layer_preferences`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/static/js/marker_layers.js tests/marker_layers_runtime.mjs tests/test_web_assets.py
git commit -m "feat: add chart marker layer preferences"
```

### Task 2: Chart-controller filtering

**Files:**
- Modify: `web/static/js/charts.js`
- Modify: `tests/test_web_assets.py`

**Interfaces:**
- Consumes: normalized layer keys from `normalizeMarkerLayers`.
- Produces: `createLinkedCharts(...).setMarkerLayers(layers): string[]`.
- Maps annotation types `strict_vcp_start` and `strict_vcp` to `strict_vcp`; `vcp_breakout_confirmed` to `vcp_breakout`; and each remaining event to its stable layer key.

- [ ] **Step 1: Write a failing chart runtime assertion**

Add a payload containing at least one marker in every layer. Assert the initial marker texts contain only the three core layers. Then call:

```js
controller.setMarkerLayers(["pocket_pivot", "higher_low"]);
assert.deepEqual(markerTexts(), ["Pocket Pivot 需求确认", "更高低点"]);
```

- [ ] **Step 2: Run the focused chart test and verify RED**

Run: `./venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_chart_marker_layers_can_be_filtered_without_reloading`

Expected: FAIL because `setMarkerLayers` does not exist and all markers are rendered.

- [ ] **Step 3: Implement marker-layer filtering**

Maintain `markerLayers` inside `createLinkedCharts`. In `renderDecorations`, discard entry and reversal markers whose stable layer key is not enabled. Implement:

```js
function setMarkerLayers(nextLayers) {
  markerLayers = new Set(normalizeMarkerLayers(nextLayers));
  renderDecorations(lastPayload);
  return [...markerLayers];
}
```

Do not call `setChartData`, change the visible range, clear the locked date, or request forecasts.

- [ ] **Step 4: Run chart tests and verify GREEN**

Run: `./venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_chart_marker_layers_can_be_filtered_without_reloading`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/static/js/charts.js tests/test_web_assets.py
git commit -m "feat: filter chart markers by model layer"
```

### Task 3: Dashboard controls, persistence, and bilingual UI

**Files:**
- Modify: `web/templates/index.html`
- Modify: `web/static/js/app.js`
- Modify: `web/static/js/i18n.js`
- Modify: `web/static/css/dashboard.css`
- Modify: `tests/dashboard_runtime.mjs`
- Modify: `tests/test_web_assets.py`

**Interfaces:**
- Consumes: marker-layer module and chart controller `setMarkerLayers`.
- HTML selectors: `[data-marker-layer]`, `[data-marker-preset]`, `#marker-layer-count`.
- Presets: `core`, `all`, `none`.

- [ ] **Step 1: Write failing asset and runtime tests**

Assert that HTML contains all nine stable layer controls and three preset buttons. In the dashboard runtime, click `none`, enable only `pocket_pivot`, switch ticker/locale, and assert the chart receives `["pocket_pivot"]` and storage contains that selection.

- [ ] **Step 2: Run the tests and verify RED**

Run: `./venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_page_has_chart_marker_layer_controls tests.test_web_assets.WebAssetTest.test_dashboard_persists_chart_marker_layers`

Expected: FAIL because the controls, copy, and listeners are absent.

- [ ] **Step 3: Add the controls and application wiring**

Create a `details.marker-layer-filter` beneath the forecast controls. Initialize chart options with `readMarkerLayers()`. On checkbox or preset changes:

```js
const selected = checkedLayerKeys();
chartController.setMarkerLayers(selected);
persistMarkerLayers(selected);
syncMarkerLayerControls(selected);
```

Render the summary as localized `已显示 {selected}/{total} 个模型图层` / `{selected}/{total} model layers shown`.

- [ ] **Step 4: Add responsive styling**

Use a compact bordered panel with a wrapping grid of checkbox labels. Keep the collapsed summary in the toolbar flow and use one column on narrow screens.

- [ ] **Step 5: Run focused and full tests**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_assets
./venv/bin/python -m unittest discover -s tests
```

Expected: all tests PASS without warnings or errors.

- [ ] **Step 6: Browser verification**

Open NBIS in a three-month view. Verify:

- Default chart shows only core markers.
- “全部隐藏” removes event markers without moving the visible range.
- Enabling only Pocket Pivot displays the 2026-06-09 event.
- Ticker switch and page refresh preserve the selection.
- Chinese/English switching translates the filter without resetting it.

- [ ] **Step 7: Commit**

```bash
git add web/templates/index.html web/static/js/app.js web/static/js/i18n.js web/static/css/dashboard.css tests/dashboard_runtime.mjs tests/test_web_assets.py
git commit -m "feat: add chart model layer selector"
```

### Task 4: Final verification and handoff

**Files:**
- Modify only if verification reveals a regression.

**Interfaces:**
- Consumes the complete feature.
- Produces a tested main-branch implementation and a concise user handoff.

- [ ] **Step 1: Run diff checks**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; unrelated untracked files remain untouched.

- [ ] **Step 2: Run full verification**

Run: `./venv/bin/python -m unittest discover -s tests`

Expected: all tests PASS.

- [ ] **Step 3: Inspect final commits**

Run: `git log -5 --oneline`

Expected: design, preference model, chart filtering, and dashboard UI commits appear on `main`.
