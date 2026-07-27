# Delisted Identity and Industry Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, resumable pilot that measures how many delisted securities can be linked safely to SEC entities and how much point-in-time SIC history is recoverable.

**Architecture:** Pure research modules select the frozen sample, index SEC submissions, adjudicate identity evidence, and derive SIC intervals. A separate SQLite reference store persists evidence and audit output; one CLI owns network collection, immutable caches, reporting, and resume behavior. Existing price databases remain read-only.

**Tech Stack:** Python 3 standard library, SQLite, `unittest`, SEC submissions bulk ZIP, EODHD Fundamentals and ID Mapping APIs.

## Global Constraints

- Do not modify `data/prices.db`, `data/research_prices.db`, or `data/delisted_research_prices.db`.
- Do not join or rewrite price histories.
- Current ticker-to-CIK equality and name similarity alone must never produce a confirmed identity.
- EODHD industry data is `snapshot_only` and must never backfill historical SEC industry.
- Every historical read uses `available_at <= forecast_asof`.
- The first SIC observation never fills dates before its `available_at`.
- API tokens, authenticated URLs, and full provider responses must not enter Git, reports, logs, or SQLite.
- Raw collection is immutable, content-hashed, resumable, and safe against empty or short responses.
- Sample selection is deterministic and must not be hand-picked.

---

## File Structure

- Create `research/delisted_identity_coverage.py`: sample selection, evidence normalization, adjudication, coverage aggregation.
- Create `research/sec_identity_archive.py`: SEC submissions ZIP validation, local index construction, candidate lookup.
- Create `research/sec_industry_history.py`: SEC header SIC parsing and point-in-time interval construction.
- Create `research/delisted_reference_store.py`: isolated SQLite schema and atomic import.
- Create `run_delisted_identity_coverage.py`: cache-aware collector, orchestrator, report renderer, CLI.
- Create `tests/test_delisted_identity_coverage.py`: sampling, evidence, adjudication, aggregation tests.
- Create `tests/test_sec_identity_archive.py`: SEC archive/index tests.
- Create `tests/test_sec_industry_history.py`: historical SIC parsing and interval tests.
- Create `tests/test_delisted_reference_store.py`: schema, transaction, idempotency, point-in-time read tests.
- Create `tests/test_run_delisted_identity_coverage.py`: collection, resume, redaction, reporting tests.
- Generate `reports/delisted-identity-industry-coverage.{md,json,csv}` only after the real pilot.
- Modify `docs/modeling-todo.md` only after real evidence is available.

### Task 1: Deterministic Coverage Sample

**Files:**
- Create: `research/delisted_identity_coverage.py`
- Create: `tests/test_delisted_identity_coverage.py`

**Interfaces:**
- Consumes: purified catalog rows and per-ticker history summaries.
- Produces: `select_coverage_sample(catalog, history, quotas) -> tuple[dict, ...]`.

- [ ] **Step 1: Write failing tests for fixed sampling**

```python
from research.delisted_identity_coverage import select_coverage_sample


def test_sample_is_stable_and_covers_identity_panels():
    catalog = [
        {"ticker": "AAA", "exchange": "NASDAQ", "name": "Alpha Inc",
         "identity_status": "strong_isin", "provider_isin": "US0000000001",
         "backfill_eligible": True},
        {"ticker": "BBB", "exchange": "NYSE", "name": "Beta PLC ADR",
         "identity_status": "ticker_only", "provider_isin": None,
         "backfill_eligible": True},
        {"ticker": "CCC", "exchange": "NASDAQ", "name": "Gamma Inc",
         "identity_status": "conflicting_isin", "provider_isin": "US0000000002",
         "backfill_eligible": True},
    ]
    history = {
        "AAA": {"valid_rows": 200, "last_date": "2025-01-01"},
        "BBB": {"valid_rows": 0, "last_date": None},
        "CCC": {"valid_rows": 100, "last_date": "2009-12-31"},
    }
    quotas = {
        "strong_isin": 1,
        "ticker_only": 1,
        "conflicting_isin": 1,
    }
    first = select_coverage_sample(catalog, history, quotas)
    second = select_coverage_sample(list(reversed(catalog)), history, quotas)
    assert first == second
    assert {row["identity_panel"] for row in first} == set(quotas)
    assert all("selection_hash" in row for row in first)
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ../../venv/bin/python -m unittest tests.test_delisted_identity_coverage -v`

