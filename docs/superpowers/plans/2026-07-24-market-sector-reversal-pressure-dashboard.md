# Market, Sector, Reversal, and Pressure Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a point-in-time market-and-sector command center with auditable market posture, semiconductor drill-down, independent reversal-opportunity and downside-risk evidence, daily OHLCV pressure proxies, and separate 5/20/60-session historical calibration.

**Architecture:** Versioned local market-group metadata feeds a pure daily evidence engine. A read-only service builds one coherent market snapshot for a new Flask API and `/market` page; the same atomic evidence, but not its composite UI scores, enters the existing leakage-safe forecast dataset. All first-release pressure measures are labeled `daily_proxy`, and remote data access remains confined to the explicit update job.

**Tech Stack:** Python 3.9, pandas, NumPy, SQLite, Flask, vanilla ES modules, HTML/CSS, `unittest`

## Global Constraints

- Every observation uses only rows at or before its explicit `asof`.
- `label_end_date < evaluation_asof` is mandatory for all calibrated outcomes.
- Supported evaluation horizons are exactly 5, 20, and 60 trading sessions.
- Reference tickers are `SPY`, `QQQ`, `XLK`, `XLC`, `XLY`, `XLP`, `XLE`, `XLF`, `XLV`, `XLI`, `XLB`, `XLRE`, `XLU`, `SOXX`, and `SMH`.
- Semiconductor context uses an equal-weight SOXX/SMH return composite; one missing proxy degrades coverage, and two missing proxies make semiconductor scores unavailable.
- Opportunity and downside risk are independent and never forced to sum to 100.
- Composite scores require all required benchmark groups and at least 80% available weight.
- Page/API reads never start a remote request or an intraday collector.
- The first release always reports evidence tier `daily_proxy`; OHLCV estimates must not be called actual order flow.
- Atomic evidence may enter the forecast model; composite UI scores must not be duplicated as model features.
- No new front-end framework, remote CDN, or Python runtime dependency is introduced.
- Stable unavailable codes are returned instead of database paths, provider text, or secrets.
- Preserve existing chart hover, date-axis, update-job, forecast-cache, and intraday-collector behavior.

---

## File Structure

### New files

- `web/market_groups.py` — versioned benchmark, sector, semiconductor, and AI-infrastructure membership.
- `research/market_pressure.py` — pure OHLCV pressure rows and atomic evidence contracts.
- `research/market_context.py` — market/sector relative-strength context and deterministic composite scores.
- `research/market_outcomes.py` — 5/20/60 opportunity/risk labels, eligibility, and score calibration.
- `web/services/market_overview.py` — cached read-only application service and JSON-ready response assembly.
- `web/templates/market.html` — accessible market command-center markup.
- `web/static/css/market.css` — isolated command-center layout and responsive rules.
- `web/static/js/market.js` — market-page state, rendering, and sector drill-down.
- `tests/test_web_market_groups.py` — membership and explicit reference-update coverage.
- `tests/test_market_pressure.py` — daily proxy math, causal boundaries, and invalid input.
- `tests/test_market_context.py` — benchmark alignment, score coverage, and future invariance.
- `tests/test_market_outcomes.py` — outcome maturation and calibration gates.
- `tests/test_web_market_overview.py` — repository/service/API snapshot behavior.
- `tests/test_web_market_assets.py` — page structure, localization, JS behavior, and responsive contracts.

### Modified files

- `web/services/update_jobs.py` — union fixed reference tickers with active local tickers.
- `web/services/market_data.py` — one coherent market-overview read snapshot.
- `web/app.py` — construct the service and expose `/market` plus `/api/market-overview`.
- `web/templates/index.html` — navigation link to the market command center.
- `web/static/js/api.js` — typed market-overview request.
- `web/static/js/i18n.js` — complete Chinese/English market-page copy.
- `web/forecasts/dataset.py` — add atomic market/pressure features without composite scores.
- `web/services/forecasts.py` — advance model/cache identity after feature schema change.
- `docs/dashboard.md` — operator workflow, proxy caveat, formulas, and unavailable states.

---

### Task 1: Versioned market groups and explicit reference updates

**Files:**
- Create: `web/market_groups.py`
- Modify: `web/services/update_jobs.py`
- Modify: `web/app.py`
- Test: `tests/test_web_market_groups.py`
- Test: `tests/test_web_update_jobs.py`

**Interfaces:**
- Produces: `REFERENCE_TICKERS: tuple[str, ...]`
- Produces: `MARKET_GROUPS: Mapping[str, MarketGroup]`
- Produces: `market_group(key: str) -> MarketGroup`
- Changes: `UpdateJobManager(repository, provider, on_success=None, reference_tickers=())`
- Consumes later: `MarketGroup.benchmark_tickers`, `constituent_tickers`, and `related_tickers`

- [ ] **Step 1: Write failing membership and reference-update tests**

```python
# tests/test_web_market_groups.py
import unittest

from web.market_groups import MARKET_GROUPS, REFERENCE_TICKERS, market_group


class MarketGroupTest(unittest.TestCase):
    def test_reference_universe_is_stable_and_complete(self):
        self.assertEqual(
            REFERENCE_TICKERS,
            (
                "SPY", "QQQ", "XLK", "XLC", "XLY", "XLP", "XLE", "XLF",
                "XLV", "XLI", "XLB", "XLRE", "XLU", "SOXX", "SMH",
            ),
        )

    def test_semiconductor_and_ai_infrastructure_are_not_conflated(self):
        group = market_group("semiconductor")
        self.assertEqual(group.benchmark_tickers, ("SOXX", "SMH"))
        self.assertIn("AMD", group.constituent_tickers)
        self.assertNotIn("NBIS", group.constituent_tickers)
        self.assertIn("NBIS", group.related_tickers)
        self.assertIs(MARKET_GROUPS["semiconductor"], group)

    def test_each_sector_etf_is_a_selectable_proxy_only_group(self):
        technology = market_group("technology")
        self.assertEqual(technology.benchmark_tickers, ("XLK",))
        self.assertEqual(technology.constituent_tickers, ())
        self.assertEqual(technology.related_tickers, ())
```

```python
# append to tests/test_web_update_jobs.py
def test_reference_tickers_are_updated_even_when_absent_from_local_summaries(self):
    repository = FakeRepository(("AMD",))
    provider = FakeProvider(
        {"AMD": history(10), "QQQ": history(20), "SOXX": history(30)}
    )
    manager = UpdateJobManager(
        repository,
        provider,
        reference_tickers=("QQQ", "SOXX"),
    )

    snapshot = manager.run_synchronously_for_test()

    self.assertEqual(snapshot.state, "completed")
    self.assertEqual(provider.calls, ["AMD", "QQQ", "SOXX"])
    self.assertEqual(snapshot.total, 3)
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_market_groups \
  tests.test_web_update_jobs.UpdateJobManagerTest.test_reference_tickers_are_updated_even_when_absent_from_local_summaries -v
```

Expected: FAIL because `web.market_groups` and `reference_tickers` do not exist.

- [ ] **Step 3: Implement immutable group metadata**

```python
# web/market_groups.py
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


REFERENCE_TICKERS = (
    "SPY", "QQQ", "XLK", "XLC", "XLY", "XLP", "XLE", "XLF",
    "XLV", "XLI", "XLB", "XLRE", "XLU", "SOXX", "SMH",
)

SECTOR_ETFS = MappingProxyType({
    "technology": "XLK",
    "communication": "XLC",
    "consumer_discretionary": "XLY",
    "consumer_staples": "XLP",
    "energy": "XLE",
    "financials": "XLF",
    "health_care": "XLV",
    "industrials": "XLI",
    "materials": "XLB",
    "real_estate": "XLRE",
    "utilities": "XLU",
})


@dataclass(frozen=True)
class MarketGroup:
    key: str
    label_key: str
    benchmark_tickers: tuple[str, ...]
    constituent_tickers: tuple[str, ...]
    related_tickers: tuple[str, ...] = ()


_SEMICONDUCTORS = (
    "NVDA", "AMD", "AVGO", "MU", "INTC", "QCOM", "TXN", "ADI",
    "MCHP", "MRVL", "ON", "NXPI", "AMAT", "LRCX", "KLAC", "TER", "ENTG",
)
_AI_INFRASTRUCTURE = ("NBIS", "ANET", "DELL", "HPE", "SMCI")

_PROXY_GROUPS = {
    key: MarketGroup(
        key=key,
        label_key=f"market.sector.{key}",
        benchmark_tickers=(ticker,),
        constituent_tickers=(),
    )
    for key, ticker in SECTOR_ETFS.items()
}
MARKET_GROUPS = MappingProxyType({
    **_PROXY_GROUPS,
    "semiconductor": MarketGroup(
        key="semiconductor",
        label_key="market.group.semiconductor",
        benchmark_tickers=("SOXX", "SMH"),
        constituent_tickers=_SEMICONDUCTORS,
        related_tickers=_AI_INFRASTRUCTURE,
    ),
})


def market_group(key: str) -> MarketGroup:
    try:
        return MARKET_GROUPS[str(key)]
    except KeyError as exc:
        raise ValueError("unsupported_market_group") from exc
```

