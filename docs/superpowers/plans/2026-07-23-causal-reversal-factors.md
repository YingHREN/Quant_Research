# Causal Reversal Factors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build auditable point-in-time reversal factors and display them on every historical chart date.

**Architecture:** A focused `research/reversal.py` module performs sequential pivot confirmation and emits row-aligned feature mappings. The existing chart-row builder passes those mappings through the stock API; the existing chart adapter renders the trendline, markers, and hover diagnostics without recomputing finance logic.

**Tech Stack:** Python 3, pandas/numpy, Flask JSON API, vanilla ES modules, TradingView Lightweight Charts, unittest/Node assertions.

## Global Constraints

- Never use a pivot before its confirmation session.
- Prior-high resistance excludes the current session.
- Higher-low tolerance is exactly `0.25 * ATR20`.
- A composite candidate requires at least two same-session component events.
- No additional remote data access or investment-performance claim.
- Preserve at least 20px between price and volume chart containers.

---

### Task 1: Causal reversal computation

**Files:**
- Create: `research/reversal.py`
- Create: `tests/test_reversal.py`

**Interfaces:**
- Produces: `build_reversal_rows(history: pd.DataFrame) -> list[dict[str, object]]`

- [ ] Write tests for prior-high crossing, delayed pivot confirmation, trendline crossing, higher-low tolerance, and prefix invariance.
- [ ] Run `./venv/bin/python -m unittest tests.test_reversal` and verify failures are caused by the missing module.
- [ ] Implement confirmed pivots and aligned feature rows using only each historical prefix.
- [ ] Run `./venv/bin/python -m unittest tests.test_reversal` and verify all tests pass.

### Task 2: Factor registry and API rows

**Files:**
- Modify: `web/factors/builtin.py`
- Modify: `tests/test_web_factors.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `build_reversal_rows`
- Produces chart keys: `prior_high_resistance`, `prior_high_breakout`,
  `descending_trendline`, `trendline_breakout`, `higher_low_confirmed`,
  `reversal_signal_count`, `reversal_candidate`, and pivot audit fields.

- [ ] Add failing chart-row and registry tests for the new fields.
- [ ] Run the focused tests and confirm expected missing-field failures.
- [ ] Merge reversal rows into `build_chart_rows` and register component factors.
- [ ] Run the focused Python tests and verify they pass.

### Task 3: Trendline, event markers, and hover detail

**Files:**
- Modify: `web/static/js/charts.js`
- Modify: `web/static/js/i18n.js`
- Modify: `tests/test_web_assets.py`
- Modify: `tests/dashboard_runtime.mjs`

**Interfaces:**
- Consumes the Task 2 chart-row keys.
- Produces one descending-trendline series, localized details, and payload-driven event markers.

- [ ] Add failing JS contract/runtime assertions for the trendline, markers, and selected-date diagnostics.
- [ ] Run focused web-asset tests and confirm expected failures.
- [ ] Add the line series, localized detail fields, and component/composite markers.
- [ ] Run focused web-asset tests and verify they pass.

### Task 4: Documentation and verification

**Files:**
- Modify: `docs/dashboard.md`

- [ ] Document definitions, confirmation lag, API fields, and research-only status.
- [ ] Run `./venv/bin/python -m unittest discover -s tests`.
- [ ] Run `node --check web/static/js/charts.js` and `node --check web/static/js/i18n.js`.
- [ ] Inspect `git diff --check` and the final diff for future leakage or unrelated edits.

