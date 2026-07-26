# Historical VCP and Pocket Pivot Entry Signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce causal per-date Strict VCP, tight-platform, confirmed-breakout, and Pocket Pivot evidence, then expose it in the existing API, model panel, and chart without changing chart interaction.

**Architecture:** Pure research functions calculate evidence from OHLCV prefixes, while a bounded `EntrySignalService` caches complete selected-ticker histories by content fingerprint and algorithm version. Flask merges signal rows into existing chart rows by trading date. Model-output and chart renderers consume those fields without performing financial calculations.

**Tech Stack:** Python 3.9, pandas, Flask, vanilla JavaScript, Lightweight Charts, `unittest`, Node.js runtime tests.

## Global Constraints

- `research.vcp.detect_vcp` is the only canonical Strict VCP detector.
- Every historical output may use only the OHLCV prefix ending on its date.
- A breakout may use only a Pivot visible before the breakout date.
- Breakout confirmation requires volume ratio `>= 1.4` and price no more than `5%` above Pivot.
- Pocket Pivot uses the previous 10 complete sessions; no down days means inactive, never automatically active.
- Universe requests must not run entry-signal history.
- Annotations use trading dates only and must not affect autoscale, visible range, dragging, zooming, hover, or date lock.
- Cache identity includes complete indexed OHLCV content and `ENTRY_SIGNAL_VERSION`.
- Preserve injected services and safe API errors.
- Do not touch or commit `data/prices.db-wal`, `data/prices.db-shm`, generated cache databases, or unrelated research files.

---

### Task 1: Canonical Evidence Primitives

**Files:**
- Modify: `research/vcp.py`
- Modify: `factors/compute.py`
- Modify: `web/factors/builtin.py`
- Modify: `tests/test_vcp.py`
- Create: `tests/test_entry_signal_rules.py`
- Modify: `tests/test_web_factors.py`

**Interfaces:**
- Produces: `research.vcp.pattern_evidence(pattern: VCPPattern) -> dict`
- Produces: `factors.compute.pocket_pivot_evidence(hist: pd.DataFrame, lookback: int = 10) -> dict`
- Preserves: `factors.compute.pocket_pivot(...) -> bool`
- Changes: built-in `strict_vcp` factor to canonical `detect_vcp`

- [ ] **Step 1: Add failing canonical-adapter tests**

Add assertions that an accepted textbook pattern exposes dated contractions,
Pivot, base metrics, and no rejection, while a rejected pattern exposes the
typed canonical reason:

```python
evidence = pattern_evidence(detect_vcp(textbook_vcp_fixture()))
self.assertTrue(evidence["accepted"])
self.assertEqual(evidence["vcp_pivot"], evidence["pivot"])
self.assertTrue(evidence["contractions"])
self.assertIsNone(evidence["reject_reason"])
```

- [ ] **Step 2: Add failing Pocket Pivot evidence tests**

Cover insufficient history, a qualifying up-volume day, a non-qualifying
volume day, and a prior window with no down days:

```python
result = pocket_pivot_evidence(monotonic_up_history)
self.assertFalse(result["active"])
self.assertEqual(result["reject_reason"], "no_down_days_in_window")
self.assertEqual(result["down_day_count"], 0)
```

- [ ] **Step 3: Run red tests**

Run:

```bash
../../venv/bin/python -m unittest \
  tests.test_vcp \
  tests.test_entry_signal_rules \
  tests.test_web_factors
```

Expected: import or assertion failures because the new evidence functions and
canonical factor mapping do not exist.

- [ ] **Step 4: Implement `pattern_evidence`**

Return JSON-ready values with ISO dates and the existing factor-compatible
keys:

```python
{
    "accepted": pattern.accepted,
    "stage": pattern.stage,
    "vcp_pivot": pattern.pivot,
    "pivot": pattern.pivot,
    "pivot_date": iso_date_or_none,
    "base_start": iso_date_or_none,
    "base_end": iso_date_or_none,
    "contractions": [round(leg.depth_pct, 2) for leg in pattern.legs],
    "contraction_legs": [...],
    "n_contractions": len(pattern.legs),
    "pending_leg": ...,
    "reject_reason": pattern.reject_reason,
    **pattern.metrics,
}
```

- [ ] **Step 5: Implement `pocket_pivot_evidence` and compatibility wrapper**

Use the preceding 10 sessions, compute down days against each session’s actual
previous close, and return:

```python
{
    "available": bool,
    "active": bool,
    "lookback": 10,
    "current_volume": float_or_none,
    "prior_down_volume": float_or_none,
    "down_day_count": int,
    "reject_reason": reason_or_none,
}
```

Make `pocket_pivot` return only `evidence["active"]`.

- [ ] **Step 6: Switch the built-in Strict VCP factor**

Change `_vcp(context)` to call:

```python
pattern_evidence(detect_vcp(context.history_asof()))
```

Map canonical English rejection codes directly instead of translating through
the legacy Chinese-only mapping. Keep `tight_platform` unchanged.

- [ ] **Step 7: Run green tests and commit**

Run the Task 1 test command, then:

```bash
git add research/vcp.py factors/compute.py web/factors/builtin.py \
  tests/test_vcp.py tests/test_entry_signal_rules.py tests/test_web_factors.py
git commit -m "refactor: unify canonical vcp evidence"
```

---

### Task 2: Sequential Historical Entry Engine

**Files:**
- Create: `research/entry_signals.py`
- Create: `tests/test_entry_signals.py`
- Modify: `research/events.py`
- Modify: `tests/test_events.py`

**Interfaces:**
- Consumes: `detect_vcp`, `pattern_evidence`, `tight_platform`, `pocket_pivot_evidence`
- Produces: `ENTRY_SIGNAL_VERSION = "historical-entry-signals-v1"`
- Produces: `build_entry_signal_rows(history: pd.DataFrame) -> list[dict]`

- [ ] **Step 1: Write failing row-contract tests**

Assert exact one-row-per-date ordering and required field families:

```python
rows = build_entry_signal_rows(history)
self.assertEqual([row["time"] for row in rows], [
    timestamp.date().isoformat() for timestamp in history.index
])
self.assertTrue({
    "strict_vcp_active", "strict_vcp_start",
    "tight_platform_active", "vcp_breakout_confirmed",
    "pocket_pivot",
}.issubset(rows[-1]))
```

- [ ] **Step 2: Write failing causal event tests**

Patch `detect_vcp` with deterministic patterns and prove:

- first-seen emits once across repeated accepted days;
- same-day first detection cannot break out;
- a later crossing uses the frozen known Pivot;
- price-only, low-volume, and more-than-5%-extended crossings are not
  confirmed;
- event invalidation and 60-session expiry permit a later new event.

- [ ] **Step 3: Write failing prefix-invariance test**

Build rows for a prefix, append future bars, rebuild, and assert every original
row is deeply equal:

```python
self.assertEqual(
    build_entry_signal_rows(prefix),
    build_entry_signal_rows(full)[:len(prefix)],
)
```

- [ ] **Step 4: Run red tests**

Run:

```bash
../../venv/bin/python -m unittest \
  tests.test_entry_signals tests.test_events
```

Expected: import failure for `research.entry_signals`.

- [ ] **Step 5: Implement the sequential engine**

Validate sorted unique OHLCV input. For each position:

1. create the current prefix;
2. compute canonical Strict VCP evidence;
3. compute tight-platform evidence;
4. compute Pocket Pivot evidence;
5. evaluate a crossing against the previously frozen active Pivot;
6. record component confirmations and typed rejection;
7. update, invalidate, expire, or replace active event state only after the
   current breakout decision;
8. append a JSON-ready row.

Use prior-volume mean excluding the current bar. Store full Strict VCP and
tight-platform evidence under nested fields in addition to flattened UI
fields.

- [ ] **Step 6: Reuse shared constants in `research.events`**

Import breakout threshold, buy-zone threshold, and event lifetime from
`research.entry_signals` so event scanning and UI history cannot silently
diverge. Preserve existing `scan_ticker_events` output and tests.

- [ ] **Step 7: Run green tests and commit**

Run:

```bash
../../venv/bin/python -m unittest \
  tests.test_entry_signal_rules tests.test_entry_signals \
  tests.test_events tests.test_vcp
```

Then commit:

```bash
git add research/entry_signals.py research/events.py \
  tests/test_entry_signals.py tests/test_events.py
git commit -m "feat: build causal historical entry signals"
```

---

### Task 3: Bounded Cache and Flask API Integration

**Files:**
- Create: `web/services/entry_signals.py`
- Create: `tests/test_web_entry_signals.py`
- Modify: `web/app.py`
- Modify: `tests/test_web_api.py`
- Modify: `tests/test_web_performance_contract.py`