- [ ] **Step 4: Union and deterministically order update tickers**

```python
# web/services/update_jobs.py
def __init__(
    self,
    repository,
    provider,
    on_success=None,
    reference_tickers=(),
):
    # retain existing validation and fields
    checked = tuple(str(value).strip().upper() for value in reference_tickers)
    if any(not value for value in checked) or len(set(checked)) != len(checked):
        raise ValueError("reference_tickers must be unique non-empty symbols")
    self._reference_tickers = checked


def _load_tickers_if_needed(self):
    with self._lock:
        if self._remaining_tickers is not None:
            return
    summaries = self._repository.list_summaries()
    active = [
        summary.ticker
        for summary in summaries
        if not getattr(summary, "inactive", False)
    ]
    ordered = tuple(dict.fromkeys((*active, *self._reference_tickers)))
    with self._lock:
        self._remaining_tickers = list(ordered)
        self._total = len(ordered)
```

Pass `REFERENCE_TICKERS` from `create_app()` when constructing the production
`UpdateJobManager`; keep the constructor default empty so existing injected
tests retain their exact scope.

- [ ] **Step 5: Run focused update tests**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_market_groups \
  tests.test_web_update_jobs -v
```

Expected: PASS, including existing resume, partial, and rate-limit cases.

- [ ] **Step 6: Commit**

```bash
git add web/market_groups.py web/services/update_jobs.py web/app.py \
  tests/test_web_market_groups.py tests/test_web_update_jobs.py
git commit -m "feat: define market groups and reference updates"
```

---

### Task 2: Causal daily OHLCV pressure primitives

**Files:**
- Create: `research/market_pressure.py`
- Create: `tests/test_market_pressure.py`

**Interfaces:**
- Produces: `EvidenceState = Literal["met", "near", "unmet", "unavailable"]`
- Produces: immutable `Evidence`
- Produces: `build_pressure_rows(history: pd.DataFrame) -> pd.DataFrame`
- Output columns: `close_location`, `upper_wick_ratio`, `lower_wick_ratio`,
  `volume_ratio`, `signed_volume_proxy`, `price_progress_efficiency`,
  `distribution_day`, `high_volume_non_progress`, `failed_breakout`,
  `capitulation_recovery`
- Consumes later: Task 3 scoring and Task 6 model features

- [ ] **Step 1: Write failing formula and causality tests**

```python
# tests/test_market_pressure.py
import unittest
import numpy as np
import pandas as pd

from research.market_pressure import build_pressure_rows


def history(close, high=None, low=None, volume=None):
    close = np.asarray(close, dtype=float)
    index = pd.bdate_range("2025-01-02", periods=len(close))
    high = close + 1.0 if high is None else np.asarray(high, dtype=float)
    low = close - 1.0 if low is None else np.asarray(low, dtype=float)
    volume = (
        np.full(len(close), 100.0)
        if volume is None else np.asarray(volume, dtype=float)
    )
    return pd.DataFrame(
        {"Open": close, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=index,
    )


class MarketPressureTest(unittest.TestCase):
    def test_close_location_and_signed_volume_have_expected_direction(self):
        frame = history(
            [10.0] * 20 + [11.8],
            high=[11.0] * 20 + [12.0],
            low=[9.0] * 20 + [10.0],
            volume=[100.0] * 20 + [200.0],
        )
        row = build_pressure_rows(frame).iloc[-1]
        self.assertAlmostEqual(row["close_location"], 0.8)
        self.assertAlmostEqual(row["volume_ratio"], 2.0)
        self.assertAlmostEqual(row["signed_volume_proxy"], 1.6)

    def test_high_volume_non_progress_and_failed_breakout_are_separate(self):
        close = list(np.linspace(90.0, 100.0, 21)) + [99.7]
        frame = history(
            close,
            high=list(np.linspace(91.0, 101.0, 21)) + [103.0],
            low=list(np.linspace(89.0, 99.0, 21)) + [98.0],
            volume=[100.0] * 21 + [220.0],
        )
        row = build_pressure_rows(frame).iloc[-1]
        self.assertTrue(row["failed_breakout"])
        self.assertTrue(row["high_volume_non_progress"])

    def test_appending_future_rows_cannot_change_prior_pressure_rows(self):
        base = history(np.linspace(80.0, 100.0, 60))
        extended = pd.concat(
            [base, history([300.0, 20.0]).set_axis(
                pd.bdate_range(base.index[-1] + pd.Timedelta(days=1), periods=2)
            )]
        )
        expected = build_pressure_rows(base)
        actual = build_pressure_rows(extended).loc[base.index]
        pd.testing.assert_frame_equal(actual, expected)

    def test_zero_range_row_is_unavailable_not_infinite(self):
        frame = history([10.0] * 21, high=[10.0] * 21, low=[10.0] * 21)
        row = build_pressure_rows(frame).iloc[-1]
        self.assertTrue(np.isnan(row["close_location"]))
        self.assertTrue(np.isnan(row["upper_wick_ratio"]))
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
./venv/bin/python -m unittest tests.test_market_pressure -v
```

Expected: FAIL because `research.market_pressure` does not exist.

- [ ] **Step 3: Implement validated causal pressure rows**

```python
# research/market_pressure.py
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Mapping

import numpy as np
import pandas as pd


EvidenceState = Literal["met", "near", "unmet", "unavailable"]
REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


@dataclass(frozen=True)
class Evidence:
    key: str
    value: float | bool | None
    threshold: float | None
    state: EvidenceState
    points: float
    max_points: float
    window: str
    unavailable_reason: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        if self.state == "unavailable" and not self.unavailable_reason:
            raise ValueError("unavailable evidence requires a reason")
        if not 0.0 <= float(self.points) <= float(self.max_points):
            raise ValueError("evidence points must be within max_points")


def build_pressure_rows(history: pd.DataFrame) -> pd.DataFrame:
    checked = _history(history)
    high, low, close, volume = (
        checked[name].astype(float) for name in ("High", "Low", "Close", "Volume")
    )
    spread = (high - low).replace(0.0, np.nan)
    prior_close = close.shift(1)
    prior_pivot = close.shift(1).rolling(20, min_periods=20).max()
    volume_ma20 = volume.shift(1).rolling(20, min_periods=20).mean()
    volume_ratio = volume / volume_ma20.replace(0.0, np.nan)
    close_location = ((close - low) - (high - close)) / spread
    upper_wick = (high - pd.concat([checked["Open"], close], axis=1).max(axis=1)) / spread
    lower_wick = (pd.concat([checked["Open"], close], axis=1).min(axis=1) - low) / spread
    daily_return = close / prior_close.replace(0.0, np.nan) - 1.0
    efficiency = daily_return.abs() / volume_ratio.replace(0.0, np.nan)

    result = pd.DataFrame(index=checked.index)
    result["close_location"] = close_location
    result["upper_wick_ratio"] = upper_wick
    result["lower_wick_ratio"] = lower_wick
    result["volume_ratio"] = volume_ratio
    result["signed_volume_proxy"] = close_location * volume_ratio
    result["price_progress_efficiency"] = efficiency
    result["distribution_day"] = (
        (daily_return < 0.0) & (volume_ratio >= 1.2) & (close_location <= -0.4)
    )
    result["high_volume_non_progress"] = (
        (volume_ratio >= 1.5) & (daily_return.abs() <= 0.005)
    )
    result["failed_breakout"] = (
        prior_pivot.notna() & (high > prior_pivot) & (close <= prior_pivot)
    )
    prior_distress = (
        (daily_return.shift(1) < 0.0)
        & (volume_ratio.shift(1) >= 1.5)
        & (close_location.shift(1) <= -0.5)
    )
    result["capitulation_recovery"] = (
        prior_distress & (close > prior_close) & (close_location >= 0.4)
    )
    numeric = result.select_dtypes(include=[np.number]).columns
    result.loc[:, numeric] = result.loc[:, numeric].where(
        np.isfinite(result.loc[:, numeric]), np.nan
    )
    return result


def _history(source: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(source, pd.DataFrame):
        raise TypeError("history must be a DataFrame")
    missing = [name for name in REQUIRED_COLUMNS if name not in source]
    if missing:
        raise ValueError(f"history is missing columns: {missing}")
    result = source.loc[:, REQUIRED_COLUMNS].copy(deep=True).sort_index()
    if not isinstance(result.index, pd.DatetimeIndex) or result.index.has_duplicates:
        raise ValueError("history requires a unique DatetimeIndex")
    values = result.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("history values must be finite")
    if (result["High"] < result[["Open", "Close", "Low"]].max(axis=1)).any():
        raise ValueError("history high is inconsistent")
    if (result["Low"] > result[["Open", "Close", "High"]].min(axis=1)).any():
        raise ValueError("history low is inconsistent")
    return result.astype(float)
```

- [ ] **Step 4: Run focused pressure tests**

Run:

```bash
./venv/bin/python -m unittest tests.test_market_pressure -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add research/market_pressure.py tests/test_market_pressure.py
git commit -m "feat: compute causal daily pressure evidence"
```

---

### Task 3: Market, sector, opportunity, and downside-risk context

**Files:**
- Create: `research/market_context.py`
- Create: `tests/test_market_context.py`
- Modify: `research/market_pressure.py`

**Interfaces:**
- Consumes: `MarketGroup`, `build_pressure_rows()`, and
  `research.reversal.build_reversal_rows()`
- Produces: immutable `CompositeScore`
- Produces: `build_market_context(histories, asof, group, horizon) -> dict`
- Produces: `build_atomic_model_rows(histories, group) -> pd.DataFrame`
- Produces: `build_group_score_frame(histories, group) -> pd.DataFrame`
- Required response keys: `market_posture`, `sectors`, `selected_group`,
  `constituents`, `changed_events`, `evidence_tier`

- [ ] **Step 1: Write failing benchmark, degradation, and independence tests**

```python
# tests/test_market_context.py
import unittest
import numpy as np
import pandas as pd

from research.market_context import build_market_context
from web.market_groups import market_group


def rising(periods=260, slope=0.2, end="2026-07-23"):
    index = pd.bdate_range(end=end, periods=periods)
    close = 100.0 + np.arange(periods) * slope
    return pd.DataFrame({
        "Open": close - 0.2, "High": close + 1.0, "Low": close - 1.0,
        "Close": close, "Volume": np.full(periods, 1_000_000.0),
    }, index=index)


class MarketContextTest(unittest.TestCase):
    def test_one_semiconductor_proxy_degrades_coverage_without_fabricating_other_proxy(self):
        histories = {
            "QQQ": rising(),
            "SPY": rising(),
            "SOXX": rising(slope=0.3),
            "AMD": rising(slope=0.4),
        }
        result = build_market_context(
            histories, pd.Timestamp("2026-07-23"), market_group("semiconductor"), 5
        )
        selected = result["selected_group"]
        self.assertEqual(selected["available_benchmarks"], ["SOXX"])
        self.assertLess(selected["coverage"], 1.0)
        self.assertNotIn("SMH", selected["available_benchmarks"])

    def test_missing_both_sector_proxies_makes_both_stock_scores_unavailable(self):
        result = build_market_context(
            {"QQQ": rising(), "SPY": rising(), "AMD": rising()},
            pd.Timestamp("2026-07-23"),
            market_group("semiconductor"),
            20,
        )
        amd = result["constituents"][0]
        self.assertIsNone(amd["reversal_opportunity"]["score"])
        self.assertIsNone(amd["downside_risk"]["score"])
        self.assertEqual(
            amd["reversal_opportunity"]["unavailable_reason"],
            "missing_sector_benchmark",
        )

    def test_opportunity_and_risk_are_independent_not_complements(self):
        histories = {
            "QQQ": rising(), "SPY": rising(), "SOXX": rising(),
            "SMH": rising(), "AMD": rising(),
        }
        result = build_market_context(
            histories, pd.Timestamp("2026-07-23"), market_group("semiconductor"), 5
        )
        amd = result["constituents"][0]
        opportunity = amd["reversal_opportunity"]["score"]
        risk = amd["downside_risk"]["score"]
        self.assertNotEqual(opportunity + risk, 100.0)

    def test_future_append_does_not_change_old_market_context(self):
        histories = {
            name: rising() for name in ("QQQ", "SPY", "SOXX", "SMH", "AMD")
        }
        before = build_market_context(
            histories, pd.Timestamp("2026-07-23"), market_group("semiconductor"), 5
        )
        extended = {}
        for name, frame in histories.items():
            tail = rising(periods=2, slope=-20.0, end="2026-07-27")
            extended[name] = pd.concat([frame, tail.loc[tail.index > frame.index[-1]]])
        after = build_market_context(
            extended, pd.Timestamp("2026-07-23"), market_group("semiconductor"), 5
        )
        self.assertEqual(after, before)
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
./venv/bin/python -m unittest tests.test_market_context -v
```

Expected: FAIL because `research.market_context` does not exist.

- [ ] **Step 3: Implement immutable score coverage**

```python
# research/market_context.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from research.market_pressure import Evidence, build_pressure_rows
from research.reversal import build_reversal_rows
from web.market_groups import MarketGroup, SECTOR_ETFS


SUPPORTED_HORIZONS = (1, 5, 20, 60)
MINIMUM_SCORE_COVERAGE = 0.80


@dataclass(frozen=True)
class CompositeScore:
    score: float | None
    coverage: float
    evidence: tuple[Evidence, ...]
    unavailable_reason: str | None = None

    def to_dict(self):
        return {
            "score": self.score,
            "coverage": self.coverage,
            "unavailable_reason": self.unavailable_reason,
            "evidence": [
                {
                    "key": item.key,
                    "value": item.value,
                    "threshold": item.threshold,
                    "state": item.state,
                    "points": item.points,
                    "max_points": item.max_points,
                    "window": item.window,
                    "unavailable_reason": item.unavailable_reason,
                    "metadata": dict(item.metadata),
                }
                for item in self.evidence
            ],
        }


def score_evidence(
    evidence: Iterable[Evidence],
    *,
    required_available: bool,
    unavailable_reason: str,
) -> CompositeScore:
    rows = tuple(evidence)
    maximum = sum(item.max_points for item in rows)
    available = sum(
        item.max_points for item in rows if item.state != "unavailable"
    )
    coverage = 0.0 if maximum == 0.0 else available / maximum
    if not required_available or coverage < MINIMUM_SCORE_COVERAGE:
        return CompositeScore(None, coverage, rows, unavailable_reason)
    points = sum(item.points for item in rows if item.state != "unavailable")
    return CompositeScore(
        round(points / available * 100.0, 2),
        coverage,
        rows,
    )
```

Implement the exact version-1 weights:

```python
MARKET_WEIGHTS = {
    "trend": 30.0,
    "breadth": 25.0,
    "sector_leadership": 25.0,
    "distribution_volatility": 20.0,
}
OPPORTUNITY_WEIGHTS = {
    "market_stabilization": 20.0,
    "sector_improvement": 20.0,
    "stock_structure": 35.0,
    "participation_confirmation": 25.0,
}
RISK_WEIGHTS = {
    "market_weakening": 20.0,
    "sector_weakening": 20.0,
    "stock_structure_damage": 35.0,
    "supply_pressure": 25.0,
}
```

Use these version-1 atomic point allocations and thresholds. A `near` state
receives half of the listed points; an unavailable input receives no points and
reduces coverage:

```python
MARKET_RULES_V1 = {
    "qqq_above_ema20": (7.5, True),
    "qqq_above_sma50": (7.5, True),
    "spy_above_ema20": (7.5, True),
    "spy_above_sma50": (7.5, True),
    "breadth_above_ema20": (10.0, 0.60, 0.45),
    "breadth_above_sma50": (10.0, 0.55, 0.40),
    "new_high_low_balance": (5.0, 0.10, 0.00),
    "sector_relative_return_5": (8.0, 0.00, -0.01),
    "sector_relative_return_20": (9.0, 0.00, -0.02),
    "sector_relative_return_60": (8.0, 0.00, -0.04),
    "distribution_count_20_safe": (12.0, 2, 4),
    "atr20_ratio_safe": (8.0, 1.10, 1.25),
}
OPPORTUNITY_RULES_V1 = {
    "qqq_not_new_20_low": (7.0, True),
    "qqq_cross_above_ema20": (7.0, True),
    "qqq_downside_range_contracting": (6.0, True),
    "sector_relative_return_5_positive": (10.0, 0.00, -0.005),
    "sector_relative_slope_20_positive": (10.0, 0.00, -0.001),
    "higher_low_confirmed": (12.0, True),
    "trendline_breakout": (12.0, True),
    "prior_high_breakout": (11.0, True),
    "capitulation_recovery": (10.0, True),
    "signed_volume_proxy_positive": (7.5, 0.50, 0.00),
    "up_volume_confirmation": (7.5, True),
}
RISK_RULES_V1 = {
    "qqq_cross_below_ema20": (10.0, True),
    "qqq_distribution_count_20": (10.0, 4, 2),
    "sector_relative_return_5_negative": (10.0, -0.01, 0.00),
    "sector_relative_slope_20_negative": (10.0, -0.001, 0.00),
    "failed_breakout": (12.0, True),
    "cross_below_ema20": (8.0, True),
    "cross_below_sma50": (8.0, True),
    "stock_sector_rs_breakdown": (7.0, True),
    "distribution_day": (10.0, True),
    "high_volume_non_progress": (6.0, True),
    "upper_wick_supply": (5.0, 0.45, 0.30),
    "signed_volume_proxy_negative": (4.0, -0.50, 0.00),
}
```

For rules whose adverse direction is lower, the implementation compares in the
documented direction rather than assuming every larger numeric value is better.
`upper_wick_supply` additionally requires `volume_ratio >= 1.2`.
`up_volume_confirmation` requires positive close return, `volume_ratio >= 1.2`,
and `close_location >= 0.4`. `atr20_ratio_safe` is current ATR20 divided by its
trailing 63-session median. `qqq_downside_range_contracting` compares the
trailing five-session mean downside true range with the preceding five
sessions. Moving-average crosses require the prior close on the opposite side,
not merely the current position.

`build_market_context()` must:

1. validate `horizon in (5, 20, 60)`; one-session return remains a displayed
   diagnostic but is not a selectable prediction/calibration horizon;
2. normalize `asof` and truncate every copied history to `index <= asof`;
3. form the sector composite by averaging aligned SOXX/SMH daily returns and
   compounding from 1.0;
4. compute ETF 1/5/20/60 return and return-minus-QQQ for every available
   `SECTOR_ETFS` entry;
5. use existing reversal rows plus Task 2 pressure rows for constituents;
6. return only locally available versioned constituents and related names;
7. emit `daily_proxy`, coverage, thresholds, source tickers, and changed-event
   deltas;
8. never include `reversal_opportunity_score`, `downside_risk_score`, or
   `market_posture_score` in `build_atomic_model_rows()`.

The top-level payload always includes:

```python
"evidence_tier": "daily_proxy",
"intraday": {
    "state": "unavailable",
    "reason": "intraday_not_integrated",
},
```

`build_group_score_frame()` returns a MultiIndex frame keyed by
`(ticker, observation_date)` with `reversal_opportunity_score`,
`downside_risk_score`, score coverage, and `atr20_pct`. It applies the same
causal evidence functions used by the current snapshot and never reads a
future row. Task 7 uses this audit frame for matured historical calibration;
the function is not a model-feature source.

- [ ] **Step 4: Add explicit 80% and common-asof counterexamples**

```python
def test_score_below_eighty_percent_is_unavailable(self):
    # Construct one required group plus unavailable optional evidence with
    # available max weight 79 of 100.
    rows = (
        Evidence("available", 1.0, 0.0, "met", 79.0, 79.0, "1 session"),
        Evidence(
            "missing", None, None, "unavailable", 0.0, 21.0, "20 sessions",
            "insufficient_history",
        ),
    )
    score = score_evidence(
        rows, required_available=True, unavailable_reason="insufficient_coverage"
    )
    self.assertIsNone(score.score)
    self.assertAlmostEqual(score.coverage, 0.79)


def test_later_benchmark_row_is_not_used_for_earlier_stock_asof(self):
    histories = {
        "QQQ": rising(end="2026-07-24"),
        "SPY": rising(end="2026-07-24"),
        "SOXX": rising(end="2026-07-24"),
        "SMH": rising(end="2026-07-24"),
        "AMD": rising(end="2026-07-23"),
    }
    result = build_market_context(
        histories, pd.Timestamp("2026-07-23"), market_group("semiconductor"), 5
    )
    self.assertEqual(result["asof"], "2026-07-23")
    self.assertEqual(result["selected_group"]["latest_source_date"], "2026-07-23")
```

- [ ] **Step 5: Run market engine tests**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_market_pressure \
  tests.test_market_context \
  tests.test_web_forecast_dataset.ForecastDatasetTest.test_reversal_events_are_numeric_model_features -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add research/market_pressure.py research/market_context.py \
  tests/test_market_context.py
git commit -m "feat: score market sector reversal and pressure context"
```

---

### Task 4: Coherent market-overview repository, service, and API

**Files:**
- Modify: `web/services/market_data.py`
- Create: `web/services/market_overview.py`
- Modify: `web/app.py`
- Create: `tests/test_web_market_overview.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Produces: `MarketOverviewSnapshot(observation_date, histories)`
- Produces: `MarketDataRepository.load_market_overview_snapshot(asof=None)`
- Produces: `MarketOverviewService.build(asof=None, horizon=5, sector="semiconductor")`
- Produces routes: `GET /market` and
  `GET /api/market-overview?asof=&horizon=&sector=`
- Consumes: Task 3 `build_market_context()`

- [ ] **Step 1: Write failing one-snapshot repository and service tests**

```python
# tests/test_web_market_overview.py
import unittest
from types import SimpleNamespace
import pandas as pd

from web.services.market_overview import MarketOverviewService


class FakeRepository:
    def __init__(self, histories):
        self.histories = histories
        self.calls = []

    def load_market_overview_snapshot(self, asof=None):
        self.calls.append(asof)
        cutoff = max(frame.index[-1] for frame in self.histories.values())
        if asof is not None:
            cutoff = min(cutoff, pd.Timestamp(asof))
        return SimpleNamespace(
            observation_date=cutoff.date().isoformat(),
            histories={
                key: value.loc[value.index <= cutoff].copy()
                for key, value in self.histories.items()
            },
        )


class MarketOverviewServiceTest(unittest.TestCase):
    def test_build_reads_one_snapshot_and_returns_daily_proxy(self):
        histories = fixture_histories()
        repository = FakeRepository(histories)
        service = MarketOverviewService(repository)

        payload = service.build(asof="2026-07-23", horizon=5, sector="semiconductor")

        self.assertEqual(repository.calls, ["2026-07-23"])
        self.assertEqual(payload["asof"], "2026-07-23")
        self.assertEqual(payload["evidence_tier"], "daily_proxy")
        self.assertEqual(payload["requested_horizon"], 5)
```

Write a SQLite integration test that opens one `load_market_overview_snapshot()`
call, proves all returned rows are at or before the normalized cutoff, and
proves a missing database is not created.

- [ ] **Step 2: Write failing API validation tests**

```python
# append to tests/test_web_api.py
def test_market_overview_route_validates_horizon_and_sector(self):
    service = mock.Mock()
    app = create_app({
        "TESTING": True,
        "MARKET_OVERVIEW_SERVICE": service,
    }, repository=FakeRepository(), update_manager=FakeManager())
    client = app.test_client()

    bad_horizon = client.get("/api/market-overview?horizon=7")
    bad_sector = client.get("/api/market-overview?sector=secret/path")

    self.assertEqual(bad_horizon.status_code, 400)
    self.assertEqual(bad_horizon.get_json()["error"]["code"], "invalid_horizon")
    self.assertEqual(bad_sector.status_code, 400)
    self.assertEqual(bad_sector.get_json()["error"]["code"], "invalid_sector")
    service.build.assert_not_called()
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_market_overview \
  tests.test_web_api.WebApiTest.test_market_overview_route_validates_horizon_and_sector -v
```

Expected: FAIL because the snapshot, service, configuration hook, and routes do
not exist.

- [ ] **Step 4: Implement coherent repository snapshot**

```python
# web/services/market_data.py
@dataclass(frozen=True)
class MarketOverviewSnapshot:
    observation_date: str | None
    histories: dict[str, pd.DataFrame]


def load_market_overview_snapshot(self, asof=None):
    cutoff = iso_date(asof)
    query = """
        SELECT ticker, date, open, high, low, close, volume
        FROM prices
    """
    params = ()
    if cutoff is not None:
        query += " WHERE date <= ?"
        params = (cutoff,)
    query += " ORDER BY ticker, date"
    with self._connect() as connection:
        rows = connection.execute(query, params).fetchall()
    histories = self._histories_from_rows(rows)
    latest = max(
        (history.index[-1] for history in histories.values() if not history.empty),
        default=None,
    )
    return MarketOverviewSnapshot(
        observation_date=iso_date(latest),
        histories=histories,
    )
```

The implementation must retain the existing URI `mode=ro`; no read path may
create a database.

- [ ] **Step 5: Implement bounded service caching**

```python
# web/services/market_overview.py
from __future__ import annotations

from copy import deepcopy
from threading import RLock

import pandas as pd

from research.market_context import build_market_context
from web.forecasts.base import SUPPORTED_HORIZONS
from web.market_groups import market_group


class MarketOverviewService:
    def __init__(self, repository, revision_getter=lambda: 0, max_cache_size=16):
        self._repository = repository
        self._revision_getter = revision_getter
        self._max_cache_size = int(max_cache_size)
        self._cache = {}
        self._lock = RLock()

    def build(self, *, asof=None, horizon=5, sector="semiconductor"):
        if horizon not in SUPPORTED_HORIZONS:
            raise ValueError("invalid_horizon")
        group = market_group(sector)
        snapshot = self._repository.load_market_overview_snapshot(asof)
        normalized_asof = snapshot.observation_date
        revision = int(self._revision_getter())
        key = (revision, normalized_asof, int(horizon), group.key, "market_evidence_v1")
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return deepcopy(cached)
            payload = build_market_context(
                snapshot.histories,
                pd.Timestamp(normalized_asof) if normalized_asof else None,
                group,
                int(horizon),
            )
            self._cache[key] = deepcopy(payload)
            while len(self._cache) > self._max_cache_size:
                self._cache.pop(next(iter(self._cache)))
            return deepcopy(payload)
```

Return this JSON-ready empty snapshot instead of passing `None` to
`pd.Timestamp`:

```python
if normalized_asof is None:
    return {
        "asof": None,
        "requested_horizon": int(horizon),
        "selected_sector": group.key,
        "evidence_tier": "daily_proxy",
        "intraday": {
            "state": "unavailable",
            "reason": "intraday_not_integrated",
        },
        "market_posture": {
            "score": None,
            "coverage": 0.0,
            "unavailable_reason": "market_data_unavailable",
            "evidence": [],
        },
        "sectors": [],
        "selected_group": {
            "key": group.key,
            "score": None,
            "coverage": 0.0,
            "unavailable_reason": "market_data_unavailable",
        },
        "constituents": [],
        "changed_events": [],
        "calibration": {},
    }
```

- [ ] **Step 6: Add Flask routes and safe error mapping**

```python
# inside create_app in web/app.py
market_overview_service = flask_app.config.get("MARKET_OVERVIEW_SERVICE")
if market_overview_service is None:
    market_overview_service = MarketOverviewService(
        repository,
        revision_getter=lambda: forecast_service.database_revision,
    )
flask_app.extensions["dashboard_market_overview_service"] = market_overview_service


@flask_app.get("/market")
def market_dashboard():
    return render_template("market.html")


@flask_app.get("/api/market-overview")
def market_overview():
    raw_horizon = request.args.get("horizon", "5")
    try:
        horizon = int(raw_horizon)
    except (TypeError, ValueError):
        return _error_response("invalid_horizon", 400)
    sector = request.args.get("sector", "semiconductor")
    try:
        payload = market_overview_service.build(
            asof=request.args.get("asof"),
            horizon=horizon,
            sector=sector,
        )
    except ValueError as error:
        code = str(error)
        if code not in {"invalid_horizon", "unsupported_market_group"}:
            raise
        return _error_response(
            "invalid_horizon" if code == "invalid_horizon" else "invalid_sector",
            400,
        )
    return _json_response(payload)
```

Use the existing error-envelope helper and safe localized client message; do
not return `str(error)` as prose.

- [ ] **Step 7: Run repository, API, and compatibility tests**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_market_overview \
  tests.test_web_api \
  tests.test_web_market_data \
  tests.test_web_update_jobs -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add web/services/market_data.py web/services/market_overview.py web/app.py \
  tests/test_web_market_overview.py tests/test_web_api.py
git commit -m "feat: expose coherent market overview API"
```

---

### Task 5: Localized market command-center page

**Files:**
- Create: `web/templates/market.html`
- Create: `web/static/css/market.css`
- Create: `web/static/js/market.js`
- Modify: `web/templates/index.html`
- Modify: `web/static/js/api.js`
- Modify: `web/static/js/i18n.js`
- Create: `tests/test_web_market_assets.py`
- Modify: `tests/test_web_assets.py`

**Interfaces:**
- Consumes: `GET /api/market-overview`
- Produces: `api.getMarketOverview({asof, horizon, sector})`
- Produces stable element IDs: `market-posture`, `sector-heatmap`,
  `market-evidence`, `sector-drilldown`, `market-events`,
  `market-data-tier`, `market-coverage`

- [ ] **Step 1: Write failing template and asset-contract tests**

```python
# tests/test_web_market_assets.py
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MarketAssetTest(unittest.TestCase):
    def test_market_template_has_accessible_command_center_regions(self):
        source = (ROOT / "web/templates/market.html").read_text()
        for element_id in (
            "market-posture", "sector-heatmap", "market-evidence",
            "sector-drilldown", "market-events", "market-data-tier",
            "market-coverage",
        ):
            self.assertIn(f'id="{element_id}"', source)
        self.assertIn('aria-live="polite"', source)
        self.assertIn('href="/"', source)

    def test_market_js_uses_payload_evidence_and_does_not_recompute_scores(self):
        source = (ROOT / "web/static/js/market.js").read_text()
        self.assertIn("payload.market_posture", source)
        self.assertIn("payload.selected_group", source)
        self.assertNotIn("reversalOpportunityScore(", source)
        self.assertNotIn("downsideRiskScore(", source)

    def test_market_javascript_is_valid(self):
        for path in (
            ROOT / "web/static/js/api.js",
            ROOT / "web/static/js/i18n.js",
            ROOT / "web/static/js/market.js",
        ):
            subprocess.run(
                ["node", "--check", str(path)],
                check=True,
                capture_output=True,
                text=True,
            )
```

Add assertions that every new `market.*` key exists in both `zh-CN` and `en`,
that heat-map tiles contain visible metric text, and that sector buttons use
`aria-pressed`.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_market_assets -v
```

Expected: FAIL because market assets do not exist.

- [ ] **Step 3: Implement typed API request**

```javascript
// web/static/js/api.js
export function getMarketOverview({ asof = "", horizon = 5, sector = "semiconductor" } = {}) {
  const params = new URLSearchParams({
    horizon: String(horizon),
    sector: String(sector),
  });
  if (asof) params.set("asof", String(asof));
  return requestJson(`/api/market-overview?${params.toString()}`);
}

export const api = Object.freeze({
  getUniverse,
  getStock,
  getStockForecast,
  getMarketOverview,
  startUpdate,
  getUpdateStatus,
});
```

- [ ] **Step 4: Implement semantic template and navigation**

`market.html` must include:

```html
<header class="market-header">
  <a class="brand-link" href="/">Quant Research</a>
  <nav aria-label="Primary navigation" data-i18n-aria-label="market.nav.aria">
    <a href="/" data-i18n="market.nav.stock">个股看板</a>
    <a href="/market" aria-current="page" data-i18n="market.nav.market">市场与板块</a>
  </nav>
</header>
<main id="market-command-center">
  <section id="market-posture" aria-labelledby="market-posture-title"></section>
  <section id="sector-heatmap" aria-labelledby="sector-heatmap-title"></section>
  <aside id="market-evidence" aria-labelledby="market-evidence-title"></aside>
  <section id="sector-drilldown" aria-labelledby="sector-drilldown-title"></section>
  <aside id="market-events" aria-labelledby="market-events-title"></aside>
  <p id="market-status" aria-live="polite"></p>
  <span id="market-data-tier"></span>
  <span id="market-coverage"></span>
</main>
<script type="module" src="/static/js/market.js"></script>
```

Add a matching `Market & Sectors` link to `index.html`. Do not copy the entire
stock workstation DOM into the market page.

- [ ] **Step 5: Render payload values without browser-side factor math**

```javascript
// web/static/js/market.js
import { getMarketOverview } from "./api.js";
import { getLocale, onLocaleChange, t } from "./i18n.js";

const state = { horizon: 5, sector: "semiconductor", payload: null };

function sectorButton(row) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "sector-tile";
  button.dataset.sector = row.key;
  button.setAttribute("aria-pressed", String(row.key === state.sector));
  button.innerHTML = `
    <span>${t(row.label_key)}</span>
    <strong>${row.relative_return == null ? "—" : `${(row.relative_return * 100).toFixed(1)}%`}</strong>
    <span>${t("market.risk")} ${row.downside_risk?.score ?? "—"}</span>
  `;
  return button;
}

function render(payload) {
  state.payload = payload;
  renderPosture(payload.market_posture);
  renderSectorHeatmap(payload.sectors.map(sectorButton));
  renderEvidence(payload.market_posture.evidence);
  renderDrilldown(payload.selected_group, payload.constituents);
  renderEvents(payload.changed_events);
  document.querySelector("#market-data-tier").textContent =
    t(`market.tier.${payload.evidence_tier}`);
}

async function load() {
  setStatus(t("market.loading"));
  try {
    render(await getMarketOverview({
      horizon: state.horizon,
      sector: state.sector,
    }));
    setStatus("");
  } catch (error) {
    setStatus(t(`request.error.${error.code || "request_failed"}`));
  }
}

document.querySelector("#sector-heatmap").addEventListener("click", (event) => {
  const button = event.target.closest("[data-sector]");
  if (!button || button.dataset.sector === state.sector) return;
  state.sector = button.dataset.sector;
  load();
});
onLocaleChange(() => state.payload && render(state.payload));
load();
```

Use DOM construction helpers that render provenance rather than recomputing
evidence:

```javascript
function text(node, value) {
  node.textContent = value == null ? "—" : String(value);
}

function renderPosture(posture) {
  const root = document.querySelector("#market-posture");
  root.replaceChildren();
  const score = document.createElement("strong");
  score.className = "market-score";
  text(score, posture?.score);
  const coverage = document.createElement("span");
  coverage.className = "market-score-coverage";
  text(coverage, `${Math.round((posture?.coverage || 0) * 100)}%`);
  const reason = document.createElement("span");
  reason.className = "market-unavailable-reason";
  text(reason, posture?.unavailable_reason
    ? t(`market.unavailable.${posture.unavailable_reason}`)
    : t("market.available"));
  root.append(score, coverage, reason);
}

function renderSectorHeatmap(buttons) {
  const root = document.querySelector("#sector-heatmap");
  const grid = document.createElement("div");
  grid.className = "sector-heatmap-grid";
  grid.append(...buttons);
  root.replaceChildren(grid);
}

function renderEvidence(evidence = []) {
  const root = document.querySelector("#market-evidence");
  const list = document.createElement("ul");
  list.className = "market-evidence-list";
  for (const row of evidence) {
    const item = document.createElement("li");
    const heading = document.createElement("strong");
    text(heading, t(`market.evidence.${row.key}`));
    const detail = document.createElement("span");
    text(
      detail,
      `${t(`market.state.${row.state}`)} · ${row.value ?? "—"} · `
      + `${t("market.threshold")} ${row.threshold ?? "—"} · ${row.window}`,
    );
    const missing = document.createElement("span");
    missing.className = "market-unavailable-reason";
    text(
      missing,
      row.unavailable_reason
        ? t(`market.unavailable.${row.unavailable_reason}`)
        : "",
    );
    item.append(heading, detail, missing);
    list.append(item);
  }
  root.replaceChildren(list);
}

function renderDrilldown(group, constituents = []) {
  const root = document.querySelector("#sector-drilldown");
  const summary = document.createElement("p");
  text(
    summary,
    `${t(group.label_key)} · ${t("market.coverage")} `
    + `${Math.round((group.coverage || 0) * 100)}%`,
  );
  const table = document.createElement("table");
  const body = document.createElement("tbody");
  for (const row of constituents) {
    const tr = document.createElement("tr");
    const link = document.createElement("a");
    link.href = `/?ticker=${encodeURIComponent(row.ticker)}`;
    text(link, row.ticker);
    for (const value of (
      link,
      row.relative_strength_20,
      row.reversal_opportunity?.score,
      row.downside_risk?.score,
      row.pressure_state,
    )) {
      const td = document.createElement("td");
      if (value instanceof Node) td.append(value);
      else text(td, value);
      tr.append(td);
    }
    body.append(tr);
  }
  table.append(body);
  root.replaceChildren(summary, table);
}

function renderEvents(events = []) {
  const root = document.querySelector("#market-events");
  const list = document.createElement("ul");
  for (const event of events) {
    const item = document.createElement("li");
    text(
      item,
      `${event.ticker || event.source} · ${t(`market.evidence.${event.key}`)} `
      + `${event.previous_value ?? "—"} → ${event.current_value ?? "—"}`,
    );
    list.append(item);
  }
  root.replaceChildren(list);
}

function setStatus(message) {
  text(document.querySelector("#market-status"), message);
}
```

- [ ] **Step 6: Add fixed-grid and responsive CSS**

Use CSS Grid with stable region minimum heights:

```css
.market-summary-grid {
  display: grid;
  grid-template-columns: 1.3fr repeat(4, minmax(8rem, 1fr));
  gap: .75rem;
  min-height: 8rem;
}
.market-main-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.7fr) minmax(16rem, .8fr);
  gap: 1rem;
  align-items: stretch;
}
.sector-heatmap-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(8rem, 1fr));
  gap: .5rem;
}
@media (max-width: 900px) {
  .market-summary-grid,
  .market-main-grid { grid-template-columns: 1fr 1fr; }
  .sector-heatmap-grid { grid-template-columns: repeat(2, minmax(8rem, 1fr)); }
}
@media (max-width: 560px) {
  .market-summary-grid,
  .market-main-grid,
  .sector-heatmap-grid { grid-template-columns: 1fr; }
}
```

Do not encode risk solely in background color. Retain visible focus outlines,
metric text, and state labels.

- [ ] **Step 7: Add complete bilingual keys**

Add exact keys for navigation, headings, score bands, evidence states, evidence
tier, coverage, opportunity, risk, pressure proxy, unavailable reasons, sector
labels, methodology labels, and safety copy to both locale dictionaries.
Export the existing locale getter/change listener if they are currently private;
do not add a second locale storage key.

- [ ] **Step 8: Run page and regression asset tests**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_market_assets \
  tests.test_web_assets -v
node --check web/static/js/market.js
node --check web/static/js/api.js
node --check web/static/js/i18n.js
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add web/templates/market.html web/templates/index.html \
  web/static/css/market.css web/static/js/market.js web/static/js/api.js \
  web/static/js/i18n.js tests/test_web_market_assets.py tests/test_web_assets.py
git commit -m "feat: add market sector command center"
```

