"""Lightweight universe summaries with revision-scoped bounded caching."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import asdict, is_dataclass
import math
from numbers import Real
from threading import RLock

import pandas as pd

from web.factors.registry import FactorRegistry
from web.services.analysis import AnalysisContext


UNIVERSE_FACTOR_KEYS = ("mom_12_1", "realized_vol_63")
UNIVERSE_MOMENTUM_FACTOR_KEY = "mom_12_1"
UNIVERSE_VOLATILITY_FACTOR_KEY = "realized_vol_63"
UNIVERSE_ALGORITHM_VERSION = "universe_summary_v2"


class UniverseSnapshotService:
    """Build and cache the inexpensive stock-picker summary payload."""

    def __init__(
        self,
        repository,
        factor_registry,
        classification_service=None,
        revision_getter=lambda: 0,
        max_cache_size=4,
    ):
        if not callable(revision_getter):
            raise TypeError("revision_getter must be callable")
        if isinstance(max_cache_size, bool) or not isinstance(max_cache_size, int):
            raise TypeError("max_cache_size must be an integer")
        if max_cache_size <= 0:
            raise ValueError("max_cache_size must be positive")
        self._repository = repository
        self._factor_registry = factor_registry
        self._classification_service = classification_service
        self._revision_getter = revision_getter
        self._max_cache_size = max_cache_size
        self._cache = OrderedDict()
        self._lock = RLock()

    @property
    def cache_size(self):
        with self._lock:
            return len(self._cache)

    def build(self):
        freshness = self._repository.freshness()
        asof = freshness.get("latest_date")
        revision = int(self._revision_getter())
        key = (revision, asof, UNIVERSE_ALGORITHM_VERSION)

        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return deepcopy(cached)

            summaries = self._repository.list_summaries()
            histories = self._repository.load_universe_histories(
                None if asof is None else pd.Timestamp(asof)
            )
            rows = build_universe_rows(
                summaries,
                histories,
                self._factor_registry,
            )
            classifications = self._build_classifications(
                [row["ticker"] for row in rows]
            )
            merge_sector_classifications(rows, classifications)
            payload = {
                "asof": asof,
                "freshness": freshness,
                "tickers": rows,
                "factor_groups": factor_groups(self._factor_registry),
                "classification_summary": {
                    key: deepcopy(value)
                    for key, value in classifications.items()
                    if key != "by_ticker"
                },
            }
            self._cache[key] = deepcopy(payload)
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_cache_size:
                self._cache.popitem(last=False)
            return deepcopy(payload)

    def _build_classifications(self, tickers):
        if self._classification_service is None:
            return _unavailable_classifications(tickers)
        try:
            return self._classification_service.build(tickers)
        except (OSError, RuntimeError, TypeError, ValueError):
            return _unavailable_classifications(tickers)


def build_universe_rows(summaries, histories, registry):
    """Build lightweight diagnostics at each ticker's real last bar."""
    benchmark = histories.get("SPY")
    contexts = [
        AnalysisContext(
            ticker=summary.ticker,
            observation_date=pd.Timestamp(histories[summary.ticker].index[-1]),
            history=histories[summary.ticker],
            benchmark_history=benchmark,
        )
        for summary in summaries
        if summary.ticker in histories and not histories[summary.ticker].empty
    ]
    selected_factors = [
        factor for factor in registry.factors if factor.key in UNIVERSE_FACTOR_KEYS
    ]
    evaluated = FactorRegistry(selected_factors).evaluate_universe(contexts)

    rows = []
    for summary in summaries:
        results = {
            result.key: result for result in evaluated.get(summary.ticker, ())
        }
        momentum = _percentile_0_100(
            results.get(UNIVERSE_MOMENTUM_FACTOR_KEY)
        )
        volatility = _annualized_percent(
            results.get(UNIVERSE_VOLATILITY_FACTOR_KEY)
        )
        inactive = bool(summary.inactive)
        stale = not inactive and summary.lag_days > 0
        row = _summary_dict(summary)
        row.update(
            {
                "fresh": not inactive and summary.lag_days == 0,
                "stale": stale,
                "data_status": (
                    "inactive" if inactive else "stale" if stale else "current"
                ),
                "strict_vcp": None,
                "tight_platform": None,
                "near_pivot": None,
                "shape_state": "unavailable",
                "momentum_percentile": momentum,
                "momentum_factor_key": UNIVERSE_MOMENTUM_FACTOR_KEY,
                "momentum_percentile_unit": "percentile_0_100",
                "volatility": volatility,
                "volatility_factor_key": UNIVERSE_VOLATILITY_FACTOR_KEY,
                "volatility_unit": "annualized_percent",
            }
        )
        rows.append(row)
    return rows


def merge_sector_classifications(rows, payload):
    by_ticker = payload.get("by_ticker", {})
    for row in rows:
        row["sector_classification"] = deepcopy(
            by_ticker.get(
                row["ticker"],
                {
                    "state": "unclassified",
                    "sec": None,
                    "market_behavior": None,
                },
            )
        )
    return rows


def _unavailable_classifications(tickers):
    return {
        "status": "unavailable",
        "asof": None,
        "research_universe_count": 0,
        "sector_counts": {},
        "by_ticker": {
            ticker: {
                "state": "unclassified",
                "sec": None,
                "market_behavior": None,
            }
            for ticker in tickers
        },
    }


def factor_groups(registry):
    groups = getattr(registry, "groups", ())
    if groups:
        return [group.to_dict() for group in groups]
    return [
        {
            "key": key,
            "label": key.replace("_", " ").title(),
            "methodology": "Registered point-in-time factor diagnostics.",
            "overview": True,
            "i18n": {},
        }
        for key in dict.fromkeys(factor.group for factor in registry.factors)
    ]


def _summary_dict(summary):
    if is_dataclass(summary):
        return asdict(summary)
    return {
        "ticker": summary.ticker,
        "latest_date": summary.latest_date,
        "lag_days": summary.lag_days,
        "inactive": summary.inactive,
    }


def _percentile_0_100(result):
    if result is None or result.percentile is None:
        return None
    return round(float(result.percentile) * 100, 2)


def _annualized_percent(result):
    value = None if result is None or result.missing else result.raw_value
    return round(float(value) * 100, 2) if _finite_number(value) else None


def _finite_number(value):
    return (
        isinstance(value, Real)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )
