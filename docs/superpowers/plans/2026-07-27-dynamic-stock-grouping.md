# Dynamic Stock Grouping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Assign every research stock to an auditable broad sector and benchmark ETF, optionally attach themes, automatically classify new stocks, and feed the same assignment into forecasts, market views, and the stock UI.

**Architecture:** A pure classification resolver produces immutable `GroupAssignment` records from SEC classification, market-behavior evidence, and versioned overrides. The research import persists one assignment snapshot per security; a read-only service supplies assignments to APIs and forecast services. Forecast risk context uses a dynamically constructed primary group while preserving separate market, sector, theme, and individual evidence.

**Tech Stack:** Python 3, dataclasses, JSON, SQLite, pandas, Flask, vanilla JavaScript, unittest/pytest-compatible tests.

## Global Constraints

- Every active common stock must have one standard broad sector or the explicit `unclassified_review` state.
- Sector assignments use only the 11 standard sector keys and the ETF mapping already defined in `web/market_groups.py`.
- SNDK resolves to `technology`, sector ETF `XLK`, theme `semiconductor`, and primary model group `semiconductor`.
- SEC classification is primary; market-behavior classification is independent corroborating evidence and never silently overwrites SEC.
- Manual overrides are versioned, effective-dated, reasoned, and tracked by Git.
- Historical calls resolve the assignment effective at the requested observation date.
- Missing sector/theme evidence remains unavailable; model weights are not silently redistributed.
- AI infrastructure related stocks are not labeled semiconductor constituents.
- All production changes follow red-green-refactor and each task ends with a focused commit.

---

## File Structure

**Create**

- `data/group_assignments.py`: domain contract, validation, resolver, override loader, and audit helpers.
- `data/security_group_overrides_v1.json`: versioned exceptional assignments, beginning with SNDK and existing AI-infrastructure distinctions.
- `web/services/group_assignments.py`: bounded read-only SQLite assignment repository.
- `audit_group_assignments.py`: CLI that reports coverage, invalid benchmarks, unresolved names, and theme counts.
- `tests/test_group_assignments.py`: pure resolver and override tests.
- `tests/test_web_group_assignments.py`: SQLite repository tests.
- `tests/test_group_assignment_audit.py`: full audit contract tests.

**Modify**

- `data/research_store.py`: persist assignment snapshots.
- `build_research_db.py`: resolve assignments during every database build and fail publication when coverage integrity fails.
- `web/market_groups.py`: separate group definitions from dynamic membership and add assignment-driven group construction.
- `web/forecasts/decision.py`: build risk contexts from dynamic groups and retain sector/theme evidence separately.
- `web/services/forecasts.py`: include assignment revision in artifact identity and accept assignment snapshots.
- `web/services/research_classification.py`: return unified assignment data beside the two classification taxonomies.
- `web/services/universe.py`: merge the unified assignment into stock rows.
- `web/app.py`: pass point-in-time assignments to active and research-on-demand forecasts.
- `web/static/js/app.js`: render broad sector, theme, primary model group, source, confidence, and conflicts.
- `web/static/js/i18n.js`: add Chinese and English assignment labels.
- Existing focused tests in `tests/test_research_store.py`, `tests/test_web_market_groups.py`, `tests/test_web_forecast_decision.py`, `tests/test_web_api.py`, and JavaScript runtime tests.

---

### Task 1: Pure Group Assignment Contract and Overrides

**Files:**
- Create: `data/group_assignments.py`
- Create: `data/security_group_overrides_v1.json`
- Test: `tests/test_group_assignments.py`

**Interfaces:**
- Produces: `GroupAssignment`, `resolve_group_assignment(ticker, classifications, asof, overrides=None)`, `load_group_overrides(path=None)`, `audit_assignments(assignments)`.
- Consumes: existing `SECTOR_ETFS` and theme benchmark definitions from `web.market_groups.py`.

- [ ] **Step 1: Write failing resolver tests**

