# Market-Confirmed Direction Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and evaluate a leakage-safe direction challenger that measures the incremental value of QQQ, sector, and early-reversal evidence.

**Architecture:** Extend the causal feature frame with atomic cross-market and early-reversal fields, attach executable next-open labels, then run fixed expanding-window classifier ablations. Keep research evaluation separate from the live provider until the promotion gate passes.

**Tech Stack:** Python, pandas, NumPy, scikit-learn, unittest, SQLite

## Global Constraints

- Do not tune parameters on NBIS or AMD.
- Do not feed UI composite scores to the model.
- Training label end dates must be strictly earlier than each test period.
- Missing market or sector data must remain explicit.
- Preserve user-owned untracked files.

---

### Task 1: Causal market and early-reversal features

**Files:**
- Modify: `research/market_context.py`
- Modify: `web/forecasts/dataset.py`
- Modify: `tests/test_market_context.py`
- Modify: `tests/test_web_forecast_dataset.py`

**Interfaces:**
- Produces: additional numeric columns from `build_atomic_model_rows(...)`
- Produces: expanded `FEATURE_COLUMNS` in `build_feature_frame(...)`

- [ ] **Step 1: Write failing tests for QQQ continuous fields, sector trend, early-reversal atomic fields, and future-append invariance.**
- [ ] **Step 2: Run the focused tests and confirm missing-column or changed-value failures.**
- [ ] **Step 3: Implement the minimal causal feature series and dataset joins.**
- [ ] **Step 4: Run the focused tests and confirm they pass.**
- [ ] **Step 5: Commit the feature change.**

### Task 2: Executable labels and purged ablation evaluator

**Files:**
- Create: `research/market_direction_model.py`
- Create: `tests/test_market_direction_model.py`

**Interfaces:**
- Produces: `attach_next_open_targets(frame, histories, horizons)`
- Produces: `chronological_purged_folds(frame, horizon, n_folds)`
- Produces: `evaluate_direction_ablation(frame, horizon, feature_sets)`

- [ ] **Step 1: Write failing tests proving next-open entry prices, mature exit dates, and strict fold purging.**
- [ ] **Step 2: Run the tests and confirm the new module is missing.**
- [ ] **Step 3: Implement next-open labels and expanding chronological folds.**
- [ ] **Step 4: Write failing tests for training-only preprocessing, missingness indicators, and class metrics.**
- [ ] **Step 5: Implement class-balanced logistic ablations and metric aggregation.**
- [ ] **Step 6: Run the focused tests and confirm they pass.**
- [ ] **Step 7: Commit the evaluator.**

### Task 3: Full-universe study and promotion decision

**Files:**
- Create: `research/run_market_direction_study.py`
- Create: `docs/research/market-direction-ablation-2026-07-25.md`
- Modify: `docs/modeling-todo.md`

**Interfaces:**
- Consumes: local `data/prices.db`
- Produces: deterministic Markdown report and CSV metrics

- [ ] **Step 1: Add a CLI smoke test for report generation on a small fixture.**
- [ ] **Step 2: Run the test and confirm the command/report behavior is absent.**
- [ ] **Step 3: Implement the CLI with fixed feature sets and promotion gates.**
- [ ] **Step 4: Run the local full-universe and semiconductor subgroup study.**
- [ ] **Step 5: Record metrics, NBIS/AMD diagnostics, limitations, and the promotion decision.**
- [ ] **Step 6: Update the modeling TODO with evidence and remaining work.**
- [ ] **Step 7: Run focused and full tests, inspect git diff, and commit the study.**

### Task 4: Conditional production promotion

**Files:**
- Modify only if the written promotion gate passes: `web/forecasts/ridge.py`, `web/services/forecasts.py`, related tests and UI model metadata

**Interfaces:**
- Consumes: Task 3 promotion decision
- Produces: a versioned live provider or no production change

- [ ] **Step 1: If the gate fails, document that Ridge remains live and make no provider change.**
- [ ] **Step 2: If the gate passes, write failing provider tests for the chosen feature set and direction output.**
- [ ] **Step 3: Implement a new versioned provider without rewriting historical forecast records.**
- [ ] **Step 4: Run provider, API, UI, and full regression tests.**
- [ ] **Step 5: Commit only the evidence-supported production change.**
