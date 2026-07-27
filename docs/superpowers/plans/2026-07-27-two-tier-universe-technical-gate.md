# Two-Tier Research Universe and Technical Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the dashboard to a two-tier active/research universe and add a causal, three-state CAN SLIM technical gate without increasing the full-prediction workload to every research ticker.

**Architecture:** A pure technical-gate module evaluates one bounded OHLCV window. A read-only research-universe repository selects point-in-time members and their latest 260 sessions from `research_prices.db`; `UniverseSnapshotService` merges those lightweight rows with the existing active universe. Research-only stock details use a bounded fallback snapshot and explicitly disable unsupported full-market outputs.

**Tech Stack:** Python 3.9, pandas, SQLite, Flask, vanilla JavaScript, HTML/CSS, `unittest`, Node runtime asset tests.

## Global Constraints

- Preserve `prices.db`, `research_prices.db`, and `delisted_research_prices.db`; every research-universe query is read-only.
- Use only data visible on or before the requested observation date.
- Research membership is `[effective_from, effective_to)`.
- A research ticker with a stale last bar is not evaluated at its private old date as though it were part of the current cross-section.
- Technical conditions return `pass`, `fail`, or `missing`; missing never passes.
- The technical gate is diagnostic and cannot claim a complete CAN SLIM candidate, a probability, or a buy signal.
- RS remains a separate model and filter; it is not silently folded into the four-condition gate.
- Do not run full historical VCP replay, full-market Ridge fitting, intraday subscriptions, or the main update queue for all research tickers.
- `/api/universe` cold build target is at most 5 seconds for the current 1,014-member research universe; cache hit target is at most 250 milliseconds.
- Existing active-universe API fields and behavior remain backward compatible.

---

## File Structure

- Create `research/canslim_technical.py`: pure point-in-time technical-gate evaluation and contracts.
- Create `tests/test_canslim_technical.py`: condition, missing-data, and causality tests.
- Create `web/services/research_universe.py`: read-only member/window repository and bounded research-only detail snapshots.
- Create `tests/test_web_research_universe.py`: SQLite membership, query-bound, failure, and snapshot tests.
- Modify `web/services/universe.py`: merge active and research rows, technical gates, pool counts, and cache revisions.
- Modify `tests/test_web_universe_service.py`: dual-pool service and graceful degradation tests.
- Modify `web/app.py`: service wiring and research-only detail fallback.
- Modify `web/forecasts/model_outputs.py`: diagnostic technical-gate output registration.
- Modify `tests/test_web_api.py` and `tests/test_web_model_outputs.py`: API and model-output contracts.
- Modify `web/static/js/universe.js`: pool/gate filtering, sorting, and row descriptions.
- Modify `web/static/js/app.js`: control state and pool summaries.
- Modify `web/static/js/i18n.js`: Chinese and English labels, reasons, and limitations.
- Modify `web/templates/index.html`: pool and technical-state controls.
- Modify `web/static/css/dashboard.css`: compact badges and details.
- Modify `tests/test_web_assets.py` and `tests/dashboard_runtime.mjs`: UI behavior and localization tests.
- Modify `docs/modeling-todo.md` only after real performance and read-only verification pass.

### Task 1: Pure CAN SLIM Technical Gate

**Files:**
- Create: `research/canslim_technical.py`
- Create: `tests/test_canslim_technical.py`

**Interfaces:**
- Consumes: one OHLCV `DataFrame` and one observation date.
- Produces:
  - `TECHNICAL_GATE_VERSION = "canslim_technical_gate_v1"`
  - `evaluate_technical_gate(history, asof, stale=False) -> dict`
  - `unavailable_technical_gate(asof, reason) -> dict`

- [ ] **Step 1: Write failing tests for a fully passing history**

Create a 260-business-day monotonic history and assert:

