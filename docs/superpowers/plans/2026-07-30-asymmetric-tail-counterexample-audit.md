# Asymmetric Tail Counterexample Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the frozen asymmetric-tail counterexample audit with point-in-time strata and reproducible research-only evidence, without retraining or changing model authority.

**Architecture:** A pure audit module validates the published counterexample rows, attaches only observation-date feature values supplied by the caller, assigns fixed descriptive bands, and summarizes counts and raw outcomes. A standalone runner reconstructs the frozen 240-stock point-in-time feature frame, verifies the original input identity, publishes separate strict audit artifacts, and records only preregistered feature hypotheses.

**Tech Stack:** Python 3, pandas, NumPy, unittest, SQLite research inputs.

## Global Constraints

- The source sample remains calibrated downside probability at least 0.40 and raw five-day terminal return at least +10%.
- All context must be available on the observation date; future outcomes are used only to define the already-published counterexample cohort.
- Volatility and ATR percentiles are same-date cross-sectional ranks over the frozen study cohort.
- Earnings proximity is explicitly `unavailable` until a reliable point-in-time earnings calendar exists.
- Bands are fixed in code before inspecting grouped results; no ticker-specific tuning is permitted.
- Outputs remain `research` with `online_authority=none`; no Ridge, policy, API, UI, threshold, or model feature changes are permitted.

---

### Task 1: Pure point-in-time audit table and strata

**Files:**
- Create: `research/asymmetric_tail_counterexample_audit.py`
- Create: `tests/test_asymmetric_tail_counterexample_audit.py`

**Interfaces:**
- Produces: `attach_point_in_time_context(counterexamples, feature_frame) -> DataFrame`.
- Produces: `summarize_counterexamples(audit_rows) -> DataFrame`.
- Produces: `preregistered_feature_hypotheses() -> tuple[dict, ...]`.

- [x] **Step 1: Write failing exact-key, band, summary and authority tests**

Use hand-checked two-date fixtures. Assert exact ticker/date joins, same-date percentile ranks, fixed gap/volatility/ATR/price/liquidity bands, stable missing labels, raw untrimmed return means, and hypotheses that all retain `online_authority=none`.

- [x] **Step 2: Run tests and verify RED**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_asymmetric_tail_counterexample_audit -v`

Expected: import failure because the audit module does not exist.

- [x] **Step 3: Implement the minimal pure audit functions**

Validate unique exact keys and required finite sample-defining fields. Compute same-observation-date percentile ranks from the complete feature frame before joining selected rows. Assign fixed bands with explicit `unavailable` values and summarize every dimension with row count, share, raw mean/median terminal return, median path MAE, and median calibrated probabilities.

- [x] **Step 4: Run focused tests and verify GREEN**

Run the Task 1 command plus `tests.test_asymmetric_tail_risk`.

Expected: all tests pass.

### Task 2: Reproducible standalone audit runner

**Files:**
- Create: `research/run_asymmetric_tail_counterexample_audit.py`
- Create: `tests/test_run_asymmetric_tail_counterexample_audit.py`

**Interfaces:**
- Produces: `run_audit(...) -> dict`.
- Produces: strict atomic `reports/asymmetric-tail-risk-counterexample-audit.{json,csv,md}`.

- [x] **Step 1: Write failing runner and publication tests**

Assert source report identity checks, exact sample preservation, explicit earnings unavailability, strict finite JSON, research-only authority, atomic publication, and no secret or absolute-path leakage.

- [x] **Step 2: Run runner tests and verify RED**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_run_asymmetric_tail_counterexample_audit -v`

Expected: import failure because the runner does not exist.

- [x] **Step 3: Implement reconstruction, validation and publication**

Reconstruct the original cohort from its recorded seed, maximum ticker count, start date, and database fingerprint. Reject any sample count/key/filter mismatch. Publish audit rows as CSV and embed the grouped summary, fixed band definitions, data availability, limitations, and preregistered hypotheses in JSON and Markdown.

- [x] **Step 4: Run focused audit and existing runner tests**

Run both new test modules plus `tests.test_run_asymmetric_tail_risk`.

Expected: all tests pass without new warnings.

### Task 3: Publish real audit evidence and close the TODO

**Files:**
- Create: `reports/asymmetric-tail-risk-counterexample-audit.json`
- Create: `reports/asymmetric-tail-risk-counterexample-audit.csv`
- Create: `reports/asymmetric-tail-risk-counterexample-audit.md`
- Modify: `docs/modeling-todo.md`
- Modify: this plan

- [x] **Step 1: Run the real audit**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m research.run_asymmetric_tail_counterexample_audit`

Expected: exactly 9,405 source rows are preserved, original input identity matches, earnings proximity is explicitly unavailable, and no online authority is granted.

- [x] **Step 2: Inspect evidence and update the global TODO**

Record exact strata counts, missingness, audit limitations, and the frozen next-feature hypotheses. Mark only the counterexample audit complete; do not mark Ridge repaired or restore direction labels.

- [x] **Step 3: Verify and commit**

Run `git diff --check`, focused tests, the complete unittest suite, and local service health checks. Confirm ignored WAL/SHM and user research files remain untouched and unstaged.

Commit only implementation, tests, audit reports, and documentation with:

```bash
git commit -m "research: audit asymmetric tail counterexamples"
```