Expected: FAIL because `research.delisted_identity_coverage` does not exist.

- [ ] **Step 3: Implement normalized rows and hash-ranked panels**

```python
SAMPLE_VERSION = "delisted_identity_coverage_sample_v1"
DEFAULT_QUOTAS = {
    "strong_isin": 100,
    "ticker_only": 100,
    "conflicting_isin": 75,
}


def select_coverage_sample(catalog, history, quotas=None):
    quotas = dict(quotas or DEFAULT_QUOTAS)
    panels = {key: [] for key in quotas}
    for raw in catalog:
        if not raw.get("backfill_eligible"):
            continue
        ticker = str(raw.get("ticker") or "").strip().upper()
        panel = str(raw.get("identity_status") or "")
        if panel not in panels:
            continue
        audit = dict(history.get(ticker) or {})
        digest = hashlib.sha256(
            f"{SAMPLE_VERSION}|{panel}|{ticker}".encode()
        ).hexdigest()
        panels[panel].append({
            "ticker": ticker,
            "exchange": str(raw.get("exchange") or "").upper(),
            "name": str(raw.get("name") or ticker),
            "provider_isin": raw.get("provider_isin"),
            "identity_panel": panel,
            "valid_rows": int(audit.get("valid_rows") or 0),
            "last_date": audit.get("last_date"),
            "selection_hash": digest,
            "sample_version": SAMPLE_VERSION,
        })
    selected = []
    for panel, quota in quotas.items():
        rows = sorted(panels[panel], key=lambda row: (
            row["selection_hash"], row["ticker"]
        ))
        if len(rows) < int(quota):
            raise ValueError(f"{panel} has {len(rows)} rows; {quota} required")
        selected.extend(rows[:int(quota)])
    return tuple(sorted(selected, key=lambda row: row["ticker"]))
```

- [ ] **Step 4: Add tests for invalid quotas, duplicate tickers, excluded rows, and empty histories**

The tests must assert explicit `ValueError` for duplicate eligible tickers and insufficient panels, and must prove `backfill_eligible=False` rows cannot enter the sample.

- [ ] **Step 5: Run focused tests**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ../../venv/bin/python -m unittest tests.test_delisted_identity_coverage -v`

Expected: all Task 1 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add research/delisted_identity_coverage.py tests/test_delisted_identity_coverage.py
git commit -m "research: add deterministic delisted identity sample"
```

### Task 2: SEC Submissions Archive Index

**Files:**
- Create: `research/sec_identity_archive.py`
- Create: `tests/test_sec_identity_archive.py`

**Interfaces:**
- Consumes: a verified SEC `submissions.zip`.
- Produces:
  - `normalize_legal_name(value: str) -> str`
  - `iter_submission_records(path: Path) -> Iterator[dict]`
  - `build_identity_index(records: Iterable[Mapping]) -> dict`
  - `find_sec_candidates(sample_row: Mapping, index: Mapping) -> tuple[dict, ...]`

- [ ] **Step 1: Write a failing in-memory ZIP test**

