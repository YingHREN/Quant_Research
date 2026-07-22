# Quant Research Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-first Flask workstation for universe browsing, linked candlestick/volume analysis, extensible factor diagnostics, historical scenario bands, and resumable price updates.

**Architecture:** Thin Flask routes call focused market-data, analysis, scenario, factor-registry, and update-job services. The browser consumes stable JSON APIs and renders a single-page workstation with vendored Lightweight Charts; all factor results pass through a registry contract so later factors do not require page-structure changes.

**Tech Stack:** Python 3.9+, Flask 3.1, pandas 2.3, NumPy 2.0, SQLite, Python `unittest`, vanilla ES modules, Node `--check`, TradingView Lightweight Charts 5.x vendored locally.

## Global Constraints

- Bind the development server to `127.0.0.1` by default.
- Read prices from `data/prices.db`; do not require network access for dashboard viewing.
- Never expose API keys, absolute paths, tracebacks, or raw exceptions in JSON.
- Never label a factor, scenario, VCP state, or legacy rule score as a buy signal, success probability, target price, or validated prediction.
- Scenario provider name is `historical_distribution`; its 20/40/60-session bands are descriptive historical scenarios.
- Market-data updates are Tiingo price-only and must not call Finnhub or Alpha Vantage.
- Keep the existing Python test runner: `python -m unittest discover -s tests -v`.
- Do not introduce React, Vue, TypeScript, npm packages, or a Node build pipeline.

---

## File Map

- `web/app.py` — application factory and safe JSON routes only.
- `web/contracts.py` — shared dataclasses and JSON conversion helpers.
- `web/services/market_data.py` — parameterized SQLite reads and freshness summaries.
- `web/services/analysis.py` — point-in-time context and cached technical calculations.
- `web/services/scenarios.py` — non-overlapping historical scenario distributions.
- `web/services/update_jobs.py` — single-worker update state machine.
- `web/factors/base.py` — factor protocol and result types.
- `web/factors/registry.py` — registration, execution isolation, percentiles, and groups.
- `web/factors/builtin.py` — adapters for trend, momentum, structure, volume, risk, and legacy rules.
- `web/templates/index.html` — workstation shell and research disclaimers.
- `web/static/css/dashboard.css` — responsive layout and visual states.
- `web/static/js/api.js` — safe API client.
- `web/static/js/store.js` — selected ticker, filters, payload, and update state.
- `web/static/js/universe.js` — search/filter/sort and ticker selection.
- `web/static/js/charts.js` — linked price/volume charts, overlays, and crosshair details.
- `web/static/js/factors.js` — factor overview and extensible detail table.
- `web/static/js/scenarios.js` — 20/40/60-session scenario chart.
- `web/static/js/update.js` — update start/poll/refresh flow.
- `web/static/js/app.js` — page composition and event wiring.
- `web/static/vendor/lightweight-charts.standalone.production.js` — pinned local chart library.
- `web/static/vendor/LICENSE-lightweight-charts.txt` — dependency license.
- `tests/test_web_contracts.py` — serialization contracts.
- `tests/test_web_market_data.py` — SQLite and freshness behavior.
- `tests/test_web_factors.py` — registry and built-in factors.
- `tests/test_web_scenarios.py` — no-future and scenario quantiles.
- `tests/test_web_update_jobs.py` — state transitions and rate limits.
- `tests/test_web_api.py` — route schemas and safe failures.
- `tests/test_web_assets.py` — template/static contract and unsupported-copy scan.

---

### Task 1: Shared contracts and JSON safety

**Files:**
- Create: `web/__init__.py`
- Create: `web/contracts.py`
- Create: `tests/test_web_contracts.py`

**Interfaces:**
- Produces: `ErrorPayload(code: str, message: str)`, `json_safe(value: object) -> object`, and `iso_date(value) -> str | None`.
- Consumes: standard library, NumPy, and pandas scalar/date types.

- [ ] **Step 1: Write failing contract tests**

