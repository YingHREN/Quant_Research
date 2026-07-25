# High-Level Distribution Risk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a causal high-level distribution and bearish top-turn state model that distinguishes elevated supply after a prior advance from ordinary low-level declines.

**Architecture:** Add one pure research module that derives grouped evidence and remembered state from point-in-time OHLCV. Attach that state as a separate forecast risk source, give only current confirmed structure damage downward override authority, and expose it through the existing model-output contract and fixed-size UI.

**Tech Stack:** Python 3.9, pandas, NumPy, unittest, immutable JSON contracts, vanilla JavaScript.

## Global Constraints

- Ridge predicted return and raw direction remain unchanged.
- Daily OHLCV can only support a suspected-distribution proxy, not verified institutional trading.
- Scores are deterministic rule scores, never probabilities.
- Every historical row uses only information available through that row.
- Related evidence is capped by group to prevent duplicate full scoring.
- Chart layers must not affect price-axis autoscaling, panning, zooming, or date locking.

---

### Task 1: Causal model kernel

**Files:**
- Create: `research/high_level_distribution.py`
- Test: `tests/test_high_level_distribution.py`

**Interfaces:**
- Consumes: `history: pd.DataFrame`, optional aligned `sector_close` and `qqq_history`.
- Produces: `build_high_level_distribution_state(history, sector_close=None, qqq_history=None) -> pd.DataFrame`.

- [ ] Write failing tests for confirmed high-level distribution, low-level exclusion, evidence-group caps, memory decay, and future-append invariance.
- [ ] Run `../../venv/bin/python -m unittest tests.test_high_level_distribution -v` and confirm failures are caused by the missing module.
- [ ] Implement validated causal features, grouped scores, gated raw states, and the existing 5/10-session risk memory.
- [ ] Re-run the focused test until all cases pass.
- [ ] Run market-pressure, risk-memory, group-regime, and slow-decline tests to catch shared-feature regressions.

### Task 2: Forecast risk context and policy

**Files:**
- Modify: `web/forecasts/decision.py`
- Test: `tests/test_web_forecast_decision.py`

**Interfaces:**
- Consumes: the Task 1 state frame for each modeled ticker.
- Produces: top-risk fields in `ForecastDecision`, risk-context rows, serialized decision payloads, and asymmetric policy behavior.

- [ ] Write failing tests proving `watch` retains Ridge, `high` downgrades Ridge up to neutral, current `confirmed` overrides to down, and fading remembered confirmation cannot independently override.
- [ ] Run the focused tests and verify the new assertions fail before implementation.
- [ ] Add top-risk context fields and attach per-ticker state using the mapped group composite and QQQ.
- [ ] Extend policy reasons and precedence without changing immediate-confirmation behavior.
- [ ] Re-run forecast decision and forecast service tests.

### Task 3: Model-output contract and localization

**Files:**
- Modify: `web/forecasts/model_outputs.py`
- Modify: `web/static/js/i18n.js`
- Modify: `web/static/js/model_outputs.js`
- Test: `tests/test_web_model_outputs.py`
- Test: `tests/model_outputs_runtime.mjs`

**Interfaces:**
- Consumes: serialized Task 2 decision fields.
- Produces: `high_level_distribution_risk_v1` in the downside model family with score, state, age, component scores, conditions, and limitation copy.

- [ ] Write failing Python contract tests for the new downside model and unavailable behavior.
- [ ] Write failing Node rendering assertions for Chinese/English names, state, component values, and proxy limitation.
- [ ] Run both test targets and observe expected failures.
- [ ] Implement the immutable presentation payload and generic UI detail rows without adding a chart overlay.
- [ ] Re-run Python and Node tests until green.

### Task 4: Historical evaluation and TODO status

**Files:**
- Create: `research/evaluate_high_level_distribution.py`
- Modify: `docs/modeling-todo.md`
- Test: `tests/test_evaluate_high_level_distribution.py`

**Interfaces:**
- Consumes: point-in-time model states and future OHLCV paths.
- Produces: coverage, event count, 5/10/20-session maximum adverse excursion, terminal return, precision, recall, and false-positive summaries.

- [ ] Write failing tests for purged future-window outcomes and empty/single-class reporting.
- [ ] Run the focused test and verify the missing evaluator causes failure.
- [ ] Implement deterministic evaluation without changing production thresholds.
- [ ] Mark only the completed TOPRISK-001 checklist items; leave calibration, richer Climax, and intraday items open.
- [ ] Re-run evaluation and documentation checks.

### Task 5: Verification and integration

**Files:**
- Verify all files modified in Tasks 1–4.

**Interfaces:**
- Consumes: complete feature branch.
- Produces: a reviewed, merge-ready TOPRISK-001 first vertical slice.

- [ ] Run `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ../../venv/bin/python -m unittest discover -s tests -q`.
- [ ] Run the repository Node-backed runtime tests.
- [ ] Run `git diff --check` and inspect the complete diff for future leakage, duplicated evidence, and mutable input changes.
- [ ] Commit the verified feature branch in reviewable commits.
- [ ] Merge locally to `main` only after all verification commands pass.