```python
def test_archive_indexes_current_and_former_names(tmp_path):
    path = tmp_path / "submissions.zip"
    payload = {
        "cik": "123",
        "name": "Example Holdings, Inc.",
        "tickers": ["NEW"],
        "exchanges": ["Nasdaq"],
        "sic": "3674",
        "sicDescription": "Semiconductors",
        "formerNames": [{
            "name": "Example Devices Corp.",
            "from": "2012-01-01",
            "to": "2020-01-01",
        }],
        "filings": {"recent": {"accessionNumber": []}, "files": []},
    }
    with ZipFile(path, "w") as archive:
        archive.writestr("CIK0000000123.json", json.dumps(payload))
    index = build_identity_index(iter_submission_records(path))
    matches = find_sec_candidates(
        {"ticker": "OLD", "name": "Example Devices Corp."},
        index,
    )
    assert matches[0]["cik"] == "0000000123"
    assert matches[0]["name_match"] == "exact_former_name"
```

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ../../venv/bin/python -m unittest tests.test_sec_identity_archive -v`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement strict archive parsing**

The parser must accept only files named `CIK##########.json`, verify the
payload CIK matches the filename, reject duplicate CIK records, normalize
CIK to ten digits, and retain only:

```python
{
    "cik": "0000000123",
    "name": "Example Holdings, Inc.",
    "normalized_name": "EXAMPLE HOLDINGS",
    "tickers": ("NEW",),
    "exchanges": ("NASDAQ",),
    "sic": "3674",
    "sic_description": "Semiconductors",
    "former_names": ({
        "name": "Example Devices Corp.",
        "normalized_name": "EXAMPLE DEVICES",
        "from": "2012-01-01",
        "to": "2020-01-01",
    },),
    "filing_files": (),
}
```

`normalize_legal_name` may remove punctuation and terminal legal suffixes
such as `INC`, `CORP`, `LTD`, `PLC`, and `LLC`; it must not remove meaningful
middle words or perform fuzzy matching.

- [ ] **Step 4: Implement candidate lookup without confirmation**

`find_sec_candidates` returns evidence candidates only. It may retrieve by
exact normalized current/former name and exact current ticker, but every row
must identify which fields matched. This function must not emit
`link_status="confirmed"`.

- [ ] **Step 5: Add malformed archive and collision tests**

Tests must reject filename/payload CIK mismatch, duplicate CIK, non-object
JSON, malformed former-name dates, and a ticker shared by multiple CIKs.
Shared ticker lookup must return both candidates in sorted CIK order.

- [ ] **Step 6: Run focused tests and commit**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ../../venv/bin/python -m unittest tests.test_sec_identity_archive -v`

Expected: all Task 2 tests PASS.

```bash
git add research/sec_identity_archive.py tests/test_sec_identity_archive.py
git commit -m "research: index SEC identity evidence"
```

### Task 3: Evidence Adjudication

**Files:**
- Modify: `research/delisted_identity_coverage.py`
- Modify: `tests/test_delisted_identity_coverage.py`

**Interfaces:**
- Consumes: sample row, SEC candidates, optional EODHD identity evidence.
- Produces:
  - `normalize_provider_evidence(payload, observed_at) -> tuple[dict, ...]`
  - `adjudicate_identity(sample_row, sec_candidates, provider_evidence) -> dict`

- [ ] **Step 1: Write failing adjudication tests**

```python
def test_ticker_only_never_confirms_identity():
    result = adjudicate_identity(
        {"ticker": "OLD", "name": "Unrelated Company", "provider_isin": None},
        [{"cik": "0000000123", "match_reasons": ["current_ticker"]}],
        (),
    )
    assert result["link_status"] == "review_required"
    assert result["reason_codes"] == ["ticker_only_match"]


def test_unique_isin_to_cik_with_exact_name_confirms():
    result = adjudicate_identity(
        {"ticker": "OLD", "name": "Example Devices", "provider_isin": "US123"},
        [{"cik": "0000000123", "match_reasons": ["exact_former_name"]}],
        [{"key_type": "isin_cik", "isin": "US123", "cik": "0000000123",
          "available_at": "2026-07-27T00:00:00Z"}],
    )
    assert result["link_status"] == "confirmed"
    assert result["cik"] == "0000000123"
