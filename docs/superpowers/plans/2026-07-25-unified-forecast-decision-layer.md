# Unified Forecast Decision Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the raw Ridge forecast while adding a causal, auditable decision layer that combines the existing 8-rule immediate overlay with the 12-rule remembered bearish-risk state.

**Architecture:** Ridge remains the only production return forecaster. A separate post-forecast policy consumes immutable `ForecastResult` values and point-in-time remembered risk context, produces a nested decision record, and may downgrade or veto the displayed direction without changing `predicted_return`. `ForecastService` builds and caches the risk context alongside its revision-scoped feature frame; the UI renders raw forecast, risk state, and adjusted conclusion separately.

**Tech Stack:** Python 3.9, pandas, immutable dataclasses, Flask JSON API, browser-native JavaScript, Node-based asset tests, `unittest`.

## Global Constraints

- A forecast at date \(t\) may use only observations available through the close at \(t\).
- Raw Ridge predicted return and raw direction must never be overwritten.
- Rule scores must be labeled as scores, not probabilities.
- Existing public fields remain compatible.
- The current 8-rule score of 70 remains an immediate confirmed-down threshold.
- Persistent-risk thresholds are versioned policy parameters and are tested independently from model fitting.
- Tickers without an explicit market-group mapping remain unavailable rather than receiving fabricated sector context.
- Every behavior change follows a red-green-refactor test cycle.

---

### Task 1: Immutable decision contract

**Files:**
- Create: `web/forecasts/decision.py`
- Modify: `web/forecasts/base.py`
- Test: `tests/test_web_forecast_decision.py`
- Test: `tests/test_web_forecast_dataset.py`

**Interfaces:**
- Produces: `ForecastDecision` with `final_direction`, `risk_state`, `action`, `reasons`, `policy_key`, `policy_version`, persistent-risk provenance, and immediate-risk provenance.
- Produces: `ForecastResult.with_decision(decision: ForecastDecision) -> ForecastResult`.
- Preserves: existing top-level `direction`, `raw_direction`, `predicted_return`, `bearish_turn_score`, `direction_adjustment_reason`, and `bearish_turn_conditions`.

- [ ] **Step 1: Write failing contract tests**

Add tests proving:

```python
decision = ForecastDecision(
    final_direction="neutral",
    risk_state="high",
    action="downgrade_to_neutral",
    reasons=("persistent_bearish_risk",),
    policy_key="forecast_decision_policy",
    policy_version="v1",
    persistent_risk_score=34.0,
    persistent_risk_raw_score=15.0,
    persistent_risk_state="fading",
    persistent_risk_age_sessions=1,
    immediate_risk_score=57.0,
)
adjusted = forecast.with_decision(decision)
assert forecast.direction == "up"
assert adjusted.direction == "neutral"
assert adjusted.raw_direction == "up"
assert adjusted.predicted_return == forecast.predicted_return
assert adjusted.to_dict()["decision"]["risk_state"] == "high"
```

Also reject invalid directions, risk states, actions, scores outside `[0, 100]`, negative memory ages, missing policy identity, and a decision whose `final_direction` disagrees with the adjusted `ForecastResult.direction`.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest \
  tests.test_web_forecast_decision \
  tests.test_web_forecast_dataset
```

Expected: import or attribute failure because the decision contract does not exist.

- [ ] **Step 3: Implement the immutable contract**

Implement:

```python
@dataclass(frozen=True)
class ForecastDecision:
    final_direction: str
    risk_state: str
    action: str
    reasons: tuple[str, ...]
    policy_key: str
    policy_version: str
    persistent_risk_score: float | None = None
    persistent_risk_raw_score: float | None = None
    persistent_risk_state: str = "unavailable"
    persistent_risk_age_sessions: int | None = None
    immediate_risk_score: float = 0.0
```

Allowed risk states are `low`, `watch`, `high`, `confirmed`, and `unavailable`. Allowed actions are `retain`, `downgrade_to_neutral`, and `override_to_down`.

Use `dataclasses.replace` in `ForecastResult.with_decision`. Serialize the decision under a nested `decision` field while retaining all legacy top-level fields.

- [ ] **Step 4: Run focused tests and verify pass**

Run the Task 1 command and require zero failures.

- [ ] **Step 5: Commit**

```bash
git add web/forecasts/decision.py web/forecasts/base.py \
  tests/test_web_forecast_decision.py tests/test_web_forecast_dataset.py
