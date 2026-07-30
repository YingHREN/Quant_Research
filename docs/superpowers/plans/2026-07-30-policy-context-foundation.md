# Policy Context Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a point-in-time policy and liquidity context to the existing market dashboard without changing Ridge, macro-risk scoring, downside vetoes, or final forecast direction.

**Architecture:** Reuse the existing release-aware `macro_observations` store and ALFRED fetch path, but keep policy context in a new pure research module and a separate read-only service. The market overview receives one new advisory payload; the UI renders a stable current-policy card above macro history. This phase is the data and current-context foundation for later policy-period matrices, historical analogs, the regime slicer, RRG, and sector-rotation validation.

**Tech Stack:** Python 3, pandas, SQLite, Flask, vanilla JavaScript, CSS, `unittest`, existing FRED/ALFRED ingestion and market-overview services.

## Global Constraints

- Model key is `macro_policy_context_v1`, model version is `v1`.
- Lifecycle is `research`, decision permission is `advisory`, and online authority is `none`.
- Every historical value must satisfy `available_at <= asof`; missing values remain unavailable.
- Do not modify Ridge values, `forecast_decision_policy`, downside vetoes, or `macro_risk_v1`.
- Continuous values and descriptive state labels must both be returned; labels are not probabilities or forecasts.
- The UI must provide Simplified Chinese and English copy and must not change chart dimensions or date-lock behavior.
- Do not fetch data merely because a user opens `/market`.
- Do not use proxy values for unavailable official series.

---

### Task 1: Register and ingest official policy/liquidity series

**Files:**
- Create: `research/policy_context.py`
- Modify: `fetch_macro_data.py`
- Modify: `tests/test_fetch_macro_data.py`
- Test: `tests/test_policy_context.py`

**Interfaces:**
- Produces: `POLICY_SERIES_IDS: tuple`
- Produces: `ALL_MACRO_SERIES_IDS: tuple` in `fetch_macro_data.py`
- Consumes: existing `fetch_initial_release_observations()` and `MacroObservationStore`

- [ ] **Step 1: Write the failing series-catalog tests**

Create `tests/test_policy_context.py`:

```python
import unittest

import pandas as pd

from research.policy_context import POLICY_SERIES_IDS


class PolicySeriesCatalogTest(unittest.TestCase):
    def test_catalog_contains_policy_liquidity_real_rate_and_pce(self):
        self.assertEqual(
            POLICY_SERIES_IDS,
            (
                "DFEDTARL",
                "DFEDTARU",
                "WALCL",
                "WSHOSHO",
                "WSHOMCB",
                "WRESBAL",
                "WTREGEN",
                "RRPONTSYD",
                "DFII10",
                "PCEPI",
                "PCEPILFE",
            ),
        )


if __name__ == "__main__":
    unittest.main()
```

Add to `tests/test_fetch_macro_data.py`:

```python
from fetch_macro_data import ALL_MACRO_SERIES_IDS


def test_cli_catalog_includes_risk_and_policy_series(self):
    self.assertIn("DGS2", ALL_MACRO_SERIES_IDS)
    self.assertIn("DFEDTARU", ALL_MACRO_SERIES_IDS)
    self.assertIn("WRESBAL", ALL_MACRO_SERIES_IDS)
    self.assertEqual(len(ALL_MACRO_SERIES_IDS), len(set(ALL_MACRO_SERIES_IDS)))
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_policy_context.PolicySeriesCatalogTest \
  tests.test_fetch_macro_data.MacroDataFetchTest.test_cli_catalog_includes_risk_and_policy_series
```

Expected: FAIL because `research.policy_context` and `ALL_MACRO_SERIES_IDS` do not exist.

- [ ] **Step 3: Add the policy series catalog**

Create `research/policy_context.py`:

```python
"""Point-in-time monetary-policy and liquidity context."""

POLICY_MODEL_KEY = "macro_policy_context_v1"
POLICY_MODEL_VERSION = "v1"

POLICY_SERIES_IDS = (
    "DFEDTARL",
    "DFEDTARU",
    "WALCL",
    "WSHOSHO",
    "WSHOMCB",
    "WRESBAL",
    "WTREGEN",
    "RRPONTSYD",
    "DFII10",
    "PCEPI",
    "PCEPILFE",
)
```

