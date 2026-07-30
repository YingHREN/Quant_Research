# Unproven Ridge Direction UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep every raw Ridge forecast intact while making its direction reliability explicit in the API and bilingual model-output UI.

**Architecture:** Derive one normalized reliability enum from forecast availability and the existing point-in-time evaluation in `web/forecasts/model_outputs.py`. Copy that derived value into the primary and final-policy presentation contracts without changing decision logic, then render it as a second card badge and detail field with legacy-payload fallback.

**Tech Stack:** Python 3, `unittest`, Flask presentation contracts, browser-native JavaScript, Node.js runtime tests, CSS.

## Global Constraints

- Preserve `predicted_return`, raw direction, final direction, and `forecast_decision_policy` behavior.
- Ridge remains informational and becomes `research`; no model permission is promoted.
- Missing legacy fields must remain renderable and must not be interpreted as proven.
- Do not touch or stage runtime SQLite WAL/SHM files or `research/high_level_reversal_study.py`.

---

### Task 1: API reliability contract

**Files:**
- Modify: `tests/test_web_model_outputs.py`
- Modify: `web/forecasts/model_outputs.py`

**Interfaces:**
- Consumes: forecast `predicted_return` and evaluation `evidence_status`.
- Produces: primary `direction_reliability` and decision `primary_direction_reliability`, each one of `proven`, `unproven`, `insufficient`, `not_precomputed`, or `unavailable`.

- [x] **Step 1: Write the failing API tests**

Add table-driven assertions covering every mapping, Ridge `research` lifecycle, unchanged raw/final directions, and the decision reliability mirror.

- [x] **Step 2: Run the focused test to verify RED**

Run: `./venv/bin/python -m unittest tests.test_web_model_outputs.ModelOutputContractTest -v`

Expected: FAIL because the reliability fields are absent and Ridge still reports `production`.

- [x] **Step 3: Implement the minimal contract**

Add a small normalization helper, use it in primary and decision output builders, and change only the Ridge registry lifecycle to `research`.

- [x] **Step 4: Run the focused test to verify GREEN**

Run: `./venv/bin/python -m unittest tests.test_web_model_outputs.ModelOutputContractTest -v`

Expected: PASS.

### Task 2: Bilingual UI reliability warning

**Files:**
- Modify: `tests/model_outputs_runtime.mjs`
- Modify: `web/static/js/model_outputs.js`
- Modify: `web/static/js/i18n.js`
- Modify: `web/static/css/dashboard.css`

**Interfaces:**
- Consumes: the two optional reliability fields from Task 1.
- Produces: a reliability badge on Ridge cards, a “primary forecast reliability” field on the decision card, and no badge for legacy payloads.

- [x] **Step 1: Write the failing browser-runtime assertions**

Assert Chinese and English warning copy, the second reliability badge, the decision explanation field, and successful rendering after removing both optional fields.

- [x] **Step 2: Run the runtime test to verify RED**

Run: `./venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_model_output_renderer_is_bilingual_and_explicit_about_scores -v`

Expected: FAIL because reliability-specific labels and badges are absent.

- [x] **Step 3: Implement the minimal renderer and translations**

Render badges only when the primary field exists, render the decision field only when its mirror exists, add enum/field/lifecycle translations, and add warning badge colors without changing layout dimensions.

- [x] **Step 4: Run the runtime test to verify GREEN**

Run the same focused unittest and expect PASS.

### Task 3: Regression, documentation, and commit

**Files:**
- Modify: `docs/modeling-todo.md`
- Verify: all changed production and test files.

**Interfaces:**
- Consumes: completed API and UI behavior.
- Produces: checked global P0 TODO item and a reproducible git commit on `main`.

- [x] **Step 1: Run focused and risk-matched regressions**

Run the model-output, registry, API contract, and web asset suites, then the repository’s full test command discovered from existing conventions.

- [x] **Step 2: Verify local service health**

Request `http://127.0.0.1:5000/` and one read-only health or universe endpoint already used by the application; confirm successful HTTP responses.

- [x] **Step 3: Update the Chinese global TODO**

Mark the 5-day unproven-direction display item complete and record the reliability contract, unchanged raw values, research lifecycle, bilingual warning, and test evidence.

- [x] **Step 4: Review and commit**

Confirm `git diff --check`, inspect the staged file list to exclude protected runtime files and secrets, and commit with `fix: mark unproven ridge direction`.