```python
import math
import unittest
import numpy as np
import pandas as pd
from web.contracts import ErrorPayload, iso_date, json_safe

class WebContractTest(unittest.TestCase):
    def test_json_safe_normalizes_numpy_dates_and_non_finite_values(self):
        value = {"n": np.int64(3), "x": np.float64(1.5), "bad": math.nan,
                 "date": pd.Timestamp("2026-07-21")}
        self.assertEqual(json_safe(value),
                         {"n": 3, "x": 1.5, "bad": None, "date": "2026-07-21"})

    def test_error_payload_has_stable_safe_shape(self):
        self.assertEqual(ErrorPayload("unknown_ticker", "Ticker not found").to_dict(),
                         {"error": {"code": "unknown_ticker", "message": "Ticker not found"}})

    def test_iso_date_accepts_none(self):
        self.assertIsNone(iso_date(None))
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_web_contracts -v`

Expected: import failure because `web.contracts` does not exist.

- [ ] **Step 3: Implement the contracts**

```python
@dataclass(frozen=True)
class ErrorPayload:
    code: str
    message: str
    def to_dict(self):
        return {"error": {"code": self.code, "message": self.message}}

def iso_date(value):
    return None if value is None else pd.Timestamp(value).date().isoformat()

def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return iso_date(value)
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
```

- [ ] **Step 4: Run the contract tests and the existing suite**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest tests.test_web_contracts -v`

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add web/__init__.py web/contracts.py tests/test_web_contracts.py
git commit -m "feat: add dashboard data contracts"
```

### Task 2: Local market-data repository

**Files:**
- Create: `web/services/__init__.py`
- Create: `web/services/market_data.py`
- Create: `tests/test_web_market_data.py`

**Interfaces:**
- Produces: `MarketDataRepository(db_path)`, `list_summaries() -> list[TickerSummary]`, `load_history(ticker, asof=None) -> DataFrame`, `freshness() -> dict`, and `UnknownTicker`.
- Consumes: `iso_date` from Task 1 and SQLite `prices(ticker,date,open,high,low,close,volume)`.

- [ ] **Step 1: Write failing repository tests using a temporary SQLite database**

```python
class MarketDataRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "prices.db"
        create_price_db(self.db, {
            "AAA": [("2026-07-20", 10, 11, 9, 10.5, 100),
                    ("2026-07-21", 10.5, 12, 10, 11.5, 150)],
            "BBB": [("2026-07-20", 20, 21, 19, 20.5, 200)],
        })
        self.repo = MarketDataRepository(self.db)

    def test_freshness_counts_dates_without_mixing_tickers(self):
        self.assertEqual(self.repo.freshness()["by_date"],
                         [{"date": "2026-07-21", "tickers": 1},
                          {"date": "2026-07-20", "tickers": 1}])

    def test_asof_truncates_future_rows(self):
        history = self.repo.load_history("AAA", "2026-07-20")
        self.assertEqual(history.index.max(), pd.Timestamp("2026-07-20"))

    def test_rejects_ticker_before_query(self):
        with self.assertRaises(InvalidTicker):
            self.repo.load_history("AAA' OR 1=1 --")
```

- [ ] **Step 2: Run and verify RED**

Run: `./venv/bin/python -m unittest tests.test_web_market_data -v`

Expected: import failure for `web.services.market_data`.

- [ ] **Step 3: Implement parameterized reads and freshness summaries**

Use `TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")`, short-lived `sqlite3.connect`, `params=(ticker,)`, explicit column aliases, and `pd.to_datetime` indexing. Mark a ticker inactive when its latest date is more than 20 calendar days behind the database-wide latest date; return the actual lag so the UI does not infer inactivity from a hidden rule.

- [ ] **Step 4: Verify repository tests**

Run: `./venv/bin/python -m unittest tests.test_web_market_data -v`

Expected: freshness, as-of, invalid-ticker, unknown-ticker, and inactive-state tests pass.

- [ ] **Step 5: Commit**

```bash
git add web/services tests/test_web_market_data.py
git commit -m "feat: add local dashboard market data repository"
```

### Task 3: Analysis context and extensible factor registry

**Files:**
- Create: `web/services/analysis.py`
- Create: `web/factors/__init__.py`
- Create: `web/factors/base.py`
- Create: `web/factors/registry.py`
- Create: `tests/test_web_factors.py`

**Interfaces:**
- Produces: `AnalysisContext`, `FactorDefinition`, `FactorResult`, `FactorRegistry.register()`, `FactorRegistry.evaluate_one()`, and `FactorRegistry.evaluate_universe()`.
- Consumes: stock and benchmark histories already truncated to the observation date.

- [ ] **Step 1: Write failing registry tests**

```python
class ConstantFactor:
    key, label, group, direction = "constant", "Constant", "test", "higher"
    description, version = "fixture", "v1"
    def compute(self, context): return 2.5
    def format(self, value): return f"{value:.1f}"

class BrokenFactor(ConstantFactor):
    key = "broken"
    def compute(self, context): raise RuntimeError("secret /tmp/path")

class FactorRegistryTest(unittest.TestCase):
    def test_duplicate_key_is_rejected(self):
        registry = FactorRegistry([ConstantFactor()])
        with self.assertRaises(DuplicateFactorKey): registry.register(ConstantFactor())

    def test_failure_is_isolated_and_message_is_safe(self):
        result = FactorRegistry([BrokenFactor()]).evaluate_one(BrokenFactor(), context())
        self.assertTrue(result.missing)
        self.assertEqual(result.missing_reason, "factor_error")

    def test_percentile_uses_same_observation_date_only(self):
        rows = registry.evaluate_universe([context("AAA", 1), context("BBB", 2)])
        self.assertEqual(rows["BBB"][0].percentile, 1.0)
```

- [ ] **Step 2: Run and verify RED**

Run: `./venv/bin/python -m unittest tests.test_web_factors -v`

Expected: missing factor modules.

- [ ] **Step 3: Implement protocol, results, and registry**

`FactorResult.to_dict()` returns `key`, `label`, `group`, `direction`, `raw_value`, `formatted`, `percentile`, `display_score`, `observation_date`, `missing`, `missing_reason`, `description`, and `version`. Registry error isolation stores only `factor_error`. Percentiles require at least five non-missing peers with the exact same observation date; otherwise `percentile=None`.

- [ ] **Step 4: Verify tests**

Run: `./venv/bin/python -m unittest tests.test_web_factors -v`

Expected: duplicate, isolation, percentile, insufficient-peer, and JSON-shape tests pass.

- [ ] **Step 5: Commit**

```bash
git add web/services/analysis.py web/factors tests/test_web_factors.py
git commit -m "feat: add extensible dashboard factor registry"
```

### Task 4: Built-in factors and chart-point calculations

**Files:**
- Create: `web/factors/builtin.py`
- Modify: `web/services/analysis.py`
- Extend: `tests/test_web_factors.py`

**Interfaces:**
- Produces: `build_default_registry()`, `build_chart_rows(context)`, and grouped built-ins for trend, momentum, structure, volume, risk, and legacy rules.
- Consumes: `factors.compute`, `research.momentum.momentum_features`, and the registry from Task 3.

- [ ] **Step 1: Add failing tests for point-in-time calculations and crosshair changes**

```python
def test_chart_rows_include_ohlcv_indicators_and_prior_changes(self):
    rows = build_chart_rows(context_from_history(price_history(260)))
    last = rows[-1]
    self.assertEqual(set(last), {"time", "open", "high", "low", "close", "volume",
        "daily_return", "true_range_pct", "volume_change", "volume_ma20", "volume_ratio",
        "ema20", "sma50", "sma200", "atr20", "pivot", "pivot_distance_pct",
        "crossed_ema20", "crossed_sma50"})
    self.assertNotIn("2026-07-22", [row["time"] for row in rows])
```

Also assert that strict VCP and tight-platform results expose rejection reasons, and that the legacy score label is exactly `Traditional rules score` with description containing `Not validated for prediction`.

- [ ] **Step 2: Run the targeted test and verify RED**

Run: `./venv/bin/python -m unittest tests.test_web_factors.BuiltinFactorTest -v`

Expected: `build_default_registry` and `build_chart_rows` are missing.

- [ ] **Step 3: Implement built-ins as adapters, not duplicated formulas**

Use existing `vcp_analysis`, `tight_platform`, `pivot_breakout`, `overheat`, `_atr`, and `momentum_features`. Cache shared results in `AnalysisContext.cache`. Compute EMA/SMA/ATR/volume series point in time. Never call `fetch()` from factor code.

- [ ] **Step 4: Verify factor tests and legacy contracts**

Run: `./venv/bin/python -m unittest tests.test_web_factors tests.test_legacy_scoring_contract -v`

Expected: all tests pass without changing legacy scoring behavior.

- [ ] **Step 5: Commit**