```python
def test_sndk_override_maps_to_semiconductor_theme():
    assignment = resolve_group_assignment(
        "SNDK",
        {
            "sec": {
                "sector_key": "technology",
                "industry_code": "3572",
                "confidence": 0.8,
            },
            "market_behavior": {
                "sector_key": "technology",
                "benchmark_ticker": "XLK",
                "confidence": 0.82,
            },
        },
        "2026-07-24",
    )
    assert assignment.sector_key == "technology"
    assert assignment.sector_benchmark == "XLK"
    assert assignment.theme_keys == ("semiconductor",)
    assert assignment.primary_model_group == "semiconductor"


def test_unknown_security_is_explicitly_queued_for_review():
    assignment = resolve_group_assignment("ZZZZ", {}, "2026-07-24")
    assert assignment.sector_key == "unclassified_review"
    assert assignment.classification_state == "needs_review"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m pytest tests/test_group_assignments.py -q`

Expected: FAIL because `data.group_assignments` does not exist.

- [ ] **Step 3: Implement the immutable contract and deterministic resolver**

```python
@dataclass(frozen=True)
class GroupAssignment:
    ticker: str
    asof: str
    sector_key: str
    sector_benchmark: str | None
    theme_keys: tuple[str, ...]
    theme_benchmarks: Mapping[str, tuple[str, ...]]
    primary_model_group: str
    classification_state: str
    source: str
    rule_version: str
    confidence: float
    override_reason: str | None = None


def resolve_group_assignment(ticker, classifications, asof, overrides=None):
    """Resolve override -> SEC exact/theme -> SEC broad -> behavior -> review."""
```

The JSON override for SNDK must include `effective_from`, `effective_to`, `sector_key`, `theme_keys`, `primary_model_group`, `reason`, and `rule_version`.

- [ ] **Step 4: Add priority, effective-date, conflict, and audit tests**

Test exact SEC precedence over behavior, behavior fallback only when SEC is absent, inactive overrides, invalid ETF references, duplicate themes, and conflicting effective ranges.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m pytest tests/test_group_assignments.py tests/test_sector_classification.py tests/test_web_market_groups.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add data/group_assignments.py data/security_group_overrides_v1.json tests/test_group_assignments.py
git commit -m "feat: add auditable stock group assignments"
```

---

### Task 2: Persist Assignments and Enforce Classification on Import

**Files:**
- Modify: `data/research_store.py`
- Modify: `build_research_db.py`
- Modify: `tests/test_research_store.py`
- Create: `tests/test_group_assignment_audit.py`

**Interfaces:**
- Consumes: `resolve_group_assignment(...)` and `audit_assignments(...)`.
- Produces: SQLite table `group_assignments` and import result fields `group_assignment_count`, `group_assignment_review_count`, `group_assignment_coverage`.

- [ ] **Step 1: Write failing schema and import tests**

```python
def test_import_security_persists_group_assignment(store):
    store.import_security(
        sndk_security,
        daily_rows,
        [],
        [],
        snapshot_date="2026-07-24",
        imported_at="2026-07-25T00:00:00Z",
    )
    row = store.connection.execute(
        "SELECT sector_key, sector_benchmark, primary_model_group "
        "FROM group_assignments WHERE ticker='SNDK'"
    ).fetchone()
    assert row == ("technology", "XLK", "semiconductor")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m pytest tests/test_research_store.py::ResearchStoreTest::test_import_security_persists_group_assignment -q`

Expected: FAIL because `group_assignments` does not exist.

- [ ] **Step 3: Add the versioned SQLite table and writer**

The table stores scalar columns plus canonical JSON for `theme_keys` and `theme_benchmarks`, keyed by `(ticker, rule_version, effective_from)`. It also stores `effective_to`, `observed_at`, `source`, `confidence`, `override_reason`, and `classification_state`.

- [ ] **Step 4: Resolve every catalog security inside `build_database`**

Build an assignment from the SEC catalog row and the completed market-behavior result, persist it, and run `audit_assignments` before replacing the production database. ETFs remain reference assets and do not count against common-stock coverage.

- [ ] **Step 5: Add publication-gate tests**

Verify a newly imported stock receives an assignment, unresolved names are stored as `unclassified_review`, missing standard ETF mappings fail integrity, and a failed audit leaves the previous output database untouched.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m pytest tests/test_research_store.py tests/test_group_assignment_audit.py tests/test_universe_catalog.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add data/research_store.py build_research_db.py tests/test_research_store.py tests/test_group_assignment_audit.py
git commit -m "feat: classify every imported research stock"
```

