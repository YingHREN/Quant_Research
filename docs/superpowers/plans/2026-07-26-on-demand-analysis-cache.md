# On-Demand Analysis Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the stock dashboard responsive by removing full-market structural recomputation from the universe endpoint, computing heavyweight analysis only for the selected stock, and caching revision-scoped lightweight results.

**Architecture:** Preserve the existing JSON endpoints. A new bounded `UniverseSnapshotService` owns revision-scoped universe payload caching, while `FactorRegistry` only evaluates peers for directional percentile factors and caches their raw values by database revision. Built-in directional factors use direct OHLCV calculations so peer ranking never triggers full chart/resistance construction.

**Tech Stack:** Python 3.9, Flask, pandas, bounded `OrderedDict`/`RLock`, `unittest`.

## Global Constraints

- Work in a dedicated worktree because another agent is implementing `feature/toprisk-001`.
- Do not modify or commit `data/prices.db-shm`, `data/prices.db-wal`, or `research/high_level_reversal_study.py`.
- Keep `/api/universe` and `/api/stocks/<ticker>` response fields backward compatible.
- Heavy structure diagnostics must remain available on the selected-stock endpoint.
- Cache keys must include database revision and algorithm version.
- Cached values must be copied before returning and bounded by configured capacity.
- Historical calculations may use only data available through the observation date.

---

### Task 1: Stop neutral and non-eligible factors from evaluating peers

**Files:**
- Modify: `web/factors/registry.py`
- Modify: `tests/test_web_factors.py`

**Interfaces:**
- Consumes: `FactorDefinition.direction` and optional `percentile_eligible`
- Produces: `FactorRegistry.evaluate_selected_with_peers(..., cache_namespace=None)`
- Guarantee: only `direction in {"higher", "lower"}` and `percentile_eligible=True` factors execute against peers

- [ ] **Step 1: Add a failing neutral-numeric peer test**

Create a counting neutral factor returning a finite number. Assert the selected ticker is evaluated once and peer tickers are never evaluated, while a directional factor still evaluates exact-date peers.

- [ ] **Step 2: Run the focused test and verify failure**

Run:

```bash
../../venv/bin/python -m unittest \
  tests.test_web_factors.FactorRegistryTest.test_neutral_numeric_factor_never_evaluates_peers
```

Expected: failure because the neutral finite factor currently evaluates every peer.

- [ ] **Step 3: Add the directional eligibility gate**

In `evaluate_selected_with_peers`, skip peer evaluation when:

```python
factor.direction not in {"higher", "lower"}
```

Keep selected-factor evaluation unchanged.

- [ ] **Step 4: Run factor and API tests**

```bash
../../venv/bin/python -m unittest tests.test_web_factors tests.test_web_api
```

- [ ] **Step 5: Commit**

```bash
git add web/factors/registry.py tests/test_web_factors.py
git commit -m "perf: skip neutral factor peer evaluation"
```

### Task 2: Replace heavyweight directional factor adapters

**Files:**
- Modify: `web/factors/builtin.py`
- Modify: `tests/test_web_factors.py`

**Interfaces:**
- Produces: direct point-in-time helpers for moving-average distance, pivot distance, and volume ratio
- Guarantee: peer evaluation of directional built-ins never calls `build_chart_rows`

- [ ] **Step 1: Add failing call-isolation tests**

Patch `web.factors.builtin.build_chart_rows` with a counting/raising function and evaluate these factors for peer contexts:

```text
close_vs_ema20_pct
close_vs_sma50_pct
close_vs_sma200_pct
volume_ratio
```

Assert valid numerical results are produced without invoking `build_chart_rows`.

- [ ] **Step 2: Run tests and verify the current adapters fail**

```bash
../../venv/bin/python -m unittest \
  tests.test_web_factors.BuiltinFactorTest.test_directional_peer_factors_do_not_build_chart_rows
```

- [ ] **Step 3: Implement direct OHLCV calculations**

Use `history_asof()` directly:

- EMA20: `close.ewm(span=20, adjust=False).mean().iloc[-1]`
- SMA50/SMA200: trailing rolling mean with the existing minimum-history semantics
- volume ratio: current volume divided by the prior/trailing 20-session average matching the chart contract

Keep selected-stock `build_chart_rows(context)` unchanged for chart payload construction.

- [ ] **Step 4: Run built-in factor, chart, and API tests**

```bash
../../venv/bin/python -m unittest \
  tests.test_web_factors tests.test_web_chart_contract tests.test_web_api
```

- [ ] **Step 5: Commit**

```bash
git add web/factors/builtin.py tests/test_web_factors.py
git commit -m "perf: compute peer factors without chart reconstruction"
```

### Task 3: Make the universe endpoint lightweight

**Files:**
- Modify: `web/app.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Changes: `UNIVERSE_FACTOR_KEYS`
- Keeps: existing universe ticker keys including shape fields
- Guarantee: universe construction does not call strict VCP, tight platform, pivot distance, resistance, reversal, or `build_chart_rows`

- [ ] **Step 1: Add a failing heavyweight-call test**

Build a registry whose structural factor callables raise if invoked. Request `/api/universe` and assert:

- HTTP 200;
- momentum and volatility fields remain populated when data is sufficient;
- shape fields are present but use the explicit unavailable/none state;
- no structural callable ran.

- [ ] **Step 2: Run the API test and verify failure**

```bash
../../venv/bin/python -m unittest \
  tests.test_web_api.WebApiTest.test_universe_never_computes_heavy_structures
