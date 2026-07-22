# Task 8 Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the forecast cache key match its exact five-field contract and make cache-invalidation failures publish a safe failed update status instead of a stale completed status.

**Architecture:** Keep the forecast cache scoped to its immutable service/factory identity, using the factory's validated `model_version` as the fifth key field because `model_key` cannot vary during a service instance's lifetime. Let completion-hook failures propagate to the update state machine, where they are logged server-side and converted to the stable `failed` / `cache_invalidation_error` snapshot without retaining resumable ticker state.

**Tech Stack:** Python 3, `unittest`, Flask service layer, threads, `OrderedDict`.

## Global Constraints

- Strict TDD: every production change follows an observed failing regression.
- Cache keys are exactly `(database_revision, ticker, first_chart_date, last_chart_date, model_version)`.
- Invalidation failures never publish `completed` or leak exception details through the status API.
- A retry after invalidation failure starts a fresh job; successful jobs still invalidate once before completion.

---

### Task 1: Exact forecast cache identity

**Files:**
- Modify: `tests/test_web_api.py`
- Modify: `web/services/forecasts.py`

**Interfaces:**
- Consumes: `ForecastService.database_revision`, immutable factory `model_key` and `model_version`.
- Produces: five-element `OrderedDict` keys ending in the validated `ForecastService.model_version`.

- [x] **Step 1: Write the failing cache-key regression**

Extend the cache test to assert the sole cold-build key equals:

```python
(
    service.database_revision,
    "AAA",
    pd.Timestamp(self.chart_dates[0]),
    pd.Timestamp(self.chart_dates[-1]),
    service.model_version,
)
```

and explicitly assert its length is five.

- [x] **Step 2: Run the focused test and verify RED**

Run: `PYTHONWARNINGS=error PYTHONPYCACHEPREFIX=/private/tmp/task8-fix-red-cache ../../venv/bin/python -m unittest tests.test_web_api.ForecastServiceTest.test_cache_key_is_exact_five_field_versioned_identity -v`

Expected: FAIL because the current key has six fields and includes `model_key`.

- [x] **Step 3: Implement the minimal five-field key**

Remove `self.model_key` from `ForecastService.build()`'s tuple. Keep factory/provider identity validation unchanged so the model key is immutable within the service/cache namespace and the validated model version is the unambiguous varying identity.

- [x] **Step 4: Run the focused test and verify GREEN**

Run the Step 2 command again. Expected: PASS.

---

### Task 2: Failed invalidation state and fresh retry

**Files:**
- Modify: `tests/test_web_update_jobs.py`
- Modify: `web/services/update_jobs.py`

**Interfaces:**
- Consumes: `UpdateJobManager(on_success=...)`.
- Produces: snapshot `{state: "failed", error: "cache_invalidation_error", resumable: false}` when the hook raises; original exception appears only in server logs.

- [x] **Step 1: Write failing failure/order/retry regressions**

Add one test whose first callback raises a secret-bearing exception and whose second callback succeeds. Assert the first callback observes `running`, the first terminal snapshot is typed failed and redacted, no completed state is observed, and the second start refetches a fresh job and reaches completed. Retain the blocking callback test and successful callback-once test as ordering/success coverage.

- [x] **Step 2: Run focused update tests and verify RED**

Run: `PYTHONWARNINGS=error PYTHONPYCACHEPREFIX=/private/tmp/task8-fix-red-update ../../venv/bin/python -m unittest tests.test_web_update_jobs.UpdateJobManagerTest -v`

Expected: FAIL because `_notify_success()` currently swallows the exception and `_run()` publishes `completed`.

- [x] **Step 3: Implement typed callback failure handling**

Introduce an internal completion-hook exception carrying only the stable error code. Log the original exception in `_notify_success()`, re-raise the safe internal exception, and catch it separately in `_run()` to publish `failed` with `cache_invalidation_error` and `resumable=False`.

- [x] **Step 4: Run focused update tests and verify GREEN**

Run the Step 2 command again. Expected: PASS.

---

### Task 3: Verification, report, and commit

**Files:**
- Modify: `.superpowers/sdd/task-8-report.md`

**Interfaces:**
- Produces: warning-strict verification evidence and one review-fix commit.

- [x] **Step 1: Run focused API/update warning-strict tests**

Run: `PYTHONWARNINGS=error PYTHONPYCACHEPREFIX=/private/tmp/task8-fix-focused ../../venv/bin/python -m unittest tests.test_web_api tests.test_web_update_jobs -v`

- [x] **Step 2: Run the full warning-strict suite**

Run: `PYTHONWARNINGS=error PYTHONPYCACHEPREFIX=/private/tmp/task8-fix-full ../../venv/bin/python -m unittest discover -s tests -v`

- [x] **Step 3: Run compilation and diff checks**

Run: `PYTHONWARNINGS=error PYTHONPYCACHEPREFIX=/private/tmp/task8-fix-compile ../../venv/bin/python -m py_compile web/services/forecasts.py web/services/update_jobs.py tests/test_web_api.py tests/test_web_update_jobs.py`

Run: `git diff --check`

- [x] **Step 4: Append the Task 8 report**

Record both review findings, red/green evidence, final verification counts, and remaining concerns without replacing the original report.

- [x] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-07-22-task-8-review-fixes.md .superpowers/sdd/task-8-report.md tests/test_web_api.py tests/test_web_update_jobs.py web/services/forecasts.py web/services/update_jobs.py
git commit -m "fix: harden forecast cache invalidation"
```
