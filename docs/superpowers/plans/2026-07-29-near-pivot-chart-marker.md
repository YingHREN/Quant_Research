# Near-Pivot Chart Marker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mark the first trading session on which an accepted strict VCP enters its existing near-pivot price zone.

**Architecture:** The historical entry-signal builder emits a causal transition Boolean. The stock response maps that Boolean to an annotation, and the chart renders it inside the existing strict-VCP marker layer with localized copy.

**Tech Stack:** Python 3.9, pandas, Flask response assembly, vanilla JavaScript, Node runtime tests, `unittest`.

## Global Constraints

- Do not change strict VCP acceptance, pivot calculation, or breakout confirmation.
- Emit one marker on entry, not one marker for every day in the zone.
- A later exit and re-entry may emit another marker.
- Reuse the existing `strict_vcp` layer; do not add a toolbar checkbox.
- Missing historical fields must fail closed without fabricated markers.
- Use red-green-refactor.

---

### Task 1: Emit the Point-in-Time Transition

**Files:**
- Modify: `research/entry_signals.py`
- Test: `tests/test_entry_signals.py`

**Interfaces:**
- Consumes: `VCPPattern.stage`
- Produces: row field `strict_vcp_near_pivot_start: bool`

- [ ] Write a test with stages `forming, near_pivot, near_pivot, forming, near_pivot`
  and assert transition flags `False, True, False, False, True`.
- [ ] Run `./venv/bin/python -m unittest tests.test_entry_signals -v` and verify
  the missing field causes RED.
- [ ] Track the prior accepted near-pivot state and append the Boolean field.
- [ ] Run the focused test and verify GREEN.

### Task 2: Publish and Render the Annotation

**Files:**
- Modify: `web/app.py`
- Modify: `web/static/js/charts.js`
- Modify: `web/static/js/i18n.js`
- Test: `tests/test_web_assets.py`
- Test: `tests/test_web_api.py`

**Interfaces:**
- Consumes: chart row `strict_vcp_near_pivot_start`
- Produces: annotation type `strict_vcp_near_pivot_start`

- [ ] Add failing API and asset assertions for the annotation mapping, strict-VCP
  layer membership, marker style, priority, and bilingual label.
- [ ] Run the focused Python and Node-backed asset tests and verify RED.
- [ ] Map the row flag in `_structure_payload`, add the chart style/layer/priority,
  and add `chart.shape.strict_vcp_near_pivot_start` translations.
- [ ] Run the focused tests and verify GREEN.

### Task 3: Verify and Commit

**Files:**
- Verify all files above.

- [ ] Run `./venv/bin/python -m unittest tests.test_entry_signals tests.test_web_assets -v`.
- [ ] Run existing stock response tests that cover structure annotations.
- [ ] Run `node --check web/static/js/charts.js` and `git diff --check`.
- [ ] Commit the implementation without merging over the dirty main worktree.