git commit -m "feat: add forecast decision contract"
```

### Task 2: Point-in-time persistent-risk context

**Files:**
- Modify: `web/forecasts/decision.py`
- Modify: `web/market_groups.py`
- Test: `tests/test_web_forecast_decision.py`
- Test: `tests/test_web_market_groups.py`

**Interfaces:**
- Produces: `build_forecast_risk_context(histories: Mapping[str, pd.DataFrame]) -> pd.DataFrame`.
- Output index: MultiIndex `("ticker", "observation_date")`.
- Output columns: `persistent_risk_raw_score`, `persistent_risk_score`, `persistent_risk_state`, `persistent_risk_age_sessions`.
- Consumes: `research.market_context.build_group_score_frame` and explicit `MarketGroup` metadata.

- [ ] **Step 1: Write failing causal context tests**

Create synthetic semiconductor histories with QQQ, SOXX, SMH, and one mapped constituent. Prove:

```python
context = build_forecast_risk_context(histories)
row = context.loc[("MU", pd.Timestamp("2026-06-30"))]
assert row["persistent_risk_score"] >= row["persistent_risk_raw_score"]
```

Append a future row with extreme values and prove every earlier context row remains identical. Prove an unmapped ticker does not receive semiconductor context. Prove duplicate group membership raises a metadata validation error.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest \
  tests.test_web_forecast_decision \
  tests.test_web_market_groups
```

Expected: failure because the context builder is missing.

- [ ] **Step 3: Implement context assembly**

Expose an iterator returning only explicitly modeled groups with constituent or related tickers:

```python
def modeled_market_groups() -> tuple[MarketGroup, ...]:
    return tuple(
        group for group in MARKET_GROUPS.values()
        if group.constituent_tickers or group.related_tickers
    )
```

For each modeled group, call `build_group_score_frame(histories, group)`, select only that group’s explicitly mapped tickers, rename the four risk columns, concatenate, validate no duplicate index keys, and return a sorted typed empty frame when no group is available.

- [ ] **Step 4: Run focused tests and verify pass**

Run the Task 2 command and require zero failures.

- [ ] **Step 5: Commit**

```bash
git add web/forecasts/decision.py web/market_groups.py \
  tests/test_web_forecast_decision.py tests/test_web_market_groups.py
git commit -m "feat: build point-in-time forecast risk context"
```

### Task 3: Versioned decision policy

**Files:**
- Modify: `web/forecasts/decision.py`
- Test: `tests/test_web_forecast_decision.py`
- Test: `tests/test_web_forecasts.py`

**Interfaces:**
- Produces: `ForecastDecisionPolicy`.
- Constructor parameters: `watch_threshold=20.0`, `high_threshold=30.0`, `immediate_confirm_threshold=70.0`, `joint_immediate_threshold=40.0`, `policy_version="v1"`.
- Produces: `decide(forecast: ForecastResult, context_row: Mapping | None) -> ForecastResult`.

- [ ] **Step 1: Write failing policy matrix tests**

Cover the complete matrix:

```python
# No context: preserve existing immediate decision provenance.
# Persistent score 20-29.999: risk_state=watch, direction retained.
# Persistent score >=30: risk_state=high; raw up becomes neutral.
# Persistent score >=30 and immediate score >=40: final direction down.
# Immediate score >=70: risk_state=confirmed and final direction down.
# Raw down is never upgraded by bullish absence or unavailable context.
# Unmapped context: risk_state=unavailable and direction retained.
# Predicted return is bit-for-bit unchanged by every policy action.
```

Add MU-like `persistent=34, immediate=57, raw=up` and NBIS-like `persistent=46.5, immediate=100, raw=up` regressions. Add a fading-memory case proving a remembered score is still actionable.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest \
  tests.test_web_forecast_decision \
  tests.test_web_forecasts
```

Expected: failure because the policy is missing.

- [ ] **Step 3: Implement the policy**

Apply precedence:

```text
immediate >= 70                         -> confirmed, override_to_down
persistent >= 30 and immediate >= 40  -> confirmed, override_to_down
persistent >= 30                       -> high, downgrade raw up to neutral
persistent >= 20                       -> watch, retain
available score below 20               -> low, retain
missing persistent context             -> unavailable, retain existing result
```

If Ridge already produced an 8-rule override, normalize it into the same nested decision rather than applying a second conflicting action. Reasons are stable machine codes: `immediate_bearish_confirmation`, `persistent_bearish_risk`, and `persistent_immediate_confluence`.

- [ ] **Step 4: Run focused tests and verify pass**

Run the Task 3 command and require zero failures.

- [ ] **Step 5: Commit**

```bash
git add web/forecasts/decision.py \
  tests/test_web_forecast_decision.py tests/test_web_forecasts.py