---

### Task 6: Add atomic pressure and relative-strength model features

**Files:**
- Modify: `research/market_context.py`
- Modify: `web/forecasts/dataset.py`
- Modify: `web/services/forecasts.py`
- Modify: `tests/test_web_forecast_dataset.py`
- Modify: `tests/test_web_forecasts.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `build_pressure_rows()` and `build_atomic_model_rows()`
- Adds exact atomic features:
  `pressure_close_location`, `pressure_upper_wick_ratio`,
  `pressure_signed_volume_proxy`, `pressure_distribution_day`,
  `pressure_failed_breakout`, `qqq_trend_state`,
  `sector_relative_strength_20`, `stock_sector_relative_strength_20`
- Excludes: `market_posture_score`, `reversal_opportunity_score`,
  `downside_risk_score`
- Advances: ridge `MODEL_VERSION` from `v2` to `v3`

- [ ] **Step 1: Write failing feature-schema and leakage tests**

```python
# append to tests/test_web_forecast_dataset.py
def test_market_pressure_atomic_features_are_numeric_and_composites_are_excluded(self):
    histories = market_feature_histories()
    frame = build_feature_frame(histories)
    row = frame.loc[("AMD", frame.loc["AMD"].index[-1])]

    for key in (
        "pressure_close_location",
        "pressure_upper_wick_ratio",
        "pressure_signed_volume_proxy",
        "pressure_distribution_day",
        "pressure_failed_breakout",
        "qqq_trend_state",
        "sector_relative_strength_20",
        "stock_sector_relative_strength_20",
    ):
        self.assertIn(key, FEATURE_COLUMNS)
        self.assertTrue(np.isfinite(row[key]) or np.isnan(row[key]))
    self.assertNotIn("market_posture_score", FEATURE_COLUMNS)
    self.assertNotIn("reversal_opportunity_score", FEATURE_COLUMNS)
    self.assertNotIn("downside_risk_score", FEATURE_COLUMNS)