```bash
git add web/factors/builtin.py web/services/analysis.py tests/test_web_factors.py
git commit -m "feat: expose dashboard technical factors"
```

### Task 5: Historical scenario provider

**Files:**
- Create: `web/services/scenarios.py`
- Create: `tests/test_web_scenarios.py`

**Interfaces:**
- Produces: `HistoricalScenarioProvider(horizons=(20,40,60), quantiles=(.25,.5,.75))` and `build(history, asof) -> dict`.
- Consumes: adjusted close history truncated at `asof`.

- [ ] **Step 1: Write failing scenario tests**

```python
class HistoricalScenarioProviderTest(unittest.TestCase):
    def test_uses_non_overlapping_samples_and_no_future_bars(self):
        history = deterministic_history(320)
        result = HistoricalScenarioProvider().build(history, history.index[-61])
        self.assertEqual(result["provider"], "historical_distribution")
        self.assertLessEqual(result["observation_date"], history.index[-61].date().isoformat())
        self.assertTrue(result["horizons"]["20"]["non_overlapping"])

    def test_quantiles_are_ordered_and_start_at_observation_close(self):
        band = HistoricalScenarioProvider().build(deterministic_history(500), None)["horizons"]["20"]
        self.assertEqual(band["paths"]["pessimistic"][0]["return"], 0.0)
        for i in range(len(band["paths"]["median"])):
            self.assertLessEqual(band["paths"]["pessimistic"][i]["price"],
                                 band["paths"]["median"][i]["price"])
            self.assertLessEqual(band["paths"]["median"][i]["price"],
                                 band["paths"]["optimistic"][i]["price"])

    def test_missing_horizon_returns_reason(self):
        result = HistoricalScenarioProvider().build(deterministic_history(80), None)
        self.assertEqual(result["horizons"]["60"]["missing_reason"], "insufficient_samples")
```

- [ ] **Step 2: Run and verify RED**

Run: `./venv/bin/python -m unittest tests.test_web_scenarios -v`

Expected: missing scenario module.

- [ ] **Step 3: Implement non-overlapping horizon returns and deterministic paths**

For each horizon, sample endpoints backward in steps of that horizon, require at least eight samples, calculate 25/50/75 percentiles, cap absolute horizon returns at `3 * realized_vol_63 * sqrt(horizon / 252)`, and interpolate cumulative log return from day zero to the horizon. Return sample count, quantiles, cap, and methodology text.

- [ ] **Step 4: Verify scenario tests**

Run: `./venv/bin/python -m unittest tests.test_web_scenarios -v`

Expected: no-future, ordered-band, cap, deterministic, and missing-horizon tests pass.

- [ ] **Step 5: Commit**

```bash
git add web/services/scenarios.py tests/test_web_scenarios.py
git commit -m "feat: add historical scenario bands"
```

### Task 6: Resumable price-only update state machine

**Files:**
- Create: `web/services/update_jobs.py`
- Create: `tests/test_web_update_jobs.py`

**Interfaces:**
- Produces: `UpdateJobManager(repository, provider)`, `start() -> JobSnapshot`, `snapshot() -> JobSnapshot`, and `PriceProvider.fetch_history(ticker)`.
- Consumes: a provider injected by the application; tests use fakes and never use network.

- [ ] **Step 1: Write failing state-machine tests**

```python
class UpdateJobManagerTest(unittest.TestCase):
    def test_rejects_concurrent_start(self):
        manager = manager_with_blocking_provider()
        manager.start()
        with self.assertRaises(UpdateAlreadyRunning): manager.start()

    def test_rate_limit_preserves_progress_and_is_resumable(self):
        manager = manager_with_provider({"AAA": history(), "BBB": RateLimited("429")})
        manager.run_synchronously_for_test()
        snap = manager.snapshot().to_dict()
        self.assertEqual(snap["state"], "rate_limited")
        self.assertEqual(snap["updated"], 1)
        self.assertEqual(snap["current_ticker"], "BBB")
        self.assertTrue(snap["resumable"])
```

- [ ] **Step 2: Run and verify RED**

Run: `./venv/bin/python -m unittest tests.test_web_update_jobs -v`

Expected: missing update-job module.

- [ ] **Step 3: Implement the manager and Tiingo price-only adapter**