```python
result = evaluate_technical_gate(history, "2026-07-24")
self.assertEqual(result["state"], "pass")
self.assertEqual(result["passed_conditions"], 4)
self.assertEqual(result["condition_count"], 4)
self.assertGreater(result["values"]["ema10"], result["values"]["ema20"])
self.assertGreater(result["values"]["sma50"], 0)
self.assertGreaterEqual(result["values"]["distance_from_high_252"], -0.20)
self.assertTrue(result["preferred_within_15pct"])
self.assertEqual(result["version"], "canslim_technical_gate_v1")
```

- [ ] **Step 2: Run the focused test and verify failure**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python \
  -m unittest tests.test_canslim_technical -v
```

Expected: import failure because the module does not exist.

- [ ] **Step 3: Implement validated history and four conditions**

The result contains stable keys:

```python
{
    "state": "pass|fail|missing",
    "passed_conditions": 0,
    "condition_count": 4,
    "asof": "YYYY-MM-DD",
    "version": TECHNICAL_GATE_VERSION,
    "preferred_within_15pct": False,
    "values": {
        "close": None,
        "sma50": None,
        "ema10": None,
        "ema20": None,
        "ema10_slope_5": None,
        "ema20_slope_5": None,
        "high_close_252": None,
        "distance_from_high_252": None,
        "last_ema_cross_date": None,
        "last_ema_cross_direction": None,
    },
    "conditions": {
        "close_above_sma50": {
            "state": "pass|fail|missing",
            "actual": None,
            "threshold": 0.0,
            "reason": None,
        },
        "ema10_above_ema20": {},
        "moving_average_slopes_positive": {},
        "within_20pct_of_52_week_high": {},
    },
    "reason_codes": [],
}
```

Use `ewm(span=10|20, adjust=False, min_periods=10|20)`, a 50-session simple mean,
five-session EMA percentage changes, and exactly the most recent 252 valid closes
for the high. Validate a monotonic, unique `DatetimeIndex`; reject missing OHLCV
columns, non-finite values, and non-positive prices with stable missing reasons.

- [ ] **Step 4: Add red-green tests for three-state aggregation**

Test each condition failing independently, multiple failures, and a mixture of
passes plus one missing condition. Assert fail dominates missing and missing
dominates pass.

- [ ] **Step 5: Add causality, 251/252-session, cross-date, and stale tests**

Prove:

- 251 sessions produce `within_20pct_of_52_week_high=missing`.
- 252 sessions produce a real state.
- appending future prices does not change an old `asof` result.
- the reported EMA cross date is never after `asof`.
- `stale=True` makes the aggregate state missing with `stale_observation`.
- NaN, duplicate dates, non-monotonic dates, and non-positive close return stable
  missing payloads rather than partial passes.

- [ ] **Step 6: Run focused tests and commit**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python \
  -m unittest tests.test_canslim_technical -v
git add research/canslim_technical.py tests/test_canslim_technical.py
git commit -m "research: add CAN SLIM technical gate"
```

### Task 2: Read-Only Point-in-Time Research Universe Repository

**Files:**
- Create: `web/services/research_universe.py`
- Create: `tests/test_web_research_universe.py`

**Interfaces:**
- Produces:
  - `ResearchUniverseRepository(database_path)`
  - `revision() -> int | None`
  - `snapshot(asof=None, sessions=260) -> ResearchUniverseSnapshot`
  - `load_detail_snapshot(ticker, asof=None) -> ResearchDetailSnapshot`
- `ResearchUniverseSnapshot` contains `status`, `asof`, `revision`,
  `members`, `histories`, and `reason`.
- `ResearchDetailSnapshot` contains the selected ticker plus only `SPY`, `QQQ`,
  and supplied benchmark tickers.

- [ ] **Step 1: Write a failing temporary-SQLite membership test**

Build `daily_prices`, `universe_memberships`, `relative_strength_snapshots`, and
`sector_classifications` fixtures. Assert a member with
`effective_from <= asof < effective_to` is included, while a future member and a
member ending exactly on `asof` are excluded.

- [ ] **Step 2: Run the focused test and verify failure**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python \
  -m unittest tests.test_web_research_universe -v
```

Expected: import failure.

- [ ] **Step 3: Implement one read-only snapshot transaction**

Open SQLite with `mode=ro`, determine the latest available common `asof`, and
select active memberships with a bounded, index-backed query.

```sql
prices.rowid IN (
  SELECT candidate.rowid
  FROM daily_prices AS candidate
  WHERE candidate.ticker = eligible.ticker
    AND candidate.date <= ?
  ORDER BY candidate.date DESC
  LIMIT ?
)
```

Return at most 260 rows per ticker, reordered ascending by date. Execute a
constant number of SQL statements: metadata, membership/window prices, and
optional security metadata. Never loop one query per ticker.

Implementation note (2026-07-27): the initial `ROW_NUMBER()` form scanned the
2.35-million-row price table and missed the five-second cold-build target. The
final single-statement correlated rowid query performs one indexed bounded seek
per eligible ticker inside SQLite while preserving constant application-level
query count. On the real 1,014-member database it reduced the research snapshot
from about 6.44 seconds to 1.27 seconds.

- [ ] **Step 4: Add tests for bounded rows and synchronized observation dates**

Assert no ticker returns more than 260 sessions; histories ending before the
common `asof` are marked stale; a stale ticker remains present but cannot receive
a passing gate.

- [ ] **Step 5: Implement bounded detail snapshots**

Validate ticker syntax. Confirm membership at `asof`; then query only the selected
ticker and explicit benchmark list. Unknown or out-of-period tickers raise
`UnknownResearchTicker`. Test that an injected SQL trace never reads unrelated
ticker prices.

- [ ] **Step 6: Add unavailable-schema and query-count tests**

Missing file, locked file, absent tables, duplicate dates, and malformed numeric
rows return an unavailable snapshot or a typed repository error. Assert the
snapshot SQL statement count remains constant when fixture membership grows from
2 to 200 tickers.

- [ ] **Step 7: Run focused tests and commit**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python \
  -m unittest tests.test_web_research_universe -v
git add web/services/research_universe.py tests/test_web_research_universe.py
git commit -m "web: add read-only research universe repository"
```

### Task 3: Merge Active and Research Pools in `/api/universe`

**Files:**
- Modify: `web/services/universe.py`
- Modify: `web/app.py`
- Modify: `tests/test_web_universe_service.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- `UniverseSnapshotService(..., research_universe_repository=None,
  technical_gate_evaluator=evaluate_technical_gate)`
- `/api/universe` adds `pool_summary` and `research_pool_status`.
- Each row adds `pool_membership` and `technical_gate`.

- [ ] **Step 1: Write failing service tests for two-pool merging**

Use an active fixture `AAA, SPY` and a research fixture `AAA, BBB, SPY`. Assert:

```python
self.assertEqual(
    payload["pool_summary"],
    {"active_count": 2, "research_count": 3, "overlap_count": 2},
)
self.assertEqual(by_ticker["AAA"]["pool_membership"], {
    "active": True, "research": True,
})
self.assertEqual(by_ticker["BBB"]["pool_membership"], {
    "active": False, "research": True,
})
```

- [ ] **Step 2: Run focused tests and verify failure**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python \
  -m unittest tests.test_web_universe_service -v
```

- [ ] **Step 3: Merge lightweight research rows**

Build existing active rows unchanged. Evaluate technical gates for research
histories, create lightweight rows for research-only tickers, then merge by ticker
in sorted order. Research-only rows set full-history shape fields to unavailable;
they must not call `detect_vcp()` or `tight_platform()`.

- [ ] **Step 4: Merge RS and classifications across the final ticker list**

Call existing metadata services after pool merging, so new research-only rows
receive their precomputed RS and sector data. Preserve explicit missing values.

- [ ] **Step 5: Expand cache identity and degradation behavior**

Add research revision, research `asof`, and technical-gate version to the cache
key. If the research repository returns unavailable or raises an expected typed
error, return active rows and:

```python
"research_pool_status": {
    "status": "unavailable",
    "reason": "research_database_unavailable",
}
```

Test that the failed research snapshot is not cached over a later successful one.

- [ ] **Step 6: Wire the repository in `create_app`**

Construct `ResearchUniverseRepository(RESEARCH_DATABASE)` unless an injected
`RESEARCH_UNIVERSE_REPOSITORY` exists. Preserve injected test services and the
existing repository as the active pool.

