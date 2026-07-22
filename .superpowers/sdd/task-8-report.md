# Task 8 Report: Forecast Service, Cache, and Stock API Integration

## Status

Implemented and verified.

## Implementation

- Added `ForecastService` in `web/services/forecasts.py`.
  - Builds the Task 5 point-in-time feature/target frame from the stock route's
    existing analysis snapshot; it performs no repository or universe reload.
  - Instantiates the Task 6 Ridge provider and serializes all 5/20/60-session
    result contracts.
  - Runs the Task 7 walk-forward evaluator for every supported horizon.
  - Returns `forecasts: {model, horizons, by_date}` plus horizon-indexed
    `forecast_evaluation` evidence.
  - Keeps `by_date` sparse by omitting dates on which every horizon is
    unavailable. Included dates retain each horizon's complete forecast
    contract, including typed per-horizon unavailable results.
  - Provides a stable `model_error` fallback with typed unavailable evaluation
    contracts when the provider/service boundary raises.
- Added a bounded LRU cache.
  - Keys include database revision, normalized ticker, exact first/last chart
    dates, and model version.
  - Returns deep copies so response consumers cannot mutate cached state.
  - Uses one re-entrant lock across cold computation/publication, preventing
    duplicate same-key fits and races with invalidation under threaded Flask.
  - `invalidate()` atomically advances the revision and clears prior entries.
- Integrated forecasts into `/api/stocks/<ticker>`.
  - Reuses `load_analysis_snapshot()` histories and the already-built chart
    dates, preserving the one-snapshot/no-extra-universe-query behavior.
  - Supports deterministic injection through Flask config `FORECAST_SERVICE`.
  - Stores the active service in `app.extensions["dashboard_forecast_service"]`.
  - Catches and logs model details server-side while returning the unchanged
    stock/factor/scenario payload plus safe typed forecast unavailability.
  - Preserves all legacy stock payload keys and values while adding only
    `forecasts` and `forecast_evaluation`.
- Added successful-update invalidation to `UpdateJobManager`.
  - A callback runs once after all active ticker writes succeed.
  - `completed` is not published until invalidation finishes, preventing a
    completed-status/stale-cache race.
  - Failed, partial, rate-limited, and zero-write runs do not invalidate.
  - Callback exceptions are logged without exposing details through job state.

## TDD Evidence

1. The initial API/update run failed with missing
   `web.services.forecasts` and an unsupported `on_success` constructor
   argument.
2. Forecast serialization, sparse date indexing, exact bounded cache reuse,
   deep-copy isolation, concurrent same-key requests, dependency injection,
   and API failure isolation passed after the minimal service/route changes.
3. A callback-order regression test observed `completed` while invalidation was
   still blocked. Moving terminal publication behind the callback made it pass.
4. A falsey injected provider factory was incorrectly replaced by the default
   Ridge factory. An explicit `is None` selection fixed the regression.
5. An injected forecast service without an `invalidate` method prevented app
   construction when the default update manager was used. Optional hook lookup
   preserved test/service injection while retaining production invalidation.

## Verification

- Focused API/update, warning-strict:
  `PYTHONWARNINGS=error ../../venv/bin/python -m unittest tests.test_web_api tests.test_web_update_jobs -v`
- Full suite, warning-strict:
  `PYTHONWARNINGS=error PYTHONPYCACHEPREFIX=/private/tmp/task8-full-pycache ../../venv/bin/python -m unittest discover -s tests -v`
- Bytecode compilation:
  `PYTHONWARNINGS=error PYTHONPYCACHEPREFIX=/private/tmp/task8-pycompile ../../venv/bin/python -m py_compile web/app.py web/services/forecasts.py web/services/update_jobs.py tests/test_web_api.py tests/test_web_update_jobs.py`
- `git diff --check`

The worktree does not contain its own `./venv`; verification uses the parent
repository interpreter at `../../venv/bin/python`.

## Self-Review

- Confirmed all Task 8 brief requirements have a production path and focused
  regression coverage.
- Confirmed cache failures are never published, cache hits are immutable to
  callers, and capacity eviction is deterministic LRU.
- Confirmed app-level provider exceptions cannot change the stock endpoint's
  HTTP status or leak exception details.
- Confirmed successful update invalidation is ordered before terminal status;
  failed, partial, and rate-limited paths retain the existing revision.
- Confirmed forecast computation consumes the route's existing snapshot and
  does not call `load_history()` or `load_universe_histories()`.
- No correctness blockers found. Cold real-model computation is intentionally
  substantial; Task 10 owns endpoint timing/profile verification, while Task 8
  ensures repeat requests are revision-cached and bounded.

---

## Review Fixes (2026-07-22)

### Status

Implemented and verified both Task 8 review findings.

### Findings Fixed

1. Exact cache-key contract
   - Removed `model_key` from the stored cache identity.
   - Cache keys are now exactly `(database_revision, ticker,
     first_chart_date, last_chart_date, model_version)`.
   - `model_version` is the validated immutable service value. The provider
     factory and `model_key` are fixed for the lifetime of each service-owned
     cache, and provider publication still requires an exact factory key and
     version match, so the version field is unambiguous in that namespace.

2. Invalidation failure state
   - A completion-hook/invalidation exception can no longer fall through to a
     `completed` publication.
   - The original exception is logged server-side, while the public snapshot
     is safely typed as `state="failed"`,
     `error="cache_invalidation_error"`, and `resumable=false`.
   - The callback still observes `running`; terminal publication occurs only
     after invalidation succeeds or its failure has been classified.
   - A subsequent start is a fresh job, refetches the complete active ticker
     set, and can complete after a successful invalidation.

### Strict TDD Evidence

1. Cache identity RED:
   `ForecastServiceTest.test_cache_key_is_exact_five_field_versioned_identity`
   failed with `AssertionError: 6 != 5` before production code changed.
2. Cache identity GREEN: the same test passed after removing only
   `self.model_key` from the tuple.
3. Invalidation RED:
   `UpdateJobManagerTest.test_invalidation_failure_is_typed_and_next_start_is_a_fresh_job`
   failed because the observed terminal state was `completed`, not `failed`.
4. Invalidation GREEN: the same test passed after adding the typed internal
   failure path. Existing blocking-order and successful-callback-once tests
   also remained green.

### Verification

- Focused API/update, warning-strict:
  `PYTHONWARNINGS=error PYTHONPYCACHEPREFIX=/private/tmp/task8-fix-focused ../../venv/bin/python -m unittest tests.test_web_api tests.test_web_update_jobs -v`
  - `Ran 42 tests` / `OK`.
- Full suite, warning-strict:
  `PYTHONWARNINGS=error PYTHONPYCACHEPREFIX=/private/tmp/task8-fix-full ../../venv/bin/python -m unittest discover -s tests -v`
  - `Ran 184 tests` / `OK`.
- Bytecode compilation:
  `PYTHONWARNINGS=error PYTHONPYCACHEPREFIX=/private/tmp/task8-fix-compile ../../venv/bin/python -m py_compile web/services/forecasts.py web/services/update_jobs.py tests/test_web_api.py tests/test_web_update_jobs.py`
  - Exit 0.
- `git diff --check`
  - Exit 0 with no output.

### Remaining Concerns

- No known correctness concern remains for these two findings.
- After an invalidation failure, price writes have already committed while the
  forecast cache intentionally remains stale and the job reports failure. A
  fresh retry repeats active-ticker price upserts before retrying invalidation;
  repository upserts are idempotent by design.