Use one daemon thread, a lock around state transitions, and independent SQLite commits through repository `upsert_history`. The production adapter calls `_tiingo_history` directly, not `fetch`, so it cannot request fundamentals. Convert HTTP 429 into `RateLimited`; redact all other exception text to `provider_error` for client snapshots while logging server-side.

- [ ] **Step 4: Verify update tests**

Run: `./venv/bin/python -m unittest tests.test_web_update_jobs -v`

Expected: completed, concurrent, partial, rate-limited, resume, and redaction tests pass.

- [ ] **Step 5: Commit**

```bash
git add web/services/update_jobs.py tests/test_web_update_jobs.py
git commit -m "feat: add resumable dashboard price updates"
```

### Task 7: Flask application factory and JSON APIs

**Files:**
- Rewrite: `web/app.py`
- Create: `tests/test_web_api.py`

**Interfaces:**
- Produces: `create_app(config=None, repository=None, update_manager=None) -> Flask` and the five routes in the design.
- Consumes: Tasks 1–6.

- [ ] **Step 1: Write failing Flask test-client tests**

```python
class WebApiTest(unittest.TestCase):
    def setUp(self):
        self.client = create_app({"TESTING": True}, fake_repository(), fake_manager()).test_client()

    def test_universe_schema(self):
        response = self.client.get("/api/universe")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json), {"asof", "freshness", "tickers", "factor_groups"})

    def test_stock_payload_has_one_consistent_observation_date(self):
        payload = self.client.get("/api/stocks/AAA").json
        self.assertEqual(set(payload), {"ticker", "observation_date", "summary", "chart",
                                       "structures", "factors", "scenarios", "warnings"})
        self.assertEqual(payload["chart"][-1]["time"], payload["observation_date"])

    def test_safe_unknown_ticker_error(self):
        response = self.client.get("/api/stocks/NOPE")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json["error"]["code"], "unknown_ticker")
        self.assertNotIn("/Users/", response.get_data(as_text=True))
```

- [ ] **Step 2: Run and verify RED**

Run: `./venv/bin/python -m unittest tests.test_web_api -v`

Expected: `create_app` is missing.

- [ ] **Step 3: Implement thin routes and safe error handlers**

Routes normalize ticker input through the repository, call service methods once, and wrap results with `json_safe`. `POST /api/update` returns 202, concurrent update returns 409, unknown ticker returns 404, invalid ticker returns 400, and unexpected failures return `internal_error` without exception text. Keep `app = create_app()` for `python web/app.py` compatibility and run with `host="127.0.0.1"`.

- [ ] **Step 4: Verify APIs and full Python suite**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -W error -m unittest discover -s tests -v`

Expected: all tests pass with no warnings.

- [ ] **Step 5: Commit**

```bash
git add web/app.py tests/test_web_api.py
git commit -m "feat: expose dashboard research APIs"
```

### Task 8: Workstation shell, universe panel, and local chart dependency

**Files:**
- Rewrite: `web/templates/index.html`
- Create: `web/static/css/dashboard.css`
- Create: `web/static/js/api.js`
- Create: `web/static/js/store.js`
- Create: `web/static/js/universe.js`
- Create: `web/static/js/app.js`
- Create: `web/static/vendor/lightweight-charts.standalone.production.js`
- Create: `web/static/vendor/LICENSE-lightweight-charts.txt`
- Create: `tests/test_web_assets.py`

**Interfaces:**
- Produces: `api.getUniverse()`, `api.getStock(ticker)`, central `store`, and `renderUniverse()`.
- Consumes: Task 7 API schemas.

- [ ] **Step 1: Write failing asset-contract tests**

```python
class WebAssetTest(unittest.TestCase):
    def test_page_has_workstation_regions_and_research_copy(self):
        html = Path("web/templates/index.html").read_text()
        for marker in ('id="universe-panel"', 'id="price-chart"', 'id="volume-chart"',
                       'id="factor-table"', 'id="scenario-chart"', 'Not validated for prediction'):
            self.assertIn(marker, html)

    def test_page_has_no_buy_signal_or_probability_copy(self):
        text = Path("web/templates/index.html").read_text()
        for banned in ("★ 买点", "上涨概率", "目标价"):
            self.assertNotIn(banned, text)

    def test_chart_library_is_local(self):
        html = Path("web/templates/index.html").read_text()
        self.assertIn("/static/vendor/lightweight-charts.standalone.production.js", html)
        self.assertNotIn("unpkg.com", html)
