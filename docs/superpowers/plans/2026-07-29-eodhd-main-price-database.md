# EODHD Main Price Database Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the existing main price database atomically from audited EODHD adjusted OHLCV while preserving its public SQLite schema.

**Architecture:** Add a focused conversion module that reads the current segment from `research_prices.db`, validates each target ticker with the existing daily-history audit, writes coverage metadata into a temporary main-format database, audits the temporary file, and atomically replaces the destination. Keep the legacy Tiingo backfill available only through an explicit provider selection.

**Tech Stack:** Python 3, pandas, sqlite3, unittest, existing `data.daily_history` and `data.research_store` contracts.

## Global Constraints

- Never mix Tiingo and EODHD rows in the rebuilt main database.
- Keep the public `prices(ticker, date, open, high, low, close, volume)` schema unchanged.
- Use EODHD adjusted OHLC and raw volume from each security's current research-history segment.
- Preserve the destination database byte-for-byte on any validation or coverage failure.
- Do not modify forecasting, factor, chart, or research-pool behavior.

---

### Task 1: Atomic EODHD conversion service

**Files:**
- Create: `data/eodhd_main_database.py`
- Create: `tests/test_eodhd_main_database.py`

**Interfaces:**
- Consumes: `data.daily_history.persist_history`, `data.daily_history.audit_history`, SQLite `daily_prices` and `history_segments`.
- Produces: `EODHDRebuildSummary` and `rebuild_from_eodhd(research_database, output_database, *, tickers=None, fetched_at=None)`.

- [ ] **Step 1: Write failing conversion and provenance tests**

Create fixtures containing raw and adjusted prices for two tickers. Assert that the output `prices` table contains adjusted OHLC, unchanged volume, only requested tickers, and `price_coverage.provider = 'eodhd'`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest tests.test_eodhd_main_database -v
```

Expected: import failure because `data.eodhd_main_database` does not exist.

- [ ] **Step 3: Implement the minimal atomic rebuild**

Implement:

```python
@dataclass(frozen=True)
class EODHDRebuildSummary:
    requested: int
    imported: int
    row_count: int
    first_date: str
    last_date: str
    integrity: str
```

Read only rows whose `segment_id` is the current segment. For main tickers outside the research pool, read the same EODHD snapshot's raw JSON through `normalize_daily_rows`; never fall back to Tiingo. Convert to a pandas frame with `Open`, `High`, `Low`, `Close`, `Volume`, call `persist_history`, verify counts and latest dates, close the temporary SQLite connection, then use `Path.replace()`.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Task 1 test command and expect all tests to pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add data/eodhd_main_database.py tests/test_eodhd_main_database.py
git commit -m "feat: rebuild main prices from EODHD"
```

### Task 2: Failure atomicity and CLI provider selection

**Files:**
- Modify: `tests/test_eodhd_main_database.py`
- Modify: `tests/test_build_local_db.py`
- Modify: `build_local_db.py`

**Interfaces:**
- Consumes: `rebuild_from_eodhd`.
- Produces: CLI flags `--provider {eodhd,tiingo}` and `--research-database PATH`; `update(provider='eodhd', research_database=..., output_database=...)`.

- [ ] **Step 1: Write failing atomicity tests**

Assert that a missing target ticker and invalid adjusted OHLC both raise a specific exception and leave a pre-existing destination file hash unchanged.

- [ ] **Step 2: Run atomicity tests and verify RED**

Run the Task 1 test command. Expected: failures because missing and invalid input are not yet handled atomically.

- [ ] **Step 3: Implement atomic cleanup and validation**

Add `EODHDMainDatabaseError`; delete only the explicit sibling temporary file on failure; validate missing tickers, no extra tickers, non-empty histories, latest-date parity, and `PRAGMA integrity_check`. Normalize only machine-precision adjusted-high/low differences within relative tolerance `1e-12`; retain rejection for larger OHLC errors.

- [ ] **Step 4: Run atomicity tests and verify GREEN**

Run the Task 1 test command and expect all tests to pass.

- [ ] **Step 5: Write failing CLI routing tests**

Patch `build_local_db.rebuild_from_eodhd` and `build_local_db.backfill`. Assert default `update()` calls EODHD and `update(provider='tiingo')` calls the legacy one-year backfill.

- [ ] **Step 6: Run CLI tests and verify RED**

Run:

```bash
/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest tests.test_build_local_db.BuildLocalDatabaseTest -v
```

Expected: new routing tests fail because `update()` has no provider argument.

- [ ] **Step 7: Implement CLI routing**

Import the conversion service, add provider and research-database arguments, make EODHD the `--update` default, and preserve explicit Tiingo behavior.

- [ ] **Step 8: Run focused tests and verify GREEN**

Run:

```bash
/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest tests.test_eodhd_main_database tests.test_build_local_db tests.test_daily_history -v
```

Expected: all focused tests pass.

- [ ] **Step 9: Commit Task 2**

```bash
git add build_local_db.py data/eodhd_main_database.py tests/test_build_local_db.py tests/test_eodhd_main_database.py
git commit -m "feat: default main updates to EODHD"
```

### Task 3: Real database rehearsal and switch

**Files:**
- Runtime data only: `data/prices.db`, `data/research_prices.db`

**Interfaces:**
- Consumes: verified CLI and the current 1,031-ticker EODHD research database.
- Produces: a 196-plus-reference-ticker main database whose coverage metadata reports EODHD.

- [ ] **Step 1: Run the complete automated suite**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache /Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest discover -s tests -v
```

Expected: zero failures.

- [ ] **Step 2: Create a recoverable backup**

Copy the current main database to an explicit timestamped file in `/private/tmp`, verify its SHA-256, and do not delete it during this task.

- [ ] **Step 3: Rehearse against copied databases**

Run the EODHD rebuild with copied main and research databases in `/private/tmp`; verify integrity, target count, latest date, provider coverage, and representative tickers `AAPL`, `NBIS`, `MU`, `QQQ`, and `ASML` when present.

- [ ] **Step 4: Switch the real main database**

Run:

```bash
source env.sh
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python build_local_db.py --update --provider eodhd --research-database data/research_prices.db
```

The command must create and verify a sibling temporary database before replacing `data/prices.db`.

- [ ] **Step 5: Verify the real database and dashboard**

Check `PRAGMA integrity_check`, ticker count, maximum date, per-ticker EODHD coverage, and the `/api/universe` plus `/api/stocks/ASML` endpoints after restarting the service.

- [ ] **Step 6: Commit implementation state**

Confirm runtime databases are ignored, run `git diff --check`, and leave the feature branch with no uncommitted source or test changes.
