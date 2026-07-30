"""Read-only adjusted SPY/QQQ history for policy-period charts."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from threading import RLock

import numpy as np
import pandas as pd

from research.expanded_market_data import ExpandedMarketDataUnavailable
from web.services.market_data import (
    MarketDataUnavailable,
    UnknownTicker,
)


SUPPORTED_POLICY_BENCHMARKS = ("SPY", "QQQ")


class PolicyBenchmarkHistoryService:
    def __init__(
        self,
        repository,
        *,
        benchmark_repository=None,
        revision_getter=lambda: 0,
        max_cache_size=8,
    ):
        if not callable(revision_getter):
            raise TypeError("revision_getter must be callable")
        if isinstance(max_cache_size, bool) or not isinstance(
            max_cache_size,
            int,
        ):
            raise TypeError("max_cache_size must be an integer")
        if max_cache_size <= 0:
            raise ValueError("max_cache_size must be positive")
        self._repository = repository
        self._benchmark_repository = benchmark_repository
        self._revision_getter = revision_getter
        self._max_cache_size = max_cache_size
        self._cache = OrderedDict()
        self._lock = RLock()

    @property
    def cache_size(self):
        with self._lock:
            return len(self._cache)

    def build(self, asof=None, benchmark="SPY"):
        symbol = _benchmark(benchmark)
        cutoff = _asof_cutoff(asof)
        key = (
            cutoff.isoformat(),
            symbol,
            self._revision_getter(),
            _cache_token(self._benchmark_repository),
        )
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return deepcopy(cached)

        history, source = self._load_history(
            symbol,
            cutoff.date().isoformat(),
        )
        if history is None or history.empty:
            payload = _unavailable_payload(
                cutoff,
                symbol,
                "benchmark_history_unavailable",
            )
        else:
            try:
                payload = _history_payload(
                    history,
                    cutoff=cutoff,
                    benchmark=symbol,
                    source=source,
                )
            except (TypeError, ValueError):
                payload = _unavailable_payload(
                    cutoff,
                    symbol,
                    "benchmark_history_invalid",
                )

        with self._lock:
            self._cache[key] = deepcopy(payload)
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_cache_size:
                self._cache.popitem(last=False)
        return deepcopy(payload)

    def _load_history(self, benchmark, asof):
        loader = getattr(
            self._benchmark_repository,
            "load_universe_histories",
            None,
        )
        if callable(loader):
            try:
                histories = loader(
                    asof=asof,
                    tickers=(benchmark,),
                )
                history = histories.get(benchmark)
                if history is not None and not history.empty:
                    return history, "research_adjusted"
            except ExpandedMarketDataUnavailable:
                pass

        loader = getattr(self._repository, "load_history", None)
        if not callable(loader):
            return None, None
        try:
            history = loader(benchmark, asof=asof)
        except (MarketDataUnavailable, UnknownTicker):
            return None, None
        if history is None or history.empty:
            return None, None
        return history, "primary_adjusted"


def _history_payload(history, *, cutoff, benchmark, source):
    frame = pd.DataFrame(history).copy()
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("benchmark history index must be DatetimeIndex")
    if "Close" not in frame:
        raise ValueError("benchmark history requires Close")
    frame.index = frame.index.tz_localize(None).normalize()
    frame = frame.loc[
        frame.index <= cutoff.tz_localize(None).normalize()
    ].copy()
    if frame.empty:
        return _unavailable_payload(
            cutoff,
            benchmark,
            "benchmark_history_unavailable",
        )
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("benchmark history dates must be unique and sorted")
    close = pd.to_numeric(frame["Close"], errors="coerce")
    values = close.to_numpy(dtype=float)
    if (
        close.isna().any()
        or not np.isfinite(values).all()
        or (values <= 0).any()
    ):
        raise ValueError("benchmark closes must be finite and positive")
    baseline = float(close.iloc[0])
    rows = [
        {
            "time": timestamp.date().isoformat(),
            "close": round(float(value), 6),
            "normalized": round(100.0 * float(value) / baseline, 6),
        }
        for timestamp, value in close.items()
    ]
    return {
        "artifact_key": "policy_benchmark_history_v1",
        "asof": cutoff.isoformat(),
        "benchmark": benchmark,
        "source": source,
        "first_date": rows[0]["time"],
        "last_date": rows[-1]["time"],
        "row_count": len(rows),
        "rows": rows,
        "lifecycle": "research",
        "decision_permission": "advisory",
        "online_authority": "none",
        "point_in_time": True,
        "historical_description_only": True,
        "unavailable_reason": None,
    }


def _unavailable_payload(cutoff, benchmark, reason):
    return {
        "artifact_key": "policy_benchmark_history_v1",
        "asof": cutoff.isoformat(),
        "benchmark": benchmark,
        "source": None,
        "first_date": None,
        "last_date": None,
        "row_count": 0,
        "rows": [],
        "lifecycle": "research",
        "decision_permission": "advisory",
        "online_authority": "none",
        "point_in_time": True,
        "historical_description_only": True,
        "unavailable_reason": str(reason),
    }


def _benchmark(value):
    if not isinstance(value, str):
        raise ValueError("benchmark must be SPY or QQQ")
    benchmark = value.strip().upper()
    if benchmark not in SUPPORTED_POLICY_BENCHMARKS:
        raise ValueError("benchmark must be SPY or QQQ")
    return benchmark


def _asof_cutoff(asof):
    if asof is None:
        return pd.Timestamp.now(tz="UTC")
    value = pd.Timestamp(asof)
    if value.tz is None:
        return (
            value.normalize()
            + pd.Timedelta(days=1)
            - pd.Timedelta(microseconds=1)
        ).tz_localize("UTC")
    return value.tz_convert("UTC")


def _cache_token(repository):
    builder = getattr(repository, "cache_token", None)
    return builder() if callable(builder) else None