```

- [ ] **Step 2: Run and verify RED**

Run: `./venv/bin/python -m unittest tests.test_web_assets -v`

Expected: missing workstation regions and vendor asset.

- [ ] **Step 3: Vendor the pinned chart library and license**

Run during implementation with approved network access:

```bash
curl -fL https://unpkg.com/lightweight-charts@5.0.8/dist/lightweight-charts.standalone.production.js -o web/static/vendor/lightweight-charts.standalone.production.js
curl -fL https://raw.githubusercontent.com/tradingview/lightweight-charts/v5.0.8/LICENSE -o web/static/vendor/LICENSE-lightweight-charts.txt
```

Record the SHA-256 of both files in a comment in `tests/test_web_assets.py` and assert it so dependency changes are explicit.

- [ ] **Step 4: Implement shell, responsive two-column layout, API client, and universe interactions**

Use semantic buttons and status regions, escape all dynamic strings through `textContent`, store the selected ticker in `localStorage`, and implement pure `filterTickers(rows, query, filters)` and `sortTickers(rows, key, direction)` exports. The initial selection is the restored valid ticker or the first non-inactive ticker.

- [ ] **Step 5: Verify HTML contracts and JavaScript syntax**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_assets -v
node --check web/static/js/api.js
node --check web/static/js/store.js
node --check web/static/js/universe.js
node --check web/static/js/app.js
```

Expected: tests pass and each syntax check exits 0.

- [ ] **Step 6: Commit**

```bash
git add web/templates web/static tests/test_web_assets.py
git commit -m "feat: build dashboard stock pool workstation"
```

### Task 9: Linked candlestick and volume charts

**Files:**
- Create: `web/static/js/charts.js`
- Modify: `web/static/js/app.js`
- Modify: `web/templates/index.html`
- Modify: `web/static/css/dashboard.css`
- Extend: `tests/test_web_assets.py`

**Interfaces:**
- Produces: `createLinkedCharts(priceEl, volumeEl, detailEl)`, `setChartData(payload)`, `setRange(range)`, and `destroy()`.
- Consumes: `payload.chart` from Task 7 and global `LightweightCharts` from Task 8.

- [ ] **Step 1: Add failing static behavior-contract tests**

Assert that `charts.js` contains one candlestick series, one volume histogram, EMA20/SMA50/SMA200 line series, pivot price line, `subscribeCrosshairMove`, synchronized time-scale range handlers with a recursion guard, and detail fields for OHLC, return, true range, volume change, volume ratio, moving averages, ATR, and pivot distance.

- [ ] **Step 2: Run and verify RED**

Run: `./venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_linked_chart_contract -v`

Expected: `charts.js` missing.

- [ ] **Step 3: Implement linked charts and locked crosshair details**

Map stock rows to candlesticks and green/red volume items. Synchronize visible logical ranges in both directions with `syncing` guard. Crosshair selection finds the exact row by `param.time`; a click toggles locked/unlocked state. Locked details persist after pointer exit and include prior-session changes already computed by the server. Range buttons set 63, 126, 252, 504, or all bars.