```

- [ ] **Step 2: Run and verify the new tests fail**

Run the two test methods with `unittest -v`; expected failure is missing
functions.

- [ ] **Step 3: Implement explicit decision rules**

Decision rule version is `delisted_identity_adjudication_v1`:

- unique provider ISIN→CIK plus exact SEC current/former legal name: confirmed;
- exact former legal name plus an SEC former-name interval overlapping the
  candidate price history: confirmed;
- current ticker alone: review required;
- exact name without a dated former-name interval: review required;
- competing CIKs or ISIN conflict: review required;
- no candidates: unresolved;
- security-type contradiction: rejected.

Return stable keys:

```python
{
    "ticker": "OLD",
    "cik": "0000000123",
    "link_status": "confirmed",
    "decision_rule": "isin_cik_plus_exact_name",
    "rule_version": "delisted_identity_adjudication_v1",
    "reason_codes": [],
    "supporting_evidence": [...],
    "conflicting_evidence": [],
}
```

- [ ] **Step 4: Add a decision-table test for every branch**

Use parametrized cases for confirmed, review required, unresolved, and
rejected. Assert that changing input order does not change output bytes after
canonical JSON serialization.

- [ ] **Step 5: Run tests and commit**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ../../venv/bin/python -m unittest tests.test_delisted_identity_coverage -v`

Expected: PASS.

```bash
git add research/delisted_identity_coverage.py tests/test_delisted_identity_coverage.py
git commit -m "research: adjudicate delisted SEC identities"
```

### Task 4: Historical SIC Observations and Intervals

**Files:**
- Create: `research/sec_industry_history.py`
- Create: `tests/test_sec_industry_history.py`

**Interfaces:**
- Produces:
  - `parse_sec_submission_header(text, accession, filing_date, accepted_at) -> dict | None`
  - `build_sic_intervals(observations) -> tuple[dict, ...]`
  - `classification_asof(intervals, asof) -> dict | None`

- [ ] **Step 1: Write failing parsing and no-lookahead tests**

```python
def test_sic_interval_starts_when_filing_is_available():
    observations = [
        {"cik": "0000000123", "sic": "3674", "available_at": "2018-03-01T21:00:00Z",
         "accession_number": "0000000123-18-000001"},
        {"cik": "0000000123", "sic": "7372", "available_at": "2020-04-01T20:00:00Z",
         "accession_number": "0000000123-20-000002"},
    ]
    intervals = build_sic_intervals(observations)
    assert classification_asof(intervals, "2018-02-28T23:59:59Z") is None
    assert classification_asof(intervals, "2019-01-01T00:00:00Z")["sic"] == "3674"
    assert classification_asof(intervals, "2021-01-01T00:00:00Z")["sic"] == "7372"
```

- [ ] **Step 2: Run and verify failure**

Run the focused file; expected failure is missing module.

- [ ] **Step 3: Implement conservative header parsing**

Parse only an explicit EDGAR header line matching:

```text
STANDARD INDUSTRIAL CLASSIFICATION: <label> [<four digit SIC>]
```

Reject missing `accepted_at`, malformed accession, non-four-digit SIC, and
conflicting duplicate SIC lines. Return `None` when the field is absent.
Set `available_at=accepted_at`; never substitute report period or fiscal date.

- [ ] **Step 4: Implement half-open interval generation**

Sort by `(available_at, accession_number)`, merge adjacent observations with
the same SIC, and close an interval at the first different SIC observation.
Duplicate accession with conflicting SIC raises `ValueError`.

- [ ] **Step 5: Add tests for same-SIC reinforcement, same-time conflict, stale open interval, and timezone-aware timestamps**

The query function must reject naive datetimes and prove the boundary is
`[valid_from, valid_to)`.

