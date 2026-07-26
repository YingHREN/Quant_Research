# Ten-Year Point-in-Time Price Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the local daily OHLCV foundation to a reproducible ten-year window without truncating existing history, while recording provider, adjustment mode, fetch time, source cutoff, revision, coverage, and basic quality evidence.

**Architecture:** Keep the existing `prices` table and read contracts stable. Add a focused daily-history persistence module that validates provider frames, performs non-destructive upserts, and writes append-only ingestion records plus current per-ticker coverage metadata. Expose explicit incremental and ten-year backfill modes from `build_local_db.py`; make cache keys period-aware so a short request cannot satisfy a long request.

**Tech Stack:** Python 3.9, pandas, SQLite, urllib/Tiingo, unittest.

## Global Constraints

- Existing `prices(ticker, date, open, high, low, close, volume)` readers remain backward compatible.
- A refresh may replace corrected rows but must never delete older rows that are absent from the provider response.
- The default research backfill window is 10 years; the acceptance floor is 8 years for securities old enough to have that history.
- Stored OHLCV is explicitly labelled `split_dividend_adjusted`; raw prices and corporate actions remain a separate DATA-001 milestone.
- All timestamps and source cutoffs are persisted so a dataset revision can be reproduced and audited.
- User-owned database WAL files and unrelated research files are not modified by source-control operations.

---

### Task 1: Period-aware provider requests

**Files:**
- Modify: `data/fetch.py`
- Test: `tests/test_data_fetch.py`

**Interfaces:**
- Produces: `_period_years(period: str) -> int`
- Produces: `_cache_path(ticker: str, period: str | None = None) -> str`
- Produces: `_fetch_tiingo(ticker: str, period: str) -> StockData`

- [ ] **Step 1: Write failing tests for parsing year windows and distinct cache paths**

```python
def test_period_cache_key_prevents_short_history_from_satisfying_ten_year_request():
    self.assertNotEqual(_cache_path("AMD", "1y"), _cache_path("AMD", "10y"))

def test_tiingo_period_parser_accepts_research_windows_and_rejects_invalid_values():
    self.assertEqual(_period_years("10y"), 10)
    with self.assertRaises(ValueError):
        _period_years("max")
```

- [ ] **Step 2: Run the focused test and confirm the missing interfaces fail**

Run: `../../venv/bin/python -m unittest tests.test_data_fetch -v`

Expected: failure because `_period_years` and the period-aware cache contract do not exist.

- [ ] **Step 3: Implement strict year parsing, period-aware cache names, and pass the requested years to Tiingo**

The cache filename must retain the final ISO date segment so the existing cache builder can still identify the newest file. `fetch()` keeps a one-year default for ordinary callers; the explicit backfill command requests ten years.

- [ ] **Step 4: Run the focused provider tests**

Run: `../../venv/bin/python -m unittest tests.test_data_fetch -v`

Expected: all tests pass.

### Task 2: Audited non-destructive daily-history persistence

**Files:**
- Create: `data/daily_history.py`
- Create: `tests/test_daily_history.py`

**Interfaces:**
- Produces: `history_start(asof: date, years: int = 10) -> date`
- Produces: `audit_history(frame: pd.DataFrame) -> DailyHistoryAudit`
- Produces: `persist_history(connection, ticker, frame, *, provider, adjustment, requested_start, fetched_at) -> PriceCoverage`
- Produces: `coverage_report(connection) -> list[PriceCoverage]`

- [ ] **Step 1: Write failing tests for leap-year window calculation, invalid bars, duplicates, gaps, suspicious adjusted returns, metadata, and preservation of older rows**

Use a temporary SQLite database with one old row, persist a shorter overlapping provider frame, and assert that the old row remains. Then persist a ten-year synthetic frame and assert that `price_ingestions` and `price_coverage` contain provider, adjustment, source cutoff, requested start, revision, row count, coverage years, and quality counts.

- [ ] **Step 2: Run the focused tests and confirm imports fail**

Run: `../../venv/bin/python -m unittest tests.test_daily_history -v`

Expected: failure because `data.daily_history` does not exist.

- [ ] **Step 3: Implement schema initialization, validation, deterministic revision hashing, non-destructive upsert, and metadata writes**

Reject non-finite or impossible OHLCV and duplicate dates atomically. Treat returns over 50% and calendar gaps over 10 days as auditable warnings, not automatic rejection. Compute coverage from the complete persisted ticker history after the upsert.

- [ ] **Step 4: Run the focused persistence tests**

Run: `../../venv/bin/python -m unittest tests.test_daily_history -v`

Expected: all tests pass.

### Task 3: Explicit ten-year backfill and coverage audit CLI

**Files:**
- Modify: `build_local_db.py`
- Modify: `tests/test_build_local_db.py`

**Interfaces:**
- Produces: `backfill(years: int = 10, tickers: Iterable[str] | None = None) -> BackfillSummary`
- Produces CLI: `build_local_db.py --backfill-years 10`
- Produces CLI: `build_local_db.py --coverage`

- [ ] **Step 1: Write failing tests for the ten-year request, non-truncating persistence integration, reference-series inclusion, and coverage summary**

Inject a fake fetcher so the test proves the command requests `10y`, uses the audited persistence path, and reports symbols below the eight-year acceptance floor without requiring network access.

- [ ] **Step 2: Run the focused CLI tests and confirm the new API is absent**

Run: `../../venv/bin/python -m unittest tests.test_build_local_db -v`

Expected: failure because `backfill` and the coverage report command do not exist.

- [ ] **Step 3: Implement the backfill loop and CLI**

Backfill all locally known active symbols plus reference tickers, persist every fetched row through `persist_history`, continue after per-symbol provider errors, stop cleanly on HTTP 429, and print successful, warning, failed, and below-floor totals. Keep `--update` as a one-year overlapping refresh using the same non-destructive persistence path.

- [ ] **Step 4: Run focused CLI and persistence tests**

Run: `../../venv/bin/python -m unittest tests.test_build_local_db tests.test_daily_history tests.test_data_fetch -v`

Expected: all tests pass.

### Task 4: Documentation, real backfill, and acceptance audit

**Files:**
- Modify: `docs/modeling-todo.md`
- Modify: `docs/dashboard.md`
- Generated local data only: `data/prices.db`

**Interfaces:**
- Consumes: `build_local_db.py --backfill-years 10`
- Consumes: `build_local_db.py --coverage`

- [ ] **Step 1: Document the backfill and audit commands plus adjustment semantics**

State that current daily bars are split/dividend adjusted, that ingestion time is not the same as original market availability, and that raw bars, corporate actions, delistings, and historical membership remain open DATA-001 work.

- [ ] **Step 2: Run all tests before touching the real database**

Run: `../../venv/bin/python -m unittest discover -s tests -q`

Expected: 0 failures.

- [ ] **Step 3: Execute the ten-year Tiingo backfill against the main local database**

Run from the main checkout after code integration:

```bash
source env.sh
unset FINNHUB_API_KEY ALPHAVANTAGE_API_KEY
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
  ./venv/bin/python build_local_db.py --backfill-years 10 --workers 4
```

- [ ] **Step 4: Audit the resulting coverage**

Run:

```bash
./venv/bin/python build_local_db.py --coverage
```

Verify total symbols, global min/max dates, symbols meeting the eight-year floor, short-history/IPO symbols, failed symbols, duplicate/invalid row counts, suspicious adjusted returns, and long gaps.

- [ ] **Step 5: Update the global TODO with exact completed and remaining DATA-001 items**

Mark only the ten-year adjusted OHLCV, provider/fetch/revision metadata, non-destructive refresh, and basic quality report as completed. Leave raw bars, corporate actions, code changes/delistings, historical membership, feature-level `available_at`, and post-expansion walk-forward experiments open.

- [ ] **Step 6: Run the complete verification suite and inspect the final diff**

Run:

```bash
../../venv/bin/python -m unittest discover -s tests -q
git diff --check
git status --short
```

Expected: all tests pass, no whitespace errors, and only DATA-001 source/tests/docs are tracked.