**Interfaces:**
- Produces: `EntrySignalService(max_cache_size: int = 16)`
- Produces: `EntrySignalService.build(ticker: str, history: pd.DataFrame) -> list[dict]`
- Produces: `merge_entry_signal_rows(chart: list[dict], signals: list[dict]) -> list[dict]`
- Configures: `ENTRY_SIGNAL_SERVICE` optional Flask injection

- [ ] **Step 1: Add failing cache behavior tests**

Patch `build_entry_signal_rows` and verify:

- identical ticker and indexed OHLCV is one calculation;
- appended data is a miss;
- same-length historical correction is a miss;
- different ticker is a miss;
- returned-row mutation does not alter cached rows;
- LRU size is bounded;
- invalid `max_cache_size` is rejected.

- [ ] **Step 2: Add failing Flask integration tests**

Inject a fake entry service and assert:

- `/api/stocks/AAA` calls it once and merges by exact ISO date;
- `/api/stocks/AAA/forecasts/<date>` receives the same evidence;
- `/api/universe` never calls it;
- explicit injected service is preserved in `app.extensions`;
- latest canonical Strict VCP structure agrees with the latest chart row.

- [ ] **Step 3: Run red tests**

Run:

```bash
../../venv/bin/python -m unittest \
  tests.test_web_entry_signals tests.test_web_api \
  tests.test_web_performance_contract
```

Expected: missing service/configuration assertions.

- [ ] **Step 4: Implement the cache service**

Fingerprint `history[["Open", "High", "Low", "Close", "Volume"]]` together
with its index using `pandas.util.hash_pandas_object`, then hash the resulting
bytes with BLAKE2b. Key the ordered cache by:

```python
(normalized_ticker, ENTRY_SIGNAL_VERSION, fingerprint)
```

Protect the LRU with `RLock` and return deep copies.

- [ ] **Step 5: Implement date merge and app factory wiring**

Create the default service only when `ENTRY_SIGNAL_SERVICE` is absent. Store
it as `dashboard_entry_signal_service`. In both stock routes:

```python
signals = entry_signal_service.build(normalized_ticker, history)
chart = merge_entry_signal_rows(build_chart_rows(context), signals)
```

Raise a typed internal consistency error if chart and signal date sets differ;
do not silently shift rows by position.

- [ ] **Step 6: Make structures consume canonical chart evidence**

Build `structures.strict_vcp`, latest Pivot, and all historical annotations
from merged chart rows. Keep tight-platform factor compatibility and remove
the latest-only Strict VCP fallback in `_attach_model_outputs`.

- [ ] **Step 7: Run green integration tests and commit**

Run the Task 3 test command, then:

```bash
git add web/services/entry_signals.py web/app.py \
  tests/test_web_entry_signals.py tests/test_web_api.py \
  tests/test_web_performance_contract.py
git commit -m "feat: expose cached historical entry signals"
```

---

### Task 4: Model Cards, Evidence, and Localization

**Files:**
- Modify: `web/forecasts/model_outputs.py`
- Modify: `tests/test_web_model_outputs.py`
- Modify: `web/static/js/model_outputs.js`
- Modify: `web/static/js/i18n.js`
- Modify: `tests/test_web_assets.py`

**Interfaces:**
- Extends: `bullish_structure` outputs with `vcp_breakout_confirmed_v1` and `pocket_pivot_v1`
- Extends: model output with `metrics: list[{"label_key", "value", "format"}]`
- Preserves: planned `demand_confirmation` as a separate model

- [ ] **Step 1: Write failing Python model-contract tests**

For computed chart rows assert:

- Strict VCP and tight platform are `active` or `inactive`, not unavailable;
- insufficient history is unavailable with `insufficient_history`;
- breakout and Pocket Pivot are production models;
- Pocket Pivot is not the planned demand-confirmation model;
- actual values and thresholds are present in metrics.

- [ ] **Step 2: Write failing Node rendering tests**

Render active, inactive, and insufficient-history cards in Chinese and
English. Assert concise names, status badges, actual/threshold values, and
localized rejection reasons. Assert the planned demand model still displays
“计划中 / Planned”.

- [ ] **Step 3: Run red tests**

Run:

```bash
../../venv/bin/python -m unittest \
  tests.test_web_model_outputs tests.test_web_assets
```

Expected: missing production entry outputs and localization keys.

- [ ] **Step 4: Implement Python outputs**

