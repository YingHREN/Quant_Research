# TOPRISK-001 Chart UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose cached TOPRISK-001 state transitions as an optional historical chart layer and show the latest state as a compact stock-header badge.

**Architecture:** Add a pure timeline serializer that converts the existing revision-wide `risk_context` DataFrame into a latest summary plus sparse state-transition events. `ForecastService` exposes that serializer through the same artifact lifecycle used by Ridge forecasts, Flask merges the events into the existing annotation protocol, and the browser renders them through the existing marker-layer selector without adding price lines or future time points.

**Tech Stack:** Python 3, pandas, Flask, `unittest`, browser-native ES modules, Lightweight Charts, Node.js runtime contract tests.

## Global Constraints

- Reuse the existing persisted forecast `risk_context`; do not retrain Ridge or run a second TOPRISK calculation for chart markers.
- Emit only transitions into watch, high, confirmed, and recovery; do not emit a marker for each remembered or fading session.
- Historical events must be causal and prefix-invariant.
- The `top_risk` chart layer is disabled by default, included in “all,” and excluded from “core.”
- Markers must use existing trading dates and must not create price lines, future time points, or layout padding.
- TOPRISK timeline failure must not fail the stock-detail endpoint.
- Keep terminology directional: “顶部向下风险观察/高风险/确认/解除.”
- Daily OHLCV evidence is a suspected-distribution proxy, not proof of institutional identity or aggressor-side trading.

---

### Task 1: Pure TOPRISK Timeline Serializer

**Files:**
- Create: `web/services/top_risk_timeline.py`
- Create: `tests/test_top_risk_timeline.py`

**Interfaces:**
- Consumes: `risk_context: pandas.DataFrame`, `ticker: str`, and `chart_dates: Iterable[str | pandas.Timestamp]`.
- Produces: `build_top_risk_timeline(risk_context, ticker, chart_dates) -> dict` with `model_key`, `model_version`, `status`, `latest`, and `events`.
- Produces: `unavailable_top_risk_timeline(reason: str = "not_available") -> dict`.

- [ ] **Step 1: Write failing tests for transition compression and latest summary**

```python
def test_emits_each_top_risk_transition_once(self):
    context = risk_frame(
        states=["inactive", "watch", "watch", "high", "fading", "confirmed"],
        recoveries=[False, False, False, False, False, False],
    )
    result = build_top_risk_timeline(
        context, "NBIS", pd.date_range("2026-06-01", periods=6, freq="B")
    )
    self.assertEqual(
        [event["type"] for event in result["events"]],
        ["top_risk_watch", "top_risk_high", "top_risk_confirmed"],
    )
    self.assertEqual(result["latest"]["state"], "confirmed")
    self.assertEqual(result["status"], "available")

def test_recovery_is_emitted_once_and_fading_is_not_an_event(self):
    context = risk_frame(
        states=["high", "fading", "inactive", "inactive"],
        recoveries=[False, False, True, False],
    )
    result = build_top_risk_timeline(context, "NBIS", chart_dates(context))
    self.assertEqual(
        [event["type"] for event in result["events"]],
        ["top_risk_high", "top_risk_recovery"],
    )
```

- [ ] **Step 2: Run the serializer tests and verify RED**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/top-risk-pycache ./venv/bin/python -m unittest tests.test_top_risk_timeline -v`

Expected: FAIL because `web.services.top_risk_timeline` does not exist.

- [ ] **Step 3: Implement minimal typed serialization**

```python
MODEL_KEY = "high_level_distribution_risk_v1"
MODEL_VERSION = "v1"
EVENT_BY_STATE = {
    "watch": "top_risk_watch",
    "high": "top_risk_high",
    "confirmed": "top_risk_confirmed",
}

