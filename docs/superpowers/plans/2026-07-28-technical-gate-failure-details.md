# Technical Gate Failure Details Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show localized failed and missing CAN SLIM technical-gate conditions beside the selected security header.

**Architecture:** Add one stable live region to the existing security header. A pure JavaScript formatter maps the existing `technical_gate.conditions` contract to localized display rows; `renderStockHeader` updates and hides the region without changing backend calculations.

**Tech Stack:** Flask/Jinja template, browser-native JavaScript, CSS, existing Python `unittest` Node harness.

## Global Constraints

- Do not modify `research/canslim_technical.py` calculations or API fields.
- Failed and missing conditions must remain semantically distinct.
- Hide the region when every condition passes.
- Dynamic text must use `textContent`, not HTML injection.
- Support Chinese and English.

---

### Task 1: Gate Detail Formatter and Header Rendering

**Files:**
- Modify: `tests/test_web_assets.py`
- Modify: `web/templates/index.html`
- Modify: `web/static/js/app.js`
- Modify: `web/static/js/i18n.js`
- Modify: `web/static/css/dashboard.css`

**Interfaces:**
- Consumes: `technical_gate.conditions[key] = {state, actual, threshold, reason}`
- Produces: `renderTechnicalGateDetails(element, gate, locale)` and the `#security-gate-details` live region.

- [ ] **Step 1: Write the failing frontend test**

Add a Node-backed asset test that renders a failed slope condition and a
missing 52-week-high condition, checks localized labels/current thresholds,
then renders four passing conditions and checks that the region is hidden.

- [ ] **Step 2: Run test to verify RED**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/gate-details-red \
/Users/renyinghao.1/Project/stock_screener/venv/bin/python \
-m unittest tests.test_web_assets.WebAssetTest.test_selected_gate_lists_failed_and_missing_conditions -v
```

Expected: fail because `renderTechnicalGateDetails` and the details element do not exist.

- [ ] **Step 3: Implement minimal rendering**

Add the live region below `.security-title-row`, export the pure renderer,
map all four condition keys to i18n labels, format actual/threshold values as
percentages, and use separate danger/unavailable tones.

- [ ] **Step 4: Run focused and regression tests**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/gate-details-green \
/Users/renyinghao.1/Project/stock_screener/venv/bin/python \
-m unittest tests.test_web_assets tests.test_web_api -v
```

Expected: 149 or more tests pass with zero failures.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-07-28-technical-gate-failure-details-design.md \
  docs/superpowers/plans/2026-07-28-technical-gate-failure-details.md \
  tests/test_web_assets.py web/templates/index.html web/static/js/app.js \
  web/static/js/i18n.js web/static/css/dashboard.css
git commit -m "web: explain unmet technical gate conditions"
```