- [ ] **Step 7: Add API compatibility and no-heavy-replay tests**

Assert old top-level fields remain, new fields are JSON-safe, and mocks of
`detect_vcp`, `tight_platform`, and `ForecastService.build` are never called for
research-only universe rows.

- [ ] **Step 8: Run tests and commit**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python \
  -m unittest tests.test_web_universe_service tests.test_web_api -v
git add web/services/universe.py web/app.py \
  tests/test_web_universe_service.py tests/test_web_api.py
git commit -m "web: expose two-tier research universe"
```

### Task 4: Research-Only Stock Detail and Diagnostic Model Output

**Files:**
- Modify: `web/app.py`
- Modify: `web/forecasts/model_outputs.py`
- Modify: `tests/test_web_api.py`
- Modify: `tests/test_web_model_outputs.py`

**Interfaces:**
- Research-only stock payload adds `data_scope`, `unsupported_outputs`, and
  `technical_gate`.
- Model outputs add `canslim_technical_gate` as a diagnostic research output.

- [ ] **Step 1: Write a failing API fallback test**

Make the active repository raise `UnknownTicker("BBB")` and the research
repository return `BBB`, `SPY`, and `QQQ`. Assert HTTP 200, `ticker="BBB"`,
`data_scope="research_only"`, and stable unsupported output keys.

- [ ] **Step 2: Extract active and research payload builders**

Keep the existing active path behavior byte-compatible. Implement a bounded
research path that builds chart rows, technical gate, precomputed RS,
classification, and lightweight factors from the selected/benchmark histories.
It returns unavailable forecast bundles and does not call full-market Ridge,
entry-signal prewarm, update manager writes, or intraday subscription methods.

- [ ] **Step 3: Register the diagnostic technical-gate model output**

Add a stable output:

```python
{
    "key": "canslim_technical_gate",
    "model_type": "rule_gate",
    "lifecycle": "research",
    "decision_permission": "diagnostic",
    "state": technical_gate["state"],
    "score": technical_gate["passed_conditions"],
    "score_denominator": 4,
}
```

The limitation says the gate excludes fundamentals, 13F, and the complete market
state. Missing output remains missing rather than zero.

- [ ] **Step 4: Test no accidental write or forecast expansion**

Assert selecting research-only `BBB` does not invoke `upsert_history`,
`UpdateJobManager.start`, `ForecastService.build`, or a full-universe history
load. Invalid and nonmember tickers retain 400/404 behavior.

- [ ] **Step 5: Run tests and commit**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python \
  -m unittest tests.test_web_api tests.test_web_model_outputs -v
git add web/app.py web/forecasts/model_outputs.py \
  tests/test_web_api.py tests/test_web_model_outputs.py
git commit -m "web: add research-only technical stock details"
```

### Task 5: Pool and Technical-Gate UI

**Files:**
- Modify: `web/templates/index.html`
- Modify: `web/static/js/universe.js`
- Modify: `web/static/js/app.js`
- Modify: `web/static/js/i18n.js`
- Modify: `web/static/css/dashboard.css`
- Modify: `tests/test_web_assets.py`
- Modify: `tests/dashboard_runtime.mjs`

**Interfaces:**
- Filters: `poolType=all|active|research`,
  `technicalState=all|pass|fail|missing`.
- Sort key: `technical_passed_conditions`.

- [ ] **Step 1: Write failing Node-backed filter tests**

Create active-only, research-only, overlap, pass, fail, and missing rows. Assert
pool filters compose with RS80/RS90, sector, freshness, and text search. Missing
must not match pass or fail.

- [ ] **Step 2: Add accessible controls and state persistence**

Add two selects with explicit labels:

- 股票池范围：全部 / 活跃池 / 研究池
- 技术门控：全部 / 通过 / 失败 / 数据缺失

Wire them to the existing store filter object and render loop. Preserve filters
during locale changes and universe refreshes.

- [ ] **Step 3: Render concise row badges and detail explanations**