---

### Task 3: Point-in-Time Assignment Repository

**Files:**
- Create: `web/services/group_assignments.py`
- Create: `tests/test_web_group_assignments.py`
- Modify: `web/services/research_classification.py`
- Modify: `tests/test_web_research_classification.py`

**Interfaces:**
- Produces: `GroupAssignmentRepository.build(tickers, asof=None) -> dict`.
- Returned payload: `status`, `asof`, `revision`, `coverage`, `review_count`, `by_ticker`.
- Consumes: SQLite `group_assignments`.

- [ ] **Step 1: Write failing point-in-time repository tests**

```python
def test_repository_selects_assignment_effective_at_asof(database):
    repository = GroupAssignmentRepository(database)
    result = repository.build(["SNDK"], asof="2026-07-01")
    assert result["by_ticker"]["SNDK"]["primary_model_group"] == "semiconductor"
    assert result["by_ticker"]["SNDK"]["sector_benchmark"] == "XLK"
```

Add a second effective range and assert dates before and after the boundary select different rows without reading the future row.

- [ ] **Step 2: Run tests and verify RED**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m pytest tests/test_web_group_assignments.py -q`

Expected: FAIL because the repository does not exist.

- [ ] **Step 3: Implement a batched, revision-cached read**

Use one window-function query per batch to select the latest row satisfying `effective_from <= asof` and `(effective_to IS NULL OR asof < effective_to)`. Return immutable JSON-safe copies and explicit missing reasons.

- [ ] **Step 4: Merge assignments into classification payloads**

`ResearchClassificationService.build` must preserve `sec` and `market_behavior`, add `group_assignment`, and expose assignment coverage independently from taxonomy coverage.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m pytest tests/test_web_group_assignments.py tests/test_web_research_classification.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web/services/group_assignments.py web/services/research_classification.py tests/test_web_group_assignments.py tests/test_web_research_classification.py
git commit -m "feat: expose point-in-time stock assignments"
```

---

### Task 4: Dynamic Market Groups and Forecast Risk Context

**Files:**
- Modify: `web/market_groups.py`
- Modify: `web/forecasts/decision.py`
- Modify: `tests/test_web_market_groups.py`
- Modify: `tests/test_web_forecast_decision.py`

**Interfaces:**
- Produces: `resolved_market_groups(assignments, available_tickers) -> tuple[MarketGroup, ...]`.
- Changes: `build_forecast_risk_context(histories, assignments=None)`.
- Consumes: assignment dictionaries from Task 3.

- [ ] **Step 1: Write failing dynamic-membership tests**

```python
def test_resolved_groups_make_sndk_a_semiconductor_constituent():
    groups = resolved_market_groups(
        {"SNDK": sndk_assignment, "ADBE": adbe_assignment},
        {"SNDK", "ADBE", "SOXX", "SMH", "IGV", "XSW"},
    )
    semiconductor = next(g for g in groups if g.key == "semiconductor")
    assert semiconductor.constituent_tickers == ("SNDK",)
```

Also assert every broad sector creates a group with its ETF, theme membership takes primary-model precedence, and NBIS remains `related_tickers`.

- [ ] **Step 2: Run tests and verify RED**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m pytest tests/test_web_market_groups.py -q`

Expected: FAIL because `resolved_market_groups` does not exist.

- [ ] **Step 3: Implement assignment-driven groups**

Keep benchmark definitions stable. Construct members from `primary_model_group`, not from hardcoded ticker tuples. Retain a temporary compatibility path only when `assignments is None`.

- [ ] **Step 4: Write failing risk-context test**

Build synthetic SNDK, SOXX, SMH, QQQ histories and assert `build_forecast_risk_context(..., assignments)` returns SNDK rows with non-null `high_level_distribution_state`.

- [ ] **Step 5: Implement separate sector/theme evidence fields**

The risk context must include `sector_risk_score`, `theme_risk_score`, `sector_group_key`, `theme_group_key`, and the existing individual/top-risk fields. The decision policy continues to use the maximum available causal risk source without relabeling it as probability.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m pytest tests/test_web_market_groups.py tests/test_web_forecast_decision.py tests/test_top_risk_timeline.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add web/market_groups.py web/forecasts/decision.py tests/test_web_market_groups.py tests/test_web_forecast_decision.py
git commit -m "feat: build forecast groups from assignments"
```