def test_future_benchmark_rows_cannot_change_old_cross_market_features(self):
    histories = market_feature_histories(end="2026-07-23")
    before = build_feature_frame(histories)
    extended = append_future_benchmark_spike(histories)
    after = build_feature_frame(extended)
    pd.testing.assert_series_equal(
        after.loc[("AMD", pd.Timestamp("2026-07-23"))],
        before.loc[("AMD", pd.Timestamp("2026-07-23"))],
    )
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_forecast_dataset.ForecastDatasetTest.test_market_pressure_atomic_features_are_numeric_and_composites_are_excluded \
  tests.test_web_forecast_dataset.ForecastDatasetTest.test_future_benchmark_rows_cannot_change_old_cross_market_features -v
```

Expected: FAIL because the atomic columns are absent.

- [ ] **Step 3: Add columns through one aligned second pass**

Extend `FEATURE_COLUMNS` with the eight exact names above. In
`build_feature_frame()`:

```python
validated = {
    str(raw_ticker): _validated_history(str(raw_ticker), source)
    for raw_ticker, source in histories.items()
}
ticker_frames = [
    _ticker_features(ticker, history)
    for ticker, history in validated.items()
    if not history.empty
]
result = pd.concat(ticker_frames, axis=0).sort_index()
market_rows = build_atomic_model_rows(
    validated,
    market_group("semiconductor"),
)
for column in MARKET_ATOMIC_FEATURE_COLUMNS:
    result[column] = market_rows[column].reindex(result.index)