def build_top_risk_timeline(risk_context, ticker, chart_dates):
    dates = pd.DatetimeIndex(pd.to_datetime(tuple(chart_dates))).normalize()
    selected = _ticker_rows(risk_context, ticker).reindex(dates)
    available = selected[
        selected["high_level_distribution_state"].isin(
            {"inactive", "low", "watch", "high", "confirmed", "fading"}
        )
    ]
    if available.empty:
        return unavailable_top_risk_timeline()
    events = _transition_events(available)
    latest = _latest_summary(available.iloc[-1], available.index[-1])
    return {
        "model_key": MODEL_KEY,
        "model_version": MODEL_VERSION,
        "status": "available",
        "latest": latest,
        "events": events,
    }
```

Implementation validation must reject duplicate ticker-date keys, normalize ticker casing, convert non-finite scores and ages to `None`, filter events outside `chart_dates`, and return unavailable for missing columns/ticker/date coverage.

- [ ] **Step 4: Add prefix-invariance, unavailable, duplicate-key, and date-filter tests**

```python
def test_appending_future_rows_does_not_change_existing_events(self):
    before = build_top_risk_timeline(self.context.iloc[:-2], "NBIS", self.dates[:-2])
    after = build_top_risk_timeline(self.context, "NBIS", self.dates)
    cutoff = self.dates[-3].date().isoformat()
    self.assertEqual(
        before["events"],
        [event for event in after["events"] if event["time"] <= cutoff],
    )
```

- [ ] **Step 5: Run serializer tests and verify GREEN**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/top-risk-pycache ./venv/bin/python -m unittest tests.test_top_risk_timeline -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit the serializer**

```bash
git add web/services/top_risk_timeline.py tests/test_top_risk_timeline.py
git commit -m "feat: serialize cached TOPRISK timeline"
```

---

### Task 2: Forecast Artifact Timeline Access

**Files:**
- Modify: `web/services/forecasts.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Consumes: Task 1 `build_top_risk_timeline(...)`.
- Produces: `ForecastService.build_top_risk_timeline(ticker, chart_dates, histories, *, expected_revision=None) -> dict`.

- [ ] **Step 1: Write failing service tests**

```python
def test_top_risk_timeline_reuses_revision_artifact(self):
    service = ForecastService(provider_factory=self.factory)
    with patch(
        "web.services.forecasts.build_forecast_risk_context",
        wraps=build_forecast_risk_context,
    ) as build_risk:
        first = service.build_top_risk_timeline(
            "NBIS", self.dates, self.histories
        )
        second = service.build_top_risk_timeline(
            "NBIS", self.dates, self.histories
        )
    self.assertEqual(build_risk.call_count, 1)
    self.assertEqual(first, second)

def test_top_risk_timeline_checks_expected_revision(self):
    service = ForecastService(provider_factory=self.factory)
    with self.assertRaises(ForecastRevisionChanged):
        service.build_top_risk_timeline(
            "NBIS", self.dates, self.histories, expected_revision=99
        )
```

- [ ] **Step 2: Run focused service tests and verify RED**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/top-risk-pycache ./venv/bin/python -m unittest tests.test_web_api.ForecastServiceTest -v`

Expected: new tests FAIL because `ForecastService.build_top_risk_timeline` is missing.

- [ ] **Step 3: Implement the service method using `_revision_artifacts`**

```python
def build_top_risk_timeline(
    self, ticker, chart_dates, histories, *, expected_revision=None
):
    ticker = _required_identity_value(ticker, "ticker")
    dates = _chart_dates(chart_dates)
    coverage, fingerprints = _history_snapshot_metadata(histories)
    with self._lock:
        self._check_expected_revision(expected_revision)
        _frame, _provider, _evaluations, risk_context = (
            self._revision_artifacts(histories, coverage, fingerprints)
        )
        return build_top_risk_timeline(risk_context, ticker, dates)
```

Use an import alias such as `serialize_top_risk_timeline` to avoid the method/function name collision. Apply the same histories mapping validation and requested-ticker validation used by `build()`.

- [ ] **Step 4: Verify persistent artifact restoration does not rebuild risk context**

Add a test that saves an artifact with one service, constructs a second service using the same `ForecastArtifactStore`, patches `build_forecast_risk_context` to raise, and asserts `build_top_risk_timeline()` still succeeds.

- [ ] **Step 5: Run forecast service tests and verify GREEN**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/top-risk-pycache ./venv/bin/python -m unittest tests.test_web_api.ForecastServiceTest tests.test_web_performance_contract -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit artifact access**

```bash
git add web/services/forecasts.py tests/test_web_api.py
git commit -m "feat: expose cached TOPRISK timeline"
```

---

### Task 3: Stock API Contract and Annotation Merge

**Files:**
- Modify: `web/app.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `forecast_service.build_top_risk_timeline(...)` when callable.
- Produces: stock payload field `top_risk`.
- Produces: TOPRISK events appended to `structures.annotations`.

- [ ] **Step 1: Add failing stock API tests for available and legacy services**

```python
def test_stock_payload_includes_top_risk_summary_and_annotations(self):
    service = InjectedForecastService()
    service.build_top_risk_timeline = lambda *args, **kwargs: {
        "model_key": "high_level_distribution_risk_v1",
        "model_version": "v1",
        "status": "available",
        "latest": {
            "time": "2026-07-23", "score": 72.0,
            "state": "confirmed", "raw_state": "confirmed",
            "memory_age_sessions": 0,
        },
        "events": [{
            "time": "2026-07-01", "type": "top_risk_confirmed",
            "score": 72.0, "state": "confirmed",
        }],
    }
    payload = self.client_with_service(service).get("/api/stocks/NBIS").get_json()
    self.assertEqual(payload["top_risk"]["latest"]["state"], "confirmed")
    self.assertIn(
        "top_risk_confirmed",
        [row["type"] for row in payload["structures"]["annotations"]],
    )

def test_legacy_forecast_service_keeps_stock_endpoint_available(self):
    payload = self.client_with_service(InjectedForecastService()).get(
        "/api/stocks/NBIS"
    ).get_json()
    self.assertEqual(payload["top_risk"]["status"], "unavailable")
```

- [ ] **Step 2: Run the API tests and verify RED**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/top-risk-pycache ./venv/bin/python -m unittest tests.test_web_api.WebApiTest -v`

Expected: new assertions FAIL because `top_risk` and its annotations are absent.

- [ ] **Step 3: Add a failure-isolated Web adapter**

```python
def _top_risk_payload(service, arguments, expected_revision):
    builder = getattr(service, "build_top_risk_timeline", None)
    if not callable(builder):
        return unavailable_top_risk_timeline("service_unsupported")
    try:
        if expected_revision is None:
            return builder(*arguments)
        return builder(*arguments, expected_revision=expected_revision)
    except ForecastRevisionChanged:
        return unavailable_top_risk_timeline("update_in_progress")
    except Exception:
        return unavailable_top_risk_timeline("model_error")
```

Call it after the normal forecast build, merge only known event types whose date exists in `chart`, and include the timeline summary at payload key `top_risk`.

- [ ] **Step 4: Add tests for unknown event types, non-chart dates, and timeline exceptions**

Assert invalid events are filtered, an exception yields `status == "unavailable"`, and the forecast payload remains present.

- [ ] **Step 5: Run API tests and verify GREEN**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/top-risk-pycache ./venv/bin/python -m unittest tests.test_web_api -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit the API contract**

```bash
git add web/app.py tests/test_web_api.py
git commit -m "feat: add TOPRISK events to stock API"
```

---

### Task 4: Optional TOPRISK Chart Layer

**Files:**
- Modify: `web/static/js/marker_layers.js`
- Modify: `web/static/js/charts.js`
- Modify: `web/static/js/i18n.js`
- Modify: `web/templates/index.html`
- Modify: `tests/marker_layers_runtime.mjs`
- Modify: `tests/dashboard_runtime.mjs`
- Modify: `tests/test_web_assets.py`

**Interfaces:**
- Consumes: `structures.annotations` event types from Task 3.
- Produces: marker layer key `top_risk` and four localized chart markers.

- [ ] **Step 1: Add failing layer-definition and HTML contract tests**

Update the runtime expectation to ten layers and assert:

```javascript
assert.equal(MARKER_LAYER_DEFINITIONS.length, 10);
assert.ok(MARKER_LAYER_PRESETS.all.includes("top_risk"));
assert.ok(!MARKER_LAYER_PRESETS.core.includes("top_risk"));
```

Update `test_page_has_chart_marker_layer_controls` to require `data-marker-layer="top_risk"` and update the dashboard count expectation from `1/9` to `1/10`.

- [ ] **Step 2: Run asset tests and verify RED**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/top-risk-pycache ./venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_page_has_chart_marker_layer_controls tests.test_web_assets.WebAssetTest.test_dashboard_persists_chart_marker_layers -v`