Modify `fetch_macro_data.py`:

```python
from research.policy_context import POLICY_SERIES_IDS

ALL_MACRO_SERIES_IDS = tuple(
    dict.fromkeys((*SERIES_IDS, *POLICY_SERIES_IDS))
)
```

Change the CLI `--series` argument to use `choices=ALL_MACRO_SERIES_IDS` and
`default=list(ALL_MACRO_SERIES_IDS)`. Do not change the existing fetch format,
availability timestamp, or database table.

- [ ] **Step 4: Run focused tests**

Run:

```bash
./venv/bin/python -m unittest tests.test_fetch_macro_data tests.test_policy_context -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add research/policy_context.py fetch_macro_data.py \
  tests/test_fetch_macro_data.py tests/test_policy_context.py
git commit -m "feat: register policy context macro series"
```

---

### Task 2: Build the pure point-in-time policy context

**Files:**
- Modify: `research/policy_context.py`
- Modify: `tests/test_policy_context.py`

**Interfaces:**
- Consumes: `pandas.DataFrame` with the existing `macro_observations` columns
- Produces: `build_policy_context(observations, asof) -> dict`
- Produces: `unavailable_policy_context(reason, asof) -> dict`
- Payload keys: `model_key`, `model_version`, `asof`, `state`, `coverage`, `dimensions`, `evidence`, `limitations`, `lifecycle`, `decision_permission`, `online_authority`, `point_in_time`

- [ ] **Step 1: Write failing point-in-time and classification tests**

Extend `tests/test_policy_context.py` with a helper that creates:

```python
def observation(series_id, date, available_at, value, vintage=None):
    return {
        "series_id": series_id,
        "observation_date": date,
        "available_at": available_at,
        "value": value,
        "realtime_start": vintage or date,
        "realtime_end": "9999-12-31",
        "source": "test",
    }


def policy_observations():
    rows = [
        observation("DFEDTARL", "2026-04-01",
                    "2026-04-01T23:59:59+00:00", 3.50),
        observation("DFEDTARU", "2026-04-01",
                    "2026-04-01T23:59:59+00:00", 3.75),
        observation("DFEDTARL", "2026-07-15",
                    "2026-07-15T23:59:59+00:00", 3.50),
        observation("DFEDTARU", "2026-07-15",
                    "2026-07-15T23:59:59+00:00", 3.75),
        observation("WALCL", "2026-04-15",
                    "2026-04-16T23:59:59+00:00", 6_500_000),
        observation("WALCL", "2026-07-15",
                    "2026-07-16T23:59:59+00:00", 6_600_000),
        observation("WRESBAL", "2026-04-15",
                    "2026-04-16T23:59:59+00:00", 3_000_000),
        observation("WRESBAL", "2026-07-15",
                    "2026-07-16T23:59:59+00:00", 3_060_000),
        observation("DFII10", "2026-04-15",
                    "2026-04-15T23:59:59+00:00", 1.60),
        observation("DFII10", "2026-07-15",
                    "2026-07-15T23:59:59+00:00", 1.90),
    ]
    for month, headline, core in (
        ("2025-04-01", 126.0, 125.0),
        ("2025-07-01", 127.0, 126.0),
        ("2026-04-01", 130.0, 129.0),
        ("2026-07-01", 131.0, 130.0),
    ):
        released = (
            pd.Timestamp(month) + pd.offsets.MonthEnd(1)
        ).date().isoformat()
        rows.extend(
            [
                observation(
                    "PCEPI",
                    month,
                    f"{released}T23:59:59+00:00",
                    headline,
                    released,
                ),
                observation(
                    "PCEPILFE",
                    month,
                    f"{released}T23:59:59+00:00",
                    core,
                    released,
                ),
            ]
        )
    return rows
```

Add tests asserting:

```python
def test_builds_restrictive_rate_with_expanding_liquidity_context(self):
    result = build_policy_context(policy_observations(), "2026-07-20")
    self.assertEqual(result["model_key"], "macro_policy_context_v1")
    self.assertEqual(result["state"], "rate_restrictive_liquidity_support")
    self.assertEqual(result["dimensions"]["policy_rate"]["level"], "restrictive")
    self.assertEqual(result["dimensions"]["policy_rate"]["direction"], "flat")
    self.assertEqual(result["dimensions"]["liquidity"]["direction"], "expanding")
    self.assertEqual(result["dimensions"]["real_rate"]["direction"], "rising")
    self.assertGreaterEqual(result["coverage"], 0.8)
    self.assertEqual(result["online_authority"], "none")

def test_future_release_does_not_change_historical_context(self):
    original = policy_observations()
    revised = original + [
        observation(
            "WALCL",
            "2026-07-15",
            "2026-07-25T23:59:59+00:00",
            5_500_000,
            "2026-07-25",
        )
    ]
    self.assertEqual(
        build_policy_context(original, "2026-07-20"),
        build_policy_context(revised, "2026-07-20"),
    )

def test_missing_required_dimensions_returns_unavailable_not_neutral(self):
    result = build_policy_context(
        [observation("DFEDTARU", "2026-07-01",
                     "2026-07-01T23:59:59+00:00", 3.75)],
        "2026-07-20",
    )
    self.assertEqual(result["state"], "unavailable")
    self.assertIsNone(result["dimensions"]["liquidity"]["direction"])
    self.assertEqual(result["unavailable_reason"], "insufficient_policy_coverage")
```

The fixture must include at least 13 weeks of `WALCL`/`WRESBAL`, 63 calendar
days of target-rate/`DFII10`, and 15 months of PCE indexes so all changes are
causal.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
./venv/bin/python -m unittest tests.test_policy_context -v
```

Expected: FAIL because `build_policy_context` is undefined.

- [ ] **Step 3: Implement observation preparation and derived dimensions**

In `research/policy_context.py`, implement:

```python
def build_policy_context(observations, asof):
    cutoff = _cutoff(asof)
    frame = _available_observations(
        _prepare_observations(observations),
        cutoff,
    )
    dimensions = {
        "policy_rate": _policy_rate_dimension(frame),
        "liquidity": _change_dimension(
            frame, "WALCL", days=91, threshold=0.01,
            change_kind="percent",
        ),
        "reserves": _change_dimension(
            frame, "WRESBAL", days=91, threshold=0.01,
            change_kind="percent",
        ),
        "real_rate": _change_dimension(
            frame, "DFII10", days=63, threshold=0.25,
            change_kind="absolute",
        ),
        "pce": _inflation_dimension(frame, "PCEPI"),
        "core_pce": _inflation_dimension(frame, "PCEPILFE"),
    }
    coverage = _coverage(dimensions)
    available = coverage >= 0.70
    return {
        "model_key": POLICY_MODEL_KEY,
        "model_version": POLICY_MODEL_VERSION,
        "asof": cutoff.isoformat(),
        "state": (
            _combined_state(dimensions)
            if available else "unavailable"
        ),
        "coverage": round(coverage, 4),
        "dimensions": dimensions,
        "evidence": [
            dimensions[key]
            for key in (
                "policy_rate", "liquidity", "reserves",
                "real_rate", "pce", "core_pce",
            )
        ],
        "limitations": [
            "descriptive_not_forecast",
            "does_not_modify_ridge",
        ],
        "lifecycle": "research",
        "decision_permission": "advisory",
        "online_authority": "none",
        "point_in_time": True,
        "unavailable_reason": (
            None if available else "insufficient_policy_coverage"
        ),
    }

def unavailable_policy_context(reason="policy_data_unavailable", asof=None):
    cutoff = _cutoff(asof)
    return {
        "model_key": POLICY_MODEL_KEY,
        "model_version": POLICY_MODEL_VERSION,
        "asof": cutoff.isoformat(),
        "state": "unavailable",
        "coverage": 0.0,
        "dimensions": {
            key: _empty_dimension(key)
            for key in (
                "policy_rate", "liquidity", "reserves",
                "real_rate", "pce", "core_pce",
            )
        },
        "evidence": [],
        "limitations": [
            "descriptive_not_forecast",
            "does_not_modify_ridge",
        ],
        "lifecycle": "research",
        "decision_permission": "advisory",
        "online_authority": "none",
        "point_in_time": True,
        "unavailable_reason": reason,
    }
