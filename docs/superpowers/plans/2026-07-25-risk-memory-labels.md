# Directional Signal Labels and Bearish Risk Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename ambiguous signals and add a causal 5–10-session bearish-risk state without changing the raw rule score or Ridge feature contract.

**Architecture:** Add a focused `research/risk_memory.py` state transform, attach its output to historical and snapshot market-context payloads, and make the market UI prefer the state score while exposing the raw score. Keep existing internal factor keys stable and update bilingual presentation metadata.

**Tech Stack:** Python, pandas, unittest, vanilla JavaScript, Node runtime tests.

## Global Constraints

- Half-life is exactly 5 trading sessions and maximum memory age is exactly 10 trading sessions.
- The transform must use no future observations.
- `downside_risk_score` remains the raw daily score.
- Composite state scores are not Ridge features.
- Scores are described as rule indices, never probabilities.

---

### Task 1: Causal Risk State Transform

**Files:**
- Create: `research/risk_memory.py`
- Create: `tests/test_risk_memory.py`

**Interfaces:**
- Produces: `build_risk_memory_state(raw_scores: pd.Series, *, half_life_sessions: int = 5, window_sessions: int = 10, active_threshold: float = 20.0) -> pd.DataFrame`
- Output columns: `raw_score`, `state_score`, `state`, `memory_age_sessions`

- [ ] Write tests for decay, MU-like persistence, renewal, expiration, missing inputs, and future-row invariance.
- [ ] Run `./venv/bin/python -m unittest tests.test_risk_memory -v` and verify the missing module/function failure.
- [ ] Implement the recurrence and status classification.
- [ ] Run the focused tests and verify they pass.
- [ ] Commit the transform and tests.

### Task 2: Historical and Snapshot Integration

**Files:**
- Modify: `research/market_context.py`
- Modify: `tests/test_market_context.py`

**Interfaces:**
- Consumes: `build_risk_memory_state`
- Produces historical columns `downside_risk_state_score`, `downside_risk_state`, and `downside_risk_memory_age_sessions`
- Produces snapshot keys `raw_score`, `state_score`, `state`, `memory_age_sessions`, `memory_half_life_sessions`, `memory_window_sessions`, and `model_key`

- [ ] Write failing historical/API tests including the `34 → 15 → 5` regression.
- [ ] Run the focused market-context tests and verify expected missing-field failures.
- [ ] Add state columns without replacing the raw historical score.
- [ ] Attach the as-of state payload and aggregate constituent state scores.
- [ ] Run focused tests and commit.

### Task 3: Bilingual Labels and Risk-State UI

**Files:**
- Modify: `web/static/js/i18n.js`
- Modify: `web/static/js/market.js`
- Modify: `web/factors/builtin.py`
- Modify: `web/app.py`
- Modify: `tests/test_web_assets.py`
- Modify: `tests/test_web_market_assets.py`
- Modify: `tests/test_web_api.py`
- Modify: `tests/dashboard_runtime.mjs`

**Interfaces:**
- Consumes snapshot risk-memory fields.
- Produces directional Chinese/English labels, model-source help, and historical stock-chart hover fields for explicitly mapped semiconductor/AI-infrastructure tickers.

- [ ] Update tests to require all four directional names and risk-state detail.
- [ ] Run focused web tests and verify old-copy failures.
- [ ] Update i18n, factor metadata, annotations, and market rendering.
- [ ] Attach raw/state score, memory age, and model source to each mapped stock chart date; leave unmapped tickers unavailable.
- [ ] Run focused Python and Node tests and commit.

### Task 4: End-to-End Verification

**Files:**
- Modify only files required by failures attributable to this change.

- [ ] Run the complete Python suite.
- [ ] Run all repository JavaScript runtime tests.
- [ ] Inspect the MU 2026-06-26 through 2026-06-30 state values from the local database.
- [ ] Confirm no generated database/WAL files are staged.
- [ ] Review the diff and commit final fixes.