Expected: FAIL because the tenth layer is absent.

- [ ] **Step 3: Add the disabled-by-default layer control**

```javascript
Object.freeze({ key: "top_risk", core: false }),
```

```html
<label>
  <input type="checkbox" data-marker-layer="top_risk">
  <span data-i18n="chart.layers.top_risk">High-level distribution / top risk</span>
</label>
```

Change the static fallback summary to `3/10`.

- [ ] **Step 4: Add failing marker rendering assertions**

Extend the dashboard runtime stock payload with four TOPRISK annotations, select only `top_risk`, and assert the marker texts are:

```javascript
[
  "顶部向下风险观察",
  "顶部向下高风险",
  "顶部向下风险确认",
  "顶部向下风险解除",
]
```

Then switch locale and assert the English equivalents.

- [ ] **Step 5: Add marker styles, layer mappings, and bilingual strings**

```javascript
top_risk_watch: Object.freeze({
  position: "aboveBar", shape: "circle", color: COLORS.warning,
}),
top_risk_high: Object.freeze({
  position: "aboveBar", shape: "arrowDown", color: COLORS.topRiskHigh,
}),
top_risk_confirmed: Object.freeze({
  position: "aboveBar", shape: "arrowDown", color: COLORS.down,
}),
top_risk_recovery: Object.freeze({
  position: "belowBar", shape: "arrowUp", color: COLORS.up,
}),
```

Map all four types to `"top_risk"`. Define explicit Chinese and English `chart.shape.*` and `chart.layers.top_risk` strings.

- [ ] **Step 6: Run marker and asset tests and verify GREEN**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/top-risk-pycache ./venv/bin/python -m unittest tests.test_web_assets -v`

Expected: all tests PASS.

- [ ] **Step 7: Commit the chart layer**

```bash
git add web/static/js/marker_layers.js web/static/js/charts.js web/static/js/i18n.js web/templates/index.html tests/marker_layers_runtime.mjs tests/dashboard_runtime.mjs tests/test_web_assets.py
git commit -m "feat: add optional TOPRISK chart markers"
```

---

### Task 5: Latest TOPRISK Header Badge

**Files:**
- Modify: `web/templates/index.html`
- Modify: `web/static/js/app.js`
- Modify: `web/static/js/i18n.js`
- Modify: `web/static/css/dashboard.css`
- Modify: `tests/dashboard_runtime.mjs`
- Modify: `tests/test_web_assets.py`

**Interfaces:**
- Consumes: payload `top_risk.latest` from Task 3.
- Produces: `renderTopRiskBadge(payload, element, locale)` behavior and `#top-risk-state`.

- [ ] **Step 1: Add failing DOM and runtime tests**

Require `id="top-risk-state"` in HTML. In the dashboard runtime, load payloads with `confirmed`, `fading`, and unavailable TOPRISK states and assert:

```javascript
assert.equal(topRiskBadge.textContent, "顶部风险 72 · 已确认");
assert.equal(topRiskBadge.dataset.tone, "confirmed");
assert.equal(fadingBadge.textContent, "顶部风险 48 · 风险衰减中");
assert.equal(unavailableBadge.textContent, "顶部风险不可用");
```