```

Implement the helpers with these exact signatures:

```python
def _cutoff(asof) -> pd.Timestamp
def _prepare_observations(observations) -> pd.DataFrame
def _available_observations(frame, cutoff) -> pd.DataFrame
def _policy_rate_dimension(frame) -> dict
def _change_dimension(
    frame, series_id, *, days, threshold, change_kind
) -> dict
def _inflation_dimension(frame, series_id) -> dict
def _coverage(dimensions) -> float
def _combined_state(dimensions) -> str
def _empty_dimension(key) -> dict
```

Every dimension dictionary has the same keys:

```python
{
    "key": str,
    "series_ids": list[str],
    "value": float | None,
    "prior_value": float | None,
    "change": float | None,
    "level": str | None,
    "direction": str | None,
    "lookback_days": int | None,
    "threshold": float | None,
    "observation_date": str | None,
    "available_at": str | None,
    "available": bool,
    "unavailable_reason": str | None,
}
```

Use these frozen v1 rules:

- target midpoint = `(DFEDTARL + DFEDTARU) / 2`;
- policy level is `restrictive` at midpoint `>= 3.0`, `accommodative` at
  midpoint `<= 1.0`, otherwise `moderate`;
- 63-calendar-day target change: rising `>= +0.10pp`, falling `<= -0.10pp`,
  otherwise flat;
- 91-calendar-day `WALCL` percent change: expanding `>= +1%`, contracting
  `<= -1%`, otherwise stable;
- 91-calendar-day `WRESBAL` percent change uses the same thresholds;
- 63-calendar-day `DFII10` change: rising `>= +0.25pp`, falling
  `<= -0.25pp`, otherwise flat;
- PCE and core PCE year-over-year use the latest available index divided by
  the latest available index at least 12 months earlier;
- three-month inflation direction is rising `>= +0.15pp`, falling
  `<= -0.15pp`, otherwise flat.

Build the combined descriptive state:

```python
if policy_level == "restrictive" and liquidity == "expanding":
    state = "rate_restrictive_liquidity_support"
elif rate_direction == "rising" and liquidity == "contracting":
    state = "dual_tightening"
elif rate_direction == "falling" and liquidity == "expanding":
    state = "broad_easing"
elif liquidity == "contracting":
    state = "liquidity_tightening"
else:
    state = "mixed"
```

Coverage is the available weight divided by 100 with frozen weights:
policy rate 25, liquidity 25, reserves 15, real rate 15, PCE 10, core PCE 10.
Coverage below 0.70 returns state `unavailable`; do not renormalize missing
dimensions into a confident state.

Each evidence row must include the series IDs, value, prior value,
observation date, available time, lookback, threshold, derived direction, and
missing reason.

- [ ] **Step 4: Run tests**

Run:

```bash
./venv/bin/python -m unittest tests.test_policy_context -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add research/policy_context.py tests/test_policy_context.py
git commit -m "feat: derive point-in-time policy context"
```

---

### Task 3: Add a cached service and market-overview contract

**Files:**
- Create: `web/services/policy_context.py`
- Modify: `web/services/market_overview.py`
- Modify: `web/app.py`
- Create: `tests/test_web_policy_context_service.py`
- Modify: `tests/test_web_market_overview.py`

**Interfaces:**
- Consumes: `MacroObservationStore.load_available(asof, series_ids=POLICY_SERIES_IDS)`
- Produces: `PolicyContextService.build(asof=None) -> dict`
- Produces: `PolicyContextService.cache_token() -> tuple`
- Adds market-overview payload key: `policy_context`

- [ ] **Step 1: Write failing service tests**

Create `tests/test_web_policy_context_service.py` with tests that:

- initialize a temporary `MacroObservationStore`;
- insert the complete fixture from Task 2;
- assert `PolicyContextService(path).build("2026-07-20")` returns the pure
  model payload;
- assert a missing database returns
  `unavailable_reason == "policy_data_unavailable"`;
- assert adding a newly available row changes `cache_token()` and invalidates
  the cached result.

Add to `tests/test_web_market_overview.py`:

```python
class PolicyContextServiceStub:
    def build(self, asof):
        return {
            "model_key": "macro_policy_context_v1",
            "state": "rate_restrictive_liquidity_support",
            "coverage": 1.0,
            "decision_permission": "advisory",
            "online_authority": "none",
        }

    def cache_token(self):
        return ("policy", 1)