- [ ] **Step 6: Run tests and commit**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ../../venv/bin/python -m unittest tests.test_sec_industry_history -v
git add research/sec_industry_history.py tests/test_sec_industry_history.py
git commit -m "research: derive point-in-time SEC industry intervals"
```

### Task 5: Isolated Reference Store

**Files:**
- Create: `research/delisted_reference_store.py`
- Create: `tests/test_delisted_reference_store.py`

**Interfaces:**
- Produces class `DelistedReferenceStore(path)` with:
  - `replace_sample(sample, catalog_sha256, snapshot_date)`
  - `replace_identity_results(results, evidence)`
  - `replace_sic_observations(observations, intervals)`
  - `replace_provider_snapshots(snapshots)`
  - `classification_asof(ticker, asof)`
  - `integrity_report()`

- [ ] **Step 1: Write a failing isolated-database test**

```python
def test_store_does_not_use_snapshot_as_historical_fallback(tmp_path):
    store = DelistedReferenceStore(tmp_path / "reference.db")
    store.replace_sample([sample_row()], "a" * 64, "2026-07-27")
    store.replace_provider_snapshots([{
        "ticker": "OLD", "sector": "Technology",
        "snapshot_at": "2026-07-27T00:00:00Z",
        "historical_eligibility": "snapshot_only",
    }])
    assert store.classification_asof("OLD", "2019-01-01T00:00:00Z") is None
```

- [ ] **Step 2: Run and verify failure**

Expected: missing module/class.

- [ ] **Step 3: Implement schema and strict constraints**

Create the tables defined by the design:

- `coverage_sample`
- `identity_evidence`
- `security_entity_links`
- `sec_industry_observations`
- `sec_industry_intervals`
- `provider_classification_snapshots`
- `market_behavior_classifications`
- `identity_conflicts`
- `rejected_industry_observations`
- `collection_runs`
- `source_artifacts`

Use foreign keys, enum-style `CHECK` constraints, unique source-record keys,
ISO timestamps, and half-open interval checks.

- [ ] **Step 4: Implement transactional replacement and point-in-time read**

Each logical import uses one transaction. Empty replacement is rejected.
`classification_asof` joins only confirmed entity links and SEC intervals;
it never queries provider snapshots.

- [ ] **Step 5: Add rollback, idempotency, FK, and integrity tests**

Assert:

```python
assert store.integrity_report() == {
    "integrity_check": "ok",
    "foreign_key_errors": 0,
}
```

Also hash three fixture price databases before and after store operations and
assert hashes are unchanged.

- [ ] **Step 6: Run tests and commit**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ../../venv/bin/python -m unittest tests.test_delisted_reference_store -v
git add research/delisted_reference_store.py tests/test_delisted_reference_store.py
git commit -m "data: add isolated delisted reference store"
```

### Task 6: Resumable Collector and Provider Boundaries

**Files:**
- Create: `run_delisted_identity_coverage.py`
- Create: `tests/test_run_delisted_identity_coverage.py`

**Interfaces:**
- Produces:
  - `collect_artifact(name, cache_root, fetcher) -> dict`
  - `run_coverage_pilot(...) -> dict`
- CLI inputs:
  - purified catalog path
  - read-only delisted staging DB path
  - reference DB output path
  - raw cache root
  - report paths
- Environment:
  - `SEC_USER_AGENT`
  - optional `EODHD_API_TOKEN`

- [ ] **Step 1: Write failing cache/resume tests with fake fetchers**

```python
def test_second_run_reuses_verified_cache(tmp_path):
    calls = []
    def fetch(url, headers):
        calls.append(url)
        return b"verified fixture bytes"
    first = collect_artifact("sec_submissions", tmp_path, fetcher=fetch)
    second = collect_artifact("sec_submissions", tmp_path, fetcher=fetch)
    assert first["sha256"] == second["sha256"]
    assert len(calls) == 1
```

Add a test proving error text and reports never contain a fake token.

- [ ] **Step 2: Run and verify failure**

Run the focused file; expected failure is missing runner.

- [ ] **Step 3: Implement immutable SEC archive collection**

Download to a temporary file, validate ZIP structure, compute SHA-256, and
atomically rename to:

```text
data/cache/sec/submissions/<snapshot-date>/submissions.zip
```

Write a manifest without authenticated URLs. Require a descriptive
`SEC_USER_AGENT`; missing value must fail before network access.

- [ ] **Step 4: Implement optional provider-assisted sample collection**

Only adjudications unresolved after local SEC matching are eligible. Cap the
default provider requests at 300 symbols. Cache each response separately by
snapshot and ticker. Fundamentals and ID mapping responses are normalized to
identity evidence and provider snapshots; raw provider JSON remains ignored.
HTTP 401/403, 404, 429, and transient 5xx must have distinct statuses.

- [ ] **Step 5: Implement orchestration and redacted progress**

The runner must:

1. verify the catalog SHA-256;
2. query history summaries read-only from `delisted_research_prices.db`;
3. freeze or reuse `sample.json`;
4. build the SEC local index;
5. adjudicate SEC-only results;
6. optionally collect provider evidence;
7. sample a bounded set of confirmed CIK filings for SIC availability;
8. build a temporary reference DB and atomically replace the output;
9. render reports.

Progress output contains counts, byte sizes, and statuses only.

- [ ] **Step 6: Add CLI, malformed cache, partial failure, and quota exhaustion tests**

Prove a corrupt cache is rejected, a prior successful artifact survives a
failed refresh, 429 leaves the run resumable, and `--offline` performs no
network access.

- [ ] **Step 7: Run tests and commit**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ../../venv/bin/python -m unittest tests.test_run_delisted_identity_coverage -v
git add run_delisted_identity_coverage.py tests/test_run_delisted_identity_coverage.py
git commit -m "research: add delisted identity coverage runner"
```

### Task 7: Coverage Aggregation and Reports

**Files:**
- Modify: `research/delisted_identity_coverage.py`
- Modify: `run_delisted_identity_coverage.py`
- Modify: `tests/test_delisted_identity_coverage.py`
- Modify: `tests/test_run_delisted_identity_coverage.py`

**Interfaces:**
- Produces:
  - `summarize_coverage(sample, decisions, sic_audits, usage) -> dict`
  - committed summary reports with no raw provider payload.

- [ ] **Step 1: Write failing summary tests**

The fixture must assert exact counts for:

- confirmed/review/rejected/unresolved by identity panel;
- SEC-only versus provider-assisted confirmations;
- candidate collisions and reason codes;
- SIC availability and date ranges;
- request counts, API units, bytes, projected storage, and projected runtime.

- [ ] **Step 2: Run and verify failure**

Expected: missing `summarize_coverage`.

- [ ] **Step 3: Implement deterministic aggregation**

All rates include numerator and denominator. Empty denominators return
`value=None` plus `reason="no_eligible_rows"`. Projections must identify the
source sample panel and must not combine mutually incompatible panels.

- [ ] **Step 4: Implement Markdown, JSON, and CSV renderers**

JSON and CSV are machine-readable evidence; Markdown explains limits.
Reports include catalog SHA-256, sample version, rule versions, cache hashes,
reference DB integrity, and hashes of all three unchanged price databases.
Limit examples to five per reason code.

- [ ] **Step 5: Add deterministic-byte and secret-scan tests**

Run the renderer twice with fixed inputs and assert identical bytes. Search
all three reports for fake tokens, `api_token=`, and authenticated URLs.

- [ ] **Step 6: Run tests and commit**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ../../venv/bin/python -m unittest tests.test_delisted_identity_coverage tests.test_run_delisted_identity_coverage -v
git add research/delisted_identity_coverage.py run_delisted_identity_coverage.py tests/test_delisted_identity_coverage.py tests/test_run_delisted_identity_coverage.py
git commit -m "research: report delisted identity coverage"
```

### Task 8: Full Verification and Real Pilot

**Files:**
- Generate: `reports/delisted-identity-industry-coverage.md`
- Generate: `reports/delisted-identity-industry-coverage.json`
- Generate: `reports/delisted-identity-industry-coverage.csv`
- Modify: `docs/modeling-todo.md`

