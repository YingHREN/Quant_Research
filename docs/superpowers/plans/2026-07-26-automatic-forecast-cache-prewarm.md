# Automatic Forecast Cache Prewarm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically rebuild the two active persistent forecast cohorts after a price-update run writes data, without making cache-warmup failure invalidate a successful market-data update.

**Architecture:** A reusable `ForecastCacheWarmer` selects active cohort dates and invokes `ForecastService.prewarm` oldest-first. `UpdateJobManager` receives a separate best-effort warmup callback and exposes its lifecycle independently from the price-update terminal state. Flask wires the warmer only for compatible default services, and the maintenance CLI reuses the same component.

**Tech Stack:** Python 3.9, pandas, Flask, threading, `unittest`.

## Global Constraints

- Work only in `perf/automatic-forecast-prewarm`.
- Preserve existing `on_success` invalidation failure semantics.
- Warmup failure must not change the price update terminal state or resumability.
- Zero-write runs must not invalidate or warm.
- Cohorts are active summary dates only, maximum two, invoked oldest-first.
- Keep the worker in `running` state until warmup finishes.
- Do not touch or commit local price WAL/SHM files or generated cache databases.

---

### Task 1: Reusable ForecastCacheWarmer

**Files:**
- Create: `web/services/forecast_warmup.py`
- Create: `tests/test_web_forecast_warmup.py`
- Modify: `build_forecast_cache.py`
- Modify: `tests/test_build_forecast_cache.py`

**Interfaces:**
- Produces: `ForecastCacheWarmer(repository, forecast_service, max_cohorts=2)`
- Produces: `ForecastCacheWarmer() -> {"state": "ready", "cohorts": [...], ...}`

- [ ] Add a failing test asserting inactive dates are excluded, the two newest active dates are selected, and calls occur oldest-first.
- [ ] Run `../../venv/bin/python -m unittest tests.test_web_forecast_warmup` and verify import failure.
- [ ] Implement the warmer with validated constructor inputs, safe JSON-ready output, and per-cohort `load_universe_histories(pd.Timestamp(date))`.
- [ ] Refactor `build_forecast_cache.py` to instantiate and call the warmer; preserve its existing JSON fields and safe error behavior.
- [ ] Run `tests.test_web_forecast_warmup tests.test_build_forecast_cache`.
- [ ] Commit with `feat: add reusable forecast cache warmer`.

### Task 2: UpdateJobManager Warmup Lifecycle

**Files:**
- Modify: `web/services/update_jobs.py`
- Modify: `tests/test_web_update_jobs.py`

**Interfaces:**
- Extends: `UpdateJobManager(..., on_cache_warmup=None)`
- Extends: `JobSnapshot` with warmup state, error, timestamps, and cohort dates.

- [ ] Add failing tests for callback order, running-state visibility during warmup, successful summary publication, zero-write skip, warmup-failure isolation, and invalidation-failure short circuit.
- [ ] Run focused update tests and verify constructor/schema failures.
- [ ] Implement reset-on-start warmup fields and execute best-effort warmup after successful invalidation but before `_finish_locked`.
- [ ] Preserve `completed`, `partial`, `rate_limited`, and resumable values when warmup fails.
- [ ] Run all update-job tests.
- [ ] Commit with `feat: track automatic forecast cache warmup`.

### Task 3: Flask Wiring and API Contract

**Files:**
- Modify: `web/app.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Default manager receives `ForecastCacheWarmer(repository, forecast_service)` only when `forecast_service.prewarm` is callable.
- Injected update managers remain unchanged.

- [ ] Add a failing factory test that captures default manager callbacks and verifies warmup is wired for the default ForecastService but omitted for incompatible injected services.
- [ ] Wire the warmer without changing explicit manager injection.
- [ ] Update API snapshot expectations for the new warmup fields.
- [ ] Run `tests.test_web_api tests.test_web_update_jobs`.
- [ ] Commit with `feat: prewarm forecasts after price updates`.

### Task 4: Verification and TODO

**Files:**
- Modify: `docs/modeling-todo.md`
- Modify: `tests/test_web_performance_contract.py`

- [ ] Add a deterministic contract proving update callback order is invalidate then warm and that a second service restores the resulting artifact without feature/risk builders.
- [ ] Run the complete test suite.
- [ ] Mark verified `PERF-003` items complete and keep cache-status UI and cross-process locking future work unchecked.
- [ ] Run `git diff --check`, compile touched Python files, and rerun the complete suite on final HEAD.
- [ ] Commit with `test: verify automatic forecast cache prewarm`.