```

`build_atomic_model_rows()` must use each row's observation date as its cutoff.
It may vectorize rolling joins, but it must not loop by repeatedly invoking the
full command-center service.

`stock_sector_relative_strength_20` is populated only for versioned
semiconductor constituents and related AI-infrastructure names in this release.
Other tickers receive `NaN`; they are not assigned to a sector from price
behavior.

Increment `MODEL_VERSION` to `v3`; keep `MODEL_KEY = "ridge_direction_v1"` so
the algorithm identity remains explicit while its feature schema advances.

- [ ] **Step 4: Run warning-strict forecast tests**

Run:

```bash
PYTHONWARNINGS=error ./venv/bin/python -m unittest \
  tests.test_web_forecast_dataset \
  tests.test_web_forecasts \
  tests.test_web_forecast_evaluation \
  tests.test_web_api -v
```

Expected: PASS with no NumPy, pandas, or BLAS warnings.

- [ ] **Step 5: Commit**

```bash
git add research/market_context.py web/forecasts/dataset.py \
  web/services/forecasts.py tests/test_web_forecast_dataset.py \
  tests/test_web_forecasts.py tests/test_web_api.py
git commit -m "feat: add atomic market pressure forecast features"
```

---

### Task 7: Independent 5/20/60 opportunity and downside-risk calibration

**Files:**
- Create: `research/market_outcomes.py`
- Modify: `web/services/market_overview.py`
- Create: `tests/test_market_outcomes.py`
- Modify: `tests/test_web_market_overview.py`

**Interfaces:**
- Produces: `attach_market_outcomes(score_frame, histories, horizons=(5,20,60))`
- Produces: `eligible_outcome_rows(frame, asof, horizon, outcome)`
- Produces: immutable `ScoreCalibration`
- Produces: `calibrate_score_probability(frame, current_score, asof, horizon, outcome, minimum_samples=100)`
- Outcome names: `opportunity` and `downside_risk`
- Uses opportunity bands: `{5: 0.01, 20: 0.02, 60: 0.04}`
- Uses risk barrier: `max(opportunity_band[horizon], atr20_pct / 100.0)`
- Consumes: Task 3 `build_group_score_frame(histories, group)`

- [ ] **Step 1: Write failing outcome maturation tests**

```python
# tests/test_market_outcomes.py
import unittest
import numpy as np
import pandas as pd