```

- [ ] **Step 3: Restrict live universe factors**

Change the live universe factor set to:

```python
("mom_12_1", "realized_vol_63")
```

Preserve `strict_vcp`, `tight_platform`, `near_pivot`, and `shape_state` response keys with unavailable semantics rather than fabricating a negative signal.

- [ ] **Step 4: Run universe and frontend contract tests**

```bash
../../venv/bin/python -m unittest \
  tests.test_web_api tests.test_web_assets
```

- [ ] **Step 5: Commit**

```bash
git add web/app.py tests/test_web_api.py
git commit -m "perf: keep universe analysis lightweight"
```

### Task 4: Add revision-scoped bounded universe caching

**Files:**
- Create: `web/services/universe.py`
- Modify: `web/app.py`
- Create: `tests/test_web_universe_service.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Produces: `UniverseSnapshotService.build() -> dict`
- Constructor: `UniverseSnapshotService(repository, factor_registry, revision_getter, max_cache_size=4)`
- Cache key: `(database_revision, asof_date, "universe_summary_v2")`

- [ ] **Step 1: Add failing cache behavior tests**

Test that:

- two builds in one revision call expensive snapshot construction once;
- callers receive independent copies;
- changing revision produces a cache miss;
- cache size never exceeds the configured bound;
- a failed build is not cached.

- [ ] **Step 2: Run the new test and verify import failure**

```bash
../../venv/bin/python -m unittest tests.test_web_universe_service
```

- [ ] **Step 3: Implement the bounded service**

Use `OrderedDict`, `RLock`, and `deepcopy`. The lock covers a cache miss so identical concurrent requests cannot duplicate work. Construct the payload using repository freshness/summaries/history methods and a passed pure row-builder callback if needed to avoid importing Flask request state.

- [ ] **Step 4: Wire `/api/universe` to the service**

Register it in `flask_app.extensions["dashboard_universe_service"]`. Use the forecast/update database revision getter already used by market overview. Add `UNIVERSE_CACHE_SIZE` configuration with default `4`.

- [ ] **Step 5: Run service, API, update, and asset tests**

```bash
../../venv/bin/python -m unittest \
  tests.test_web_universe_service \
  tests.test_web_api \
  tests.test_web_update_jobs \
  tests.test_web_assets
```

- [ ] **Step 6: Commit**

```bash
git add web/services/universe.py web/app.py \
  tests/test_web_universe_service.py tests/test_web_api.py
git commit -m "perf: cache revision-scoped universe snapshots"
```

### Task 5: Add bounded revision-scoped peer factor caching

**Files:**
- Modify: `web/factors/registry.py`
- Modify: `web/app.py`
- Modify: `tests/test_web_factors.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Extends: `evaluate_selected_with_peers(..., cache_namespace=None)`
- Cache key: `(cache_namespace, factor.key, factor.version, ticker, observation_date)`
- Configuration: `FACTOR_PEER_CACHE_SIZE`, default `4096`

- [ ] **Step 1: Add failing peer cache tests**

Assert repeated selected-stock evaluations in the same namespace do not recompute peer raw values, a new namespace misses, returned `FactorResult` values cannot mutate the cache, and the cache remains bounded.

- [ ] **Step 2: Run focused tests and verify repeated calls**

```bash
../../venv/bin/python -m unittest \
  tests.test_web_factors.FactorRegistryTest.test_peer_factor_cache_is_revision_scoped_and_bounded
```

- [ ] **Step 3: Implement cache and namespace wiring**

Use a private `OrderedDict` and `RLock` in `FactorRegistry`. Cache only peer `FactorResult` objects for eligible directional factors. Pass `forecast_revision` from the stock endpoint as `cache_namespace`.

- [ ] **Step 4: Run factor and API tests**

```bash
../../venv/bin/python -m unittest tests.test_web_factors tests.test_web_api
```

- [ ] **Step 5: Commit**

```bash
git add web/factors/registry.py web/app.py \
  tests/test_web_factors.py tests/test_web_api.py
git commit -m "perf: cache revision-scoped peer factors"
```

### Task 6: Performance and full verification

**Files:**
- Create: `tests/test_web_performance_contract.py`
- Modify: `docs/modeling-todo.md`

**Interfaces:**
- Verifies: universe heavy-call count, selected chart-build call count, cache hit behavior

- [ ] **Step 1: Add deterministic operation-count regression tests**

Use synthetic repositories and counters rather than fragile wall-clock assertions. Assert universe heavy calls are zero and selected-stock full chart building is constant with respect to peer count.

- [ ] **Step 2: Run the complete test suite**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
../../venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

- [ ] **Step 3: Benchmark the real local database**

Measure cold and warm requests for `/api/universe` and `/api/stocks/NBIS`. Report exact timings; do not claim the target if the observed result misses it.

- [ ] **Step 4: Update the global TODO**

Record completed in-memory caching and leave persistent `analysis_cache.db` plus split frontend endpoints as future work.

- [ ] **Step 5: Commit**

```bash
git add tests/test_web_performance_contract.py docs/modeling-todo.md
git commit -m "test: protect on-demand analysis performance"
```