**Interfaces:**
- Consumes the verified implementation from Tasks 1–7.
- Produces real coverage evidence and a go/no-go decision for full backfill.

- [ ] **Step 1: Run focused and full tests**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ../../venv/bin/python -m unittest \
  tests.test_delisted_identity_coverage \
  tests.test_sec_identity_archive \
  tests.test_sec_industry_history \
  tests.test_delisted_reference_store \
  tests.test_run_delisted_identity_coverage -v
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ../../venv/bin/python -m unittest discover -s tests -q
```

Expected: all tests PASS.

- [ ] **Step 2: Record immutable pre-run database hashes**

```bash
shasum -a 256 data/prices.db data/research_prices.db data/delisted_research_prices.db
```

- [ ] **Step 3: Run the real pilot**

```bash
source env.sh
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python \
  run_delisted_identity_coverage.py \
  --catalog data/cache/eodhd_delisted_security_catalog/2026-07-27/catalog.json \
  --delisted-db data/delisted_research_prices.db \
  --reference-db data/delisted_reference_data.db \
  --raw-root data/cache/delisted_identity_coverage/2026-07-27 \
  --report-prefix reports/delisted-identity-industry-coverage
```

Expected: completed or explicitly partial/resumable status; no silent
substitution if SEC or EODHD access fails.

- [ ] **Step 4: Verify database integrity and non-mutation**

```bash
sqlite3 -readonly data/delisted_reference_data.db "PRAGMA integrity_check; PRAGMA foreign_key_check;"
shasum -a 256 data/prices.db data/research_prices.db data/delisted_research_prices.db
git diff --check
```

Expected: `ok`, no FK rows, and hashes identical to Step 2.

- [ ] **Step 5: Audit the report and decide the next gate**

Mark only the coverage experiment subtask complete. Record one of:

- `full_backfill_recommended`
- `sec_only_backfill_recommended`
- `identity_quality_insufficient`
- `provider_access_blocked`

Do not close the full historical classification TODO.

- [ ] **Step 6: Commit real evidence**

```bash
git add reports/delisted-identity-industry-coverage.md \
  reports/delisted-identity-industry-coverage.json \
  reports/delisted-identity-industry-coverage.csv \
  docs/modeling-todo.md
git commit -m "data: record delisted identity coverage pilot"
```

### Task 9: Final Review

**Files:**
- Review all Task 1–8 files.

**Interfaces:**
- Produces a verified, documented pilot with no model or UI behavior changes.

- [ ] **Step 1: Review spec coverage**

Compare implementation against every section of
`docs/superpowers/specs/2026-07-27-delisted-point-in-time-identity-industry-design.md`.
Confirm full backfill, model integration, and UI integration remain out of scope.

- [ ] **Step 2: Run security and repository hygiene checks**

```bash
rg -n "(api_token=|EODHD_API_TOKEN.{0,20}[A-Za-z0-9]{12,}|PK[A-Z0-9]{20,})" \
  reports research run_delisted_identity_coverage.py tests
git status --short
git diff --check
```

Expected: no secret values; only known user-owned untracked files remain.

- [ ] **Step 3: Run final regression**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ../../venv/bin/python -m unittest discover -s tests -q`

Expected: full suite PASS.

- [ ] **Step 4: Commit any review-only fixes separately**

```bash
git add research/delisted_identity_coverage.py \
  research/sec_identity_archive.py \
  research/sec_industry_history.py \
  research/delisted_reference_store.py \
  run_delisted_identity_coverage.py \
  tests/test_delisted_identity_coverage.py \
  tests/test_sec_identity_archive.py \
  tests/test_sec_industry_history.py \
  tests/test_delisted_reference_store.py \
  tests/test_run_delisted_identity_coverage.py
git commit -m "fix: harden delisted identity coverage audit"
```

Skip the commit when review finds no changes.
