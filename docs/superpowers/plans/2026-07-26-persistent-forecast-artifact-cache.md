# Persistent Forecast Artifact Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist revision-wide forecast features and risk context in `data/analysis_cache.db` so a new service process can open an unchanged market snapshot without repeating the 50–60 second artifact build.

**Architecture:** A focused `ForecastArtifactStore` owns SQLite schema, serialization, checksum validation, bounded retention, and safe failure behavior. `ForecastService` computes a stable market-content signature, restores only exact-version artifacts, reconstructs and validates the provider from the cached frame, and writes a new artifact only after a complete successful build. Flask enables the store for the production default service; tests remain disk-free unless they explicitly inject a store.

**Tech Stack:** Python 3.9, SQLite, `pickle` protocol 5, `zlib`, pandas, Flask, `unittest`.

## Global Constraints

- Store cache data only in `data/analysis_cache.db`; never add tables to `data/prices.db`.
- Work only in the existing `perf/persistent-forecast-cache` worktree.
- Do not modify or commit `data/prices.db-shm`, `data/prices.db-wal`, or `research/high_level_reversal_study.py`.
- Cache identity must include market content, model version, feature version, risk-context version, and storage-format version.
- Corrupt, locked, read-only, incompatible, or missing cache state must fall back to correct in-memory computation.
- Never serialize or trust a runtime provider; reconstruct it from a validated cached feature frame.
- Custom provider/evaluator tests remain persistence-disabled unless a store is explicitly injected.
- Historical data corrections with unchanged row counts must miss the cache.
- Keep at most two persistent artifacts by default.
- No HTTP input may choose or modify the cache path.

---

### Task 1: SQLite Artifact Store

**Files:**
- Create: `web/services/forecast_artifacts.py`
- Create: `tests/test_web_forecast_artifacts.py`

**Interfaces:**
- Produces: `ForecastArtifact(frame, risk_context, evaluations, coverage, fingerprints)`
- Produces: `ForecastArtifactIdentity(model_key, model_version, feature_version, risk_context_version)`
- Produces: `ForecastArtifactStore(path, max_entries=2)`
- Produces: `ForecastArtifactStore.load(identity, market_signature) -> ForecastArtifact | None`
- Produces: `ForecastArtifactStore.save(identity, market_signature, artifact) -> bool`
- Produces: `ForecastArtifactStore.entry_count() -> int`

- [ ] **Step 1: Add failing schema and round-trip tests**

Create temporary SQLite paths and assert:

```python
store = ForecastArtifactStore(path, max_entries=2)
identity = ForecastArtifactIdentity("ridge_direction_v1", "v4", "ridge-features-v1", "risk-context-v1")
artifact = ForecastArtifact(frame, risk_context, evaluations, coverage, fingerprints)
self.assertTrue(store.save(identity, "market-a", artifact))
restored = store.load(identity, "market-a")
pd.testing.assert_frame_equal(restored.frame, frame)
pd.testing.assert_frame_equal(restored.risk_context, risk_context)
self.assertEqual(restored.evaluations, evaluations)
self.assertEqual(store.entry_count(), 1)
```

Also assert an unknown market signature returns `None`, the SQLite table contains the version columns from the design, callers receive independent objects, and saving three identities retains only two.

- [ ] **Step 2: Run the store tests and verify import failure**

Run:

```bash
../../venv/bin/python -m unittest tests.test_web_forecast_artifacts
```

Expected: `ModuleNotFoundError: web.services.forecast_artifacts`.

- [ ] **Step 3: Implement identity, artifact, schema, and safe serialization**

Use frozen dataclasses for identity and artifact metadata. Serialize this dictionary:

```python
{
    "frame": artifact.frame,
    "risk_context": artifact.risk_context,
    "evaluations": artifact.evaluations,
    "coverage": artifact.coverage,
    "fingerprints": artifact.fingerprints,
}
```

with `pickle.dumps(..., protocol=5)`, compress using `zlib.compress`, and checksum the compressed bytes with `blake2b(digest_size=32)`. Generate `cache_key` from a canonical JSON list containing market signature plus every identity and format version field. Use `sqlite3.connect`, `BEGIN IMMEDIATE`, `INSERT ... ON CONFLICT DO UPDATE`, and delete older rows after each successful save.

`load()` must verify row identity, codec, checksum, payload type, DataFrame types, coverage/fingerprint mappings, and return `None` on `sqlite3.Error`, checksum failure, decompression error, unpickle error, unknown format, or validation failure. A corrupt exact-key row should be deleted on a best-effort basis.

