# Early Reversal Watch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a causal end-of-session early-reversal watch that identifies NBIS on 2026-07-17 without changing the confirmed reversal count or Ridge direction.

**Architecture:** A focused research module combines the existing causal reversal rows with the existing market-pressure rows and emits four atomic conditions, a 0–100 score, stable condition codes, and a watch flag for every session. The chart adapter carries those values into the stock API; the factor registry and linked chart render a localized score, explanations, and an amber marker without extending or moving the chart timeline.

**Tech Stack:** Python 3, pandas, Flask stock API, vanilla JavaScript, Lightweight Charts, `unittest`.

## Global Constraints

- Every condition must use only observations available through the selected session close.
- The existing `reversal_signal_count`, `reversal_candidate`, Ridge features, and bearish direction adjustment must not change.
- Reuse `research.market_pressure.build_pressure_rows`; do not duplicate volume-ratio or close-location formulas.
- A score is descriptive evidence, not a probability or investment instruction.
- Structured condition codes must remain stable across Chinese and English rendering.

---

### Task 1: Causal early-watch research rows

**Files:**
- Create: `research/early_reversal.py`
- Create: `tests/test_early_reversal.py`

**Interfaces:**
- Consumes: `build_pressure_rows(history: pd.DataFrame) -> pd.DataFrame` and `build_reversal_rows(history: pd.DataFrame) -> list[dict[str, object]]`
- Produces: `build_early_reversal_rows(history: pd.DataFrame, reversal_rows: Sequence[Mapping[str, object]] | None = None) -> list[dict[str, object]]`

- [ ] **Step 1: Write failing unit tests**

Cover exact inclusive thresholds, required-gate behavior, 75-point support,
future-row append invariance, non-mutation, and the four stable codes:
`prior_session_selloff`, `current_price_acceptance`,
`descending_trendline_proximity`, and `current_volume_support`.

- [ ] **Step 2: Run the new test module and verify failure**

Run:

```bash
./venv/bin/python -m unittest tests.test_early_reversal -v
```

Expected: import failure because `research.early_reversal` does not exist.

- [ ] **Step 3: Implement the minimal causal calculation**

Each output row contains:

```python
{
    "early_reversal_score": 0,
    "early_reversal_watch": False,
    "early_reversal_conditions": [],
    "early_prior_session_selloff": False,
    "early_current_price_acceptance": False,
    "early_descending_trendline_proximity": False,
    "early_current_volume_support": False,
}
```

Award 25 points per satisfied condition. Require both the prior-session
selloff and current-session acceptance plus a total score of at least 75 for
`early_reversal_watch`.

- [ ] **Step 4: Run unit tests and verify they pass**

Run:

```bash
./venv/bin/python -m unittest tests.test_early_reversal -v
```

- [ ] **Step 5: Commit the research layer**

```bash
git add research/early_reversal.py tests/test_early_reversal.py
git commit -m "feat: add causal early reversal watch"
```

### Task 2: Chart rows, stock API, and factor registration

**Files:**
- Modify: `web/factors/builtin.py`
- Modify: `tests/test_web_factors.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `build_early_reversal_rows(...)`
- Produces: seven early-watch fields on every chart row and a numeric `early_reversal_score` structure factor

- [ ] **Step 1: Extend failing chart-row and registry tests**

Assert that all early-watch fields are present, score is an integer from 0 to
100, watch is boolean, condition codes are a list, the factor has complete
Chinese metadata, and the API serializes the same fields without changing
`reversal_signal_count`.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_factors tests.test_web_api -v
```

- [ ] **Step 3: Integrate the research rows once per chart build**

Call:

```python
early_rows = build_early_reversal_rows(history, reversal_rows)
```

Merge `early_rows[position]` beside `reversal_rows[position]`. Register
`early_reversal_score` as a structure factor formatted as `{score}/100`, with
`percentile_eligible=False` so the evidence score is not replaced by a
cross-sectional percentile.

- [ ] **Step 4: Run focused tests and verify they pass**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_factors tests.test_web_api -v
```

- [ ] **Step 5: Commit the web data contract**

```bash
git add web/factors/builtin.py tests/test_web_factors.py tests/test_web_api.py
git commit -m "feat: expose early reversal evidence"
```

### Task 3: Localized chart marker and selected-date explanation

**Files:**
- Modify: `web/static/js/charts.js`
- Modify: `web/static/js/i18n.js`
- Modify: `tests/test_web_assets.py`

**Interfaces:**
- Consumes: chart-row early-watch fields and stable condition codes
- Produces: localized selected-date detail entries and an amber `arrowUp` marker for qualifying rows

- [ ] **Step 1: Write failing JavaScript asset tests**

Assert that a qualifying row renders “Early reversal watch · 100” / “早期反转观察 · 100”,
shows `100/100`, lists the four localized contributing conditions, retains
the separate `0/3` confirmed-reversal count, and does not create forecast
dates or change the visible range.

- [ ] **Step 2: Run the focused asset test and verify failure**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_chart_adapter_plots_shape_levels_annotations_and_volume_diagnostics -v
```

- [ ] **Step 3: Implement localized detail and markers**

Add chart-detail labels for score, state, and conditions. Add an amber
above-bar marker only when `early_reversal_watch === true`; its text is
localized and includes the score. Keep marker creation inside the existing
row-based decoration pass so it cannot add timestamps.

- [ ] **Step 4: Verify NBIS acceptance dates in the real local database**

Run a point-in-time check through 2026-07-17 and assert:

```text
score=100, watch=true, reversal_signal_count=0
```

Then check 2026-07-20 and assert the existing trendline breakout remains a
separate confirmed event.

- [ ] **Step 5: Run the full suite**

Run:

```bash
git diff --check
./venv/bin/python -m unittest discover -s tests -v
```

- [ ] **Step 6: Update the persistent TODO and commit**

Mark the implemented P0 early-watch items complete, leaving the
next-open entry comparison open if it is not part of the delivered UI.

```bash
git add web/static/js/charts.js web/static/js/i18n.js tests/test_web_assets.py docs/modeling-todo.md
git commit -m "feat: display early reversal watch"
```
