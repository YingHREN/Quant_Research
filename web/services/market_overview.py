"""Read-only market-overview orchestration and bounded caching."""

from __future__ import annotations

from copy import deepcopy
from threading import RLock

import pandas as pd

from research.market_context import (
    SUPPORTED_HORIZONS,
    build_market_context,
)
from web.market_groups import market_group


class MarketOverviewService:
    def __init__(
        self,
        repository,
        revision_getter=lambda: 0,
        max_cache_size=16,
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
        self._revision_getter = revision_getter
        self._max_cache_size = max_cache_size
        self._cache = {}
        self._lock = RLock()

    def build(
        self,
        *,
        asof=None,
        horizon=5,
        sector="semiconductor",
    ):
        if (
            isinstance(horizon, bool)
            or not isinstance(horizon, int)
            or horizon not in SUPPORTED_HORIZONS
        ):
            raise ValueError("invalid_horizon")
        group = market_group(sector)
        snapshot = self._repository.load_market_overview_snapshot(asof)
        normalized_asof = snapshot.observation_date
        revision = int(self._revision_getter())
        key = (
            revision,
            normalized_asof,
            horizon,
            group.key,
            "market_evidence_v1",
        )
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return deepcopy(cached)

        if normalized_asof is None:
            payload = _empty_payload(horizon, group.key)
        else:
            payload = build_market_context(
                snapshot.histories,
                pd.Timestamp(normalized_asof),
                group,
                horizon,
            )

        with self._lock:
            self._cache[key] = deepcopy(payload)
            while len(self._cache) > self._max_cache_size:
                self._cache.pop(next(iter(self._cache)))
        return deepcopy(payload)


def _empty_payload(horizon, sector):
    return {
        "asof": None,
        "requested_horizon": int(horizon),
        "selected_sector": sector,
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
            "key": sector,
            "score": None,
            "coverage": 0.0,
            "unavailable_reason": "market_data_unavailable",
        },
        "constituents": [],
        "changed_events": [],
        "calibration": {},
    }