git commit -m "feat: add versioned forecast decision policy"
```

### Task 4: Integrate policy into the revision-scoped forecast service

**Files:**
- Modify: `web/services/forecasts.py`
- Test: `tests/test_web_forecast_service.py`
- Test: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `build_forecast_risk_context(histories)`.
- Consumes: `ForecastDecisionPolicy.decide`.
- Produces: every available forecast JSON row with nested `decision`.
- Preserves: cache invalidation and `max_forecast_dates` behavior.

- [ ] **Step 1: Write failing service and API tests**

Use a recording policy and deterministic provider to prove:

- context is built once per database revision, not once per requested date;
- richer or corrected history snapshots rebuild both feature and risk artifacts;
- cache invalidation discards decisions and contexts atomically;
- historical date forecasts use the context row for the exact forecast date;
- an unmapped ticker receives `risk_state="unavailable"`;
- raw predicted return remains positive when the final direction is down;
- the stock API exposes both raw fields and the nested decision.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest \
  tests.test_web_forecast_service \
  tests.test_web_api
```

Expected: assertion failure because no nested decision is returned.

- [ ] **Step 3: Implement revision-scoped integration**

Extend `_revision_artifacts` to cache `_artifact_risk_context`. Pass the requested ticker/date context row into the policy after provider results and before `_sparse_results`. Keep the policy injectable through `ForecastService(decision_policy=...)` for deterministic tests. Do not add composite risk fields to `RIDGE_V4_FEATURE_COLUMNS`.

- [ ] **Step 4: Run focused tests and verify pass**

Run the Task 4 command and require zero failures.

- [ ] **Step 5: Commit**

```bash
git add web/services/forecasts.py \
  tests/test_web_forecast_service.py tests/test_web_api.py
git commit -m "feat: integrate risk decisions into forecasts"
```

### Task 5: Present raw and adjusted forecast semantics in the UI

**Files:**
- Modify: `web/static/js/forecasts.js`
- Modify: `web/static/js/i18n.js`
- Test: `tests/test_web_assets.py`

**Interfaces:**
- Consumes: `forecast.decision`.
- Produces: localized raw forecast, persistent risk, immediate risk, adjustment action, and final direction.
- Preserves: `forecastMarker` uses the final direction.

- [ ] **Step 1: Write failing JavaScript asset tests**

Add fixtures for retained, watch, downgraded, and confirmed forecasts. Assert Chinese and English output includes:

```text
Ridge 原始方向 / Raw Ridge direction
Ridge 原始预测收益率 / Raw Ridge predicted return
持续向下风险 / Persistent bearish risk
即时确认风险 / Immediate confirmation risk
风险调整结论 / Risk-adjusted conclusion
```

Prove a positive raw return plus final down renders a down marker and never labels the positive return as the final predicted return. Prove an unavailable persistent context is explicit rather than displayed as zero.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest \
  tests.test_web_assets
```

Expected: missing localized decision fields.

- [ ] **Step 3: Implement UI rendering**

Replace the special-case `adjustedByBearishRisk` branch with a generic decision renderer. Always show raw forecast first; show decision evidence second; show final direction in the heading and marker. Add localized labels and stable translations for risk states, actions, and reasons.

- [ ] **Step 4: Run focused tests and verify pass**

Run the Task 5 command and require zero failures.

- [ ] **Step 5: Commit**

```bash
git add web/static/js/forecasts.js web/static/js/i18n.js \
  tests/test_web_assets.py
git commit -m "feat: show raw and risk-adjusted forecasts"
```

### Task 6: End-to-end regressions and documentation

**Files:**
- Modify: `docs/modeling-todo.md`
- Modify: `docs/dashboard.md`
- Test: `tests/test_web_api.py`
- Test: `tests/test_web_assets.py`

**Interfaces:**
- Produces: documented API and UI semantics.
- Produces: MU/NBIS/MRVL-like deterministic regression fixtures without training on named live dates.

- [ ] **Step 1: Add end-to-end regression assertions**

Prove:

- MU-like persistent/immediate confluence overrides a positive raw direction;
- NBIS-like immediate confirmation overrides a positive raw direction;
- a watch-only context retains direction but visibly lowers risk status;
- an unmapped software stock retains raw direction and reports unavailable sector risk until a software group is added;
- no future observation changes an earlier decision.

- [ ] **Step 2: Update documentation**

Document raw versus adjusted semantics, policy v1 precedence, rule-score disclaimers, and the planned replacement of fixed thresholds by walk-forward calibrated sector models. Mark the corresponding P1 separation items complete only when API and UI tests pass.

- [ ] **Step 3: Run complete verification**

Run:

```bash
/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest discover \
  -s tests -p 'test_*.py'
git diff --check
```

Expected: all tests pass and `git diff --check` prints nothing.

- [ ] **Step 4: Commit**

```bash
git add docs/modeling-todo.md docs/dashboard.md \
  tests/test_web_api.py tests/test_web_assets.py
git commit -m "docs: document unified forecast decisions"
```