---

### Task 5: Forecast Service, Cache Identity, and Research-On-Demand Wiring

**Files:**
- Modify: `web/services/forecasts.py`
- Modify: `web/services/forecast_artifacts.py`
- Modify: `web/app.py`
- Modify: `tests/test_web_api.py`
- Modify: forecast artifact service tests selected by `rg -l "ForecastArtifactIdentity" tests`

**Interfaces:**
- Changes: `ForecastService.build(ticker, chart_dates, histories, *, assignments=None, assignment_revision=None, expected_revision=None)`.
- Changes: `ForecastService.build_top_risk_timeline(..., assignments=None, assignment_revision=None, expected_revision=None)`.
- Consumes: Task 3 assignment payload.

- [ ] **Step 1: Write failing cache and API tests**

Assert two otherwise identical builds with different assignment revisions rebuild artifacts. Assert joined SNDK detail returns `top_risk.status == "available"` and contains a `top_risk_watch` event on 2026-06-26.

- [ ] **Step 2: Run tests and verify RED**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m pytest tests/test_web_api.py -k 'sndk and top_risk' -q`

Expected: FAIL because SNDK currently returns `top_risk.status == "unavailable"`.

- [ ] **Step 3: Include assignments in forecast artifacts**

Pass assignments to `build_forecast_risk_context`; include a deterministic assignment fingerprint/revision in the in-memory key and persistent artifact identity. An assignment change must not reuse a stale risk context.

- [ ] **Step 4: Wire both active and research-on-demand paths**

For a selected ticker, load assignments for the ticker and reference histories at the requested observation date. Pass the same assignment snapshot to Ridge build, top-risk timeline, historical forecast, and market context.

- [ ] **Step 5: Add SNDK historical regression assertions**

Verify:

- 2026-06-16 state `watch`;
- 2026-06-26 state `watch`;
- 2026-06-29 through 2026-07-01 retain remembered/fading risk;
- 2026-07-02 state `confirmed`.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m pytest tests/test_web_api.py tests/test_top_risk_timeline.py tests/test_web_forecast_artifacts.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add web/services/forecasts.py web/services/forecast_artifacts.py web/app.py tests
git commit -m "fix: apply dynamic groups to forecast risk"
```

---

### Task 6: Unified Grouping API and UI

**Files:**
- Modify: `web/services/universe.py`
- Modify: `web/app.py`
- Modify: `web/static/js/app.js`
- Modify: `web/static/js/i18n.js`
- Modify: `web/templates/index.html`
- Modify: `tests/test_web_universe_service.py`
- Modify: `tests/test_web_api.py`
- Modify: `tests/universe_runtime.mjs`

**Interfaces:**
- Produces API field `group_assignment` on universe rows and stock detail.
- Consumes the assignment payload from Task 3.

- [ ] **Step 1: Write failing API and JavaScript tests**

Assert SNDK returns:

```json
{
  "sector_key": "technology",
  "sector_benchmark": "XLK",
  "theme_keys": ["semiconductor"],
  "theme_benchmarks": {"semiconductor": ["SOXX", "SMH"]},
  "primary_model_group": "semiconductor"
}
```

Assert the Chinese UI renders “科技 / XLK”, “半导体 / SOXX+SMH”, classification source, confidence, and conflict state.