Each row displays pool membership and one technical state badge. The selected
security header shows `通过 N/4`; a details block lists actual value, threshold,
observation date, EMA cross date, and reason for all four conditions.

- [ ] **Step 4: Add Chinese and English localization**

Add keys for pool names, gate states, four conditions, missing reason codes,
diagnostic limitation, research-only scope, and unsupported outputs. Tests assert
every referenced key exists in both locales and no raw reason code reaches UI.

- [ ] **Step 5: Add stable layout and accessibility tests**

Verify controls have labels, badges use text plus color, empty states are
localized, and toggling filters does not change chart dimensions or trigger a
stock request.

- [ ] **Step 6: Run UI tests and commit**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python \
  -m unittest tests.test_web_assets -v
git add web/templates/index.html web/static/js/universe.js \
  web/static/js/app.js web/static/js/i18n.js web/static/css/dashboard.css \
  tests/test_web_assets.py tests/dashboard_runtime.mjs
git commit -m "web: add research pool technical gate controls"
```

### Task 6: Real Data, Performance, Documentation, and Merge Gate

**Files:**
- Modify: `docs/modeling-todo.md`
- Create: `reports/two-tier-universe-technical-gate.json`
- Create: `reports/two-tier-universe-technical-gate.md`
- Review all Task 1–5 files.

**Interfaces:**
- Produces immutable counts, performance measurements, examples, and database
  hashes for the real 1,014-member research universe.

- [ ] **Step 1: Run focused and full regression tests**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python \
  -m unittest tests.test_canslim_technical \
  tests.test_web_research_universe \
  tests.test_web_universe_service \
  tests.test_web_api \
  tests.test_web_model_outputs \
  tests.test_web_assets -v
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python \
  -m unittest discover -s tests -q
```

- [ ] **Step 2: Record pre-run protected database hashes**

```bash
shasum -a 256 data/prices.db data/research_prices.db \
  data/delisted_research_prices.db
```

- [ ] **Step 3: Benchmark real cold and hot universe builds**

Instantiate the real Flask app without starting a second server. Time one cold
`UniverseSnapshotService.build()` and ten cache hits. Record:

- active, research, and overlap counts
- pass, fail, and missing counts
- technical pass combined with RS80 and RS90
- cold elapsed seconds
- median and maximum hot elapsed milliseconds
- SQL statement count

Fail the gate if cold exceeds 5 seconds, median hot exceeds 250 milliseconds, or
the SQL count grows per ticker.

- [ ] **Step 4: Audit real example tickers**

Record technical values and reasons for NBIS, MU, AMD, MRVL and at least five
research-only tickers selected deterministically by ticker hash. Do not hand-pick
only passing examples.

- [ ] **Step 5: Render deterministic JSON and Chinese Markdown reports**

Reports contain algorithm version, observation date, counts, timings, selected
examples, limitations, and protected database hashes. They contain no raw API
credentials or authenticated URLs.

- [ ] **Step 6: Verify non-mutation and repository hygiene**

```bash
shasum -a 256 data/prices.db data/research_prices.db \
  data/delisted_research_prices.db
rg -n 'api_token=|EODHD_API_TOKEN.{0,20}[A-Za-z0-9]{12,}|PK[A-Z0-9]{20,}' \
  reports/two-tier-universe-technical-gate.* research web tests
git diff --check
```

Hashes must match Step 2. Ignore only known fake-token security fixtures.

- [ ] **Step 7: Update the global Chinese TODO**

Mark only the technical-gate and two-tier expansion subtasks complete. Keep
fundamental, 13F, complete market-state, and strict four-way AND candidate tasks
open.

- [ ] **Step 8: Commit evidence**

```bash
git add reports/two-tier-universe-technical-gate.json \
  reports/two-tier-universe-technical-gate.md docs/modeling-todo.md
git commit -m "data: validate two-tier technical universe"
```

- [ ] **Step 9: Re-run the full suite before integration**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python \
  -m unittest discover -s tests -q
```

Expected: zero failures. Only then merge locally to `main`, preserving
user-owned untracked files and the independent SEC download cache.