```

Assert the market payload includes this object while its existing market score,
macro risk, sector rows, and calibration remain unchanged.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_policy_context_service \
  tests.test_web_market_overview -v
```

Expected: FAIL because the service and `policy_context` contract do not exist.

- [ ] **Step 3: Implement `PolicyContextService`**

Follow the bounded-cache and deep-copy pattern in
`web/services/macro_risk.py`. The service must:

- normalize a date-only `asof` to UTC end-of-day;
- load only `POLICY_SERIES_IDS`;
- cache by database `mtime_ns`, file size, and cutoff;
- catch `MacroDataUnavailable`, `ValueError`, and `TypeError`;
- return `unavailable_policy_context()` on failure.

- [ ] **Step 4: Wire the service into market overview and Flask**

Add optional `policy_context_service=None` to `MarketOverviewService`.
Include its cache token in the existing cache key. Add:

```python
payload["policy_context"] = _policy_context_payload(
    self._policy_context_service,
    normalized_asof,
)
```

Add the same unavailable payload to `_empty_payload()`.

In `web/app.py`, construct a default `PolicyContextService` from
`flask_app.config["MACRO_DATABASE"]`, expose it under
`flask_app.extensions["dashboard_policy_context_service"]`, and pass it to the
default `MarketOverviewService`. Respect an injected
`POLICY_CONTEXT_SERVICE` config object for tests.

- [ ] **Step 5: Run focused tests**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_policy_context_service \
  tests.test_web_market_overview \
  tests.test_web_macro_risk_service -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add web/services/policy_context.py web/services/market_overview.py \
  web/app.py tests/test_web_policy_context_service.py \
  tests/test_web_market_overview.py
git commit -m "feat: expose policy context in market overview"
```

---

### Task 4: Render the current policy combination in the market UI

**Files:**
- Modify: `web/templates/market.html`
- Modify: `web/static/js/market.js`
- Modify: `web/static/js/i18n.js`
- Modify: `web/static/css/market.css`
- Modify: `tests/test_web_market_assets.py`
- Modify: `tests/dashboard_runtime.mjs`

**Interfaces:**
- Consumes: `payload.policy_context`
- Produces DOM container: `#policy-context`
- Produces renderer: `renderPolicyContext(policy = {})`

- [ ] **Step 1: Write failing asset and runtime tests**

In `tests/test_web_market_assets.py`, assert:

- `market.html` contains `id="policy-context"`;
- Chinese and English translation keys exist for policy state, rate level,
  rate direction, liquidity, reserves, real rate, PCE, core PCE, coverage,
  advisory authority, and unavailable reason;
- the script contains `renderPolicyContext`.

In `tests/dashboard_runtime.mjs`, call the renderer with a complete payload and
assert the generated cards contain:

- the localized combined state;
- target range `3.50%–3.75%`;
- policy direction;
- liquidity direction;
- real-rate direction;
- coverage;
- an advisory/research badge.

Call it again with an unavailable payload and assert it shows an explicit
unavailable reason rather than “neutral”.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_market_assets -v
node --test tests/dashboard_runtime.mjs
```

Expected: FAIL because the policy container and renderer do not exist.

- [ ] **Step 3: Add stable policy-context markup and styles**

Insert a subsection before macro risk history:

```html
<div class="policy-context-heading">
  <div>
    <p class="eyebrow" data-i18n="market.policy.eyebrow">政策与流动性</p>
    <h3 data-i18n="market.policy.title">当前政策组合</h3>
  </div>
  <span data-i18n="market.policy.advisory">
    研究观察层，不是板块预测
  </span>