from research.market_outcomes import (
    attach_market_outcomes,
    calibrate_score_probability,
    eligible_outcome_rows,
)


class MarketOutcomeTest(unittest.TestCase):
    def test_opportunity_and_risk_labels_can_both_be_true(self):
        index = pd.bdate_range("2025-01-02", periods=70)
        close = pd.Series(100.0, index=index)
        close.iloc[51] = 95.0
        close.iloc[55] = 103.0
        frame = score_frame(index, score=70.0, atr20_pct=2.0)

        result = attach_market_outcomes(
            frame,
            {"AMD": pd.DataFrame({"Close": close})},
            horizons=(5,),
        )
        row = result.loc[("AMD", index[50])]
        self.assertEqual(row["opportunity_outcome_5"], 1.0)
        self.assertEqual(row["downside_risk_outcome_5"], 1.0)
        self.assertEqual(row["opportunity_label_end_date_5"], index[55])
        self.assertEqual(row["downside_risk_label_end_date_5"], index[55])

    def test_eligibility_requires_label_end_strictly_before_asof(self):
        frame = matured_outcome_frame()
        cutoff = pd.Timestamp("2026-01-20")
        eligible = eligible_outcome_rows(frame, cutoff, 5, "opportunity")
        self.assertTrue((eligible["opportunity_label_end_date_5"] < cutoff).all())

    def test_calibration_requires_one_hundred_samples_and_both_classes(self):
        too_small = calibration_frame(99, classes=True)
        one_class = calibration_frame(120, classes=False)
        self.assertEqual(
            calibrate_score_probability(
                too_small, 70.0, "2026-07-23", 5, "opportunity"
            ).reason,
            "insufficient_calibration_samples",
        )
        self.assertEqual(
            calibrate_score_probability(
                one_class, 70.0, "2026-07-23", 5, "opportunity"
            ).reason,
            "calibration_requires_both_classes",
        )
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
./venv/bin/python -m unittest tests.test_market_outcomes -v
```

Expected: FAIL because `research.market_outcomes` does not exist.

- [ ] **Step 3: Implement explicit labels and strict eligibility**

```python
# research/market_outcomes.py
from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral

import numpy as np
import pandas as pd


SUPPORTED_HORIZONS = (5, 20, 60)
POSITIVE_BANDS = {5: 0.01, 20: 0.02, 60: 0.04}
OUTCOMES = ("opportunity", "downside_risk")


@dataclass(frozen=True)
class ScoreCalibration:
    probability: float | None
    reason: str | None
    sample_count: int
    training_cutoff: str | None

    def to_dict(self):
        return {
            "probability": self.probability,
            "reason": self.reason,
            "sample_count": self.sample_count,
            "training_cutoff": self.training_cutoff,
        }


def eligible_outcome_rows(frame, asof, horizon, outcome):
    checked_horizon = _horizon(horizon)
    checked_outcome = _outcome(outcome)
    cutoff = pd.Timestamp(asof).tz_localize(None).normalize()
    end = f"{checked_outcome}_label_end_date_{checked_horizon}"
    target = f"{checked_outcome}_outcome_{checked_horizon}"
    rows = frame.loc[
        frame[target].notna()
        & frame[end].notna()
        & (frame[end] < cutoff)
    ]
    return rows.sort_index().copy(deep=True)
