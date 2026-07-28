# Market Return Labels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make sector absolute returns, QQQ-relative returns, and constituent latest-day returns explicit and internally consistent.

**Architecture:** Extend the existing point-in-time market context payload with a backend-computed constituent `daily_return`. Keep the existing group `returns` and `relative_return` fields as the only sources for sector values, and update the bilingual renderer to label each metric by its exact formula.

**Tech Stack:** Python, pandas, Flask JSON payloads, vanilla JavaScript DOM rendering, bilingual i18n, unittest.

## Global Constraints

- Use only point-in-time daily OHLCV data already present in the local database.
- Do not present daily values as intraday real-time quotes.
- Do not recompute model scores or returns in the browser.
- Keep Chinese and English labels aligned.

---

### Task 1: Add constituent latest-day return to the market payload

**Files:**
- Modify: `research/market_context.py`
- Test: `tests/test_market_context.py`

**Interfaces:**
- Consumes: `_daily_return(close: pd.Series, asof: pd.Timestamp) -> float | None`
- Produces: constituent JSON field `daily_return: float | None`

- [x] Add a failing test for `daily_return`.
- [x] Verify the test fails because the field is absent.
- [x] Add the backend field using `_daily_return`.
- [x] Verify the focused test passes.

### Task 2: Clarify heatmap and drilldown labels

**Files:**
- Modify: `web/static/js/market.js`
- Modify: `web/static/js/i18n.js`
- Modify: `web/static/css/market.css`
- Test: `tests/test_web_market_assets.py`

**Interfaces:**
- Consumes: `row.returns[String(state.horizon)]`, `row.relative_return`, and `row.daily_return`
- Produces: explicit absolute-return, QQQ-relative-return, and latest-day-return labels

- [x] Add failing tests for bilingual keys and payload bindings.
- [x] Verify the tests fail because keys and bindings are absent.
- [x] Render the selected-horizon return, QQQ-relative return, latest-day return, and formula note.
- [x] Verify all market asset tests pass.

### Task 3: Regression verification

- [x] Run market context, overview service, and asset tests.
- [x] Run the focused market-overview API route test.
- [x] Inspect the live local page and verify tile, summary, note, columns, and horizontal overflow behavior.