- [ ] **Step 2: Run tests and verify RED**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m pytest tests/test_web_universe_service.py tests/test_web_api.py -k group_assignment -q && node tests/universe_runtime.mjs`

Expected: FAIL because `group_assignment` is not present.

- [ ] **Step 3: Merge one assignment contract across APIs**

Do not duplicate resolution logic in Flask routes. `UniverseSnapshotService` and stock detail receive the same repository payload; unavailable assignment data returns a stable unavailable reason without breaking price charts.

- [ ] **Step 4: Render grouping context**

Extend the existing classification card rather than adding another full-width panel. Display broad sector, theme, primary model group, references, source/confidence, and conflict/review status with bilingual tooltips.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m pytest tests/test_web_universe_service.py tests/test_web_api.py -q && node tests/universe_runtime.mjs && node tests/model_outputs_runtime.mjs`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web/services/universe.py web/app.py web/static/js/app.js web/static/js/i18n.js web/templates/index.html tests
git commit -m "feat: show unified stock grouping context"
```

---

### Task 7: Full-Universe Assignment Audit and Database Migration

**Files:**
- Create: `audit_group_assignments.py`
- Modify: `tests/test_group_assignment_audit.py`
- Modify: `docs/modeling-todo.md`

**Interfaces:**
- Produces CLI JSON fields: `active_common_stocks`, `assigned`, `coverage`, `needs_review`, `invalid_benchmarks`, `theme_counts`, `conflicts`.
- Consumes current `data/research_prices.db`.

- [ ] **Step 1: Write failing CLI test**

```python
def test_audit_reports_complete_coverage(tmp_path):
    result = audit_database(tmp_path / "research.db", asof="2026-07-24")
    assert result["coverage"] == 1.0
    assert result["invalid_benchmarks"] == []
    assert "semiconductor" in result["theme_counts"]
```

- [ ] **Step 2: Run test and verify RED**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m pytest tests/test_group_assignment_audit.py -q`

Expected: FAIL because `audit_group_assignments.py` does not exist.

- [ ] **Step 3: Implement bounded audit output and nonzero failure exit**

`--strict` exits nonzero for coverage below 100%, invalid ETF references, overlapping effective ranges, or missing assignment rows. `unclassified_review` counts as an assigned explicit state but is reported separately.

- [ ] **Step 4: Rebuild or migrate the local research database**

Build a temporary database, run SQLite integrity checks and the strict assignment audit, then atomically replace `data/research_prices.db`. Preserve the prior database until all checks succeed.

- [ ] **Step 5: Run the real-universe audit**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python audit_group_assignments.py --database data/research_prices.db --strict
```

Expected: coverage `100.0%`, no invalid benchmark references, SNDK in semiconductor, and an explicit review count.

- [ ] **Step 6: Update the Chinese global TODO**

Mark the dynamic grouping foundation, automatic new-stock classification, SNDK migration, and UI context as complete. Record review-count reduction and additional theme taxonomies as follow-up work rather than hiding unresolved names.

- [ ] **Step 7: Commit**

```bash
git add audit_group_assignments.py tests/test_group_assignment_audit.py docs
git commit -m "feat: audit full-universe group coverage"
```

---

### Task 8: End-to-End Verification, Browser QA, and Service Restart

**Files:**
- Modify only if verification exposes a tested defect.

**Interfaces:**
- Verifies every output introduced by Tasks 1–7.

- [ ] **Step 1: Run formatting and repository checks**

Run: `git diff --check`

Expected: exit 0.

- [ ] **Step 2: Run the complete Python suite**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 3: Run all JavaScript runtime tests**

Run: `for test_file in tests/*_runtime.mjs; do node "$test_file"; done`

Expected: every runtime script exits 0.

- [ ] **Step 4: Verify assignment coverage**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python audit_group_assignments.py --database data/research_prices.db --strict`

Expected: 100% explicit assignment coverage and zero invalid benchmarks.

- [ ] **Step 5: Restart the local service and verify API**

Start the existing Flask command used by the repository, then verify `/api/stocks/SNDK` returns an available top-risk timeline and the expected assignment contract.

- [ ] **Step 6: Browser QA**

Open SNDK, verify the classification card, select “疑似派发 / 顶部向下风险”, hover 2026-06-26 and 2026-07-02, and confirm the chart displays observation then confirmation without layout movement.

- [ ] **Step 7: Final commit if verification required fixes**

Commit only tested verification fixes with a scoped message. Leave user-owned untracked database WAL/SHM and research files untouched.