```

`attach_market_outcomes()` accepts the Task 3 score frame and the validated
history mapping. It constructs one aligned MultiIndex close series and uses
ticker-local session positions:

```python
close = pd.concat(
    {
        str(ticker): history["Close"].astype(float)
        for ticker, history in histories.items()
        if str(ticker) in score_frame.index.get_level_values("ticker")
    },
    names=("ticker", "observation_date"),
).reindex(score_frame.index)
terminal = close.groupby(level="ticker", sort=False).shift(-horizon)
forward_return = terminal / close.replace(0.0, np.nan) - 1.0
forward_min = (
    close.groupby(level="ticker", sort=False)
    .transform(lambda series: series.shift(-1)[::-1].rolling(horizon).min()[::-1])
)
forward_drawdown = forward_min / close.replace(0.0, np.nan) - 1.0
risk_barrier = np.maximum(
    POSITIVE_BANDS[horizon],
    frame["atr20_pct"].to_numpy(dtype=float) / 100.0,
)
complete = terminal.notna()
result[f"opportunity_outcome_{horizon}"] = (
    (forward_return > POSITIVE_BANDS[horizon])
    .astype(float)
    .where(complete)
)
result[f"downside_risk_outcome_{horizon}"] = (
    (forward_drawdown < -risk_barrier)
    .astype(float)
    .where(complete)
)
```

Rows without a complete horizon remain `NaN`, not false. Store the actual
ticker-local terminal date in both explicit label-end columns.

- [ ] **Step 4: Implement monotonic empirical score calibration**

Use the same 100-row and both-class gates as forecast confidence. Sort eligible
rows by score, pool adjacent violating bins until event frequency is
non-decreasing, and interpolate the current score within the fitted step
function. Only rows strictly eligible at `asof` participate.

```python
def calibrate_score_probability(
    frame,
    current_score,
    asof,
    horizon,
    outcome,
    minimum_samples=100,
):
    if isinstance(minimum_samples, bool) or not isinstance(minimum_samples, Integral):
        raise TypeError("minimum_samples must be an integer")
    minimum = max(100, int(minimum_samples))
    rows = eligible_outcome_rows(frame, asof, horizon, outcome)
    score_column = (
        "reversal_opportunity_score"
        if outcome == "opportunity"
        else "downside_risk_score"
    )
    target_column = f"{outcome}_outcome_{int(horizon)}"
    pairs = rows.loc[:, (score_column, target_column)].dropna()
    if len(pairs) < minimum:
        return ScoreCalibration(
            None, "insufficient_calibration_samples", len(pairs), None
        )
    classes = set(pairs[target_column].astype(int))
    if classes != {0, 1}:
        return ScoreCalibration(
            None, "calibration_requires_both_classes", len(pairs), None
        )
    probability = _isotonic_probability(
        pairs[score_column].to_numpy(dtype=float),
        pairs[target_column].to_numpy(dtype=float),
        float(current_score),
    )
    cutoff = pairs.index.get_level_values("observation_date").max()
    return ScoreCalibration(
        probability, None, len(pairs), cutoff.date().isoformat()
    )
```

- [ ] **Step 5: Integrate calibration without blocking deterministic scores**

For each selected group, the market service explicitly calls:

```python
score_frame = build_group_score_frame(snapshot.histories, group)
outcome_frame = attach_market_outcomes(
    score_frame,
    snapshot.histories,
    horizons=(5, 20, 60),
)
```

It calibrates the current opportunity and downside-risk scores independently
from `outcome_frame`, with the requested `asof` passed to every call. The
market service returns:

```python
"calibration": {
    "opportunity": {
        str(horizon): calibration.to_dict()
        for horizon in (5, 20, 60)
    },
    "downside_risk": {
        str(horizon): calibration.to_dict()
        for horizon in (5, 20, 60)
    },
}
```

If the score or matured frame is unavailable, return a `ScoreCalibration` with
reason `score_unavailable` or `insufficient_calibration_samples`; do not make
the entire market snapshot unavailable.

- [ ] **Step 6: Run outcome and service tests**

Run:

```bash
PYTHONWARNINGS=error ./venv/bin/python -m unittest \
  tests.test_market_outcomes \
  tests.test_web_market_overview \
  tests.test_web_forecast_evaluation -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add research/market_outcomes.py web/services/market_overview.py \
  tests/test_market_outcomes.py tests/test_web_market_overview.py
git commit -m "feat: calibrate reversal and downside evidence"
```

---

### Task 8: Documentation, integration, browser validation, and completion gates

**Files:**
- Modify: `docs/dashboard.md`
- Modify: `tests/test_web_market_assets.py`
- Modify: `tests/test_web_api.py`
- Modify: files from Tasks 1–7 only when a failing integration test identifies a defect

**Interfaces:**
- Verifies the complete `/market` workflow and does not add a new production
  interface.

- [ ] **Step 1: Add end-to-end API/UI contract tests**

```python
def test_market_page_and_api_keep_daily_proxy_and_unavailable_states_honest(self):
    app = create_app(
        {
            "TESTING": True,
            "MARKET_OVERVIEW_SERVICE": FakeMarketOverviewService(
                evidence_tier="daily_proxy",
                missing_intraday=True,
            ),
        },
        repository=FakeRepository(),
        update_manager=FakeManager(),
    )
    client = app.test_client()

    page = client.get("/market")
    payload = client.get("/api/market-overview?horizon=5&sector=semiconductor")

    self.assertEqual(page.status_code, 200)
    self.assertEqual(payload.status_code, 200)
    self.assertEqual(payload.get_json()["evidence_tier"], "daily_proxy")
    self.assertEqual(payload.get_json()["intraday"]["state"], "unavailable")
```

Add a same-date corrected-history test proving the market overview cache key
advances after the update manager's revision callback.

- [ ] **Step 2: Run focused integration tests**

Run:

```bash
PYTHONWARNINGS=error ./venv/bin/python -m unittest \
  tests.test_web_market_groups \
  tests.test_market_pressure \
  tests.test_market_context \
  tests.test_market_outcomes \
  tests.test_web_market_overview \
  tests.test_web_market_assets \
  tests.test_web_api \
  tests.test_web_update_jobs \
  tests.test_web_forecast_dataset \
  tests.test_web_forecasts \
  tests.test_web_forecast_evaluation -v
```

Expected: PASS.

- [ ] **Step 3: Document operation and methodology**

Add to `docs/dashboard.md`:

- `/market` navigation and query behavior;
- the fixed market and sector proxy list;
- the distinction between ETF proxy performance and constituent breadth;
- the separate opportunity/risk scores and 80% coverage gate;
- daily pressure proxy formulas and the statement that they are not true order
  flow;
- 5/20/60 label definitions and strict maturity boundary;
- reference ETF update behavior;
- `daily_proxy` and future `intraday_enhanced` evidence tiers;
- safe unavailable states and recovery using the explicit update button.

- [ ] **Step 4: Measure cold and warm local performance**

Run a production-shaped test client probe twice against a copied database:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/market-dashboard-perf \
./venv/bin/python - <<'PY'
from time import perf_counter
from web.app import create_app

app = create_app({"TESTING": True})
client = app.test_client()
for label in ("cold", "warm"):
    start = perf_counter()
    response = client.get(
        "/api/market-overview?horizon=5&sector=semiconductor"
    )
    elapsed = perf_counter() - start
    print(label, response.status_code, round(elapsed, 4), len(response.data))
PY
```

Expected: both responses are 200; cold and warm calls each complete under five
seconds on the development machine, and warm is not slower than cold by more
than normal timer noise.

- [ ] **Step 5: Run browser validation**

Start the existing local Flask app and verify with the in-app browser:

1. `/market` loads without console errors.
2. Chinese and English labels are complete.
3. 5/20/60 controls retain page geometry; the displayed one-session return is
   not presented as a prediction horizon.
4. Clicking semiconductor changes only the drill-down region.
5. Clicking AMD navigates to the existing stock dashboard.
6. Missing SOXX/SMH renders typed unavailability and coverage.
7. A narrow viewport keeps dates, labels, and evidence tables readable.
8. Keyboard focus reaches navigation, horizon controls, sector tiles, evidence
   details, and stock links.
9. No UI text calls the daily proxy actual order flow.

Record the checked viewport widths and outcomes in the task report.

- [ ] **Step 6: Run final automated verification**

Run:

```bash
PYTHONWARNINGS=error \
PYTHONPYCACHEPREFIX=/private/tmp/market-dashboard-final-pycache \
./venv/bin/python -m unittest discover -s tests -v

git diff --name-only --diff-filter=ACMR HEAD~8..HEAD -- '*.py' |
  xargs env PYTHONPYCACHEPREFIX=/private/tmp/market-dashboard-compile \
  ./venv/bin/python -m py_compile

node --check web/static/js/api.js
node --check web/static/js/i18n.js
node --check web/static/js/market.js
git diff --check
```

Expected: all tests pass, Python compilation passes, all JavaScript syntax
checks pass, and the diff check prints no output.

- [ ] **Step 7: Commit documentation and integration fixes**

```bash
git add docs/dashboard.md tests/test_web_market_assets.py tests/test_web_api.py
git commit -m "docs: document market pressure command center"
```

- [ ] **Step 8: Request final whole-branch review**

Generate one review package from the implementation base to `HEAD`. The reviewer
must check:

- point-in-time benchmark alignment and label maturity;
- score coverage and unavailable states;
- no composite-feature duplication;
- corrected-history cache invalidation;
- no remote page-load data access;
- bilingual accessibility and stable layout;
- unchanged stock hover, update, forecast, and intraday behavior.

Critical or Important findings block merge and require a focused RED→GREEN fix
wave followed by re-review.
