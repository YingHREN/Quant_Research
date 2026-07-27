# Intraday Live Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users persist up to 27 Alpaca IEX realtime stock subscriptions and inspect a selected stock's corrected one-minute trades, quote and buy/sell pressure in the dashboard.

**Architecture:** SQLite is the cross-process control and event boundary: Flask atomically writes a revisioned desired-symbol set, while the collector polls revisions and hot-updates its existing WebSocket. A read-only aggregation service converts effective trades and latest quotes into bounded minute snapshots; a focused browser controller owns subscription state, polling and rendering without changing the daily chart.

**Tech Stack:** Python 3.9, Flask, SQLite/WAL, asyncio, vanilla ES modules, existing dashboard CSS and `unittest`/Node checks.

## Global Constraints

- `SPY`, `QQQ`, and `SOXX` are fixed subscriptions; users may select at most 27 additional symbols.
- Alpaca credentials remain server-side and never appear in an HTTP response.
- IEX output is labelled partial-market evidence and cannot override Ridge or final forecast policy.
- Unknown trade direction remains unknown; it is never forced into buy or sell volume.
- The realtime panel has fixed layout boundaries and cannot resize, pan, or mutate the existing daily charts.
- All stored and HTTP timestamps are UTC-aware ISO 8601 values.

---

### Task 1: Revisioned subscription control

**Files:**
- Modify: `marketdata/storage.py`
- Create: `web/services/intraday_subscriptions.py`
- Test: `tests/test_intraday_subscription_control.py`

**Interfaces:**
- Produces: `IntradayStore.read_subscription_request() -> dict`
- Produces: `IntradayStore.replace_subscription_request(symbols, updated_at) -> dict`
- Produces: `IntradaySubscriptionService.snapshot() -> dict`
- Produces: `IntradaySubscriptionService.replace(symbols) -> dict`

- [ ] **Step 1: Write failing persistence and service tests**

Cover initial empty state, normalization/deduplication, persistence across store instances,
monotonic revision, fixed-symbol exclusion, 27-symbol limit, and requested/confirmed/pending
status composition.

- [ ] **Step 2: Run the focused test and verify missing interfaces fail**

Run: `../../venv/bin/python -m unittest tests.test_intraday_subscription_control -v`

Expected: FAIL because the service and store methods do not exist.

- [ ] **Step 3: Add the control table and atomic store methods**

Add a singleton `intraday_subscription_control` table with `revision`, JSON
`user_symbols`, and `updated_at`. `replace_subscription_request()` validates normalized
symbols before `BEGIN IMMEDIATE`, increments revision once, and returns the committed record.

- [ ] **Step 4: Add the subscription service**

Use constants `FIXED_SYMBOLS = ("SPY", "QQQ", "SOXX")`,
`MAX_TOTAL_SYMBOLS = 30`, and `MAX_USER_SYMBOLS = 27`. Compose collector status without
claiming requested symbols are confirmed.

- [ ] **Step 5: Run focused and existing storage tests**

Run: `../../venv/bin/python -m unittest tests.test_intraday_subscription_control tests.test_marketdata_storage -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add marketdata/storage.py web/services/intraday_subscriptions.py tests/test_intraday_subscription_control.py
git commit -m "feat: persist realtime subscription choices"
```

### Task 2: Collector cross-process hot updates

**Files:**
- Modify: `marketdata/collector.py`
- Modify: `collect_intraday.py`
- Test: `tests/test_marketdata_collector.py`
- Test: `tests/test_collect_intraday.py`

**Interfaces:**
- Consumes: `IntradayStore.read_subscription_request()`
- Produces: collector option `subscription_poll_interval=1.0`
- Produces: background `_subscription_control_loop()` that calls existing `set_selection()`

- [ ] **Step 1: Add failing collector polling tests**

Use an in-memory fake store whose revision changes during a running collector. Assert the
provider receives the fixed symbols plus new user symbols without restarting, identical
revisions do not resubscribe, and poll failures preserve the active subscription.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `../../venv/bin/python -m unittest tests.test_marketdata_collector tests.test_collect_intraday -v`

Expected: FAIL because the polling option and worker are absent.

- [ ] **Step 3: Implement the polling worker**

Start it with the heartbeat/writer workers, stop it during cleanup, and use the existing
`set_selection(selected, peers, candidates)` path. The control-table user symbols are passed
as candidates; fixed references continue to come from `build_pool()`.