- [ ] **Step 4: Verify static contracts and syntax**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_assets -v
node --check web/static/js/charts.js
```

Expected: all asset tests pass; Node exits 0.

- [ ] **Step 5: Commit**

```bash
git add web/static/js/charts.js web/static/js/app.js web/templates/index.html web/static/css/dashboard.css tests/test_web_assets.py
git commit -m "feat: add linked price and volume charts"
```

### Task 10: Factors, structures, scenarios, and update progress UI

**Files:**
- Create: `web/static/js/factors.js`
- Create: `web/static/js/scenarios.js`
- Create: `web/static/js/update.js`
- Modify: `web/static/js/app.js`
- Modify: `web/static/css/dashboard.css`
- Extend: `tests/test_web_assets.py`

**Interfaces:**
- Produces: `renderFactors(results)`, `renderStructures(structures)`, `renderScenarios(payload)`, and `createUpdateController()`.
- Consumes: Tasks 7–9.

- [ ] **Step 1: Add failing asset tests for extensibility and safety copy**

Assert that factor rows are generated from payload data rather than hard-coded keys, raw values remain visible, unknown groups fall back to an `Other` section, missing reasons render, scenario labels use `Pessimistic`, `Median`, and `Optimistic historical scenarios`, and update UI handles `rate_limited` with a resume action.

- [ ] **Step 2: Run and verify RED**

Run: `./venv/bin/python -m unittest tests.test_web_assets -v`

Expected: missing factor/scenario/update modules.

- [ ] **Step 3: Implement factor and structure rendering**

Build group cards entirely from registry metadata. The overview uses CSS bars/radar-like axes only for factors with numeric display scores; the detail table always shows label, formatted raw value, percentile with peer count, display score, date, description, version, and missing reason.

- [ ] **Step 4: Implement scenario rendering and update polling**

Scenario rendering draws three line series per available horizon and puts methodology/sample count beside the chart. Update polling uses a 1-second interval while running, stops on terminal states, refreshes the universe, and reloads the selected ticker only when its observation date changes. `rate_limited` is rendered as partial, resumable work—not completion.

- [ ] **Step 5: Verify all front-end modules**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_assets -v
for file in web/static/js/*.js; do node --check "$file"; done
```

Expected: asset tests pass and every JavaScript file exits 0.

- [ ] **Step 6: Commit**

```bash
git add web/static/js web/static/css/dashboard.css tests/test_web_assets.py
git commit -m "feat: render factors scenarios and update progress"
```

### Task 11: Integration verification, browser QA, and operating notes

**Files:**
- Create: `docs/dashboard.md`
- Modify only if verification exposes defects: files from Tasks 1–10 and their corresponding tests.

**Interfaces:**
- Produces: reproducible local run/update instructions and verified dashboard behavior.
- Consumes: complete dashboard.

- [ ] **Step 1: Write operating documentation**

Document:

```bash
cd /Users/renyinghao.1/Project/stock_screener
source env.sh
./venv/bin/python web/app.py
# Open http://127.0.0.1:5000
```

Explain local-only viewing, the update button's price-only behavior, rate-limit resumption, factor plug-in file locations, scenario methodology, and the project's no-alpha disclaimer.

- [ ] **Step 2: Run full fresh verification**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -W error -m unittest discover -s tests -v
for file in web/static/js/*.js; do node --check "$file"; done
./venv/bin/python -m py_compile web/app.py web/contracts.py web/services/*.py web/factors/*.py
git diff --check
```

Expected: all Python tests pass, every JavaScript syntax check exits 0, compilation exits 0, and `git diff --check` prints nothing.

- [ ] **Step 3: Verify local-only startup and API smoke tests**

Start `web/app.py` without Finnhub, Alpha Vantage, or Tiingo variables and verify:

```bash
curl -fsS http://127.0.0.1:5000/api/universe
curl -fsS http://127.0.0.1:5000/api/stocks/MSFT
```

Expected: both return JSON from the local database; no network request occurs.

- [ ] **Step 4: Perform browser QA**

Verify desktop and narrow widths, full-universe filtering, ticker switching, 3M/6M/1Y/2Y/all ranges, linked crosshairs, click-to-lock details, volume panel, factor missing states, scenario horizons, stale/inactive ticker status, and fake-provider update progress. Inspect the browser console and require zero uncaught errors.

- [ ] **Step 5: Scan for unsupported claims and secret leakage**

Run:

```bash
rg -n "★ 买点|上涨概率|目标价|胜率|buyable_now|env\.sh|TIINGO_API_KEY|FINNHUB_API_KEY" web docs/dashboard.md
```

Expected: no unsupported UI copy or secret value; documentation may mention environment variable names only in the security section.

- [ ] **Step 6: Commit the verified integration**

```bash
git add docs/dashboard.md
git commit -m "docs: add dashboard operating guide"
```

## Plan Self-Review

- Spec coverage: universe browsing, linked OHLCV charts, crosshair lock, extensible factors, historical scenarios, price-only updates, stale/inactive states, safe APIs, responsive UI, and verification each map to a task above.
- Placeholder scan: the plan contains no deferred implementation markers; every missing or error behavior has an explicit output.
- Type consistency: `AnalysisContext`, `FactorResult`, `FactorRegistry`, `HistoricalScenarioProvider`, `UpdateJobManager`, and the API payload keys remain consistent between producer and consumer tasks.