- [ ] **Step 2: Run focused badge tests and verify RED**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/top-risk-pycache ./venv/bin/python -m unittest tests.test_web_assets.WebAssetTest -v`

Expected: new badge assertions FAIL because the element and renderer are absent.

- [ ] **Step 3: Implement stable badge rendering**

Add the badge next to `security-state`, capture it in `app.js`, and render:

```javascript
function renderTopRiskBadge(topRisk, element, locale) {
  const latest = topRisk?.status === "available" ? topRisk.latest : null;
  if (!latest || !Number.isFinite(latest.score)) {
    element.textContent = t("topRisk.badge.unavailable", {}, locale);
    element.dataset.tone = "unavailable";
    return;
  }
  element.textContent = t(
    "topRisk.badge.value",
    {
      score: Math.round(latest.score),
      state: t(`topRisk.state.${latest.state}`, {}, locale),
    },
    locale,
  );
  element.dataset.tone = latest.state;
}
```

Reset the badge during loading/error/empty states. Add fixed single-line badge styles and tone colors without changing the header grid height.

- [ ] **Step 4: Run dashboard and asset tests and verify GREEN**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/top-risk-pycache ./venv/bin/python -m unittest tests.test_web_assets -v`

Expected: all tests PASS in Chinese and English.

- [ ] **Step 5: Commit the header badge**

```bash
git add web/templates/index.html web/static/js/app.js web/static/js/i18n.js web/static/css/dashboard.css tests/dashboard_runtime.mjs tests/test_web_assets.py
git commit -m "feat: show latest TOPRISK state badge"
```

---

### Task 6: TODO Closure, Regression Suite, and Browser Verification

**Files:**
- Modify: `docs/modeling-todo.md`
- Modify: `docs/superpowers/specs/2026-07-26-top-risk-chart-ui-design.md`

**Interfaces:**
- Consumes: all prior task outputs.
- Produces: completed TOPRISK UI checklist and verified feature branch.

- [ ] **Step 1: Run focused Python and browser-runtime regression suites**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/top-risk-pycache ./venv/bin/python -m unittest \
  tests.test_top_risk_timeline \
  tests.test_web_api \
  tests.test_web_forecast_decision \
  tests.test_web_model_outputs \
  tests.test_web_assets -v
```

Expected: all tests PASS with no errors or warnings.

- [ ] **Step 2: Run the complete committed test suite**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/top-risk-pycache ./venv/bin/python -m unittest discover -s tests -v`

Expected: all committed tests PASS. Untracked tests from other worktrees or concurrent work are not part of this branch and must not be copied into it.

- [ ] **Step 3: Update the global TODO and design status**

Mark this existing TOPRISK item complete:

```markdown
- [x] 在历史 K 线上以可关闭且不参与价格轴缩放的事件标记展示首次观察、高风险、确认和解除日期；不得绘制会改变拖拽或日期锁定行为的未来延伸线。
```

Add a completed line for the latest TOPRISK header badge and change the design document status from `待书面确认` to `已实现`.

- [ ] **Step 4: Start the isolated-worktree service and verify real stocks**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/top-risk-pycache ./venv/bin/python -m web.app
```

Use the in-app browser at `http://127.0.0.1:5000/` and verify NBIS, MU, and MRVL:

- TOPRISK is off on first load;
- enabling it adds sparse transition markers;
- disabling it removes only TOPRISK markers;
- selecting “all” includes the markers;
- title badge matches the latest lower model card;
- dragging, zooming, hover, and locked-date scrolling retain the same visible range;
- no marker date exists outside the price rows.

- [ ] **Step 5: Commit documentation closure**

```bash
git add docs/modeling-todo.md docs/superpowers/specs/2026-07-26-top-risk-chart-ui-design.md
git commit -m "docs: complete TOPRISK chart UI"
```

- [ ] **Step 6: Inspect final branch state**

Run:

```bash
git status --short
git log --oneline --decorate -8
```

Expected: clean feature worktree with the design, implementation, tests, and TODO updates committed.