- [ ] **Step 4: Make CLI startup honor persisted choices**

After `store.initialize()`, seed the collector from the persisted user symbols while retaining
CLI `--selected`, `--peer`, and `--candidate` inputs as higher-priority additions.

- [ ] **Step 5: Run focused tests**

Run: `../../venv/bin/python -m unittest tests.test_marketdata_collector tests.test_collect_intraday tests.test_marketdata_subscriptions -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add marketdata/collector.py collect_intraday.py tests/test_marketdata_collector.py tests/test_collect_intraday.py
git commit -m "feat: hot reload realtime subscriptions"
```

### Task 3: Corrected minute aggregation and HTTP API

**Files:**
- Modify: `marketdata/storage.py`
- Modify: `web/services/intraday.py`
- Modify: `web/app.py`
- Create: `tests/test_web_intraday_live.py`

**Interfaces:**
- Produces: `IntradayStore.read_latest_quote(provider, symbol) -> QuoteEvent | None`
- Produces: `IntradaySnapshotService.snapshot(ticker, window_minutes=120) -> dict`
- Produces: `GET /api/market-data/subscriptions`
- Produces: `PUT /api/market-data/subscriptions`
- Produces: `GET /api/intraday/<ticker>?window=120`

- [ ] **Step 1: Write failing aggregation and route tests**

Create trades spanning two minutes with buy, sell and unknown direction, then add a correction
and cancel. Assert OHLCV and directional volumes use only effective trades. Cover latest quote,
limit errors, invalid JSON, bounded window, not-subscribed, pending, stale and live states.

- [ ] **Step 2: Run focused test and verify failure**

Run: `../../venv/bin/python -m unittest tests.test_web_intraday_live -v`

Expected: FAIL because the snapshot service and routes are absent.

- [ ] **Step 3: Add bounded read methods and aggregation**

Filter effective trades to the selected symbol, current/latest trading date and requested
window. Emit minute rows with `time`, `open`, `high`, `low`, `close`, `volume`, `buy_volume`,
`sell_volume`, `unknown_volume`, and `delta`. Compute pressure only when directed volume is
nonzero and include `direction_coverage`.

- [ ] **Step 4: Wire Flask services and routes**

Initialize both services from the same `IntradayStore`. PUT requires a JSON object whose
`symbols` field is an array. Convert validation failures to stable 400/409 envelopes without
returning database paths or provider messages.

- [ ] **Step 5: Run API, storage and regression tests**

Run: `../../venv/bin/python -m unittest tests.test_web_intraday_live tests.test_web_intraday_status tests.test_marketdata_storage tests.test_web_api -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add marketdata/storage.py web/services/intraday.py web/app.py tests/test_web_intraday_live.py
git commit -m "feat: expose corrected intraday minute snapshots"
```

### Task 4: Browser subscription controller

**Files:**
- Modify: `web/static/js/api.js`
- Modify: `web/static/js/universe.js`
- Create: `web/static/js/intraday-live.js`
- Create: `tests/test_web_intraday_assets.py`

**Interfaces:**
- Produces: `api.getIntradaySubscriptions()`
- Produces: `api.replaceIntradaySubscriptions(symbols)`
- Produces: `api.getIntradaySnapshot(ticker, windowMinutes)`
- Produces: `createIntradayLiveController(options)`
- Consumes: `renderUniverse(..., { realtimeSymbols, onRealtimeToggle })`

- [ ] **Step 1: Write failing Node-backed asset tests**

Assert API methods use the intended methods/paths, subscription toggles stop row selection
propagation, the controller polls only a visible subscribed ticker, and `destroy()` clears all
timers/listeners.

- [ ] **Step 2: Run the focused test and verify failure**

Run: `../../venv/bin/python -m unittest tests.test_web_intraday_assets -v`

Expected: FAIL because the module and APIs are absent.

- [ ] **Step 3: Add API calls and safe row toggles**

Build controls with `createElement`, `textContent`, `aria-pressed` and a translated label.
Never use `innerHTML`. Disable the toggle while the atomic replacement request is pending.

- [ ] **Step 4: Implement lifecycle and polling**

The controller owns subscription payload, selected ticker, visibility listener, one timeout and
an abort/generation guard. Poll every 2 seconds only when selected, subscribed and visible.
Retain last valid snapshot when a later poll fails.

