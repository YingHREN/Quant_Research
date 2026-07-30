"""Read-only service for the descriptive policy-period ETF matrix."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import RLock

import pandas as pd

from research.policy_period_matrix import build_policy_period_matrix
from research.policy_period_returns import POLICY_ETFS
from web.services.policy_event_store import (
    PolicyDataUnavailable,
    PolicyEventStore,
)


class PolicyPeriodMatrixService:
    def __init__(self, database_path, max_cache_size=32):
        if (
            isinstance(max_cache_size, bool)
            or not isinstance(max_cache_size, int)
            or max_cache_size <= 0
        ):
            raise ValueError(
                "max_cache_size must be a positive integer"
            )
        self._database_path = Path(database_path)
        self._store = PolicyEventStore(self._database_path)
        self._max_cache_size = max_cache_size
        self._cache = {}
        self._lock = RLock()

    def build(self, asof, histories):
        cutoff = _asof_cutoff(asof)
        key = (
            self.cache_token(),
            cutoff.isoformat(),
            _history_identity(histories),
        )
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return deepcopy(cached)
        try:
            events = self._store.load_events(cutoff)
            periods = self._store.load_periods(cutoff)
            payload = build_policy_period_matrix(
                periods,
                events,
                histories,
                cutoff,
            )
        except (PolicyDataUnavailable, ValueError, TypeError, KeyError):
            payload = _unavailable_payload(
                cutoff,
                "policy_catalog_unavailable",
            )
        with self._lock:
            self._cache[key] = deepcopy(payload)
            while len(self._cache) > self._max_cache_size:
                self._cache.pop(next(iter(self._cache)))
        return deepcopy(payload)

    def cache_token(self):
        try:
            stat = self._database_path.stat()
        except OSError:
            return ("missing",)
        return (stat.st_mtime_ns, stat.st_size)


def _history_identity(histories):
    identity = []
    for ticker in POLICY_ETFS:
        history = histories.get(ticker)
        if history is None or history.empty:
            identity.append((ticker, None))
            continue
        if not isinstance(history.index, pd.DatetimeIndex):
            raise ValueError("policy history index must be a DatetimeIndex")
        column = "Adj Close" if "Adj Close" in history.columns else "Close"
        if column not in history.columns:
            raise ValueError("policy history requires Close or Adj Close")
        identity.append(
            (
                ticker,
                pd.Timestamp(history.index.min()).isoformat(),
                pd.Timestamp(history.index.max()).isoformat(),
                int(len(history)),
                float(history[column].iloc[-1]),
            )
        )
    return tuple(identity)


def _unavailable_payload(cutoff, reason):
    return {
        "artifact_key": "policy_period_matrix_v1",
        "asof": cutoff.isoformat(),
        "periods": [],
        "rows": [],
        "metrics": [
            "total_return",
            "annualized_return",
            "relative_spy_return",
            "max_drawdown",
            "positive_month_ratio",
        ],
        "coverage": {
            "period_count": 0,
            "ticker_period_rows": 0,
            "status_counts": {},
            "complete_rows": 0,
            "eligible_rows": 0,
            "ratio": None,
        },
        "lifecycle": "research",
        "decision_permission": "advisory",
        "online_authority": "none",
        "point_in_time": True,
        "historical_description_only": True,
        "unavailable_reason": str(reason),
    }


def _asof_cutoff(asof):
    if asof is None:
        return pd.Timestamp.now(tz="UTC")
    value = pd.Timestamp(asof)
    if value.tz is None:
        value = value.normalize() + pd.Timedelta(days=1)
        value -= pd.Timedelta(microseconds=1)
        return value.tz_localize("UTC")
    return value.tz_convert("UTC")