Change `_shape_state` to consume `*_active`, nested evidence, and typed reason.
Add `_vcp_breakout` and `_pocket_pivot` builders with `shape_state` or
`rule_event` kinds, `production` lifecycle, `close_confirmed` timing,
conditions, and metrics.

- [ ] **Step 5: Implement generic metric rendering**

Render each metric according to `format`:

- `number`;
- `percent`;
- `ratio`;
- `volume`;
- `date`;
- `text`.

Render `unavailable_reason` through a localized entry-reason map. Unknown
codes fall back to readable text without exposing internal paths.

- [ ] **Step 6: Add Chinese and English copy**

Add model names, explanations, limitations, evidence labels, condition labels,
and rejection reasons for Strict VCP, tight platform, VCP breakout, Pocket
Pivot, and the still-planned broader demand-confirmation model.

- [ ] **Step 7: Run green tests and commit**

Run the Task 4 test command, then:

```bash
git add web/forecasts/model_outputs.py web/static/js/model_outputs.js \
  web/static/js/i18n.js tests/test_web_model_outputs.py tests/test_web_assets.py
git commit -m "feat: explain historical entry model outputs"
```

---

### Task 5: Stable Chart Markers, Performance, and TODO Closure

**Files:**
- Modify: `web/static/js/charts.js`
- Modify: `tests/dashboard_runtime.mjs`
- Modify: `tests/test_web_assets.py`
- Modify: `tests/test_web_performance_contract.py`
- Modify: `docs/modeling-todo.md`

**Interfaces:**
- Consumes: `structures.annotations`
- Supports annotation types: `strict_vcp_start`, `vcp_breakout_confirmed`, `pocket_pivot`

- [ ] **Step 1: Write failing marker tests**

Supply multiple historical annotations and assert marker date, shape, color,
position, and localized text. Assert marker creation does not call:

- `setVisibleLogicalRange`;
- `fitContent`;
- `createPriceLine` for historical entry markers;
- any data-series method that adds future time points.

- [ ] **Step 2: Write failing interaction regression**

In `dashboard_runtime.mjs`, hover and lock a historical date with entry
markers, move right, scroll to details, drag, and change range. Assert the
locked date and logical range remain stable until explicit unlock.

- [ ] **Step 3: Implement marker style mapping**

Use one immutable map:

```javascript
{
  strict_vcp_start: { position: "aboveBar", shape: "diamond", color: COLORS.strictPivot },
  vcp_breakout_confirmed: { position: "belowBar", shape: "arrowUp", color: COLORS.up },
  pocket_pivot: { position: "belowBar", shape: "circle", color: COLORS.volumeMa20 },
}
```

Ignore unknown and non-trading-date annotations.

- [ ] **Step 4: Run frontend green tests**

Run:

```bash
../../venv/bin/python -m unittest tests.test_web_assets
```

- [ ] **Step 5: Benchmark real local histories**

Identify current MRVL and the longest local history. Record:

- raw cold `build_entry_signal_rows`;
- first `EntrySignalService.build`;
- second cache-hit build;
- warm `/api/stocks/<ticker>` request.

Add deterministic operation-count assertions to
`tests/test_web_performance_contract.py`. If longest cold time exceeds 5
seconds, optimize the prefix scanner or add a versioned per-ticker SQLite
artifact before proceeding.

- [ ] **Step 6: Update the global TODO**

Mark only verified `ENTRY-001` items complete. Record measured timings and any
remaining manual full-universe study as unchecked. Do not mark trading
performance claims complete without walk-forward evidence.

- [ ] **Step 7: Run focused and complete verification**

Run:

```bash
../../venv/bin/python -m unittest \
  tests.test_entry_signal_rules tests.test_entry_signals tests.test_events \
  tests.test_vcp tests.test_web_entry_signals tests.test_web_factors \
  tests.test_web_model_outputs tests.test_web_api tests.test_web_assets \
  tests.test_web_performance_contract

LOKY_MAX_CPU_COUNT=8 ../../venv/bin/python -m unittest discover -s tests
```

Then run `git diff --check` and compile every touched Python file with
`PYTHONPYCACHEPREFIX` under `/private/tmp`.

- [ ] **Step 8: Commit**

```bash
git add web/static/js/charts.js tests/dashboard_runtime.mjs \
  tests/test_web_assets.py tests/test_web_performance_contract.py \
  docs/modeling-todo.md
git commit -m "test: verify historical entry signal workflow"
```

After the final commit, rerun the complete suite on final HEAD before offering
local merge, pull request, or branch preservation.