- [ ] **Step 5: Run asset tests and syntax checks**

Run: `../../venv/bin/python -m unittest tests.test_web_intraday_assets tests.test_web_assets -v`

Run: `node --check web/static/js/intraday-live.js && node --check web/static/js/api.js && node --check web/static/js/universe.js`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web/static/js/api.js web/static/js/universe.js web/static/js/intraday-live.js tests/test_web_intraday_assets.py
git commit -m "feat: add realtime subscription browser controller"
```

### Task 5: Dashboard panel, styles and localization

**Files:**
- Modify: `web/templates/index.html`
- Modify: `web/static/css/dashboard.css`
- Modify: `web/static/js/app.js`
- Modify: `web/static/js/i18n.js`
- Modify: `web/static/js/intraday-live.js`
- Modify: `tests/test_web_intraday_assets.py`

**Interfaces:**
- Consumes: `createIntradayLiveController(options)`
- Produces: fixed-size subscription summary and realtime card DOM regions

- [ ] **Step 1: Extend failing asset tests for required regions and translations**

Require IDs for subscription status/list/capacity, selected-stock realtime toggle, quote cards,
minute price SVG, minute volume SVG, pressure bar and limitation text. Require every key in
both locales and assert no raw secret/key strings are present.

- [ ] **Step 2: Add semantic markup and fixed layout**

Place the compact subscription manager in the universe panel and the realtime card between the
security header and daily chart. Give SVGs fixed `viewBox` dimensions and containers fixed
minimum heights so polling cannot move the daily chart.

- [ ] **Step 3: Render snapshots without mutating daily charts**

Render price and volume polylines/bars into the realtime card's own SVG elements. Update quote,
spread, volume, Delta, pressure, coverage and state text through `textContent` and attributes.

- [ ] **Step 4: Integrate controller with app lifecycle**

Initialize after element capture, pass current selection after universe/stock changes, repaint
row toggles after subscription changes, rerender on locale changes, and call `destroy()` on
`pagehide`.

- [ ] **Step 5: Add Chinese and English copy**

Include live/pending/stale/closed/disconnected/unsubscribed states, capacity, fixed references,
trade/quote fields, pressure/coverage labels and the IEX partial-market disclaimer.

- [ ] **Step 6: Run frontend and related API tests**

Run: `../../venv/bin/python -m unittest tests.test_web_intraday_assets tests.test_web_assets tests.test_web_api -v`

Run: `node --check web/static/js/app.js && node --check web/static/js/intraday-live.js && node --check web/static/js/i18n.js`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add web/templates/index.html web/static/css/dashboard.css web/static/js/app.js web/static/js/i18n.js web/static/js/intraday-live.js tests/test_web_intraday_assets.py
git commit -m "feat: render intraday live dashboard"
```

### Task 6: End-to-end verification and TODO completion

**Files:**
- Modify: `docs/modeling-todo.md`

**Interfaces:**
- Verifies all interfaces from Tasks 1–5.

- [ ] **Step 1: Run all focused intraday tests**

Run: `../../venv/bin/python -m unittest tests.test_intraday_subscription_control tests.test_web_intraday_live tests.test_web_intraday_assets tests.test_collect_intraday tests.test_marketdata_collector tests.test_marketdata_storage tests.test_marketdata_subscriptions tests.test_marketdata_contracts tests.test_web_intraday_status -v`

Expected: PASS.

- [ ] **Step 2: Run the full regression suite**

Run: `../../venv/bin/python -m unittest discover -s tests -v`

Expected: PASS; if unrelated existing failures occur, record exact names and verify the focused
suite remains green.

- [ ] **Step 3: Perform local browser QA**

Start Flask and the collector with existing local environment credentials. Verify adding and
removing one stock, requested-to-confirmed transition, realtime updates, hidden-tab polling
pause, Chinese/English labels, narrow layout, and no daily-chart movement.

- [ ] **Step 4: Update global TODO from evidence**

Mark only the INTRA-002 checklist items proven by tests and browser QA as complete. Leave
Lee–Ready refinement, full-market order flow and model integration under INTRA-001 incomplete.

- [ ] **Step 5: Commit**

```bash
git add docs/modeling-todo.md
git commit -m "docs: record intraday dashboard completion"
```