- [ ] **Step 4: Add corruption and write-failure tests**

Directly alter `payload_checksum`, truncate `payload`, and set an unknown `payload_codec`; each load must return `None`. Patch `sqlite3.connect` to raise on save and assert `False` rather than an exception. Assert a failed save does not remove an older valid row.

- [ ] **Step 5: Run store tests**

```bash
../../venv/bin/python -m unittest tests.test_web_forecast_artifacts
```

- [ ] **Step 6: Commit**

```bash
git add web/services/forecast_artifacts.py tests/test_web_forecast_artifacts.py
git commit -m "feat: add persistent forecast artifact store"
```

### Task 2: ForecastService Restore, Save, and Prewarm

**Files:**
- Modify: `web/services/forecasts.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Adds: `FORECAST_FEATURE_VERSION = "ridge-features-v1"`
- Adds: `FORECAST_RISK_CONTEXT_VERSION = "forecast-risk-context-v1"`
- Extends: `ForecastService(..., artifact_store=None)`
- Adds: `ForecastService.prewarm(histories, *, expected_revision=None) -> dict`
- Adds internal: `_market_signature(coverage, fingerprints) -> str`

- [ ] **Step 1: Add failing cross-process-equivalent restore test**

Use two distinct `ForecastService` instances with the same explicit temporary store and the default stable provider identity. Patch `build_feature_frame` and `build_forecast_risk_context`:

```python
first.build("AAA", chart_dates, histories)
second.build("AAA", chart_dates, histories)
self.assertEqual(feature_builder.call_count, 1)
self.assertEqual(risk_builder.call_count, 1)
```

Also assert the second service creates its own provider, produces the same response, and does not reuse the first runtime provider object.

- [ ] **Step 2: Add failing invalidation tests**

Assert each of these causes a new artifact build:

- append a new session;
- change a historical close without changing row count;
- change `model_version` through a stable fake factory identity;
- change `FORECAST_FEATURE_VERSION`;
- change `FORECAST_RISK_CONTEXT_VERSION`.

Assert the default unit-test service with no injected store creates no SQLite file.

- [ ] **Step 3: Implement market signature and restore path**

Build a canonical sorted sequence:

```python
[
    (
        ticker,
        row_count,
        None if last_date is None else last_date.isoformat(),
        None if fingerprint is None else fingerprint.hex(),
    )
    for ticker in sorted(coverage)
]
```

Hash it with BLAKE2. In `_revision_artifacts`, after current same-revision checks and before cold calculation, call the store with the current identity and market signature. On hit:

- reconstruct provider with `self._provider_factory(artifact.frame)`;
- call `_validate_provider_identity`;
- publish all artifact fields to the existing in-memory slots;
- return without calling feature/risk builders.

- [ ] **Step 4: Implement successful save and prewarm**

After a complete cold build and provider validation, call `store.save(...)`; ignore a `False` result because the current request already owns valid memory artifacts.

Implement:

```python
def prewarm(self, histories, *, expected_revision=None):
    coverage, fingerprints = _history_snapshot_metadata(histories)
    with self._lock:
        self._check_expected_revision(expected_revision)
        frame, _provider, evaluations, risk_context = self._revision_artifacts(
            histories, coverage, fingerprints
        )
        return {
            "database_revision": self._database_revision,
            "row_count": len(frame),
            "risk_row_count": len(risk_context),
            "evaluation_horizons": sorted(evaluations),
        }
```

Extract the duplicated expected-revision check into a private helper used by `build()` and `prewarm()`.

- [ ] **Step 5: Add prewarm and failure-isolation tests**

Assert prewarm writes the artifact without forecasting a ticker, a new service restores it, a store whose `save()` returns `False` does not change the live forecast response, and a store whose `load()` returns `None` triggers normal cold computation.

- [ ] **Step 6: Run forecast and API tests**

```bash
../../venv/bin/python -m unittest tests.test_web_forecast_artifacts tests.test_web_api
```

- [ ] **Step 7: Commit**

```bash
git add web/services/forecasts.py tests/test_web_api.py
git commit -m "perf: persist revision-wide forecast artifacts"
```

### Task 3: Flask Configuration and Maintenance Command

**Files:**
- Modify: `web/app.py`
- Create: `build_forecast_cache.py`
- Create: `tests/test_build_forecast_cache.py`
- Modify: `tests/test_web_api.py`
- Modify: `.gitignore`

**Interfaces:**
- Adds config: `FORECAST_ARTIFACT_CACHE_ENABLED`
- Adds config: `FORECAST_ARTIFACT_CACHE_PATH`
- Adds config: `FORECAST_ARTIFACT_CACHE_ENTRIES`
- Adds CLI: `python build_forecast_cache.py [--database PATH] [--cache PATH]`

- [ ] **Step 1: Add failing factory configuration tests**

Assert:

- production default creates a `ForecastArtifactStore` at `PROJECT_ROOT/data/analysis_cache.db`;
- `TESTING=True` without an explicit cache path creates a disk-free service;
- `FORECAST_ARTIFACT_CACHE_ENABLED=False` disables persistence;
- an explicitly injected `FORECAST_SERVICE` remains untouched.

- [ ] **Step 2: Wire default Flask service**

Create the artifact store before constructing the default `ForecastService`. Resolve paths only from trusted Flask configuration. Production defaults:

```python
FORECAST_ARTIFACT_CACHE_ENABLED=True
FORECAST_ARTIFACT_CACHE_PATH=PROJECT_ROOT / "data" / "analysis_cache.db"
FORECAST_ARTIFACT_CACHE_ENTRIES=2
```

When `TESTING=True`, enable persistence only if `FORECAST_ARTIFACT_CACHE_PATH` was explicitly supplied by the caller. Add `data/analysis_cache.db`, `data/analysis_cache.db-shm`, and `data/analysis_cache.db-wal` to `.gitignore`.

- [ ] **Step 3: Add failing CLI test**

Patch `MarketDataRepository`, `ForecastService.prewarm`, and assert the command loads the latest universe snapshot, calls prewarm exactly once, prints JSON containing cache path, as-of date, row counts, and elapsed seconds, and exits nonzero with a safe message on failure.

- [ ] **Step 4: Implement the prewarm CLI**

Use `argparse`; create `MarketDataRepository`, call `freshness()`, then `load_universe_histories(pd.Timestamp(latest_date))`, create the production artifact store and service, and call `prewarm`. Do not accept model identity or pickle codec from command-line input.

- [ ] **Step 5: Run configuration, CLI, API, and update tests**

```bash
../../venv/bin/python -m unittest \
  tests.test_build_forecast_cache \
  tests.test_web_api \
  tests.test_web_update_jobs
```

- [ ] **Step 6: Commit**

```bash
git add web/app.py build_forecast_cache.py .gitignore \
  tests/test_build_forecast_cache.py tests/test_web_api.py
git commit -m "feat: configure and prewarm forecast artifact cache"
```

### Task 4: Real Cache Build, Performance Verification, and Documentation

**Files:**
- Modify: `docs/modeling-todo.md`
- Modify: `tests/test_web_performance_contract.py`

**Interfaces:**
- Verifies: new-process persistent hit does not execute feature/risk builders
- Records: observed cache size, build time, INTC cold-process request time, and warm request time

- [ ] **Step 1: Add deterministic persistent-hit performance contract**

Use a temporary store and two services. Build with the first, then patch the feature and risk builders to raise while the second builds. Assert the second succeeds and the persistent store contains one artifact. This is an operation-count test, not a wall-clock threshold.

- [ ] **Step 2: Run the complete suite**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
../../venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

- [ ] **Step 3: Build the real cache**

Run:

```bash
../../venv/bin/python build_forecast_cache.py \
  --database /Users/renyinghao.1/Project/stock_screener/data/prices.db \
  --cache /Users/renyinghao.1/Project/stock_screener/data/analysis_cache.db
```

Do not add or commit the generated SQLite file.

- [ ] **Step 4: Benchmark a new process**

Create a new Flask app process against the real price and cache databases. Measure:

```text
/api/stocks/INTC first request in new process
/api/stocks/INTC second request in same process
```

Record exact status, response size, cache database size, and elapsed times. The target is at most 5 seconds for the first request and about 1 second for the second; report actual results if missed.

- [ ] **Step 5: Update the global TODO**

Mark only verified `PERF-002` implementation items complete. Keep automatic post-update warmup, cache-status UI, metrics, artifact splitting, batch prediction, and multi-process locking unchecked.

- [ ] **Step 6: Final verification and commit**

Run the complete suite again after documentation changes, then:

```bash
git add tests/test_web_performance_contract.py docs/modeling-todo.md
git commit -m "test: verify persistent forecast cache performance"
```