</div>
<div id="policy-context" class="policy-context-grid"></div>
```

Use a responsive grid with fixed minimum card height. Loading, missing, and
complete states must occupy the same layout footprint.

- [ ] **Step 4: Implement the renderer and translations**

Implement `renderPolicyContext()` in `market.js` using existing `element`,
`text`, `localized`, `formatPercent`, and unavailable helpers. Do not derive
state or direction in JavaScript.

Render these cards in order:

1. combined policy state;
2. target rate and 63-day direction;
3. Fed balance-sheet direction and change;
4. reserve direction and change;
5. 10-year real-rate direction and change;
6. headline/core PCE and direction;
7. coverage and model authority.

Call `renderPolicyContext(payload.policy_context)` from the existing market
payload render path. Add exact Chinese and English state/dimension labels to
`i18n.js`.

- [ ] **Step 5: Run UI tests**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_market_assets -v
node --test tests/dashboard_runtime.mjs
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add web/templates/market.html web/static/js/market.js \
  web/static/js/i18n.js web/static/css/market.css \
  tests/test_web_market_assets.py tests/dashboard_runtime.mjs
git commit -m "feat: show current policy context"
```

---

### Task 5: Verification, documentation, and TODO state

**Files:**
- Modify: `docs/dashboard.md`
- Modify: `docs/modeling-todo.md`
- Modify: `docs/superpowers/specs/2026-07-30-point-in-time-policy-sector-rotation-design.md` only if implementation reveals a genuine contract correction

**Interfaces:**
- Documents: data update command, payload semantics, model authority, and next phase
- Updates: only completed checkboxes actually verified in `MACRO-ROTATION-001`

- [ ] **Step 1: Run the focused Python suite**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_fetch_macro_data \
  tests.test_policy_context \
  tests.test_web_policy_context_service \
  tests.test_web_market_overview \
  tests.test_web_macro_risk_service \
  tests.test_web_market_assets -v
```

Expected: all tests PASS.

- [ ] **Step 2: Run JavaScript and syntax verification**

Run:

```bash
node --test tests/dashboard_runtime.mjs
./venv/bin/python -m py_compile \
  fetch_macro_data.py research/policy_context.py \
  web/services/policy_context.py web/services/market_overview.py web/app.py
```

Expected: all tests PASS and compilation exits 0.

- [ ] **Step 3: Run the full test suite**

Run:

```bash
./venv/bin/python -m unittest discover -s tests -v
```

Expected: all tests PASS. Existing untracked database WAL/SHM files and unrelated
research files must not be staged or removed.

- [ ] **Step 4: Document operation and limitations**

Add a “Policy and liquidity context” section to `docs/dashboard.md` that
documents:

```bash
source env.sh
./venv/bin/python fetch_macro_data.py \
  --series DFEDTARL DFEDTARU WALCL WSHOSHO WSHOMCB \
  WRESBAL WTREGEN RRPONTSYD DFII10 PCEPI PCEPILFE \
  --start 2010-01-01
```

State explicitly that opening the page never starts this fetch, reserve
management purchases are not automatically labeled QE, and the output is
descriptive/advisory.

- [ ] **Step 5: Update the global TODO accurately**

Mark only these `MACRO-ROTATION-001` items complete if their tests and UI
verification passed:

- official series catalog and point-in-time store reuse;
- current policy combination model;
- model registry/authority payload;
- current policy combination UI.

Leave historical policy periods, sector matrices, analogs, slicer, RRG,
rotation state machine, walk-forward validation, and Ridge review unchecked.

- [ ] **Step 6: Commit**

```bash
git add docs/dashboard.md docs/modeling-todo.md
git commit -m "docs: record policy context foundation"
```

- [ ] **Step 7: Record the next implementation boundary**

The next plan is `policy-period-sector-matrix`: versioned official policy
events, 2010-present adjusted sector ETF histories, period return/matrix API,
and the policy-band UI. Do not begin historical analogs or predictive sector
priors until the matrix data audit passes.
