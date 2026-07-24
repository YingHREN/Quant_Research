# Reference-First Market Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fetch fixed market and sector reference tickers before ordinary active stocks during a full price update.

**Architecture:** Keep `UpdateJobManager` as the sole owner of update ordering. Change only the construction of a new run's deterministic work list; resumed runs continue using their existing remaining list, and all provider, persistence, error, and cache behavior stays unchanged.

**Tech Stack:** Python 3, `unittest`, Flask local development server, SQLite price repository.

## Global Constraints

- The update universe remains the union of fixed references and locally active stocks.
- Stable deduplication fetches a symbol once, at its first reference-priority position.
- No new API route, UI button, provider method, or automatic page-triggered update.
- Rate-limit, partial-failure, progress, cache-invalidation, and resume behavior must remain unchanged.
- Preserve the user's untracked database WAL/SHM files and `research/high_level_reversal_study.py`.

---

### Task 1: Prioritize fixed reference tickers

**Files:**
- Modify: `tests/test_web_update_jobs.py:126-148`
- Modify: `web/services/update_jobs.py:229-245`

**Interfaces:**
- Consumes: `UpdateJobManager(..., reference_tickers: Iterable[str])`
- Produces: a deterministic `_remaining_tickers` list ordered as references first, then non-duplicate active tickers

- [ ] **Step 1: Write the failing ordering and deduplication test**

Change the existing reference test so one reference is also locally active:

```python
def test_reference_tickers_are_prioritized_and_deduplicated(self):
    repository = FakeRepository(("AMD", "QQQ"))
    provider = FakeProvider(
        {
            "AMD": history(10),
            "QQQ": history(20),
            "SOXX": history(30),
        }
    )
    manager = UpdateJobManager(
        repository,
        provider,
        reference_tickers=("QQQ", "SOXX"),
    )

    snapshot = manager.run_synchronously_for_test()

    self.assertEqual(snapshot.state, "completed")
    self.assertEqual(provider.calls, ["QQQ", "SOXX", "AMD"])
    self.assertEqual(snapshot.total, 3)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_update_jobs.UpdateJobManagerTest.test_reference_tickers_are_prioritized_and_deduplicated \
  -v
```

Expected: FAIL because the current implementation calls `AMD` before the
reference tickers.

- [ ] **Step 3: Implement the minimal reference-first ordering**

In `_load_tickers_if_needed`, replace the work-list construction with:

```python
ordered_tickers = tuple(
    dict.fromkeys((*self._reference_tickers, *active_tickers))
)
```

Do not change resumed-run state or any code in `_run`.

- [ ] **Step 4: Verify focused and update-manager tests GREEN**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_update_jobs.UpdateJobManagerTest.test_reference_tickers_are_prioritized_and_deduplicated \
  tests.test_web_update_jobs.UpdateJobManagerTest.test_completed_job_updates_active_tickers_only \
  -v
./venv/bin/python -m unittest tests.test_web_update_jobs -v
```

Expected: all selected tests pass.

- [ ] **Step 5: Run full verification**

Run:

```bash
PYTHONWARNINGS=error \
PYTHONPYCACHEPREFIX=/private/tmp/reference-first-update-pycache \
./venv/bin/python -m unittest discover -s tests -v
git diff --check
```

Expected: all tests pass and `git diff --check` prints nothing.

- [ ] **Step 6: Commit the behavior change**

```bash
git add web/services/update_jobs.py tests/test_web_update_jobs.py
git commit -m "fix: prioritize market reference updates"
```

### Task 2: Roll out and validate the local updater

**Files:**
- No source changes

**Interfaces:**
- Consumes: `POST /api/update`, `GET /api/update/status`
- Produces: a running local update whose first current/completed symbols come from `REFERENCE_TICKERS`

- [ ] **Step 1: Stop the old local server and start the committed main version**

Resolve the exact listener on `127.0.0.1:5000`, stop only that process, then run:

```bash
./venv/bin/python web/app.py
```

Expected: Flask listens on `http://127.0.0.1:5000`.

- [ ] **Step 2: Start a fresh update and inspect early progress**

Run:

```bash
curl -sS -X POST http://127.0.0.1:5000/api/update
curl -sS http://127.0.0.1:5000/api/update/status
```

Expected: `state` is `running`; the first `current_ticker` belongs to the fixed
reference pool rather than the alphabetical active-stock list.

- [ ] **Step 3: Verify the market page and API remain available**

Run:

```bash
curl -sS -o /private/tmp/reference-first-market.html \
  -w '%{http_code}\n' http://127.0.0.1:5000/market
curl -sS -o /private/tmp/reference-first-market.json \
  -w '%{http_code}\n' \
  'http://127.0.0.1:5000/api/market-overview?horizon=5&sector=semiconductor'
```

Expected: both commands print `200`.
